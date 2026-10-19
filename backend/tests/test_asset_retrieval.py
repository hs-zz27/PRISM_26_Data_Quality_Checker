"""Revision-pinned source and preview retrieval service coverage."""

from io import BytesIO
from hashlib import sha256

from PIL import Image
import numpy as np
import pytest

from backend.app.core.config import AssetStorageSettings
from backend.app.models.asset_manifest import AssetKind, AssetManifestEntry, SampleAssetManifest
from backend.app.services.asset_preview import (
    MAX_PREVIEW_SOURCE_BYTES, PreviewKind, PreviewUnavailableError, RenderedPreview,
)
from backend.app.services.asset_retrieval import PublicAssetKind, RetrievedAsset, SampleAssetService
from backend.app.storage.workflow_repository import (
    AssetIntegrityError, AssetReadLimitError, AssetUnavailableError,
    SampleAssetManifestNotFoundError, SampleAssetManifestRepository,
)


def _png(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _manifest(revision: int = 2, *, content_type: str = "image/png") -> SampleAssetManifest:
    missing = AssetManifestEntry(availability="missing")
    content = f"image-{revision}".encode()
    return SampleAssetManifest(
        dataset_id="dataset",
        sample_id="sample",
        sample_revision=revision,
        original_image=AssetManifestEntry(
            availability="available", storage_key=f"image-{revision}.png",
            content_type=content_type, size_bytes=len(content),
            sha256=sha256(content).hexdigest(),
        ),
        raw_mask=AssetManifestEntry(
            availability="available", storage_key=f"mask-{revision}.png",
            content_type="image/png", size_bytes=len(content),
            sha256=sha256(content).hexdigest(),
        ),
        annotation_text=missing,
    )


class Repository:
    def __init__(self, manifests, contents):
        self.manifests = manifests
        self.contents = contents
        self.manifest_calls = []
        self.read_calls = []

    def get_manifest(self, dataset_id, sample_id, sample_revision):
        self.manifest_calls.append((dataset_id, sample_id, sample_revision))
        return self.manifests[(dataset_id, sample_id, sample_revision)]

    def read_asset_bytes(self, dataset_id, sample_id, sample_revision, asset_kind, *, max_bytes=None):
        self.get_manifest(dataset_id, sample_id, sample_revision)
        self.read_calls.append((dataset_id, sample_id, sample_revision, asset_kind, max_bytes))
        return self.contents[(dataset_id, sample_id, sample_revision, asset_kind)]


def test_raw_assets_use_the_explicit_revision_and_preserve_exact_16_bit_mask_bytes():
    mask = np.array([[0, 1], [257, 65535]], dtype=np.uint16)
    raw_mask = _png(Image.fromarray(mask))
    image = b"exact-image-bytes"
    manifests = {
        ("dataset", "sample", 1): _manifest(1),
        ("dataset", "sample", 2): _manifest(2),
    }
    repository = Repository(
        manifests,
        {
            ("dataset", "sample", 2, AssetKind.ORIGINAL_IMAGE): image,
            ("dataset", "sample", 2, AssetKind.RAW_MASK): raw_mask,
        },
    )
    service = SampleAssetService(repository)

    retrieved_image = service.retrieve("dataset", "sample", 2, "image")
    retrieved_mask = service.retrieve("dataset", "sample", 2, "mask")

    assert retrieved_image == RetrievedAsset(image, "image/png")
    assert retrieved_mask == RetrievedAsset(raw_mask, "image/png")
    assert repository.manifest_calls == [
        ("dataset", "sample", 2), ("dataset", "sample", 2),
    ]
    assert repository.read_calls == [
        ("dataset", "sample", 2, AssetKind.ORIGINAL_IMAGE, MAX_PREVIEW_SOURCE_BYTES),
        ("dataset", "sample", 2, AssetKind.RAW_MASK, MAX_PREVIEW_SOURCE_BYTES),
    ]
    with Image.open(BytesIO(retrieved_mask.content)) as decoded:
        assert np.array_equal(np.asarray(decoded), mask)


def test_raw_delivery_uses_a_server_selected_type_and_one_manifest_lookup(tmp_path):
    content = b"<html>malformed captured image</html>"
    (tmp_path / "image.png").write_bytes(content)
    missing = AssetManifestEntry(availability="missing")
    manifest = SampleAssetManifest(
        dataset_id="dataset", sample_id="sample", sample_revision=2,
        original_image=AssetManifestEntry(
            availability="available", storage_key="image.png", content_type="text/html",
            sha256=sha256(content).hexdigest(), size_bytes=len(content),
        ),
        raw_mask=missing, annotation_text=missing,
    )

    class ManagedAssets(SampleAssetManifestRepository):
        def __init__(self):
            self.asset_settings = AssetStorageSettings(root=tmp_path)
            self.calls = []

        def get_manifest(self, *identity):
            self.calls.append(identity)
            return manifest

    repository = ManagedAssets()
    result = SampleAssetService(repository).retrieve("dataset", "sample", 2, "image")

    assert result == RetrievedAsset(content, "image/png")
    assert repository.calls == [("dataset", "sample", 2)]
    assert (tmp_path / "image.png").read_bytes() == content


def test_preview_kinds_delegate_to_the_existing_preview_service():
    class Preview:
        def __init__(self):
            self.calls = []

        def render(self, *args):
            self.calls.append(args)
            return RenderedPreview(b"derived-preview", 4, 3)

    repository = Repository({}, {})
    preview = Preview()
    service = SampleAssetService(repository, preview_service=preview)

    assert service.retrieve("dataset", "sample", 7, PublicAssetKind.MASK_PREVIEW) == RetrievedAsset(
        b"derived-preview", "image/png",
    )
    assert service.retrieve("dataset", "sample", 7, "thumbnail") == RetrievedAsset(
        b"derived-preview", "image/png",
    )
    assert preview.calls == [
        ("dataset", "sample", 7, PreviewKind.MASK_PREVIEW),
        ("dataset", "sample", 7, PreviewKind.THUMBNAIL),
    ]
    assert repository.read_calls == []


@pytest.mark.parametrize("error", [
    SampleAssetManifestNotFoundError("dataset", "sample", 2),
    AssetUnavailableError("dataset", "sample", 2, AssetKind.RAW_MASK, "missing"),
    AssetIntegrityError("stored bytes changed"),
    AssetReadLimitError("source exceeds the read budget"),
])
def test_raw_storage_errors_are_propagated_unchanged(error):
    class BrokenRepository(Repository):
        def read_asset_bytes(self, *args, **kwargs):
            raise error

    service = SampleAssetService(BrokenRepository({}, {}))
    with pytest.raises(type(error)) as caught:
        service.retrieve("dataset", "sample", 2, "mask")
    assert caught.value is error


def test_preview_errors_are_propagated_unchanged():
    error = PreviewUnavailableError("captured PNG could not be decoded")

    class BrokenPreview:
        def render(self, *args):
            raise error

    service = SampleAssetService(Repository({}, {}), preview_service=BrokenPreview())
    with pytest.raises(PreviewUnavailableError) as caught:
        service.retrieve("dataset", "sample", 2, "thumbnail")
    assert caught.value is error


@pytest.mark.parametrize("revision", [0, -1, True, "2"])
def test_revision_must_be_explicit_and_positive(revision):
    repository = Repository({}, {})
    service = SampleAssetService(repository)
    with pytest.raises(ValueError):
        service.retrieve("dataset", "sample", revision, "image")
    assert repository.manifest_calls == []


def test_unknown_public_kind_is_rejected_before_storage_access():
    repository = Repository({}, {})
    with pytest.raises(ValueError):
        SampleAssetService(repository).retrieve("dataset", "sample", 2, "annotation_text")
    assert repository.manifest_calls == []
