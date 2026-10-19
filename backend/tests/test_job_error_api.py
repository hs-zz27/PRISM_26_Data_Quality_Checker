"""HTTP error mappings reserved for the future job endpoints."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.app.models.audit_read import ErrorResponse
from backend.app.models.job import JobStatus
from backend.app.routers.errors import register_error_handlers
from backend.app.storage.workflow_repository import JobAlreadyFinishedError, JobNotFoundError


@pytest.fixture
def error_api():
    def build(error):
        app = FastAPI()
        register_error_handlers(app)

        @app.get("/test-job-error")
        def raise_job_error():
            raise error

        return app

    return build


@pytest.mark.parametrize(
    "error,status,code,message",
    [
        (
            JobNotFoundError("private-job"),
            404,
            "NOT_FOUND",
            "The requested job does not exist.",
        ),
        (
            JobAlreadyFinishedError("private-job", JobStatus.COMPLETED),
            409,
            "JOB_ALREADY_FINISHED",
            "A terminal job cannot be cancelled.",
        ),
    ],
)
def test_job_errors_use_safe_error_response_mappings(error_api, error, status, code, message):
    with TestClient(error_api(error), raise_server_exceptions=False) as client:
        response = client.get("/test-job-error")

    assert response.status_code == status
    body = response.json()
    ErrorResponse.model_validate(body)
    assert body["error"]["code"] == code
    assert body["error"]["message"] == message
    assert body["error"]["request_id"]
    assert str(error) not in response.text
    assert "private-job" not in response.text
    assert "claim" not in response.text.lower()
    assert "token" not in response.text.lower()
    assert "sql" not in response.text.lower()
    assert "filesystem" not in response.text.lower()
