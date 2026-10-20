"""Public audit submission and collection transports."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .audit import AuditRun
from .dataset import Count, Identifier, Revision
from .job import Job


class AuditRequest(BaseModel):
    """Select catalogue heads; private paths and execution settings stay server-side."""

    model_config = ConfigDict(extra="forbid", strict=True)

    dataset_id: Identifier
    rulebook_id: Identifier
    rulebook_version: Revision
    sample_ids: list[Identifier] | None = Field(
        default=None, min_length=1, json_schema_extra={"uniqueItems": True},
        description="Omit or send null for all catalogue samples; a subset preserves request order.",
    )
    feedback_policy_id: None = Field(
        default=None, description="Feedback policies are not implemented; omit or send null.",
    )

    @field_validator("sample_ids")
    @classmethod
    def unique_samples(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("sample_ids must contain unique identifiers.")
        return value


class AuditStarted(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    audit: AuditRun
    job: Job
    # Reserved relationship fields also used by the planned recheck contract.
    parent_audit_id: Identifier | None = None
    feedback_policy_id: Identifier | None = None


class AuditPage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[AuditRun]
    limit: int = Field(ge=1, le=100)
    offset: Count
    total: Count
    next_offset: Count | None
