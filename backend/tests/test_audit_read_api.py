"""HTTP validation, response contracts and safe error handling for audit reads."""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, call

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
import pytest

from backend.app.core.config import DatabaseConfigurationError
from backend.app.main import create_app
from backend.app.models.audit import AuditRun
from backend.app.models.audit_read import AuditSamplePage, SampleAudit
from backend.app.models.job import JobStatus
from backend.app.models.finding import FindingReview
from backend.app.models.review import (
    FindingReviewPage,
    FindingReviewRecord,
    FindingReviewRequest,
    SampleReview,
    SampleReviewPage,
    SampleReviewRecord,
    SampleReviewRequest,
)
from backend.app.routers.audits import get_audit_read_repository, get_audit_repository
from backend.app.routers.finding_reviews import get_finding_repository
from backend.app.routers.sample_reviews import get_sample_review_repository
from backend.app.storage.audit_read_repository import AuditReadRepository
from backend.app.storage.workflow_repository import (
    AuditNotFoundError,
    AuditSampleNotFoundError,
    FindingRepository,
    FindingNotFoundError,
    FindingReviewConflictError,
    SampleRevisionConflictError,
    SampleReviewConflictError,
    SampleReviewStateError,
    SampleReviewRepository,
    WorkflowStorageError,
)

@pytest.fixture
def api():
    app = create_app()
    repository = Mock(spec=AuditReadRepository)
    repository.get_audit = Mock()
    finding_repository = Mock(spec=FindingRepository)
    sample_repository = Mock(spec=SampleReviewRepository)
    app.dependency_overrides[get_audit_read_repository] = lambda: repository
    app.dependency_overrides[get_audit_repository] = lambda: repository
    app.dependency_overrides[get_finding_repository] = lambda: finding_repository
    app.dependency_overrides[get_sample_review_repository] = lambda: sample_repository
    repository.finding_repository = finding_repository
    repository.sample_review_repository = sample_repository
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, repository

@pytest.fixture
def detail():
    return SampleAudit.model_validate({
        "audit_id": "audit_1", "dataset_id": "dataset_1",
        "summary": {
            "sample_id": "sample_1", "sample_revision": 2,
            "processing_status": "queued", "incomplete": False,
            "tool_status": "pending", "review_status": "pending_review",
            "finding_counts": {"pass": 0, "fail": 0, "unsure": 0, "unavailable": 0},
        },
        "measurements": None, "findings": [], "failures": [],
        "review": {"audit_id": "audit_1", "sample_id": "sample_1", "sample_revision": 2},
    })

def _audit_run(status: JobStatus = JobStatus.COMPLETED) -> AuditRun:
    return AuditRun(
        audit_id="audit_1", job_id="job_1", dataset_id="dataset_1",
        rulebook_id="book_1", rulebook_version=1, status=status,
        sample_count=2, processed_count=1, failed_count=0, incomplete_count=0,
        created_at=datetime(2026, 10, 18, tzinfo=UTC),
        updated_at=datetime(2026, 10, 18, 1, tzinfo=UTC),
    )

@pytest.mark.parametrize("status", list(JobStatus))
def test_get_audit_returns_public_persisted_lifecycle_state(api, status):
    client, repository = api
    repository.get_audit.return_value = _audit_run(status)

    response = client.get("/api/v1/audits/audit_1")

    assert response.status_code == 200
    assert response.json() == _audit_run(status).model_dump(mode="json")
    assert set(response.json()) == {
        "audit_id", "job_id", "dataset_id", "rulebook_id", "rulebook_version",
        "source_mode", "status", "sample_count", "processed_count", "failed_count",
        "incomplete_count", "created_at", "updated_at",
    }
    repository.get_audit.assert_called_once_with("audit_1")
    assert repository.method_calls == [call.get_audit("audit_1")]

@pytest.mark.parametrize("error,status,code", [
    (AuditNotFoundError("private-audit"), 404, "NOT_FOUND"),
    (WorkflowStorageError("private SQL and claim token"), 503, "DEPENDENCY_UNAVAILABLE"),
])
def test_get_audit_errors_use_safe_existing_envelopes(api, error, status, code):
    client, repository = api
    repository.get_audit.side_effect = error

    response = client.get("/api/v1/audits/private-audit")

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert str(error) not in response.text
    assert_contract("ErrorResponse", response.json())

def assert_contract(name, payload):
    spec = json.loads((Path(__file__).parents[1] / "api/openapi.json").read_text())
    Draft202012Validator({
        "components": spec["components"], "$ref": f"#/components/schemas/{name}",
    }).validate(payload)

def test_list_defaults_and_detail_preserve_nulls_and_match_contract(api, detail):
    client, repository = api
    repository.list_samples.return_value = AuditSamplePage(
        items=[detail.summary], limit=20, offset=0, total=1, next_offset=None,
    )
    repository.get_sample.return_value = detail
    page = client.get("/api/v1/audits/audit_1/samples")
    assert page.status_code == 200
    repository.list_samples.assert_called_once_with(
        "audit_1", limit=20, offset=0, tool_status=None, review_status=None,
    )
    assert_contract("AuditSamplePage", page.json())
    response = client.get("/api/v1/audits/audit_1/samples/sample_1")
    assert response.status_code == 200
    repository.get_sample.assert_called_once_with("audit_1", "sample_1")
    data = response.json()
    assert data["measurements"] is None and data["failures"] == []
    assert all(value is None for value in data["assets"].values())
    assert "pass" in data["summary"]["finding_counts"]
    assert "pass_count" not in data["summary"]["finding_counts"]
    assert_contract("SampleAudit", data)

def test_list_forwards_filters_and_pagination(api):
    client, repository = api
    repository.list_samples.return_value = AuditSamplePage(
        items=[], limit=5, offset=10, total=0, next_offset=None,
    )
    response = client.get("/api/v1/audits/audit_1/samples", params={
        "limit": 5, "offset": 10, "tool_status": "flagged", "review_status": "accepted",
    })
    assert response.status_code == 200
    repository.list_samples.assert_called_once_with(
        "audit_1", limit=5, offset=10, tool_status="flagged", review_status="accepted",
    )

@pytest.mark.parametrize("query", [
    "limit=0", "limit=101", "limit=abc", "offset=-1", "offset=1.5",
    "tool_status=accepted", "review_status=pass",
])
def test_invalid_queries_do_not_read_storage(api, query):
    client, repository = api
    response = client.get(f"/api/v1/audits/audit_1/samples?{query}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["details"]
    assert_contract("ErrorResponse", response.json())
    repository.list_samples.assert_not_called()

def _finding_review_payload(**changes):
    payload = {
        "expected_review_version": 0,
        "resolution": "accepted",
        "reviewer_id": "reviewer_1",
        "comment": "Reviewed the finding.",
    }
    payload.update(changes)
    return payload

def test_finding_review_write_reaches_service_and_returns_saved_review(api, monkeypatch):
    client, repository = api
    saved = FindingReview(
        revision=1,
        resolution="accepted",
        reviewer_id="reviewer_1",
        comment="Reviewed the finding.",
        updated_at=datetime(2026, 10, 18, tzinfo=UTC),
    )
    service = Mock(return_value=saved)
    monkeypatch.setattr(
        "backend.app.routers.finding_reviews.review_finding_service", service,
    )

    response = client.put(
        "/api/v1/findings/finding_1/review",
        json=_finding_review_payload(),
    )

    assert response.status_code == 200
    assert response.json() == saved.model_dump(mode="json")
    service.assert_called_once()
    assert service.call_args.args[0] == "finding_1"
    assert service.call_args.args[1] == FindingReviewRequest.model_validate(
        _finding_review_payload()
    )
    assert service.call_args.kwargs["repository"] is repository.finding_repository

def test_invalid_finding_review_body_returns_422_without_saving(api, monkeypatch):
    client, repository = api
    service = Mock()
    monkeypatch.setattr(
        "backend.app.routers.finding_reviews.review_finding_service", service,
    )

    response = client.put(
        "/api/v1/findings/finding_1/review",
        json=_finding_review_payload(expected_review_version=-1),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    service.assert_not_called()
    repository.finding_repository.save_finding_review.assert_not_called()

def test_missing_finding_review_target_returns_404(api):
    client, repository = api
    error = FindingNotFoundError("private-finding")
    repository.finding_repository.save_finding_review.side_effect = error

    response = client.put(
        "/api/v1/findings/finding_1/review",
        json=_finding_review_payload(),
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert str(error) not in response.text

def test_stale_finding_review_version_returns_409(api):
    client, repository = api
    error = FindingReviewConflictError("private-finding", 0, 1)
    repository.finding_repository.save_finding_review.side_effect = error

    response = client.put(
        "/api/v1/findings/finding_1/review",
        json=_finding_review_payload(),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VERSION_CONFLICT"
    assert "refresh" in response.json()["error"]["message"].lower()
    assert str(error) not in response.text

def test_finding_review_history_forwards_pagination_and_preserves_order(api):
    client, repository = api
    page = FindingReviewPage(
        items=[
            FindingReviewRecord(
                finding_id="finding_1", revision=3, resolution="accepted",
                reviewer_id="reviewer_3", comment="third",
                updated_at=datetime(2026, 10, 18, 12, tzinfo=UTC),
            ),
            FindingReviewRecord(
                finding_id="finding_1", revision=4, resolution="unsure",
                reviewer_id="reviewer_4", comment="fourth",
                updated_at=datetime(2026, 10, 18, 13, tzinfo=UTC),
            ),
        ],
        limit=2, offset=2, total=5, next_offset=4,
    )
    repository.finding_repository.list_finding_reviews.return_value = page

    response = client.get(
        "/api/v1/findings/finding_1/reviews",
        params={"limit": 2, "offset": 2},
    )

    assert response.status_code == 200
    assert [item["revision"] for item in response.json()["items"]] == [3, 4]
    assert response.json()["next_offset"] == 4
    repository.finding_repository.list_finding_reviews.assert_called_once_with(
        "finding_1", limit=2, offset=2,
    )

def test_finding_review_history_supports_empty_history(api):
    client, repository = api
    repository.finding_repository.list_finding_reviews.return_value = FindingReviewPage(
        items=[], limit=20, offset=0, total=0, next_offset=None,
    )

    response = client.get("/api/v1/findings/finding_1/reviews")

    assert response.status_code == 200
    assert response.json() == {
        "items": [], "limit": 20, "offset": 0, "total": 0, "next_offset": None,
    }
    repository.finding_repository.list_finding_reviews.assert_called_once_with(
        "finding_1", limit=20, offset=0,
    )

@pytest.mark.parametrize("query", [
    "limit=0", "limit=101", "limit=abc", "offset=-1", "offset=1.5",
])
def test_invalid_finding_review_history_queries_do_not_read_storage(api, query):
    client, repository = api

    response = client.get(f"/api/v1/findings/finding_1/reviews?{query}")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    repository.finding_repository.list_finding_reviews.assert_not_called()

def test_missing_finding_review_history_returns_404(api):
    client, repository = api
    error = FindingNotFoundError("private-finding")
    repository.finding_repository.list_finding_reviews.side_effect = error

    response = client.get("/api/v1/findings/finding_1/reviews")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert str(error) not in response.text

def _sample_review_payload(**changes):
    payload = {
        "expected_sample_revision": 7,
        "expected_review_version": 3,
        "decision": "accepted",
        "reviewer_id": "reviewer_1",
        "comment": "Reviewed the audited sample.",
    }
    payload.update(changes)
    return payload

def test_sample_review_write_reaches_service_with_distinct_versions_and_returns_result(
    api, monkeypatch,
):
    client, repository = api
    saved = SampleReview(
        audit_id="audit_1",
        sample_id="sample_1",
        sample_revision=7,
        revision=4,
        decision="accepted",
        reviewer_id="reviewer_1",
        comment="Reviewed the audited sample.",
        updated_at=datetime(2026, 10, 18, tzinfo=UTC),
    )
    service = Mock(return_value=saved)
    monkeypatch.setattr(
        "backend.app.routers.sample_reviews.review_sample_service", service,
    )

    response = client.put(
        "/api/v1/audits/audit_1/samples/sample_1/review",
        json=_sample_review_payload(),
    )

    assert response.status_code == 200
    assert response.json() == saved.model_dump(mode="json")
    service.assert_called_once()
    assert service.call_args.args[:2] == ("audit_1", "sample_1")
    request = service.call_args.args[2]
    assert request == SampleReviewRequest.model_validate(_sample_review_payload())
    assert request.expected_sample_revision == 7
    assert request.expected_review_version == 3
    assert service.call_args.kwargs["repository"] is repository.sample_review_repository

def test_invalid_sample_review_body_returns_422_without_saving(api, monkeypatch):
    client, repository = api
    service = Mock()
    monkeypatch.setattr(
        "backend.app.routers.sample_reviews.review_sample_service", service,
    )

    response = client.put(
        "/api/v1/audits/audit_1/samples/sample_1/review",
        json=_sample_review_payload(expected_sample_revision=0),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    service.assert_not_called()
    repository.sample_review_repository.save_sample_review.assert_not_called()

def test_sample_review_history_forwards_exact_scope_and_pagination(api):
    client, repository = api
    page = SampleReviewPage(
        items=[
            SampleReviewRecord(
                audit_id="audit_1", sample_id="sample_1", sample_revision=7,
                revision=3, decision="accepted", reviewer_id="reviewer_3",
                comment="third", updated_at=datetime(2026, 10, 18, 12, tzinfo=UTC),
            ),
            SampleReviewRecord(
                audit_id="audit_1", sample_id="sample_1", sample_revision=7,
                revision=4, decision="needs_correction", reviewer_id="reviewer_4",
                comment="fourth", updated_at=datetime(2026, 10, 18, 13, tzinfo=UTC),
            ),
        ],
        limit=2, offset=2, total=4, next_offset=None,
    )
    repository.sample_review_repository.list_sample_reviews.return_value = page

    response = client.get(
        "/api/v1/audits/audit_1/samples/sample_1/reviews",
        params={"sample_revision": 7, "limit": 2, "offset": 2},
    )

    assert response.status_code == 200
    assert [item["revision"] for item in response.json()["items"]] == [3, 4]
    assert response.json()["next_offset"] is None
    repository.sample_review_repository.list_sample_reviews.assert_called_once_with(
        "audit_1", "sample_1", 7, limit=2, offset=2,
    )

def test_sample_review_history_requires_a_valid_sample_revision(api):
    client, repository = api

    for query in ("", "sample_revision=0", "sample_revision=abc", "sample_revision=1.5"):
        response = client.get(
            f"/api/v1/audits/audit_1/samples/sample_1/reviews?{query}"
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    repository.sample_review_repository.list_sample_reviews.assert_not_called()

@pytest.mark.parametrize("query", [
    "limit=0", "limit=101", "limit=abc", "offset=-1", "offset=1.5",
])
def test_sample_review_history_rejects_invalid_pagination(api, query):
    client, repository = api

    response = client.get(
        f"/api/v1/audits/audit_1/samples/sample_1/reviews?sample_revision=7&{query}"
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    repository.sample_review_repository.list_sample_reviews.assert_not_called()

def test_missing_sample_review_history_returns_404(api):
    client, repository = api
    error = AuditSampleNotFoundError("private-audit", "private-sample")
    repository.sample_review_repository.list_sample_reviews.side_effect = error

    response = client.get(
        "/api/v1/audits/audit_1/samples/sample_1/reviews?sample_revision=7"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert str(error) not in response.text

def test_stale_sample_review_history_revision_returns_409(api):
    client, repository = api
    error = SampleRevisionConflictError("private-audit", "private-sample", 7, [1, 2])
    repository.sample_review_repository.list_sample_reviews.side_effect = error

    response = client.get(
        "/api/v1/audits/audit_1/samples/sample_1/reviews?sample_revision=7"
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STALE_SAMPLE_REVISION"
    assert str(error) not in response.text

@pytest.mark.parametrize("error", [
    AuditSampleNotFoundError("private-audit", "private-sample"),
    SampleRevisionConflictError("private-audit", "private-sample", 7, [1, 2]),
    SampleReviewConflictError("private-audit", "private-sample", 7, 3, 4),
    SampleReviewStateError("private-audit", "private-sample", 7, "running"),
])
def test_sample_review_storage_errors_use_existing_http_mappings(api, error):
    client, repository = api
    repository.sample_review_repository.save_sample_review.side_effect = error

    response = client.put(
        "/api/v1/audits/audit_1/samples/sample_1/review",
        json=_sample_review_payload(),
    )

    expected = {
        AuditSampleNotFoundError: (404, "NOT_FOUND"),
        SampleRevisionConflictError: (409, "STALE_SAMPLE_REVISION"),
        SampleReviewConflictError: (409, "VERSION_CONFLICT"),
        SampleReviewStateError: (409, "INVALID_SAMPLE_REVIEW_STATE"),
    }[type(error)]
    assert response.status_code == expected[0]
    assert response.json()["error"]["code"] == expected[1]
    assert str(error) not in response.text

@pytest.mark.parametrize("error,status,code", [
    (AuditNotFoundError("private-id"), 404, "NOT_FOUND"),
    (AuditSampleNotFoundError("private-id", "private-sample"), 404, "NOT_FOUND"),
    (WorkflowStorageError("secret password and SQL"), 503, "DEPENDENCY_UNAVAILABLE"),
    (DatabaseConfigurationError("secret connection string"), 503, "DEPENDENCY_UNAVAILABLE"),
    (RuntimeError("internal implementation details"), 500, "INTERNAL_ERROR"),
])
def test_read_errors_have_safe_consistent_payloads(api, error, status, code):
    client, repository = api
    repository.get_sample.side_effect = error
    response = client.get("/api/v1/audits/audit_1/samples/sample_1")
    assert response.status_code == status
    body = response.json()
    assert body["error"]["code"] == code
    assert body["error"]["request_id"]
    assert str(error) not in response.text
    assert_contract("ErrorResponse", body)

def test_finding_not_found_uses_shared_safe_not_found_mapping(api):
    client, repository = api
    error = FindingNotFoundError("private-finding")
    repository.get_sample.side_effect = error

    response = client.get("/api/v1/audits/audit_1/samples/sample_1")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["message"] == "The requested audit, sample, or finding does not exist."
    assert body["error"]["request_id"]
    assert str(error) not in response.text
    assert_contract("ErrorResponse", body)

def test_finding_review_conflict_maps_to_safe_refreshable_version_conflict(api):
    client, repository = api
    error = FindingReviewConflictError("private-finding", 2, 3)
    repository.get_sample.side_effect = error

    response = client.get("/api/v1/audits/audit_1/samples/sample_1")

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "VERSION_CONFLICT"
    assert "refresh" in body["error"]["message"].lower()
    assert "resubmit" in body["error"]["message"].lower()
    assert body["error"]["request_id"]
    assert str(error) not in response.text

@pytest.mark.parametrize("error,code,message", [
    (
        SampleReviewConflictError("private-audit", "private-sample", 2, 0, 1),
        "VERSION_CONFLICT",
        "The sample review is out of date. Refresh before resubmitting.",
    ),
    (
        SampleRevisionConflictError("private-audit", "private-sample", 7, [1, 2]),
        "STALE_SAMPLE_REVISION",
        "The requested sample revision is stale. Refresh before resubmitting.",
    ),
    (
        SampleReviewStateError("private-audit", "private-sample", 2, "running"),
        "INVALID_SAMPLE_REVIEW_STATE",
        "The sample is not currently eligible for review.",
    ),
])
def test_sample_review_errors_map_to_safe_conflict_payloads(api, error, code, message):
    client, repository = api
    repository.get_sample.side_effect = error

    response = client.get("/api/v1/audits/audit_1/samples/sample_1")

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == code
    assert body["error"]["message"] == message
    assert body["error"]["request_id"]
    assert str(error) not in response.text
    assert_contract("ErrorResponse", body)

def test_live_openapi_lists_implemented_routes_and_rejects_invalid_writes(api):
    client, _ = api
    spec = client.get("/openapi.json").json()
    assert set(spec["paths"]) == {
        "/api/v1/rulebooks/extractions",
        "/api/v1/rulebooks",
        "/api/v1/rulebooks/{rulebook_id}",
        "/api/v1/rulebooks/{rulebook_id}/versions",
        "/api/v1/rulebooks/{rulebook_id}/reviews",
        "/api/v1/rulebooks/{rulebook_id}/confirm",
        "/api/v1/rulebooks/{rulebook_id}/reject",
        "/api/v1/rulebooks/{rulebook_id}/revisions",
        "/api/v1/audits",
        "/api/v1/audits/{audit_id}",
        "/api/v1/audits/{audit_id}/samples",
        "/api/v1/audits/{audit_id}/samples/{sample_id}",
        "/api/v1/datasets/{dataset_id}/samples/{sample_id}/assets/{asset_kind}",
        "/api/v1/datasets",
        "/api/v1/datasets/{dataset_id}",
        "/api/v1/datasets/{dataset_id}/samples",
        "/api/v1/datasets/{dataset_id}/samples/{sample_id}",
        "/api/v1/exports",
        "/api/v1/exports/{export_id}",
        "/api/v1/exports/{export_id}/manifest",
        "/api/v1/exports/{export_id}/download",
        "/api/v1/findings/{finding_id}/review",
        "/api/v1/findings/{finding_id}/reviews",
        "/api/v1/jobs/{job_id}",
        "/api/v1/jobs/{job_id}/cancel",
        "/api/v1/audits/{audit_id}/samples/{sample_id}/review",
        "/api/v1/audits/{audit_id}/samples/{sample_id}/reviews",
    }
    assert {operation["operationId"] for path in spec["paths"].values() for operation in path.values()} == {
        "extractRulebook", "createRulebook", "getRulebook", "listRulebookReviews", "listRulebookVersions", "listRulebooks",
        "replaceDraftRulebook", "confirmRulebook", "rejectRulebook", "reviseRulebook",
        "startAudit", "listAudits", "getAudit", "listAuditSamples", "getAuditSample", "getSampleAsset", "reviewFinding", "listFindingReviews", "getJob", "cancelJob",
        "importDataset", "listDatasets", "getDataset", "listDatasetSamples", "getDatasetSample",
        "reviewSample", "listSampleReviews",
        "startExport", "listExports", "getExport", "getExportManifest", "downloadExport",
    }
    assert client.post("/api/v1/audits/audit_1/samples").status_code == 405
    response = client.put("/api/v1/audits/audit_1/samples/sample_1/review", json={})
    assert response.status_code == 422
    assert_contract("ErrorResponse", response.json())
