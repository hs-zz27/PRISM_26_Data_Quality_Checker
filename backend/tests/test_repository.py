"""PostgreSQL integration tests, isolated from application tables.

Set PRISM_TEST_DATABASE_URL to a disposable PostgreSQL database. Each test
creates its own schema and removes only that schema when it finishes. Missing
configuration skips these tests; a configured but unreachable server fails.
"""

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
from pydantic import SecretStr, ValidationError
import pytest

from backend.app.core.config import DatabaseSettings
from backend.app.models.review import RulebookAction, RulebookReplace, RulebookReviewAction
from backend.app.models.rule import ConfirmationState, Rule, Rulebook, RuleOrigin, RuleProposal
from backend.app.services import rule_extraction
from backend.app.services.review import confirm_rulebook, reject_rulebook, revise_rulebook, save_rulebook_review
from backend.app.storage import database
from backend.app.storage.database import DatabaseError, database_connection, initialize_database
from backend.app.storage.repository import (
    RulebookNotFoundError,
    RulebookRepository,
    RulebookStorageError,
    RulebookVersionConflictError,
)


SOURCE = "Allowed labels are person and véhicule."


def make_book(rulebook_id="rulebook_demo", version=1, state=ConfirmationState.DRAFT):
    rule = Rule(
        rule_id="LBL-1", description="Use an allowed label.", check_name="allowed_labels",
        parameters={"allowed_labels": ["person", "véhicule"], "context": {"optional": None}},
        origin=RuleOrigin.EXTRACTED, source_sentence=SOURCE, confirmation_state=state,
    )
    return Rulebook(
        rulebook_id=rulebook_id, version=version, guideline_text=f"\n  {SOURCE}\n",
        rules=[rule], confirmation_state=state,
    )


@pytest.fixture
def empty_database_settings():
    conninfo = os.environ.get("PRISM_TEST_DATABASE_URL")
    if not conninfo:
        pytest.skip("Set PRISM_TEST_DATABASE_URL to run PostgreSQL integration tests.")

    schema = f"prism_test_{uuid4().hex}"
    with psycopg.connect(conninfo, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        scoped_conninfo = make_conninfo(conninfo, options=f"-c search_path={schema}")
        settings = DatabaseSettings(conninfo=SecretStr(scoped_conninfo))
        yield settings
    finally:
        with psycopg.connect(conninfo, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


@pytest.fixture
def database_settings(empty_database_settings):
    initialize_database(empty_database_settings)
    return empty_database_settings


@pytest.fixture
def store(database_settings):
    return RulebookRepository(settings=database_settings)


@pytest.fixture
def unconnected_store(monkeypatch):
    def unexpected_connection(*args, **kwargs):
        pytest.fail("This operation must validate input before opening a database connection.")

    monkeypatch.setattr(psycopg, "connect", unexpected_connection)
    settings = DatabaseSettings(conninfo=SecretStr("host=127.0.0.1 dbname=unconnected"))
    return RulebookRepository(settings=settings)


def edit_database(store, statement, parameters=()):
    with database_connection(store.settings) as connection:
        connection.execute(statement, parameters)


def test_rulebook_round_trip_survives_new_repository_instance(store):
    original = make_book()
    saved = store.save_rulebook(original)
    reopened = RulebookRepository(settings=store.settings)
    loaded = reopened.get_rulebook(original.rulebook_id, version=1)
    assert saved == loaded == original
    assert loaded.guideline_text == f"\n  {SOURCE}\n"
    assert loaded.rules[0].origin is RuleOrigin.EXTRACTED
    assert loaded.confirmation_state is ConfirmationState.DRAFT
    assert loaded.rules[0].parameters["context"]["optional"] is None


@pytest.mark.parametrize("proposal_kind", ["supported", "unsupported", "empty"])
def test_extraction_workflow_commits_reviewable_drafts(store, monkeypatch, proposal_kind):
    proposals = [RuleProposal(
        **rule.model_dump(exclude={"origin", "confirmation_state"})
    ) for rule in make_book().rules]
    if proposal_kind == "unsupported":
        proposals[0].check_name = None
    elif proposal_kind == "empty":
        proposals = []
    text = f"\n  {SOURCE}\n"
    connect = psycopg.connect
    connections = []

    def track_connection(*args, **kwargs):
        connections.append(True)
        return connect(*args, **kwargs)

    def propose(guideline_text, *, settings):
        assert guideline_text == text and settings is None
        assert connections == []
        return proposals

    monkeypatch.setattr(psycopg, "connect", track_connection)
    monkeypatch.setattr(rule_extraction, "propose_rules", propose)
    saved = rule_extraction.extract_and_save_rulebook(text, repository=store)
    reopened = RulebookRepository(settings=store.settings)
    loaded = reopened.get_rulebook(saved.rulebook_id, version=1)

    assert loaded == saved and loaded is not saved
    assert loaded.guideline_text == text
    assert [rule.model_dump(exclude={"origin", "confirmation_state"}) for rule in loaded.rules] == [
        proposal.model_dump() for proposal in proposals
    ]
    assert loaded.version == 1 and loaded.confirmation_state is ConfirmationState.DRAFT
    assert all(rule.origin is RuleOrigin.EXTRACTED for rule in loaded.rules)
    assert all(rule.confirmation_state is ConfirmationState.DRAFT for rule in loaded.rules)


def test_schema_initialization_is_idempotent_and_preserves_records(store):
    original = store.save_rulebook(make_book())
    initialize_database(store.settings)
    initialize_database(store.settings)
    assert store.get_rulebook(original.rulebook_id) == original


def test_concurrent_schema_initialization_creates_one_migration_history(empty_database_settings):
    barrier = Barrier(2)

    def initialize(_):
        barrier.wait(timeout=10)
        initialize_database(empty_database_settings)

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(initialize, range(2)))

    with database_connection(empty_database_settings, readonly=True) as connection:
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [{"version": 1}, {"version": 2}]
        assert connection.execute(
            "SELECT COUNT(*) AS total FROM rulebook_versions"
        ).fetchone() == {"total": 0}


@pytest.mark.parametrize("versions", [(2,), (1, 3), (1, 2, 3)])
def test_incompatible_or_future_migration_history_is_refused(store, versions):
    original = store.save_rulebook(make_book())
    with database_connection(store.settings) as connection:
        connection.execute("DELETE FROM schema_migrations")
        for version in versions:
            connection.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))

    with pytest.raises(DatabaseError, match="history"):
        initialize_database(store.settings)

    with database_connection(store.settings, readonly=True) as connection:
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [{"version": version} for version in versions]
    assert store.get_rulebook(original.rulebook_id) == original


def test_failed_migration_batch_rolls_back_ddl_and_history(store, tmp_path, monkeypatch):
    original = store.save_rulebook(make_book())
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for source in database.MIGRATIONS_DIRECTORY.glob("[0-9][0-9][0-9]_*.sql"):
        (migrations / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (migrations / "003_probe.sql").write_text(
        "CREATE TABLE migration_probe (probe_id INTEGER);", encoding="utf-8",
    )
    failing_migration = migrations / "004_failure.sql"
    failing_migration.write_text(
        "CREATE TABLE second_migration_probe (probe_id INTEGER);\n"
        "SELECT 'invalid integer'::INTEGER;", encoding="utf-8",
    )
    monkeypatch.setattr(database, "MIGRATIONS_DIRECTORY", migrations)

    with pytest.raises(DatabaseError):
        initialize_database(store.settings)

    with database_connection(store.settings, readonly=True) as connection:
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [{"version": 1}, {"version": 2}]
        assert connection.execute(
            "SELECT to_regclass('migration_probe') AS first, "
            "to_regclass('second_migration_probe') AS second"
        ).fetchone() == {"first": None, "second": None}
    assert store.get_rulebook(original.rulebook_id) == original

    failing_migration.write_text(
        "CREATE TABLE second_migration_probe (probe_id INTEGER);", encoding="utf-8",
    )
    initialize_database(store.settings)
    with database_connection(store.settings, readonly=True) as connection:
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [{"version": 1}, {"version": 2}, {"version": 3}, {"version": 4}]
        assert connection.execute("SELECT COUNT(*) AS total FROM migration_probe").fetchone() == {"total": 0}
        assert connection.execute("SELECT COUNT(*) AS total FROM second_migration_probe").fetchone() == {"total": 0}
    assert store.get_rulebook(original.rulebook_id) == original


def test_old_versions_are_preserved_and_latest_includes_new_drafts(store):
    first = make_book(state=ConfirmationState.CONFIRMED)
    store.save_rulebook(first)
    second = make_book(version=2)
    second.rules[0].description = "A reviewed draft of the next version."
    store.save_rulebook(second, expected_version=1)
    assert store.get_rulebook(first.rulebook_id, version=1) == first
    assert store.get_rulebook(first.rulebook_id) == second
    assert store.get_rulebook(first.rulebook_id).confirmation_state is ConfirmationState.DRAFT


def test_duplicate_save_cannot_overwrite_a_snapshot(store):
    first = make_book()
    store.save_rulebook(first)
    replacement = make_book()
    replacement.rules[0].description = "Attempted overwrite."
    with pytest.raises(RulebookVersionConflictError) as caught:
        store.save_rulebook(replacement)
    assert caught.value.expected_version == 0 and caught.value.actual_version == 1
    assert store.get_rulebook(first.rulebook_id) == first


@pytest.mark.parametrize("version,expected", [(2, 0), (3, 1)])
def test_versions_cannot_skip_numbers(store, version, expected):
    with pytest.raises(ValueError, match="expected_version"):
        store.save_rulebook(make_book(version=version), expected_version=expected)
    assert store.list_rulebooks().total == 0


@pytest.mark.parametrize("version", [None, 1, 9])
def test_missing_rulebooks_and_versions_are_explicit(store, version):
    with pytest.raises(RulebookNotFoundError) as caught:
        store.get_rulebook("missing", version)
    assert caught.value.rulebook_id == "missing" and caught.value.version == version
    store.save_rulebook(make_book())
    with pytest.raises(RulebookNotFoundError):
        store.get_rulebook("rulebook_demo", version=9)


def test_saved_data_does_not_share_mutable_objects(store):
    original = make_book()
    saved = store.save_rulebook(original)
    original.rules[0].parameters["allowed_labels"].append("other")
    saved.rules[0].parameters["context"]["optional"] = "changed"
    loaded = store.get_rulebook(original.rulebook_id)
    assert loaded.rules[0].parameters["allowed_labels"] == ["person", "véhicule"]
    assert loaded.rules[0].parameters["context"]["optional"] is None
    loaded.rules.clear()
    assert len(store.get_rulebook(original.rulebook_id).rules) == 1


@pytest.mark.parametrize("mutation", ["missing_citation", "duplicate_id", "unconfirmed_rule"])
def test_mutated_models_are_revalidated_before_saving(store, mutation):
    book = make_book()
    if mutation == "missing_citation":
        book.rules[0].source_sentence = None
    elif mutation == "duplicate_id":
        book.rules.append(book.rules[0])
    else:
        book.confirmation_state = ConfirmationState.CONFIRMED
    with pytest.raises(ValidationError):
        store.save_rulebook(book)
    assert store.list_rulebooks().total == 0


def test_latest_version_listing_is_paginated_and_stable(store):
    for rulebook_id in ("c", "a", "b"):
        store.save_rulebook(make_book(rulebook_id))
    store.save_rulebook(make_book("a", version=2), expected_version=1)
    first = store.list_rulebooks(limit=2)
    assert [(book.rulebook_id, book.version) for book in first.items] == [("a", 2), ("b", 1)]
    assert first.total == 3 and first.offset == 0 and first.next_offset == 2
    last = store.list_rulebooks(limit=2, offset=first.next_offset)
    assert [book.rulebook_id for book in last.items] == ["c"] and last.next_offset is None
    empty = store.list_rulebooks(offset=99)
    assert empty.items == [] and empty.total == 3 and empty.next_offset is None
    assert first.model_dump(mode="json")["items"][0]["version"] == 2


def test_empty_listing_has_no_next_page(store):
    page = store.list_rulebooks()
    assert page.items == [] and page.total == 0 and page.next_offset is None


def test_quoted_ids_are_bound_as_values_not_sql(store):
    book = make_book("quoted'; DROP TABLE rulebook_versions; --")
    store.save_rulebook(book)
    assert store.get_rulebook(book.rulebook_id) == book
    assert store.list_rulebooks().total == 1


def test_unsupported_requirements_are_not_discarded(store):
    book = make_book()
    book.rules[0].check_name = None
    store.save_rulebook(book)
    assert store.get_rulebook(book.rulebook_id).rules[0].check_name is None


@pytest.mark.parametrize("expected_version", [0, 1])
def test_two_writers_cannot_both_save_the_same_version(store, expected_version):
    if expected_version:
        store.save_rulebook(make_book())
    barrier = Barrier(2)

    def save_edit(description):
        writer = RulebookRepository(settings=store.settings)
        edited = make_book(version=expected_version + 1)
        edited.rules[0].description = description
        barrier.wait(timeout=10)
        try:
            return writer.save_rulebook(edited, expected_version=expected_version)
        except RulebookVersionConflictError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(save_edit, ("First edit.", "Second edit.")))
    assert sum(isinstance(outcome, Rulebook) for outcome in outcomes) == 1
    conflicts = [outcome for outcome in outcomes if isinstance(outcome, RulebookVersionConflictError)]
    assert len(conflicts) == 1 and conflicts[0].actual_version == expected_version + 1
    assert store.get_rulebook("rulebook_demo").version == expected_version + 1
    if expected_version:
        assert store.get_rulebook("rulebook_demo", version=1) == make_book()


@pytest.mark.parametrize("document", [{}, {"rulebook_id": "rulebook_demo", "version": "invalid"}])
def test_invalid_stored_documents_report_storage_failure(store, document):
    store.save_rulebook(make_book())
    edit_database(store, "UPDATE rulebook_versions SET document = %s", (Jsonb(document),))
    with pytest.raises(RulebookStorageError, match="validation"):
        store.get_rulebook("rulebook_demo")
    with pytest.raises(RulebookStorageError):
        store.list_rulebooks()


def test_document_metadata_mismatch_is_detected(store):
    store.save_rulebook(make_book())
    edit_database(store, "UPDATE rulebook_versions SET confirmation_state = %s", ("rejected",))
    with pytest.raises(RulebookStorageError, match="metadata"):
        store.get_rulebook("rulebook_demo")


def test_failed_insert_rolls_back_and_does_not_advance_version(store):
    store.save_rulebook(make_book())
    edit_database(store, """
        CREATE FUNCTION reject_test_insert() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'simulated write failure';
        END
        $$
    """)
    edit_database(store, """
        CREATE TRIGGER block_test_insert BEFORE INSERT ON rulebook_versions
        FOR EACH ROW EXECUTE FUNCTION reject_test_insert()
    """)
    with pytest.raises(RulebookStorageError):
        store.save_rulebook(make_book(version=2), expected_version=1)
    assert store.get_rulebook("rulebook_demo").version == 1
    edit_database(store, "DROP TRIGGER block_test_insert ON rulebook_versions")
    store.save_rulebook(make_book(version=2), expected_version=1)
    assert store.get_rulebook("rulebook_demo").version == 2


@pytest.mark.parametrize("options", [{"limit": 0}, {"limit": 101}, {"limit": True}, {"offset": -1}, {"offset": 1.5}])
def test_invalid_pagination(unconnected_store, options):
    with pytest.raises(ValueError):
        unconnected_store.list_rulebooks(**options)


@pytest.mark.parametrize("version", [0, -1, True, 1.5])
def test_invalid_read_versions(unconnected_store, version):
    with pytest.raises(ValueError):
        unconnected_store.get_rulebook("rulebook_demo", version)


@pytest.mark.parametrize("expected", [-1, True, 1.5])
def test_invalid_expected_versions(unconnected_store, expected):
    with pytest.raises(ValueError):
        unconnected_store.save_rulebook(make_book(), expected_version=expected)


def test_repository_construction_does_not_connect_or_create_tables(unconnected_store):
    assert unconnected_store.settings.conninfo.get_secret_value() == "host=127.0.0.1 dbname=unconnected"


def test_read_connections_are_read_only_and_use_a_repeatable_snapshot(store):
    store.save_rulebook(make_book())
    with database_connection(store.settings, readonly=True) as reader:
        assert reader.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"] == "repeatable read"
        assert reader.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "on"
        before = reader.execute("SELECT COUNT(*) AS total FROM rulebook_versions").fetchone()
        store.save_rulebook(make_book("another"))
        after = reader.execute("SELECT COUNT(*) AS total FROM rulebook_versions").fetchone()
        assert before == after == {"total": 1}
    assert store.list_rulebooks().total == 2


def test_review_migration_upgrades_existing_books_without_fabricating_history(
    empty_database_settings, tmp_path, monkeypatch,
):
    original_directory = database.MIGRATIONS_DIRECTORY
    migrations = tmp_path / "initial_migrations"
    migrations.mkdir()
    initial = original_directory / "001_rulebooks.sql"
    (migrations / initial.name).write_text(initial.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(database, "MIGRATIONS_DIRECTORY", migrations)
    initialize_database(empty_database_settings)
    repository = RulebookRepository(settings=empty_database_settings)
    original = repository.save_rulebook(make_book())

    monkeypatch.setattr(database, "MIGRATIONS_DIRECTORY", original_directory)
    initialize_database(empty_database_settings)
    initialize_database(empty_database_settings)
    assert repository.get_rulebook(original.rulebook_id) == original
    assert repository.list_rulebook_reviews(original.rulebook_id).items == []


def test_review_lifecycle_preserves_versions_decisions_and_attribution(store):
    original = store.save_rulebook(make_book())
    rules = original.model_copy(deep=True).rules
    rules[0].confirmation_state = ConfirmationState.CONFIRMED
    rules.append(Rule(rule_id="manual", description="Unsupported requirement.", origin="reviewer",
                      confirmation_state="rejected"))
    first_review = save_rulebook_review(
        original.rulebook_id,
        RulebookReplace(expected_version=1, reviewer_id="reviewer_one",
                        guideline_text=original.guideline_text, rules=rules),
        repository=store,
    )
    assert first_review.version == 2 and first_review.rules[-1].confirmation_state is ConfirmationState.REJECTED
    ready = save_rulebook_review(
        original.rulebook_id,
        RulebookReplace(expected_version=2, reviewer_id="reviewer_two",
                        guideline_text=original.guideline_text, rules=rules[:1]),
        repository=store,
    )
    confirmed = confirm_rulebook(
        original.rulebook_id, RulebookAction(expected_version=3, reviewer_id="reviewer_two"), repository=store,
    )
    revised = revise_rulebook(
        original.rulebook_id, RulebookAction(expected_version=4, reviewer_id="reviewer_one"), repository=store,
    )
    rejected = reject_rulebook(
        original.rulebook_id, RulebookAction(expected_version=5, reviewer_id="reviewer_one"), repository=store,
    )
    reopened = RulebookRepository(settings=store.settings)
    final = revise_rulebook(
        original.rulebook_id, RulebookAction(expected_version=6, reviewer_id="reviewer_two"), repository=reopened,
    )
    expected = [original, first_review, ready, confirmed, revised, rejected, final]
    assert reopened.list_rulebook_versions(original.rulebook_id).items == expected
    assert reopened.get_rulebook(original.rulebook_id) == final
    assert confirmed.confirmation_state is ConfirmationState.CONFIRMED
    assert revised.rules[0].confirmation_state is ConfirmationState.DRAFT
    assert rejected.confirmation_state is ConfirmationState.REJECTED
    records = reopened.list_rulebook_reviews(original.rulebook_id)
    assert [(record.version, record.action.value, record.reviewer_id) for record in records.items] == [
        (2, "edit", "reviewer_one"), (3, "edit", "reviewer_two"), (4, "confirm", "reviewer_two"),
        (5, "revise", "reviewer_one"), (6, "reject", "reviewer_one"), (7, "revise", "reviewer_two"),
    ]
    assert all(record.created_at.utcoffset().total_seconds() == 0 for record in records.items)
    assert records.total == 6 and records.next_offset is None


def test_version_and_review_history_pages_are_scoped_and_ordered(store):
    original = store.save_rulebook(make_book("book'; --"))
    store.save_rulebook(make_book("other"))
    reject_rulebook(original.rulebook_id, RulebookAction(expected_version=1, reviewer_id="first"), repository=store)
    revise_rulebook(original.rulebook_id, RulebookAction(expected_version=2, reviewer_id="second"), repository=store)

    first = store.list_rulebook_versions(original.rulebook_id, limit=2)
    assert [book.version for book in first.items] == [1, 2] and first.total == 3 and first.next_offset == 2
    last = store.list_rulebook_versions(original.rulebook_id, offset=first.next_offset, limit=2)
    assert [book.version for book in last.items] == [3] and last.next_offset is None
    actions = store.list_rulebook_reviews(original.rulebook_id, limit=1)
    assert actions.total == 2 and actions.items[0].version == 2 and actions.next_offset == 1
    tail = store.list_rulebook_reviews(original.rulebook_id, offset=actions.next_offset)
    assert [record.version for record in tail.items] == [3] and tail.next_offset is None
    for page in (store.list_rulebook_versions(original.rulebook_id, offset=99),
                 store.list_rulebook_reviews(original.rulebook_id, offset=99)):
        assert page.items == [] and page.next_offset is None and page.total > 0
    assert store.list_rulebook_reviews("other").total == 0


@pytest.mark.parametrize("method", ["list_rulebook_versions", "list_rulebook_reviews"])
def test_missing_history_is_explicit(store, method):
    with pytest.raises(RulebookNotFoundError):
        getattr(store, method)("missing")


def test_competing_review_actions_cannot_both_commit(store, monkeypatch):
    original = make_book()
    original.rules[0].confirmation_state = ConfirmationState.CONFIRMED
    store.save_rulebook(original)
    barrier = Barrier(2)
    save = RulebookRepository.save_rulebook_review

    def synchronized_save(self, *args, **kwargs):
        barrier.wait(timeout=10)
        return save(self, *args, **kwargs)

    monkeypatch.setattr(RulebookRepository, "save_rulebook_review", synchronized_save)

    def review(kind):
        writer = RulebookRepository(settings=store.settings)
        try:
            if kind == "confirm":
                return confirm_rulebook(original.rulebook_id,
                                        RulebookAction(expected_version=1, reviewer_id=kind), repository=writer)
            return save_rulebook_review(
                original.rulebook_id,
                RulebookReplace(expected_version=1, reviewer_id=kind,
                                guideline_text=original.guideline_text, rules=original.rules),
                repository=writer,
            )
        except RulebookVersionConflictError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(review, ("confirm", "edit")))
    saved = [outcome for outcome in outcomes if isinstance(outcome, Rulebook)]
    conflicts = [outcome for outcome in outcomes if isinstance(outcome, RulebookVersionConflictError)]
    assert len(saved) == len(conflicts) == 1
    assert conflicts[0].actual_version == 2 and conflicts[0].expected_version == 1
    assert store.get_rulebook(original.rulebook_id) == saved[0]
    assert store.get_rulebook(original.rulebook_id, version=1) == original
    history = store.list_rulebook_reviews(original.rulebook_id)
    assert history.total == 1 and history.items[0].version == 2
    expected_action = "confirm" if saved[0].confirmation_state is ConfirmationState.CONFIRMED else "edit"
    assert history.items[0].action.value == history.items[0].reviewer_id == expected_action


@pytest.mark.parametrize("failure", ["snapshot", "history", "commit"])
def test_review_failures_roll_back_both_snapshot_and_history(store, failure):
    original = store.save_rulebook(make_book())
    edit_database(store, """
        CREATE FUNCTION reject_review_write() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'simulated review failure';
        END
        $$
    """)
    triggers = {
        "snapshot": "CREATE TRIGGER block_review BEFORE INSERT ON rulebook_versions "
                    "FOR EACH ROW EXECUTE FUNCTION reject_review_write()",
        "history": "CREATE TRIGGER block_review BEFORE INSERT ON rulebook_reviews "
                   "FOR EACH ROW EXECUTE FUNCTION reject_review_write()",
        "commit": "CREATE CONSTRAINT TRIGGER block_review AFTER INSERT ON rulebook_reviews "
                  "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION reject_review_write()",
    }
    edit_database(store, triggers[failure])
    request = RulebookAction(expected_version=1, reviewer_id="reviewer")
    with pytest.raises(RulebookStorageError):
        reject_rulebook(original.rulebook_id, request, repository=store)
    assert store.get_rulebook(original.rulebook_id) == original
    assert store.list_rulebook_versions(original.rulebook_id).total == 1
    assert store.list_rulebook_reviews(original.rulebook_id).total == 0


@pytest.mark.parametrize("options", [
    {"expected_version": 0}, {"expected_version": True}, {"reviewer_id": " "}, {"action": "unknown"},
])
def test_invalid_review_metadata_is_rejected_before_connecting(unconnected_store, options):
    arguments = dict(expected_version=1, reviewer_id="reviewer", action=RulebookReviewAction.EDIT)
    arguments.update(options)
    with pytest.raises(ValueError):
        unconnected_store.save_rulebook_review(make_book(version=2), **arguments)


@pytest.mark.parametrize("method", ["list_rulebook_versions", "list_rulebook_reviews"])
@pytest.mark.parametrize("options", [{"limit": True}, {"limit": 101}, {"offset": -1}])
def test_invalid_history_pagination_never_connects(unconnected_store, method, options):
    with pytest.raises(ValueError):
        getattr(unconnected_store, method)("book", **options)
