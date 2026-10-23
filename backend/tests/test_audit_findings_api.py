"""HTTP behavior of GET /api/v1/audits/{audit_id}/findings with a mocked repository."""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, call

import pytest
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from backend.app.main import create_app
from backend.app.models.audit import AuditRun
from backend.app.models.audit_read import AuditSamplePage, FindingPage
from backend.app.models.finding import Finding, FindingOutcome
from backend.app.models.job import JobStatus
from backend.app.routers.audits import get_audit_read_repository, get_audit_repository
from backend.app.storage.audit_read_repository import AuditReadRepository
from backend.app.storage.workflow_repository import AuditNotFoundError, WorkflowStorageError

MAX_OFFSET = 9_223_372_036_854_775_807
URL = "/api/v1/audits/audit_1/findings"


@pytest.fixture
def api():
    app = create_app()
    repository = Mock(spec=AuditReadRepository)
    app.dependency_overrides[get_audit_read_repository] = lambda: repository
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, repository, app


def assert_contract(name, payload):
    spec = json.loads((Path(__file__).parents[1] / "api/openapi.json").read_text(encoding="utf-8"))
    Draft202012Validator({
        "components": spec["components"], "$ref": f"#/components/schemas/{name}",
    }).validate(payload)


def finding(finding_id="finding_1", outcome="fail", sample_id="sample_1", **changes):
    values = dict(
        finding_id=finding_id, audit_id="audit_1", sample_id=sample_id, sample_revision=2, object_id=None,
        rulebook_id="book_1", rulebook_version=3, rule_id="rule_1", description="Use an allowed label.",
        check_name="allowed_labels", outcome=outcome, origin="reviewer",
        parameters={"allowed_labels": ["person"]}, evidence={"measurements": {"label": "vehicle"}},
    )
    return Finding(**(values | changes))


def page(items=(), *, limit=20, offset=0, total=None, next_offset=None):
    items = list(items)
    return FindingPage(
        items=items, limit=limit, offset=offset,
        total=len(items) + offset if total is None else total, next_offset=next_offset,
    )


# --- success ---------------------------------------------------------------------------------

def test_defaults_are_forwarded_and_the_page_matches_the_contract(api):
    client, repository, _ = api
    result = page([finding("finding_1"), finding("finding_2", outcome="pass")])
    repository.list_audit_findings.return_value = result

    response = client.get(URL)

    assert response.status_code == 200
    assert response.json() == result.model_dump(mode="json")
    assert set(response.json()) == {"items", "limit", "offset", "total", "next_offset"}
    repository.list_audit_findings.assert_called_once_with(
        "audit_1", limit=20, offset=0, sample_id=None, outcome=None,
    )
    assert_contract("FindingPage", response.json())


def test_explicit_pagination_and_filters_are_forwarded(api):
    client, repository, _ = api
    repository.list_audit_findings.return_value = page([], limit=5, offset=10, total=0)

    response = client.get(URL, params={"limit": 5, "offset": 10, "sample_id": "sample_1", "outcome": "fail"})

    assert response.status_code == 200
    repository.list_audit_findings.assert_called_once_with(
        "audit_1", limit=5, offset=10, sample_id="sample_1", outcome="fail",
    )
    assert repository.list_audit_findings.call_args.kwargs["outcome"] is FindingOutcome.FAIL


@pytest.mark.parametrize("sample_id", [" sample_1 ", "sample\n1", "人物_1", "sample%00_1"])
def test_valid_sample_identifiers_are_forwarded_unchanged(api, sample_id):
    client, repository, _ = api
    repository.list_audit_findings.return_value = page([])

    response = client.get(URL, params={"sample_id": sample_id})

    assert response.status_code == 200
    assert repository.list_audit_findings.call_args.kwargs["sample_id"] == sample_id


@pytest.mark.parametrize("outcome", [value.value for value in FindingOutcome])
def test_every_outcome_filter_is_supported(api, outcome):
    client, repository, _ = api
    repository.list_audit_findings.return_value = page([finding(outcome=outcome)])

    response = client.get(URL, params={"outcome": outcome})

    assert response.status_code == 200 and response.json()["items"][0]["outcome"] == outcome
    assert repository.list_audit_findings.call_args.kwargs["outcome"] == outcome
    assert_contract("FindingPage", response.json())


def test_empty_and_unmatched_filter_pages_are_http_200(api):
    client, repository, _ = api
    repository.list_audit_findings.return_value = page([])
    for query in ("", "?sample_id=not_in_this_audit", "?outcome=unsure&sample_id=other"):
        response = client.get(URL + query)
        assert response.status_code == 200
        assert response.json() == {"items": [], "limit": 20, "offset": 0, "total": 0, "next_offset": None}
        assert_contract("FindingPage", response.json())


def test_a_page_with_more_findings_exposes_next_offset(api):
    client, repository, _ = api
    repository.list_audit_findings.return_value = page(
        [finding("finding_1"), finding("finding_2")], limit=2, total=5, next_offset=2,
    )
    body = client.get(URL, params={"limit": 2}).json()
    assert (body["total"], body["next_offset"], len(body["items"])) == (5, 2, 2)


def test_findings_keep_nulls_review_state_and_the_repository_order(api):
    client, repository, _ = api
    reviewed = finding("finding_b", review={
        "revision": 2, "resolution": "rejected", "reviewer_id": "reviewer_1",
        "comment": "Not an error.", "updated_at": datetime(2026, 10, 21, 9, tzinfo=UTC),
    })
    repository.list_audit_findings.return_value = page([finding("finding_a"), reviewed])

    items = client.get(URL).json()["items"]

    assert [item["finding_id"] for item in items] == ["finding_a", "finding_b"]
    assert items[0]["review"] == {
        "revision": 0, "resolution": "pending", "reviewer_id": None, "comment": None, "updated_at": None,
    }
    assert items[1]["review"]["resolution"] == "rejected" and items[1]["review"]["revision"] == 2
    assert items[0]["object_id"] is None and items[0]["source_sentence"] is None
    assert items[0]["evidence"]["image_url"] is None


def test_the_largest_supported_offset_is_accepted(api):
    client, repository, _ = api
    repository.list_audit_findings.return_value = page([], offset=MAX_OFFSET, total=0)
    assert client.get(URL, params={"offset": MAX_OFFSET}).status_code == 200
    assert repository.list_audit_findings.call_args.kwargs["offset"] == MAX_OFFSET


# --- validation -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("query", [
    "limit=0", "limit=101", "limit=-1", "limit=abc", "limit=", "limit=1.5",
    "offset=-1", "offset=1.5", "offset=abc", "offset=", f"offset={MAX_OFFSET + 1}",
    "outcome=failed", "outcome=FAIL", "outcome=", "outcome=accepted",
    "sample_id=", "sample_id=%20", "sample_id=%20%20%09",
    "sample_id=%00", "sample_id=%00sample_1", "sample_id=sample%00_1", "sample_id=sample_1%00",
    "sample_id=sample_1%00%0A",
])
def test_invalid_queries_are_rejected_without_reading_storage(api, query):
    client, repository, _ = api
    response = client.get(f"{URL}?{query}")
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR" and body["error"]["details"]
    assert "request_id" in body["error"]
    assert_contract("ErrorResponse", body)
    repository.list_audit_findings.assert_not_called()


@pytest.mark.parametrize("audit_id", [
    "%20", "%20%20", "%00", "%00audit_1", "audit%00_1", "audit_1%00", "audit_1%00%0A",
])
def test_an_invalid_audit_identifier_is_rejected_without_reading_storage(api, audit_id):
    client, repository, _ = api
    response = client.get(f"/api/v1/audits/{audit_id}/findings")
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"
    repository.list_audit_findings.assert_not_called()


# --- errors ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("error,status,code", [
    (AuditNotFoundError("private-audit-id"), 404, "NOT_FOUND"),
    (WorkflowStorageError("private SQL, connection string and claim token"), 503, "DEPENDENCY_UNAVAILABLE"),
    (RuntimeError("private traceback and /srv/storage/path"), 500, "INTERNAL_ERROR"),
])
def test_errors_use_the_safe_existing_envelope_without_leaking_details(api, error, status, code):
    client, repository, _ = api
    repository.list_audit_findings.side_effect = error

    response = client.get("/api/v1/audits/private-audit-id/findings?sample_id=sample_1")

    assert response.status_code == status
    body = response.json()
    assert body["error"]["code"] == code and "request_id" in body["error"]
    assert str(error) not in response.text
    for secret in ("private", "claim token", "/srv/storage", "SQL", "traceback"):
        assert secret not in json.dumps(body["error"]["message"])
    assert_contract("ErrorResponse", body)


# --- wiring ------------------------------------------------------------------------------------------------------

def test_the_route_uses_the_request_time_repository_dependency(api):
    _, _, app = api
    contexts = [c for c in iter_route_contexts(app.routes) if c.path == "/api/v1/audits/{audit_id}/findings"]
    assert len(contexts) == 1
    context = contexts[0]
    route = context.original_route
    assert context.methods == {"GET"} and route.operation_id == "listAuditFindings"
    assert [dependency.call for dependency in route.dependant.dependencies] == [get_audit_read_repository]
    assert route.body_field is None  # no request body


def test_a_read_only_contacts_the_repository_once_and_starts_nothing(api):
    client, repository, _ = api
    repository.list_audit_findings.return_value = page([finding()])

    client.get(URL, params={"outcome": "fail", "limit": 3})

    assert repository.method_calls == [
        call.list_audit_findings("audit_1", limit=3, offset=0, sample_id=None, outcome="fail"),
    ]


def test_the_findings_route_coexists_with_the_other_audit_reads(api):
    client, repository, app = api
    app.dependency_overrides[get_audit_repository] = lambda: repository
    run = AuditRun(
        audit_id="audit_1", job_id="job_1", dataset_id="dataset_1", rulebook_id="book_1", rulebook_version=1,
        status=JobStatus.COMPLETED, sample_count=1, processed_count=1, failed_count=0, incomplete_count=0,
        created_at=datetime(2026, 10, 18, tzinfo=UTC), updated_at=datetime(2026, 10, 18, 1, tzinfo=UTC),
    )
    repository.get_audit = Mock(return_value=run)
    repository.list_samples.return_value = AuditSamplePage(items=[], limit=20, offset=0, total=0, next_offset=None)
    repository.list_audit_findings.return_value = page([])

    assert client.get("/api/v1/audits/audit_1").status_code == 200
    assert client.get("/api/v1/audits/audit_1/samples").status_code == 200
    assert client.get(URL).status_code == 200

    assert repository.method_calls == [
        call.get_audit("audit_1"),
        call.list_samples("audit_1", limit=20, offset=0, tool_status=None, review_status=None),
        call.list_audit_findings("audit_1", limit=20, offset=0, sample_id=None, outcome=None),
    ]


def test_other_methods_are_not_allowed_and_do_not_touch_storage(api):
    client, repository, _ = api
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)(URL).status_code == 405
    assert repository.method_calls == []
