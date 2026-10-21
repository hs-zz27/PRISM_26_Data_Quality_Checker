"""Evidence from running the CPU person detector and comparing it with annotations.

These records describe what ran, what it produced and what could not run. They
hold no findings, pass/fail outcomes or reviewer decisions, and model output is
fallible evidence rather than proof about any annotation.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .detection import COORDINATE_CONVENTION, DetectionResult
from .detection_matching import MATCHING_POLICY, DetectionMatchingResult

ANNOTATION_COORDINATE_CONVENTION = (
    "Annotation boxes are [xmin, ymin, xmax, ymax] in one-based, inclusive integer "
    "pixels. Matching converts them to [xmin - 1, ymin - 1, xmax, ymax] before "
    "comparing them with detector boxes."
)
DETECTION_EVIDENCE_LIMITATIONS = (
    "Detector output is fallible evidence and does not show that any annotation is "
    "correct or incorrect. The detector is asked for every person candidate it "
    "produces (score cutoff 0.0) and the confidence threshold is applied only during "
    "matching. Candidates that torchvision discards internally, such as low region "
    "proposal scores, non-maximum suppression and the per-image detection limit, were "
    "never examined. An unmatched detection or annotation only means no eligible "
    "counterpart was found."
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class EvidenceStatus(StrEnum):
    COMPARED = "compared"
    COMPARED_INCOMPLETE = "compared_incomplete"
    COMPARISON_UNAVAILABLE = "comparison_unavailable"
    DETECTOR_UNAVAILABLE = "detector_unavailable"
    INFERENCE_FAILED = "inference_failed"


class UnavailableReason(StrEnum):
    WEIGHTS_MISSING = "weights_missing"
    WEIGHTS_UNUSABLE = "weights_unusable"
    INFERENCE_FAILED = "inference_failed"
    ANNOTATIONS_UNAVAILABLE = "annotations_unavailable"


_REASONS_BY_STATUS = {
    EvidenceStatus.COMPARISON_UNAVAILABLE: {UnavailableReason.ANNOTATIONS_UNAVAILABLE},
    EvidenceStatus.DETECTOR_UNAVAILABLE: {
        UnavailableReason.WEIGHTS_MISSING, UnavailableReason.WEIGHTS_UNUSABLE,
    },
    EvidenceStatus.INFERENCE_FAILED: {UnavailableReason.INFERENCE_FAILED},
}
_WITH_DETECTOR_OUTPUT = {
    EvidenceStatus.COMPARED,
    EvidenceStatus.COMPARED_INCOMPLETE,
    EvidenceStatus.COMPARISON_UNAVAILABLE,
}
_WITH_MATCHING = {EvidenceStatus.COMPARED, EvidenceStatus.COMPARED_INCOMPLETE}


class ModelIdentity(BaseModel):
    """Which model produced the evidence. A missing hash is never invented."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    architecture: str | None = Field(default=None, min_length=1)
    checkpoint_source: Literal["local_checkpoint", "injected_detector"]
    # SHA-256 of the local checkpoint file; None when unreadable or not a real checkpoint.
    checkpoint_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def injected_detectors_have_no_checkpoint_hash(self):
        if self.checkpoint_source == "injected_detector" and self.checkpoint_sha256 is not None:
            raise ValueError("an injected detector has no checkpoint to hash")
        return self


class ExcludedAnnotation(BaseModel):
    """An annotation outside the configured person-label scope, by original position."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    index: int = Field(ge=0)
    object_id: int | None = None
    label: str


class DetectionEvidence(BaseModel):
    """Detection and comparison evidence for one image.

    `detector_output` keeps its detections in the order returned, and match
    references use positions in that list. Annotation references use positions in
    the caller's original annotation list. When annotations are unavailable,
    `annotation_count` is None and no comparison was attempted; that is different
    from an empty annotation list, which describes a sample with no objects.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    status: EvidenceStatus = Field(strict=False)
    reason: UnavailableReason | None = Field(default=None, strict=False)
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    min_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    min_iou: float = Field(gt=0, le=1, allow_inf_nan=False)
    person_labels: list[str] = Field(min_length=1)
    detector_identity: ModelIdentity
    detector_output: DetectionResult | None = None
    annotations_available: bool
    annotation_count: int | None = Field(default=None, ge=0)
    excluded_annotations: list[ExcludedAnnotation]
    matching: DetectionMatchingResult | None = None
    matching_policy: str = MATCHING_POLICY
    detector_coordinate_convention: str = COORDINATE_CONVENTION
    annotation_coordinate_convention: str = ANNOTATION_COORDINATE_CONVENTION
    limitations: str = DETECTION_EVIDENCE_LIMITATIONS

    @model_validator(mode="after")
    def require_consistent_stages(self):
        allowed = _REASONS_BY_STATUS.get(self.status)
        if allowed is None:
            if self.reason is not None:
                raise ValueError("reason is only set when a stage was unavailable")
        elif self.reason not in allowed:
            raise ValueError(f"reason {self.reason} does not fit status {self.status}")

        if (self.detector_output is not None) != (self.status in _WITH_DETECTOR_OUTPUT):
            raise ValueError("detector_output must be present exactly when detection completed")
        compared = self.status in _WITH_MATCHING
        if (self.matching is not None) != compared:
            raise ValueError("matching must be present exactly when a comparison completed")
        if compared:
            if not self.annotations_available:
                raise ValueError("a comparison needs annotations")
            if self.matching.comparison_complete != (self.status == EvidenceStatus.COMPARED):
                raise ValueError("status must agree with the matching comparison_complete flag")
        if self.status == EvidenceStatus.COMPARISON_UNAVAILABLE and self.annotations_available:
            raise ValueError("comparison_unavailable means the annotations were unavailable")

        if self.annotations_available != (self.annotation_count is not None):
            raise ValueError("annotation_count must be set exactly when annotations are available")
        if not self.annotations_available and self.excluded_annotations:
            raise ValueError("excluded annotations need available annotations")
        return self
