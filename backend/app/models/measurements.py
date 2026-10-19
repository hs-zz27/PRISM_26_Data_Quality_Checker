"""Shared result shapes for measurement functions and sample audit reports."""

from typing import Any, Literal, TypedDict

from .annotation import AnnotationObject, ImageSize


ASSET_LOAD_FAILURE_DETAIL = "The source asset could not be loaded."


class MeasurementResult(TypedDict):
    measurement_available: bool
    reason: str | None


class CheckEnvelope(TypedDict):
    status: Literal["completed", "unavailable"]
    reason: str | None
    detail: str | None
    result: dict[str, Any] | None


class ProcessingError(TypedDict):
    stage: str
    reason: str
    detail: str


class ObjectMeasurements(TypedDict):
    annotation: AnnotationObject
    checks: dict[str, CheckEnvelope]


class AuditMeasurements(TypedDict):
    image_id: str
    image_size: ImageSize | None
    annotation_text: str | None
    allowed_labels: list[str]
    status: Literal["completed", "incomplete"]
    review_status: Literal["pending_review"]
    errors: list[ProcessingError]
    checks: dict[str, CheckEnvelope]
    objects: list[ObjectMeasurements]
