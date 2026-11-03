"""Review HTTP delegation, real service policy and safe validation boundaries."""

from copy import deepcopy
import json
from unittest.mock import Mock

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from backend.app.main import create_app
from backend.tests.display_name_helpers import without_display_names
from backend.app.models.audit_read import ErrorResponse
from backend.app.models.review import RulebookAction, RulebookReplace
from backend.app.models.rule import Rule, Rulebook
from backend.app.routers import rulebooks
from backend.app.services.review import RulebookContentValidationError
from backend.app.storage.repository import RulebookNotFoundError, RulebookRepository, RulebookStorageError, RulebookVersionConflictError

BASE = "/api/v1/rulebooks/book_1"
OPERATIONS = [
    ("put", "", "save_rulebook_review_service", RulebookReplace, 200),
    ("post", "/confirm", "confirm_rulebook_service", RulebookAction, 200),
    ("post", "/reject", "reject_rulebook_service", RulebookAction, 200),
    ("post", "/revisions", "revise_rulebook_service", RulebookAction, 201),
]

def make_book(state="draft", rule_state="draft"):
    return Rulebook(rulebook_id="book_1", guideline_text="Use person labels.",
                    rules=[Rule(rule_id="label", description="Use person labels.", origin="extracted",
                                source_sentence="Use person labels.", check_name="unsupported_future_check",
                                confirmation_state=rule_state)], confirmation_state=state)

def payload(replace=False):
    body = {"expected_version": 1, "reviewer_id": "reviewer_1"}
    if replace:
        body.update(guideline_text="Use person labels.", rules=make_book().model_dump(mode="json")["rules"])
    return body

@pytest.fixture
def api():
    app = without_display_names(create_app())
    repository = Mock(spec_set=RulebookRepository)
    current = make_book()
    repository.get_rulebook.side_effect = lambda *args, **kwargs: current.model_copy(deep=True)
    repository.save_rulebook_review.side_effect = lambda value, **kwargs: value.model_copy(deep=True)
    app.dependency_overrides[rulebooks.get_rulebook_repository] = lambda: repository
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, repository
    app.dependency_overrides.clear()

def assert_failure(response, status, code):
    assert response.status_code == status
    error = ErrorResponse.model_validate(response.json()).error
    assert error.code == code and error.request_id
    assert "private" not in response.text and "SQL" not in response.text
    return error

@pytest.mark.parametrize("method,suffix,service_name,model,status", OPERATIONS)
def test_each_mutation_delegates_once_to_its_service(api, monkeypatch, method, suffix, service_name, model, status):
    client, repository = api
    result = make_book().model_copy(update={"version": 2})
    service = Mock(return_value=result)
    monkeypatch.setattr(rulebooks, service_name, service)
    body = payload(model is RulebookReplace)
    response = getattr(client, method)(BASE + suffix, json=body)
    assert response.status_code == status
    assert response.json() == result.model_dump(mode="json")
    service.assert_called_once()
    args, kwargs = service.call_args
    assert args[0] == "book_1" and isinstance(args[1], model)
    assert args[1].model_dump(mode="json") == body
    assert kwargs == {"repository": repository}
    assert not repository.method_calls

@pytest.mark.parametrize("method,suffix,service_name,model,status", OPERATIONS)
@pytest.mark.parametrize("change", [{"expected_version": True}, {"expected_version": "1"},
                                    {"expected_version": 0}, {"expected_version": None},
                                    {"expected_version": 1.0}, {"expected_version": -1},
                                    {"reviewer_id": " "}, {"unknown": 1}])
def test_strict_requests_fail_before_service_or_storage(api, monkeypatch, method, suffix, service_name, model, status, change):
    client, repository = api
    service = Mock()
    monkeypatch.setattr(rulebooks, service_name, service)
    body = {**payload(model is RulebookReplace), **change}
    assert_failure(getattr(client, method)(BASE + suffix, json=body), 422, "VALIDATION_ERROR")
    service.assert_not_called()
    assert not repository.method_calls

@pytest.mark.parametrize("method,suffix,service_name,model,status", OPERATIONS)
def test_whitespace_mutation_ids_fail_without_storage(api, method, suffix, service_name, model, status):
    client, repository = api
    assert_failure(getattr(client, method)("/api/v1/rulebooks/%20%09" + suffix,
                                          json=payload(model is RulebookReplace)), 422, "VALIDATION_ERROR")
    assert not repository.method_calls

@pytest.mark.parametrize("method,suffix,service_name,model,status", OPERATIONS)
@pytest.mark.parametrize("failure", ["missing", "stale", "storage", "internal"])
def test_all_mutations_preserve_safe_failures_without_retries(api, method, suffix, service_name, model, status, failure):
    client, repository = api
    errors = {"missing": (RulebookNotFoundError("private-book", None), 404, "NOT_FOUND"),
              "stale": (RulebookVersionConflictError("private-book", 1, 2), 409, "VERSION_CONFLICT"),
              "storage": (RulebookStorageError("private SQL connection"), 503, "DEPENDENCY_UNAVAILABLE"),
              "internal": (ValueError("private bug"), 500, "INTERNAL_ERROR")}
    error, expected_status, code = errors[failure]
    repository.get_rulebook.side_effect = error
    assert_failure(getattr(client, method)(BASE + suffix, json=payload(model is RulebookReplace)), expected_status, code)
    assert repository.get_rulebook.call_count == 1
    repository.save_rulebook_review.assert_not_called()

@pytest.mark.parametrize("change", ["duplicate", "citation", "removed_citation_text", "origin", "new_extracted", "nested_extra", "missing_source"])
def test_invalid_replacement_cannot_save(api, change):
    client, repository = api
    body = payload(True)
    if change == "duplicate":
        body["rules"].append(deepcopy(body["rules"][0]))
    elif change == "citation":
        body["rules"][0]["source_sentence"] = "private absent text"
    elif change == "removed_citation_text":
        body["guideline_text"] = "Other guidelines."
    elif change == "origin":
        body["rules"][0].update(origin="reviewer", source_sentence=None)
    elif change == "new_extracted":
        body["rules"][0]["rule_id"] = "new"
    elif change == "nested_extra":
        body["rules"][0]["unexpected"] = True
    else:
        body["rules"][0]["source_sentence"] = None
    assert_failure(client.put(BASE, json=body), 422, "VALIDATION_ERROR")
    repository.save_rulebook_review.assert_not_called()

@pytest.mark.parametrize("rule_state", ["draft", "rejected", "empty", "mixed"])
def test_unready_confirmation_never_writes(api, rule_state):
    client, repository = api
    current = make_book(rule_state="draft" if rule_state in {"empty", "mixed"} else rule_state)
    if rule_state == "empty":
        current.rules = []
    elif rule_state == "mixed":
        current.rules.append(Rule(rule_id="other", description="Other", origin="reviewer", confirmation_state="confirmed"))
    repository.get_rulebook.side_effect = lambda *args, **kwargs: current.model_copy(deep=True)
    assert_failure(client.post(BASE + "/confirm", json=payload()), 409, "RULEBOOK_NOT_READY")
    repository.save_rulebook_review.assert_not_called()

@pytest.mark.parametrize("state", ["confirmed", "rejected"])
@pytest.mark.parametrize("method,suffix", [("put", ""), ("post", "/confirm"), ("post", "/reject")])
def test_final_book_cannot_be_edited_confirmed_or_rejected(api, state, method, suffix):
    client, repository = api
    current = make_book(state, "confirmed")
    repository.get_rulebook.side_effect = lambda *args, **kwargs: current.model_copy(deep=True)
    assert_failure(getattr(client, method)(BASE + suffix, json=payload(method == "put")), 409, "INVALID_RULEBOOK_STATE")
    repository.save_rulebook_review.assert_not_called()

def test_draft_cannot_be_revised(api):
    client, repository = api
    assert_failure(client.post(BASE + "/revisions", json=payload()), 409, "INVALID_RULEBOOK_STATE")
    repository.save_rulebook_review.assert_not_called()

def test_replacement_removes_omitted_rules_and_keeps_book_draft(api):
    client, repository = api
    body = payload(True)
    body["rules"] = []
    response = client.put(BASE, json=body)
    assert response.status_code == 200
    assert response.json()["rules"] == [] and response.json()["confirmation_state"] == "draft"
    assert repository.save_rulebook_review.call_count == 1
    assert repository.save_rulebook_review.call_args.kwargs["expected_version"] == 1

@pytest.mark.parametrize("stage", ["load", "save"])
def test_generic_pydantic_errors_outside_content_boundary_remain_500(api, stage):
    client, repository = api
    with pytest.raises(ValidationError) as caught:
        RulebookAction(expected_version=0, reviewer_id="private-reviewer")
    if stage == "load":
        repository.get_rulebook.side_effect = caught.value
    else:
        repository.save_rulebook_review.side_effect = caught.value
    assert_failure(client.put(BASE, json=payload(True)), 500, "INTERNAL_ERROR")
    assert repository.save_rulebook_review.call_count == (0 if stage == "load" else 1)

def test_content_boundary_preserves_python_validationerror_compatibility(api):
    _, repository = api
    request = RulebookReplace.model_validate_json(json.dumps(payload(True)))
    request.rules.append(request.rules[0].model_copy(deep=True))
    with pytest.raises(ValidationError) as caught:
        rulebooks.save_rulebook_review_service("book_1", request, repository=repository)
    assert isinstance(caught.value, RulebookContentValidationError)
    assert caught.value.errors()
    repository.save_rulebook_review.assert_not_called()

def test_malformed_json_and_missing_required_bodies_remain_safe(api):
    client, repository = api
    for method, suffix, _, model, _ in OPERATIONS:
        assert_failure(getattr(client, method)(BASE + suffix), 422, "VALIDATION_ERROR")
        assert_failure(getattr(client, method)(BASE + suffix, content="{", headers={"Content-Type": "application/json"}), 422, "VALIDATION_ERROR")
    assert not repository.method_calls

@pytest.mark.parametrize("method,suffix,service_name,model,status", OPERATIONS)
@pytest.mark.parametrize("failure", ["stale", "storage"])
def test_write_failures_are_not_retried_or_reported_as_success(api, method, suffix, service_name, model, status, failure):
    client, repository = api
    if suffix == "/revisions":
        final = make_book("rejected", "draft")
        repository.get_rulebook.side_effect = lambda *args, **kwargs: final.model_copy(deep=True)
    elif suffix == "/confirm":
        ready = make_book("draft", "confirmed")
        repository.get_rulebook.side_effect = lambda *args, **kwargs: ready.model_copy(deep=True)
    error = (RulebookVersionConflictError("private-book", 1, 2) if failure == "stale"
             else RulebookStorageError("private SQL commit failure"))
    repository.save_rulebook_review.side_effect = error
    response = getattr(client, method)(BASE + suffix, json=payload(model is RulebookReplace))
    assert_failure(response, 409 if failure == "stale" else 503,
                   "VERSION_CONFLICT" if failure == "stale" else "DEPENDENCY_UNAVAILABLE")
    assert repository.save_rulebook_review.call_count == 1
    assert repository.get_rulebook.call_count == 1

@pytest.mark.parametrize("method,suffix,service_name,model,status", OPERATIONS)
def test_invalid_service_response_is_safe_500(api, monkeypatch, method, suffix, service_name, model, status):
    client, _ = api
    service = Mock(return_value={"rulebook_id": "private-broken-response"})
    monkeypatch.setattr(rulebooks, service_name, service)
    assert_failure(getattr(client, method)(BASE + suffix, json=payload(model is RulebookReplace)), 500, "INTERNAL_ERROR")
    service.assert_called_once()

@pytest.mark.parametrize("field", ["expected_version", "reviewer_id", "guideline_text", "rules"])
def test_replacement_requires_the_complete_snapshot_and_attribution(api, field):
    client, repository = api
    body = payload(True)
    del body[field]
    assert_failure(client.put(BASE, json=body), 422, "VALIDATION_ERROR")
    assert not repository.method_calls
