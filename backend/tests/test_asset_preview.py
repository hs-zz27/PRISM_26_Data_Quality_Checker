"""Display previews preserve revision selection and raw instance-mask semantics."""

from hashlib import sha256
from io import BytesIO

import numpy as np
from PIL import Image
import pytest

from backend.app.core.config import AssetStorageSettings
from backend.app.models.asset_manifest import AssetKind, AssetManifestEntry, SampleAssetManifest
from backend.app.services.asset_preview import (
    MAX_PREVIEW_SOURCE_BYTES, PreviewKind, PreviewUnavailableError, SamplePreviewService,
)
from backend.app.storage.workflow_repository import (
    AssetIntegrityError, AssetReadLimitError, AssetUnavailableError, SampleAssetManifestNotFoundError,
    SampleAssetManifestRepository,
)


def png(image):
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class Assets:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def read_asset_bytes(self, *identity, max_bytes=None):
        assert max_bytes == MAX_PREVIEW_SOURCE_BYTES
        self.calls.append(identity)
        if len(self.content) > max_bytes:
            raise AssetReadLimitError("source exceeds byte budget")
        return self.content


def render(content, kind="mask_preview"):
    repository = Assets(content)
    result = SamplePreviewService(repository).render("dataset", "sample", 3, kind)
    with Image.open(BytesIO(result.content)) as image:
        pixels = np.array(image)
        assert image.format == "PNG"
        assert image.size == (result.width, result.height)
    assert result.content_type == "image/png"
    return result, pixels, repository


def test_thumbnail_keeps_aspect_ratio_and_uses_the_exact_revision():
    original = png(Image.new("RGB", (800, 400), "red"))
    result, pixels, repository = render(original, PreviewKind.THUMBNAIL)
    assert (result.width, result.height) == (256, 128)
    assert tuple(pixels[0, 0]) == (255, 0, 0, 255)
    assert repository.calls == [("dataset", "sample", 3, AssetKind.ORIGINAL_IMAGE)]
    assert repository.content == original


def test_thumbnail_does_not_enlarge_small_images_or_discard_transparency():
    result, pixels, _ = render(png(Image.new("RGBA", (8, 4), (10, 20, 30, 0))), "thumbnail")
    assert (result.width, result.height) == (8, 4)
    assert np.all(pixels[:, :, 3] == 0)


def test_thumbnail_keeps_source_coordinates_and_strips_orientation_metadata():
    image = Image.new("RGB", (8, 4), "red")
    exif = Image.Exif()
    exif[274] = 6
    output = BytesIO()
    image.save(output, format="PNG", exif=exif)
    result, _, _ = render(output.getvalue(), "thumbnail")
    assert (result.width, result.height) == (8, 4)
    with Image.open(BytesIO(result.content)) as preview:
        assert not preview.getexif()


def test_mask_colors_preserve_dimensions_background_and_full_integer_ids():
    mask = np.array([[0, 1, 257], [65535, 257, 1]], dtype=np.uint16)
    original = png(Image.fromarray(mask))
    result, pixels, repository = render(original)
    assert (result.width, result.height) == (3, 2)
    assert tuple(pixels[0, 0]) == (0, 0, 0, 0)
    assert np.all(pixels[mask > 0, 3] == 255)
    assert np.array_equal(pixels[0, 1], pixels[1, 2])
    assert np.array_equal(pixels[0, 2], pixels[1, 1])
    assert len({tuple(color) for color in pixels[mask > 0]}) == 3
    assert repository.calls == [("dataset", "sample", 3, AssetKind.RAW_MASK)]
    with Image.open(BytesIO(original)) as raw:
        assert np.array_equal(np.asarray(raw), mask)


def test_colors_are_deterministic_and_independent_of_other_present_ids():
    content = png(Image.fromarray(np.array([[0, 257]], dtype=np.uint16)))
    first, pixels, _ = render(content)
    second, _, _ = render(content)
    _, changed, _ = render(png(Image.fromarray(np.array([[1, 257, 300]], dtype=np.uint16))))
    assert first.content == second.content
    assert np.array_equal(pixels[0, 1], changed[0, 1])


def test_every_16_bit_instance_id_has_a_distinct_color():
    mask = np.arange(65536, dtype=np.uint16).reshape(256, 256)
    _, pixels, _ = render(png(Image.fromarray(mask)))
    colors = pixels[:, :, :3].reshape(-1, 3)
    assert len(np.unique(colors, axis=0)) == 65536
    assert pixels[0, 0, 3] == 0
    assert np.all(pixels[:, :, 3].ravel()[1:] == 255)


def test_palette_masks_use_indices_instead_of_palette_colors():
    mask = Image.new("P", (2, 1))
    mask.putdata([0, 9])
    mask.putpalette([255, 0, 0] * 256)
    _, indexed, _ = render(png(mask))
    _, grayscale, _ = render(png(Image.fromarray(np.array([[0, 9]], dtype=np.uint8))))
    assert np.array_equal(indexed, grayscale)


@pytest.mark.parametrize("mode,color", [("RGB", (1, 2, 3)), ("RGBA", (1, 2, 3, 255)), ("1", 1)])
def test_non_instance_masks_are_not_silently_converted(mode, color):
    with pytest.raises(PreviewUnavailableError, match="2D integer"):
        render(png(Image.new(mode, (2, 2), color)))


@pytest.mark.parametrize("kind", list(PreviewKind))
def test_malformed_captured_bytes_have_a_safe_preview_error(kind):
    with pytest.raises(PreviewUnavailableError, match="could not be decoded") as caught:
        render(b"broken PNG /private/example/source.png", kind)
    assert "/private" not in str(caught.value)


@pytest.mark.parametrize("kind", list(PreviewKind))
def test_invalid_pixel_chunk_checksum_is_rejected_even_with_decodable_pixels(kind):
    content = bytearray(png(Image.new("L", (2, 2), 1)))
    chunk_type = content.index(b"IDAT")
    length = int.from_bytes(content[chunk_type - 4:chunk_type], "big")
    checksum = chunk_type + 4 + length
    content[checksum] ^= 1
    with pytest.raises(PreviewUnavailableError, match="could not be decoded"):
        render(bytes(content), kind)


def test_preview_byte_budget_has_a_safe_error_before_decoding():
    class OversizedAsset:
        def read_asset_bytes(self, *args, max_bytes):
            assert max_bytes == MAX_PREVIEW_SOURCE_BYTES
            raise AssetReadLimitError("private internal detail")

    with pytest.raises(PreviewUnavailableError, match="byte limit") as caught:
        SamplePreviewService(OversizedAsset()).render("dataset", "sample", 3, "thumbnail")
    assert "private" not in str(caught.value)


def test_oversized_preview_is_rejected_before_pixel_decoding(monkeypatch):
    content = png(Image.new("L", (3, 2)))
    monkeypatch.setattr("backend.app.services.asset_preview.MAX_PREVIEW_PIXELS", 5)
    with pytest.raises(PreviewUnavailableError, match="pixel limit"):
        render(content)


def test_non_png_source_is_not_rendered_under_a_png_manifest():
    output = BytesIO()
    Image.new("RGB", (2, 2)).save(output, format="JPEG")
    with pytest.raises(PreviewUnavailableError, match="not a PNG"):
        render(output.getvalue(), "thumbnail")


def test_animated_png_does_not_silently_select_one_frame():
    output = BytesIO()
    Image.new("RGB", (2, 2), "red").save(
        output, format="PNG", save_all=True,
        append_images=[Image.new("RGB", (2, 2), "blue")], duration=100,
    )
    with pytest.raises(PreviewUnavailableError, match="Animated PNG"):
        render(output.getvalue(), "thumbnail")


def test_unsupported_preview_kind_is_rejected_before_reading_assets():
    repository = Assets(b"unused")
    with pytest.raises(ValueError):
        SamplePreviewService(repository).render("dataset", "sample", 3, "unknown")
    assert repository.calls == []


@pytest.mark.parametrize("error", [
    SampleAssetManifestNotFoundError("dataset", "sample", 3),
    AssetUnavailableError("dataset", "sample", 3, AssetKind.RAW_MASK, "missing"),
    AssetIntegrityError("Stored bytes no longer match"),
])
def test_storage_errors_are_preserved_without_a_fallback(error):
    class BrokenAssets:
        def read_asset_bytes(self, *args, **kwargs):
            raise error

    with pytest.raises(type(error)) as caught:
        SamplePreviewService(BrokenAssets()).render("dataset", "sample", 3, "mask_preview")
    assert caught.value is error


def test_preview_reads_verified_managed_bytes_and_never_writes_them(tmp_path):
    content = png(Image.fromarray(np.array([[0, 400]], dtype=np.uint16)))
    stored = tmp_path / "mask.png"
    stored.write_bytes(content)
    missing = AssetManifestEntry(availability="missing")
    manifest = SampleAssetManifest(
        dataset_id="dataset", sample_id="sample", sample_revision=3,
        original_image=missing, annotation_text=missing,
        raw_mask=AssetManifestEntry(
            availability="available", storage_key="mask.png", content_type="image/png",
            size_bytes=len(content), sha256=sha256(content).hexdigest(),
        ),
    )

    class ManagedAssets(SampleAssetManifestRepository):
        def __init__(self):
            self.asset_settings = AssetStorageSettings(root=tmp_path)

        def get_manifest(self, dataset_id, sample_id, sample_revision):
            assert (dataset_id, sample_id, sample_revision) == ("dataset", "sample", 3)
            return manifest

    service = SamplePreviewService(ManagedAssets())
    result = service.render("dataset", "sample", 3, "mask_preview")
    assert (result.width, result.height) == (2, 1)
    assert stored.read_bytes() == content
    assert set(tmp_path.iterdir()) == {stored}
    stored.write_bytes(b"tampered")
    with pytest.raises(AssetIntegrityError):
        service.render("dataset", "sample", 3, "mask_preview")
