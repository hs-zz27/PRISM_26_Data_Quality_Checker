"""Per-box crops of the untouched source image, with exact coordinate provenance.

Source boxes are one-based and inclusive; PIL crops are zero-based and half-open, so
(xmin, ymin, xmax, ymax) becomes (xmin - 1, ymin - 1, xmax, ymax). Pixels are cropped
from the original image only, never from a rendered overview, so no red border or label
can obscure the evidence. A crop is never padded, resized, clipped or merged.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from typing import Any

from PIL import Image

from .views import MAX_BOXES, RenderError, ViewLimits, encode

CROP_PREPROCESSING = "box-crop-png-v1"
Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class BoxCrop:
    annotation_index: int  # zero-based position in the source annotation list
    object_id: int  # the source object ID, which may repeat
    label: str
    bbox: Box  # one-based inclusive source coordinates, exactly as annotated
    pil_box: Box  # the zero-based half-open box handed to PIL
    width: int
    height: int
    png: bytes
    png_sha256: str

    @property
    def box_number(self) -> int:
        """One-based number shown to the model and in reports."""
        return self.annotation_index + 1


def checked_box(obj: dict[str, Any], size: tuple[int, int]) -> tuple[Box, int, str]:
    """Validate one source object. Never repair, clip or omit it."""
    bbox = obj.get("bbox")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4 or any(type(v) is not int for v in bbox):
        raise RenderError("invalid_box_coordinates")
    xmin, ymin, xmax, ymax = bbox
    if not (1 <= xmin <= xmax <= size[0] and 1 <= ymin <= ymax <= size[1]):
        raise RenderError("box_outside_source_or_inverted")
    object_id, label = obj.get("object_id"), obj.get("label")
    if type(object_id) is not int or not isinstance(label, str) or not label.strip():
        raise RenderError("invalid_object_fields")
    return (xmin, ymin, xmax, ymax), object_id, label


def open_source(image_bytes: bytes, objects: Sequence[dict[str, Any]], origin: tuple[int, int],
                limits: ViewLimits) -> Image.Image:
    """The scope gate both arms share: unrepresentable evidence is rejected, never truncated."""
    if origin != (1, 1) or not 1 <= len(objects) <= MAX_BOXES:
        raise RenderError("unsupported_origin_or_box_count")
    if len(image_bytes) > limits.max_image_bytes:
        raise RenderError("source_byte_limit")
    with Image.open(BytesIO(image_bytes)) as opened:
        if opened.width * opened.height > limits.max_pixels:
            raise RenderError("source_pixel_limit")
        return opened.convert("RGB")


def validate_scope(image_bytes: bytes, objects: Sequence[dict[str, Any]], origin: tuple[int, int],
                   limits: ViewLimits) -> None:
    """Raise RenderError unless every box is representable. Used by the raw control too."""
    source = open_source(image_bytes, objects, origin, limits)
    for obj in objects:
        checked_box(obj, source.size)


def render_box_crops(image_bytes: bytes, objects: Sequence[dict[str, Any]], origin: tuple[int, int],
                     limits: ViewLimits) -> tuple[BoxCrop, ...]:
    """Crop every annotated box. Duplicate object IDs keep distinct annotation indices."""
    source = open_source(image_bytes, objects, origin, limits)
    validated = [checked_box(obj, source.size) for obj in objects]
    crops = []
    for index, (bbox, object_id, label) in enumerate(validated):
        xmin, ymin, xmax, ymax = bbox
        pil_box = (xmin - 1, ymin - 1, xmax, ymax)
        rendered = encode(source.crop(pil_box), [bbox], limits)
        crops.append(BoxCrop(
            annotation_index=index, object_id=object_id, label=label, bbox=bbox, pil_box=pil_box,
            width=rendered.width, height=rendered.height, png=rendered.data,
            png_sha256=sha256(rendered.data).hexdigest(),
        ))
    return tuple(crops)
