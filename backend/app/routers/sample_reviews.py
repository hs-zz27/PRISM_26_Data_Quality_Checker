"""HTTP endpoints for sample decisions and their review history."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from ..models.audit_read import ErrorResponse
from ..models.review import SampleReview, SampleReviewPage, SampleReviewRequest
from ..services.review import review_sample as review_sample_service
from ..storage.workflow_repository import SampleReviewRepository


router = APIRouter(prefix="/audits", tags=["Reviews"], responses={
    404: {"model": ErrorResponse, "description": "Audit sample not found."},
    409: {"model": ErrorResponse, "description": "Sample review conflict or invalid state."},
    422: {"model": ErrorResponse, "description": "Invalid sample review request fields."},
    500: {"model": ErrorResponse, "description": "Unexpected server error."},
    503: {"model": ErrorResponse, "description": "Sample review storage unavailable."},
})


def get_sample_review_repository() -> SampleReviewRepository:
    """Resolve database configuration when the request is handled."""
    return SampleReviewRepository()


Repository = Annotated[SampleReviewRepository, Depends(get_sample_review_repository)]
Identifier = Annotated[str, Path(min_length=1, pattern=r"\S")]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]
SampleRevision = Annotated[int, Query(ge=1)]


@router.get(
    "/{audit_id}/samples/{sample_id}/reviews",
    response_model=SampleReviewPage,
    operation_id="listSampleReviews",
)
def list_sample_reviews_endpoint(
    audit_id: Identifier,
    sample_id: Identifier,
    repository: Repository,
    sample_revision: SampleRevision,
    limit: Limit = 20,
    offset: Offset = 0,
) -> SampleReviewPage:
    """Read decisions for exactly one audit/sample/revision pin."""
    return repository.list_sample_reviews(
        audit_id,
        sample_id,
        sample_revision,
        limit=limit,
        offset=offset,
    )


@router.put(
    "/{audit_id}/samples/{sample_id}/review",
    response_model=SampleReview,
    operation_id="reviewSample",
)
def review_sample_endpoint(
    audit_id: Identifier,
    sample_id: Identifier,
    request: SampleReviewRequest,
    repository: Repository,
) -> SampleReview:
    """Save one attributed decision for the pinned audited sample revision."""
    return review_sample_service(
        audit_id, sample_id, request, repository=repository,
    )
