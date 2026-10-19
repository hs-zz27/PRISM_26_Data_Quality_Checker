"""Opt-in PostgreSQL coverage for sample decisions and preserved audit evidence."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
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
from backend.app.models.audit import AuditFailure, BatchAuditRequest
from backend.app.models.finding import Finding, FindingOutcome
from backend.app.models.job import ApiError
from backend.app.models.review import FindingReviewRequest, SampleReviewDecision, SampleReviewRequest
from backend.app.services.review import review_finding, review_sample
from backend.app.storage import (
    AuditRepository, AuditSampleNotFoundError, FindingRepository, SampleReviewConflictError,
    SampleReviewRepository, SampleReviewStateError, SampleRevisionConflictError, WorkflowStorageError,
)
from backend.app.storage import database
from backend.app.storage.database import database_connection, initialize_database


@pytest.fixture
def empty_database_settings():
    conninfo = os.environ.get("PRISM_TEST_DATABASE_URL")
    if not conninfo:
        pytest.skip("Set PRISM_TEST_DATABASE_URL to run PostgreSQL integration tests.")
    schema = f"prism_sample_review_{uuid4().hex}"
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
    return SampleReviewRepository(settings=empty_database_settings)


def start_audit(settings, *, audit_id="audit_1", sample_id="sample_1", sample_revision=2, extra_sample=False):
    audits = AuditRepository(settings=settings)
    audits.start_audit(
        BatchAuditRequest(
            dataset_id="dataset_1", dataset_root="opaque-root", rulebook_id="book_1", rulebook_version=3,
            sample_ids=[sample_id, "sample_later"] if extra_sample else [sample_id],
            sample_revisions={sample_id: sample_revision},
        ), audit_id=audit_id, job_id=f"job_{audit_id}",
    )
    return audits


def seed_result(settings, *, outcome=None, incomplete=False, extra_sample=False,
                audit_id="audit_1", sample_id="sample_1", sample_revision=2):
    audits = start_audit(settings, audit_id=audit_id, sample_id=sample_id,
                         sample_revision=sample_revision, extra_sample=extra_sample)
    job_claim = audits.claim_audit(audit_id, worker_id="worker_1")
    assert job_claim is not None
    sample_claim = audits.claim_next_sample(job_claim)
    assert sample_claim is not None
    measurements = {"image_id": sample_id, "review_status": "pending_review",
                    "status": "incomplete" if incomplete else "completed"}
    findings = [] if outcome is None else [Finding(
        finding_id=f"finding_{audit_id}_{sample_id}", audit_id=audit_id, sample_id=sample_id,
        sample_revision=sample_revision, rulebook_id="book_1", rulebook_version=3, rule_id="r1",
        description="Use person labels.", check_name="allowed_labels", outcome=outcome,
        origin="reviewer", evidence={"measurements": {"label": "car"}},
    )]
    audits.persist_sample_success(sample_claim, measurements=measurements, findings=findings)
    return audits, job_claim, sample_claim, measurements, findings


def decide(repository, *, audit_id="audit_1", sample_id="sample_1", sample_revision=2,
           revision=0, decision="accepted", reviewer="reviewer_1", comment=""):
    return review_sample(audit_id, sample_id, SampleReviewRequest(
        expected_sample_revision=sample_revision, expected_review_version=revision,
        decision=decision, reviewer_id=reviewer, comment=comment,
    ), repository=repository)


def machine_snapshot(settings):
    """Capture persisted inputs/results and finding reviews, excluding sample decisions."""
    with database_connection(settings, readonly=True) as connection:
        snapshots = {}
        for table, order in (
            ("jobs", "job_id"), ("audit_runs", "audit_id"),
            ("audit_samples", "audit_id, sample_id, sample_revision"),
            ("audit_measurements", "audit_id, sample_id, sample_revision"),
            ("audit_failures", "failure_id"), ("findings", "finding_id"),
            ("finding_reviews", "finding_id, revision"),
        ):
            rows = connection.execute(sql.SQL("SELECT * FROM {} ORDER BY {}").format(
                sql.Identifier(table), sql.SQL(order),
            )).fetchall()
            snapshots[table] = rows
        return snapshots


@pytest.mark.parametrize("outcome", [None, *FindingOutcome])
def test_decisions_preserve_all_machine_evidence_and_finding_reviews(repository, outcome):
    audits, _, _, measurements, findings = seed_result(repository.settings, outcome=outcome)
    if findings:
        review_finding(findings[0].finding_id, FindingReviewRequest(
            expected_review_version=0, resolution="rejected", reviewer_id="finding_reviewer", comment="Wrong rule.",
        ), repository=FindingRepository(settings=repository.settings))
    before = machine_snapshot(repository.settings)
    pending = repository.get_sample_review("audit_1", "sample_1", 2)
    assert pending.decision is SampleReviewDecision.PENDING_REVIEW and pending.revision == 0
    assert pending.sample_revision == 2 and pending.reviewer_id is None
    assert repository.list_sample_reviews("audit_1", "sample_1", 2).total == 0

    saved = decide(repository, comment="Inspected the available source and evidence.")

    reopened = SampleReviewRepository(settings=repository.settings)
    assert reopened.get_sample_review("audit_1", "sample_1", 2) == saved
    assert saved.decision is SampleReviewDecision.ACCEPTED
    assert saved.sample_revision == 2 and saved.revision == 1
    assert audits.get_measurements("audit_1", "sample_1", 2) == measurements
    assert machine_snapshot(repository.settings) == before


def test_history_retains_reopening_and_is_scoped_to_audit_sample_and_revision(repository):
    seed_result(repository.settings)
    seed_result(repository.settings, audit_id="audit_2")
    seed_result(repository.settings, audit_id="audit_3", sample_id="other_sample")
    # The schema permits multiple historical pins even though start_audit accepts
    # one revision per sample. Seed a second pin to exercise the full storage key.
    with database_connection(repository.settings) as connection:
        connection.execute("""INSERT INTO audit_samples (audit_id, sample_id, sample_revision, ordinal, status)
                              VALUES ('audit_1', 'sample_1', 3, 1, 'completed'),
                                     ('audit_1', 'other_sample', 2, 2, 'completed')""")
    other_pin = decide(repository, sample_revision=3, decision="rejected")
    other_sample = decide(repository, sample_id="other_sample", decision="needs_correction")
    saved = [decide(repository, revision=revision, decision=decision, reviewer=f"reviewer_{revision}",
                    comment=f"Decision {revision + 1}.")
             for revision, decision in enumerate(("needs_correction", "accepted", "pending_review", "rejected"))]

    first = repository.list_sample_reviews("audit_1", "sample_1", 2, limit=2)
    last = repository.list_sample_reviews("audit_1", "sample_1", 2, limit=2, offset=first.next_offset)
    assert first.total == last.total == 4
    assert first.next_offset == 2 and last.next_offset is None
    assert [item.model_dump() for item in first.items + last.items] == [item.model_dump() for item in saved]
    assert repository.get_sample_review("audit_1", "sample_1", 2) == saved[-1]
    assert repository.get_sample_review("audit_1", "sample_1", 3) == other_pin
    assert repository.get_sample_review("audit_1", "other_sample", 2) == other_sample
    assert repository.list_sample_reviews("audit_2", "sample_1", 2).total == 0
    assert repository.get_sample_review("audit_2", "sample_1", 2).revision == 0
    assert repository.list_sample_reviews("audit_3", "other_sample", 2).total == 0
    beyond = repository.list_sample_reviews("audit_1", "sample_1", 2, offset=99)
    assert beyond.items == [] and beyond.total == 4 and beyond.next_offset is None


def test_sample_revision_mismatch_and_stale_review_are_separate_conflicts(repository):
    seed_result(repository.settings)
    with pytest.raises(SampleRevisionConflictError) as mismatch:
        decide(repository, sample_revision=1)
    assert mismatch.value.expected_sample_revision == 1
    assert mismatch.value.available_sample_revisions == (2,)
    assert repository.list_sample_reviews("audit_1", "sample_1", 2).total == 0
    saved = decide(repository)
    with pytest.raises(SampleReviewConflictError) as stale:
        decide(repository, decision="rejected")
    assert stale.value.sample_revision == 2
    assert stale.value.expected_review_version == 0 and stale.value.actual_revision == 1
    assert repository.get_sample_review("audit_1", "sample_1", 2) == saved
    assert repository.list_sample_reviews("audit_1", "sample_1", 2).total == 1
    with pytest.raises(SampleRevisionConflictError):
        repository.get_sample_review("audit_1", "sample_1", 1)
    with pytest.raises(SampleRevisionConflictError):
        repository.list_sample_reviews("audit_1", "sample_1", 1)


def test_competing_reviewers_cannot_both_advance_the_same_revision(repository):
    seed_result(repository.settings)
    barrier = Barrier(2)

    def submit(decision):
        writer = SampleReviewRepository(settings=repository.settings)
        barrier.wait(timeout=10)
        try:
            return decide(writer, decision=decision, reviewer=decision)
        except SampleReviewConflictError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, ("accepted", "rejected")))
    conflicts = [item for item in results if isinstance(item, SampleReviewConflictError)]
    winners = [item for item in results if not isinstance(item, SampleReviewConflictError)]
    assert len(conflicts) == len(winners) == 1
    assert repository.get_sample_review("audit_1", "sample_1", 2) == winners[0]
    assert repository.list_sample_reviews("audit_1", "sample_1", 2).total == 1


@pytest.mark.parametrize("state", ["queued", "running", "cancelled"])
def test_unprocessed_or_cancelled_samples_cannot_receive_any_decision(repository, state):
    audits = start_audit(repository.settings)
    if state == "running":
        claim = audits.claim_audit("audit_1", worker_id="worker_1")
        assert audits.claim_next_sample(claim) is not None
    elif state == "cancelled":
        audits.request_cancellation("audit_1")
    before = machine_snapshot(repository.settings)
    for decision in SampleReviewDecision:
        with pytest.raises(SampleReviewStateError) as caught:
            decide(repository, decision=decision)
        assert caught.value.status == state
    assert repository.get_sample_review("audit_1", "sample_1", 2).revision == 0
    assert repository.list_sample_reviews("audit_1", "sample_1", 2).total == 0
    assert machine_snapshot(repository.settings) == before


@pytest.mark.parametrize("parent_state", ["running", "failed", "cancelled"])
def test_completed_samples_remain_reviewable_regardless_of_parent_audit_status(repository, parent_state):
    audits, job_claim, _, _, _ = seed_result(repository.settings, extra_sample=True, incomplete=True)
    if parent_state == "failed":
        audits.fail_audit(job_claim, ApiError(
            code="WORKER_FAILED", message="Worker stopped.", request_id="request_1",
        ))
    elif parent_state == "cancelled":
        audits.request_cancellation("audit_1")
        audits.finalize_audit(job_claim)
    assert audits.get_audit("audit_1").status.value == parent_state
    before = machine_snapshot(repository.settings)
    assert decide(repository).decision is SampleReviewDecision.ACCEPTED
    assert machine_snapshot(repository.settings) == before


def test_failed_samples_are_reviewable_without_measurements_and_keep_failure_evidence(repository):
    audits = start_audit(repository.settings)
    job_claim = audits.claim_audit("audit_1", worker_id="worker_1")
    sample_claim = audits.claim_next_sample(job_claim)
    audits.persist_sample_failure(sample_claim, AuditFailure(
        failure_id="failure_1", audit_id="audit_1", sample_id="sample_1", sample_revision=2,
        stage="sample_processing", reason="processing_failed", detail="Could not inspect input.",
        created_at=datetime.now(UTC),
    ))
    before = machine_snapshot(repository.settings)
    assert audits.get_measurements("audit_1", "sample_1", 2) is None
    for revision, decision in enumerate(SampleReviewDecision):
        assert decide(repository, revision=revision, decision=decision).decision is decision
    assert machine_snapshot(repository.settings) == before


@pytest.mark.parametrize("failure", ["insert", "commit"])
def test_failed_review_write_retains_previous_decision_and_history(repository, failure):
    seed_result(repository.settings)
    previous = decide(repository)
    history = repository.list_sample_reviews("audit_1", "sample_1", 2)
    with database_connection(repository.settings) as connection:
        connection.execute("""CREATE FUNCTION reject_sample_review_write() RETURNS trigger LANGUAGE plpgsql AS $$
                              BEGIN RAISE EXCEPTION 'simulated sample review failure'; END; $$;""")
        if failure == "insert":
            connection.execute("""CREATE TRIGGER block_review BEFORE INSERT ON sample_reviews
                                  FOR EACH ROW EXECUTE FUNCTION reject_sample_review_write()""")
        else:
            connection.execute("""CREATE CONSTRAINT TRIGGER block_review AFTER INSERT ON sample_reviews
                                  DEFERRABLE INITIALLY DEFERRED
                                  FOR EACH ROW EXECUTE FUNCTION reject_sample_review_write()""")
    with pytest.raises(WorkflowStorageError):
        decide(repository, revision=1, decision="rejected")
    assert repository.get_sample_review("audit_1", "sample_1", 2) == previous
    assert repository.list_sample_reviews("audit_1", "sample_1", 2) == history


def test_audit_result_replays_preserve_review_and_reject_changed_evidence(repository):
    audits, _, claim, measurements, findings = seed_result(repository.settings, outcome="pass")
    saved = decide(repository, decision="needs_correction")
    audits.persist_sample_success(claim, measurements=measurements, findings=findings)
    with pytest.raises(WorkflowStorageError):
        audits.persist_sample_success(claim, measurements=measurements | {"extra": True}, findings=findings)
    assert repository.get_sample_review("audit_1", "sample_1", 2) == saved
    assert repository.list_sample_reviews("audit_1", "sample_1", 2).total == 1
    assert audits.get_measurements("audit_1", "sample_1", 2) == measurements


@pytest.mark.parametrize("audit_id,sample_id", [("missing", "sample_1"), ("audit_1", "missing")])
def test_missing_audit_samples_cannot_be_reviewed_or_given_empty_history(repository, audit_id, sample_id):
    seed_result(repository.settings)
    with pytest.raises(AuditSampleNotFoundError):
        decide(repository, audit_id=audit_id, sample_id=sample_id)
    with pytest.raises(AuditSampleNotFoundError):
        repository.get_sample_review(audit_id, sample_id, 2)
    with pytest.raises(AuditSampleNotFoundError):
        repository.list_sample_reviews(audit_id, sample_id, 2)


def test_upgrade_preserves_existing_audits_and_finding_reviews_without_inventing_sample_decisions(
    empty_database_settings, tmp_path, monkeypatch,
):
    prior_migrations = tmp_path / "prior_migrations"
    prior_migrations.mkdir()
    for source in database.MIGRATIONS_DIRECTORY.glob("*.sql"):
        if int(source.name.split("_", 1)[0]) <= 5:
            (prior_migrations / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(database, "MIGRATIONS_DIRECTORY", prior_migrations)
        initialize_database(empty_database_settings)
        # Seed the old schema directly: current audit writers require columns
        # deliberately absent until the upgrade under test has been applied.
        legacy_request = BatchAuditRequest(
            dataset_id="dataset_1", dataset_root="opaque-root", rulebook_id="book_1",
            rulebook_version=3, sample_ids=["sample_1"], sample_revisions={"sample_1": 2},
        ).model_dump(mode="json", exclude={"source_mode"})
        with database_connection(empty_database_settings) as connection:
            connection.execute(
                """INSERT INTO jobs (job_id, kind, status, progress, result, created_at,
                       updated_at, resource_kind, resource_id)
                   VALUES ('job_audit_1', 'audit', 'completed', %s, %s,
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'audit', 'audit_1')""",
                (Jsonb({"phase": "completed", "processed": 1, "total": 1, "fraction": 1.0}),
                 Jsonb({"kind": "audit", "id": "audit_1", "version": None, "href": "/api/v1/audits/audit_1"})),
            )
            connection.execute(
                """INSERT INTO audit_runs (audit_id, job_id, dataset_id, dataset_root,
                       rulebook_id, rulebook_version, status, request, sample_count,
                       processed_count, created_at, updated_at)
                   VALUES ('audit_1', 'job_audit_1', 'dataset_1', 'opaque-root', 'book_1',
                       3, 'completed', %s, 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                (Jsonb(legacy_request),),
            )
            connection.execute(
                """INSERT INTO audit_samples (audit_id, sample_id, sample_revision, ordinal,
                       status, started_at, finished_at, attempt_count)
                   VALUES ('audit_1', 'sample_1', 2, 0, 'completed',
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1)""",
            )
            connection.execute(
                """INSERT INTO audit_measurements
                       (audit_id, sample_id, sample_revision, document, created_at)
                   VALUES ('audit_1', 'sample_1', 2, %s, CURRENT_TIMESTAMP)""",
                (Jsonb({"image_id": "sample_1", "status": "completed", "review_status": "pending_review"}),),
            )
        finding = Finding(
            finding_id="finding_legacy", audit_id="audit_1", sample_id="sample_1",
            sample_revision=2, rulebook_id="book_1", rulebook_version=3, rule_id="r1",
            description="Use person labels.", check_name="allowed_labels", outcome="fail",
            origin="reviewer", evidence={"measurements": {"label": "car"}},
        )
        FindingRepository(settings=empty_database_settings).save_findings([finding])
        review_finding(finding.finding_id, FindingReviewRequest(
            expected_review_version=0, resolution="accepted", reviewer_id="reviewer_1", comment="Confirmed.",
        ), repository=FindingRepository(settings=empty_database_settings))
        before = machine_snapshot(empty_database_settings)

    initialize_database(empty_database_settings)
    initialize_database(empty_database_settings)

    repository = SampleReviewRepository(settings=empty_database_settings)
    after = machine_snapshot(empty_database_settings)
    for row in after["audit_runs"]:
        assert row.pop("source_mode") == "legacy"
    for row in after["audit_samples"]:
        assert row.pop("manifest_dataset_id") is None
    assert after == before
    assert repository.get_sample_review("audit_1", "sample_1", 2).revision == 0
    assert repository.list_sample_reviews("audit_1", "sample_1", 2).total == 0
    with database_connection(empty_database_settings, readonly=True) as connection:
        assert connection.execute(
            "SELECT source_mode FROM audit_runs WHERE audit_id = 'audit_1'"
        ).fetchone() == {"source_mode": "legacy"}
        assert connection.execute(
            "SELECT manifest_dataset_id FROM audit_samples "
            "WHERE audit_id = 'audit_1' AND sample_id = 'sample_1'"
        ).fetchone() == {"manifest_dataset_id": None}
    assert decide(repository).decision is SampleReviewDecision.ACCEPTED
