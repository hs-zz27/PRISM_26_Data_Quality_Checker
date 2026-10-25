"""Audit submission, persisted summaries and the human review queue."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from ..models.audit import AuditRun
from ..models.audit_submission import AuditPage, AuditRequest, AuditStarted
from ..models.audit_read import AuditSamplePage, ErrorResponse, FindingPage, SampleAudit, ToolStatus
from ..models.finding import FindingOutcome
from ..models.review import SampleReviewDecision
from ..services.audit_submission import AuditSubmissionService
from ..services.batch_audit import BatchAuditService
from ..storage.audit_read_repository import AuditReadRepository
from ..storage.dataset_repository import DatasetRepository
from ..storage.repository import RulebookRepository
from ..storage.workflow_repository import AuditRepository


router = APIRouter(prefix="/audits", tags=["Audits"], responses={
    422: {"model": ErrorResponse, "description": "Invalid request fields."},
    500: {"model": ErrorResponse, "description": "Unexpected server error."},
    503: {"model": ErrorResponse, "description": "Audit storage unavailable."},
})
NOT_FOUND = {404: {"model": ErrorResponse, "description": "The requested resource does not exist."}}


def get_audit_read_repository() -> AuditReadRepository:
    """Resolve configuration at request time; imports never connect or migrate."""
    return AuditReadRepository()


def get_audit_repository() -> AuditRepository:
    """Resolve audit storage at request time."""
    return AuditRepository()


def get_audit_submission_service(
    repository: Annotated[AuditRepository, Depends(get_audit_repository)],
) -> AuditSubmissionService:
    return AuditSubmissionService(
        catalogue=DatasetRepository(settings=repository.settings),
        batch=BatchAuditService(
            rulebook_repository=RulebookRepository(settings=repository.settings),
            audit_repository=repository,
        ),
    )


Repository = Annotated[AuditReadRepository, Depends(get_audit_read_repository)]
AuditRepositoryDependency = Annotated[AuditRepository, Depends(get_audit_repository)]
Identifier = Annotated[str, Path(min_length=1, pattern=r"\S")]


@router.post(
    "", response_model=AuditStarted, status_code=202, operation_id="startAudit",
    responses={**NOT_FOUND, 409: {"model": ErrorResponse, "description": "Dataset or rulebook is not ready."}},
)
def start_audit(
    request: AuditRequest,
    service: Annotated[AuditSubmissionService, Depends(get_audit_submission_service)],
) -> AuditStarted:
    """Queue a snapshot audit; processing runs in the explicit audit worker."""
    return service.start(request)


@router.get("", response_model=AuditPage, operation_id="listAudits")
def list_audits(
    repository: AuditRepositoryDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=9_223_372_036_854_775_807)] = 0,
    roots_only: bool = False,
) -> AuditPage:
    """Read all audit states, newest first, without running or recovering jobs."""
    return repository.list_audits(limit=limit, offset=offset, **({"roots_only": True} if roots_only else {}))


@router.get("/{audit_id}", response_model=AuditRun, operation_id="getAudit", responses=NOT_FOUND)
def get_audit(audit_id: Identifier, repository: AuditRepositoryDependency) -> AuditRun:
    """Read persisted audit progress without changing workflow or review state."""
    return repository.get_audit(audit_id)


@router.get("/{audit_id}/samples", response_model=AuditSamplePage, operation_id="listAuditSamples", responses=NOT_FOUND)
def list_audit_samples(
    audit_id: Identifier,
    repository: Repository,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    tool_status: ToolStatus | None = None,
    review_status: SampleReviewDecision | None = None,
    include_rechecks: bool = False,
) -> AuditSamplePage:
    """Read all samples in audit order, optionally filtering machine or human state."""
    return repository.list_samples(
        audit_id, limit=limit, offset=offset, tool_status=tool_status, review_status=review_status,
        **({"include_rechecks": True} if include_rechecks else {}),
    )


@router.get("/{audit_id}/samples/{sample_id}", response_model=SampleAudit, operation_id="getAuditSample", responses=NOT_FOUND)
def get_audit_sample(
    audit_id: Identifier, sample_id: Identifier, repository: Repository,
    include_rechecks: bool = False,
) -> SampleAudit:
    """Read stored evidence and current reviews for the sample's audited revision."""
    return repository.get_sample(audit_id, sample_id, **({"include_rechecks": True} if include_rechecks else {}))


@router.get(
    "/{audit_id}/findings", response_model=FindingPage, operation_id="listAuditFindings",
    responses=NOT_FOUND,
)
def list_audit_findings(
    audit_id: Annotated[str, Path(min_length=1, pattern=r"^[^\x00]*[^\s\x00][^\x00]*$")],
    repository: Repository,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=9_223_372_036_854_775_807)] = 0,
    sample_id: Annotated[str | None, Query(min_length=1, pattern=r"^[^\x00]*[^\s\x00][^\x00]*$")] = None,
    outcome: FindingOutcome | None = None,
    include_rechecks: bool = False,
) -> FindingPage:
    """Read an audit's machine findings with their latest human reviews, in finding_id order."""
    return repository.list_audit_findings(
        audit_id, limit=limit, offset=offset, sample_id=sample_id, outcome=outcome,
        **({"include_rechecks": True} if include_rechecks else {}),
    )
