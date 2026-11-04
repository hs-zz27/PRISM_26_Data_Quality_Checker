"""Opt-in durable manual creation through HTTP and isolated PostgreSQL schemas.

Author only. Do not run until PostgreSQL test execution is explicitly authorized.
"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from backend.app.main import create_app
from backend.tests.display_name_helpers import document, documents
from backend.app.models.audit_read import ErrorResponse
from backend.app.routers.rulebooks import get_rulebook_repository
from backend.app.services import rulebook_creation
from backend.app.storage.database import database_connection
from backend.app.storage.repository import RulebookNotFoundError, RulebookRepository
# Reuse the established schema lifecycle; never target application tables.
from backend.tests.test_repository import database_settings, empty_database_settings  # noqa: F401

BASE = "/api/v1/rulebooks"
SOURCE = " \tUse person labels.\r\n人 café e\u0301 🙂\n "

@pytest.fixture
def store(database_settings):
    return RulebookRepository(settings=database_settings)

@pytest.fixture
def client(store):
    app = create_app()
    app.dependency_overrides[get_rulebook_repository] = lambda: store
    try:
        with TestClient(app, raise_server_exceptions=False) as http:
            yield http
    finally:
        app.dependency_overrides.clear()

def payload():
    return {"guideline_text": SOURCE, "rules": [{
        "rule_id": "manual", "description": "Use person labels.", "check_name": "future_check",
        "parameters": {"nested": [None, True, 0, {"label": "人"}]},
        "source_sentence": "Use person labels.",
    }]}

def create(client, body=None):
    response = client.post(BASE, json=payload() if body is None else body)
    assert response.status_code == 201, response.text
    return document(response.json())

def path(book):
    return BASE + "/" + book["rulebook_id"]

def action(version, reviewer="reviewer_create"):
    return {"expected_version": version, "reviewer_id": reviewer}

def replacement(book):
    body = {**action(book["version"]), "guideline_text": book["guideline_text"],
            "rules": deepcopy(book["rules"])}
    for rule in body["rules"]:
        rule["confirmation_state"] = "confirmed"
    return body

def fingerprint(store, identifier):
    return (store.list_rulebook_versions(identifier, limit=100).model_dump(mode="json"),
            store.list_rulebook_reviews(identifier, limit=100).model_dump(mode="json"))

def row_counts(store):
    with database_connection(store.settings, readonly=True) as connection:
        return (connection.execute("SELECT COUNT(*) AS total FROM rulebook_versions").fetchone()["total"],
                connection.execute("SELECT COUNT(*) AS total FROM rulebook_reviews").fetchone()["total"])

def assert_error(response, status, code):
    assert response.status_code == status
    error = ErrorResponse.model_validate(response.json()).error
    assert error.code == code and UUID(hex=error.request_id)
    for marker in ("private", "SQL", "password", "/internal/path"):
        assert marker not in response.text

def test_post_is_durable_across_repositories_get_list_and_history(client, store):
    body = payload()
    original = create(client, body)
    reopened = RulebookRepository(settings=store.settings)
    assert reopened.get_rulebook(original["rulebook_id"], version=1).model_dump(mode="json") == original
    assert document(client.get(path(original)).json()) == original
    assert document(client.get(path(original), params={"version": 1}).json()) == original
    assert documents(client.get(BASE).json()["items"]) == [original]
    versions = client.get(path(original) + "/versions").json()
    assert documents(versions["items"]) == [original] and versions["total"] == 1
    reviews = client.get(path(original) + "/reviews").json()
    assert reviews["items"] == [] and reviews["total"] == 0
    assert reopened.list_rulebook_reviews(original["rulebook_id"]).items == []
    assert original["guideline_text"] == SOURCE
    assert original["version"] == 1 and original["confirmation_state"] == "draft"
    assert original["rules"][0]["origin"] == "reviewer"
    assert original["rules"][0]["confirmation_state"] == "draft"
    assert {key: original["rules"][0][key] for key in body["rules"][0]} == body["rules"][0]
    body["rules"][0]["parameters"]["nested"][-1]["label"] = "changed"
    assert reopened.get_rulebook(original["rulebook_id"]).model_dump(mode="json") == original
    assert row_counts(store) == (1, 0)

def test_create_edit_confirm_appends_only_real_review_actions(client, store):
    v1 = create(client)
    target = path(v1)
    response = client.put(target, json=replacement(v1))
    assert response.status_code == 200
    v2 = document(response.json())
    assert v2["version"] == 2 and v2["confirmation_state"] == "draft"
    response = client.post(target + "/confirm", json=action(2, "confirmer"))
    assert response.status_code == 200
    v3 = document(response.json())
    assert v3["version"] == 3 and v3["confirmation_state"] == "confirmed"
    reopened = RulebookRepository(settings=store.settings)
    assert [item.model_dump(mode="json") for item in reopened.list_rulebook_versions(v1["rulebook_id"]).items] == [v1, v2, v3]
    for version, saved in enumerate((v1, v2, v3), 1):
        assert document(client.get(target, params={"version": version}).json()) == saved
    history = reopened.list_rulebook_reviews(v1["rulebook_id"])
    assert [(record.version, record.action.value, record.reviewer_id) for record in history.items] == [
        (2, "edit", "reviewer_create"), (3, "confirm", "confirmer"),
    ]
    assert all(record.created_at for record in history.items)
    assert row_counts(store) == (3, 2)
    before = fingerprint(store, v1["rulebook_id"])
    assert_error(client.post(target + "/reject", json=action(2)), 409, "VERSION_CONFLICT")
    assert_error(client.post(target + "/reject", json=action(3)), 409, "INVALID_RULEBOOK_STATE")
    assert fingerprint(store, v1["rulebook_id"]) == before

@pytest.mark.parametrize("body", [{"guideline_text": ""}, {"guideline_text": "", "rules": []}])
def test_empty_creation_succeeds_but_confirmation_does_not_write(client, store, body):
    original = create(client, body)
    assert original["rules"] == [] and original["confirmation_state"] == "draft"
    before = fingerprint(store, original["rulebook_id"])
    assert_error(client.post(path(original) + "/confirm", json=action(1)), 409, "RULEBOOK_NOT_READY")
    assert fingerprint(store, original["rulebook_id"]) == before
    assert row_counts(store) == (1, 0)

@pytest.mark.parametrize("change", ["duplicate", "citation", "metadata", "null_rules"])
def test_invalid_creation_leaves_no_snapshot_or_review(client, store, change):
    body = payload()
    if change == "duplicate":
        body["rules"].append(deepcopy(body["rules"][0]))
    elif change == "citation":
        body["rules"][0]["source_sentence"] = "private invented citation"
    elif change == "metadata":
        body["rules"][0]["confirmation_state"] = "confirmed"
    else:
        body["rules"] = None
    assert_error(client.post(BASE, json=body), 422, "VALIDATION_ERROR")
    assert documents(client.get(BASE).json()["items"]) == []
    assert row_counts(store) == (0, 0)

@pytest.mark.parametrize("failure", ["insert", "commit"])
def test_failed_transaction_never_succeeds_or_leaves_creation_rows(client, store, monkeypatch, failure):
    fixed = UUID("12345678-1234-4234-8123-1234567890ab")
    identifier = f"rulebook_{fixed.hex}"
    monkeypatch.setattr(rulebook_creation, "uuid4", lambda: fixed)
    # Same failure-injection pattern as test_repository.py. Deferred failure is
    # a server rejection with a known rollback outcome, not a lost commit reply.
    with database_connection(store.settings) as connection:
        connection.execute("""
            CREATE FUNCTION reject_manual_creation() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'private SQL password /internal/path';
            END;
            $$
        """)
        statement = (
            "CREATE TRIGGER block_manual_creation BEFORE INSERT ON rulebook_versions "
            "FOR EACH ROW EXECUTE FUNCTION reject_manual_creation()"
            if failure == "insert" else
            "CREATE CONSTRAINT TRIGGER block_manual_creation AFTER INSERT ON rulebook_versions "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION reject_manual_creation()"
        )
        connection.execute(statement)
    assert_error(client.post(BASE, json=payload()), 503, "DEPENDENCY_UNAVAILABLE")
    reopened = RulebookRepository(settings=store.settings)
    with pytest.raises(RulebookNotFoundError):
        reopened.get_rulebook(identifier)
    assert_error(client.get(BASE + "/" + identifier + "/reviews"), 404, "NOT_FOUND")
    assert documents(client.get(BASE).json()["items"]) == [] and row_counts(store) == (0, 0)
    with database_connection(store.settings) as connection:
        connection.execute("DROP TRIGGER block_manual_creation ON rulebook_versions")
    # A later explicit request after a known rollback must not inherit a partial
    # snapshot or a leaked transaction lock. The application itself never retries.
    recovered = create(client)
    assert recovered["rulebook_id"] == identifier and recovered["version"] == 1
    assert reopened.get_rulebook(identifier).model_dump(mode="json") == recovered
    assert reopened.list_rulebook_reviews(identifier).items == []
    assert row_counts(store) == (1, 0)

@pytest.mark.parametrize("latest_version", [1, 3])
def test_collision_preserves_all_existing_versions_and_reviews(client, store, monkeypatch, latest_version):
    v1 = create(client)
    target = path(v1)
    if latest_version == 3:
        assert client.put(target, json=replacement(v1)).status_code == 200
        assert client.post(target + "/confirm", json=action(2)).status_code == 200
    before = fingerprint(store, v1["rulebook_id"])
    fixed = UUID(hex=v1["rulebook_id"].removeprefix("rulebook_"))
    monkeypatch.setattr(rulebook_creation, "uuid4", lambda: fixed)
    assert_error(client.post(BASE, json={"guideline_text": "new content", "rules": []}), 409, "VERSION_CONFLICT")
    assert fingerprint(store, v1["rulebook_id"]) == before
    assert document(client.get(target, params={"version": 1}).json()) == v1
    assert row_counts(store) == (latest_version, latest_version - 1)

def test_missing_versions_rejection_revision_and_stale_actions_stay_unchanged(client, store):
    original = create(client)
    target = path(original)
    before = fingerprint(store, original["rulebook_id"])
    assert_error(client.get(target, params={"version": 999}), 404, "NOT_FOUND")
    assert_error(client.get(BASE + "/missing"), 404, "NOT_FOUND")
    assert_error(client.post(target + "/revisions", json=action(1)), 409, "INVALID_RULEBOOK_STATE")
    assert fingerprint(store, original["rulebook_id"]) == before
    rejected = client.post(target + "/reject", json=action(1))
    assert rejected.status_code == 200
    assert rejected.json()["version"] == 2 and rejected.json()["rules"] == original["rules"]
    before = fingerprint(store, original["rulebook_id"])
    assert_error(client.put(target, json=replacement(original)), 409, "VERSION_CONFLICT")
    assert fingerprint(store, original["rulebook_id"]) == before
    revised = client.post(target + "/revisions", json=action(2))
    assert revised.status_code == 201 and revised.json()["version"] == 3
    assert revised.json()["confirmation_state"] == "draft"
    assert all(rule["confirmation_state"] == "draft" for rule in revised.json()["rules"])
    assert document(client.get(target, params={"version": 1}).json()) == original
    assert [record["action"] for record in client.get(target + "/reviews").json()["items"]] == ["reject", "revise"]

def test_separate_successful_posts_do_not_reuse_ids_or_records(client, store):
    first, second = create(client), create(client)
    assert first["rulebook_id"] != second["rulebook_id"]
    assert client.put(path(first), json=replacement(first)).status_code == 200
    assert document(client.get(path(second)).json()) == second
    assert store.list_rulebook_reviews(second["rulebook_id"]).items == []
    assert row_counts(store) == (3, 1)

def test_simultaneous_generated_id_collision_has_exactly_one_initial_snapshot(store, monkeypatch):
    fixed = UUID("12345678-1234-4234-8123-1234567890ab")
    identifier = f"rulebook_{fixed.hex}"
    monkeypatch.setattr(rulebook_creation, "uuid4", lambda: fixed)
    rendezvous = Barrier(2)
    original_save = RulebookRepository.save_rulebook

    def synchronized_save(repository, draft, *, expected_version=0):
        assert expected_version == 0 and draft.version == 1
        rendezvous.wait(timeout=30)
        return original_save(repository, draft, expected_version=expected_version)

    monkeypatch.setattr(RulebookRepository, "save_rulebook", synchronized_save)

    def post_draft(index):
        # Independent apps, clients and repositories; only the isolated schema
        # and forced UUID are shared. No sleeps or production locking changes.
        repository = RulebookRepository(settings=store.settings)
        app = create_app()
        app.dependency_overrides[get_rulebook_repository] = lambda: repository
        try:
            with TestClient(app, raise_server_exceptions=False) as http:
                return http.post(BASE, json={"guideline_text": f"contender {index}", "rules": []})
        finally:
            app.dependency_overrides.clear()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(post_draft, index) for index in (1, 2)]
        responses = [future.result(timeout=60) for future in futures]
    assert sorted(response.status_code for response in responses) == [201, 409]
    winner = next(response.json() for response in responses if response.status_code == 201)
    # The rejected contender committed nothing, so it took no number.
    assert winner.pop("name")["label"] == "Rules 001"
    assert winner["rulebook_id"] == identifier and winner["version"] == 1
    assert winner["guideline_text"] in {"contender 1", "contender 2"}
    assert winner["rules"] == [] and winner["confirmation_state"] == "draft"
    assert_error(next(response for response in responses if response.status_code == 409), 409, "VERSION_CONFLICT")
    reopened = RulebookRepository(settings=store.settings)
    assert reopened.get_rulebook(identifier).model_dump(mode="json") == winner
    assert [book.model_dump(mode="json") for book in reopened.list_rulebook_versions(identifier).items] == [winner]
    assert reopened.list_rulebook_reviews(identifier).items == []
    assert row_counts(store) == (1, 0)
