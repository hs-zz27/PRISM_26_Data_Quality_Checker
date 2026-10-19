"""Job status reads and audit-aware cancellation endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from ..models.audit_read import ErrorResponse
from ..models.job import Job
from ..storage.workflow_repository import AuditRepository, JobRepository


router = APIRouter(prefix="/jobs", tags=["Jobs"], responses={
    404: {"model": ErrorResponse, "description": "Job not found."},
    422: {"model": ErrorResponse, "description": "Invalid job identifier."},
    500: {"model": ErrorResponse, "description": "Unexpected job error."},
    503: {"model": ErrorResponse, "description": "Job storage unavailable."},
})


def get_job_repository() -> JobRepository:
    """Resolve database configuration only when a job request is handled."""
    return JobRepository()


def get_audit_repository() -> AuditRepository:
    """Resolve audit storage only when a job cancellation is requested."""
    return AuditRepository()


Repository = Annotated[JobRepository, Depends(get_job_repository)]
Identifier = Annotated[str, Path(min_length=1, pattern=r"\S")]


@router.get("/{job_id}", response_model=Job, operation_id="getJob")
def get_job(job_id: Identifier, repository: Repository) -> Job:
    """Read the persisted public job state without changing workflow state."""
    return repository.get_job(job_id)


@router.post(
    "/{job_id}/cancel",
    response_model=Job,
    status_code=202,
    operation_id="cancelJob",
    responses={409: {"model": ErrorResponse, "description": "The job has already finished."}},
)
def cancel_job(
    job_id: Identifier,
    repository: Annotated[AuditRepository, Depends(get_audit_repository)],
) -> Job:
    """Request cancellation through the audit-aware transactional boundary."""
    return repository.cancel_job(job_id)
