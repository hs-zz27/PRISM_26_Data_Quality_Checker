"""HTTP endpoints for finding decisions and their review history."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from ..models.audit_read import ErrorResponse
from ..models.finding import FindingReview
from ..models.review import FindingReviewPage, FindingReviewRequest
from ..services.review import review_finding as review_finding_service
from ..storage.workflow_repository import FindingRepository


router = APIRouter(prefix="/findings", tags=["Reviews"], responses={
    404: {"model": ErrorResponse, "description": "Finding not found."},
    422: {"model": ErrorResponse, "description": "Invalid review request fields."},
    500: {"model": ErrorResponse, "description": "Unexpected server error."},
    503: {"model": ErrorResponse, "description": "Finding storage unavailable."},
})


def get_finding_repository() -> FindingRepository:
    """Resolve database configuration when the request is handled."""
    return FindingRepository()


Repository = Annotated[FindingRepository, Depends(get_finding_repository)]
Identifier = Annotated[str, Path(min_length=1, pattern=r"\S")]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


@router.get(
    "/{finding_id}/reviews",
    response_model=FindingReviewPage,
    operation_id="listFindingReviews",
)
def list_finding_reviews_endpoint(
    finding_id: Identifier,
    repository: Repository,
    limit: Limit = 20,
    offset: Offset = 0,
) -> FindingReviewPage:
    """Read attributed finding decisions in repository-defined revision order."""
    return repository.list_finding_reviews(finding_id, limit=limit, offset=offset)


@router.put(
    "/{finding_id}/review", response_model=FindingReview, operation_id="reviewFinding",
    responses={409: {"model": ErrorResponse, "description": "Finding review version conflict."}},
)
def review_finding_endpoint(
    finding_id: Identifier,
    request: FindingReviewRequest,
    repository: Repository,
) -> FindingReview:
    """Save one attributed finding decision using the service-owned workflow."""
    return review_finding_service(finding_id, request, repository=repository)
