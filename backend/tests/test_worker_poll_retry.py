"""Workers wait out brief storage outages while polling, but never retry unknown commits."""

import sys
from unittest.mock import Mock

import pytest
from psycopg import OperationalError, errors

from backend.app.storage.database import CommitOutcomeUnknownError, DatabaseError

from backend.app.core.export import ExportOutcomeUnknown
from backend.app.core.feedback import FeedbackError
from backend.app.storage.dataset_repository import DatasetCommitOutcomeUnknownError, DatasetStorageError
from backend.app.storage.workflow_repository import RetryableWorkflowStorageError, WorkflowStorageError
from backend.app.workers import audit, dataset_import, export, feedback, poll_retry, rule_extraction
from backend.app.workers.poll_retry import PollRetry

# The production delays, captured before the autouse fixture below zeroes them for speed.
REAL_DELAYS = poll_retry.RETRY_DELAYS_SECONDS


def outage(error):
    error.__cause__ = OperationalError("connection refused")
    return error


@pytest.fixture(autouse=True)
def no_real_waiting(monkeypatch):
    monkeypatch.setattr(poll_retry, "RETRY_DELAYS_SECONDS", (0, 0, 0, 0, 0))
    monkeypatch.setattr(feedback.time, "sleep", lambda _seconds: None)


def test_poll_retry_waits_with_increasing_delays_then_gives_up(capsys):
    waits = []
    retry = PollRetry("Test worker", delays=(2, 4, 8))
    error = outage(WorkflowStorageError("secret connection detail"))
    assert [retry.should_retry(error, waits.append) for _ in range(4)] == [True, True, True, False]
    assert waits == [2, 4, 8]
    err = capsys.readouterr().err
    assert "retrying in 2s (1/3)" in err and "secret" not in err


def test_poll_retry_never_retries_an_unknown_commit_and_resets_after_success():
    waits = []
    retry = PollRetry("Test worker", delays=(2, 4))
    unknown = RetryableWorkflowStorageError("x", commit_outcome_unknown=True)
    assert retry.should_retry(unknown, waits.append) is False and waits == []
    assert retry.should_retry(outage(WorkflowStorageError("x")), waits.append)
    retry.reset()
    assert retry.failures == 0


def audit_worker(monkeypatch):
    repository, service = Mock(), Mock(lease_seconds=300)
    monkeypatch.setattr(audit, "AuditRepository", lambda: repository)
    monkeypatch.setattr(audit, "RulebookRepository", Mock())
    monkeypatch.setattr(audit, "SampleAssetManifestRepository", Mock())
    monkeypatch.setattr(audit, "BatchAuditService", Mock(return_value=service))
    return repository, service


def test_audit_worker_recovers_from_a_brief_outage_and_runs_the_job(monkeypatch):
    repository, service = audit_worker(monkeypatch)
    claim = Mock()
    repository.jobs.claim_next_job.side_effect = [outage(WorkflowStorageError("timeout")), outage(WorkflowStorageError("timeout")), claim]
    assert audit.main(["--once"]) == 0
    service.run_claimed_job.assert_called_once_with(claim)


def test_audit_worker_still_stops_when_storage_stays_unavailable(monkeypatch, capsys):
    repository, service = audit_worker(monkeypatch)
    repository.jobs.claim_next_job.side_effect = outage(WorkflowStorageError("down"))
    assert audit.main(["--once"]) == 1
    assert repository.jobs.claim_next_job.call_count == 6  # first try + five retries
    service.run_claimed_job.assert_not_called()
    assert "check storage and configuration" in capsys.readouterr().err


def dataset_worker(monkeypatch):
    repository = Mock()
    monkeypatch.setattr(dataset_import, "load_import_settings", Mock())
    monkeypatch.setattr(dataset_import, "DatasetImportRepository", lambda: repository)
    run = Mock()
    monkeypatch.setattr(dataset_import, "run_claimed_import", run)
    return repository, run


def test_dataset_worker_retries_storage_errors_but_not_unknown_commits(monkeypatch):
    repository, run = dataset_worker(monkeypatch)
    repository.claim_next.side_effect = [outage(DatasetStorageError("timeout")), Mock()]
    assert dataset_import.main(["--once"]) == 0
    run.assert_called_once()

    repository, run = dataset_worker(monkeypatch)
    repository.claim_next.side_effect = DatasetCommitOutcomeUnknownError("reconcile")
    assert dataset_import.main(["--once"]) == 2
    repository.claim_next.assert_called_once()
    run.assert_not_called()


def test_export_worker_never_retries_an_unknown_export_outcome(monkeypatch):
    repository = Mock()
    monkeypatch.setattr(export, "load_export_settings", Mock())
    monkeypatch.setattr(export, "ExportRepository", lambda: repository)
    monkeypatch.setattr(export, "run_claimed_export", Mock())
    repository.recover_stale.side_effect = ExportOutcomeUnknown("reconcile")
    assert export.main(["--once"]) == 2
    repository.recover_stale.assert_called_once()


def test_feedback_worker_retries_only_unavailable_storage(monkeypatch):
    repository, run = Mock(), Mock()
    monkeypatch.setattr(feedback, "FeedbackJobRepository", lambda: repository)
    monkeypatch.setattr(feedback, "run_claim", run)
    monkeypatch.setattr(sys, "argv", ["feedback", "--once"])
    repository.claim.side_effect = [outage(FeedbackError("DEPENDENCY_UNAVAILABLE")), Mock()]
    feedback.main()
    run.assert_called_once()

    repository.claim.side_effect = FeedbackError("FEEDBACK_CONFLICT")
    with pytest.raises(FeedbackError):
        feedback.main()


@pytest.mark.parametrize("cause", [
    errors.InvalidPassword("bad credentials"),
    errors.UndefinedTable("missing migration"),
    ValueError("corrupt stored payload"),
    None,
])
def test_poll_does_not_retry_permanent_or_unclassified_failures(cause):
    failure = FeedbackError("DEPENDENCY_UNAVAILABLE")
    failure.__cause__ = cause
    wait = Mock()
    assert not PollRetry("Feedback").should_retry(failure, wait)
    wait.assert_not_called()


def test_poll_retries_a_known_rolled_back_deadlock():
    failure = DatabaseError("transaction rolled back")
    failure.__cause__ = errors.DeadlockDetected("deadlock")
    wait = Mock()
    assert PollRetry("Test", delays=REAL_DELAYS).should_retry(failure, wait)
    wait.assert_called_once_with(2)


@pytest.mark.parametrize("operation", ["claim", "recover"])
def test_feedback_stops_after_wrapped_unknown_commit(monkeypatch, operation):
    repository = Mock()
    inner = CommitOutcomeUnknownError("commit acknowledgement lost")
    inner.__cause__ = OperationalError("connection lost")
    failure = FeedbackError("DEPENDENCY_UNAVAILABLE")
    failure.__cause__ = inner
    getattr(repository, operation).side_effect = failure
    monkeypatch.setattr(feedback, "FeedbackJobRepository", lambda: repository)
    run = Mock()
    monkeypatch.setattr(feedback, "run_claim", run)
    monkeypatch.setattr(sys, "argv", ["feedback", "--once"])
    with pytest.raises(FeedbackError):
        feedback.main()
    getattr(repository, operation).assert_called_once()
    run.assert_not_called()


def test_extraction_retries_an_idle_outage_but_not_the_claimed_work(monkeypatch):
    repository = Mock()
    claim = Mock()
    repository.claim_next.side_effect = [outage(WorkflowStorageError("down")), claim]
    monkeypatch.setattr(rule_extraction, "ExtractionRepository", lambda: repository)
    supervise = Mock(side_effect=WorkflowStorageError("claimed job failed"))
    monkeypatch.setattr(rule_extraction, "supervise", supervise)
    assert rule_extraction.main(["--once"]) == 1
    assert repository.claim_next.call_count == 2
    supervise.assert_called_once()
