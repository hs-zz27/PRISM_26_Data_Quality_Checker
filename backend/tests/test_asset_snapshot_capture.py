"""Focused temporary-directory coverage for exact local sample snapshots."""

from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from threading import Event

import pytest

from backend.app.core.config import AssetStorageSettings
from backend.app.models.asset_manifest import AssetAvailability, AssetKind
from backend.app.services import asset_snapshot
from backend.app.services.asset_snapshot import (
    AssetSnapshotStorageError, capture_sample, materialize_sample, sample_work_directory,
)
from backend.app.storage.workflow_repository import (
    JobCancellationError, JobClaimLostError, RetryableWorkflowStorageError,
    SampleAssetManifestConflictError, SampleProcessingTimeoutError,
)


SOURCE_LAYOUT = {
    AssetKind.ORIGINAL_IMAGE: (Path("PNGImages"), "sample.png", "image/png"),
    AssetKind.RAW_MASK: (Path("PedMasks"), "sample_mask.png", "image/png"),
    AssetKind.ANNOTATION_TEXT: (Path("Annotation"), "sample.txt", "text/plain"),
}


class MemoryManifestRepository:
    """Small manifest sink for filesystem behavior without PostgreSQL."""

    def __init__(self, root: Path):
        self.asset_settings = AssetStorageSettings(root=root)
        self.manifests = {}

    def save_manifest(self, manifest):
        key = (manifest.dataset_id, manifest.sample_id, manifest.sample_revision)
        current = self.manifests.get(key)
        if current is not None and current != manifest:
            raise SampleAssetManifestConflictError(*key)
        if current is None:
            self.manifests[key] = manifest.model_copy(deep=True)
        return self.manifests[key].model_copy(deep=True)


def write_sample(root: Path, payloads: dict[AssetKind, bytes]) -> dict[AssetKind, Path]:
    paths = {}
    for kind, (directory, filename, _) in SOURCE_LAYOUT.items():
        path = root / directory / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payloads[kind])
        paths[kind] = path
    return paths


def payloads(*, malformed: bool = False) -> dict[AssetKind, bytes]:
    return {
        AssetKind.ORIGINAL_IMAGE: b"not-a-png" if malformed else b"image-bytes\x00\xff",
        AssetKind.RAW_MASK: b"not-a-mask" if malformed else b"mask-instance-ids\x00\x02",
        AssetKind.ANNOTATION_TEXT: b"\xff\xfe malformed text" if malformed else b"annotation\r\nexact\n",
    }


def destination_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and ".locks" not in path.relative_to(root).parts
    )


def test_capture_preserves_exact_bytes_and_is_independent_of_source_changes(tmp_path):
    source_root = tmp_path / "dataset"
    storage_root = tmp_path / "managed"
    original = payloads()
    source_paths = write_sample(source_root, original)
    os.link(source_paths[AssetKind.ORIGINAL_IMAGE], source_root / "PNGImages" / "other-link.png")
    repository = MemoryManifestRepository(storage_root)

    manifest = capture_sample(
        dataset_id="dataset_1", sample_id="sample", sample_revision=1,
        source_root=source_root, repository=repository,
    )

    destinations = {}
    for kind, (_, _, content_type) in SOURCE_LAYOUT.items():
        entry = getattr(manifest, kind.value)
        destination = storage_root / entry.storage_key
        destinations[kind] = destination
        assert entry.availability is AssetAvailability.AVAILABLE
        assert entry.content_type == content_type
        assert destination.read_bytes() == original[kind]
        assert entry.size_bytes == len(original[kind])
        assert entry.sha256 == sha256(original[kind]).hexdigest()
        assert not destination.is_symlink()
        assert destination.stat().st_nlink == 1
        assert not os.path.samefile(source_paths[kind], destination)

    source_paths[AssetKind.ORIGINAL_IMAGE].write_bytes(b"changed image")
    source_paths[AssetKind.RAW_MASK].unlink()
    source_paths[AssetKind.ANNOTATION_TEXT].write_bytes(b"changed annotation")

    assert all(destinations[kind].read_bytes() == original[kind] for kind in destinations)


@pytest.mark.parametrize("missing_kind", list(AssetKind))
def test_capture_records_missing_assets_independently(tmp_path, missing_kind):
    source_root = tmp_path / "dataset"
    repository = MemoryManifestRepository(tmp_path / "managed")
    paths = write_sample(source_root, payloads())
    paths[missing_kind].unlink()

    manifest = capture_sample(
        dataset_id="dataset_1", sample_id="sample", sample_revision=1,
        source_root=source_root, repository=repository,
    )

    missing = getattr(manifest, missing_kind.value)
    assert missing.availability is AssetAvailability.MISSING
    assert missing.storage_key is None
    for kind in AssetKind:
        if kind is not missing_kind:
            assert getattr(manifest, kind.value).availability is AssetAvailability.AVAILABLE


def test_capture_preserves_readable_malformed_bytes_for_later_audit(tmp_path):
    source_root = tmp_path / "dataset"
    repository = MemoryManifestRepository(tmp_path / "managed")
    original = payloads(malformed=True)
    write_sample(source_root, original)

    manifest = capture_sample(
        dataset_id="dataset_1", sample_id="sample", sample_revision=1,
        source_root=source_root, repository=repository,
    )

    for kind, content in original.items():
        entry = getattr(manifest, kind.value)
        assert entry.availability is AssetAvailability.AVAILABLE
        assert (repository.asset_settings.root / entry.storage_key).read_bytes() == content


def test_partial_storage_failure_does_not_publish_or_leave_files(tmp_path):
    source_root = tmp_path / "dataset"
    storage_root = tmp_path / "managed"
    write_sample(source_root, payloads())
    repository = MemoryManifestRepository(storage_root)
    real_copy = asset_snapshot._copy_exact
    calls = 0

    def fail_on_second_copy(source, temporary):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise AssetSnapshotStorageError("synthetic managed-storage failure")
        return real_copy(source, temporary)

    with pytest.raises(AssetSnapshotStorageError):
        capture_sample(
            dataset_id="dataset_1", sample_id="sample", sample_revision=1,
            source_root=source_root, repository=repository, copy_file=fail_on_second_copy,
        )

    assert repository.manifests == {}
    assert destination_files(storage_root) == []


def test_same_content_replay_reuses_the_existing_snapshot(tmp_path):
    source_root = tmp_path / "dataset"
    storage_root = tmp_path / "managed"
    write_sample(source_root, payloads())
    repository = MemoryManifestRepository(storage_root)

    first = capture_sample(
        dataset_id="dataset_1", sample_id="sample", sample_revision=1,
        source_root=source_root, repository=repository,
    )
    before = {path: (path.stat().st_ino, path.read_bytes()) for path in destination_files(storage_root)}
    second = capture_sample(
        dataset_id="dataset_1", sample_id="sample", sample_revision=1,
        source_root=source_root, repository=repository,
    )

    after = {path: (path.stat().st_ino, path.read_bytes()) for path in destination_files(storage_root)}
    assert second == first
    assert after == before


def test_revision_conflict_preserves_original_snapshot_and_cleans_new_bytes(tmp_path):
    source_root = tmp_path / "dataset"
    storage_root = tmp_path / "managed"
    source_paths = write_sample(source_root, payloads())
    repository = MemoryManifestRepository(storage_root)
    original = capture_sample(
        dataset_id="dataset_1", sample_id="sample", sample_revision=1,
        source_root=source_root, repository=repository,
    )
    original_image = storage_root / original.original_image.storage_key
    source_paths[AssetKind.ORIGINAL_IMAGE].write_bytes(b"different image bytes")

    with pytest.raises(SampleAssetManifestConflictError):
        capture_sample(
            dataset_id="dataset_1", sample_id="sample", sample_revision=1,
            source_root=source_root, repository=repository,
        )

    assert original_image.read_bytes() == payloads()[AssetKind.ORIGINAL_IMAGE]
    assert len(destination_files(storage_root)) == 3


def test_failed_capture_cleanup_cannot_remove_a_concurrent_capture(tmp_path):
    source_root = tmp_path / "dataset"
    write_sample(source_root, payloads())
    repository = MemoryManifestRepository(tmp_path / "managed")
    first_installed = Event()
    release_failure = Event()
    second_started = Event()
    second_copying = Event()
    real_copy = asset_snapshot._copy_exact

    def failing_copy(source, temporary):
        if source.parent.name == "PedMasks":
            first_installed.set()
            assert release_failure.wait(5)
            raise AssetSnapshotStorageError("synthetic storage failure")
        return real_copy(source, temporary)

    def succeeding_copy(source, temporary):
        second_copying.set()
        return real_copy(source, temporary)

    def capture(copy_file):
        if copy_file is succeeding_copy:
            second_started.set()
        return capture_sample(
            dataset_id="dataset_1", sample_id="sample", sample_revision=1,
            source_root=source_root, repository=repository, copy_file=copy_file,
        )

    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(capture, failing_copy)
        try:
            assert first_installed.wait(5)
            second = workers.submit(capture, succeeding_copy)
            assert second_started.wait(5)
            assert not second_copying.wait(0.2)
        finally:
            release_failure.set()
        with pytest.raises(AssetSnapshotStorageError):
            first.result(timeout=5)
        manifest = second.result(timeout=5)

    assert second_copying.is_set()
    for kind, content in payloads().items():
        assert (repository.asset_settings.root / getattr(manifest, kind.value).storage_key).read_bytes() == content


def test_unknown_manifest_commit_keeps_files_for_safe_replay(tmp_path):
    source_root = tmp_path / "dataset"
    write_sample(source_root, payloads())

    class LostAcknowledgementRepository(MemoryManifestRepository):
        first = True

        def save_manifest(self, manifest):
            result = super().save_manifest(manifest)
            if self.first:
                self.first = False
                raise RetryableWorkflowStorageError("lost acknowledgement", commit_outcome_unknown=True)
            return result

    repository = LostAcknowledgementRepository(tmp_path / "managed")
    arguments = dict(
        dataset_id="dataset_1", sample_id="sample", sample_revision=1,
        source_root=source_root, repository=repository,
    )
    with pytest.raises(RetryableWorkflowStorageError):
        capture_sample(**arguments)
    assert len(destination_files(repository.asset_settings.root)) == 3
    manifest = capture_sample(**arguments)
    for kind, content in payloads().items():
        assert (repository.asset_settings.root / getattr(manifest, kind.value).storage_key).read_bytes() == content


def test_materialization_write_failure_is_a_storage_error(tmp_path, monkeypatch):
    source_root = tmp_path / "dataset"
    write_sample(source_root, payloads())
    repository = MemoryManifestRepository(tmp_path / "managed")
    manifest = capture_sample(
        dataset_id="dataset_1", sample_id="sample", sample_revision=1,
        source_root=source_root, repository=repository,
    )

    def disk_full(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", disk_full)
    with pytest.raises(AssetSnapshotStorageError, match="could not be written"):
        materialize_sample(
            manifest, asset_settings=repository.asset_settings, target_root=tmp_path / "working",
        )


@pytest.mark.parametrize("error_type", [
    JobCancellationError, JobClaimLostError, SampleProcessingTimeoutError, RuntimeError,
])
def test_cleanup_failure_preserves_the_processing_error(tmp_path, monkeypatch, caplog, error_type):
    class BrokenCleanup:
        name = str(tmp_path)

        def cleanup(self):
            raise OSError("synthetic cleanup failure")

    monkeypatch.setattr(asset_snapshot, "TemporaryDirectory", lambda **kwargs: BrokenCleanup())
    original = error_type("original processing error")
    with pytest.raises(error_type) as caught:
        with sample_work_directory():
            raise original
    assert caught.value is original
    assert "Sample processing storage cleanup failed" in caplog.text


def test_cleanup_failure_after_success_is_still_a_storage_error(tmp_path, monkeypatch):
    class BrokenCleanup:
        name = str(tmp_path)

        def cleanup(self):
            raise OSError("synthetic cleanup failure")

    monkeypatch.setattr(asset_snapshot, "TemporaryDirectory", lambda **kwargs: BrokenCleanup())
    with pytest.raises(AssetSnapshotStorageError, match="cleaned up"):
        with sample_work_directory() as working:
            assert working == tmp_path
