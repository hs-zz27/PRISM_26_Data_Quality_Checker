"""Format-independent reader output, not the existing catalogue/API contract.

Coordinates are unrounded source image coordinates with a zero-based origin.
Boxes use (left, top, right, bottom); raster inclusion is NOT implied. Original
records remain available even when normalized geometry cannot be constructed.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

Number = int | float
Point = tuple[Number, Number]
Box = tuple[Number, Number, Number, Number]


@dataclass
class ReaderIssue:
    code: str
    location: str  # JSON pointer within this document, not a source object ID
    message: str
    severity: Literal["error", "unsupported"] = "error"


@dataclass
class MaskReference:
    """Supplied mask evidence. Readers do not decode or generate mask pixels."""

    encoding: Literal["coco_rle", "labelme_png", "file"]
    value: Any
    decoded: bool = False


@dataclass
class AnnotationRecord:
    key: str  # Document-local JSON pointer; stable even for duplicate source IDs
    source: Any
    source_id: int | str | None = None
    label: str | None = None
    category_id: int | None = None
    group_id: int | None = None
    bbox: Box | None = None
    bbox_origin: Literal["supplied", "derived_from_polygon"] | None = None
    polygons: list[list[Point]] = field(default_factory=list)
    polygon_locations: list[str] = field(default_factory=list)
    mask: MaskReference | None = None
    issues: list[ReaderIssue] = field(default_factory=list)


@dataclass
class ImageRecord:
    key: str
    source: Any
    source_id: int | str | None = None
    file_name: str | None = None  # Reference only; never resolved/opened by readers
    width: int | None = None
    height: int | None = None
    annotations: list[AnnotationRecord] = field(default_factory=list)
    issues: list[ReaderIssue] = field(default_factory=list)


@dataclass
class AnnotationDocument:
    format: Literal["coco", "labelme"]
    source: dict[str, Any]
    images: list[ImageRecord] = field(default_factory=list)
    unassigned_annotations: list[AnnotationRecord] = field(default_factory=list)
    issues: list[ReaderIssue] = field(default_factory=list)
    source_path: str | None = None
    source_bytes: bytes | None = None  # Exact bytes when loaded from a file

    @property
    def all_issues(self) -> list[ReaderIssue]:
        return [
            *self.issues,
            *(issue for image in self.images for issue in image.issues),
            *(issue for image in self.images for obj in image.annotations for issue in obj.issues),
            *(issue for obj in self.unassigned_annotations for issue in obj.issues),
        ]
