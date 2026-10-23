"""Bounded, pure rendering of one annotation box with marked surrounding context.

No network, database, model or filesystem access. Source pixels inside the target are
preserved exactly; nothing is resized, repaired, clipped, dropped or reordered.

Coordinates: an annotation (xmin, ymin, xmax, ymax) is one-based and inclusive; the PIL
box is (xmin-1, ymin-1, xmax, ymax). Each side gets half the target dimension, rounded up,
with a minimum of 32 pixels, clipped only at the source boundary. A neutral 2 px margin and
a red 2 px frame drawn entirely outside the target are added.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from math import ceil
from typing import Any

from PIL import Image, ImageDraw, ImageOps

from ..models.enclosure import MAX_BOXES, VIEW_VERSION

BORDER = 2
MIN_CONTEXT = 32
MARGIN_FILL = (128, 128, 128)
FRAME_COLOR = (255, 0, 0)
Box = tuple[int, int, int, int]


class EnclosureViewError(ValueError):
    """The evidence cannot be represented faithfully. The code is safe to store and show."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ViewLimits:
    max_image_bytes: int = 8 * 1024**2
    max_pixels: int = 16_000_000


@dataclass(frozen=True)
class TargetBox:
    annotation_index: int  # zero-based position in the source annotation list
    object_id: int  # may repeat; never used as a key
    label: str
    bbox: Box  # one-based inclusive, exactly as annotated
    pil_box: Box  # zero-based half-open

    @property
    def box_number(self) -> int:
        return self.annotation_index + 1


@dataclass(frozen=True)
class ContextView:
    target: TargetBox
    png: bytes
    png_sha256: str
    size: tuple[int, int]
    source_window: Box  # zero-based half-open window cut from the source
    target_window: Box  # zero-based half-open target inside the attached view, incl. margin


def open_source(image_bytes: bytes, limits: ViewLimits) -> Image.Image:
    if len(image_bytes) > limits.max_image_bytes:
        raise EnclosureViewError("source_byte_limit")
    try:
        with Image.open(BytesIO(image_bytes)) as opened:
            if opened.width * opened.height > limits.max_pixels:
                raise EnclosureViewError("source_pixel_limit")
            return opened.convert("RGB")
    except EnclosureViewError:
        raise  # a limit error is already precise; it must not be relabeled as unreadable
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise EnclosureViewError("source_image_unreadable") from error


def checked_target(index: int, obj: dict[str, Any], size: tuple[int, int]) -> TargetBox:
    """Validate one annotation exactly as given. Never repair it."""
    bbox = obj.get("bbox")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4 or any(type(v) is not int for v in bbox):
        raise EnclosureViewError("invalid_box_coordinates")
    xmin, ymin, xmax, ymax = bbox
    if not (1 <= xmin <= xmax <= size[0] and 1 <= ymin <= ymax <= size[1]):
        raise EnclosureViewError("box_outside_source_or_inverted")
    object_id, label = obj.get("object_id"), obj.get("label")
    if type(object_id) is not int or not isinstance(label, str) or not label.strip():
        raise EnclosureViewError("invalid_object_fields")
    return TargetBox(index, object_id, label, (xmin, ymin, xmax, ymax), (xmin - 1, ymin - 1, xmax, ymax))


def context_window(target: TargetBox, size: tuple[int, int]) -> Box:
    x0, y0, x1, y1 = target.pil_box
    dx, dy = max(MIN_CONTEXT, ceil((x1 - x0) / 2)), max(MIN_CONTEXT, ceil((y1 - y0) / 2))
    return (max(0, x0 - dx), max(0, y0 - dy), min(size[0], x1 + dx), min(size[1], y1 + dy))


def encode(image: Image.Image, limits: ViewLimits) -> bytes:
    if image.width * image.height > limits.max_pixels:
        raise EnclosureViewError("rendered_pixel_limit")
    buffer = BytesIO()
    image.save(buffer, format="PNG", compress_level=6, optimize=False)
    data = buffer.getvalue()
    if len(data) > limits.max_image_bytes:
        raise EnclosureViewError("rendered_byte_limit")
    return data


def render_context(source: Image.Image, target: TargetBox, limits: ViewLimits) -> ContextView:
    window = context_window(target, source.size)
    view = ImageOps.expand(source.crop(window), border=BORDER, fill=MARGIN_FILL)
    x0, y0, x1, y1 = target.pil_box
    placed = (x0 - window[0] + BORDER, y0 - window[1] + BORDER, x1 - window[0] + BORDER, y1 - window[1] + BORDER)
    left, top, right, bottom = placed
    ImageDraw.Draw(view).rectangle((left - BORDER, top - BORDER, right + BORDER - 1, bottom + BORDER - 1),
                                   outline=FRAME_COLOR, width=BORDER)
    data = encode(view, limits)
    return ContextView(target, data, sha256(data).hexdigest(), (view.width, view.height), window, placed)


def render_views(image_bytes: bytes, objects: Sequence[dict[str, Any]], origin: tuple[int, int],
                 limits: ViewLimits, *, max_boxes: int = MAX_BOXES) -> tuple[ContextView, ...]:
    """Render every annotation in source order, or raise. A sample is never silently truncated.

    Zero annotations are an explicit error (the caller records it as unavailable), never an
    all-pass. The whole annotation list is validated before any view is produced.
    """
    if origin != (1, 1):
        raise EnclosureViewError("unsupported_coordinate_origin")
    if not objects:
        raise EnclosureViewError("no_annotation_boxes")
    if len(objects) > min(max_boxes, MAX_BOXES):
        raise EnclosureViewError("too_many_annotation_boxes")
    source = open_source(image_bytes, limits)
    targets = [checked_target(index, obj, source.size) for index, obj in enumerate(objects)]
    return tuple(render_context(source, target, limits) for target in targets)


__all__ = ["BORDER", "ContextView", "EnclosureViewError", "TargetBox", "ViewLimits", "VIEW_VERSION", "render_views"]
