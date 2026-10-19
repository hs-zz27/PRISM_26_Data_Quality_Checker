"""Opt-in PostgreSQL coverage for decisions, history and immutable evidence."""

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
from pydantic import SecretStr
import pytest

from backend.app.core.config import DatabaseSettings
from backend.app.models.audit import BatchAuditRequest
from backend.app.models.finding import Finding, FindingOutcome, FindingResolution
from backend.app.models.review import FindingReviewRequest
from backend.app.services.review import review_finding
from backend.app.storage import database
from backend.app.storage.database import database_connection, initialize_database
from backend.app.storage.workflow_repository import (
    AuditRepository, FindingNotFoundError, FindingRepository,
    FindingReviewConflictError, WorkflowStorageError,
)


@pytest.fixture
def empty_database_settings():
    conninfo = os.environ.get("PRISM_TEST_DATABASE_URL")
    if not conninfo:
        pytest.skip("Set PRISM_TEST_DATABASE_URL to run PostgreSQL integration tests.")
    schema = f"prism_finding_review_{uuid4().hex}"
    with psycopg.connect(conninfo, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        yield DatabaseSettings(conninfo=SecretStr(make_conninfo(conninfo, options=f"-c search_path={schema}")))
    finally:
        with psycopg.connect(conninfo, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def repository(empty_database_settings):
    initialize_database(empty_database_settings)
    return FindingRepository(settings=empty_database_settings)


def make_finding(outcome="fail"):
    return Finding(
        finding_id="finding_1", audit_id="audit_1", sample_id="sample_1", sample_revision=1,
        object_id=1, rulebook_id="book_1", rulebook_version=2, rule_id="r1",
        description="Use person labels.", check_name="allowed_labels", outcome=outcome,
        origin="extracted", source_sentence="Use person labels.",
        parameters={"allowed_labels": ["person"]}, evidence={"measurements": {"label": "car"}},
    )


def seed_finding(settings, outcome="fail"):
    audits = AuditRepository(settings=settings)
    audit, _ = audits.start_audit(
        BatchAuditRequest(dataset_id="dataset_1", dataset_root="opaque-root", rulebook_id="book_1",
                          rulebook_version=2, sample_ids=["sample_1"]),
        audit_id="audit_1", job_id="job_1",
    )
    job_claim = audits.claim_audit(audit.audit_id, worker_id="worker_a")
    assert job_claim is not None
    sample_claim = audits.claim_next_sample(job_claim)
    assert sample_claim is not None
    finding = make_finding(outcome)
    measurements = {"image_id": "sample_1", "review_status": "pending_review", "status": "completed"}
    audits.persist_sample_success(sample_claim, measurements=measurements, findings=[finding])
    return audits, sample_claim, finding, measurements


def decide(repository, *, revision=0, resolution="accepted", reviewer="reviewer_1", comment="", finding_id="finding_1"):
    return review_finding(
        finding_id, FindingReviewRequest(expected_review_version=revision, resolution=resolution,
                                         reviewer_id=reviewer, comment=comment), repository=repository,
    )


@pytest.mark.parametrize("outcome", list(FindingOutcome))
def test_decisions_preserve_machine_data_and_survive_reopening(repository, outcome):
    audits, _, original, measurements = seed_finding(repository.settings, outcome)
    audit_before, job_before = audits.get_audit("audit_1"), audits.get_job("audit_1")
    with database_connection(repository.settings, readonly=True) as connection:
        machine_before = connection.execute("SELECT * FROM findings").fetchone()
        sample_before = connection.execute("SELECT * FROM audit_samples").fetchone()
    assert repository.get_finding(original.finding_id) == original
    assert repository.list_finding_reviews(original.finding_id).total == 0

    saved = decide(repository, comment="I checked the source annotation.")

    reopened = FindingRepository(settings=repository.settings)
    current = reopened.get_finding(original.finding_id)
    assert current.review == saved
    assert current.model_dump(exclude={"review"}) == original.model_dump(exclude={"review"})
    assert reopened.list_findings("audit_1") == [current]
    assert reopened.list_findings("audit_1", sample_id="sample_1") == [current]
    assert audits.get_measurements("audit_1", "sample_1", 1) == measurements
    assert audits.get_audit("audit_1") == audit_before
    assert audits.get_job("audit_1") == job_before
    with database_connection(repository.settings, readonly=True) as connection:
        assert connection.execute("SELECT * FROM findings").fetchone() == machine_before
        assert connection.execute("SELECT * FROM audit_samples").fetchone() == sample_before


def test_history_retains_each_decision_and_is_scoped_and_paginated(repository):
    _, _, original, _ = seed_finding(repository.settings)
    other = original.model_copy(deep=True, update={"finding_id": "finding_2"})
    repository.save_findings([other])
    saved = [
        decide(repository, revision=0, resolution="accepted", reviewer="one", comment="Confirmed."),
        decide(repository, revision=1, resolution="rejected", reviewer="two", comment="Wrong label rule."),
        decide(repository, revision=2, resolution="unsure", reviewer="one", comment="Needs clarification."),
    ]

    first = repository.list_finding_reviews("finding_1", limit=2)
    last = repository.list_finding_reviews("finding_1", offset=first.next_offset, limit=2)
    records = first.items + last.items
    assert first.total == last.total == 3
    assert first.next_offset == 2 and last.next_offset is None
    assert [item.model_dump(exclude={"finding_id"}) for item in records] == [item.model_dump() for item in saved]
    assert all(item.finding_id == "finding_1" for item in records)
    assert repository.get_finding("finding_1").review == saved[-1]
    assert repository.get_finding("finding_2") == other
    assert repository.list_finding_reviews("finding_2").total == 0
    beyond = repository.list_finding_reviews("finding_1", offset=99)
    assert beyond.items == [] and beyond.total == 3 and beyond.next_offset is None


def test_stale_review_cannot_overwrite_a_newer_decision(repository):
    seed_finding(repository.settings)
    current = decide(repository)

    with pytest.raises(FindingReviewConflictError) as caught:
        decide(repository, resolution="rejected", reviewer="other")

    assert caught.value.finding_id == "finding_1"
    assert caught.value.expected_review_version == 0 and caught.value.actual_revision == 1
    assert repository.get_finding("finding_1").review == current
    assert repository.list_finding_reviews("finding_1").total == 1


def test_competing_reviewers_cannot_both_advance_the_same_revision(repository):
    seed_finding(repository.settings)
    barrier = Barrier(2)

    def submit(resolution):
        writer = FindingRepository(settings=repository.settings)
        barrier.wait(timeout=10)
        try:
            return decide(writer, resolution=resolution, reviewer=resolution)
        except FindingReviewConflictError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, ("accepted", "rejected")))

    conflicts = [item for item in results if isinstance(item, FindingReviewConflictError)]
    winners = [item for item in results if not isinstance(item, FindingReviewConflictError)]
    assert len(conflicts) == len(winners) == 1
    assert repository.get_finding("finding_1").review == winners[0]
    assert repository.list_finding_reviews("finding_1").total == 1


@pytest.mark.parametrize("failure", ["insert", "commit"])
def test_failed_review_write_leaves_no_partial_history(repository, failure):
    _, _, original, _ = seed_finding(repository.settings)
    with database_connection(repository.settings) as connection:
        connection.execute("""
            CREATE FUNCTION reject_review_write() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'simulated review failure'; END;
            $$;
        """)
        if failure == "insert":
            connection.execute("""CREATE TRIGGER block_review BEFORE INSERT ON finding_reviews
                                  FOR EACH ROW EXECUTE FUNCTION reject_review_write()""")
        else:
            connection.execute("""CREATE CONSTRAINT TRIGGER block_review AFTER INSERT ON finding_reviews
                                  DEFERRABLE INITIALLY DEFERRED
                                  FOR EACH ROW EXECUTE FUNCTION reject_review_write()""")

    with pytest.raises(WorkflowStorageError):
        decide(repository)

    assert repository.get_finding("finding_1") == original
    assert repository.list_finding_reviews("finding_1").total == 0


def test_audit_replays_preserve_reviews_and_still_reject_changed_evidence(repository):
    audits, claim, original, measurements = seed_finding(repository.settings)
    saved = decide(repository, resolution="rejected")

    audits.persist_sample_success(claim, measurements=measurements, findings=[original])
    repository.save_findings([original])
    changed = original.model_copy(deep=True)
    changed.evidence.measurements["label"] = "person"
    with pytest.raises(WorkflowStorageError):
        repository.save_findings([changed])
    with pytest.raises(WorkflowStorageError):
        audits.persist_sample_success(claim, measurements=measurements, findings=[changed])

    current = repository.get_finding("finding_1")
    assert current.review == saved
    assert current.evidence == original.evidence
    assert repository.list_finding_reviews("finding_1").total == 1


def test_missing_findings_cannot_be_reviewed_or_given_empty_history(repository):
    with pytest.raises(FindingNotFoundError):
        decide(repository)
    with pytest.raises(FindingNotFoundError):
        repository.list_finding_reviews("finding_1")


def test_upgrade_preserves_existing_findings_without_fabricating_reviews(
    empty_database_settings, tmp_path, monkeypatch,
):
    prior_migrations = tmp_path / "prior_migrations"
    prior_migrations.mkdir()
    for source in database.MIGRATIONS_DIRECTORY.glob("*.sql"):
        if int(source.name.split("_", 1)[0]) <= 4:
            (prior_migrations / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(database, "MIGRATIONS_DIRECTORY", prior_migrations)
        initialize_database(empty_database_settings)
        # Seed the historical schema directly. Current workflow writers require
        # source_mode and manifest columns introduced after this schema version.
        original = make_finding()
        legacy_request = BatchAuditRequest(
            dataset_id="dataset_1", dataset_root="opaque-root", rulebook_id="book_1",
            rulebook_version=2, sample_ids=["sample_1"],
        ).model_dump(mode="json", exclude={"source_mode"})
        with database_connection(empty_database_settings) as connection:
            connection.execute(
                """INSERT INTO jobs (job_id, kind, status, progress, result, created_at,
                       updated_at, resource_kind, resource_id)
                   VALUES ('job_1', 'audit', 'completed', %s, %s,
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'audit', 'audit_1')""",
                (Jsonb({"phase": "completed", "processed": 1, "total": 1, "fraction": 1.0}),
                 Jsonb({"kind": "audit", "id": "audit_1", "href": "/api/v1/audits/audit_1"})),
            )
            connection.execute(
                """INSERT INTO audit_runs (audit_id, job_id, dataset_id, dataset_root,
                       rulebook_id, rulebook_version, status, request, sample_count,
                       processed_count, created_at, updated_at)
                   VALUES ('audit_1', 'job_1', 'dataset_1', 'opaque-root', 'book_1',
                       2, 'completed', %s, 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                (Jsonb(legacy_request),),
            )
            connection.execute(
                """INSERT INTO audit_samples (audit_id, sample_id, sample_revision, ordinal,
                       status, started_at, finished_at, attempt_count)
                   VALUES ('audit_1', 'sample_1', 1, 0, 'completed',
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1)""",
            )
            connection.execute(
                """INSERT INTO findings (finding_id, audit_id, sample_id, sample_revision,
                       object_id, rulebook_id, rulebook_version, rule_id, outcome,
                       review_resolution, document, created_at)
                   VALUES ('finding_1', 'audit_1', 'sample_1', 1, 1, 'book_1', 2,
                       'r1', 'fail', 'pending', %s, CURRENT_TIMESTAMP)""",
                (Jsonb(original.model_dump(mode="json")),),
            )
            before = connection.execute("SELECT * FROM findings").fetchone()

    initialize_database(empty_database_settings)
    initialize_database(empty_database_settings)

    repository = FindingRepository(settings=empty_database_settings)
    with database_connection(empty_database_settings, readonly=True) as connection:
        assert connection.execute("SELECT * FROM findings").fetchone() == before
    assert repository.get_finding("finding_1") == original
    assert repository.list_finding_reviews("finding_1").total == 0
    decide(repository)
    assert repository.get_finding("finding_1").review.resolution is FindingResolution.ACCEPTED
