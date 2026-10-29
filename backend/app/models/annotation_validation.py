"""Read-only inspection results, separate from model findings and human decisions."""

from dataclasses import dataclass, field
from typing import Literal

from .annotation_document import ReaderIssue


@dataclass(frozen=True)
class ValidationLimits:
    max_image_bytes: int = 64 * 1024**2
    max_image_pixels: int = 16_000_000
    max_polygon_vertices: int = 512

    def __post_init__(self):
        for value in (self.max_image_bytes, self.max_image_pixels, self.max_polygon_vertices):
            if type(value) is not int or value <= 0:
                raise ValueError("Validation limits must be positive integers.")


@dataclass
class ImageInspection:
    image_key: str
    file_name: str | None
    annotation_count: int
    availability: Literal["available", "unavailable"] = "unavailable"
    actual_width: int | None = None
    actual_height: int | None = None
    image_format: str | None = None
    sha256: str | None = None
    issues: list[ReaderIssue] = field(default_factory=list)


@dataclass
class DocumentInspection:
    format: str
    source_path: str | None
    unassigned_annotation_count: int
    reader_issues: list[ReaderIssue]
    images: list[ImageInspection] = field(default_factory=list)
    geometry_issues: list[ReaderIssue] = field(default_factory=list)

    @property
    def all_issues(self) -> list[ReaderIssue]:
        return [*self.reader_issues, *(issue for image in self.images for issue in image.issues),
                *self.geometry_issues]
