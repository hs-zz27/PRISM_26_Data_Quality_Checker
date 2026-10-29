"""Shared JSON/geometry helpers for standalone annotation readers."""

from copy import deepcopy
import json
from math import isfinite
from pathlib import Path
from typing import Any, Callable

from ..models.annotation_document import AnnotationDocument, AnnotationRecord, ReaderIssue

DEFAULT_MAX_JSON_BYTES = 64 * 1024**2


class AnnotationReadError(ValueError):
    """The file or document envelope cannot be read without ambiguity."""


def document_copy(data: Any, required_lists: tuple[str, ...]) -> dict:
    if not isinstance(data, dict) or any(not isinstance(data.get(key), list) for key in required_lists):
        raise AnnotationReadError(f"Expected a JSON object with lists: {', '.join(required_lists)}.")
    # Both entry points accept JSON data only. Reject non-finite values, including
    # Python json.loads' permissive NaN/Infinity extensions, before normalization.
    try:
        json.dumps(data, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as error:
        raise AnnotationReadError("Document must contain finite JSON values.") from error
    return deepcopy(data)


def load_document(path: str | Path, parser: Callable[[dict], AnnotationDocument], *,
                  max_bytes: int = DEFAULT_MAX_JSON_BYTES) -> AnnotationDocument:
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer.")

    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise AnnotationReadError("Annotation JSON exceeds max_bytes.")
        result = parse_document_bytes(raw, parser)
    except AnnotationReadError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise AnnotationReadError("Cannot read annotation JSON.") from error
    result.source_path = str(path)
    return result


def parse_document_bytes(raw: bytes, parser: Callable[[dict], AnnotationDocument]) -> AnnotationDocument:
    """Parse already bounded captured bytes with the same strict JSON rules."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise AnnotationReadError("Duplicate JSON keys are ambiguous.")
            result[key] = value
        return result

    def invalid_constant(value):
        raise AnnotationReadError("Non-finite JSON numbers are not supported.")

    try:
        data = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs,
                          parse_constant=invalid_constant)
        result = parser(data)
    except AnnotationReadError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise AnnotationReadError("Cannot read annotation JSON.") from error
    result.source_bytes = raw
    return result


def issue(target, code, location, message, *, unsupported=False):
    target.issues.append(ReaderIssue(code, location, message,
                                    "unsupported" if unsupported else "error"))


def number(value):
    if type(value) not in (int, float):
        return False
    try:
        return isfinite(value)
    except OverflowError:
        return False


def dimensions(image, width, height):
    for field, value in (("width", width), ("height", height)):
        if type(value) is int and value > 0:
            setattr(image, field, value)
        else:
            issue(image, "invalid_dimensions", image.key, f"Declared {field} must be a positive integer.")


def polygon(obj: AnnotationRecord, points: Any, location: str):
    if not isinstance(points, list) or not all(
        isinstance(point, (list, tuple)) and len(point) == 2 and all(number(v) for v in point)
        for point in points
    ):
        issue(obj, "invalid_polygon", location, "Expected a list of finite coordinate pairs.")
        return
    # Keep degenerate geometry for later review; do not repair, clip or discard it.
    parsed = [tuple(point) for point in points]
    obj.polygons.append(parsed)
    obj.polygon_locations.append(location)
    if len(set(parsed)) < 3:
        issue(obj, "degenerate_polygon", location, "Polygon has fewer than three distinct vertices.")


def polygon_bounds(obj: AnnotationRecord):
    points = [point for part in obj.polygons for point in part]
    if points:
        obj.bbox = (min(p[0] for p in points), min(p[1] for p in points),
                    max(p[0] for p in points), max(p[1] for p in points))
        obj.bbox_origin = "derived_from_polygon"


def check_box(obj: AnnotationRecord):
    if obj.bbox is not None:
        left, top, right, bottom = obj.bbox
        if right <= left or bottom <= top:
            issue(obj, "degenerate_box", obj.key, "Box has a non-positive width or height.")
