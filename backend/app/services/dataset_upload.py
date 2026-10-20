"""Streaming, durable service acceptance. No archive processing or HTTP dependency."""

from collections.abc import Iterable
from hashlib import sha256
import os
from pathlib import Path
import shutil
from uuid import uuid4

from pydantic import TypeAdapter

from ..core.dataset_import import (
    ImportAcceptanceError, ImportOutcomeUnresolved, ImportSettings, UploadTooLargeError,
    load_import_settings,
)
from ..models.dataset import Identifier
from ..models.dataset_import import ImportRequest
from ..storage.dataset_repository import DatasetCommitOutcomeUnknownError, DatasetStorageError

CHUNK_BYTES = 1024 * 1024


def sync_directory(path: Path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def private_directory(path: Path):
    """Private application root; paths are server configuration, never ZIP names."""
    if not path.is_absolute():
        raise OSError("Private directories require an absolute server root.")
    # Create and fsync each missing ancestor, not just the final directory.
    missing, ancestor = [], path
    while not ancestor.exists():
        missing.append(ancestor)
        ancestor = ancestor.parent
    for component in (ancestor, *ancestor.parents):
        if component.is_symlink():
            raise OSError("Private directory ancestors must not be symbolic links.")
    for directory in reversed(missing):
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            raise OSError("Unsafe private directory.")
        sync_directory(directory)
        sync_directory(directory.parent)
    if path.is_symlink() or not path.is_dir():
        raise OSError("Unsafe private directory.")
    os.chmod(path, 0o700)
    sync_directory(path)
    sync_directory(path.parent)


def submit_archive(
    chunks: Iterable[bytes], *, name: str, format: str = "penn_fudan",
    repository, settings: ImportSettings | None = None,
):
    """Consume bounded chunks; caller owns/always closes its source handle.

    A future async UploadFile route should run this blocking service off the event
    loop. Ingress/parser limits remain Phase 3; this counter is not an ingress cap.
    """
    name = TypeAdapter(Identifier).validate_python(name, strict=True)
    if type(format) is not str or format != "penn_fudan":
        raise ValueError("Unsupported dataset format.")
    effective = load_import_settings() if settings is None else ImportSettings.model_validate(settings.model_dump())
    submission = uuid4().hex
    directory = effective.root / "uploads" / submission
    accepted_or_unknown = False
    created = False
    try:
        private_directory(effective.root)
        private_directory(effective.root / "uploads")
        directory.mkdir(mode=0o700)
        created = True
        sync_directory(directory.parent)
        digest, size = sha256(), 0
        temporary = directory / "archive.part"
        with temporary.open("xb") as destination:
            for chunk in chunks:
                if type(chunk) is not bytes or len(chunk) > CHUNK_BYTES:
                    raise ValueError("Upload producers must yield bytes chunks no larger than 1 MiB.")
                size += len(chunk)
                if size > effective.policy.max_archive_bytes:
                    raise UploadTooLargeError("The archive exceeds the configured upload limit.")
                destination.write(chunk)
                digest.update(chunk)
            if size == 0:
                raise ValueError("The archive upload must not be empty.")
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, directory / "archive.zip")
        sync_directory(directory)
        request = ImportRequest(
            job_id=f"job_dataset_import_{uuid4().hex}", dataset_id=f"dataset_{uuid4().hex}",
            archive_key=f"uploads/{submission}/archive.zip", archive_sha256=digest.hexdigest(),
            archive_size_bytes=size, policy=effective.policy,
        )
        try:
            result = repository.enqueue(request, name=name, format=format)
        except DatasetCommitOutcomeUnknownError as error:
            accepted_or_unknown = True
            raise ImportOutcomeUnresolved("Archive acceptance requires operator reconciliation.") from error
        accepted_or_unknown = True
        return result
    except (OSError, DatasetStorageError) as error:
        raise ImportAcceptanceError("The archive could not be durably accepted.") from error
    finally:
        # Never delete a possibly referenced archive, snapshots, or another upload.
        if created and not accepted_or_unknown:
            try:
                shutil.rmtree(directory)
                sync_directory(directory.parent)
            except OSError:
                pass  # Request-owned orphan only; do not hide the original failure.
