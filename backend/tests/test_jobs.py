"""Job state and batch-request boundaries; PostgreSQL integration stays opt-in."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from backend.app.models.audit import BatchAuditRequest
from backend.app.models.job import ApiError, Job, JobKind, JobStatus, Progress, ResultReference
from backend.app.models.rule import Rule, Rulebook
from backend.app.services.batch_audit import BatchAuditService


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


def test_batch_collection_does_not_require_shared_label_configuration():
    rulebook = Rulebook(
        rulebook_id="book_1", version=1, guideline_text="Boxes are valid.",
        rules=[Rule(
            rule_id="box", description="Boxes are valid.", check_name="box_validity",
            origin="reviewer", confirmation_state="confirmed",
        ), Rule(
            rule_id="person-label", description="Person labels are allowed.",
            check_name="allowed_labels", parameters={"allowed_labels": ["person"]},
            origin="reviewer", confirmation_state="confirmed",
        ), Rule(
            rule_id="vehicle-label", description="Vehicle labels are allowed.",
            check_name="allowed_labels", parameters={"allowed_labels": ["vehicle"]},
            origin="reviewer", confirmation_state="confirmed",
        )],
        confirmation_state="confirmed",
    )

    class Rulebooks:
        def get_rulebook(self, rulebook_id, *, version):
            assert (rulebook_id, version) == ("book_1", 1)
            return rulebook

    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="opaque-root", rulebook_id="book_1",
        rulebook_version=1, sample_ids=["one"], allowed_labels=None,
    )
    service = BatchAuditService(rulebook_repository=Rulebooks(), audit_repository=object())
    resolved, _ = service._resolve_request(request)
    assert resolved.allowed_labels is None


def test_geometry_only_rulebook_resolves_without_labels():
    rulebook = Rulebook(
        rulebook_id="geometry_book", version=1, guideline_text="Boxes are valid.",
        rules=[Rule(
            rule_id="box", description="Boxes are valid.", check_name="box_validity",
            origin="reviewer", confirmation_state="confirmed",
        )],
        confirmation_state="confirmed",
    )

    class Rulebooks:
        def get_rulebook(self, rulebook_id, *, version):
            return rulebook

    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="opaque-root", rulebook_id="geometry_book",
        rulebook_version=1, sample_ids=["one"], allowed_labels=None,
    )
    resolved, _ = BatchAuditService(
        rulebook_repository=Rulebooks(), audit_repository=object(),
    )._resolve_request(request)
    assert resolved.allowed_labels is None


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
