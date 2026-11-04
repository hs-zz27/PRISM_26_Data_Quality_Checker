"""Public audit creation/listing validation and safe transport boundaries."""

import json
from unittest.mock import Mock

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
import pytest

from backend.api.build_contract import build_contract
from backend.api.dataset_contract import schema_closure, structural
from backend.api.fixtures import build_examples
from backend.app.main import create_app
from backend.tests.display_name_helpers import without_display_names
from backend.app.models.audit import AuditRun
from backend.app.models.audit_submission import AuditPage, AuditRequest, AuditStarted
from backend.app.models.job import Job, JobKind
from backend.app.routers.audits import get_audit_repository, get_audit_submission_service
from backend.app.services.audit_submission import AuditRuleParametersError, AuditSubmissionService
from backend.app.services.batch_audit import RulebookNotConfirmedError
from backend.app.storage.dataset_repository import DatasetNotFoundError, DatasetNotReadyError, DatasetSampleNotFoundError
from backend.app.storage.repository import RulebookNotFoundError
from backend.app.storage.workflow_repository import AuditRepository, RetryableWorkflowStorageError


@pytest.fixture
def api():
    app = without_display_names(create_app())
    service = Mock(spec=AuditSubmissionService)
    repository = Mock(spec=AuditRepository)
    audit = AuditRun.model_validate_json(json.dumps({
        **build_examples()["audit_run"], "status": "queued", "processed_count": 0,
    }))
    job = Job.queued(job_id=audit.job_id, kind=JobKind.AUDIT, total=audit.sample_count, now=audit.created_at)
    service.start.return_value = AuditStarted(audit=audit, job=job)
    repository.list_audits.return_value = AuditPage(items=[audit], limit=20, offset=0, total=1, next_offset=None)
    app.dependency_overrides[get_audit_submission_service] = lambda: service
    app.dependency_overrides[get_audit_repository] = lambda: repository
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, service, repository


BODY = {"dataset_id": "dataset_demo", "rulebook_id": "rulebook_demo", "rulebook_version": 1}


@pytest.mark.parametrize("sample_ids", [None, ["sample_b", "sample_a"]])
def test_start_returns_202_queued_public_resources_and_forwards_selection(api, sample_ids):
    client, service, _ = api
    response = client.post("/api/v1/audits", json=BODY | {"sample_ids": sample_ids})
    assert response.status_code == 202
    service.start.assert_called_once_with(AuditRequest(**BODY, sample_ids=sample_ids))
    body = response.json()
    assert body["audit"]["job_id"] == body["job"]["job_id"]
    assert body["audit"]["source_mode"] == "snapshot"
    assert body["job"]["status"] == body["audit"]["status"] == "queued"
    assert body["job"]["result"] is None
    assert body["parent_audit_id"] is None and body["feedback_policy_id"] is None
    for name in ("dataset_root", "claim_token", "claimed_by", "storage_key", "sample_revisions"):
        assert name not in response.text
    spec = build_contract()
    Draft202012Validator({"components": spec["components"], "$ref": "#/components/schemas/AuditStarted"}).validate(body)


@pytest.mark.parametrize("changes", [
    {"sample_ids": []}, {"sample_ids": ["a", "a"]}, {"sample_ids": ["a\x00b"]},
    {"rulebook_version": True}, {"rulebook_version": "1"}, {"rulebook_version": 0},
    {"dataset_root": "/private"}, {"source_mode": "legacy"}, {"feedback_policy_id": " "},
])
def test_bad_requests_are_422_and_never_submitted(api, changes):
    client, service, _ = api
    response = client.post("/api/v1/audits", json=BODY | changes)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["request_id"]
    service.start.assert_not_called()


@pytest.mark.parametrize("error,status,code", [
    (DatasetNotFoundError("private"), 404, "NOT_FOUND"),
    (DatasetSampleNotFoundError("private", "private"), 404, "NOT_FOUND"),
    (RulebookNotFoundError("private", 99), 404, "NOT_FOUND"),
    (DatasetNotReadyError("private"), 409, "DATASET_NOT_READY"),
    (RulebookNotConfirmedError("private"), 409, "RULEBOOK_NOT_CONFIRMED"),
    (AuditRuleParametersError("private"), 422, "VALIDATION_ERROR"),
    (RetryableWorkflowStorageError("private", commit_outcome_unknown=True), 503, "DEPENDENCY_UNAVAILABLE"),
    (RuntimeError("private"), 500, "INTERNAL_ERROR"),
])
def test_submission_errors_are_safe_and_unknown_commits_are_not_retried(api, error, status, code):
    client, service, _ = api
    service.start.side_effect = error
    response = client.post("/api/v1/audits", json=BODY)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["request_id"]
    assert "private" not in response.text
    service.start.assert_called_once()


def test_list_returns_public_page_without_starting_work(api):
    client, service, repository = api
    response = client.get("/api/v1/audits")
    assert response.status_code == 200
    repository.list_audits.assert_called_once_with(limit=20, offset=0)
    assert response.json()["total"] == 1
    assert "dataset_root" not in response.text
    service.start.assert_not_called()


def test_list_can_hide_linked_recheck_attempts(api):
    client, service, repository = api
    response = client.get("/api/v1/audits?roots_only=true")
    assert response.status_code == 200
    repository.list_audits.assert_called_once_with(limit=20, offset=0, roots_only=True)
    service.start.assert_not_called()


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "offset=9223372036854775808"])
def test_list_rejects_invalid_pagination(api, query):
    client, _, repository = api
    assert client.get("/api/v1/audits?" + query).status_code == 422
    repository.list_audits.assert_not_called()


def test_creation_and_listing_contracts_match_live_transports():
    contract = build_contract()
    live = create_app().openapi()
    for method, statuses in (("post", {"202", "404", "409", "422", "500", "503"}),
                             ("get", {"200", "422", "500", "503"})):
        documented = contract["paths"]["/audits"][method]
        actual = live["paths"]["/api/v1/audits"][method]
        assert documented["x-implementation-status"] == "implemented"
        assert set(documented["responses"]) == set(actual["responses"]) == statuses
        for key in ("parameters", "requestBody"):
            assert structural(documented.get(key, [])) == structural(actual.get(key, []))
        # Compare response schemas without comparing descriptive prose/examples.
        for status in statuses:
            assert documented["responses"][status]["content"]["application/json"]["schema"] == actual["responses"][status]["content"]["application/json"]["schema"]
        generated = schema_closure(documented, contract["components"]["schemas"])
        runtime = schema_closure(actual, live["components"]["schemas"])
        assert structural(generated, field_map=True) == structural(runtime, field_map=True)
