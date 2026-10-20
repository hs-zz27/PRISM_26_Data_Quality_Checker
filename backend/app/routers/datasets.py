"""Exactly five thin dataset operations. No SQL, archive decoding or worker startup."""
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import AfterValidator, TypeAdapter
from starlette.concurrency import run_in_threadpool

from ..core.dataset_http import DatasetHttpDependencyError, DatasetRequestError, DatasetUpload
from ..core.dataset_import import ImportSettings
from ..models.audit_read import ErrorResponse
from ..models.dataset import Dataset, DatasetPage, DatasetImportStarted, Identifier as ModelIdentifier, Sample, SamplePage
from ..services.dataset_http import DatasetUploadService
from ..storage.dataset_import_repository import DatasetImportRepository
from ..storage.dataset_repository import DatasetRepository
from .dataset_ingress import SETTINGS_SCOPE_KEY
from .dataset_multipart import parsed_dataset_upload

router = APIRouter(prefix="/datasets", tags=["Datasets"], responses={
    422: {"model": ErrorResponse, "description": "Invalid dataset request fields."},
    500: {"model": ErrorResponse, "description": "Unexpected dataset error."},
    503: {"model": ErrorResponse, "description": "Dataset storage or durable acceptance unavailable."},
})
_NOT_FOUND = {404: {"model": ErrorResponse, "description": "Dataset, sample or exact revision not found."}}


def get_dataset_repository() -> DatasetRepository:
    """Request-time construction; configuration only, never connect or migrate."""
    return DatasetRepository()


def get_dataset_import_repository() -> DatasetImportRepository:
    return DatasetImportRepository()


def get_import_settings(request: Request) -> ImportSettings:
    effective = request.scope.get(SETTINGS_SCOPE_KEY)
    if effective is None:
        raise DatasetHttpDependencyError("The scoped upload ingress guard is required.")
    return ImportSettings.model_validate(effective.imports.model_dump())


def get_dataset_upload_service(
    repository: Annotated[DatasetImportRepository, Depends(get_dataset_import_repository)],
    settings: Annotated[ImportSettings, Depends(get_import_settings)],
) -> DatasetUploadService:
    return DatasetUploadService(repository, settings)


def _identifier(value):
    return TypeAdapter(ModelIdentifier).validate_python(value, strict=True)


Identifier = Annotated[str, Path(min_length=1, max_length=255, pattern=r"\S"), AfterValidator(_identifier)]
Repository = Annotated[DatasetRepository, Depends(get_dataset_repository)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=9223372036854775807)]
Revision = Annotated[int | None, Query(ge=1, le=2147483647)]


def _query(request, allowed):
    seen = set()
    for key, _ in request.query_params.multi_items():
        if key not in allowed or key in seen:
            raise DatasetRequestError("query")
        seen.add(key)


@router.post("", response_model=DatasetImportStarted, status_code=202, operation_id="importDataset",
             responses={413: {"model": ErrorResponse, "description": "Upload byte budget exceeded."},
                        415: {"model": ErrorResponse, "description": "Unsupported upload representation."}},
             openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {
                 "schema": DatasetUpload.model_json_schema(),
             }}}})
async def import_dataset(
    request: Request,
    service: Annotated[DatasetUploadService, Depends(get_dataset_upload_service)],
    settings: Annotated[ImportSettings, Depends(get_import_settings)],
) -> DatasetImportStarted:
    """Acknowledge durable acceptance only. Explicit worker owns layout inspection."""
    _query(request, set())
    effective = request.scope.get(SETTINGS_SCOPE_KEY)
    if effective is None:
        raise DatasetHttpDependencyError("Upload ingress is not configured.")
    # Dependency and ingress policies must not diverge via an unsafe override.
    if settings != effective.imports:
        raise DatasetHttpDependencyError("Upload ingress and staging policies disagree.")
    async with parsed_dataset_upload(request, effective) as fields:
        return await run_in_threadpool(service.submit, fields.archive.file,
                                       name=fields.name, format=fields.format)


@router.get("", response_model=DatasetPage, operation_id="listDatasets")
def list_datasets(request: Request, repository: Repository, limit: Limit = 20, offset: Offset = 0) -> DatasetPage:
    _query(request, {"limit", "offset"})
    return repository.list_datasets(limit=limit, offset=offset)


@router.get("/{dataset_id}", response_model=Dataset, operation_id="getDataset", responses=_NOT_FOUND)
def get_dataset(dataset_id: Identifier, request: Request, repository: Repository) -> Dataset:
    _query(request, set())
    return repository.get_dataset(dataset_id)


@router.get("/{dataset_id}/samples", response_model=SamplePage, operation_id="listDatasetSamples", responses=_NOT_FOUND)
def list_dataset_samples(dataset_id: Identifier, request: Request, repository: Repository,
                         limit: Limit = 20, offset: Offset = 0) -> SamplePage:
    _query(request, {"limit", "offset"})
    return repository.list_samples(dataset_id, limit=limit, offset=offset)


@router.get("/{dataset_id}/samples/{sample_id}", response_model=Sample, operation_id="getDatasetSample", responses=_NOT_FOUND)
def get_dataset_sample(dataset_id: Identifier, sample_id: Identifier, request: Request,
                       repository: Repository, revision: Revision = None) -> Sample:
    _query(request, {"revision"})
    return repository.get_sample(dataset_id, sample_id, revision=revision)
