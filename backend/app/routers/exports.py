"""Thin export HTTP boundary. Request-time dependencies; explicit workers only."""
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import Response

from ..core.export import (
    AuditNotExportable, AuditNotReady, ExportDependencyError, ExportNotFound,
    ExportNotReady, ExportRequestError, ExportSettings, load_export_settings,
)
from ..models.audit_read import ErrorResponse
from ..models.export import Export, ExportManifest, ExportPage, ExportRequest, ExportStarted, Identifier
from ..services.export_download import ExportDownloadService
from ..storage.export_repository import ExportRepository
from .errors import _response

router = APIRouter(prefix='/exports', tags=['Exports'], responses={
    422: {'model': ErrorResponse, 'description': 'Invalid export request fields.'},
    500: {'model': ErrorResponse, 'description': 'Unexpected export error.'},
    503: {'model': ErrorResponse, 'description': 'Export storage, configuration or durable outcome unavailable.'},
})
_NOT_FOUND = {404: {'model': ErrorResponse, 'description': 'Export or source audit not found.'}}
_NOT_READY = {409: {'model': ErrorResponse, 'description': 'Export has not completed.'}}


def register_export_error_handlers(app):
    # Deliberately narrow categories; do not map all ValueErrors or internal
    # Pydantic errors to 422, and do not replace existing workflow handlers.
    def handler(status, code, message):
        async def respond(request, error):
            return _response(status, code, message)
        return respond
    for category, status, code, message in (
        (ExportNotFound, 404, 'NOT_FOUND', 'The requested export does not exist.'),
        (AuditNotReady, 409, 'AUDIT_NOT_READY', 'Wait for a terminal audit before requesting an export.'),
        (AuditNotExportable, 409, 'AUDIT_NOT_EXPORTABLE', 'The source audit has no reproducible immutable snapshot.'),
        (ExportNotReady, 409, 'EXPORT_NOT_READY', 'The export has not completed; inspect its resource and job state.'),
        (ExportDependencyError, 503, 'DEPENDENCY_UNAVAILABLE', 'Export storage or durable acceptance is unavailable; unresolved outcomes require reconciliation.'),
        (ExportRequestError, 422, 'VALIDATION_ERROR', 'One or more export request fields are invalid.'),
    ):
        app.add_exception_handler(category, handler(status, code, message))


def get_export_repository():
    return ExportRepository()


def get_export_settings():
    return load_export_settings()


Repository = Annotated[ExportRepository, Depends(get_export_repository)]
Settings = Annotated[ExportSettings, Depends(get_export_settings)]
ExportId = Annotated[Identifier, Path()]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=9_223_372_036_854_775_807)]


def get_export_download_service(repository: Repository, settings: Settings):
    return ExportDownloadService(repository, settings)


Downloads = Annotated[ExportDownloadService, Depends(get_export_download_service)]


def _query(request, allowed):
    seen = set()
    for key, _ in request.query_params.multi_items():
        if key not in allowed or key in seen:
            raise ExportRequestError()
        seen.add(key)


@router.post('', response_model=ExportStarted, status_code=202, operation_id='startExport',
             responses={**_NOT_FOUND, 409: {'model': ErrorResponse,
                        'description': 'AUDIT_NOT_READY or AUDIT_NOT_EXPORTABLE.'}})
def start_export(body: ExportRequest, request: Request, repository: Repository, settings: Settings):
    _query(request, set())
    return repository.enqueue(body, policy=settings.policy)


@router.get('', response_model=ExportPage, operation_id='listExports')
def list_exports(request: Request, repository: Repository, limit: Limit = 20, offset: Offset = 0):
    _query(request, {'limit', 'offset'})
    return repository.list_exports(limit=limit, offset=offset)


@router.get('/{export_id}', response_model=Export, operation_id='getExport', responses=_NOT_FOUND)
def get_export(export_id: ExportId, request: Request, repository: Repository):
    _query(request, set())
    return repository.get_export(export_id)


@router.get('/{export_id}/manifest', response_model=ExportManifest, operation_id='getExportManifest',
            responses={**_NOT_FOUND, **_NOT_READY})
def get_export_manifest(export_id: ExportId, request: Request, service: Downloads):
    _query(request, set())
    return service.get_manifest(export_id)


@router.get('/{export_id}/download', response_class=Response, operation_id='downloadExport',
            responses={**_NOT_FOUND, **_NOT_READY, 200: {
                'description': 'Reviewed dataset ZIP; errors before headers remain JSON.',
                'content': {'application/zip': {'schema': {'type': 'string', 'format': 'binary'}}},
                'headers': {
                    'Content-Disposition': {'schema': {'type': 'string'}, 'description': 'Constant-safe attachment filename.'},
                    'X-Content-Type-Options': {'schema': {'type': 'string', 'const': 'nosniff'}},
                    'Content-Length': {'schema': {'type': 'string'}, 'description': 'Registered verified artifact byte length.'},
                },
            }})
def download_export(export_id: ExportId, request: Request, service: Downloads):
    _query(request, set())
    return service.download(export_id)
