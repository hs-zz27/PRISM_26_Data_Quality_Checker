"""Opt-in PostgreSQL coverage for claims, recovery and atomic sample writes."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import os
from queue import Queue
from threading import Barrier
from time import monotonic, sleep
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr
import pytest

from backend.app.core.config import DatabaseSettings
from backend.app.models.audit import AuditFailure, BatchAuditRequest
from backend.app.models.finding import Finding
from backend.app.models.job import ApiError, Job, JobKind, JobStatus, Progress, ResultReference
from backend.app.storage import workflow_repository
from backend.app.storage.database import CommitOutcomeUnknownError, database_connection, initialize_database
from backend.app.storage.workflow_repository import (
    AuditRepository,
    JobCancellationError,
    JobAlreadyFinishedError,
    JobClaimLostError,
    JobRepository,
    RetryableWorkflowStorageError,
    SampleProcessingTimeoutError,
    WorkflowStorageError,
)


@pytest.fixture
def workflow_settings():
    conninfo = os.environ.get("PRISM_TEST_DATABASE_URL")
    if not conninfo:
        pytest.skip("Set PRISM_TEST_DATABASE_URL to run PostgreSQL integration tests.")
    schema = f"prism_workflow_{uuid4().hex}"
    with psycopg.connect(conninfo, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        settings = DatabaseSettings(conninfo=SecretStr(make_conninfo(conninfo, options=f"-c search_path={schema}")))
        initialize_database(settings)
        yield settings
    finally:
        with psycopg.connect(conninfo, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def queued_audit_claim(workflow_settings):
    repository = AuditRepository(settings=workflow_settings)
    now = datetime(2026, 10, 17, 12, tzinfo=UTC)
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="opaque-root", rulebook_id="book_1",
        rulebook_version=2, sample_ids=["sample_1"],
    )
    audit, _ = repository.start_audit(
        request, audit_id="audit_1", job_id="job_1", now=now,
    )
    claim = repository.jobs.claim_next_job(worker_id="worker_a", now=now)
    assert claim is not None
    return repository, audit, claim, now


def _saved_workflow_state(settings):
    with database_connection(settings, readonly=True) as connection:
        return (
            connection.execute("SELECT * FROM jobs ORDER BY job_id").fetchall(),
            connection.execute("SELECT * FROM audit_runs ORDER BY audit_id").fetchall(),
            connection.execute(
                "SELECT * FROM audit_samples ORDER BY audit_id, ordinal"
            ).fetchall(),
            connection.execute(
                "SELECT * FROM audit_measurements ORDER BY audit_id, sample_id, sample_revision"
            ).fetchall(),
            connection.execute("SELECT * FROM findings ORDER BY finding_id").fetchall(),
            connection.execute("SELECT * FROM audit_failures ORDER BY failure_id").fetchall(),
        )


def _run_after_lock_wait(
    repository, operation, *, monkeypatch, lock_query, lock_params=(), before, after,
):
    """Advance the live clock only after PostgreSQL confirms a real lock wait."""
    timestamp = workflow_repository._timestamp
    connect = repository._connection
    current_time = [before]
    worker_pids = Queue()

    def controlled_timestamp(value=None):
        return timestamp(current_time[0] if value is None else value)

    @contextmanager
    def observed_connection(*, write=False):
        with connect(write=write) as connection:
            connection.execute("SET LOCAL lock_timeout = '10s'")
            connection.execute("SET LOCAL statement_timeout = '15s'")
            worker_pids.put(connection.info.backend_pid)
            yield connection

    with monkeypatch.context() as patch:
        patch.setattr(workflow_repository, "_timestamp", controlled_timestamp)
        patch.setattr(repository, "_connection", observed_connection)
        with ThreadPoolExecutor(max_workers=1) as executor:
            # Release the blocker before waiting for the worker, including
            # assertion/error paths; SQL timeouts also bound accidental hangs.
            with database_connection(repository.settings) as blocker:
                blocker.execute(lock_query, lock_params)
                future = executor.submit(operation)
                worker_pid = worker_pids.get(timeout=5)
                deadline = monotonic() + 5
                while True:
                    blocking_pids = blocker.execute(
                        "SELECT pg_blocking_pids(%s) AS pids", (worker_pid,),
                    ).fetchone()["pids"]
                    if blocker.info.backend_pid in blocking_pids:
                        break
                    if future.done():
                        future.result()
                        pytest.fail("The worker finished without waiting for the held lock.")
                    if monotonic() >= deadline:
                        pytest.fail("The worker did not reach the held PostgreSQL lock.")
                    sleep(0.01)
                current_time[0] = after
            return future.result(timeout=10)


def test_prepare_claimed_audit_preserves_queue_ownership(queued_audit_claim, workflow_settings):
    repository, audit, claim, now = queued_audit_claim
    assert repository.get_audit(audit.audit_id).status is JobStatus.QUEUED
    original_job = _saved_workflow_state(workflow_settings)[0][0]

    prepared = repository.prepare_claimed_audit(claim, now=now + timedelta(seconds=1))

    saved_job = _saved_workflow_state(workflow_settings)[0][0]
    assert prepared.claim_token == claim.claim_token == saved_job["claim_token"]
    assert prepared.worker_id == claim.worker_id == saved_job["claimed_by"]
    assert prepared.lease_until == claim.lease_until == saved_job["lease_until"]
    assert saved_job["attempt"] == original_job["attempt"] == 1
    assert prepared.job.status is JobStatus.RUNNING
    assert prepared.job.progress.phase == "processing"
    assert repository.get_audit(audit.audit_id).status is JobStatus.RUNNING


def test_prepare_claimed_audit_uses_renewed_database_lease(queued_audit_claim):
    repository, _, claim, now = queued_audit_claim
    renewal_time = now + timedelta(seconds=30)
    repository.jobs.renew_claim(claim, lease_seconds=60, now=renewal_time)

    prepared = repository.prepare_claimed_audit(claim, now=now + timedelta(seconds=61))

    assert prepared.lease_until == renewal_time + timedelta(seconds=60)
    assert prepared.claim_token == claim.claim_token


@pytest.mark.parametrize("field,value", [
    ("claim_token", "different_token"),
    ("worker_id", "different_worker"),
    ("resource_id", "different_audit"),
])
def test_prepare_claimed_audit_rejects_mismatched_claim_without_mutation(
    queued_audit_claim, workflow_settings, field, value,
):
    repository, _, claim, now = queued_audit_claim
    before = _saved_workflow_state(workflow_settings)

    with pytest.raises(JobClaimLostError):
        repository.prepare_claimed_audit(replace(claim, **{field: value}), now=now)

    assert _saved_workflow_state(workflow_settings) == before


def test_prepare_claimed_audit_rejects_wrong_job_without_mutation(
    queued_audit_claim, workflow_settings,
):
    repository, _, claim, now = queued_audit_claim
    other_audit, other_job = repository.start_audit(
        repository.get_request(claim.resource_id), audit_id="audit_2", job_id="job_2", now=now,
    )
    other_claim = repository.jobs.claim_job(other_job.job_id, worker_id="worker_b", now=now)
    assert other_claim is not None
    mismatched = replace(claim, job=other_claim.job, resource_id=other_audit.audit_id)
    before = _saved_workflow_state(workflow_settings)

    with pytest.raises(JobClaimLostError):
        repository.prepare_claimed_audit(mismatched, now=now)

    assert _saved_workflow_state(workflow_settings) == before


@pytest.mark.parametrize("field,value", [
    ("resource_kind", "dataset"),
    ("resource_id", None),
])
def test_prepare_claimed_audit_requires_audit_resource(
    queued_audit_claim, workflow_settings, field, value,
):
    repository, _, claim, now = queued_audit_claim
    before = _saved_workflow_state(workflow_settings)

    with pytest.raises(ValueError):
        repository.prepare_claimed_audit(replace(claim, **{field: value}), now=now)

    assert _saved_workflow_state(workflow_settings) == before


@pytest.mark.parametrize("column,value", [
    ("kind", "dataset_import"),
    ("resource_kind", "dataset"),
    ("resource_id", "different_audit"),
])
def test_prepare_claimed_audit_rejects_inconsistent_saved_job_metadata(
    queued_audit_claim, workflow_settings, column, value,
):
    repository, _, claim, now = queued_audit_claim
    with database_connection(workflow_settings) as connection:
        connection.execute(
            sql.SQL("UPDATE jobs SET {} = %s WHERE job_id = %s").format(sql.Identifier(column)),
            (value, claim.job.job_id),
        )
    before = _saved_workflow_state(workflow_settings)

    with pytest.raises(JobClaimLostError):
        repository.prepare_claimed_audit(claim, now=now)

    assert _saved_workflow_state(workflow_settings) == before


def test_prepare_claimed_audit_rejects_expired_claim_without_mutation(
    queued_audit_claim, workflow_settings,
):
    repository, _, claim, _ = queued_audit_claim
    before = _saved_workflow_state(workflow_settings)

    with pytest.raises(JobClaimLostError):
        repository.prepare_claimed_audit(claim, now=claim.lease_until)

    assert _saved_workflow_state(workflow_settings) == before


def test_prepare_claimed_audit_rejects_replaced_claim_without_mutation(
    queued_audit_claim, workflow_settings,
):
    repository, _, original, _ = queued_audit_claim
    replacement_time = original.lease_until + timedelta(seconds=1)
    replacement = repository.jobs.claim_next_job(worker_id="worker_b", now=replacement_time)
    assert replacement is not None
    before = _saved_workflow_state(workflow_settings)

    with pytest.raises(JobClaimLostError):
        repository.prepare_claimed_audit(original, now=replacement_time)

    assert _saved_workflow_state(workflow_settings) == before


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "cancelling"])
def test_prepare_claimed_audit_does_not_revive_contradictory_audit_state(
    queued_audit_claim, workflow_settings, status,
):
    repository, audit, claim, now = queued_audit_claim
    with database_connection(workflow_settings) as connection:
        connection.execute(
            "UPDATE audit_runs SET status = %s WHERE audit_id = %s", (status, audit.audit_id),
        )
    before = _saved_workflow_state(workflow_settings)

    with pytest.raises(WorkflowStorageError):
        repository.prepare_claimed_audit(claim, now=now)

    assert _saved_workflow_state(workflow_settings) == before


def test_prepare_claimed_audit_preserves_active_sample_with_same_token(
    queued_audit_claim, workflow_settings,
):
    repository, _, claim, now = queued_audit_claim
    prepared = repository.prepare_claimed_audit(claim, now=now)
    sample_claim = repository.claim_next_sample(prepared, now=now)
    assert sample_claim is not None
    original_samples = _saved_workflow_state(workflow_settings)[2]

    repository.prepare_claimed_audit(prepared, now=now + timedelta(seconds=1))

    assert _saved_workflow_state(workflow_settings)[2] == original_samples
    replayed = repository.claim_next_sample(prepared, now=now + timedelta(seconds=1))
    assert replayed == sample_claim
    assert _saved_workflow_state(workflow_settings)[2] == original_samples


def test_concurrent_workers_only_claim_one_job(workflow_settings):
    repository = JobRepository(settings=workflow_settings)
    now = datetime(2026, 10, 16, 20, tzinfo=UTC)
    repository.create_job(Job.queued(job_id="job_1", kind=JobKind.AUDIT, total=1, now=now))
    barrier = Barrier(2)

    def claim(worker):
        barrier.wait(timeout=10)
        return repository.claim_job("job_1", worker_id=worker, now=now)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, ("worker_a", "worker_b")))
    assert sum(claim is not None for claim in claims) == 1
    assert repository.get_job("job_1").status is JobStatus.RUNNING


def test_reclaim_rejects_stale_worker_finalization(workflow_settings):
    repository = JobRepository(settings=workflow_settings)
    now = datetime(2026, 10, 16, 20, tzinfo=UTC)
    repository.create_job(Job.queued(job_id="job_1", kind=JobKind.AUDIT, total=1, now=now))
    first = repository.claim_job("job_1", worker_id="worker_a", lease_seconds=1, now=now)
    assert first is not None
    with pytest.raises(JobClaimLostError):
        repository.renew_claim(first, now=now + timedelta(seconds=2))
    second = repository.claim_job(
        "job_1", worker_id="worker_b", lease_seconds=60, now=now + timedelta(seconds=2),
    )
    assert second is not None and second.claim_token != first.claim_token
    result = ResultReference(kind="audit", id="audit_1", href="/api/v1/audits/audit_1")
    with pytest.raises(JobClaimLostError):
        repository.complete_job(first, result, now=now + timedelta(seconds=3))
    assert repository.complete_job(second, result, now=now + timedelta(seconds=3)).status is JobStatus.COMPLETED


def test_queued_cancellation_is_terminal_and_idempotence_is_explicit(workflow_settings):
    repository = JobRepository(settings=workflow_settings)
    now = datetime(2026, 10, 16, 20, tzinfo=UTC)
    repository.create_job(Job.queued(job_id="job_1", kind=JobKind.AUDIT, total=1, now=now))
    assert repository.request_cancellation("job_1", now=now).status is JobStatus.CANCELLED
    with pytest.raises(JobAlreadyFinishedError):
        repository.request_cancellation("job_1", now=now)


def test_audit_sample_result_commits_measurements_findings_and_progress(workflow_settings):
    repository = AuditRepository(settings=workflow_settings)
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="opaque-root", rulebook_id="book_1",
        rulebook_version=2, sample_ids=["sample_1"], allowed_labels=["person"],
    )
    audit, _ = repository.start_audit(request, audit_id="audit_1", job_id="job_1")
    claim = repository.claim_audit(audit.audit_id, worker_id="worker_a")
    assert claim is not None
    sample_claim = repository.claim_next_sample(claim)
    assert sample_claim is not None
    finding = Finding(
        finding_id="finding_1", audit_id="audit_1", sample_id="sample_1", sample_revision=1,
        object_id=None, rulebook_id="book_1", rulebook_version=2, rule_id="r1",
        description="A rule.", check_name=None, outcome="unavailable",
        reason="unsupported_check", origin="reviewer", evidence={"measurements": {}},
    )
    job = repository.persist_sample_success(
        sample_claim,
        measurements={"image_id": "sample_1", "review_status": "pending_review"},
        findings=[finding],
    )
    assert job.status is JobStatus.COMPLETED
    replayed = repository.persist_sample_success(
        sample_claim,
        measurements={"image_id": "sample_1", "review_status": "pending_review"},
        findings=[finding],
    )
    assert replayed.status is JobStatus.COMPLETED
    assert repository.get_measurements("audit_1", "sample_1", 1)["image_id"] == "sample_1"
    assert repository.get_job("audit_1").progress.processed == 1


def test_attempt_increment_is_idempotent_for_one_operation(workflow_settings):
    repository = AuditRepository(settings=workflow_settings)
    now = datetime(2026, 10, 16, 20, tzinfo=UTC)
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="opaque-root", rulebook_id="book_1",
        rulebook_version=2, sample_ids=["sample_1"], allowed_labels=None,
    )
    audit, _ = repository.start_audit(request, audit_id="audit_attempt_op", job_id="job_attempt_op", now=now)
    claim = repository.claim_audit(audit.audit_id, worker_id="worker_a", now=now)
    assert claim is not None
    sample_claim = repository.claim_next_sample(claim, now=now)
    assert sample_claim is not None

    first = repository.increment_sample_attempt(
        sample_claim, operation_id="attempt-operation-1", now=now,
    )
    replayed = repository.increment_sample_attempt(
        sample_claim, operation_id="attempt-operation-1", now=now,
    )

    assert first == replayed == 2


def test_audit_claim_replay_requires_the_same_acquisition_identity(queued_audit_claim):
    repository, audit, _, now = queued_audit_claim
    second_audit, _ = repository.start_audit(
        repository.get_request(audit.audit_id), audit_id="audit_acquisition", job_id="job_acquisition", now=now,
    )
    first = repository.claim_audit(
        second_audit.audit_id, worker_id="shared_worker", acquisition_id="acquisition_one", now=now,
    )
    assert first is not None
    # A shared worker label alone must never disclose another execution's token.
    assert repository.claim_audit(second_audit.audit_id, worker_id="shared_worker", now=now) is None
    assert repository.claim_audit(
        second_audit.audit_id, worker_id="shared_worker", acquisition_id="acquisition_two", now=now,
    ) is None
    replay = repository.claim_audit(
        second_audit.audit_id, worker_id="shared_worker", acquisition_id="acquisition_one", now=now,
    )
    assert replay is not None
    assert replay.claim_token == first.claim_token
    expired_at = first.lease_until + timedelta(seconds=1)
    with pytest.raises(JobClaimLostError):
        repository.claim_audit(
            second_audit.audit_id, worker_id="shared_worker", acquisition_id="acquisition_one", now=expired_at,
        )
    replacement = repository.claim_audit(
        second_audit.audit_id, worker_id="shared_worker", acquisition_id="acquisition_two", now=expired_at,
    )
    assert replacement is not None and replacement.claim_token != first.claim_token


@pytest.mark.parametrize("mutation", ["success", "attempt", "claim", "sample_claim"])
def test_commit_without_acknowledgement_reconciles_without_duplicate_writes(
    queued_audit_claim, monkeypatch, mutation,
):
    repository, audit, claim, now = queued_audit_claim
    if mutation == "claim":
        audit, _ = repository.start_audit(
            repository.get_request(audit.audit_id), audit_id="audit_lost_ack", job_id="job_lost_ack", now=now,
        )
        operation = lambda: repository.claim_audit(
            audit.audit_id, worker_id="worker_a", acquisition_id="stable_acquisition", now=now,
        )
    else:
        repository.prepare_claimed_audit(claim, now=now)
        if mutation == "sample_claim":
            operation = lambda: repository.claim_next_sample(claim, now=now)
        else:
            sample = repository.claim_next_sample(claim, now=now)
            assert sample is not None
            if mutation == "attempt":
                operation = lambda: repository.increment_sample_attempt(
                    sample, operation_id="stable_attempt", now=now,
                )
            else:
                operation = lambda: repository.persist_sample_success(
                    sample, findings=[], now=now,
                    measurements={"image_id": "sample_1", "review_status": "pending_review"},
                )

    connect = workflow_repository.database_connection
    acknowledgement_lost = False

    @contextmanager
    def lose_one_acknowledgement(settings, *, readonly=False):
        nonlocal acknowledgement_lost
        with connect(settings, readonly=readonly) as connection:
            yield connection
        if not readonly and not acknowledgement_lost:
            acknowledgement_lost = True
            # The transaction above really committed. No SQLSTATE is available
            # when the client loses the connection before receiving its ACK.
            raise CommitOutcomeUnknownError("commit acknowledgement lost") from psycopg.OperationalError()

    monkeypatch.setattr(workflow_repository, "database_connection", lose_one_acknowledgement)
    with pytest.raises(RetryableWorkflowStorageError) as caught:
        operation()
    assert caught.value.commit_outcome_unknown
    committed_state = _saved_workflow_state(repository.settings)
    reconciled = operation()
    if mutation == "attempt":
        assert reconciled == 2
    elif mutation == "claim":
        assert reconciled is not None and reconciled.claim_token == "stable_acquisition"
    elif mutation == "sample_claim":
        assert reconciled is not None
        assert reconciled.sample_id == "sample_1"
        assert reconciled.attempt_count == 1
        assert reconciled.claim_token == claim.claim_token
    else:
        assert reconciled.status is JobStatus.COMPLETED
        assert reconciled.progress.processed == 1
    state = _saved_workflow_state(repository.settings)
    if mutation == "claim":
        claimed_job = next(row for row in state[0] if row["job_id"] == "job_lost_ack")
        assert claimed_job["attempt"] == 1
    elif mutation == "attempt":
        assert state[2][0]["attempt_count"] == 2
    elif mutation == "sample_claim":
        assert state == committed_state
    else:
        assert len(state[3]) == 1


def test_expired_sample_claim_cannot_persist_a_result(workflow_settings):
    repository = AuditRepository(settings=workflow_settings)
    now = datetime(2026, 10, 16, 20, tzinfo=UTC)
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="opaque-root", rulebook_id="book_1",
        rulebook_version=2, sample_ids=["sample_1"], allowed_labels=None,
    )
    audit, _ = repository.start_audit(request, audit_id="audit_expired", job_id="job_expired", now=now)
    claim = repository.claim_audit(audit.audit_id, worker_id="worker_a", lease_seconds=1, now=now)
    assert claim is not None
    sample_claim = repository.claim_next_sample(claim, lease_seconds=1, now=now)
    assert sample_claim is not None
    finding = Finding(
        finding_id="finding_expired", audit_id="audit_expired", sample_id="sample_1",
        sample_revision=1, object_id=None, rulebook_id="book_1", rulebook_version=2,
        rule_id="r1", description="A rule.", outcome="unavailable",
        reason="unsupported_check", origin="reviewer", evidence={"measurements": {}},
    )
    with pytest.raises(JobClaimLostError):
        repository.persist_sample_success(
            sample_claim, measurements={"image_id": "sample_1"}, findings=[finding],
            now=now + timedelta(seconds=2),
        )


def test_sample_deadline_is_persisted_and_checkpoint_rejects_expiry(workflow_settings):
    repository = AuditRepository(settings=workflow_settings)
    now = datetime(2026, 10, 16, 20, tzinfo=UTC)
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="opaque-root", rulebook_id="book_1",
        rulebook_version=2, sample_ids=["sample_1"], allowed_labels=None,
    )
    audit, _ = repository.start_audit(request, audit_id="audit_deadline", job_id="job_deadline", now=now)
    claim = repository.claim_audit(audit.audit_id, worker_id="worker_a", now=now)
    assert claim is not None
    sample_claim = repository.claim_next_sample(
        claim, lease_seconds=60, processing_seconds=10, now=now,
    )
    assert sample_claim is not None
    assert sample_claim.processing_deadline == now + timedelta(seconds=10)
    with database_connection(workflow_settings, readonly=True) as connection:
        assert connection.execute(
            "SELECT processing_deadline, attempt_count FROM audit_samples WHERE audit_id = %s",
            (audit.audit_id,),
        ).fetchone() == {
            "processing_deadline": now + timedelta(seconds=10), "attempt_count": 1,
        }

    with pytest.raises(SampleProcessingTimeoutError):
        repository.checkpoint_sample_claim(
            sample_claim, now=now + timedelta(seconds=10), renew=True,
        )


def test_sample_checkpoint_observes_cancellation_before_persistence(workflow_settings):
    repository = AuditRepository(settings=workflow_settings)
    now = datetime(2026, 10, 16, 20, tzinfo=UTC)
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="opaque-root", rulebook_id="book_1",
        rulebook_version=2, sample_ids=["sample_1"], allowed_labels=None,
    )
    audit, job = repository.start_audit(
        request, audit_id="audit_cancel_checkpoint", job_id="job_cancel_checkpoint", now=now,
    )
    claim = repository.claim_audit(audit.audit_id, worker_id="worker_a", now=now)
    assert claim is not None
    sample_claim = repository.claim_next_sample(claim, now=now)
    assert sample_claim is not None
    assert repository.request_cancellation(audit.audit_id, now=now).status is JobStatus.CANCELLING

    with pytest.raises(JobCancellationError):
        repository.checkpoint_sample_claim(sample_claim, now=now + timedelta(seconds=1))


def test_timeout_finalization_cannot_override_requested_cancellation(workflow_settings):
    repository = AuditRepository(settings=workflow_settings)
    now = datetime(2026, 10, 16, 20, tzinfo=UTC)
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="opaque-root", rulebook_id="book_1",
        rulebook_version=2, sample_ids=["sample_1"], allowed_labels=None,
    )
    audit, job = repository.start_audit(
        request, audit_id="audit_cancel_timeout", job_id="job_cancel_timeout", now=now,
    )
    claim = repository.claim_audit(audit.audit_id, worker_id="worker_a", now=now)
    assert claim is not None
    sample_claim = repository.claim_next_sample(
        claim, lease_seconds=60, processing_seconds=10, now=now,
    )
    assert sample_claim is not None
    assert repository.request_cancellation(audit.audit_id, now=now + timedelta(seconds=5)).status is JobStatus.CANCELLING
    failure = AuditFailure(
        failure_id="failure_cancel_timeout", audit_id=audit.audit_id,
        sample_id="sample_1", sample_revision=1, stage="sample_processing",
        reason="processing_timeout", detail="expired", created_at=now,
    )

    with pytest.raises(JobCancellationError):
        repository.record_sample_timeout(sample_claim, failure, now=now + timedelta(seconds=10))

    assert repository.cancel_sample_claim(sample_claim, now=now + timedelta(seconds=10)).status is JobStatus.CANCELLED


@pytest.mark.parametrize("operation", ["renew", "progress", "complete", "fail", "cancel"])
def test_job_mutations_reject_a_lease_that_expires_while_waiting(
    queued_audit_claim, workflow_settings, monkeypatch, operation,
):
    audits, _, claim, now = queued_audit_claim
    repository = audits.jobs
    if operation == "cancel":
        repository.request_cancellation(claim.job.job_id, now=now)
    actions = {
        "renew": lambda: repository.renew_claim(claim),
        "progress": lambda: repository.update_progress(
            claim, Progress(phase="processing", processed=1, total=1, fraction=1.0),
        ),
        "complete": lambda: repository.complete_job(
            claim, ResultReference(kind="audit", id="audit_1", href="/api/v1/audits/audit_1"),
        ),
        "fail": lambda: repository.fail_job(
            claim, ApiError(code="worker_failed", message="Worker failed.", request_id="request_1"),
        ),
        "cancel": lambda: repository.finalize_cancellation(claim),
    }
    before = _saved_workflow_state(workflow_settings)

    with pytest.raises(JobClaimLostError):
        _run_after_lock_wait(
            repository, actions[operation], monkeypatch=monkeypatch,
            lock_query="SELECT job_id FROM jobs WHERE job_id = %s FOR UPDATE",
            lock_params=(claim.job.job_id,), before=now, after=claim.lease_until,
        )

    assert _saved_workflow_state(workflow_settings) == before


@pytest.mark.parametrize("operation", ["renew", "success", "failure"])
@pytest.mark.parametrize("locked_row", ["job", "sample"])
@pytest.mark.parametrize("expired_lease", ["job", "sample"])
def test_sample_mutations_recheck_both_leases_after_all_ownership_locks(
    queued_audit_claim, workflow_settings, monkeypatch, operation, locked_row, expired_lease,
):
    repository, audit, claim, now = queued_audit_claim
    repository.prepare_claimed_audit(claim, now=now)
    sample_claim = repository.claim_next_sample(
        claim, lease_seconds=120 if expired_lease == "job" else 30, now=now,
    )
    assert sample_claim is not None
    expiry = claim.lease_until if expired_lease == "job" else sample_claim.lease_until
    measurements = {"image_id": sample_claim.sample_id, "review_status": "pending_review"}
    finding = Finding(
        finding_id="finding_1", audit_id=audit.audit_id, sample_id=sample_claim.sample_id,
        sample_revision=sample_claim.sample_revision, object_id=None,
        rulebook_id=audit.rulebook_id, rulebook_version=audit.rulebook_version,
        rule_id="rule_1", description="A rule.", outcome="unavailable",
        reason="unsupported_check", origin="reviewer", evidence={"measurements": {}},
    )
    failure = AuditFailure(
        failure_id="failure_1", audit_id=audit.audit_id, sample_id=sample_claim.sample_id,
        sample_revision=sample_claim.sample_revision, stage="collection",
        reason="missing_input", detail="The sample input is missing.", created_at=now,
    )
    actions = {
        "renew": lambda: repository.renew_sample_claim(sample_claim),
        "success": lambda: repository.persist_sample_success(
            sample_claim, measurements=measurements, findings=[finding],
        ),
        "failure": lambda: repository.persist_sample_failure(
            sample_claim, failure, measurements=measurements,
        ),
    }
    if locked_row == "job":
        lock_query = "SELECT job_id FROM jobs WHERE job_id = %s FOR UPDATE"
        lock_params = (claim.job.job_id,)
    else:
        lock_query = "SELECT sample_id FROM audit_samples WHERE audit_id = %s FOR UPDATE"
        lock_params = (audit.audit_id,)
    before = _saved_workflow_state(workflow_settings)

    with pytest.raises(JobClaimLostError):
        _run_after_lock_wait(
            repository, actions[operation], monkeypatch=monkeypatch,
            lock_query=lock_query, lock_params=lock_params, before=now, after=expiry,
        )

    assert _saved_workflow_state(workflow_settings) == before


@pytest.mark.parametrize("operation,locked_row", [
    ("finalize", "job"),
    ("fail", "job"),
    ("fail", "sample"),
    ("prepare", "job"),
])
def test_audit_transitions_reject_expiry_during_lock_wait(
    queued_audit_claim, workflow_settings, monkeypatch, operation, locked_row,
):
    repository, audit, claim, now = queued_audit_claim
    repository.prepare_claimed_audit(claim, now=now)
    sample_claim = repository.claim_next_sample(claim, lease_seconds=120, now=now)
    assert sample_claim is not None
    actions = {
        "finalize": lambda: repository.finalize_audit(claim),
        "fail": lambda: repository.fail_audit(
            claim, ApiError(code="worker_failed", message="Worker failed.", request_id="request_1"),
        ),
        "prepare": lambda: repository.prepare_claimed_audit(claim),
    }
    if locked_row == "job":
        lock_query = "SELECT job_id FROM jobs WHERE job_id = %s FOR UPDATE"
        lock_params = (claim.job.job_id,)
    else:
        lock_query = "SELECT sample_id FROM audit_samples WHERE audit_id = %s FOR UPDATE"
        lock_params = (audit.audit_id,)
    before = _saved_workflow_state(workflow_settings)

    with pytest.raises(JobClaimLostError):
        _run_after_lock_wait(
            repository, actions[operation], monkeypatch=monkeypatch,
            lock_query=lock_query, lock_params=lock_params,
            before=now, after=claim.lease_until,
        )

    assert _saved_workflow_state(workflow_settings) == before


def test_preparing_takeover_rechecks_expiry_after_old_sample_reset_wait(
    queued_audit_claim, workflow_settings, monkeypatch,
):
    repository, audit, original, now = queued_audit_claim
    repository.prepare_claimed_audit(original, now=now)
    assert repository.claim_next_sample(original, now=now) is not None
    takeover_time = original.lease_until + timedelta(seconds=1)
    replacement = repository.jobs.claim_next_job(worker_id="worker_b", now=takeover_time)
    assert replacement is not None
    before = _saved_workflow_state(workflow_settings)

    with pytest.raises(JobClaimLostError):
        _run_after_lock_wait(
            repository, lambda: repository.prepare_claimed_audit(replacement),
            monkeypatch=monkeypatch,
            lock_query="SELECT sample_id FROM audit_samples WHERE audit_id = %s FOR UPDATE",
            lock_params=(audit.audit_id,), before=takeover_time, after=replacement.lease_until,
        )

    assert _saved_workflow_state(workflow_settings) == before


@pytest.mark.parametrize("expired", [False, True])
@pytest.mark.parametrize("locked_resource", ["job_row", "sample_table"])
def test_sample_claim_checks_parent_and_starts_lease_after_lock_wait(
    queued_audit_claim, workflow_settings, monkeypatch, expired, locked_resource,
):
    repository, _, claim, now = queued_audit_claim
    repository.prepare_claimed_audit(claim, now=now)
    before = _saved_workflow_state(workflow_settings)
    released_at = claim.lease_until if expired else now + timedelta(seconds=10)
    if locked_resource == "job_row":
        lock_query = "SELECT job_id FROM jobs WHERE job_id = %s FOR UPDATE"
        lock_params = (claim.job.job_id,)
    else:
        lock_query = "LOCK TABLE audit_samples IN ACCESS EXCLUSIVE MODE"
        lock_params = ()

    def claim_after_wait():
        return _run_after_lock_wait(
            repository, lambda: repository.claim_next_sample(claim, lease_seconds=30),
            monkeypatch=monkeypatch, lock_query=lock_query, lock_params=lock_params,
            before=now, after=released_at,
        )

    if expired:
        with pytest.raises(JobClaimLostError):
            claim_after_wait()
        assert _saved_workflow_state(workflow_settings) == before
    else:
        sample_claim = claim_after_wait()
        assert sample_claim is not None
        assert sample_claim.lease_until == released_at + timedelta(seconds=30)
        saved_sample = _saved_workflow_state(workflow_settings)[2][0]
        assert saved_sample["lease_until"] == sample_claim.lease_until
        assert saved_sample["started_at"] == released_at


def test_reclaimed_audit_lease_starts_after_old_sample_reset_wait(
    queued_audit_claim, workflow_settings, monkeypatch,
):
    repository, audit, original, now = queued_audit_claim
    repository.prepare_claimed_audit(original, now=now)
    assert repository.claim_next_sample(original, now=now) is not None
    takeover_time = original.lease_until
    released_at = takeover_time + timedelta(minutes=2)

    replacement = _run_after_lock_wait(
        repository,
        lambda: repository.claim_audit(audit.audit_id, worker_id="worker_b", lease_seconds=30),
        monkeypatch=monkeypatch,
        lock_query="SELECT sample_id FROM audit_samples WHERE audit_id = %s FOR UPDATE",
        lock_params=(audit.audit_id,), before=takeover_time, after=released_at,
    )

    assert replacement is not None
    assert replacement.claim_token != original.claim_token
    assert replacement.lease_until == released_at + timedelta(seconds=30)
    assert replacement.job.updated_at == released_at
    saved_samples = _saved_workflow_state(workflow_settings)[2]
    assert all(sample["status"] == "queued" for sample in saved_samples)


@pytest.mark.parametrize("operation", ["job", "next_job", "audit"])
def test_new_claim_gets_a_full_lease_after_waiting_for_database_lock(
    workflow_settings, monkeypatch, operation,
):
    repository = AuditRepository(settings=workflow_settings)
    now = datetime(2026, 10, 17, 12, tzinfo=UTC)
    audit, job = repository.start_audit(
        BatchAuditRequest(
            dataset_id="dataset_1", dataset_root="opaque-root", rulebook_id="book_1",
            rulebook_version=2, sample_ids=["sample_1"],
        ),
        audit_id="audit_1", job_id="job_1", now=now,
    )
    actions = {
        "job": lambda: repository.jobs.claim_job(job.job_id, worker_id="worker_a", lease_seconds=30),
        "next_job": lambda: repository.jobs.claim_next_job(worker_id="worker_a", lease_seconds=30),
        "audit": lambda: repository.claim_audit(audit.audit_id, worker_id="worker_a", lease_seconds=30),
    }
    released_at = now + timedelta(minutes=2)

    claim = _run_after_lock_wait(
        repository if operation == "audit" else repository.jobs,
        actions[operation], monkeypatch=monkeypatch,
        # SKIP LOCKED skips row locks, but still waits for table-level locks.
        lock_query="LOCK TABLE jobs IN ACCESS EXCLUSIVE MODE",
        before=now, after=released_at,
    )

    assert claim is not None
    assert claim.lease_until == released_at + timedelta(seconds=30)
    assert claim.job.updated_at == released_at
    saved_job = _saved_workflow_state(workflow_settings)[0][0]
    assert saved_job["lease_until"] == claim.lease_until
