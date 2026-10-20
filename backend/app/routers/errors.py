"""Consistent HTTP errors without exposing database or exception internals."""

import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from ..core.config import AssetStorageConfigurationError, AuditConfigurationError, DatabaseConfigurationError
from ..models.audit_read import ErrorResponse
from ..core.dataset_http import DatasetHttpDependencyError, DatasetRequestError, DatasetUnsupportedMediaError
from ..core.dataset_import import (
    ImportAcceptanceError, ImportConfigurationError, ImportOutcomeUnresolved, UploadTooLargeError,
)
from ..storage.dataset_repository import (
    DatasetNotFoundError, DatasetNotReadyError, DatasetRevisionNotFoundError, DatasetSampleNotFoundError, DatasetStorageError,
)
from ..storage.extraction_repository import ExtractionSubmissionError
from ..models.job import ApiError, ErrorDetail
from ..services.asset_preview import PreviewUnavailableError
from ..services.audit_submission import AuditRuleParametersError
from ..services.batch_audit import RulebookNotConfirmedError
from ..services.review import RulebookContentValidationError, RulebookReviewError
from ..storage.repository import (
    RulebookNotFoundError,
    RulebookStorageError,
    RulebookVersionConflictError,
)
from ..storage.workflow_repository import (
    AuditNotFoundError,
    AuditSampleNotFoundError,
    AssetReadLimitError,
    AssetUnavailableError,
    FindingNotFoundError,
    FindingReviewConflictError,
    JobAlreadyFinishedError,
    JobNotFoundError,
    SampleAssetManifestNotFoundError,
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
    async def dataset_not_ready(request: Request, error: DatasetNotReadyError) -> JSONResponse:
        return _response(409, "DATASET_NOT_READY", "Wait for a ready dataset before starting an audit.")

    async def rulebook_not_confirmed(request: Request, error: RulebookNotConfirmedError) -> JSONResponse:
        return _response(409, "RULEBOOK_NOT_CONFIRMED", "Confirm the selected rulebook version and all its rules before starting an audit.")

    async def audit_parameters_invalid(request: Request, error: AuditRuleParametersError) -> JSONResponse:
        return _response(422, "VALIDATION_ERROR", "The selected rulebook contains invalid check parameters. Revise and confirm the rulebook before auditing.")

    async def dataset_not_found(request: Request, error: Exception) -> JSONResponse:
        return _response(404, "NOT_FOUND", "The requested dataset, sample or exact revision does not exist.")

    async def dataset_request_invalid(request: Request, error: DatasetRequestError) -> JSONResponse:
        return _response(422, "VALIDATION_ERROR", "One or more dataset request fields are invalid.",
                         details=[] if error.field is None else [ErrorDetail(
                             field=error.field, message="The dataset request field is invalid.")])

    async def dataset_upload_large(request: Request, error: UploadTooLargeError) -> JSONResponse:
        return _response(413, "UPLOAD_TOO_LARGE", "The dataset upload exceeds the configured byte limit.")

    async def dataset_media_invalid(request: Request, error: DatasetUnsupportedMediaError) -> JSONResponse:
        return _response(415, "UNSUPPORTED_MEDIA_TYPE", "Upload a supported multipart dataset ZIP archive.")

    async def dataset_unavailable(request: Request, error: Exception) -> JSONResponse:
        # No exception-chain logging: source keys/configuration/connection strings
        # must not leak. Unknown acceptance commits require operator reconciliation.
        logger.warning("Dataset storage or durable acceptance unavailable")
        return _response(503, "DEPENDENCY_UNAVAILABLE", "Dataset storage or durable acceptance is unavailable.")

    async def not_found(request: Request, error: Exception) -> JSONResponse:
        return _response(404, "NOT_FOUND", "The requested audit, sample, or finding does not exist.")

    async def asset_not_found(request: Request, error: Exception) -> JSONResponse:
        return _response(404, "NOT_FOUND", "The requested sample asset does not exist.")

    async def asset_not_renderable(request: Request, error: PreviewUnavailableError) -> JSONResponse:
        return _response(422, "ASSET_NOT_RENDERABLE", "The requested sample asset cannot be rendered.")

    async def asset_too_large(request: Request, error: AssetReadLimitError) -> JSONResponse:
        return _response(422, "ASSET_TOO_LARGE", "The requested sample asset exceeds the read limit.")

    async def job_not_found(request: Request, error: JobNotFoundError) -> JSONResponse:
        return _response(404, "NOT_FOUND", "The requested job does not exist.")

    async def job_already_finished(request: Request, error: JobAlreadyFinishedError) -> JSONResponse:
        return _response(409, "JOB_ALREADY_FINISHED", "A terminal job cannot be cancelled.")

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

    async def rulebook_not_found(
        request: Request, error: RulebookNotFoundError,
    ) -> JSONResponse:
        return _response(404, "NOT_FOUND", "The requested rulebook or version does not exist.")

    async def rulebook_version_conflict(
        request: Request, error: RulebookVersionConflictError,
    ) -> JSONResponse:
        return _response(
            409,
            "VERSION_CONFLICT",
            "The rulebook is out of date. Refresh before resubmitting.",
        )

    async def rulebook_unavailable(
        request: Request, error: RulebookStorageError,
    ) -> JSONResponse:
        logger.exception("Rulebook storage unavailable", exc_info=error)
        return _response(503, "DEPENDENCY_UNAVAILABLE", "Rulebook storage is unavailable.")

    async def rulebook_content_invalid(
        request: Request, error: RulebookContentValidationError,
    ) -> JSONResponse:
        return _response(
            422,
            "VALIDATION_ERROR",
            "Rule IDs must be unique and citations must match the submitted guidelines.",
        )

    async def rulebook_review_error(
        request: Request, error: RulebookReviewError,
    ) -> JSONResponse:
        if error.reason == "invalid_rulebook_state":
            return _response(
                409,
                "INVALID_RULEBOOK_STATE",
                "This action is not allowed in the current rulebook state.",
            )
        if error.reason == "rulebook_not_ready":
            return _response(
                409,
                "RULEBOOK_NOT_READY",
                "Confirm every retained rule and remove rejected rules before confirming the book.",
            )
        if error.reason == "invalid_rule_origin":
            return _response(
                422,
                "VALIDATION_ERROR",
                "Retained rules must preserve origin; new rules must use reviewer origin.",
            )
        # Unknown reasons are internal defects, not implicitly client mistakes.
        return await unexpected_error(request, error)

    async def unavailable(request: Request, error: Exception) -> JSONResponse:
        logger.exception("Audit storage unavailable", exc_info=error)
        return _response(503, "DEPENDENCY_UNAVAILABLE", "Audit storage is unavailable.")

    async def extraction_unavailable(request: Request, error: ExtractionSubmissionError) -> JSONResponse:
        # No exception-chain logging: DB/model configuration may include private inputs.
        logger.warning("Extraction submission unavailable")
        return _response(503, "DEPENDENCY_UNAVAILABLE", "Extraction could not be durably accepted.")

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

    app.add_exception_handler(DatasetNotReadyError, dataset_not_ready)
    app.add_exception_handler(RulebookNotConfirmedError, rulebook_not_confirmed)
    app.add_exception_handler(AuditRuleParametersError, audit_parameters_invalid)
    app.add_exception_handler(DatasetNotFoundError, dataset_not_found)
    app.add_exception_handler(DatasetSampleNotFoundError, dataset_not_found)
    app.add_exception_handler(DatasetRevisionNotFoundError, dataset_not_found)
    app.add_exception_handler(DatasetRequestError, dataset_request_invalid)
    app.add_exception_handler(UploadTooLargeError, dataset_upload_large)
    app.add_exception_handler(DatasetUnsupportedMediaError, dataset_media_invalid)
    app.add_exception_handler(DatasetStorageError, dataset_unavailable)
    app.add_exception_handler(DatasetHttpDependencyError, dataset_unavailable)
    app.add_exception_handler(ImportConfigurationError, dataset_unavailable)
    app.add_exception_handler(ImportAcceptanceError, dataset_unavailable)
    app.add_exception_handler(ImportOutcomeUnresolved, dataset_unavailable)
    app.add_exception_handler(ExtractionSubmissionError, extraction_unavailable)
    app.add_exception_handler(AuditNotFoundError, not_found)
    app.add_exception_handler(AuditSampleNotFoundError, not_found)
    app.add_exception_handler(FindingNotFoundError, not_found)
    app.add_exception_handler(SampleAssetManifestNotFoundError, asset_not_found)
    app.add_exception_handler(AssetUnavailableError, asset_not_found)
    app.add_exception_handler(PreviewUnavailableError, asset_not_renderable)
    app.add_exception_handler(AssetReadLimitError, asset_too_large)
    app.add_exception_handler(JobNotFoundError, job_not_found)
    app.add_exception_handler(JobAlreadyFinishedError, job_already_finished)
    app.add_exception_handler(FindingReviewConflictError, finding_review_conflict)
    app.add_exception_handler(SampleReviewConflictError, sample_review_conflict)
    app.add_exception_handler(SampleRevisionConflictError, sample_revision_conflict)
    app.add_exception_handler(SampleReviewStateError, sample_review_state_error)
    app.add_exception_handler(RulebookNotFoundError, rulebook_not_found)
    app.add_exception_handler(RulebookVersionConflictError, rulebook_version_conflict)
    app.add_exception_handler(RulebookStorageError, rulebook_unavailable)
    app.add_exception_handler(RulebookReviewError, rulebook_review_error)
    app.add_exception_handler(RulebookContentValidationError, rulebook_content_invalid)
    app.add_exception_handler(WorkflowStorageError, unavailable)
    app.add_exception_handler(DatabaseConfigurationError, unavailable)
    app.add_exception_handler(AssetStorageConfigurationError, unavailable)
    app.add_exception_handler(AuditConfigurationError, unavailable)
    app.add_exception_handler(RequestValidationError, invalid_request)
    app.add_exception_handler(HTTPException, http_error)
    app.add_exception_handler(Exception, unexpected_error)
