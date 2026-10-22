"""Public read models for the audit queue and one sample's stored evidence."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .audit import AuditFailure
from .finding import Finding
from .job import ApiError
from .json_value import FiniteJsonValue
from .review import SampleReview, SampleReviewDecision
from .rule import NonBlankText


class ToolStatus(StrEnum):
    PENDING = "pending"
    FLAGGED = "flagged"
    UNSURE = "unsure"
    NO_ISSUE = "no_issue"
    UNAVAILABLE = "unavailable"


class FindingCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    pass_count: int = Field(alias="pass", ge=0)
    fail: int = Field(ge=0)
    unsure: int = Field(ge=0)
    unavailable: int = Field(ge=0)


class AssetReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    url: NonBlankText
    content_type: NonBlankText
    revision: int = Field(ge=1)


class SampleAssets(BaseModel):
    """Links advertise captured source availability for the audited revision."""

    model_config = ConfigDict(extra="forbid", strict=True)

    image: AssetReference | None = None
    mask: AssetReference | None = None
    mask_preview: AssetReference | None = None
    thumbnail: AssetReference | None = None


class SampleAuditSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sample_id: NonBlankText
    sample_revision: int = Field(ge=1)
    processing_status: Literal["queued", "running", "completed", "failed", "cancelled"]
    incomplete: bool
    tool_status: ToolStatus = Field(strict=False)
    review_status: SampleReviewDecision = Field(strict=False)
    finding_counts: FindingCounts
    thumbnail_url: NonBlankText | None = None


class AuditSamplePage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[SampleAuditSummary]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)
    next_offset: int | None = Field(ge=0)


class FindingPage(BaseModel):
    """One page of an audit's findings, ordered by ascending finding_id.

    Findings are the stored machine snapshots with their latest human review
    applied. total counts every finding that matches the filters before paging.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[Finding]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)
    next_offset: int | None = Field(ge=0)

    @model_validator(mode="after")
    def require_consistent_paging(self):
        if len(self.items) > self.limit:
            raise ValueError("A page cannot hold more items than its limit.")
        if self.next_offset is not None and (
            self.next_offset != self.offset + len(self.items) or self.next_offset >= self.total
        ):
            raise ValueError("next_offset must follow this page and point at a remaining finding.")
        return self


class SampleAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    audit_id: NonBlankText
    dataset_id: NonBlankText
    summary: SampleAuditSummary
    assets: SampleAssets = Field(default_factory=SampleAssets)
    # Preserve the stored document, including older/minimal collector envelopes.
    measurements: dict[str, FiniteJsonValue] | None
    findings: list[Finding]
    failures: list[AuditFailure]
    review: SampleReview


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    error: ApiError
