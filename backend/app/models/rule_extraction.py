"""Public submission and private, restart-stable extraction execution records."""

from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ..core.config import OllamaSettings
from .job import Job

Identifier = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]

class RuleExtractionRequest(BaseModel):
    """Validate nonblankness without altering whitespace or Unicode."""

    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")
    guideline_text: Identifier

class ExtractionPolicy(BaseModel):
    """Server-only execution bounds, unrelated to guideline semantics/token limits."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    max_seconds: int = Field(default=300, ge=1, le=86_400)
    lease_seconds: int = Field(default=30, ge=2, le=300)
    poll_seconds: float = Field(default=0.2, gt=0, le=5, allow_inf_nan=False)
    renew_seconds: float = Field(default=5.0, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_cadence(self) -> "ExtractionPolicy":
        if not self.poll_seconds <= self.renew_seconds < self.lease_seconds / 2:
            raise ValueError("Require poll <= renew < half the lease duration.")
        return self

class ExtractionInput(BaseModel):
    """Private ledger snapshot; never embed this model in a public Job."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    job_id: Identifier
    rulebook_id: Annotated[str, StringConstraints(pattern=r"^rulebook_[0-9a-f]{32}$")]
    guideline_text: Identifier
    settings: OllamaSettings
    policy: ExtractionPolicy
    started_at: AwareDatetime | None = None
    deadline: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_attempt_times(self) -> "ExtractionInput":
        if (self.started_at is None) != (self.deadline is None):
            raise ValueError("Attempt start and deadline must both be present or absent.")
        if self.started_at is not None:
            assert self.deadline is not None
            if (self.deadline - self.started_at).total_seconds() != self.policy.max_seconds:
                raise ValueError("The original attempt deadline must match its saved policy.")
        return self

class ExtractionClaim(BaseModel):
    """An untrusted caller's claim, always compared to database-row ownership."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    job: Job
    request: ExtractionInput
    worker_id: Identifier
    claim_token: Identifier
    lease_until: AwareDatetime

    @model_validator(mode="after")
    def validate_identity(self) -> "ExtractionClaim":
        if self.job.job_id != self.request.job_id or self.job.kind.value != "rule_extraction":
            raise ValueError("Claim and private request identities disagree.")
        if self.request.started_at is None:
            raise ValueError("A claim must have a persisted processing deadline.")
        return self
