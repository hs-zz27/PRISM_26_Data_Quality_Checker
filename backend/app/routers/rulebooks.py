"""HTTP manual draft creation, reads and reviewer actions for rulebooks."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from ..models.audit_read import ErrorResponse
from ..models.review import RulebookAction, RulebookReplace, RulebookReviewPage
from ..models.rule import Rulebook, RulebookPage
from ..models.rulebook_creation import RulebookCreate
from ..models.rule_extraction import RuleExtractionRequest
from ..models.job import Job
from ..core.config import DatabaseConfigurationError
from ..services.extraction_jobs import submit_extraction
from ..storage.extraction_repository import ExtractionRepository, ExtractionSubmissionError
from ..services.rulebook_creation import create_rulebook as create_rulebook_service
from ..services.review import (
    confirm_rulebook as confirm_rulebook_service,
    reject_rulebook as reject_rulebook_service,
    revise_rulebook as revise_rulebook_service,
    save_rulebook_review as save_rulebook_review_service,
)
from ..storage.repository import RulebookRepository

router = APIRouter(prefix="/rulebooks", tags=["Rulebooks"], responses={
    404: {"model": ErrorResponse, "description": "Rulebook or version not found."},
    422: {"model": ErrorResponse, "description": "Invalid request fields."},
    500: {"model": ErrorResponse, "description": "Unexpected server error."},
    503: {"model": ErrorResponse, "description": "Rulebook storage unavailable."},
})

def get_rulebook_repository() -> RulebookRepository:
    """Resolve configuration per request; never connect or migrate on import."""
    return RulebookRepository()

def get_extraction_repository() -> ExtractionRepository:
    """Resolve per request; construction does not connect, migrate or start a worker."""
    try:
        return ExtractionRepository()
    except DatabaseConfigurationError as error:
        raise ExtractionSubmissionError("Extraction configuration is unavailable.") from error

ExtractionStore = Annotated[ExtractionRepository, Depends(get_extraction_repository)]

@router.post(
    "/extractions", response_model=Job, status_code=202, operation_id="extractRulebook",
    responses={503: {
        "model": ErrorResponse, "description": "Extraction submission unavailable.",
        "content": {"application/json": {"examples": {"DEPENDENCY_UNAVAILABLE": {"value": {"error": {
            "code": "DEPENDENCY_UNAVAILABLE", "message": "Extraction could not be durably accepted.",
            "details": [], "request_id": "request_demo",
        }}}}}},
    }},
)
def extract_rulebook_endpoint(request: RuleExtractionRequest, repository: ExtractionStore) -> Job:
    """Commit a private queued request only; an explicit worker performs extraction."""
    return submit_extraction(request, repository=repository)

Repository = Annotated[RulebookRepository, Depends(get_rulebook_repository)]
Identifier = Annotated[str, Path(min_length=1, pattern=r"\S")]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]
Version = Annotated[int | None, Query(ge=1)]

@router.get("", response_model=RulebookPage, operation_id="listRulebooks")
def list_rulebooks_endpoint(
    repository: Repository, limit: Limit = 20, offset: Offset = 0,
) -> RulebookPage:
    """Read one latest snapshot per book, in repository-defined order."""
    return repository.list_rulebooks(limit=limit, offset=offset)

@router.get("/{rulebook_id}", response_model=Rulebook, operation_id="getRulebook")
def get_rulebook_endpoint(
    rulebook_id: Identifier, repository: Repository, version: Version = None,
) -> Rulebook:
    """Read exactly the requested version; omission means latest, in any state."""
    return repository.get_rulebook(rulebook_id, version=version)

@router.get(
    "/{rulebook_id}/versions", response_model=RulebookPage, operation_id="listRulebookVersions",
)
def list_rulebook_versions_endpoint(
    rulebook_id: Identifier, repository: Repository, limit: Limit = 20, offset: Offset = 0,
) -> RulebookPage:
    """Read immutable snapshots in ascending version order."""
    return repository.list_rulebook_versions(rulebook_id, limit=limit, offset=offset)

@router.get(
    "/{rulebook_id}/reviews", response_model=RulebookReviewPage, operation_id="listRulebookReviews",
)
def list_rulebook_reviews_endpoint(
    rulebook_id: Identifier, repository: Repository, limit: Limit = 20, offset: Offset = 0,
) -> RulebookReviewPage:
    """Read real attributed actions only; historical saves may have no reviews."""
    return repository.list_rulebook_reviews(rulebook_id, limit=limit, offset=offset)

_CONFLICT_MESSAGES = {
    "VERSION_CONFLICT": "The rulebook is out of date. Refresh before resubmitting.",
    "INVALID_RULEBOOK_STATE": "This action is not allowed in the current rulebook state.",
    "RULEBOOK_NOT_READY": "Confirm every retained rule and remove rejected rules before confirming the book.",
}

def _conflicts(*codes: str) -> dict:
    """Document only the conflicts supported by this mutation, never by GETs."""
    return {409: {
        "model": ErrorResponse,
        "description": "Conflict; see the error code.",
        "content": {"application/json": {"examples": {
            code: {"value": {"error": {
                "code": code, "message": _CONFLICT_MESSAGES[code],
                "details": [], "request_id": "request_demo",
            }}} for code in codes
        }}},
    }}

@router.post(
    "", response_model=Rulebook, status_code=201, operation_id="createRulebook",
    responses=_conflicts("VERSION_CONFLICT"),
)
def create_rulebook_endpoint(request: RulebookCreate, repository: Repository) -> Rulebook:
    """Persist a manual version-1 draft; separate successful requests create separate books."""
    return create_rulebook_service(request, repository=repository)

@router.put(
    "/{rulebook_id}", response_model=Rulebook, operation_id="replaceDraftRulebook",
    responses=_conflicts("VERSION_CONFLICT", "INVALID_RULEBOOK_STATE"),
)
def replace_draft_rulebook_endpoint(
    rulebook_id: Identifier, request: RulebookReplace, repository: Repository,
) -> Rulebook:
    """Save the complete draft and explicit rule decisions through the service."""
    return save_rulebook_review_service(rulebook_id, request, repository=repository)

@router.post(
    "/{rulebook_id}/confirm", response_model=Rulebook, operation_id="confirmRulebook",
    responses=_conflicts("VERSION_CONFLICT", "INVALID_RULEBOOK_STATE", "RULEBOOK_NOT_READY"),
)
def confirm_rulebook_endpoint(
    rulebook_id: Identifier, request: RulebookAction, repository: Repository,
) -> Rulebook:
    """Confirm a ready latest draft; never invent individual rule decisions."""
    return confirm_rulebook_service(rulebook_id, request, repository=repository)

@router.post(
    "/{rulebook_id}/reject", response_model=Rulebook, operation_id="rejectRulebook",
    responses=_conflicts("VERSION_CONFLICT", "INVALID_RULEBOOK_STATE"),
)
def reject_rulebook_endpoint(
    rulebook_id: Identifier, request: RulebookAction, repository: Repository,
) -> Rulebook:
    """Reject a latest draft while preserving the individual rule states."""
    return reject_rulebook_service(rulebook_id, request, repository=repository)

@router.post(
    "/{rulebook_id}/revisions", response_model=Rulebook, status_code=201, operation_id="reviseRulebook",
    responses=_conflicts("VERSION_CONFLICT", "INVALID_RULEBOOK_STATE"),
)
def revise_rulebook_endpoint(
    rulebook_id: Identifier, request: RulebookAction, repository: Repository,
) -> Rulebook:
    """Append a draft revision of a final book; service owns resets and history."""
    return revise_rulebook_service(rulebook_id, request, repository=repository)
