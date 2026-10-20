"""Authored upload durability/unknown-commit regressions. NOT executed."""
from hashlib import sha256
from pathlib import Path

import pytest

from backend.app.core.dataset_import import ImportAcceptanceError, ImportOutcomeUnresolved, ImportSettings, UploadTooLargeError
from backend.app.models.dataset_import import ImportPolicy
from backend.app.services.dataset_upload import CHUNK_BYTES, submit_archive
from backend.app.storage.dataset_repository import DatasetCommitOutcomeUnknownError, DatasetStorageError


class Ledger:
    def __init__(self, error=None):
        self.error, self.request = error, None

    def enqueue(self, request, **kwargs):
        self.request = request
        if self.error:
            raise self.error
        return request


def settings(tmp_path, **updates):
    return ImportSettings(root=tmp_path / "imports", asset_root=tmp_path / "assets", policy=ImportPolicy(**updates))


def test_stream_is_closed_and_durable_before_enqueue(tmp_path):
    effective = settings(tmp_path)
    class ObservingLedger(Ledger):
        def enqueue(self, request, **kwargs):
            path = effective.root / request.archive_key
            assert path.read_bytes() == b"abcdef"
            assert not path.with_suffix(".part").exists()
            assert request.started_at is None
            assert request.archive_sha256 == sha256(b"abcdef").hexdigest()
            assert request.archive_size_bytes == 6
            return super().enqueue(request, **kwargs)
    request = submit_archive(iter([b"abc", b"", b"def"]), name="Example", repository=ObservingLedger(), settings=effective)
    assert (effective.root / request.archive_key).is_file()
    # A new service object needs no live UploadFile or in-memory queue to locate it.
    assert effective.root.joinpath(request.archive_key).read_bytes() == b"abcdef"
    second = submit_archive([b"abcdef"], name="Example", repository=Ledger(), settings=effective)
    assert second.dataset_id != request.dataset_id and second.archive_key != request.archive_key


@pytest.mark.parametrize("failure", [DatasetStorageError("safe"), OSError("private-path")])
def test_known_rollback_cleans_only_this_submission(tmp_path, failure):
    effective = settings(tmp_path)
    old = effective.root / "uploads" / "another_request"
    old.mkdir(parents=True)
    (old / "archive.zip").write_bytes(b"previous")
    with pytest.raises(ImportAcceptanceError):
        submit_archive([b"abcdef"], name="Example", repository=Ledger(failure), settings=effective)
    assert sorted(item.name for item in old.parent.iterdir()) == ["another_request"]
    assert (old / "archive.zip").read_bytes() == b"previous"


def test_unknown_commit_retains_maybe_referenced_archive_without_replay(tmp_path):
    effective = settings(tmp_path)
    ledger = Ledger(DatasetCommitOutcomeUnknownError("unknown"))
    with pytest.raises(ImportOutcomeUnresolved):
        submit_archive([b"abcdef"], name="Example", repository=ledger, settings=effective)
    assert (effective.root / ledger.request.archive_key).read_bytes() == b"abcdef"


@pytest.mark.parametrize("chunks", [[], [b""], [bytearray(b"not-bytes")], [b"x" * (CHUNK_BYTES + 1)]])
def test_invalid_stream_does_not_enqueue(tmp_path, chunks):
    ledger = Ledger()
    with pytest.raises(ValueError):
        submit_archive(chunks, name="Example", repository=ledger, settings=settings(tmp_path))
    assert ledger.request is None


def test_measured_cap_and_source_exception_cleanup(tmp_path):
    effective = settings(tmp_path, max_archive_bytes=5)
    with pytest.raises(UploadTooLargeError):
        submit_archive([b"abc", b"def"], name="Example", repository=Ledger(), settings=effective)
    assert list((effective.root / "uploads").iterdir()) == []
    def failing():
        yield b"abc"
        raise OSError("private source")
    with pytest.raises(ImportAcceptanceError) as caught:
        submit_archive(failing(), name="Example", repository=Ledger(), settings=effective)
    assert "private" not in str(caught.value)
    assert list((effective.root / "uploads").iterdir()) == []


@pytest.mark.parametrize("name,format", [(" ", "penn_fudan"), ("x", "coco"), (True, "penn_fudan")])
def test_fields_rejected_before_staging(tmp_path, name, format):
    effective = settings(tmp_path)
    with pytest.raises(ValueError):
        submit_archive([b"abc"], name=name, format=format, repository=Ledger(), settings=effective)
    assert not effective.root.exists()
