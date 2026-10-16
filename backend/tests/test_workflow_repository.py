"""Opt-in PostgreSQL coverage for claims, recovery and atomic sample writes."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import os
from threading import Barrier
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr
import pytest

from backend.app.core.config import DatabaseSettings
from backend.app.models.audit import BatchAuditRequest
from backend.app.models.finding import Finding
from backend.app.models.job import Job, JobKind, JobStatus, ResultReference
from backend.app.storage.database import database_connection, initialize_database
from backend.app.storage.workflow_repository import (
    AuditRepository,
    JobAlreadyFinishedError,
    JobClaimLostError,
    JobRepository,
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
    assert repository.get_measurements("audit_1", "sample_1", 1)["image_id"] == "sample_1"
    assert repository.get_job("audit_1").progress.processed == 1


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
