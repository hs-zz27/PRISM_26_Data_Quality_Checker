"""Parsed source records; invalid source values remain available for auditing."""

from typing import TypedDict


ImageSize = tuple[int, int]
BoundingBox = tuple[int, int, int, int]


class AnnotationObject(TypedDict):
    object_id: int
    label: str
    bbox: BoundingBox


class AnnotationMetadata(TypedDict):
    image_size: tuple[int, int, int] | None
    object_count: int | None
    coordinate_origin: tuple[int, int] | None
    errors: dict[str, str]
