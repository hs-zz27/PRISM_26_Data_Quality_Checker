"""Managed-file reads enforce metadata and byte budgets before allocation."""

from hashlib import sha256
from pathlib import Path

import pytest

from backend.app.core.config import AssetStorageSettings
from backend.app.models.asset_manifest import AssetManifestEntry, SampleAssetManifest
from backend.app.storage.workflow_repository import (
    AssetIntegrityError, AssetReadLimitError, read_manifest_asset_file,
)


@pytest.fixture
def stored_asset(tmp_path):
    content = b"original bytes"
    path = tmp_path / "image.png"
    path.write_bytes(content)
    missing = AssetManifestEntry(availability="missing")
    manifest = SampleAssetManifest(
        dataset_id="dataset", sample_id="sample", sample_revision=1,
        original_image=AssetManifestEntry(
            availability="available", storage_key=path.name, content_type="image/png",
            size_bytes=len(content), sha256=sha256(content).hexdigest(),
        ),
        raw_mask=missing, annotation_text=missing,
    )
    return path, content, manifest, AssetStorageSettings(root=tmp_path)


def test_manifest_over_budget_is_rejected_without_opening_the_file(stored_asset, monkeypatch):
    _, content, manifest, settings = stored_asset

    def forbidden_open(*args, **kwargs):
        raise AssertionError("An oversized asset must not be opened")

    monkeypatch.setattr(Path, "open", forbidden_open)
    with pytest.raises(AssetReadLimitError):
        read_manifest_asset_file(manifest, "original_image", settings, max_bytes=len(content) - 1)


@pytest.mark.parametrize("change", ["none", "before_open", "during_read"])
def test_reads_are_bounded_and_reject_a_growing_file(stored_asset, monkeypatch, change):
    path, content, manifest, settings = stored_asset
    if change == "before_open":
        path.write_bytes(content + b"extra bytes")
    original_open = Path.open
    read_sizes = []

    class GuardedFile:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, size):
            read_sizes.append(size)
            assert size == len(content) + 1
            if change == "during_read":
                with original_open(path, "ab") as writer:
                    writer.write(b"extra bytes")
            return self.stream.read(size)

    monkeypatch.setattr(Path, "open", lambda self, *args, **kwargs: GuardedFile(
        original_open(self, *args, **kwargs),
    ))
    if change == "none":
        _, saved = read_manifest_asset_file(
            manifest, "original_image", settings, max_bytes=len(content),
        )
        assert saved == content
    else:
        with pytest.raises(AssetIntegrityError):
            read_manifest_asset_file(manifest, "original_image", settings, max_bytes=len(content))
    assert read_sizes == ([] if change == "before_open" else [len(content) + 1])


@pytest.mark.parametrize("limit", [True, 0, -1, "100"])
def test_invalid_read_limits_are_rejected(stored_asset, limit):
    _, _, manifest, settings = stored_asset
    with pytest.raises(ValueError, match="positive integer"):
        read_manifest_asset_file(manifest, "original_image", settings, max_bytes=limit)
