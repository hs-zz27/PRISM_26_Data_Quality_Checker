"""Rule evaluation snapshots, their evidence and separate reviewer decisions."""

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator

from .rule import NonBlankText, RuleOrigin


class FindingOutcome(StrEnum):
    """The tool's conclusion, independent of the reviewer's final decision."""

    PASS = "pass"
    FAIL = "fail"
    UNSURE = "unsure"
    UNAVAILABLE = "unavailable"


class FindingResolution(StrEnum):
    """Accepting a finding confirms its conclusion, not acceptance of the sample."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNSURE = "unsure"


class Evidence(BaseModel):
    """JSON measurements and optional asset links supporting the outcome.

    Asset links may be relative API paths. Empty measurements are valid when
    evaluation could not run, such as for an unsupported requirement.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    image_url: NonBlankText | None = None
    mask_preview_url: NonBlankText | None = None
    crop_url: NonBlankText | None = None
    measurements: dict[str, JsonValue] = Field(default_factory=dict)


class FindingReview(BaseModel):
    """Review state with attribution required for non-pending resolutions.

    Recorded decisions require a positive revision, reviewer and timestamp.
    Future review services own decision transitions.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    revision: int = Field(default=0, ge=0)
    resolution: FindingResolution = Field(default=FindingResolution.PENDING, strict=False)
    reviewer_id: NonBlankText | None = None
    comment: str | None = None
    updated_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def require_review_attribution(self):
        if self.resolution is not FindingResolution.PENDING and (
            self.revision < 1 or self.reviewer_id is None or self.updated_at is None
        ):
            raise ValueError(
                "A non-pending finding review requires a positive revision, reviewer ID and timestamp."
            )
        return self


class Finding(BaseModel):
    """One rule's outcome against a pinned sample revision and rulebook version.

    Rule content is copied into the finding so evidence remains understandable
    after later edits. object_id is absent for sample-wide findings and may be
    nonpositive when reporting invalid source annotation IDs. The evaluator
    owns rulebook confirmation, source matching and reason selection. Extracted
    findings require a source sentence; reviewer-authored findings may omit it.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    finding_id: NonBlankText
    audit_id: NonBlankText
    sample_id: NonBlankText
    sample_revision: int = Field(ge=1)
    object_id: int | None = None
    rulebook_id: NonBlankText
    rulebook_version: int = Field(ge=1)
    rule_id: NonBlankText
    description: NonBlankText
    check_name: NonBlankText | None = None
    outcome: FindingOutcome = Field(strict=False)
    reason: NonBlankText | None = None
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    origin: RuleOrigin = Field(strict=False)
    source_sentence: NonBlankText | None = None
    evidence: Evidence = Field(default_factory=Evidence)
    review: FindingReview = Field(default_factory=FindingReview)

    @model_validator(mode="after")
    def require_extracted_source(self):
        if self.origin is RuleOrigin.EXTRACTED and self.source_sentence is None:
            raise ValueError("An extracted finding must cite a source sentence.")
        return self
