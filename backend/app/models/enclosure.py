"""Typed, immutable contract for the pedestrian-box enclosure check.

The check judges one rule: each annotation box must enclose a visible pedestrian.
A partly visible but identifiable pedestrian inside the box counts; a person entirely
outside it does not. Nothing here requires a whole body, motion, box tightness or a
visibility percentage. These models carry no database, network or model behavior.
"""

from __future__ import annotations

import ipaddress
from typing import Annotated, Any, Literal, Sequence
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

CHECK_NAME = "pedestrian_box_enclosure"
PROTOCOL_VERSION = "pedestrian-box-context-v1"
VIEW_VERSION = "box-context-png-v1"
RESPONSE_SCHEMA_VERSION = "box-prediction-v1"
MAX_BOXES = 32

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
    max_image_bytes: int = Field(default=8 * 1024**2, ge=1, le=64 * 1024**2)
    max_annotation_bytes: int = Field(default=131_072, ge=1, le=1024**2)
    max_pixels: int = Field(default=16_000_000, ge=1, le=100_000_000)

    @field_validator("host")
    @classmethod
    def loopback_only(cls, host: str) -> str:
        parsed = urlsplit(host)
        name = parsed.hostname
        local = name == "localhost"
        if not local and name is not None:
            try:
                local = ipaddress.ip_address(name).is_loopback
            except ValueError:
                local = False
        if (parsed.scheme not in ("http", "https") or not local or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.port == 0):
            raise ValueError("The model host must be a loopback URL without credentials or paths.")
        return host

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
