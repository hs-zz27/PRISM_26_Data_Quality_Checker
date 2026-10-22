"""Pure numbered-box and crop-sheet references for development experiments.

Original source bytes and coordinates are never changed. This module performs
no network, database, model-loading or filesystem operations.
"""

from dataclasses import dataclass
from io import BytesIO
from math import ceil, sqrt

from PIL import Image, ImageDraw

MAX_BOXES = 32
VARIANTS = {"overview", "crops"}


@dataclass(frozen=True)
class ViewLimits:
    max_image_bytes: int = 8 * 1024**2
    max_pixels: int = 16_000_000


class RenderError(ValueError):
    """The complete source cannot be rendered faithfully within this experiment."""


@dataclass(frozen=True)
class RenderedView:
    data: bytes
    width: int
    height: int
    source_boxes: tuple[tuple[int, int, int, int], ...]


def encode(image, boxes, limits):
    if image.width * image.height > limits.max_pixels:
        raise RenderError("rendered_pixel_limit")
    buffer = BytesIO()
    image.save(buffer, format="PNG", compress_level=6, optimize=False)
    data = buffer.getvalue()
    if len(data) > limits.max_image_bytes:
        raise RenderError("rendered_byte_limit")
    return RenderedView(data, image.width, image.height, tuple(boxes))


def render_view(image_bytes, objects, origin, limits, variant):
    """Render every source box. Never repair, clip, merge or omit a box.

    Penn-Fudan endpoints are one-based inclusive. PIL crops use zero-based
    half-open edges: (xmin - 1, ymin - 1, xmax, ymax).
    """
    if variant not in VARIANTS:
        raise ValueError("Unknown visual variant.")
    if origin != (1, 1) or not 1 <= len(objects) <= MAX_BOXES:
        raise RenderError("unsupported_origin_or_box_count")
    if len(image_bytes) > limits.max_image_bytes:
        raise RenderError("source_byte_limit")
    with Image.open(BytesIO(image_bytes)) as opened:
        if opened.width * opened.height > limits.max_pixels:
            raise RenderError("source_pixel_limit")
        source = opened.convert("RGB")
    boxes = []
    for obj in objects:
        bbox = obj.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4 or any(type(v) is not int for v in bbox):
            raise RenderError("invalid_box_coordinates")
        xmin, ymin, xmax, ymax = bbox
        if not (1 <= xmin <= xmax <= source.width and 1 <= ymin <= ymax <= source.height):
            raise RenderError("box_outside_source_or_inverted")
        boxes.append(tuple(bbox))
    if variant == "overview":
        overview = source.copy()
        draw = ImageDraw.Draw(overview)
        for index, (xmin, ymin, xmax, ymax) in enumerate(boxes):
            draw.rectangle((xmin - 1, ymin - 1, xmax - 1, ymax - 1), outline="red", width=3)
            draw.text((xmin, ymin), str(index + 1), fill="white", stroke_width=1, stroke_fill="black")
        return encode(overview, boxes, limits)
    columns = min(4, ceil(sqrt(len(boxes))))
    sheet = Image.new("RGB", (columns * 256, ceil(len(boxes) / columns) * 288), "white")
    captions = ImageDraw.Draw(sheet)
    for index, (xmin, ymin, xmax, ymax) in enumerate(boxes):
        # Crop from source, never from the annotated overview: labels and red
        # borders must not obscure the evidence in a crop.
        crop = source.crop((xmin - 1, ymin - 1, xmax, ymax))
        crop.thumbnail((240, 248), Image.Resampling.LANCZOS)
        left, top = (index % columns) * 256, (index // columns) * 288
        captions.text((left + 8, top + 8), f"Box {index + 1}", fill="black")
        sheet.paste(crop, (left + (256 - crop.width) // 2, top + 32 + (248 - crop.height) // 2))
    return encode(sheet, boxes, limits)
