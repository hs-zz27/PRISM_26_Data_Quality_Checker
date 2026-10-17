"""Worker supervision and commit retry policy without a running database."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace

import pytest

from backend.app.models.audit import AuditSampleClaim, BatchAuditRequest
from backend.app.models.rule import Rule, Rulebook
from backend.app.services.batch_audit import BatchAuditService
from backend.app.storage.workflow_repository import (
    JobClaimLostError, RetryableWorkflowStorageError, SampleProcessingTimeoutError,
)


@pytest.mark.parametrize("expired_bound", ["deadline", "lease"])
def test_watchdog_stops_child_while_database_checkpoint_is_blocked(monkeypatch, expired_bound):
    blocked = Event()
    release = Event()
    terminated = Event()

    class Repository:
        def checkpoint_sample_claim(self, *args, **kwargs):
            blocked.set()
            if not release.wait(timeout=5):
                raise AssertionError("The test did not release its database checkpoint")

    class Process:
        pid = None
        alive = False

        def start(self):
            self.pid = 1
            self.alive = True

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.alive = False
            terminated.set()

        def join(self, timeout=None):
            pass

    class Connection:
        def poll(self, timeout=None):
            return False

        def close(self):
            pass

    context = SimpleNamespace(
        Pipe=lambda **kwargs: (Connection(), Connection()),
        Process=lambda **kwargs: Process(),
    )
    monkeypatch.setattr("backend.app.services.batch_audit.multiprocessing.get_context", lambda _: context)
    service = BatchAuditService(
        rulebook_repository=object(), audit_repository=Repository(), sample_max_seconds=30,
    )
    request = BatchAuditRequest(
        dataset_id="dataset", dataset_root="unused", rulebook_id="book",
        rulebook_version=1, sample_ids=["sample"],
    )
    rulebook = Rulebook(
        rulebook_id="book", version=1, guideline_text="Dimensions match.",
        confirmation_state="confirmed", rules=[Rule(
            rule_id="dimensions", description="Dimensions match.", check_name="mask_dimensions",
            origin="reviewer", confirmation_state="confirmed",
        )],
    )
    now = datetime.now(UTC)
    claim = AuditSampleClaim(
        audit_id="audit", job_id="job", sample_id="sample", sample_revision=1,
        worker_id="worker", claim_token="token", attempt_count=1,
        lease_until=now + timedelta(seconds=0.5 if expired_bound == "lease" else 30),
        processing_deadline=now + timedelta(seconds=0.5 if expired_bound == "deadline" else 30),
    )
    expected_error = SampleProcessingTimeoutError if expired_bound == "deadline" else JobClaimLostError
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(service._compute_isolated, request, rulebook, claim)
        try:
            assert blocked.wait(timeout=2)
            assert terminated.wait(timeout=3)
            # The supervisor is still in its database call when the independent
            # watchdog terminates computation.
            assert not future.done()
        finally:
            release.set()
        with pytest.raises(expected_error):
            future.result(timeout=3)


@pytest.mark.parametrize("reconciliation_enabled", [False, True])
def test_unknown_commits_require_explicit_replay_safety(monkeypatch, reconciliation_enabled):
    service = BatchAuditService(
        rulebook_repository=object(), audit_repository=object(), sample_max_seconds=30,
    )
    monkeypatch.setattr(service, "_retry_delay", lambda _: 0.0)
    calls = []

    def mutation():
        calls.append(None)
        if len(calls) == 1:
            raise RetryableWorkflowStorageError("commit acknowledgement lost", commit_outcome_unknown=True)
        return "reconciled"

    if reconciliation_enabled:
        assert service._storage_retry(None, mutation, reconcile_unknown_commit=True) == "reconciled"
        assert len(calls) == 2
    else:
        with pytest.raises(RetryableWorkflowStorageError):
            service._storage_retry(None, mutation)
        assert len(calls) == 1
