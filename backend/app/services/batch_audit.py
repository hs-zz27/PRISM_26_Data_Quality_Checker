"""Sequential, restart-safe execution of PostgreSQL-backed batch audits."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from hashlib import sha256
from datetime import UTC, datetime, timedelta
import logging
from threading import Event, Thread
from typing import Any, Callable

from ..checks.registry import validate_check_parameters
from ..models.audit import AuditFailure, AuditRun, BatchAuditRequest
from ..models.job import ApiError, Job, JobStatus
from ..models.rule import ConfirmationState, Rulebook
from ..storage.workflow_repository import (
    AuditRepository,
    JobCancellationError,
    JobClaimLostError,
)
from .audit import audit_sample
from .evaluation import evaluate_rules


class BatchAuditError(ValueError):
    """The batch request cannot be safely started."""


class SampleDataError(ValueError):
    """An expected source-data problem that should fail only one sample."""


logger = logging.getLogger(__name__)


class _SampleLeaseHeartbeat:
    """Renew one sample and its parent job while collection is running."""

    def __init__(self, repository: AuditRepository, claim: Any, lease_seconds: int) -> None:
        self.repository = repository
        self.claim = claim
        self.lease_seconds = lease_seconds
        self.stop_event = Event()
        self.error: BaseException | None = None
        self.thread = Thread(target=self._run, name="audit-lease-heartbeat", daemon=True)

    def _run(self) -> None:
        interval = max(0.1, self.lease_seconds / 3)
        while not self.stop_event.wait(interval):
            try:
                self.repository.renew_sample_claim(
                    self.claim, lease_seconds=self.lease_seconds,
                )
            except BaseException as error:  # noqa: BLE001 - surfaced by the worker thread owner
                self.error = error
                self.stop_event.set()
                return

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join()


def _snapshot_confirmed_rulebook(rulebook: Rulebook) -> Rulebook:
    if not isinstance(rulebook, Rulebook):
        raise TypeError("rulebook must be a Rulebook instance.")
    snapshot = Rulebook.model_validate(rulebook.model_dump())
    if snapshot.confirmation_state is not ConfirmationState.CONFIRMED or not snapshot.rules:
        raise BatchAuditError("A batch audit requires a nonempty confirmed rulebook.")
    if any(rule.confirmation_state is not ConfirmationState.CONFIRMED for rule in snapshot.rules):
        raise BatchAuditError("A batch audit requires only confirmed rules.")
    for rule in snapshot.rules:
        validate_check_parameters(rule.check_name, rule.parameters)
    return snapshot


def _failure_id(audit_id: str, sample_id: str, revision: int, reason: str) -> str:
    identity = "\x1f".join((audit_id, sample_id, str(revision), reason))
    digest = sha256(identity.encode("utf-8")).hexdigest()
    return f"failure_{digest}"


class BatchAuditService:
    """Orchestrate one sample at a time; PostgreSQL owns durable progress."""

    def __init__(
        self,
        *,
        rulebook_repository: Any,
        audit_repository: AuditRepository,
        measurement_collector: Callable[..., Any] = audit_sample,
        lease_seconds: int = 300,
    ) -> None:
        self.rulebook_repository = rulebook_repository
        self.audit_repository = audit_repository
        self.measurement_collector = measurement_collector
        if type(lease_seconds) is not int or lease_seconds < 1:
            raise ValueError("lease_seconds must be a positive integer.")
        self.lease_seconds = lease_seconds

    def _resolve_request(self, request: BatchAuditRequest) -> tuple[BatchAuditRequest, Rulebook]:
        if not isinstance(request, BatchAuditRequest):
            raise TypeError("request must be a BatchAuditRequest instance.")
        request = BatchAuditRequest.model_validate(request.model_dump())
        rulebook = self.rulebook_repository.get_rulebook(
            request.rulebook_id, version=request.rulebook_version,
        )
        if rulebook.rulebook_id != request.rulebook_id or rulebook.version != request.rulebook_version:
            raise BatchAuditError("The rulebook repository returned a different version than requested.")
        rulebook = _snapshot_confirmed_rulebook(rulebook)
        # Do not derive a shared label list from the rulebook. Collection must
        # remain independent; each label rule uses its own parameters later.
        return request, rulebook

    def start(
        self,
        request: BatchAuditRequest,
        *,
        audit_id: str | None = None,
        job_id: str | None = None,
        now: datetime | None = None,
    ) -> tuple[AuditRun, Job]:
        """Validate and pin all inputs before creating the queued job."""
        resolved, _ = self._resolve_request(request)
        return self.audit_repository.start_audit(
            resolved, audit_id=audit_id, job_id=job_id, now=now,
        )

    def _renew(self, claim):
        job = self.audit_repository.jobs.renew_claim(
            claim, lease_seconds=self.lease_seconds,
        )
        return replace(
            claim,
            job=job,
            lease_until=datetime.now(UTC) + timedelta(seconds=self.lease_seconds),
        )

    def _raise_if_heartbeat_failed(self, heartbeat: _SampleLeaseHeartbeat, audit_id: str) -> None:
        if heartbeat.error is None:
            return
        if isinstance(heartbeat.error, JobClaimLostError):
            current = self.audit_repository.get_job(audit_id)
            if current.status is JobStatus.CANCELLING:
                return
        raise heartbeat.error

    def run_audit(self, audit_id: str, *, worker_id: str) -> Job:
        """Claim and process the complete audit sequentially.

        Expected source-data exceptions are recorded as sample failures and the
        worker moves to the next item. Programming, rule-evaluation and storage
        errors fail the job instead. Lease loss is a worker-recovery signal and
        is allowed to escape so a stale worker cannot write again.
        """
        claim = self.audit_repository.claim_audit(
            audit_id,
            worker_id=worker_id,
            lease_seconds=self.lease_seconds,
        )
        if claim is None:
            return self.audit_repository.get_job(audit_id)
        try:
            request = self.audit_repository.get_request(audit_id)
            if request.allowed_labels is None:
                raise BatchAuditError("A persisted audit is missing its resolved allowed labels.")
            rulebook = self.rulebook_repository.get_rulebook(
                request.rulebook_id, version=request.rulebook_version,
            )
            if rulebook.rulebook_id != request.rulebook_id or rulebook.version != request.rulebook_version:
                raise BatchAuditError("The rulebook repository returned a different version than requested.")
            rulebook = _snapshot_confirmed_rulebook(rulebook)

            while True:
                current = self.audit_repository.get_job(audit_id)
                if current.status is JobStatus.RUNNING:
                    claim = self._renew(claim)
                elif current.status is JobStatus.CANCELLING:
                    # Cancellation is cooperative.  Keep the token so the worker
                    # can perform the safe-boundary transition without renewing a
                    # lease that is intentionally being wound down.
                    claim = replace(claim, job=current)
                else:
                    return current
                sample_claim = self.audit_repository.claim_next_sample(
                    claim, lease_seconds=self.lease_seconds,
                )
                if sample_claim is None:
                    current = self.audit_repository.get_job(audit_id)
                    if current.status in (
                        JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED,
                    ):
                        return current
                    return self.audit_repository.finalize_audit(claim)
                measurements = None
                heartbeat = _SampleLeaseHeartbeat(
                    self.audit_repository, sample_claim, self.lease_seconds,
                )
                heartbeat.start()
                try:
                    measurements = self.measurement_collector(
                        request.dataset_root,
                        sample_claim.sample_id,
                        request.allowed_labels,
                        request.connectivity,
                    )
                    findings = evaluate_rules(
                        rulebook,
                        measurements,
                        audit_id=audit_id,
                        sample_id=sample_claim.sample_id,
                        sample_revision=sample_claim.sample_revision,
                    )
                    self._raise_if_heartbeat_failed(heartbeat, audit_id)
                    self.audit_repository.persist_sample_success(
                        sample_claim, measurements=measurements, findings=findings,
                    )
                except JobClaimLostError:
                    raise
                except (OSError, UnicodeError, SampleDataError) as error:
                    failure = AuditFailure(
                        failure_id=_failure_id(
                            audit_id, sample_claim.sample_id, sample_claim.sample_revision,
                            "sample_processing_failed",
                        ),
                        audit_id=audit_id,
                        sample_id=sample_claim.sample_id,
                        sample_revision=sample_claim.sample_revision,
                        stage="sample_processing",
                        reason="sample_processing_failed",
                        detail=str(error) or error.__class__.__name__,
                        created_at=datetime.now(UTC),
                    )
                    self.audit_repository.persist_sample_failure(
                        sample_claim,
                        failure,
                        measurements=measurements if isinstance(measurements, Mapping) else None,
                    )
                finally:
                    heartbeat.stop()
        except JobClaimLostError:
            raise
        except JobCancellationError:
            return self.audit_repository.get_job(audit_id)
        except Exception as error:  # noqa: BLE001 - convert worker failure to a readable job
            logger.exception("Audit worker failed", extra={"audit_id": audit_id})
            safe_error = ApiError(
                code="AUDIT_FAILED",
                message="The audit worker failed before the batch completed.",
                request_id=claim.job.job_id,
                details=[],
            )
            try:
                return self.audit_repository.fail_audit(claim, safe_error)
            except (JobClaimLostError, JobCancellationError):
                return self.audit_repository.get_job(audit_id)

    def run_job(self, job_id: str, *, worker_id: str) -> Job:
        """Run an audit job by ID without changing the public job shape."""
        kind, audit_id = self.audit_repository.jobs.get_job_context(job_id)
        if kind != "audit" or audit_id is None:
            raise BatchAuditError("The requested job is not a batch audit job.")
        return self.run_audit(audit_id, worker_id=worker_id)

    def cancel(self, audit_id: str) -> Job:
        """Request cancellation using the audit identifier."""
        return self.audit_repository.request_cancellation(audit_id)

    def cancel_job(self, job_id: str) -> Job:
        """Request cancellation through the public job identifier."""
        return self.audit_repository.cancel_job(job_id)

    def recover(self, *, now: datetime | None = None) -> list[Job]:
        return self.audit_repository.recover_stale_jobs(now=now)
