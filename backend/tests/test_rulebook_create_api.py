"""Manual creation HTTP boundaries; author only, no live database or inference."""

from copy import deepcopy
import json
from inspect import iscoroutinefunction
from unittest.mock import Mock, call
from uuid import UUID

from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from backend.app.main import create_app
from backend.tests.display_name_helpers import without_display_names
from backend.app.models.audit_read import ErrorResponse
from backend.app.models.rule import Rule, Rulebook
from backend.app.models.rulebook_creation import RulebookCreate
from backend.app.routers import rulebooks
from backend.app.services import rulebook_creation
from backend.app.storage.repository import (
    RulebookRepository, RulebookStorageError, RulebookVersionConflictError,
)

BASE = "/api/v1/rulebooks"
SOURCE = " \tUse person labels.\r\n人 café e\u0301 🙂\n "

def payload():
    return {"guideline_text": SOURCE, "rules": [{
        "rule_id": "label", "description": " Keep the submitted description. ",
        "check_name": "future_check", "parameters": {"nested": [None, True, 0, {"label": "人"}]},
        "source_sentence": "Use person labels.",
    }]}

@pytest.fixture
def api():
    app = without_display_names(create_app())
    repository = Mock(spec_set=RulebookRepository)
    repository.save_rulebook.side_effect = lambda draft, **kwargs: Rulebook.model_validate(draft.model_dump())
    app.dependency_overrides[rulebooks.get_rulebook_repository] = lambda: repository
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, repository
    finally:
        app.dependency_overrides.clear()

def assert_failure(response, status, code):
    assert response.status_code == status
    error = ErrorResponse.model_validate(response.json()).error
    assert error.code == code and UUID(hex=error.request_id)
    for secret in ("private", "password", "SQL", "/internal/path"):
        assert secret not in response.text
    return error

def test_create_returns_saved_public_draft_and_only_initial_save(api):
    client, repository = api
    body = payload()
    response = client.post(BASE, json=body)
    assert response.status_code == 201
    book = Rulebook.model_validate(response.json())
    assert book.guideline_text == SOURCE
    assert book.version == 1 and book.confirmation_state == "draft"
    assert UUID(hex=book.rulebook_id.removeprefix("rulebook_")).version == 4
    assert book.rules[0].origin == "reviewer" and book.rules[0].confirmation_state == "draft"
    assert book.rules[0].model_dump(exclude={"origin", "confirmation_state"}) == body["rules"][0]
    assert repository.method_calls == [call.save_rulebook(book, expected_version=0)]

def test_route_forwards_exact_typed_payload_and_overridden_repository(api, monkeypatch):
    client, repository = api
    body = payload()
    saved = Rulebook(rulebook_id="saved", guideline_text="repository snapshot")
    service = Mock(return_value=saved)
    monkeypatch.setattr(rulebooks, "create_rulebook_service", service)
    response = client.post(BASE, json=body)
    assert response.status_code == 201 and response.json() == saved.model_dump(mode="json")
    args, kwargs = service.call_args
    assert service.call_count == 1 and len(args) == 1
    assert isinstance(args[0], RulebookCreate)
    assert args[0].model_dump() == body
    assert kwargs == {"repository": repository} and repository.method_calls == []

@pytest.mark.parametrize("body", [
    {"guideline_text": ""}, {"guideline_text": "", "rules": []},
    {"guideline_text": SOURCE, "rules": []},
])
def test_empty_or_omitted_rules_create_unconfirmed_editable_drafts(api, body):
    client, repository = api
    response = client.post(BASE, json=body)
    assert response.status_code == 201
    assert response.json()["rules"] == [] and response.json()["confirmation_state"] == "draft"
    assert repository.save_rulebook.call_count == 1
    repository.save_rulebook_review.assert_not_called()

@pytest.mark.parametrize("level", ["book", "rule"])
@pytest.mark.parametrize("field,value", [
    ("unknown", True), ("rulebook_id", "client"), ("version", 2),
    ("origin", "extracted"), ("confirmation_state", "confirmed"),
    ("review_history", []), ("created_at", "2026-10-20T00:00:00Z"), ("reviewer_id", "client"),
])
def test_forbidden_fields_rejected_before_service_or_save(api, monkeypatch, level, field, value):
    client, repository = api
    service = Mock()
    monkeypatch.setattr(rulebooks, "create_rulebook_service", service)
    body = payload()
    (body if level == "book" else body["rules"][0])[field] = value
    assert_failure(client.post(BASE, json=body), 422, "VALIDATION_ERROR")
    service.assert_not_called()
    assert repository.method_calls == []

@pytest.mark.parametrize("change", [
    "guideline_null", "guideline_number", "rules_null", "parameters_null", "blank_id",
    "description_number", "duplicate", "citation", "citation_line_endings",
])
def test_invalid_fields_and_aggregate_content_never_save(api, change):
    client, repository = api
    body = payload()
    if change == "guideline_null":
        body["guideline_text"] = None
    elif change == "guideline_number":
        body["guideline_text"] = 1
    elif change == "rules_null":
        body["rules"] = None
    elif change == "parameters_null":
        body["rules"][0]["parameters"] = None
    elif change == "blank_id":
        body["rules"][0]["rule_id"] = " \t"
    elif change == "description_number":
        body["rules"][0]["description"] = 1
    elif change == "duplicate":
        body["rules"].append(deepcopy(body["rules"][0]))
    else:
        body["rules"][0]["source_sentence"] = (
            "private fabricated citation" if change == "citation" else SOURCE.replace("\r\n", "\n")
        )
    assert_failure(client.post(BASE, json=body), 422, "VALIDATION_ERROR")
    assert repository.method_calls == []

@pytest.mark.parametrize("content", ["", "null", "[]", "{", '{"guideline_text":'])
def test_missing_null_and_malformed_bodies_remain_safe(api, content):
    client, repository = api
    assert_failure(client.post(BASE, content=content, headers={"Content-Type": "application/json"}),
                   422, "VALIDATION_ERROR")
    assert repository.method_calls == []

@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_json_extensions_are_rejected_without_writes(api, number):
    client, repository = api
    body = payload()
    body["rules"][0]["parameters"] = {"outer": [{"value": number}]}
    response = client.post(BASE, content=json.dumps(body), headers={"Content-Type": "application/json"})
    assert_failure(response, 422, "VALIDATION_ERROR")
    assert repository.method_calls == []

@pytest.mark.parametrize("failure", ["storage", "commit", "value", "validation"])
def test_storage_and_internal_failures_never_become_success_or_client_errors(api, failure):
    client, repository = api
    if failure == "validation":
        with pytest.raises(ValidationError) as caught:
            Rule(rule_id="private", description=123, origin="reviewer")
        error, status, code = caught.value, 500, "INTERNAL_ERROR"
    elif failure == "value":
        error, status, code = ValueError("private SQL password /internal/path"), 500, "INTERNAL_ERROR"
    else:
        error = RulebookStorageError(f"private {failure} SQL password /internal/path")
        status, code = 503, "DEPENDENCY_UNAVAILABLE"
    repository.save_rulebook.side_effect = error
    response = client.post(BASE, json=payload())
    assert_failure(response, status, code)
    assert repository.save_rulebook.call_count == len(repository.method_calls) == 1
    assert repository.save_rulebook.call_args.kwargs == {"expected_version": 0}

def test_generated_id_conflict_uses_existing_safe_409_without_retry(api, monkeypatch):
    client, repository = api
    fixed = UUID("12345678-1234-4234-8123-1234567890ab")
    generator = Mock(return_value=fixed)
    monkeypatch.setattr(rulebook_creation, "uuid4", generator)
    repository.save_rulebook.side_effect = RulebookVersionConflictError(f"rulebook_{fixed.hex}", 0, 3)
    error = assert_failure(client.post(BASE, json=payload()), 409, "VERSION_CONFLICT")
    assert error.details == []
    generator.assert_called_once_with()
    assert repository.save_rulebook.call_count == len(repository.method_calls) == 1
    assert repository.save_rulebook.call_args.args[0].version == 1
    assert repository.save_rulebook.call_args.kwargs == {"expected_version": 0}

def test_invalid_saved_response_remains_safe_500(api):
    client, repository = api
    repository.save_rulebook.side_effect = None
    repository.save_rulebook.return_value = {"rulebook_id": "private-invalid-snapshot"}
    assert_failure(client.post(BASE, json=payload()), 500, "INTERNAL_ERROR")
    assert repository.save_rulebook.call_count == 1

def test_live_creation_schema_and_registration_preserve_existing_routes():
    app = create_app()
    routes = [context.original_route for context in iter_route_contexts(app.routes)
              if isinstance(context.original_route, APIRoute) and context.original_route.include_in_schema]
    creations = [route for route in routes if route.operation_id == "createRulebook"]
    assert len(creations) == 1 and creations[0].methods == {"POST"}
    assert not iscoroutinefunction(creations[0].endpoint)
    identifiers = [route.operation_id for route in routes]
    for identifier in ("listRulebooks", "getRulebook", "listRulebookVersions", "listRulebookReviews",
                       "replaceDraftRulebook", "confirmRulebook", "rejectRulebook", "reviseRulebook"):
        assert identifiers.count(identifier) == 1
    spec = app.openapi()
    operation = spec["paths"][BASE]["post"]
    assert operation["operationId"] == "createRulebook"
    assert "get" in spec["paths"][BASE] and operation.get("parameters", []) == []
    assert operation["requestBody"]["required"] is True
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RulebookCreate",
    }
    assert operation["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RulebookRecord",
    }
    assert set(operation["responses"]) == {"201", "404", "409", "422", "500", "503"}

def test_large_json_exponent_overflow_is_a_safe_422_without_writes(api):
    client, repository = api
    raw = '{"guideline_text":"","rules":[{"rule_id":"r","description":"R.","parameters":{"nested":[1e999]}}]}'
    response = client.post(BASE, content=raw, headers={"Content-Type": "application/json"})
    assert_failure(response, 422, "VALIDATION_ERROR")
    assert repository.method_calls == []

@pytest.mark.parametrize("check", [None, "future_check", "omitted"])
def test_nonempty_uncited_manual_rules_preserve_null_future_and_omitted_checks(api, check):
    client, repository = api
    body = {"guideline_text": "", "rules": [{"rule_id": "manual", "description": "Human review."}]}
    if check != "omitted":
        body["rules"][0]["check_name"] = check
    response = client.post(BASE, json=body)
    assert response.status_code == 201
    saved = Rulebook.model_validate(response.json())
    rule = saved.rules[0]
    assert rule.check_name == (None if check == "omitted" else check)
    assert rule.source_sentence is None and rule.parameters == {}
    assert rule.origin == "reviewer" and rule.confirmation_state == saved.confirmation_state == "draft"
    assert repository.method_calls == [call.save_rulebook(saved, expected_version=0)]
