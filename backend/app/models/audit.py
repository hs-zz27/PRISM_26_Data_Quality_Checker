"""Pinned batch-audit requests and durable audit summaries."""

from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .job import JobStatus


NonBlank = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]


class BatchAuditRequest(BaseModel):
    """All immutable inputs needed to process a bounded sample batch.

    ``dataset_root`` is intentionally only carried as a string.  Filesystem
    existence, symlink policy and archive/path validation belong to a future
    dataset-import boundary and are not performed here.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    dataset_id: NonBlank
    dataset_root: str
    rulebook_id: NonBlank
    rulebook_version: int = Field(ge=1)
    sample_ids: list[NonBlank] = Field(min_length=1)
    allowed_labels: list[NonBlank] | None = Field(default=None, min_length=1)
    sample_revisions: dict[str, int] = Field(default_factory=dict)
    connectivity: int = Field(default=8)
    feedback_policy_id: NonBlank | None = None

    @field_validator("sample_ids")
    @classmethod
    def require_unique_samples(cls, sample_ids: list[str]) -> list[str]:
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("sample_ids must be unique within an audit.")
        return sample_ids

    @field_validator("allowed_labels")
    @classmethod
    def require_unique_labels(cls, labels: list[str] | None) -> list[str] | None:
        if labels is not None and len(labels) != len(set(labels)):
            raise ValueError("allowed_labels must not contain duplicates.")
        return labels

    @field_validator("sample_revisions")
    @classmethod
    def validate_revisions(cls, revisions: dict[str, int]) -> dict[str, int]:
        if any(type(revision) is not int or revision < 1 for revision in revisions.values()):
            raise ValueError("sample revisions must be positive integers.")
        return revisions

    @field_validator("connectivity")
    @classmethod
    def validate_connectivity(cls, connectivity: int) -> int:
        if type(connectivity) is not int or connectivity not in (4, 8):
            raise ValueError("connectivity must be the integer 4 or 8.")
        return connectivity

    @model_validator(mode="after")
    def reject_unknown_revision_keys(self) -> "BatchAuditRequest":
        unknown = set(self.sample_revisions).difference(self.sample_ids)
        if unknown:
            raise ValueError("sample_revisions may only contain requested sample IDs.")
        return self


class AuditRun(BaseModel):
    """Read model for a durable audit and its job-backed progress."""

    model_config = ConfigDict(extra="forbid", strict=True)

    audit_id: NonBlank
    job_id: NonBlank
    dataset_id: NonBlank
    rulebook_id: NonBlank
    rulebook_version: int = Field(ge=1)
    status: JobStatus = Field(strict=False)
    sample_count: int = Field(ge=0)
    processed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    incomplete_count: int = Field(default=0, ge=0)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_counts(self) -> "AuditRun":
        if self.processed_count > self.sample_count:
            raise ValueError("processed_count cannot exceed sample_count.")
        if self.failed_count > self.processed_count:
            raise ValueError("failed_count cannot exceed processed_count.")
        if self.incomplete_count > self.processed_count:
            raise ValueError("incomplete_count cannot exceed processed_count.")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at.")
        return self


class AuditFailure(BaseModel):
    """A sample-level technical failure retained for later review/recovery."""

    model_config = ConfigDict(extra="forbid", strict=True)

    failure_id: NonBlank
    audit_id: NonBlank
    sample_id: NonBlank
    sample_revision: int = Field(ge=1)
    stage: NonBlank
    reason: NonBlank
    detail: NonBlank
    created_at: AwareDatetime


class AuditSampleClaim(BaseModel):
    """Internal lease for one sequential sample."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    audit_id: NonBlank
    job_id: NonBlank
    sample_id: NonBlank
    sample_revision: int = Field(ge=1)
    worker_id: NonBlank
    claim_token: NonBlank
    lease_until: AwareDatetime
    processing_deadline: AwareDatetime
    attempt_count: int = Field(ge=1, le=3)
