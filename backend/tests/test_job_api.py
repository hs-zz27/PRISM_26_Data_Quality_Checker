"""HTTP coverage for public job status reads and audit-aware cancellation."""

from datetime import UTC, datetime
from unittest.mock import Mock, call

from fastapi.testclient import TestClient
import pytest

from backend.app.main import create_app
from backend.app.models.audit_read import ErrorResponse
from backend.app.models.job import ApiError, Job, JobKind, JobStatus, Progress, ResultReference
from backend.app.routers.jobs import get_audit_repository, get_job_repository
from backend.app.storage.workflow_repository import (
    AuditRepository,
    JobAlreadyFinishedError,
    JobNotFoundError,
    JobRepository,
    WorkflowStorageError,
)


NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)
PUBLIC_JOB_FIELDS = {
    "job_id", "kind", "status", "progress", "result", "error", "created_at", "updated_at",
}
PRIVATE_JOB_FIELDS = {
    "resource_kind", "resource_id", "claimed_by", "claim_token", "lease_until",
    "attempt", "attempt_count", "recovery_count",
}


def _job(status: JobStatus) -> Job:
    result = None
    error = None
    if status is JobStatus.COMPLETED:
        result = ResultReference(
            kind="audit", id="audit_1", href="/api/v1/audits/audit_1",
        )
    elif status is JobStatus.FAILED:
        error = ApiError(
            code="MODEL_UNAVAILABLE", message="The local model is unavailable.",
            request_id="stored-request-1",
        )
    progress = (
        Progress(phase="queued", processed=0, total=None, fraction=None)
        if status is JobStatus.QUEUED else
        Progress(
            phase=status.value, processed=1 if status is JobStatus.COMPLETED else 0,
            total=1, fraction=1.0 if status is JobStatus.COMPLETED else 0.0,
        )
    )
    return Job(
        job_id="job_1", kind=JobKind.AUDIT, status=status, progress=progress,
        result=result, error=error, created_at=NOW, updated_at=NOW,
    )


@pytest.fixture
def api():
    app = create_app()
    repository = Mock(spec=JobRepository)
    app.dependency_overrides[get_job_repository] = lambda: repository
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, repository


@pytest.fixture
def cancellation_api():
    app = create_app()
    repository = Mock(spec=AuditRepository)
    app.dependency_overrides[get_audit_repository] = lambda: repository
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, repository


@pytest.mark.parametrize("status", list(JobStatus))
def test_get_job_returns_each_persisted_lifecycle_state_as_public_data(api, status):
    client, repository = api
    repository.get_job.return_value = _job(status)

    response = client.get("/api/v1/jobs/job_1")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == PUBLIC_JOB_FIELDS
    assert set(payload) & PRIVATE_JOB_FIELDS == set()
    assert payload["job_id"] == "job_1"
    assert payload["kind"] == "audit"
    assert payload["status"] == status.value
    if status is JobStatus.QUEUED:
        assert payload["progress"]["total"] is None
    else:
        assert payload["progress"]["total"] == 1
    if status is JobStatus.COMPLETED:
        assert payload["result"] is not None
    else:
        assert payload["result"] is None
    if status is JobStatus.FAILED:
        assert payload["error"] is not None
    else:
        assert payload["error"] is None
    repository.get_job.assert_called_once_with("job_1")
    assert repository.method_calls == [call.get_job("job_1")]


def test_get_job_preserves_completed_result_and_failed_error_payloads(api):
    client, repository = api

    repository.get_job.return_value = _job(JobStatus.COMPLETED)
    completed = client.get("/api/v1/jobs/job_1")
    assert completed.json()["result"] == {
        "kind": "audit", "id": "audit_1", "version": None,
        "href": "/api/v1/audits/audit_1",
    }
    assert completed.json()["error"] is None

    repository.reset_mock()
    repository.get_job.return_value = _job(JobStatus.FAILED)
    failed = client.get("/api/v1/jobs/job_1")
    assert failed.status_code == 200
    assert failed.json()["result"] is None
    assert failed.json()["error"] == {
        "code": "MODEL_UNAVAILABLE",
        "message": "The local model is unavailable.",
        "details": [],
        "request_id": "stored-request-1",
    }


@pytest.mark.parametrize("error,status,code,message", [
    (
        JobNotFoundError("private-job"), 404, "NOT_FOUND", "The requested job does not exist.",
    ),
    (
        WorkflowStorageError("private SQL, claim token and filesystem path"),
        503, "DEPENDENCY_UNAVAILABLE", "Audit storage is unavailable.",
    ),
])
def test_get_job_errors_use_safe_existing_envelopes(api, error, status, code, message):
    client, repository = api
    repository.get_job.side_effect = error

    response = client.get("/api/v1/jobs/private-job")

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


def test_invalid_job_id_uses_validation_envelope_without_repository_access(api):
    client, repository = api

    response = client.get("/api/v1/jobs/%20")

    assert response.status_code == 422
    body = response.json()
    ErrorResponse.model_validate(body)
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["request_id"]
    repository.get_job.assert_not_called()


def test_job_openapi_limits_terminal_conflicts_to_cancellation():
    paths = create_app().openapi()["paths"]
    polling = paths["/api/v1/jobs/{job_id}"]["get"]
    cancellation = paths["/api/v1/jobs/{job_id}/cancel"]["post"]

    assert set(polling["responses"]) == {"200", "404", "422", "500", "503"}
    assert set(cancellation["responses"]) == {"202", "404", "409", "422", "500", "503"}
    assert cancellation["responses"]["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse",
    }


@pytest.mark.parametrize(
    "returned_status, expected_status",
    [
        (JobStatus.CANCELLED, 202),
        (JobStatus.CANCELLING, 202),
    ],
)
def test_cancel_job_returns_public_state_from_audit_dispatch_boundary(
    cancellation_api, returned_status, expected_status,
):
    client, repository = cancellation_api
    repository.cancel_job.return_value = _job(returned_status)

    response = client.post("/api/v1/jobs/job_1/cancel")

    assert response.status_code == expected_status
    payload = response.json()
    assert set(payload) == PUBLIC_JOB_FIELDS
    assert payload["status"] == returned_status.value
    repository.cancel_job.assert_called_once_with("job_1")
    assert repository.method_calls == [call.cancel_job("job_1")]


@pytest.mark.parametrize("status", [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED])
def test_cancel_job_maps_terminal_conflicts_safely(cancellation_api, status):
    client, repository = cancellation_api
    error = JobAlreadyFinishedError("private-job", status)
    repository.cancel_job.side_effect = error

    response = client.post("/api/v1/jobs/private-job/cancel")

    assert response.status_code == 409
    body = response.json()
    ErrorResponse.model_validate(body)
    assert body["error"]["code"] == "JOB_ALREADY_FINISHED"
    assert body["error"]["message"] == "A terminal job cannot be cancelled."
    assert body["error"]["request_id"]
    assert str(error) not in response.text
    assert "private-job" not in response.text


def test_cancel_job_maps_missing_job_safely(cancellation_api):
    client, repository = cancellation_api
    error = JobNotFoundError("private-job")
    repository.cancel_job.side_effect = error

    response = client.post("/api/v1/jobs/private-job/cancel")

    assert response.status_code == 404
    body = response.json()
    ErrorResponse.model_validate(body)
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["message"] == "The requested job does not exist."
    assert body["error"]["request_id"]
    assert str(error) not in response.text
    assert "private-job" not in response.text
