"""Inspect local image evidence and annotation geometry without modifying either.

For local, non-concurrently-mutated datasets. Not a replacement for the managed
ZIP import/snapshot boundary. No AI, database, downloads or automatic corrections.
"""

from fractions import Fraction
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path, PureWindowsPath
import stat
import warnings

from PIL import Image

from ..models.annotation_document import AnnotationDocument, AnnotationRecord, ReaderIssue
from ..models.annotation_validation import DocumentInspection, ImageInspection, ValidationLimits


class ImageInputError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _image_path(reference: str, root: Path, base: Path) -> Path:
    # Permit ../ only where it remains within the explicitly selected dataset root.
    # Do not interpret platform-dependent separators, drives or URLs as local paths.
    if ("\x00" in reference or "\\" in reference or ":" in reference
            or Path(reference).is_absolute() or PureWindowsPath(reference).drive):
        raise ImageInputError("unsafe_image_reference", "Image reference must be a relative local path.")
    try:
        candidate = (base / reference).resolve()
    except (OSError, RuntimeError, ValueError) as error:
        raise ImageInputError("unsafe_image_reference", "Image reference cannot be resolved safely.") from error
    if not candidate.is_relative_to(root):
        raise ImageInputError("unsafe_image_reference", "Image reference escapes the selected dataset root.")
    return candidate


def _image_bytes(path: Path, limits: ValidationLimits) -> bytes:
    try:
        # Nonblocking prevents accidental FIFO reads; no-follow rejects a replaced
        # final symlink. Parent-directory mutation is outside this local tool's scope.
        # O_NONBLOCK/O_NOFOLLOW do not exist on Windows; O_BINARY exists only there.
        # resolve() already rejected links that leave the root, so the flags are a
        # race guard where available, not the only containment check.
        flags = os.O_RDONLY | sum(getattr(os, name, 0) for name in ("O_NONBLOCK", "O_NOFOLLOW", "O_BINARY"))
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ImageInputError("invalid_image_file", "Image reference is not a regular file.")
            if metadata.st_size > limits.max_image_bytes:
                raise ImageInputError("image_byte_limit", "Image exceeds the configured byte limit.")
            raw = handle.read(limits.max_image_bytes + 1)
        if len(raw) > limits.max_image_bytes:
            raise ImageInputError("image_byte_limit", "Image exceeds the configured byte limit.")
        return raw
    except FileNotFoundError as error:
        raise ImageInputError("missing_image", "Referenced image does not exist.") from error
    except OSError as error:
        raise ImageInputError("unreadable_image", "Referenced image cannot be read.") from error


def _decode_metadata(raw: bytes, limits: ValidationLimits):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw), formats=("PNG", "JPEG")) as image:
                if image.width * image.height > limits.max_image_pixels:
                    raise ImageInputError("image_pixel_limit", "Image exceeds the configured pixel limit.")
                if getattr(image, "n_frames", 1) != 1:
                    raise ImageInputError("unsupported_multiframe_image", "Only single-frame images are supported.")
                image.verify()
            # verify() checks structure; loading fresh bytes also checks decoding.
            with Image.open(BytesIO(raw), formats=("PNG", "JPEG")) as image:
                image.load()
                return image.width, image.height, image.format, image.getexif().get(274, 1)
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        raise ImageInputError("image_pixel_limit", "Image exceeds decoder safety limits.") from error
    except (OSError, SyntaxError, ValueError) as error:
        if isinstance(error, ImageInputError):
            raise
        raise ImageInputError("invalid_image", "Image is corrupt or is not supported PNG/JPEG data.") from error


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a, b, point):
    return (min(a[0], b[0]) <= point[0] <= max(a[0], b[0])
            and min(a[1], b[1]) <= point[1] <= max(a[1], b[1]))


def _intersects(a, b, c, d):
    if (max(a[0], b[0]) < min(c[0], d[0]) or max(c[0], d[0]) < min(a[0], b[0])
            or max(a[1], b[1]) < min(c[1], d[1]) or max(c[1], d[1]) < min(a[1], b[1])):
        return False
    ab_c, ab_d, cd_a, cd_b = _cross(a, b, c), _cross(a, b, d), _cross(c, d, a), _cross(c, d, b)
    if ((ab_c == 0 and _on_segment(a, b, c)) or (ab_d == 0 and _on_segment(a, b, d))
            or (cd_a == 0 and _on_segment(c, d, a)) or (cd_b == 0 and _on_segment(c, d, b))):
        return True
    return (ab_c > 0) != (ab_d > 0) and (cd_a > 0) != (cd_b > 0)


def _polygon_issues(points, location, limits):
    # A repeated final endpoint is a conventional explicit closure, not a repair.
    vertices = points[:-1] if len(points) > 1 and points[0] == points[-1] else points
    if len(vertices) > limits.max_polygon_vertices:
        return [ReaderIssue("polygon_topology_limit", location,
                            "Topology checks skipped above the configured vertex limit.", "unsupported")]
    if len(set(vertices)) < 3:
        return []  # Reader already reports this structural error.
    findings = []
    if len(set(vertices)) < len(vertices):
        findings.append(ReaderIssue("repeated_polygon_vertex", location,
                                    "Polygon repeats a vertex other than its closing endpoint."))
    # Exact arithmetic on represented coordinates avoids arbitrary tolerances and
    # overflow. This is structural geometry, not a semantic fit/coverage judgment.
    exact = [(Fraction(x), Fraction(y)) for x, y in vertices]
    edges = list(zip(exact, exact[1:] + exact[:1]))
    twice_area = sum(a[0] * b[1] - b[0] * a[1] for a, b in edges)
    if twice_area == 0:
        findings.append(ReaderIssue("zero_polygon_area", location, "Polygon has zero signed area."))
    for i, (a, b) in enumerate(edges):
        for j in range(i + 1, len(edges)):
            if j == i + 1 or (i == 0 and j == len(edges) - 1):
                continue
            if _intersects(a, b, *edges[j]):
                findings.append(ReaderIssue("self_intersecting_polygon", location,
                                            "Non-adjacent polygon edges intersect or touch."))
                return findings
    return findings


def _geometry_issues(obj: AnnotationRecord, size, limits):
    findings = []
    if size is not None:
        width, height = size
        # Bounds are continuous image extents, not a chosen box-fit tolerance.
        if obj.bbox is not None and obj.bbox_origin == "supplied":
            left, top, right, bottom = obj.bbox
            if any(x < 0 or x > width for x in (left, right)) or any(y < 0 or y > height for y in (top, bottom)):
                findings.append(ReaderIssue("box_outside_image", obj.key,
                                            "Supplied box extends outside the decoded image extent."))
    for index, points in enumerate(obj.polygons):
        location = obj.polygon_locations[index] if index < len(obj.polygon_locations) else obj.key
        if size is not None and any(x < 0 or y < 0 or x > size[0] or y > size[1] for x, y in points):
            findings.append(ReaderIssue("polygon_outside_image", location,
                                        "Polygon vertices extend outside the decoded image extent."))
        findings.extend(_polygon_issues(points, location, limits))
    return findings


def resolve_reference_base(document: AnnotationDocument, image_root: str | Path,
                           reference_base: str | Path | None = None) -> tuple[Path, Path]:
    """Return (resolved root, directory that relative image references start from)."""
    root = Path(image_root).resolve()
    if not root.is_dir():
        raise ValueError("image_root must be an existing directory.")
    if reference_base is not None:
        return root, Path(reference_base).resolve()
    if document.format == "labelme" and document.source_path is not None:
        return root, Path(document.source_path).resolve().parent
    return root, root


def inspect_document(document: AnnotationDocument, image_root: str | Path, *,
                     reference_base: str | Path | None = None,
                     limits: ValidationLimits | None = None) -> DocumentInspection:
    """Inspect files under an explicit root. Never mutate parsed data or originals.

    COCO file_name is relative to image_root. LabelMe imagePath is relative to its
    JSON file's directory (or image_root for in-memory documents). reference_base
    explicitly overrides that choice, but all resolved images must stay in root.
    """
    limits = limits or ValidationLimits()
    root, base = resolve_reference_base(document, image_root, reference_base)
    result = DocumentInspection(document.format, document.source_path,
                                len(document.unassigned_annotations), list(document.all_issues))
    for record in document.images:
        inspection = ImageInspection(record.key, record.file_name, len(record.annotations))
        result.images.append(inspection)
        size = None
        if record.file_name is None:
            inspection.issues.append(ReaderIssue("image_unavailable", record.key,
                                                 "No supported external image reference to inspect."))
        elif (document.format == "labelme" and document.source.get("imageData") is not None):
            # LabelMe can use embedded pixels instead of imagePath. Do not silently
            # inspect a potentially different external image while ignoring them.
            inspection.issues.append(ReaderIssue("embedded_image_unavailable", "/imageData",
                                                 "Embedded image must be decoded before image validation.", "unsupported"))
        else:
            try:
                path = _image_path(record.file_name, root, base)
                raw = _image_bytes(path, limits)
                width, height, image_format, orientation = _decode_metadata(raw, limits)
                inspection.availability = "available"
                inspection.actual_width, inspection.actual_height = width, height
                inspection.image_format, inspection.sha256 = image_format, sha256(raw).hexdigest()
                if orientation != 1:
                    inspection.issues.append(ReaderIssue("unsupported_image_orientation", record.key,
                        "Image has EXIF orientation; dimension and bounds checks need an explicit coordinate mapping.", "unsupported"))
                else:
                    size = (width, height)
                    if ((record.width is not None and record.width != width)
                            or (record.height is not None and record.height != height)):
                        inspection.issues.append(ReaderIssue("image_dimension_mismatch", record.key,
                            f"Declared dimensions differ from decoded dimensions {width} x {height}."))
            except ImageInputError as error:
                inspection.issues.append(ReaderIssue(error.code, record.key, str(error),
                    "unsupported" if error.code.startswith("unsupported_") else "error"))
        for obj in record.annotations:
            result.geometry_issues.extend(_geometry_issues(obj, size, limits))
    for obj in document.unassigned_annotations:
        result.geometry_issues.extend(_geometry_issues(obj, None, limits))
    return result
