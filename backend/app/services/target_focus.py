"""Target-focused evidence views (preprocessing ``target-focus-overlay-v1``, prompt v6).

For ONE annotation (the target) this renders what the model needs to judge it:

* the full overlay, with the target's selected geometry in thick magenta; other geometry is thin context
  (cyan polygons, yellow boxes), including unselected geometry of the same target;
* a crop window around the selected SUPPLIED geometry, cut from the original and from that same overlay with
  the same window and the same scale, so the two crops align pixel for pixel.

The window is a function of the target's own supplied geometry and the image size only. It never uses a
baseline, an answer key or the model's earlier answers. Its margin is wide (half the geometry's extent on each
side, at least ``MIN_MARGIN_PX``) so a displaced outline still has the person it should have enclosed inside
the crop, and the full image remains available for wider context. The crop is a window into the image: its
borders are not image borders, and ``FocusWindow.edges`` records which sides really coincide with one.

Pure and display-only: no I/O, original bytes and annotation geometry are never altered. The shared
``annotation_overlay`` (also used by correction proposals and v3 to v5) is not changed by this module.
"""

from dataclasses import dataclass
from io import BytesIO
import math
from typing import Any, Mapping, Sequence

from PIL import Image, ImageDraw

from .rule_applicability import object_key

MARGIN_FRACTION = 0.5     # of the target geometry's extent, on every side
MIN_MARGIN_PX = 96        # original pixels, on every side
MIN_WINDOW_PX = 192       # smallest crop side (the image permitting)
CROP_MAX_SIDE = 768       # the long side of the crop is scaled toward this (bounded image tokens)
MAX_CROP_SCALE = 2.0      # small targets are enlarged at most this much
TARGET_COLOR, TARGET_WIDTH = "#ff00ff", 3
POLYGON_CONTEXT_COLOR, BOX_CONTEXT_COLOR, CONTEXT_WIDTH = "#00ffff", "#ffdd00", 1
_COORD_LIMIT = 1_000_000.0  # drawing guard only; the annotation reader already rejects out-of-range coordinates


@dataclass(frozen=True)
class FocusWindow:
    """A crop window in ORIGINAL pixel coordinates plus the scale applied to it."""

    x0: int
    y0: int
    x1: int
    y1: int
    scale: float
    image_width: int
    image_height: int

    @property
    def size(self) -> tuple[int, int]:
        return max(1, round((self.x1 - self.x0) * self.scale)), max(1, round((self.y1 - self.y0) * self.scale))

    @property
    def edges(self) -> list[str]:
        """Crop sides that coincide with the image border; every other side is only the crop's own border."""
        return [name for name, hit in (("left", self.x0 == 0), ("top", self.y0 == 0),
                                       ("right", self.x1 == self.image_width), ("bottom", self.y1 == self.image_height))
                if hit]

    def to_original(self, crop_x: float, crop_y: float) -> tuple[float, float]:
        return self.x0 + crop_x / self.scale, self.y0 + crop_y / self.scale

    def to_crop(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.x0) * self.scale, (y - self.y0) * self.scale

    def document(self) -> dict[str, Any]:
        """What the model is told about the mapping (JSON-safe)."""
        return {"crop_window_original_px": [self.x0, self.y0, self.x1, self.y1], "crop_scale": self.scale,
                "crop_size": list(self.size), "crop_edges_touching_image_edge": self.edges,
                "mapping": "original_xy = crop_window_original_px[0:2] + crop_xy / crop_scale"}


@dataclass(frozen=True)
class FocusViews:
    window: FocusWindow
    overlay_full: bytes
    original_crop: bytes
    overlay_crop: bytes


def _clamp(value: float) -> float:
    return max(-_COORD_LIMIT, min(_COORD_LIMIT, float(value)))


def _supplied_box(obj: Mapping[str, Any], offset: int):
    box = obj.get("bbox")
    if box and obj.get("bbox_origin") != "derived_from_polygon" and box[0] <= box[2] and box[1] <= box[3]:
        return _clamp(box[0] - offset), _clamp(box[1] - offset), _clamp(box[2] - offset), _clamp(box[3] - offset)
    return None


def _target_points(obj: Mapping[str, Any], offset: int, geometry: Sequence[str] | None = None) -> list[tuple[float, float]]:
    """Selected supplied points; the legacy unspecified mode retains its derived-box fallback."""
    points = ([(_clamp(x), _clamp(y)) for polygon in obj.get("polygons", []) if len(polygon) >= 3 for x, y in polygon]
              if geometry is None or 'polygon' in geometry else [])
    box = _supplied_box(obj, offset) if geometry is None or 'box' in geometry else None
    if box:
        points += [(box[0], box[1]), (box[2], box[3])]
    if not points and geometry is None and obj.get("bbox"):
        left, top, right, bottom = obj["bbox"]
        points = [(_clamp(left - offset), _clamp(top - offset)), (_clamp(right - offset), _clamp(bottom - offset))]
    return points


def _validate_geometry(obj: Mapping[str, Any], geometry: Sequence[str] | None, annotation_format: str) -> None:
    """An explicit focus must name supplied geometry; never fall back to a derived box."""
    if geometry is None:
        return
    if not geometry or len(set(geometry)) != len(geometry) or any(kind not in {'box', 'polygon'} for kind in geometry):
        raise ValueError('Focus geometry must contain distinct box or polygon entries.')
    if 'box' in geometry and (not _supplied_box(obj, 1 if annotation_format == 'penn_fudan' else 0)
                             or (annotation_format != 'penn_fudan' and obj.get('bbox_origin') != 'supplied')):
        raise ValueError('The requested focus box was not supplied.')
    if 'polygon' in geometry and not any(len(polygon) >= 3 for polygon in obj.get('polygons', [])):
        raise ValueError('The requested focus polygon was not supplied.')


def _at_least(low: int, high: int, limit: int, minimum: int) -> tuple[int, int]:
    """Widen [low, high] around its centre to ``minimum`` inside [0, limit]; the original span stays covered."""
    if limit < minimum or high - low >= minimum:
        return low, high
    low = max(0, low - (minimum - (high - low)) // 2)
    high = low + minimum
    if high > limit:
        low, high = limit - minimum, limit
    return low, high


def focus_window(obj: Mapping[str, Any], image_size: tuple[int, int], annotation_format: str,
                 *, geometry: Sequence[str] | None = None) -> FocusWindow:
    """Crop window around one target's supplied geometry, clipped to the image, with its scale."""
    width, height = image_size
    _validate_geometry(obj, geometry, annotation_format)
    points = _target_points(obj, 1 if annotation_format == "penn_fudan" else 0, geometry)
    if not points:
        x0, y0, x1, y1 = 0, 0, width, height
    else:
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        margin_x = max(MARGIN_FRACTION * (max(xs) - min(xs)), MIN_MARGIN_PX)
        margin_y = max(MARGIN_FRACTION * (max(ys) - min(ys)), MIN_MARGIN_PX)
        x0, x1 = max(0, math.floor(min(xs) - margin_x)), min(width, math.ceil(max(xs) + margin_x))
        y0, y1 = max(0, math.floor(min(ys) - margin_y)), min(height, math.ceil(max(ys) + margin_y))
    # A target far outside the image leaves an empty window; fall back to the whole image, never to nothing.
    if x1 <= x0 or y1 <= y0:
        x0, y0, x1, y1 = 0, 0, width, height
    x0, x1 = _at_least(x0, x1, width, MIN_WINDOW_PX)
    y0, y1 = _at_least(y0, y1, height, MIN_WINDOW_PX)
    long_side = max(x1 - x0, y1 - y0)
    scale = round(min(MAX_CROP_SCALE, CROP_MAX_SIDE / long_side), 4)
    return FocusWindow(x0, y0, x1, y1, scale, width, height)


def _draw(canvas: Image.Image, obj: Mapping[str, Any], offset: int, *, target: bool,
          geometry: Sequence[str] | None = None) -> None:
    draw = ImageDraw.Draw(canvas)
    width = TARGET_WIDTH if target else CONTEXT_WIDTH
    box = _supplied_box(obj, offset)
    if box and (geometry is None or 'box' in geometry):
        draw.rectangle(box, outline=TARGET_COLOR if target else BOX_CONTEXT_COLOR, width=width)
    polygons = obj.get("polygons", []) if geometry is None or 'polygon' in geometry else []
    for polygon in polygons:
        if len(polygon) >= 3:
            points = [(_clamp(x), _clamp(y)) for x, y in polygon]
            draw.line(points + [points[0]], fill=TARGET_COLOR if target else POLYGON_CONTEXT_COLOR, width=width)


def _png(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def render_focus(image: bytes, objects: Sequence[Mapping[str, Any]], target_key: str,
                 annotation_format: str, *, geometry: Sequence[str] | None = None) -> FocusViews:
    """Highlight the selected target geometry; other supplied geometry remains thin context.

    The crop uses only selected geometry. None retains the original all-geometry renderer for callers
    without a rule scope. Source objects, coordinates and image bytes are never edited.
    """
    target = next((obj for obj in objects if object_key(obj) == target_key), None)
    if target is None:
        raise ValueError("The target annotation is not among the sample's annotations.")
    _validate_geometry(target, geometry, annotation_format)
    offset = 1 if annotation_format == "penn_fudan" else 0
    with Image.open(BytesIO(image)) as source:
        original = source.convert("RGB")
    overlay = original.copy()
    for obj in objects:                  # context first, so the target is drawn on top of it
        if object_key(obj) != target_key:
            _draw(overlay, obj, offset, target=False)
    if geometry is not None:
        _draw(overlay, target, offset, target=False)
    _draw(overlay, target, offset, target=True, geometry=geometry)
    window = focus_window(target, original.size, annotation_format, geometry=geometry)
    crop_box = (window.x0, window.y0, window.x1, window.y1)
    size = window.size
    return FocusViews(window=window, overlay_full=_png(overlay),
                      original_crop=_png(original.crop(crop_box).resize(size, Image.LANCZOS)),
                      overlay_crop=_png(overlay.crop(crop_box).resize(size, Image.LANCZOS)))
