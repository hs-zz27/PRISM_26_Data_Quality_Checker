"""Consistent HTTP errors without exposing database or exception internals."""

import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from ..core.config import DatabaseConfigurationError
from ..models.audit_read import ErrorResponse
from ..models.job import ApiError, ErrorDetail
from ..storage.workflow_repository import (
    AuditNotFoundError,
    AuditSampleNotFoundError,
    FindingNotFoundError,
    FindingReviewConflictError,
    SampleRevisionConflictError,
    SampleReviewConflictError,
    SampleReviewStateError,
    WorkflowStorageError,
)


logger = logging.getLogger(__name__)


def _response(status: int, code: str, message: str, *, details: list[ErrorDetail] | None = None) -> JSONResponse:
    body = ErrorResponse(error=ApiError(
        code=code, message=message, details=details or [], request_id=uuid4().hex,
    ))
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


def register_error_handlers(app: FastAPI) -> None:
    async def not_found(request: Request, error: Exception) -> JSONResponse:
        return _response(404, "NOT_FOUND", "The requested audit, sample, or finding does not exist.")

    async def finding_review_conflict(
        request: Request, error: FindingReviewConflictError,
    ) -> JSONResponse:
        return _response(
            409,
            "VERSION_CONFLICT",
            "The finding review is out of date. Refresh before resubmitting.",
        )

    async def sample_review_conflict(
        request: Request, error: SampleReviewConflictError,
    ) -> JSONResponse:
        return _response(
            409,
            "VERSION_CONFLICT",
            "The sample review is out of date. Refresh before resubmitting.",
        )

    async def sample_revision_conflict(
        request: Request, error: SampleRevisionConflictError,
    ) -> JSONResponse:
        return _response(
            409,
            "STALE_SAMPLE_REVISION",
            "The requested sample revision is stale. Refresh before resubmitting.",
        )

    async def sample_review_state_error(
        request: Request, error: SampleReviewStateError,
    ) -> JSONResponse:
        return _response(
            409,
            "INVALID_SAMPLE_REVIEW_STATE",
            "The sample is not currently eligible for review.",
        )

    async def unavailable(request: Request, error: Exception) -> JSONResponse:
        logger.exception("Audit storage unavailable", exc_info=error)
        return _response(503, "DEPENDENCY_UNAVAILABLE", "Audit storage is unavailable.")

    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        return _response(422, "VALIDATION_ERROR", "One or more request fields are invalid.", details=[
            ErrorDetail(field=".".join(str(part) for part in item["loc"]), message=item["msg"])
            for item in error.errors()
        ])

    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        code = "NOT_FOUND" if error.status_code == 404 else "INVALID_REQUEST"
        response = _response(error.status_code, code, "The requested HTTP operation is unavailable.")
        if error.headers:
            response.headers.update(error.headers)
        return response

    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        logger.exception("Unhandled API error", exc_info=error)
        return _response(500, "INTERNAL_ERROR", "The request could not be completed.")

    app.add_exception_handler(AuditNotFoundError, not_found)
    app.add_exception_handler(AuditSampleNotFoundError, not_found)
    app.add_exception_handler(FindingNotFoundError, not_found)
    app.add_exception_handler(FindingReviewConflictError, finding_review_conflict)
    app.add_exception_handler(SampleReviewConflictError, sample_review_conflict)
    app.add_exception_handler(SampleRevisionConflictError, sample_revision_conflict)
    app.add_exception_handler(SampleReviewStateError, sample_review_state_error)
    app.add_exception_handler(WorkflowStorageError, unavailable)
    app.add_exception_handler(DatabaseConfigurationError, unavailable)
    app.add_exception_handler(RequestValidationError, invalid_request)
    app.add_exception_handler(HTTPException, http_error)
    app.add_exception_handler(Exception, unexpected_error)
