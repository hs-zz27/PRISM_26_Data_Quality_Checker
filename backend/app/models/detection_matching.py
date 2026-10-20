"""Evidence from comparing person detections with annotation boxes.

These records describe geometric overlap only. They carry no pass/fail meaning,
and an unmatched record is not by itself an error in the detections or the
annotations.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MATCHING_COORDINATE_CONVENTION = (
    "Continuous, zero-based pixel-edge boxes [xmin, ymin, xmax, ymax] in the original "
    "image: width = xmax - xmin and height = ymax - ymin. Detector boxes are used as "
    "supplied. One-based inclusive annotation boxes [xmin, ymin, xmax, ymax] are "
    "converted to [xmin - 1, ymin - 1, xmax, ymax]."
)
MATCHING_POLICY = "connected_components_v1"
MATCHING_POLICY_DESCRIPTION = (
    "Detection-annotation pairs whose IoU is at least the minimum form a bipartite "
    "graph. A connected component with exactly one detection and one annotation is a "
    "match. Any larger component is ambiguous and is left for review: no assignment, "
    "confidence ranking or tie-breaking is applied."
)
MATCHING_INTERPRETATION = (
    "An unmatched record only means no eligible counterpart was found among the valid, "
    "considered inputs. An unmatched detection may be a missing annotation, a duplicate "
    "detection or a false positive. An unmatched annotation may be a detector miss or an "
    "annotation problem. Neither establishes an error without review."
)

InvalidReason = Literal[
    "malformed_record",
    "invalid_box",
    "zero_area_box",
    "box_outside_image",
    "invalid_score",
    "invalid_object_id",
    "invalid_label",
]


class DetectionRef(BaseModel):
    """A detection identified by its position in the caller's list."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    index: int = Field(ge=0)
    score: float = Field(ge=0, le=1, allow_inf_nan=False)


class AnnotationRef(BaseModel):
    """An annotation identified by list position. `object_id` may repeat across records."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    index: int = Field(ge=0)
    object_id: int
    label: str


class PairEvidence(BaseModel):
    """One detection-annotation pair whose IoU reached the minimum."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    detection: DetectionRef
    annotation: AnnotationRef
    iou: float = Field(gt=0, le=1, allow_inf_nan=False)


class AmbiguousGroup(BaseModel):
    """A connected group of eligible pairs that is larger than one detection and one annotation."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    detections: list[DetectionRef] = Field(min_length=1)
    annotations: list[AnnotationRef] = Field(min_length=1)
    pairs: list[PairEvidence] = Field(min_length=1)


class InvalidRecord(BaseModel):
    """An input record that could not be compared, with the reason."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    record_type: Literal["detection", "annotation"]
    index: int = Field(ge=0)
    object_id: int | None = None  # annotations only, when the source ID is readable
    reason: InvalidReason
    detail: str = Field(min_length=1)


class DetectionMatchingResult(BaseModel):
    """Every input record appears in exactly one of the record lists below."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    coordinate_convention: str = MATCHING_COORDINATE_CONVENTION
    policy: str = MATCHING_POLICY
    policy_description: str = MATCHING_POLICY_DESCRIPTION
    interpretation: str = MATCHING_INTERPRETATION
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    min_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    min_iou: float = Field(gt=0, le=1, allow_inf_nan=False)
    matches: list[PairEvidence]
    ambiguous_groups: list[AmbiguousGroup]
    unmatched_detections: list[DetectionRef]
    unmatched_annotations: list[AnnotationRef]
    confidence_filtered_detections: list[DetectionRef]
    invalid_records: list[InvalidRecord]
    comparison_complete: bool

    @model_validator(mode="after")
    def require_incomplete_when_records_are_invalid(self):
        if self.comparison_complete == bool(self.invalid_records):
            raise ValueError("comparison_complete must be False exactly when invalid_records is not empty")
        return self