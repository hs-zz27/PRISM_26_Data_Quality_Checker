"""Requests and history records for rulebook, finding and sample review."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .finding import FindingResolution, FindingReview
from .rule import NonBlankText, Rule


ReviewedFindingResolution = Literal[
    FindingResolution.ACCEPTED, FindingResolution.REJECTED, FindingResolution.UNSURE,
]


class FindingReviewRequest(BaseModel):
    """Resolve a finding at the review revision the caller last inspected."""

    model_config = ConfigDict(extra="forbid", strict=True)

    expected_review_version: int = Field(ge=0, description="Last-read finding review version.")
    resolution: ReviewedFindingResolution
    reviewer_id: NonBlankText
    comment: str


class FindingReviewRecord(FindingReview):
    """One attributed decision in a finding's append-only review history."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    finding_id: NonBlankText
    revision: int = Field(ge=1)
    resolution: ReviewedFindingResolution
    reviewer_id: NonBlankText
    comment: str
    updated_at: AwareDatetime


class FindingReviewPage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[FindingReviewRecord]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)


class SampleReviewDecision(StrEnum):
    """The reviewer's data-use decision, independent of machine findings."""

    PENDING_REVIEW = "pending_review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NEEDS_CORRECTION = "needs_correction"


class SampleReviewRequest(BaseModel):
    """Decide on an explicit audit sample pin at the last-read review revision."""

    model_config = ConfigDict(extra="forbid", strict=True)

    expected_sample_revision: int = Field(
        ge=1, description="Stored audit sample revision, not a live asset freshness check.",
    )
    expected_review_version: int = Field(ge=0, description="Last-read sample review version.")
    decision: SampleReviewDecision = Field(strict=False)
    reviewer_id: NonBlankText
    comment: str


class SampleReview(BaseModel):
    """Current decision for one audited sample revision, initially pending."""

    model_config = ConfigDict(extra="forbid", strict=True)

    audit_id: NonBlankText
    sample_id: NonBlankText
    sample_revision: int = Field(ge=1)
    revision: int = Field(default=0, ge=0, description="Review revision, not annotation revision.")
    decision: SampleReviewDecision = Field(default=SampleReviewDecision.PENDING_REVIEW, strict=False)
    reviewer_id: NonBlankText | None = None
    comment: str | None = None
    updated_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def require_review_attribution(self) -> "SampleReview":
        if self.revision == 0:
            if self.decision is not SampleReviewDecision.PENDING_REVIEW or any(
                value is not None for value in (self.reviewer_id, self.comment, self.updated_at)
            ):
                raise ValueError("An initial sample review must be pending without attribution.")
        elif self.reviewer_id is None or self.comment is None or self.updated_at is None:
            raise ValueError("A saved sample review requires a reviewer ID, comment and timestamp.")
        return self


class SampleReviewRecord(SampleReview):
    """An attributed decision, including explicit reopening to pending review."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    revision: int = Field(ge=1)
    reviewer_id: NonBlankText
    comment: str
    updated_at: AwareDatetime


class SampleReviewPage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[SampleReviewRecord]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)


class RulebookReviewAction(StrEnum):
    EDIT = "edit"
    CONFIRM = "confirm"
    REJECT = "reject"
    REVISE = "revise"


class RulebookAction(BaseModel):
    """Identify the reviewed version and the person submitting the action."""

    model_config = ConfigDict(extra="forbid", strict=True)

    expected_version: int = Field(ge=1)
    reviewer_id: NonBlankText


class RulebookReplace(RulebookAction):
    """Replace the complete draft contents, including individual rule decisions.

    Omitted rules are removed. New IDs must use reviewer origin; retained IDs
    preserve their origin. Confirmation states are explicit reviewer decisions.
    """

    guideline_text: str
    rules: list[Rule]


class RulebookReviewRecord(BaseModel):
    """Attribution for one new snapshot, assigned by the backend."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    rulebook_id: NonBlankText
    version: int = Field(ge=2)
    reviewer_id: NonBlankText
    action: RulebookReviewAction = Field(strict=False)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))


class RulebookReviewPage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[RulebookReviewRecord]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)
