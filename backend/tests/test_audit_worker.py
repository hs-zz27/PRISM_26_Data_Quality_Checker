"""The explicit worker dispatches audit jobs through the existing supervisor."""

from unittest.mock import Mock

from backend.app.models.job import JobKind
from backend.app.storage.workflow_repository import JobClaimLostError, RetryableWorkflowStorageError
from backend.app.workers import audit as worker


def prepare(monkeypatch):
    repository = Mock()
    service = Mock(lease_seconds=300)
    monkeypatch.setattr(worker, "AuditRepository", lambda: repository)
    monkeypatch.setattr(worker, "RulebookRepository", Mock())
    monkeypatch.setattr(worker, "SampleAssetManifestRepository", Mock())
    constructor = Mock(return_value=service)
    monkeypatch.setattr(worker, "BatchAuditService", constructor)
    return repository, service, constructor


def test_once_recovers_and_dispatches_an_audit_only(monkeypatch):
    repository, service, constructor = prepare(monkeypatch)
    assert worker.main(["--once"]) == 0
    service.recover.assert_called_once_with()
    service.run_claimed_job.assert_called_once_with(repository.jobs.claim_next_job.return_value)
    options = repository.jobs.claim_next_job.call_args.kwargs
    assert options["kind"] is JobKind.AUDIT and options["lease_seconds"] == 300
    assert options["worker_id"].startswith("audit_worker_")
    assert "manifest_repository" in constructor.call_args.kwargs


def test_empty_queue_does_not_execute_work(monkeypatch):
    repository, service, _ = prepare(monkeypatch)
    repository.jobs.claim_next_job.return_value = None
    assert worker.main(["--once"]) == 0
    service.run_claimed_job.assert_not_called()


def test_claim_loss_is_left_to_existing_recovery(monkeypatch):
    _, service, _ = prepare(monkeypatch)
    service.run_claimed_job.side_effect = JobClaimLostError("lost")
    assert worker.main(["--once"]) == 0
    service.run_claimed_job.assert_called_once()


def test_unknown_claim_commit_stops_without_retry(monkeypatch, capsys):
    repository, service, _ = prepare(monkeypatch)
    repository.jobs.claim_next_job.side_effect = RetryableWorkflowStorageError("private", commit_outcome_unknown=True)
    assert worker.main(["--once"]) == 1
    repository.jobs.claim_next_job.assert_called_once()
    service.run_claimed_job.assert_not_called()
    assert "private" not in capsys.readouterr().err
