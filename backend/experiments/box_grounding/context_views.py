"""Context views for a diagnostic, preserving every pixel inside the target box."""

from dataclasses import dataclass
from hashlib import sha256
from math import ceil

from PIL import ImageDraw, ImageOps

from .crops import Box, BoxCrop, checked_box, open_source
from .scope import ScopeCase
from .views import ViewLimits, encode

CONTEXT_VARIANT = "box-context-v1"
BORDER = 2


@dataclass(frozen=True)
class ContextView:
    png: bytes
    png_sha256: str
    size: tuple[int, int]
    source_window: Box  # zero-based half-open; only padding clips at image edges
    target_window: Box  # zero-based half-open in the attached view, including margin


def render_context(case: ScopeCase, crop: BoxCrop, limits: ViewLimits) -> ContextView:
    """Add half a box dimension (minimum 32px) per side, without resizing.

    The 2px frame is outside the target, so even one-pixel targets and targets
    touching source edges retain all their pixels. A neutral outer margin makes
    room for the frame at clipped edges. Original annotations are never changed.
    """
    source = open_source(case.image_bytes, case.objects, (1, 1), limits)
    bbox, object_id, label = checked_box(case.objects[crop.annotation_index], source.size)
    if (bbox, object_id, label) != (crop.bbox, crop.object_id, crop.label):
        raise ValueError("Target does not match its source annotation.")
    x0, y0, x1, y1 = crop.pil_box
    if crop.pil_box != (bbox[0] - 1, bbox[1] - 1, bbox[2], bbox[3]):
        raise ValueError("Target crop coordinates do not match the annotation.")
    dx, dy = max(32, ceil((x1 - x0) / 2)), max(32, ceil((y1 - y0) / 2))
    window = (max(0, x0 - dx), max(0, y0 - dy),
              min(source.width, x1 + dx), min(source.height, y1 + dy))
    view = ImageOps.expand(source.crop(window), border=BORDER, fill=(128, 128, 128))
    target = (x0 - window[0] + BORDER, y0 - window[1] + BORDER,
              x1 - window[0] + BORDER, y1 - window[1] + BORDER)
    left, top, right, bottom = target
    ImageDraw.Draw(view).rectangle(
        (left - BORDER, top - BORDER, right + BORDER - 1, bottom + BORDER - 1),
        outline=(255, 0, 0), width=BORDER,
    )
    rendered = encode(view, [bbox], limits)
    return ContextView(rendered.data, sha256(rendered.data).hexdigest(),
                       (rendered.width, rendered.height), window, target)
