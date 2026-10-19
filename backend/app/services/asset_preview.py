"""Render display-only PNGs from verified, revision-pinned source assets."""

from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from typing import ClassVar

import numpy as np
from PIL import Image

from ..models.asset_manifest import AssetKind
from ..storage.workflow_repository import SampleAssetManifestRepository


THUMBNAIL_MAX_SIZE = (256, 256)
MAX_PREVIEW_PIXELS = 16_000_000
MAX_PREVIEW_SOURCE_BYTES = 64 * 1024 * 1024
MAX_MASK_ID = 65535


class PreviewKind(StrEnum):
    THUMBNAIL = "thumbnail"
    MASK_PREVIEW = "mask_preview"


class PreviewUnavailableError(RuntimeError):
    """Captured bytes cannot be rendered as the requested display asset."""


@dataclass(frozen=True)
class RenderedPreview:
    content: bytes
    width: int
    height: int
    content_type: ClassVar[str] = "image/png"


def _decode_png(content: bytes) -> Image.Image:
    try:
        with Image.open(BytesIO(content)) as source:
            if source.format != "PNG":
                raise PreviewUnavailableError("The captured asset is not a PNG image.")
            if source.width * source.height > MAX_PREVIEW_PIXELS:
                raise PreviewUnavailableError("The captured image exceeds the preview pixel limit.")
            if getattr(source, "n_frames", 1) != 1:
                raise PreviewUnavailableError("Animated PNGs are not supported for sample previews.")
            source.verify()
        # verify() consumes the PNG parser; decode the same verified bytes afresh.
        with Image.open(BytesIO(content)) as source:
            source.load()
            return source.copy()
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as error:
        raise PreviewUnavailableError("The captured PNG could not be decoded.") from error


def _encode_png(image: Image.Image) -> RenderedPreview:
    output = BytesIO()
    # Derived display files carry no source metadata or orientation instructions.
    image.info.clear()
    image.save(output, format="PNG")
    return RenderedPreview(output.getvalue(), image.width, image.height)


def _mask_preview(image: Image.Image) -> Image.Image:
    # Reading palette indices directly preserves instance IDs; converting to
    # RGB here would replace those IDs with the source palette's display colors.
    mask = np.asarray(image)
    if mask.ndim != 2 or not np.issubdtype(mask.dtype, np.integer):
        raise PreviewUnavailableError("A mask preview requires a 2D integer instance mask.")
    if mask.min() < 0 or mask.max() > MAX_MASK_ID:
        raise PreviewUnavailableError("PNG instance mask IDs must be between 0 and 65535.")

    # Multiplication by an odd number permutes the 24-bit color space. Thus all
    # 16-bit IDs have distinct, stable RGB values, regardless of present objects.
    # A fixed 256 KiB lookup table also avoids a pixel-sized sort/inverse array.
    ids = np.arange(MAX_MASK_ID + 1, dtype=np.uint32)
    colors = (ids * np.uint32(0x9E3779)) & np.uint32(0xFFFFFF)
    palette = np.empty((MAX_MASK_ID + 1, 4), dtype=np.uint8)
    for channel, shift in enumerate((16, 8, 0)):
        palette[:, channel] = (colors >> shift) & 255
    palette[:, 3] = 255
    palette[0] = 0
    return Image.fromarray(palette[mask])


class SamplePreviewService:
    """Generate previews in memory; raw assets and manifests remain unchanged."""

    def __init__(self, repository: SampleAssetManifestRepository) -> None:
        self.repository = repository

    def render(
        self, dataset_id: str, sample_id: str, sample_revision: int,
        preview_kind: PreviewKind | str,
    ) -> RenderedPreview:
        kind = PreviewKind(preview_kind)
        asset_kind = (
            AssetKind.ORIGINAL_IMAGE if kind is PreviewKind.THUMBNAIL else AssetKind.RAW_MASK
        )
        # The repository verifies size and SHA-256 for this exact revision.
        # Availability, integrity and byte-limit failures retain their storage
        # error types so HTTP callers can distinguish them from decoding errors.
        content = self.repository.read_asset_bytes(
            dataset_id, sample_id, sample_revision, asset_kind,
            max_bytes=MAX_PREVIEW_SOURCE_BYTES,
        )
        with _decode_png(content) as source:
            if kind is PreviewKind.THUMBNAIL:
                rendered = source.convert("RGBA")
            else:
                rendered = _mask_preview(source)
            with rendered:
                if kind is PreviewKind.THUMBNAIL:
                    rendered.thumbnail(THUMBNAIL_MAX_SIZE, Image.Resampling.LANCZOS)
                return _encode_png(rendered)
