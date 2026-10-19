"""Focused validation for private immutable sample-asset metadata."""

from hashlib import sha256

import pytest
from pydantic import ValidationError

from backend.app.models.asset_manifest import (
    AssetAvailability,
    AssetManifestEntry,
    SampleAssetManifest,
)


def available(key: str, content: bytes, content_type: str) -> AssetManifestEntry:
    return AssetManifestEntry(
        availability=AssetAvailability.AVAILABLE,
        storage_key=key,
        content_type=content_type,
        sha256=sha256(content).hexdigest(),
        size_bytes=len(content),
    )


def make_manifest() -> SampleAssetManifest:
    return SampleAssetManifest(
        dataset_id="dataset_1",
        sample_id="sample_1",
        sample_revision=2,
        original_image=available("dataset_1/sample_1/2/image", b"image", "image/png"),
        raw_mask=available("dataset_1/sample_1/2/mask", b"mask", "image/png"),
        annotation_text=available("dataset_1/sample_1/2/annotation", b"annotation", "text/plain"),
    )


def test_manifest_round_trip_keeps_exact_source_identity():
    original = make_manifest()
    restored = SampleAssetManifest.model_validate(original.model_dump(mode="json"))
    assert restored == original
    assert restored.sample_revision == 2
    assert restored.original_image.sha256 == sha256(b"image").hexdigest()


@pytest.mark.parametrize("key", [
    "", " ", "/absolute/file", "C:\\absolute\\file", "sample/../file", "sample/./file",
    "sample//file", "sample/file/",
])
def test_storage_keys_cannot_escape_private_root(key):
    with pytest.raises(ValidationError):
        AssetManifestEntry(
            availability="available", storage_key=key, content_type="image/png",
            sha256="0" * 64, size_bytes=1,
        )


def test_unavailable_assets_do_not_claim_file_identity():
    with pytest.raises(ValidationError):
        AssetManifestEntry(
            availability="missing", storage_key="sample/image", content_type="image/png",
            sha256="0" * 64, size_bytes=1,
        )
    assert AssetManifestEntry(availability="unreadable").storage_key is None


def test_manifest_is_strict_and_requires_all_source_roles():
    values = make_manifest().model_dump()
    values.pop("raw_mask")
    with pytest.raises(ValidationError):
        SampleAssetManifest.model_validate(values)
    with pytest.raises(ValidationError):
        SampleAssetManifest.model_validate({**make_manifest().model_dump(), "private_path": "/secret"})
