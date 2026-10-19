"""Durable job records shared by workers and the HTTP job endpoints."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .rule import NonBlankText


class JobStatus(StrEnum):
    """Persisted lifecycle states for asynchronous work."""

    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobKind(StrEnum):
    """Kinds reserved by the public API contract."""

    RULE_EXTRACTION = "rule_extraction"
    DATASET_IMPORT = "dataset_import"
    AUDIT = "audit"
    RECHECK = "recheck"
    EXPORT = "export"
    FEEDBACK_EVALUATION = "feedback_evaluation"


class Progress(BaseModel):
    """Safe-boundary progress; unknown work is represented by null values."""

    model_config = ConfigDict(extra="forbid", strict=True)

    phase: NonBlankText
    processed: int = Field(ge=0)
    total: int | None = Field(default=None, ge=0)
    fraction: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_fraction(self) -> "Progress":
        if self.total is None and self.fraction is not None:
            raise ValueError("fraction must be null when total is unknown.")
        if self.total is not None and self.processed > self.total:
            raise ValueError("processed cannot exceed total.")
        if self.total is not None and self.total > 0 and self.fraction is None:
            raise ValueError("fraction is required when total is known.")
        if self.total == 0 and self.fraction not in (0.0, 1.0):
            raise ValueError("an empty job must have a zero or complete fraction.")
        return self


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    field: str | None = None
    message: NonBlankText


class ApiError(BaseModel):
    """Structured, secret-free error information retained on failed jobs."""

    model_config = ConfigDict(extra="forbid", strict=True)

    code: NonBlankText
    message: NonBlankText
    details: list[ErrorDetail] = Field(default_factory=list)
    request_id: NonBlankText


JobResultKind = Literal["rulebook", "dataset", "audit", "export", "feedback_evaluation"]


class ResultReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: JobResultKind
    id: NonBlankText
    version: int | None = Field(default=None, ge=1)
    href: NonBlankText


class Job(BaseModel):
    """The public job resource; worker lease fields stay private in storage."""

    model_config = ConfigDict(extra="forbid", strict=True)

    job_id: NonBlankText
    kind: JobKind = Field(strict=False)
    status: JobStatus = Field(strict=False)
    progress: Progress
    result: ResultReference | None = None
    error: ApiError | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_terminal_payload(self) -> "Job":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at.")
        if self.status is JobStatus.COMPLETED:
            if self.result is None or self.error is not None:
                raise ValueError("completed jobs require a result and cannot contain an error.")
        if self.status is JobStatus.FAILED:
            if self.error is None:
                raise ValueError("failed jobs require a structured error.")
            if self.result is not None:
                raise ValueError("failed jobs cannot contain a result.")
        if self.status is not JobStatus.FAILED and self.error is not None:
            raise ValueError("only failed jobs may contain an error.")
        return self

    @classmethod
    def queued(
        cls,
        *,
        job_id: str,
        kind: JobKind,
        total: int | None,
        now: datetime | None = None,
    ) -> "Job":
        """Build a new queued job without opening a database connection."""
        timestamp = datetime.now(UTC) if now is None else now
        fraction = None if total is None else (1.0 if total == 0 else 0.0)
        return cls(
            job_id=job_id,
            kind=kind,
            status=JobStatus.QUEUED,
            progress=Progress(phase="queued", processed=0, total=total, fraction=fraction),
            created_at=timestamp,
            updated_at=timestamp,
        )
