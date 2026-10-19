"""Revision-pinned sample asset delivery."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response

from ..models.audit_read import ErrorResponse
from ..services.asset_retrieval import PublicAssetKind, SampleAssetService
from ..storage.workflow_repository import SampleAssetManifestRepository


router = APIRouter(prefix="/datasets", tags=["Datasets"], responses={
    404: {"model": ErrorResponse, "description": "Sample asset not found."},
    422: {"model": ErrorResponse, "description": "Invalid asset request or unavailable preview."},
    500: {"model": ErrorResponse, "description": "Unexpected asset-serving error."},
    503: {"model": ErrorResponse, "description": "Asset storage unavailable."},
})


def get_sample_asset_service() -> SampleAssetService:
    """Construct storage dependencies only when an asset request is handled."""
    return SampleAssetService(SampleAssetManifestRepository())


AssetService = Annotated[SampleAssetService, Depends(get_sample_asset_service)]
Identifier = Annotated[str, Path(min_length=1, pattern=r"\S")]
Revision = Annotated[int, Query(ge=1)]


@router.get(
    "/{dataset_id}/samples/{sample_id}/assets/{asset_kind}",
    operation_id="getSampleAsset",
    response_class=Response,
    responses={
        200: {
            "description": "Verified raw or derived PNG bytes.",
            "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}},
        },
    },
)
def get_sample_asset(
    dataset_id: Identifier,
    sample_id: Identifier,
    asset_kind: PublicAssetKind,
    revision: Revision,
    service: AssetService,
) -> Response:
    """Return verified bytes for one explicit dataset sample revision."""
    asset = service.retrieve(dataset_id, sample_id, revision, asset_kind)
    return Response(
        content=asset.content, media_type=asset.content_type,
        headers={"X-Content-Type-Options": "nosniff"},
    )
