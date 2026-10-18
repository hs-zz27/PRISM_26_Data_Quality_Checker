"""Read endpoints for the human audit review queue."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from ..models.audit_read import AuditSamplePage, ErrorResponse, SampleAudit, ToolStatus
from ..models.review import SampleReviewDecision
from ..storage.audit_read_repository import AuditReadRepository


router = APIRouter(prefix="/audits", tags=["Audits"], responses={
    404: {"model": ErrorResponse, "description": "Audit or sample not found."},
    422: {"model": ErrorResponse, "description": "Invalid request fields."},
    500: {"model": ErrorResponse, "description": "Unexpected server error."},
    503: {"model": ErrorResponse, "description": "Audit storage unavailable."},
})


def get_audit_read_repository() -> AuditReadRepository:
    """Resolve configuration at request time; imports never connect or migrate."""
    return AuditReadRepository()


Repository = Annotated[AuditReadRepository, Depends(get_audit_read_repository)]
Identifier = Annotated[str, Path(min_length=1, pattern=r"\S")]


@router.get("/{audit_id}/samples", response_model=AuditSamplePage, operation_id="listAuditSamples")
def list_audit_samples(
    audit_id: Identifier,
    repository: Repository,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    tool_status: ToolStatus | None = None,
    review_status: SampleReviewDecision | None = None,
) -> AuditSamplePage:
    """Read all samples in audit order, optionally filtering machine or human state."""
    return repository.list_samples(
        audit_id, limit=limit, offset=offset, tool_status=tool_status, review_status=review_status,
    )


@router.get("/{audit_id}/samples/{sample_id}", response_model=SampleAudit, operation_id="getAuditSample")
def get_audit_sample(
    audit_id: Identifier, sample_id: Identifier, repository: Repository,
) -> SampleAudit:
    """Read stored evidence and current reviews for the sample's audited revision."""
    return repository.get_sample(audit_id, sample_id)
