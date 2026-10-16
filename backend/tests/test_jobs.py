"""Job state and batch-request boundaries; PostgreSQL integration stays opt-in."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from backend.app.models.audit import BatchAuditRequest
from backend.app.models.job import ApiError, Job, JobKind, JobStatus, Progress, ResultReference


NOW = datetime(2026, 10, 16, 20, tzinfo=UTC)


def test_unknown_total_progress_remains_null():
    progress = Progress(phase="collecting", processed=3, total=None, fraction=None)
    assert progress.total is None and progress.fraction is None


def test_progress_rejects_inconsistent_known_total():
    with pytest.raises(ValidationError):
        Progress(phase="collecting", processed=3, total=2, fraction=1.0)


def test_job_terminal_payloads_are_consistent():
    result = ResultReference(kind="audit", id="audit_1", href="/api/v1/audits/audit_1")
    completed = Job(
        job_id="job_1", kind=JobKind.AUDIT, status=JobStatus.COMPLETED,
        progress=Progress(phase="completed", processed=1, total=1, fraction=1.0),
        result=result, created_at=NOW, updated_at=NOW,
    )
    assert completed.result == result
    with pytest.raises(ValidationError):
        Job(
            job_id="job_2", kind=JobKind.AUDIT, status=JobStatus.FAILED,
            progress=Progress(phase="failed", processed=0, total=1, fraction=0.0),
            created_at=NOW, updated_at=NOW,
        )


def test_batch_request_does_not_require_or_validate_a_filesystem_path():
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="a/path-that-does-not-exist",
        rulebook_id="book_1", rulebook_version=3, sample_ids=["one"],
        allowed_labels=["person"], sample_revisions={"one": 2},
    )
    assert request.dataset_root == "a/path-that-does-not-exist"


def test_batch_request_rejects_duplicate_samples_and_unknown_revisions():
    with pytest.raises(ValidationError):
        BatchAuditRequest(
            dataset_id="dataset_1", dataset_root="root", rulebook_id="book_1",
            rulebook_version=1, sample_ids=["one", "one"], allowed_labels=["person"],
        )
    with pytest.raises(ValidationError):
        BatchAuditRequest(
            dataset_id="dataset_1", dataset_root="root", rulebook_id="book_1",
            rulebook_version=1, sample_ids=["one"], allowed_labels=["person"],
            sample_revisions={"other": 1},
        )


def test_api_error_does_not_accept_terminal_result_and_error_together():
    result = ResultReference(kind="audit", id="audit_1", href="/api/v1/audits/audit_1")
    error = ApiError(code="FAILED", message="failed", request_id="request_1")
    with pytest.raises(ValidationError):
        Job(
            job_id="job_1", kind=JobKind.AUDIT, status=JobStatus.COMPLETED,
            progress=Progress(phase="completed", processed=1, total=1, fraction=1.0),
            result=result, error=error, created_at=NOW,
            updated_at=NOW + timedelta(seconds=1),
        )


def test_failed_job_cannot_expose_a_success_result():
    result = ResultReference(kind="audit", id="audit_1", href="/api/v1/audits/audit_1")
    error = ApiError(code="FAILED", message="failed", request_id="request_1")
    with pytest.raises(ValidationError):
        Job(
            job_id="job_1", kind=JobKind.AUDIT, status=JobStatus.FAILED,
            progress=Progress(phase="failed", processed=0, total=1, fraction=0.0),
            result=result, error=error, created_at=NOW, updated_at=NOW,
        )
