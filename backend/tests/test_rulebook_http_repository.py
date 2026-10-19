"""Opt-in PostgreSQL HTTP workflow using existing isolated-schema fixtures.

Author only: do not execute without separate permission to run database tests.
Repository transaction/concurrency regressions remain in test_repository.py.
"""

from copy import deepcopy

from fastapi.testclient import TestClient
import pytest

from backend.app.main import create_app
from backend.app.models.rule import Rule, Rulebook
from backend.app.routers.rulebooks import get_rulebook_repository
from backend.app.storage.repository import RulebookRepository
# Reuse the established isolated-schema lifecycle rather than a second system.
from backend.tests.test_repository import database_settings, empty_database_settings  # noqa: F401

BASE = "/api/v1/rulebooks/book_http"

@pytest.fixture
def store(database_settings):
    return RulebookRepository(settings=database_settings)

@pytest.fixture
def client(store):
    app = create_app()
    app.dependency_overrides[get_rulebook_repository] = lambda: store
    with TestClient(app, raise_server_exceptions=False) as http:
        yield http
    app.dependency_overrides.clear()

def seed(store, identifier="book_http", empty=False):
    return store.save_rulebook(Rulebook(
        rulebook_id=identifier, guideline_text="Use person labels.",
        rules=[] if empty else [Rule(rule_id="label", description="Use person labels.",
                                    origin="extracted", source_sentence="Use person labels.",
                                    check_name="future_supported_check")],
    ))

def fingerprint(store):
    return (store.list_rulebook_versions("book_http", limit=100).model_dump(mode="json"),
            store.list_rulebook_reviews("book_http", limit=100).model_dump(mode="json"))

def action(version, reviewer="reviewer_http"):
    return {"expected_version": version, "reviewer_id": reviewer}

def replacement(book, *, version=None):
    return {**action(book["version"] if version is None else version),
            "guideline_text": book["guideline_text"], "rules": deepcopy(book["rules"])}

def test_full_http_review_cycle_preserves_snapshots_and_attribution(client, store):
    v1 = seed(store).model_dump(mode="json")
    assert client.get(BASE).json() == v1
    assert client.get(BASE + "/reviews").json()["items"] == []
    body = replacement(v1)
    body["rules"][0]["confirmation_state"] = "confirmed"
    response = client.put(BASE, json=body)
    assert response.status_code == 200
    v2 = response.json()
    assert v2["version"] == 2 and v2["confirmation_state"] == "draft"
    response = client.post(BASE + "/confirm", json=action(2, "confirmer"))
    assert response.status_code == 200
    v3 = response.json()
    assert v3["version"] == 3 and v3["confirmation_state"] == "confirmed"
    for version, snapshot in [(1, v1), (2, v2)]:
        assert client.get(BASE, params={"version": version}).json() == snapshot
    before = fingerprint(store)
    response = client.post(BASE + "/reject", json=action(2))
    assert response.status_code == 409 and response.json()["error"]["code"] == "VERSION_CONFLICT"
    assert fingerprint(store) == before
    response = client.post(BASE + "/revisions", json=action(3, "reviser"))
    assert response.status_code == 201
    v4 = response.json()
    assert v4["version"] == 4 and v4["confirmation_state"] == "draft"
    assert all(rule["confirmation_state"] == "draft" for rule in v4["rules"])
    assert v4["rules"][0]["check_name"] == "future_supported_check"
    response = client.post(BASE + "/reject", json=action(4, "rejecter"))
    assert response.status_code == 200
    v5 = response.json()
    assert v5["version"] == 5 and v5["confirmation_state"] == "rejected"
    assert v5["rules"] == v4["rules"]
    response = client.post(BASE + "/revisions", json=action(5, "reopener"))
    assert response.status_code == 201
    v6 = response.json()
    assert v6["version"] == 6 and v6["confirmation_state"] == "draft"
    assert all(rule["confirmation_state"] == "draft" for rule in v6["rules"])
    for version, snapshot in enumerate([v1, v2, v3, v4, v5, v6], 1):
        assert client.get(BASE, params={"version": version}).json() == snapshot
    history = client.get(BASE + "/reviews").json()["items"]
    assert [record["version"] for record in history] == [2, 3, 4, 5, 6]
    assert [record["action"] for record in history] == ["edit", "confirm", "revise", "reject", "revise"]
    assert [record["reviewer_id"] for record in history] == ["reviewer_http", "confirmer", "reviser", "rejecter", "reopener"]
    assert all(record["created_at"] for record in history)
    assert [book["version"] for book in client.get(BASE + "/versions").json()["items"]] == list(range(1, 7))
    assert client.get("/api/v1/rulebooks").json()["items"] == [v6]
    for suffix in ["/versions", "/reviews"]:
        beyond = client.get(BASE + suffix, params={"offset": 100}).json()
        assert beyond["items"] == [] and beyond["next_offset"] is None
    first = client.get(BASE + "/versions?limit=2").json()
    assert first["next_offset"] == 2 and [b["version"] for b in first["items"]] == [1, 2]
    second = client.get(BASE + "/versions?limit=2&offset=2").json()
    assert [b["version"] for b in second["items"]] == [3, 4]

@pytest.mark.parametrize("empty", [False, True])
def test_unready_or_empty_confirmation_and_draft_revision_do_not_write(client, store, empty):
    seed(store, empty=empty)
    before = fingerprint(store)
    for suffix, code in [("/confirm", "RULEBOOK_NOT_READY"), ("/revisions", "INVALID_RULEBOOK_STATE")]:
        response = client.post(BASE + suffix, json=action(1))
        assert response.status_code == 409 and response.json()["error"]["code"] == code
        assert fingerprint(store) == before

@pytest.mark.parametrize("final", ["confirmed", "rejected"])
def test_final_state_edits_and_actions_fail_without_writes(client, store, final):
    original = seed(store).model_dump(mode="json")
    if final == "confirmed":
        body = replacement(original)
        body["rules"][0]["confirmation_state"] = "confirmed"
        assert client.put(BASE, json=body).status_code == 200
        assert client.post(BASE + "/confirm", json=action(2)).status_code == 200
        version = 3
    else:
        assert client.post(BASE + "/reject", json=action(1)).status_code == 200
        version = 2
    before = fingerprint(store)
    for method, suffix, body in [("put", "", replacement(original, version=version)),
                                  ("post", "/confirm", action(version)), ("post", "/reject", action(version))]:
        response = getattr(client, method)(BASE + suffix, json=body)
        assert response.status_code == 409 and response.json()["error"]["code"] == "INVALID_RULEBOOK_STATE"
        assert fingerprint(store) == before

@pytest.mark.parametrize("change", ["duplicate", "citation", "origin"])
def test_content_policy_failure_does_not_write_or_record_history(client, store, change):
    original = seed(store).model_dump(mode="json")
    body = replacement(original)
    if change == "duplicate":
        body["rules"].append(deepcopy(body["rules"][0]))
    elif change == "citation":
        body["rules"][0]["source_sentence"] = "private nonmatching citation"
    else:
        body["rules"][0].update(origin="reviewer", source_sentence=None)
    before = fingerprint(store)
    response = client.put(BASE, json=body)
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "private" not in response.text
    assert fingerprint(store) == before

def test_empty_collections_real_empty_history_and_missing_explicit_versions(client, store):
    assert client.get("/api/v1/rulebooks").json()["items"] == []
    for suffix in ["", "/versions", "/reviews"]:
        response = client.get(BASE + suffix)
        assert response.status_code == 404
    for method, suffix in [("put", ""), ("post", "/confirm"), ("post", "/reject"), ("post", "/revisions")]:
        body = action(1)
        if method == "put":
            body.update(guideline_text="", rules=[])
        response = getattr(client, method)(BASE + suffix, json=body)
        assert response.status_code == 404
        assert client.get("/api/v1/rulebooks").json()["items"] == []
    original = seed(store)
    # Historical low-level saves must not fabricate a reviewer record.
    second = original.model_copy(deep=True, update={"version": 2})
    store.save_rulebook(second, expected_version=1)
    assert client.get(BASE + "/reviews").json()["items"] == []
    assert client.get(BASE + "?version=999").status_code == 404
    assert client.get(BASE).json()["version"] == 2
    assert client.get(BASE + "?version=1").json() == original.model_dump(mode="json")
    assert client.get("/api/v1/rulebooks?offset=999").json()["items"] == []
    for method, suffix, body in [("put", "", replacement(original.model_dump(mode="json"))),
                                  ("post", "/confirm", action(1)), ("post", "/reject", action(1)),
                                  ("post", "/revisions", action(1))]:
        before = fingerprint(store)
        response = getattr(client, method)(BASE + suffix, json=body)
        assert response.status_code == 409 and response.json()["error"]["code"] == "VERSION_CONFLICT"
        assert fingerprint(store) == before

def test_latest_collection_contains_each_book_once_in_repository_order(client, store):
    original = seed(store, identifier="z_book")
    seed(store, identifier="a_book")
    store.save_rulebook(original.model_copy(deep=True, update={"version": 2}), expected_version=1)
    response = client.get("/api/v1/rulebooks?limit=1")
    assert response.status_code == 200
    first = response.json()
    assert first["total"] == 2 and first["next_offset"] == 1
    assert [book["rulebook_id"] for book in first["items"]] == ["a_book"]
    second = client.get("/api/v1/rulebooks?limit=1&offset=1").json()
    assert [book["rulebook_id"] for book in second["items"]] == ["z_book"]
    assert second["items"][0]["version"] == 2 and second["next_offset"] is None
