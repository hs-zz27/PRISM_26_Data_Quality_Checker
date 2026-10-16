"""PostgreSQL workflow tests for durable audit progress and recovery.

These tests are intentionally opt-in with the rest of the PostgreSQL suite. A
local unit-test run does not need a database; CI or a developer can provide
``PRISM_TEST_DATABASE_URL`` to exercise the transaction boundaries.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import os
from time import sleep
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr
import pytest

from backend.app.core.config import DatabaseSettings
from backend.app.models.audit import BatchAuditRequest
from backend.app.models.finding import FindingResolution
from backend.app.models.rule import ConfirmationState, Rule, Rulebook
from backend.app.services.batch_audit import BatchAuditService
from backend.app.storage.database import initialize_database
from backend.app.storage.repository import RulebookRepository
from backend.app.storage.workflow_repository import AuditRepository, FindingRepository


NOW = datetime.now(UTC) - timedelta(seconds=1)
SOURCE = "Every object uses one of the allowed labels."


@pytest.fixture
def workflow_settings():
    conninfo = os.environ.get("PRISM_TEST_DATABASE_URL")
    if not conninfo:
        pytest.skip("Set PRISM_TEST_DATABASE_URL to run PostgreSQL integration tests.")

    schema = f"prism_workflow_{uuid4().hex}"
    with psycopg.connect(conninfo, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        settings = DatabaseSettings(conninfo=SecretStr(make_conninfo(
            conninfo, options=f"-c search_path={schema}",
        )))
        initialize_database(settings)
        yield settings
    finally:
        with psycopg.connect(conninfo, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def clean_rulebook() -> Rulebook:
    return Rulebook(
        rulebook_id="book_workflow",
        version=1,
        guideline_text=SOURCE,
        rules=[Rule(
            rule_id="labels",
            description="Use only the configured labels.",
            check_name="allowed_labels",
            parameters={"allowed_labels": ["person"]},
            origin="reviewer",
            source_sentence=None,
            confirmation_state=ConfirmationState.CONFIRMED,
        )],
        confirmation_state=ConfirmationState.CONFIRMED,
    )


def measurement_report(
    dataset_root: str,
    sample_id: str,
    allowed_labels: list[str] | None,
    connectivity: int,
) -> dict:
    return {
        "image_id": sample_id,
        "image_size": (2, 2),
        "annotation_text": SOURCE,
        "allowed_labels": ["person"],
        "status": "completed",
        "review_status": "pending_review",
        "errors": [],
        "checks": {},
        "objects": [{
            "annotation": {"object_id": 1, "label": "person", "bbox": (1, 1, 2, 2)},
            "checks": {"label": {
                "status": "completed", "reason": None, "detail": None,
                "result": {"object_id": 1, "label": "person", "matches": True, "reason": None},
            }},
        }],
    }


def make_service(settings, collector=measurement_report, lease_seconds=5):
    rulebooks = RulebookRepository(settings=settings)
    audits = AuditRepository(settings=settings)
    rulebooks.save_rulebook(clean_rulebook())
    return BatchAuditService(
        rulebook_repository=rulebooks,
        audit_repository=audits,
        measurement_collector=collector,
        lease_seconds=lease_seconds,
    ), audits


def test_batch_audit_commits_measurements_findings_and_progress_per_sample(workflow_settings):
    service, audits = make_service(workflow_settings)
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="not-validated-here",
        rulebook_id="book_workflow", rulebook_version=1,
        sample_ids=["one", "two"], sample_revisions={"two": 2},
        allowed_labels=["person"],
    )

    audit, queued_job = service.start(request, audit_id="audit_workflow", job_id="job_workflow", now=NOW)
    completed = service.run_audit(audit.audit_id, worker_id="worker_1")

    assert queued_job.status.value == "queued"
    assert completed.status.value == "completed"
    assert completed.progress.processed == 2
    assert completed.progress.fraction == 1.0
    assert audits.get_audit(audit.audit_id).processed_count == 2
    assert audits.get_measurements(audit.audit_id, "two", 2)["image_id"] == "two"

    findings = FindingRepository(settings=workflow_settings).list_findings(audit.audit_id)
    assert len(findings) == 2
    assert all(finding.review.resolution is FindingResolution.PENDING for finding in findings)
    assert {finding.rulebook_version for finding in findings} == {1}


def test_sample_failures_are_durable_and_do_not_hide_successful_samples(workflow_settings):
    def collector(dataset_root, sample_id, allowed_labels, connectivity):
        if sample_id == "bad":
            raise OSError("synthetic source-data failure")
        return measurement_report(dataset_root, sample_id, allowed_labels, connectivity)

    service, audits = make_service(workflow_settings, collector)
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="root", rulebook_id="book_workflow",
        rulebook_version=1, sample_ids=["good", "bad"], allowed_labels=["person"],
    )
    audit, _ = service.start(request, audit_id="audit_failures", job_id="job_failures", now=NOW)

    completed = service.run_audit(audit.audit_id, worker_id="worker_1")

    assert completed.status.value == "completed"
    assert completed.progress.processed == 2
    assert completed.progress.fraction == 1.0
    assert audits.get_audit(audit.audit_id).failed_count == 1
    failures = audits.get_failures(audit.audit_id)
    assert len(failures) == 1 and failures[0].sample_id == "bad"
    assert audits.get_measurements(audit.audit_id, "good", 1) is not None


def test_programming_errors_fail_the_job_instead_of_becoming_sample_failures(workflow_settings):
    def collector(dataset_root, sample_id, allowed_labels, connectivity):
        raise RuntimeError("synthetic programming failure")

    service, audits = make_service(workflow_settings, collector)
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="root", rulebook_id="book_workflow",
        rulebook_version=1, sample_ids=["one"], allowed_labels=["person"],
    )
    audit, _ = service.start(request, audit_id="audit_programming", job_id="job_programming", now=NOW)

    failed = service.run_audit(audit.audit_id, worker_id="worker_1")

    assert failed.status.value == "failed"
    assert audits.get_audit(audit.audit_id).status.value == "failed"
    assert failed.error is not None and failed.error.code == "AUDIT_FAILED"


def test_heartbeat_keeps_a_long_sample_claim_alive(workflow_settings):
    def collector(dataset_root, sample_id, allowed_labels, connectivity):
        sleep(1.5)
        return measurement_report(dataset_root, sample_id, allowed_labels, connectivity)

    service, _ = make_service(workflow_settings, collector, lease_seconds=1)
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="root", rulebook_id="book_workflow",
        rulebook_version=1, sample_ids=["one"], allowed_labels=["person"],
    )
    audit, _ = service.start(request, audit_id="audit_slow", job_id="job_slow", now=NOW)

    completed = service.run_audit(audit.audit_id, worker_id="worker_1")

    assert completed.status.value == "completed"


def test_concurrent_claims_are_exclusive_and_stale_work_can_recover(workflow_settings):
    service, audits = make_service(workflow_settings)
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="root", rulebook_id="book_workflow",
        rulebook_version=1, sample_ids=["one"], allowed_labels=["person"],
    )
    audit, _ = service.start(request, audit_id="audit_claims", job_id="job_claims", now=NOW)

    def claim(worker_id):
        return audits.claim_audit(audit.audit_id, worker_id=worker_id, lease_seconds=1, now=NOW)

    with ThreadPoolExecutor(max_workers=2) as workers:
        claims = list(workers.map(claim, ["worker_a", "worker_b"]))

    assert sum(claim is not None for claim in claims) == 1
    sample_claim = audits.claim_next_sample(next(claim for claim in claims if claim is not None), now=NOW)
    assert sample_claim is not None

    recovered = audits.recover_stale_jobs(now=NOW + timedelta(seconds=2))
    assert [job.status.value for job in recovered] == ["queued"]
    assert audits.get_audit(audit.audit_id).status.value == "queued"
