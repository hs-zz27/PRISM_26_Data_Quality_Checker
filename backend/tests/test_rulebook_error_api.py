"""Rulebook HTTP error boundaries before rulebook routes are introduced."""

from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from backend.app.models.audit_read import ErrorResponse
from backend.app.models.review import RulebookAction
from backend.app.routers import errors as error_handlers
from backend.app.routers.errors import register_error_handlers
from backend.app.services.review import RulebookReviewError
from backend.app.storage.repository import (
    RulebookNotFoundError,
    RulebookStorageError,
    RulebookVersionConflictError,
)

PRIVATE_DIAGNOSTIC = "private SQL SELECT secret FROM /private/rulebooks; connection token"
INTERNAL_MESSAGE = "The request could not be completed."

@pytest.fixture
def error_api():
    def build(error):
        app = FastAPI()
        register_error_handlers(app)

        @app.get("/test-rulebook-error")
        def raise_rulebook_error():
            raise error

        return app

    return build

def assert_error(response, status, code, message, *, details=None):
    assert response.status_code == status
    body = response.json()
    ErrorResponse.model_validate(body)
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details", "request_id"}
    assert body["error"]["code"] == code
    assert body["error"]["message"] == message
    if details is None:
        assert body["error"]["details"] == []
    else:
        assert body["error"]["details"] == details
    request_id = body["error"]["request_id"]
    assert UUID(hex=request_id).hex == request_id
    assert len(request_id) == 32
    for diagnostic in ("private", "sql", "select", "secret", "connection", "token", "traceback"):
        assert diagnostic not in response.text.lower()
    return body["error"]

@pytest.mark.parametrize(
    "error,status,code,message",
    [
        (
            RulebookNotFoundError("private-rulebook", None),
            404, "NOT_FOUND", "The requested rulebook or version does not exist.",
        ),
        (
            RulebookNotFoundError("private-rulebook", 999),
            404, "NOT_FOUND", "The requested rulebook or version does not exist.",
        ),
        (
            RulebookVersionConflictError("private-rulebook", 1, 2),
            409, "VERSION_CONFLICT", "The rulebook is out of date. Refresh before resubmitting.",
        ),
        (
            RulebookStorageError(PRIVATE_DIAGNOSTIC),
            503, "DEPENDENCY_UNAVAILABLE", "Rulebook storage is unavailable.",
        ),
        (
            RulebookReviewError("invalid_rulebook_state", PRIVATE_DIAGNOSTIC),
            409, "INVALID_RULEBOOK_STATE",
            "This action is not allowed in the current rulebook state.",
        ),
        (
            RulebookReviewError("rulebook_not_ready", PRIVATE_DIAGNOSTIC),
            409, "RULEBOOK_NOT_READY",
            "Confirm every retained rule and remove rejected rules before confirming the book.",
        ),
        (
            RulebookReviewError("invalid_rule_origin", PRIVATE_DIAGNOSTIC),
            422, "VALIDATION_ERROR",
            "Retained rules must preserve origin; new rules must use reviewer origin.",
        ),
    ],
)
def test_rulebook_errors_use_safe_existing_envelope(error_api, error, status, code, message):
    with TestClient(error_api(error), raise_server_exceptions=False) as client:
        first = client.get("/test-rulebook-error")
        second = client.get("/test-rulebook-error")

    first_error = assert_error(first, status, code, message)
    second_error = assert_error(second, status, code, message)
    assert first_error["request_id"] != second_error["request_id"]
    assert str(error) not in first.text

@pytest.mark.parametrize("reason", ["future_policy", "", "private-unknown-reason"])
def test_unknown_review_reasons_are_safe_internal_errors(error_api, reason):
    error = RulebookReviewError(reason, PRIVATE_DIAGNOSTIC)
    with TestClient(error_api(error), raise_server_exceptions=False) as client:
        response = client.get("/test-rulebook-error")

    assert_error(response, 500, "INTERNAL_ERROR", INTERNAL_MESSAGE)

@pytest.mark.parametrize("error_type", [ValueError, RuntimeError, TypeError])
def test_programming_errors_are_not_mapped_to_client_errors(error_api, error_type):
    with TestClient(error_api(error_type(PRIVATE_DIAGNOSTIC)), raise_server_exceptions=False) as client:
        response = client.get("/test-rulebook-error")

    assert_error(response, 500, "INTERNAL_ERROR", INTERNAL_MESSAGE)

def test_internal_pydantic_validation_error_remains_500(error_api):
    with pytest.raises(ValidationError) as caught:
        RulebookAction(expected_version=0, reviewer_id="private-reviewer")

    with TestClient(error_api(caught.value), raise_server_exceptions=False) as client:
        response = client.get("/test-rulebook-error")

    assert_error(response, 500, "INTERNAL_ERROR", INTERNAL_MESSAGE)

def test_response_validation_error_remains_500():
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/test-response-error", response_model=RulebookAction)
    def invalid_response():
        return {"expected_version": 0, "reviewer_id": "private-reviewer"}

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test-response-error")

    assert_error(response, 500, "INTERNAL_ERROR", INTERNAL_MESSAGE)

@pytest.mark.parametrize(
    "payload,field",
    [
        ({"expected_version": True, "reviewer_id": "reviewer_1"}, "expected_version"),
        ({"expected_version": "1", "reviewer_id": "reviewer_1"}, "expected_version"),
        ({"expected_version": 0, "reviewer_id": "reviewer_1"}, "expected_version"),
        ({"expected_version": 1, "reviewer_id": " "}, "reviewer_id"),
        ({"expected_version": 1, "reviewer_id": "reviewer_1", "extra": 1}, "extra"),
    ],
)
def test_http_request_validation_keeps_existing_422_envelope(payload, field):
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/test-request-validation")
    def validate_request(request: RulebookAction):
        return request

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/test-request-validation", json=payload)

    body = response.json()
    details = body["error"]["details"]
    assert details
    assert any(item["field"] == f"body.{field}" for item in details)
    assert_error(
        response, 422, "VALIDATION_ERROR", "One or more request fields are invalid.",
        details=details,
    )

@pytest.mark.parametrize(
    "error_name,handler_name",
    [
        ("AuditNotFoundError", "not_found"),
        ("AuditSampleNotFoundError", "not_found"),
        ("FindingNotFoundError", "not_found"),
        ("SampleAssetManifestNotFoundError", "asset_not_found"),
        ("AssetUnavailableError", "asset_not_found"),
        ("PreviewUnavailableError", "asset_not_renderable"),
        ("AssetReadLimitError", "asset_too_large"),
        ("JobNotFoundError", "job_not_found"),
        ("JobAlreadyFinishedError", "job_already_finished"),
        ("FindingReviewConflictError", "finding_review_conflict"),
        ("SampleReviewConflictError", "sample_review_conflict"),
        ("SampleRevisionConflictError", "sample_revision_conflict"),
        ("SampleReviewStateError", "sample_review_state_error"),
        ("WorkflowStorageError", "unavailable"),
        ("DatabaseConfigurationError", "unavailable"),
        ("AssetStorageConfigurationError", "unavailable"),
        ("RequestValidationError", "invalid_request"),
        ("HTTPException", "http_error"),
    ],
)
def test_existing_exception_handlers_remain_registered(error_name, handler_name):
    app = FastAPI()
    register_error_handlers(app)
    assert app.exception_handlers[getattr(error_handlers, error_name)].__name__ == handler_name
    assert Exception in app.exception_handlers
    assert ValueError not in app.exception_handlers
    assert ValidationError not in app.exception_handlers

def test_rulebook_handlers_do_not_register_routes():
    app = FastAPI()
    original_routes = list(app.routes)
    register_error_handlers(app)
    assert app.routes == original_routes
    for error_type in (
        RulebookNotFoundError, RulebookVersionConflictError,
        RulebookStorageError, RulebookReviewError,
    ):
        assert error_type in app.exception_handlers
