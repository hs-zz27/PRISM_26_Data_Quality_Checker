"""Capture exact Penn-Fudan sample bytes into managed local storage."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
import errno
from hashlib import sha256
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from ..core.config import AssetStorageSettings
from ..models.asset_manifest import AssetAvailability, AssetKind, AssetManifestEntry, SampleAssetManifest
from ..storage.workflow_repository import (
    RetryableWorkflowStorageError,
    SampleAssetManifestRepository,
    read_manifest_asset_file,
)


logger = logging.getLogger(__name__)

try:
    import fcntl
    msvcrt = None
except ImportError:  # Windows has no fcntl; msvcrt provides the equivalent file lock
    fcntl = None
    import msvcrt


class AssetSnapshotStorageError(RuntimeError):
    """Managed snapshot or temporary processing storage could not be used."""


class _SourceReadError(OSError):
    """A source file became unreadable while its bytes were being copied."""


_CHUNK_SIZE = 1024 * 1024
_SOURCE_ASSETS = {
    AssetKind.ORIGINAL_IMAGE: (Path("PNGImages"), "image/png"),
    AssetKind.RAW_MASK: (Path("PedMasks"), "image/png"),
    AssetKind.ANNOTATION_TEXT: (Path("Annotation"), "text/plain"),
}


def _validate_identifier(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be a nonblank identifier.")
    if value in {".", ".."} or any(character in value for character in ("/", "\\", "\x00")):
        raise ValueError(f"{name} must be a single safe path component.")
    return value


def _validate_revision(sample_revision: int) -> int:
    if type(sample_revision) is not int or sample_revision < 1:
        raise ValueError("sample_revision must be a positive integer.")
    return sample_revision


def _source_name(kind: AssetKind, sample_id: str) -> str:
    return {
        AssetKind.ORIGINAL_IMAGE: f"{sample_id}.png",
        AssetKind.RAW_MASK: f"{sample_id}_mask.png",
        AssetKind.ANNOTATION_TEXT: f"{sample_id}.txt",
    }[kind]


def _source_file(source_root: Path, kind: AssetKind, sample_id: str) -> tuple[Path | None, AssetAvailability]:
    directory, _ = _SOURCE_ASSETS[kind]
    candidate = source_root / directory / _source_name(kind, sample_id)
    try:
        resolved_root = source_root.resolve(strict=False)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(resolved_root)
    except FileNotFoundError:
        return None, AssetAvailability.MISSING
    except (OSError, RuntimeError, ValueError):
        return None, AssetAvailability.UNREADABLE
    if not resolved.is_file():
        return None, AssetAvailability.UNREADABLE
    return resolved, AssetAvailability.AVAILABLE


def _storage_key(dataset_id: str, sample_id: str, sample_revision: int, kind: AssetKind, digest: str) -> str:
    dataset_key = sha256(dataset_id.encode("utf-8")).hexdigest()
    sample_key = sha256(sample_id.encode("utf-8")).hexdigest()
    return f"snapshots/v1/{dataset_key}/{sample_key}/{sample_revision}/{kind.value}/{digest}"


def _copy_exact(source: Path, temporary: Path) -> tuple[str, int]:
    """Copy source bytes to a private temporary file and return digest and size."""
    digest = sha256()
    size = 0
    try:
        source_file = source.open("rb")
    except OSError as error:
        raise _SourceReadError("The source asset could not be opened.") from error

    try:
        try:
            destination = temporary.open("xb")
        except OSError as error:
            source_file.close()
            raise AssetSnapshotStorageError("The managed asset storage could not be opened.") from error
        try:
            with source_file:
                with destination:
                    while True:
                        try:
                            chunk = source_file.read(_CHUNK_SIZE)
                        except OSError as error:
                            raise _SourceReadError("The source asset could not be read.") from error
                        if not chunk:
                            break
                        try:
                            destination.write(chunk)
                        except OSError as error:
                            raise AssetSnapshotStorageError("The managed asset storage could not be written.") from error
                        digest.update(chunk)
                        size += len(chunk)
                    try:
                        destination.flush()
                        os.fsync(destination.fileno())
                    except OSError as error:
                        raise AssetSnapshotStorageError("The managed asset storage could not be flushed.") from error
        except AssetSnapshotStorageError:
            raise
    except (_SourceReadError, AssetSnapshotStorageError):
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return digest.hexdigest(), size


def _install_copy(temporary: Path, destination: Path, digest: str, size: int) -> bool:
    """Install without replacing an existing file; return whether it was new."""
    try:
        if destination.is_symlink():
            raise AssetSnapshotStorageError("Managed asset storage contains a symlink.")
        if destination.exists():
            if not destination.is_file():
                raise AssetSnapshotStorageError("Managed asset storage contains a non-file.")
            if destination.stat().st_nlink != 1:
                raise AssetSnapshotStorageError("Managed asset storage contains a hard-linked file.")
            existing = destination.read_bytes()
            if len(existing) != size or sha256(existing).hexdigest() != digest:
                raise AssetSnapshotStorageError("Managed asset storage contains different bytes.")
            return False
        # The temporary file and final file briefly share an inode; the final
        # file is independent from the source and has one link after cleanup.
        os.link(temporary, destination)
        return True
    except AssetSnapshotStorageError:
        raise
    except OSError as error:
        raise AssetSnapshotStorageError("The managed asset storage could not publish a file.") from error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError as error:
            raise AssetSnapshotStorageError("The temporary asset could not be removed.") from error


def _remove_created(paths: list[Path]) -> None:
    for path in reversed(paths):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Cleanup is best effort. The caller still reports the original
            # failure and never claims filesystem/database atomicity.
            pass


_LOCK_BUSY = getattr(errno, "EDEADLOCK", errno.EDEADLK)


def _lock_exclusively(stream) -> None:
    """Block until this process holds an exclusive lock on the open file.

    POSIX uses flock. Windows uses msvcrt.locking on the first byte. That call waits about ten
    seconds and then fails with EDEADLOCK when another process still holds the lock, so keep
    waiting on that error only. Any other error is a real failure and is raised.
    """
    if fcntl is not None:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        return
    while True:
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            return
        except OSError as error:
            if error.errno != _LOCK_BUSY:
                raise


def _unlock(stream) -> None:
    """POSIX releases on close. Windows may hold a lock briefly after close, so release it now."""
    if fcntl is not None:
        return
    try:
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass  # closing the file releases the lock anyway


@contextmanager
def _capture_lock(root: Path, dataset_id: str, sample_id: str, revision: int):
    """Serialize publication and cleanup for one revision on the local filesystem."""
    identity = "\x00".join((dataset_id, sample_id, str(revision)))
    lock_path = root / ".locks" / f"{sha256(identity.encode('utf-8')).hexdigest()}.lock"
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = lock_path.open("a+b")
    except OSError as error:
        raise AssetSnapshotStorageError("The sample capture lock could not be opened.") from error
    with lock:
        try:
            _lock_exclusively(lock)
        except OSError as error:
            raise AssetSnapshotStorageError("The sample capture lock could not be acquired.") from error
        # Closing releases the lock, including after an exception or process exit.
        # Retain the file: unlinking it could give waiters different lock inodes.
        try:
            yield
        finally:
            _unlock(lock)


def capture_sample(
    *,
    dataset_id: str,
    sample_id: str,
    sample_revision: int,
    source_root: str | Path,
    repository: SampleAssetManifestRepository,
    copy_file: Callable[[Path, Path], tuple[str, int]] | None = None,
    source_files: dict[AssetKind, tuple[Path, str]] | None = None,
    annotation_format: str = "penn_fudan",
    image_key: str | None = None,
) -> SampleAssetManifest:
    """Capture one trusted local Penn-Fudan sample and publish its manifest.

    Source read failures become per-asset ``missing``/``unreadable`` states.
    Managed-storage failures abort before publication. Filesystem writes and the
    PostgreSQL manifest transaction are separate systems; an unknown database
    commit outcome deliberately leaves staged files for later reconciliation.
    """
    dataset_id = _validate_identifier(dataset_id, "dataset_id")
    sample_id = _validate_identifier(sample_id, "sample_id")
    sample_revision = _validate_revision(sample_revision)
    copy_file = _copy_exact if copy_file is None else copy_file
    source_root = Path(source_root).resolve(strict=False)
    storage_root = repository.asset_settings.root.resolve(strict=False)
    with _capture_lock(storage_root, dataset_id, sample_id, sample_revision):
        return _capture_locked(
            dataset_id, sample_id, sample_revision, source_root, storage_root,
            repository, copy_file, source_files, annotation_format, image_key,
        )


def _capture_locked(
    dataset_id: str, sample_id: str, sample_revision: int,
    source_root: Path, storage_root: Path, repository: SampleAssetManifestRepository,
    copy_file: Callable[[Path, Path], tuple[str, int]],
    source_files: dict[AssetKind, tuple[Path, str]] | None = None,
    annotation_format: str = "penn_fudan",
    image_key: str | None = None,
) -> SampleAssetManifest:
    created: list[Path] = []
    entries: dict[AssetKind, AssetManifestEntry] = {}

    try:
        for kind, (_, content_type) in _SOURCE_ASSETS.items():
            if source_files is None:
                source, availability = _source_file(source_root, kind, sample_id)
            elif kind not in source_files:
                source, availability = None, AssetAvailability.MISSING
            else:
                candidate, content_type = source_files[kind]
                try:
                    source = candidate.resolve(strict=True)
                    source.relative_to(source_root)
                    if not source.is_file():
                        raise ValueError("Not a file.")
                    availability = AssetAvailability.AVAILABLE
                except FileNotFoundError:
                    source, availability = None, AssetAvailability.MISSING
                except (OSError, ValueError, RuntimeError):
                    source, availability = None, AssetAvailability.UNREADABLE
            if availability is not AssetAvailability.AVAILABLE or source is None:
                entries[kind] = AssetManifestEntry(availability=availability)
                continue

            digest = None
            size = None
            temporary: Path | None = None
            try:
                # The content digest is not known until the exact bytes have
                # been copied, so stage under a private, server-owned name.
                temporary_directory = storage_root / ".staging"
                temporary_directory.mkdir(parents=True, exist_ok=True)
                temporary = temporary_directory / f"{uuid4().hex}.tmp"
                try:
                    digest, size = copy_file(source, temporary)
                except _SourceReadError:
                    entries[kind] = AssetManifestEntry(availability=AssetAvailability.UNREADABLE)
                    continue
                key = _storage_key(dataset_id, sample_id, sample_revision, kind, digest)
                destination = storage_root / key
                destination.parent.mkdir(parents=True, exist_ok=True)
                if _install_copy(temporary, destination, digest, size):
                    created.append(destination)
            except AssetSnapshotStorageError:
                raise
            except OSError as error:
                raise AssetSnapshotStorageError("The managed asset storage could not be prepared.") from error
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass

            entries[kind] = AssetManifestEntry(
                availability=AssetAvailability.AVAILABLE,
                storage_key=key,
                content_type=content_type,
                sha256=digest,
                size_bytes=size,
            )

        manifest = SampleAssetManifest(
            dataset_id=dataset_id,
            sample_id=sample_id,
            sample_revision=sample_revision,
            annotation_format=annotation_format, image_key=image_key,
            original_image=entries[AssetKind.ORIGINAL_IMAGE],
            raw_mask=entries[AssetKind.RAW_MASK],
            annotation_text=entries[AssetKind.ANNOTATION_TEXT],
        )
        return repository.save_manifest(manifest)
    except RetryableWorkflowStorageError as error:
        if not error.commit_outcome_unknown:
            _remove_created(created)
        raise
    except Exception:
        _remove_created(created)
        raise


@contextmanager
def sample_work_directory():
    """Keep temporary-storage failures distinct from collector source errors."""
    try:
        temporary = TemporaryDirectory(prefix="prism-sample-")
    except OSError as error:
        raise AssetSnapshotStorageError("Sample processing storage could not be created.") from error
    processing_failed = False
    try:
        yield Path(temporary.name)
    except BaseException:
        processing_failed = True
        raise
    finally:
        try:
            temporary.cleanup()
        except OSError as error:
            if processing_failed:
                # In particular, never turn cancellation or lost ownership
                # into a different failure that could finalize the job.
                logger.exception("Sample processing storage cleanup failed")
            else:
                raise AssetSnapshotStorageError("Sample processing storage could not be cleaned up.") from error


def materialize_sample(
    manifest: SampleAssetManifest,
    *,
    asset_settings: AssetStorageSettings,
    target_root: Path,
) -> None:
    """Prepare verified bytes without database access; safe to run in a child."""
    if not isinstance(manifest, SampleAssetManifest):
        raise TypeError("manifest must be a SampleAssetManifest instance.")
    _validate_identifier(manifest.sample_id, "sample_id")
    for kind, (relative_directory, _) in _SOURCE_ASSETS.items():
        entry = getattr(manifest, kind.value)
        if entry.availability is not AssetAvailability.AVAILABLE:
            continue
        _, content = read_manifest_asset_file(manifest, kind, asset_settings)
        target = target_root / relative_directory / _source_name(kind, manifest.sample_id)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        except OSError as error:
            raise AssetSnapshotStorageError("Sample processing storage could not be written.") from error
