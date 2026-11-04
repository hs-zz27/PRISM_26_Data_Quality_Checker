"""Thin read delegation, boundary validation, safe errors and registration."""

from datetime import UTC, datetime
from unittest.mock import Mock, call
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from backend.app.main import create_app
from backend.tests.display_name_helpers import without_display_names
from backend.app.models.audit_read import ErrorResponse
from backend.app.models.review import RulebookReviewPage, RulebookReviewRecord
from backend.app.models.rule import ConfirmationState, Rule, Rulebook, RulebookPage
from backend.app.routers.rulebooks import get_rulebook_repository
from backend.app.storage.repository import RulebookNotFoundError, RulebookRepository, RulebookStorageError

@pytest.fixture
def api():
    app = without_display_names(create_app())
    repository = Mock(spec_set=RulebookRepository)
    app.dependency_overrides[get_rulebook_repository] = lambda: repository
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, repository
    app.dependency_overrides.clear()

def book(version=1, state=ConfirmationState.DRAFT, identifier="book_1"):
    return Rulebook(
        rulebook_id=identifier, version=version, guideline_text="Review every image.",
        rules=[Rule(rule_id="manual", description="Review every image.", origin="reviewer",
                    check_name="not_implemented", confirmation_state=state)],
        confirmation_state=state,
    )

READS = [
    ("/api/v1/rulebooks", "list_rulebooks", None),
    ("/api/v1/rulebooks/book_1/versions", "list_rulebook_versions", "book_1"),
    ("/api/v1/rulebooks/book_1/reviews", "list_rulebook_reviews", "book_1"),
]

@pytest.mark.parametrize("path,method,identifier", READS)
@pytest.mark.parametrize("limit,offset", [(20, 0), (1, 0), (100, 999)])
def test_empty_pages_and_pagination_forwarding(api, path, method, identifier, limit, offset):
    client, repository = api
    model = RulebookReviewPage if method.endswith("reviews") else RulebookPage
    expected = model(items=[], limit=limit, offset=offset, total=0, next_offset=None)
    getattr(repository, method).return_value = expected
    params = {} if (limit, offset) == (20, 0) else {"limit": limit, "offset": offset}
    response = client.get(path, params=params)
    assert response.status_code == 200
    assert response.json() == expected.model_dump(mode="json")
    args = () if identifier is None else (identifier,)
    assert repository.method_calls == [getattr(call, method)(*args, limit=limit, offset=offset)]

@pytest.mark.parametrize("state", list(ConfirmationState))
@pytest.mark.parametrize("version", [None, 1, 9])
def test_latest_and_historical_read_forwarding(api, state, version):
    client, repository = api
    expected = book(version=version or 9, state=state)
    repository.get_rulebook.return_value = expected
    response = client.get("/api/v1/rulebooks/book_1", params={} if version is None else {"version": version})
    assert response.status_code == 200
    assert response.json() == expected.model_dump(mode="json")
    assert repository.method_calls == [call.get_rulebook("book_1", version=version)]

def test_lists_preserve_repository_order_and_history(api):
    client, repository = api
    latest = RulebookPage(items=[book(7, identifier="z"), book(3, identifier="a")],
                          limit=20, offset=0, total=2)
    repository.list_rulebooks.return_value = latest
    assert client.get("/api/v1/rulebooks").json() == latest.model_dump(mode="json")
    versions = RulebookPage(items=[book(1), book(2), book(3, ConfirmationState.REJECTED)],
                            limit=20, offset=0, total=3)
    repository.list_rulebook_versions.return_value = versions
    assert client.get("/api/v1/rulebooks/book_1/versions").json() == versions.model_dump(mode="json")
    assert repository.method_calls == [call.list_rulebooks(limit=20, offset=0),
                                       call.list_rulebook_versions("book_1", limit=20, offset=0)]

def test_real_review_attribution_is_not_invented(api):
    client, repository = api
    expected = RulebookReviewPage(
        items=[RulebookReviewRecord(rulebook_id="book_1", version=2, reviewer_id="reviewer_1",
                                    action="edit", created_at=datetime(2026, 10, 19, tzinfo=UTC))],
        limit=20, offset=0, total=1,
    )
    repository.list_rulebook_reviews.return_value = expected
    response = client.get("/api/v1/rulebooks/book_1/reviews")
    assert response.status_code == 200
    assert response.json() == expected.model_dump(mode="json")
    assert repository.method_calls == [call.list_rulebook_reviews("book_1", limit=20, offset=0)]

@pytest.mark.parametrize("path,method,identifier", READS)
@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"limit": "true"},
                                    {"offset": -1}, {"offset": "1.5"}])
def test_invalid_pagination_never_calls_storage(api, path, method, identifier, params):
    client, repository = api
    response = client.get(path, params=params)
    assert response.status_code == 422
    assert ErrorResponse.model_validate(response.json()).error.code == "VALIDATION_ERROR"
    assert not repository.method_calls

@pytest.mark.parametrize("version", [0, -1, "true", "1.5", "", "null"])
def test_invalid_explicit_version_never_reads(api, version):
    client, repository = api
    assert client.get("/api/v1/rulebooks/book_1", params={"version": version}).status_code == 422
    assert not repository.method_calls

@pytest.mark.parametrize("suffix", ["", "/versions", "/reviews"])
def test_blank_identifiers_never_read(api, suffix):
    client, repository = api
    assert client.get("/api/v1/rulebooks/%20%09" + suffix).status_code == 422
    assert not repository.method_calls

@pytest.mark.parametrize("identifier", ["livre-é", "book.with-dash_1", "book with space"])
def test_nonblank_identifiers_are_not_overrestricted(api, identifier):
    client, repository = api
    repository.get_rulebook.return_value = book(identifier=identifier)
    assert client.get("/api/v1/rulebooks/" + identifier).status_code == 200
    repository.get_rulebook.assert_called_once_with(identifier, version=None)

@pytest.mark.parametrize("path,method,identifier", READS + [("/api/v1/rulebooks/book_1", "get_rulebook", "book_1")])
@pytest.mark.parametrize("kind,status,code", [("missing", 404, "NOT_FOUND"),
                                             ("storage", 503, "DEPENDENCY_UNAVAILABLE"),
                                             ("internal", 500, "INTERNAL_ERROR")])
def test_safe_read_failures(api, path, method, identifier, kind, status, code):
    client, repository = api
    error = {"missing": RulebookNotFoundError("private-book", 999),
             "storage": RulebookStorageError("private SQL connection password /private/file"),
             "internal": ValueError("private programming defect")}[kind]
    getattr(repository, method).side_effect = error
    response = client.get(path)
    assert response.status_code == status
    payload = ErrorResponse.model_validate(response.json()).error
    assert payload.code == code and UUID(hex=payload.request_id)
    assert payload.details == []
    assert "private" not in response.text and "SQL" not in response.text
    assert str(error) not in response.text
    assert len(repository.method_calls) == 1

def test_missing_explicit_version_never_falls_back(api):
    client, repository = api
    repository.get_rulebook.side_effect = RulebookNotFoundError("book_1", 999)
    assert client.get("/api/v1/rulebooks/book_1?version=999").status_code == 404
    assert repository.method_calls == [call.get_rulebook("book_1", version=999)]

def test_invalid_stored_response_is_internal_error(api):
    client, repository = api
    repository.get_rulebook.return_value = {"rulebook_id": "private-book"}
    response = client.get("/api/v1/rulebooks/book_1")
    assert response.status_code == 500 and response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "private" not in response.text

def test_unique_read_registration_without_mutation_conflicts(api):
    client, _ = api
    spec = client.get("/openapi.json").json()
    expected = {"listRulebooks", "getRulebook", "listRulebookVersions", "listRulebookReviews"}
    operations = [op for path in spec["paths"].values() for op in path.values()]
    identifiers = [op["operationId"] for op in operations]
    assert all(identifiers.count(name) == 1 for name in expected)
    for op in operations:
        if op["operationId"] in expected:
            assert set(op["responses"]) == {"200", "404", "422", "500", "503"}
