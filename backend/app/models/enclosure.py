"""Typed, immutable contract for the pedestrian-box enclosure check.

The check judges one rule: each annotation box must enclose a visible pedestrian.
A partly visible but identifiable pedestrian inside the box counts; a person entirely
outside it does not. Nothing here requires a whole body, motion, box tightness or a
visibility percentage. These models carry no database, network or model behavior.
"""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Annotated, Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from ..core.model_host import validate_local_model_host

CHECK_NAME = "pedestrian_box_enclosure"
PROTOCOL_VERSION = "pedestrian-box-context-v1"
VIEW_VERSION = "box-context-png-v1"
RESPONSE_SCHEMA_VERSION = "box-prediction-v1"
MAX_BOXES = 32


def enclosure_case_identity(*, rulebook_id: str, rulebook_version: int, rule_id: str,
                            sample_id: str, sample_revision: int, image_sha256: str,
                            annotation_sha256: str) -> str:
    """Stable identity for one rule and exact source revision; no execution or storage access."""
    document = dict(rulebook_id=rulebook_id, rulebook_version=rulebook_version, rule_id=rule_id,
                    sample_id=sample_id, sample_revision=sample_revision, check_name=CHECK_NAME,
                    protocol_version=PROTOCOL_VERSION, image_sha256=image_sha256,
                    annotation_sha256=annotation_sha256)
    return sha256(json.dumps(document, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ModelDigest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
Outcome = Literal["pass", "fail", "unsure", "unavailable"]
Reference = Literal["image", "annotations", "guideline"]


class EnclosureModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class EnclosureExecutionPin(EnclosureModel):
    """Everything that decides how a box is judged, frozen when an audit is accepted.

    Queued, retried and rechecked work uses this pin, never current defaults. The view
    and prompt are versioned honestly: transformed pixels are never labeled as the
    untouched original image.
    """

    check_name: Literal["pedestrian_box_enclosure"] = CHECK_NAME
    protocol_version: Literal["pedestrian-box-context-v1"] = PROTOCOL_VERSION
    view_version: Literal["box-context-png-v1"] = VIEW_VERSION
    response_schema_version: Literal["box-prediction-v1"] = RESPONSE_SCHEMA_VERSION
    identity_schema: Literal["bound-v1"] = "bound-v1"
    host: str
    model: Annotated[str, StringConstraints(min_length=1, max_length=255, pattern=r"\S")]
    model_digest: ModelDigest
    system_prompt_sha256: Digest
    instruction_sha256: Digest
    temperature: Literal[0] = 0
    num_ctx: int = Field(ge=1024, le=131072)
    num_predict: int = Field(ge=128, le=2048)
    num_thread: int | None = Field(default=None, ge=1, le=1024)  # None keeps automatic selection
    request_timeout_seconds: int = Field(ge=1, le=3600)
    sample_budget_seconds: int = Field(ge=1, le=86_400)
    max_boxes: int = Field(ge=1, le=MAX_BOXES)
    max_request_bytes: int = Field(ge=1024, le=128 * 1024**2)
    max_response_bytes: int = Field(ge=1024, le=65_536)
    keep_alive_seconds: int = Field(ge=0, le=3600)
    max_image_bytes: int = Field(default=8 * 1024**2, ge=1024, le=16 * 1024**2)
    max_annotation_bytes: int = Field(default=131_072, ge=1024, le=262_144)
    max_pixels: int = Field(default=16_000_000, ge=1, le=32_000_000)

    @field_validator("host")
    @classmethod
    def loopback_only(cls, host: str) -> str:
        return validate_local_model_host(host)

    @model_validator(mode="after")
    def one_request_fits_the_sample_budget(self) -> "EnclosureExecutionPin":
        if self.request_timeout_seconds > self.sample_budget_seconds:
            raise ValueError("A single request cannot be allowed longer than the whole sample budget.")
        return self


class EnclosureRuntime(EnclosureModel):
    """The frozen runtime stored on an accepted audit request."""

    execution: EnclosureExecutionPin


class BoxResponse(EnclosureModel):
    """One box's proposal. Identity fields must be echoed exactly or the result is unavailable."""

    case_id: Digest
    rule_id: Annotated[str, StringConstraints(min_length=1, max_length=255, pattern=r"\S")]
    box_number: int = Field(ge=1, le=MAX_BOXES)
    object_id: int
    outcome: Outcome
    reason: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    evidence_references: list[Reference] = Field(max_length=3)


class BoxVerdict(EnclosureModel):
    outcome: Outcome
    model_verified: bool

    @model_validator(mode="after")
    def unverified_results_are_unavailable(self) -> "BoxVerdict":
        if not self.model_verified and self.outcome != "unavailable":
            raise ValueError("Only a verified model result may carry a decision.")
        return self


class EnclosureBoxEvidence(EnclosureModel):
    """Durable provenance for one attempted box, including unavailable attempts."""

    annotation_index: int = Field(ge=0)
    box_number: int = Field(ge=1, le=MAX_BOXES)
    object_id: int
    label: str
    bbox: list[int] = Field(min_length=4, max_length=4)
    view_sha256: Digest
    view_size: list[int] = Field(min_length=2, max_length=2)
    source_window: list[int] = Field(min_length=4, max_length=4)
    target_window: list[int] = Field(min_length=4, max_length=4)
    request_sha256: Digest | None
    outcome: Outcome
    reason: str | None
    evidence_references: list[Reference] = Field(max_length=3)
    model_verified: bool
    duration_ms: float = Field(ge=0, allow_inf_nan=False)
    chat_ms: float | None = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def coherent(self):
        if self.box_number != self.annotation_index + 1:
            raise ValueError("Box ordinal differs from annotation order.")
        BoxVerdict(outcome=self.outcome, model_verified=self.model_verified)
        if self.model_verified and (self.request_sha256 is None or not self.reason):
            raise ValueError("Verified boxes require request provenance and a reason.")
        return self


class EnclosureAggregate(EnclosureModel):
    outcome: Outcome
    reason: str


class EnclosureEvidence(EnclosureModel):
    """The existing enclosure document, retained unchanged in feedback snapshots."""

    protocol_version: Literal["pedestrian-box-context-v1"]
    view_version: Literal["box-context-png-v1"]
    execution: EnclosureExecutionPin
    image_sha256: Digest | None
    annotation_sha256: Digest | None
    boxes_intended: int | None = Field(ge=0)
    case_id: Digest | None = Field(default=None, exclude_if=lambda value: value is None)
    failure: str | None
    aggregate: EnclosureAggregate
    boxes: list[EnclosureBoxEvidence] = Field(max_length=MAX_BOXES)
    feedback_policy_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    feedback_policy_sha256: Digest | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def coherent(self):
        if (self.feedback_policy_id is None) != (self.feedback_policy_sha256 is None):
            raise ValueError("Policy provenance must be complete.")
        if self.failure is None:
            if self.case_id is None or self.boxes_intended != len(self.boxes):
                raise ValueError("Every intended box requires an accounted result.")
            if [box.box_number for box in self.boxes] != list(range(1, len(self.boxes) + 1)):
                raise ValueError("Box order differs from source annotation order.")
            outcome, _ = aggregate_sample(self.boxes)
            if self.aggregate.outcome != outcome:
                raise ValueError("Sample outcome differs from its box evidence.")
        elif self.boxes or self.aggregate.outcome != "unavailable":
            raise ValueError("Failed evidence preparation cannot contain a verdict.")
        return self


def aggregate_sample(boxes: Sequence[BoxVerdict]) -> tuple[Outcome, str]:
    """Any verified failing box fails the sample; then unavailable, then unsure, then pass.

    A sample with no boxes is unavailable and says so: absence of boxes is never read as
    compliance. Every intended box must be present; a caller that lost one must pass an
    unavailable verdict for it.
    """
    if not boxes:
        return "unavailable", "no_annotation_boxes"
    if any(box.outcome == "fail" for box in boxes):
        return "fail", "verified_failing_box"
    if any(box.outcome == "unavailable" for box in boxes):
        return "unavailable", "unavailable_box"
    if any(box.outcome == "unsure" for box in boxes):
        return "unsure", "unsure_box"
    return "pass", "all_boxes_pass"


def bound_response_schema(identity: dict[str, Any]) -> dict[str, Any]:
    """A fresh schema per call that fixes only the known identities.

    Outcomes, reasons and evidence are never constrained, and the returned identities are
    still validated by the caller. Nothing is repaired.
    """
    schema = BoxResponse.model_json_schema()
    for field, value in identity.items():
        schema["properties"][field]["const"] = value
    return schema
