"""Permanent display names: formatting, allocation, backfill, concurrency and title edits.

Database tests are opt-in through PRISM_TEST_DATABASE_URL and use isolated schemas.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from pydantic import ValidationError
import pytest

from backend.app.main import create_app
from backend.app.models.audit import AuditRun
from backend.app.models.correction import RecheckRequest
from backend.app.models.display_names import TitleUpdate, audit_label, rulebook_label
from backend.app.models.feedback import fingerprint
from backend.app.models.job import JobKind
from backend.app.models.rule import Rule, Rulebook, RulebookRecord
from backend.app.routers.audits import get_audit_read_repository, get_audit_repository
from backend.app.routers.display_names import get_display_name_repository
from backend.app.routers.rechecks import get_recheck_repository
from backend.app.routers.rulebooks import get_rulebook_repository
from backend.app.storage import database
from backend.app.storage.database import DatabaseError, database_connection
from backend.app.storage.display_name_repository import DisplayNameRepository
from backend.app.storage.recheck_repository import RecheckRepository
from backend.app.storage.repository import RulebookRepository
from backend.app.storage.audit_read_repository import AuditReadRepository
from backend.tests.dataset_fixtures import (  # noqa: F401
    dataset_database_settings, empty_dataset_database_settings, read_sql, seed_import, write_sql,
)
from backend.tests.test_rechecks import prepared

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


# ---------------------------------------------------------------- formatting

@pytest.mark.parametrize(("number", "recheck", "label"), [
    (1, None, "Audit 001"), (14, None, "Audit 014"), (999, None, "Audit 999"), (1000, None, "Audit 1000"),
    (123456, None, "Audit 123456"), (14, 1, "Audit 014 R01"), (14, 99, "Audit 014 R99"),
    (14, 100, "Audit 014 R100"), (1000, 7, "Audit 1000 R07"),
])
def test_audit_labels(number, recheck, label):
    assert audit_label(number, recheck) == label


@pytest.mark.parametrize(("number", "version", "label"), [
    (3, None, "Rules 003"), (3, 3, "Rules 003 v3"), (1000, None, "Rules 1000"), (1000, 12, "Rules 1000 v12"),
])
def test_rulebook_labels(number, version, label):
    assert rulebook_label(number, version) == label


def update(title="Person checks", revision=0, reviewer="reviewer"):
    return {"title": title, "expected_title_revision": revision, "reviewer_id": reviewer}


def test_title_updates_are_trimmed_and_validated():
    assert TitleUpdate.model_validate(update("  LabelMe demo  ")).title == "LabelMe demo"
    assert TitleUpdate.model_validate(update("x" * 120)).title == "x" * 120
    assert TitleUpdate.model_validate(update("人 <b>&amp;</b>")).title == "人 <b>&amp;</b>"
    for invalid in [update(""), update("   "), update("x" * 121), update("two\nlines"), update("tab\there"),
                    update(revision=-1), update(revision="1"), update(revision=True), update(reviewer=""),
                    update(reviewer=" "), update(reviewer="r" * 256), {**update(), "label": "Audit 999"},
                    {**update(), "number": 5}]:
        with pytest.raises(ValidationError):
            TitleUpdate.model_validate(invalid)


def book(identifier="book_a", text="1. Person boxes must be tight.\n2. Labels must match.", version=1, **changes):
    return Rulebook(rulebook_id=identifier, version=version, guideline_text=text, rules=[Rule(
        rule_id="tight", description="Person boxes must be tight.", origin="extracted",
        source_sentence="Person boxes must be tight.", check_name="annotation_guideline",
    )], **changes)


def test_names_are_never_part_of_stored_documents():
    stored = book().model_dump(mode="json")
    # A record without a name serializes exactly like the stored document.
    assert RulebookRecord.model_validate(stored).model_dump(mode="json") == stored
    named = RulebookRecord.model_validate({**stored, "name": {
        "rulebook_id": "book_a", "number": 3, "label": "Rules 003", "title": "Person checks", "title_revision": 0}})
    assert {k: v for k, v in named.model_dump(mode="json").items() if k != "name"} == stored
    # The storage model refuses a name, so one can never be saved into a version.
    with pytest.raises(ValidationError):
        Rulebook.model_validate(named.model_dump(mode="json"))
    audit = {"audit_id": "audit_1", "job_id": "job_1", "dataset_id": "d", "rulebook_id": "b", "rulebook_version": 1,
             "status": "queued", "sample_count": 0, "processed_count": 0, "failed_count": 0,
             "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"}
    assert "name" not in AuditRun.model_validate_json(__import__("json").dumps(audit)).model_dump(mode="json")


# ---------------------------------------------------------------- seeding

def seed_audit(connection, audit_id, created_at, *, dataset_id="dataset_missing", rulebook_id="book_a", request=None):
    connection.execute(
        """INSERT INTO jobs (job_id, kind, status, progress, created_at, updated_at, resource_kind, resource_id)
           VALUES (%s, 'audit', 'queued', %s, %s, %s, 'audit', %s)""",
        (f"job_{audit_id}", Jsonb({}), created_at, created_at, audit_id))
    connection.execute(
        """INSERT INTO audit_runs (audit_id, job_id, dataset_id, dataset_root, rulebook_id, rulebook_version,
               status, request, sample_count, processed_count, created_at, updated_at)
           VALUES (%s, %s, %s, 'opaque-root', %s, 1, 'completed', %s, 0, 0, %s, %s)""",
        (audit_id, f"job_{audit_id}", dataset_id, rulebook_id, Jsonb(request or {}), created_at, created_at))


def link(connection, child, parent, created_at):
    connection.execute(
        """INSERT INTO audit_recheck_lineage (child_audit_id, parent_audit_id, reviewer_id, created_at)
           VALUES (%s, %s, 'reviewer', %s)""", (child, parent, created_at))


def seed_version(connection, rulebook, *, reviewed_at=None):
    connection.execute(
        "INSERT INTO rulebook_versions (rulebook_id, version, confirmation_state, document) VALUES (%s, %s, %s, %s)",
        (rulebook.rulebook_id, rulebook.version, rulebook.confirmation_state.value, Jsonb(rulebook.model_dump(mode="json"))))
    if reviewed_at is not None:
        connection.execute(
            "INSERT INTO rulebook_reviews (rulebook_id, version, reviewer_id, action, created_at) VALUES (%s, %s, 'r', 'edit', %s)",
            (rulebook.rulebook_id, rulebook.version, reviewed_at))


def seed_extraction(connection, rulebook_id, created_at):
    connection.execute(
        """INSERT INTO jobs (job_id, kind, status, progress, created_at, updated_at, resource_kind, resource_id)
           VALUES (%s, 'rule_extraction', 'queued', %s, %s, %s, 'rulebook', %s)""",
        (f"job_{rulebook_id}", Jsonb({}), created_at, created_at, rulebook_id))
    connection.execute(
        """INSERT INTO rule_extraction_requests (job_id, rulebook_id, guideline_text, settings, policy)
           VALUES (%s, %s, 'Scope: people.', %s, %s)""", (f"job_{rulebook_id}", rulebook_id, Jsonb({}), Jsonb({})))


def names(settings):
    audits = {row["audit_id"]: (audit_label(row["display_number"], row["recheck_number"]), row["root_audit_id"], row["title"])
              for row in read_sql(settings, "SELECT * FROM audit_names")}
    books = {row["rulebook_id"]: (rulebook_label(row["display_number"]), row["title"], row["first_recorded_at"])
             for row in read_sql(settings, "SELECT * FROM rulebook_names")}
    return audits, books


def apply_migrations_through(settings, tmp_path, monkeypatch, last, extra=None):
    directory = tmp_path / f"migrations_{last}_{extra is not None}"
    directory.mkdir()
    for source in database.MIGRATIONS_DIRECTORY.glob("[0-9][0-9][0-9]_*.sql"):
        if int(source.name[:3]) <= last:
            (directory / source.name).write_bytes(source.read_bytes())
    if extra is not None:
        (directory / extra[0]).write_text(extra[1], encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(database, "MIGRATIONS_DIRECTORY", directory)
        database.initialize_database(settings)


EXTRACTED = "rulebook_" + "b" * 32
FEEDBACK = {"reviewer_feedback": {"sample_a": {"reviewer_id": "r", "comment": "Check the edge."}}}


def seed_history(settings):
    """Existing records written before migration 016, with deliberate ties and gaps."""
    seed_import(settings, "dataset_named", started=False)
    with database_connection(settings) as connection:
        seed_version(connection, book("book_a"))
        seed_version(connection, book("book_a", "Changed first line.\nPerson boxes must be tight.", 2), reviewed_at=at(0))
        seed_version(connection, book("book_a", "Changed first line.\nPerson boxes must be tight.", 3), reviewed_at=at(30))
        seed_version(connection, book(EXTRACTED, "  Scope:   people   only.\nPerson boxes must be tight."))
        seed_extraction(connection, EXTRACTED, at(10))
        seed_version(connection, book("book_d", "Person boxes must be tight."))
        seed_version(connection, book("book_c", "\r\n   \r\n• " + "Long title " * 10 + "\r\nPerson boxes must be tight."))
        seed_audit(connection, "audit_old", at(0), dataset_id="dataset_named")
        seed_audit(connection, "audit_tie_b", at(10))
        seed_audit(connection, "audit_tie_a", at(10))
        seed_audit(connection, "recheck_b", at(20))
        seed_audit(connection, "recheck_a", at(20))
        seed_audit(connection, "recheck_of_recheck", at(30), request=FEEDBACK)
        seed_audit(connection, "recheck_tie_b", at(20))
        link(connection, "recheck_b", "audit_old", at(20))
        link(connection, "recheck_a", "audit_old", at(20))
        link(connection, "recheck_of_recheck", "recheck_a", at(30))
        link(connection, "recheck_tie_b", "audit_tie_b", at(20))


EXPECTED_AUDITS = {
    "audit_old": ("Audit 001", "audit_old", "Synthetic source"),
    "audit_tie_a": ("Audit 002", "audit_tie_a", "Untitled audit"),
    "audit_tie_b": ("Audit 003", "audit_tie_b", "Untitled audit"),
    "recheck_a": ("Audit 001 R01", "audit_old", "Recheck"),
    "recheck_b": ("Audit 001 R02", "audit_old", "Recheck"),
    "recheck_of_recheck": ("Audit 001 R03", "audit_old", "Feedback recheck"),
    "recheck_tie_b": ("Audit 003 R01", "audit_tie_b", "Recheck"),
}
EXPECTED_BOOKS = {
    "book_a": ("Rules 001", "Person boxes must be tight.", at(0)),
    EXTRACTED: ("Rules 002", "Scope: people only.", at(10)),
    "book_c": ("Rules 003", ("Long title " * 10)[:79].rstrip() + "…", None),
    "book_d": ("Rules 004", "Person boxes must be tight.", None),
}
HISTORY_TABLES = ["jobs", "audit_runs", "audit_recheck_lineage", "rulebook_versions", "rulebook_reviews",
                  "rule_extraction_requests", "datasets"]


def snapshot(settings):
    return {table: read_sql(settings, f"SELECT * FROM {table} ORDER BY 1, 2") for table in HISTORY_TABLES}


# ---------------------------------------------------------------- migration

def test_backfill_names_existing_records_deterministically_without_changing_them(empty_dataset_database_settings, tmp_path, monkeypatch):
    settings = empty_dataset_database_settings
    apply_migrations_through(settings, tmp_path, monkeypatch, 15)
    seed_history(settings)
    before = snapshot(settings)
    database.initialize_database(settings)
    audits, books = names(settings)
    assert audits == EXPECTED_AUDITS
    assert books == EXPECTED_BOOKS
    assert snapshot(settings) == before
    counters = {row["audit_id"]: row["last_recheck_number"] for row in read_sql(settings, "SELECT * FROM audit_names")}
    assert counters["audit_old"] == 3 and counters["audit_tie_b"] == 1 and counters["audit_tie_a"] == 0
    for table in ("audit_names", "rulebook_names"):
        assert all(row["title_revision"] == 0 and row["title_updated_by"] is None and row["title_updated_at"] is None
                   for row in read_sql(settings, f"SELECT * FROM {table}"))
    # Repeated initialization is a no-op.
    database.initialize_database(settings)
    assert names(settings) == (EXPECTED_AUDITS, EXPECTED_BOOKS)
    assert [row["version"] for row in read_sql(settings, "SELECT version FROM schema_migrations ORDER BY version")] == list(range(1, 17))
    # New records continue after the backfilled numbers.
    with database_connection(settings) as connection:
        seed_audit(connection, "audit_new", at(40))
        seed_audit(connection, "recheck_new", at(40))
        link(connection, "recheck_new", "recheck_of_recheck", at(40))
        seed_version(connection, book("book_new"))
    audits, books = names(settings)
    assert audits["audit_new"][0] == "Audit 004"
    assert audits["recheck_new"][:2] == ("Audit 001 R04", "audit_old")
    assert books["book_new"][0] == "Rules 005" and books["book_new"][2] is not None


def test_failed_upgrade_rolls_back_completely_and_can_be_retried(empty_dataset_database_settings, tmp_path, monkeypatch):
    settings = empty_dataset_database_settings
    apply_migrations_through(settings, tmp_path, monkeypatch, 15)
    seed_history(settings)
    before = snapshot(settings)
    with pytest.raises(DatabaseError):
        apply_migrations_through(settings, tmp_path, monkeypatch, 16, extra=("017_broken.sql", "SELECT 1 / 0;"))
    assert read_sql(settings, "SELECT to_regclass('audit_names') AS t, to_regclass('rulebook_names') AS r") == [{"t": None, "r": None}]
    assert max(row["version"] for row in read_sql(settings, "SELECT version FROM schema_migrations")) == 15
    assert snapshot(settings) == before
    database.initialize_database(settings)
    assert names(settings) == (EXPECTED_AUDITS, EXPECTED_BOOKS)


def test_empty_database_numbers_start_at_one(dataset_database_settings):
    with database_connection(dataset_database_settings) as connection:
        seed_audit(connection, "audit_first", at(0))
        seed_version(connection, book("book_first"))
    audits, books = names(dataset_database_settings)
    assert audits == {"audit_first": ("Audit 001", "audit_first", "Untitled audit")}
    assert books["book_first"][0] == "Rules 001"


@pytest.mark.parametrize(("text", "title"), [
    ("1. Person boxes must be tight.", "Person boxes must be tight."),
    ("(2) Person boxes", "Person boxes"),
    ("3) Person boxes", "Person boxes"),
    ("- Person boxes", "Person boxes"),
    ("* Person boxes", "Person boxes"),
    ("\n\n\tPerson\t\tboxes  \n second", "Person boxes"),
    ("Scope: people.\r\nSecond", "Scope: people."),
    ("x" * 80, "x" * 80),
    ("x" * 81, "x" * 79 + "…"),
    ("", "Untitled guidelines"),
    ("   \n  \n", "Untitled guidelines"),
    ("1.", "Untitled guidelines"),
    ("2026 guidelines", "2026 guidelines"),
])
def test_default_rulebook_title(dataset_database_settings, text, title):
    assert read_sql(dataset_database_settings, "SELECT default_rulebook_title(%s) AS t", (text,)) == [{"t": title}]


# ---------------------------------------------------------------- allocation safety

def test_concurrent_root_audits_get_unique_numbers(dataset_database_settings):
    count = 16
    barrier = Barrier(count)

    def create(index):
        with database_connection(dataset_database_settings) as connection:
            seed_audit(connection, f"audit_{index:02d}", at(index))
            barrier.wait(timeout=30)

    with ThreadPoolExecutor(max_workers=count) as pool:
        list(pool.map(create, range(count)))
    rows = read_sql(dataset_database_settings, "SELECT display_number, recheck_number FROM audit_names")
    assert sorted(row["display_number"] for row in rows) == list(range(1, count + 1))
    assert all(row["recheck_number"] is None for row in rows)


def test_concurrent_rechecks_of_one_root_get_unique_suffixes(dataset_database_settings):
    with database_connection(dataset_database_settings) as connection:
        seed_audit(connection, "root", at(0))
        seed_audit(connection, "first", at(1))
        link(connection, "first", "root", at(1))
    count = 12
    barrier = Barrier(count)

    def recheck(index):
        with database_connection(dataset_database_settings) as connection:
            seed_audit(connection, f"child_{index:02d}", at(2))
            # Half recheck the root and half recheck its recheck; all share one root.
            link(connection, f"child_{index:02d}", "root" if index % 2 else "first", at(2))
            barrier.wait(timeout=30)

    with ThreadPoolExecutor(max_workers=count) as pool:
        list(pool.map(recheck, range(count)))
    rows = read_sql(dataset_database_settings, "SELECT * FROM audit_names WHERE recheck_number IS NOT NULL")
    assert {(row["display_number"], row["root_audit_id"]) for row in rows} == {(1, "root")}
    assert sorted(row["recheck_number"] for row in rows) == list(range(1, count + 2))
    assert read_sql(dataset_database_settings, "SELECT last_recheck_number FROM audit_names WHERE audit_id='root'") == [
        {"last_recheck_number": count + 1}]


def test_numbers_are_permanent_and_never_reused(dataset_database_settings):
    settings = dataset_database_settings
    with database_connection(settings) as connection:
        seed_audit(connection, "kept", at(0))
        seed_version(connection, book("kept_book"))
    # A number allocated by a transaction that then rolls back leaves a gap; it is not reused.
    with pytest.raises(RuntimeError):
        with database_connection(settings) as connection:
            seed_audit(connection, "rolled_back", at(1))
            connection.execute("SET CONSTRAINTS audit_runs_display_name IMMEDIATE")
            assert connection.execute("SELECT display_number FROM audit_names WHERE audit_id='rolled_back'").fetchone() == {"display_number": 2}
            raise RuntimeError("abandon")
    with database_connection(settings) as connection:
        seed_audit(connection, "after_gap", at(2))
    audits, _ = names(settings)
    assert audits["kept"][0] == "Audit 001" and audits["after_gap"][0] == "Audit 003" and "rolled_back" not in audits
    for statement in [
        "DELETE FROM audit_names WHERE audit_id='kept'",
        "DELETE FROM rulebook_names WHERE rulebook_id='kept_book'",
        "UPDATE audit_names SET display_number=99 WHERE audit_id='kept'",
        "UPDATE audit_names SET recheck_number=1 WHERE audit_id='kept'",
        "UPDATE audit_names SET root_audit_id='after_gap' WHERE audit_id='kept'",
        "UPDATE rulebook_names SET display_number=99 WHERE rulebook_id='kept_book'",
        "UPDATE rulebook_names SET first_recorded_at=NULL WHERE rulebook_id='kept_book'",
        "UPDATE audit_names SET last_recheck_number=-1 WHERE audit_id='kept'",
        "DELETE FROM audit_runs WHERE audit_id='kept'",
    ]:
        with pytest.raises(DatabaseError):
            write_sql(settings, statement)
    assert names(settings)[0]["kept"][0] == "Audit 001"


def test_formatting_past_999_audits_99_rechecks_and_999_rulebooks(dataset_database_settings):
    settings = dataset_database_settings
    write_sql(settings, "SELECT setval('audit_display_number_seq', 998)")
    write_sql(settings, "SELECT setval('rulebook_display_number_seq', 999)")
    with database_connection(settings) as connection:
        seed_audit(connection, "a999", at(0))
        seed_audit(connection, "a1000", at(1))
        seed_version(connection, book("b1000"))
    write_sql(settings, "UPDATE audit_names SET last_recheck_number=98 WHERE audit_id='a1000'")
    for index, minute in [(99, 2), (100, 3)]:
        with database_connection(settings) as connection:
            seed_audit(connection, f"r{index}", at(minute))
            link(connection, f"r{index}", "a1000", at(minute))
    audits, books = names(settings)
    assert [audits[key][0] for key in ("a999", "a1000", "r99", "r100")] == [
        "Audit 999", "Audit 1000", "Audit 1000 R99", "Audit 1000 R100"]
    assert books["b1000"][0] == "Rules 1000"
    lookup = DisplayNameRepository(settings=settings).lookup(audit_ids=["r100"], rulebook_ids=["b1000"])
    assert (lookup.audits[0].label, lookup.rulebooks[0].label) == ("Audit 1000 R100", "Rules 1000")


# ---------------------------------------------------------------- real workflow and HTTP

@pytest.fixture
def workflow(dataset_database_settings, tmp_path):
    repo, _, parent, audits, worker = prepared(dataset_database_settings, tmp_path)
    app = create_app()
    rulebooks = RulebookRepository(settings=dataset_database_settings)
    app.dependency_overrides[get_audit_repository] = lambda: audits
    app.dependency_overrides[get_audit_read_repository] = lambda: AuditReadRepository(settings=dataset_database_settings)
    app.dependency_overrides[get_rulebook_repository] = lambda: rulebooks
    app.dependency_overrides[get_recheck_repository] = lambda: RecheckRepository(settings=dataset_database_settings)
    app.dependency_overrides[get_display_name_repository] = lambda: DisplayNameRepository(settings=dataset_database_settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, parent.audit.audit_id, audits, worker, dataset_database_settings


def run_next(audits, worker):
    claim = audits.jobs.claim_next_job(worker_id="synthetic", kind=JobKind.AUDIT, lease_seconds=300)
    assert worker.run_claimed_job(claim).status.value == "completed"


def recheck_body(feedback=None):
    body = {"sample_ids": ["sample_a"], "sample_revisions": {"sample_a": 1}, "reviewer_id": "reviewer"}
    return body if feedback is None else {**body, "feedback": feedback}


def test_real_audits_and_rechecks_are_named_through_the_api(workflow):
    client, parent_id, audits, worker, settings = workflow
    sequence_before = read_sql(settings, "SELECT last_value FROM audit_display_number_seq")
    parent = client.get(f"/api/v1/audits/{parent_id}").json()
    assert parent["name"]["label"] == "Audit 001" and parent["name"]["title"] == "Synthetic source"
    assert parent["name"]["root_audit_id"] == parent_id and parent["name"]["recheck_number"] is None
    assert parent["rulebook_name"]["label"] == "Rules 001" and parent["rulebook_version"] == 1
    response = client.post(f"/api/v1/audits/{parent_id}/rechecks", json=recheck_body("Check the lower edge."))
    assert response.status_code == 202, response.text
    first = response.json()["audit"]
    assert first["name"]["label"] == "Audit 001 R01" and first["name"]["title"] == "Feedback recheck"
    assert first["name"]["root_audit_id"] == parent_id and first["rulebook_name"]["label"] == "Rules 001"
    run_next(audits, worker)
    # A recheck of a recheck still names the original root and takes the next suffix.
    response = client.post(f"/api/v1/audits/{first['audit_id']}/rechecks", json=recheck_body())
    assert response.status_code == 202, response.text
    second = response.json()["audit"]
    assert second["name"]["label"] == "Audit 001 R02" and second["name"]["title"] == "Recheck"
    assert second["name"]["root_audit_id"] == parent_id
    listed = client.get("/api/v1/audits").json()["items"]
    assert [item["name"]["label"] for item in listed] == ["Audit 001 R02", "Audit 001 R01", "Audit 001"]
    roots = client.get("/api/v1/audits", params={"roots_only": True}).json()["items"]
    assert [item["name"]["label"] for item in roots] == ["Audit 001"]
    # Repeated reads return the same names and allocate nothing.
    for _ in range(3):
        assert client.get(f"/api/v1/audits/{second['audit_id']}").json()["name"] == second["name"]
        client.get("/api/v1/display-names", params={"audit_id": [parent_id, "audit_missing"]})
    assert read_sql(settings, "SELECT count(*) AS n FROM audit_names") == [{"n": 3}]
    assert read_sql(settings, "SELECT last_value FROM audit_display_number_seq") == sequence_before


def test_title_edit_changes_only_the_title(workflow):
    client, parent_id, audits, worker, settings = workflow
    request_before = audits.get_request(parent_id).model_dump(mode="json")
    run_before = read_sql(settings, "SELECT * FROM audit_runs WHERE audit_id=%s", (parent_id,))
    findings_before = client.get(f"/api/v1/audits/{parent_id}/findings").json()
    path = f"/api/v1/audits/{parent_id}/title"
    response = client.put(path, json=update("  LabelMe demo  "))
    assert response.status_code == 200, response.text
    name = response.json()["name"]
    assert (name["label"], name["title"], name["title_revision"], name["title_updated_by"]) == (
        "Audit 001", "LabelMe demo", 1, "reviewer")
    assert response.json()["audit_id"] == parent_id
    stale = client.put(path, json=update("Other", revision=0))
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"
    assert client.put(path, json=update("Second", revision=1, reviewer="other")).json()["name"]["title"] == "Second"
    assert client.put(path, json=update("   ")).status_code == 422
    assert client.put("/api/v1/audits/audit_missing/title", json=update()).status_code == 404
    assert client.put(path, json={**update(), "number": 7}).status_code == 422
    assert audits.get_request(parent_id).model_dump(mode="json") == request_before
    assert read_sql(settings, "SELECT * FROM audit_runs WHERE audit_id=%s", (parent_id,)) == run_before
    assert client.get(f"/api/v1/audits/{parent_id}/findings").json() == findings_before
    edits = read_sql(settings, "SELECT resource_kind, resource_id, title_revision, title, reviewer_id FROM display_title_edits ORDER BY title_revision")
    assert edits == [
        {"resource_kind": "audit", "resource_id": parent_id, "title_revision": 1, "title": "LabelMe demo", "reviewer_id": "reviewer"},
        {"resource_kind": "audit", "resource_id": parent_id, "title_revision": 2, "title": "Second", "reviewer_id": "other"},
    ]
    with pytest.raises(DatabaseError):
        write_sql(settings, "DELETE FROM display_title_edits")
    # A later recheck still gets its own default title and keeps the root's number.
    response = client.post(f"/api/v1/audits/{parent_id}/rechecks", json=recheck_body())
    assert response.json()["audit"]["name"]["label"] == "Audit 001 R01"
    assert response.json()["audit"]["name"]["title"] == "Recheck"


def test_rulebook_versions_share_one_number_and_title_edits_keep_content(workflow):
    client, _, _, _, settings = workflow
    created = client.post("/api/v1/rulebooks", json={"guideline_text": "1. Person boxes must be tight.", "rules": [{
        "rule_id": "tight", "description": "Person boxes must be tight.", "check_name": "annotation_guideline",
        "parameters": {}, "source_sentence": "Person boxes must be tight."}]})
    assert created.status_code == 201, created.text
    v1 = created.json()
    assert v1["name"]["label"] == "Rules 002" and v1["name"]["title"] == "Person boxes must be tight."
    target = f"/api/v1/rulebooks/{v1['rulebook_id']}"
    rules = [{**rule, "confirmation_state": "confirmed"} for rule in v1["rules"]]
    v2 = client.put(target, json={"expected_version": 1, "reviewer_id": "r", "guideline_text": v1["guideline_text"], "rules": rules}).json()
    v3 = client.post(target + "/confirm", json={"expected_version": 2, "reviewer_id": "r"}).json()
    v4 = client.post(target + "/revisions", json={"expected_version": 3, "reviewer_id": "r"}).json()
    assert [b["version"] for b in (v1, v2, v3, v4)] == [1, 2, 3, 4]
    assert {b["name"]["label"] for b in (v1, v2, v3, v4)} == {"Rules 002"}
    versions = client.get(target + "/versions").json()["items"]
    assert {(b["version"], b["name"]["label"]) for b in versions} == {(1, "Rules 002"), (2, "Rules 002"), (3, "Rules 002"), (4, "Rules 002")}
    stored = RulebookRepository(settings=settings).list_rulebook_versions(v1["rulebook_id"], limit=100)
    documents_before = stored.model_dump(mode="json")
    reviews_before = client.get(target + "/reviews").json()
    rule_fingerprints = [fingerprint(rule) for item in stored.items for rule in item.rules]
    renamed = client.put(target + "/title", json=update("Person annotation checks"))
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["version"] == 4 and renamed.json()["name"]["title"] == "Person annotation checks"
    assert client.put(target + "/title", json=update("Again", revision=0)).status_code == 409
    assert client.put("/api/v1/rulebooks/book_missing/title", json=update()).status_code == 404
    assert client.get(target, params={"version": 1}).json()["name"]["title"] == "Person annotation checks"
    after = RulebookRepository(settings=settings).list_rulebook_versions(v1["rulebook_id"], limit=100)
    assert after.model_dump(mode="json") == documents_before
    assert [fingerprint(rule) for item in after.items for rule in item.rules] == rule_fingerprints
    assert client.get(target + "/reviews").json() == reviews_before
    assert read_sql(settings, "SELECT count(*) AS n FROM rulebook_versions WHERE rulebook_id=%s", (v1["rulebook_id"],)) == [{"n": 4}]
    # The audited book keeps its own number; a confirmed rulebook can still be audited by its ID.
    listed = client.get("/api/v1/rulebooks").json()["items"]
    assert sorted(b["name"]["label"] for b in listed) == ["Rules 001", "Rules 002"]


def test_lookup_returns_known_names_in_request_order(workflow):
    client, parent_id, _, _, settings = workflow
    response = client.get("/api/v1/display-names", params={
        "audit_id": ["audit_missing", parent_id, parent_id], "rulebook_id": ["book", "book_missing"]})
    assert response.status_code == 200
    body = response.json()
    assert [a["label"] for a in body["audits"]] == ["Audit 001"]
    assert [b["label"] for b in body["rulebooks"]] == ["Rules 001"]
    assert client.get("/api/v1/display-names").json() == {"audits": [], "rulebooks": []}
    assert client.get("/api/v1/display-names", params={"audit_id": [f"a{i}" for i in range(101)]}).status_code == 422
    assert client.get("/api/v1/display-names", params={"audit_id": " "}).status_code == 422
    assert client.get("/api/v1/display-names", params={"label": "Audit 001"}).json() == {"audits": [], "rulebooks": []}


def test_title_edits_follow_optimistic_concurrency(workflow):
    _, parent_id, _, _, settings = workflow
    store = DisplayNameRepository(settings=settings)
    barrier = Barrier(6)

    def edit(index):
        barrier.wait(timeout=30)
        try:
            return store.update_audit_title(parent_id, TitleUpdate.model_validate(update(f"Title {index}"))).title
        except Exception as error:  # noqa: BLE001 - the loser's error type is what we check.
            return type(error).__name__

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(edit, range(6)))
    assert sorted(results).count("DisplayTitleConflictError") == 5
    winner = next(result for result in results if result.startswith("Title"))
    assert store.audit_names([parent_id])[parent_id].title == winner
    assert store.audit_names([parent_id])[parent_id].title_revision == 1


def test_names_are_read_only_on_unavailable_storage():
    store = DisplayNameRepository(settings_from=lambda: (_ for _ in ()).throw(RuntimeError("unused")))
    # Nothing is resolved until a name is needed, and empty requests need nothing.
    assert store.audit_names([]) == {} and store.rulebook_names([]) == {}
    with pytest.raises(ValueError):
        store.lookup(audit_ids=[f"a{i}" for i in range(101)], rulebook_ids=[])
    with pytest.raises(RuntimeError, match="unused"):
        store.audit_names(["a"])


def test_committed_writes_are_not_reported_as_failures_when_names_are_unavailable(monkeypatch):
    from unittest.mock import Mock

    from backend.app.routers.rulebooks import get_rulebook_display_names
    from backend.app.storage.display_name_repository import DisplayNameStorageError

    def unavailable(self, ids):
        raise DisplayNameStorageError("down")

    monkeypatch.setattr(DisplayNameRepository, "rulebook_names", unavailable)
    store = DisplayNameRepository(settings_from=lambda: None)
    saved = book("book_saved")
    # Reads report the failure; responses to committed writes omit the name instead.
    with pytest.raises(DisplayNameStorageError):
        store.with_rulebook_names([saved])
    assert store.with_rulebook_names([saved], after_write=True)[0].name is None
    repository = Mock(spec_set=RulebookRepository)
    repository.save_rulebook.side_effect = lambda draft, **kwargs: Rulebook.model_validate(draft.model_dump())
    app = create_app()
    app.dependency_overrides[get_rulebook_repository] = lambda: repository
    app.dependency_overrides[get_rulebook_display_names] = lambda: store
    with TestClient(app, raise_server_exceptions=False) as client:
        created = client.post("/api/v1/rulebooks", json={"guideline_text": "Use person labels.", "rules": []})
        assert created.status_code == 201 and "name" not in created.json()
        repository.get_rulebook.return_value = saved
        read = client.get("/api/v1/rulebooks/book_saved")
        assert read.status_code == 503
        assert read.json()["error"] == {**read.json()["error"], "code": "DEPENDENCY_UNAVAILABLE",
                                        "message": "Display name storage is unavailable."}
    assert repository.save_rulebook.call_count == 1


def test_extracted_rulebooks_are_named_on_publication_and_unpublished_ones_take_no_number(empty_dataset_database_settings):
    from backend.app.models.review import RulebookAction, RulebookReplace
    from backend.app.services.review import confirm_rulebook, save_rulebook_review
    from backend.app.storage.database import initialize_database
    from backend.app.storage.extraction_repository import ExtractionRepository
    from backend.tests.test_extraction_repository import claim_one, draft_for

    settings = empty_dataset_database_settings
    initialize_database(settings)
    extraction = ExtractionRepository(settings=settings)
    claim = claim_one(extraction)
    # A queued or claimed extraction has no rulebook yet, so it has no number.
    assert read_sql(settings, "SELECT count(*) AS n FROM rulebook_names") == [{"n": 0}]
    extraction.publish(claim, draft_for(claim))
    book_id = claim.request.rulebook_id
    names_now = DisplayNameRepository(settings=settings).rulebook_names([book_id])
    assert names_now[book_id].label == "Rules 001"
    store = RulebookRepository(settings=settings)
    v1 = store.get_rulebook(book_id, version=1)
    rules = [{**rule.model_dump(mode="json"), "confirmation_state": "confirmed"} for rule in v1.rules]
    save_rulebook_review(book_id, RulebookReplace.model_validate_json(__import__("json").dumps({
        "expected_version": 1, "reviewer_id": "r", "guideline_text": v1.guideline_text, "rules": rules})), repository=store)
    confirm_rulebook(book_id, RulebookAction(expected_version=2, reviewer_id="r"), repository=store)
    assert [b.version for b in store.list_rulebook_versions(book_id).items] == [1, 2, 3]
    assert read_sql(settings, "SELECT rulebook_id, display_number FROM rulebook_names") == [
        {"rulebook_id": book_id, "display_number": 1}]
    # The rulebook ID alone is never turned into a number on read.
    assert DisplayNameRepository(settings=settings).lookup(audit_ids=[], rulebook_ids=["rulebook_" + "f" * 32]).rulebooks == []
