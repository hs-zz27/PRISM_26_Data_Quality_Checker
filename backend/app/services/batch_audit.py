"""Sequential, restart-safe execution of PostgreSQL-backed batch audits."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass, replace
from hashlib import sha256
from datetime import UTC, datetime, timedelta
import logging
import multiprocessing as multiprocessing
import os
from multiprocessing import forkserver
import pickle
import random
import sys
import time
from pathlib import Path
from threading import Condition, Thread
from typing import Any, Callable, Protocol, TypeVar
from uuid import uuid4

from ..checks.registry import validate_check_parameters
from ..core.config import load_audit_settings
from ..models.asset_manifest import SampleAssetManifest
from ..models.audit import AuditFailure, AuditRun, AuditSampleClaim, AuditSourceMode, BatchAuditRequest
from ..models.job import ApiError, Job, JobStatus
from ..models.rule import ConfirmationState, Rulebook
from ..storage.workflow_repository import (
    AuditRepository,
    JobCancellationError,
    JobClaim,
    JobClaimLostError,
    RetryableWorkflowStorageError,
    SampleAssetManifestNotFoundError,
    SampleAssetManifestRepository,
    SampleProcessingTimeoutError,
)
from .audit import audit_sample
from .asset_snapshot import capture_sample, materialize_sample, sample_work_directory
from .evaluation import RuleEvaluationResult, evaluate_rules_detailed
from .sample_worker import (
    SampleComputationError,
    SampleDataError,
    TransientSampleError,
    run_rule_checks,
    run_sample_child,
)


class BatchAuditError(ValueError):
    """The batch request cannot be safely started."""


class RulebookNotConfirmedError(BatchAuditError):
    """The requested book must contain confirmed rules before it can run."""


logger = logging.getLogger(__name__)
StorageResult = TypeVar("StorageResult")
# Every sample still runs in its own process, but processes start from a fork server that has already
# imported these, instead of importing Torch and the person check again for each sample (about 1.4 s).
_SAMPLE_PRELOAD = ["torch", "torchvision", "backend.app.services.sample_worker",
                   "backend.app.services.person_coverage", "backend.app.inference.person_segmenter"]


@dataclass(frozen=True)
class ResolvedSampleRevision:
    """Authoritative revision and trusted source root selected before an audit."""

    sample_revision: int
    source_root: str | Path


class SampleRevisionResolver(Protocol):
    """Resolve every requested sample to one authoritative local source root."""

    def resolve_samples(
        self,
        *,
        dataset_id: str,
        sample_ids: Sequence[str],
        requested_revisions: Mapping[str, int],
    ) -> Mapping[str, ResolvedSampleRevision]: ...


class _SampleWatchdog:
    """Stop computation on time even while its supervisor is blocked in I/O."""

    def __init__(self, claim: AuditSampleClaim, terminate: Callable[[], None]) -> None:
        now = time.monotonic()
        wall_time = datetime.now(UTC)
        self.deadline = now + (claim.processing_deadline - wall_time).total_seconds()
        self.lease_until = now + (claim.lease_until - wall_time).total_seconds()
        self._terminate = terminate
        self._condition = Condition()
        self._stopped = False
        self._error: JobClaimLostError | SampleProcessingTimeoutError | None = None
        self._thread = Thread(target=self._run, name="audit-sample-watchdog", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        with self._condition:
            while not self._stopped:
                remaining = min(self.deadline, self.lease_until) - time.monotonic()
                if remaining > 0:
                    self._condition.wait(remaining)
                    continue
                if self.deadline <= self.lease_until:
                    self._error = SampleProcessingTimeoutError(
                        "The sample processing deadline has expired."
                    )
                else:
                    self._error = JobClaimLostError(
                        "Sample ownership could not be renewed before its lease expired."
                    )
                break
            else:
                return
        try:
            self._terminate()
        except Exception:
            logger.exception("The sample watchdog could not stop computation")

    def renewed(self, started_at: float, lease_seconds: int) -> None:
        with self._condition:
            # Use the request's start, rather than its acknowledgement time,
            # so a slow renewal can never invent additional ownership locally.
            if self._error is None:
                self.lease_until = min(started_at + lease_seconds, self.deadline)
                self._condition.notify_all()

    def raise_if_expired(self) -> None:
        with self._condition:
            if self._error is not None:
                raise self._error
            expiry = min(self.deadline, self.lease_until)
            if time.monotonic() >= expiry:
                if self.deadline <= self.lease_until:
                    raise SampleProcessingTimeoutError("The sample processing deadline has expired.")
                raise JobClaimLostError("The worker's sample lease has expired.")

    def stop(self) -> None:
        with self._condition:
            self._stopped = True
            self._condition.notify_all()
        self._thread.join()


def _snapshot_confirmed_rulebook(rulebook: Rulebook) -> Rulebook:
    if not isinstance(rulebook, Rulebook):
        raise TypeError("rulebook must be a Rulebook instance.")
    snapshot = Rulebook.model_validate(rulebook.model_dump())
    if snapshot.confirmation_state is not ConfirmationState.CONFIRMED or not snapshot.rules:
        raise RulebookNotConfirmedError("A batch audit requires a nonempty confirmed rulebook.")
    if any(rule.confirmation_state is not ConfirmationState.CONFIRMED for rule in snapshot.rules):
        raise RulebookNotConfirmedError("A batch audit requires only confirmed rules.")
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
        rule_evaluator: Callable[..., RuleEvaluationResult] = evaluate_rules_detailed,
        sample_revision_resolver: SampleRevisionResolver | None = None,
        manifest_repository: SampleAssetManifestRepository | None = None,
        snapshot_capturer: Callable[..., SampleAssetManifest] = capture_sample,
        lease_seconds: int = 300,
        sample_max_seconds: int | None = None,
        isolate_sample_computation: bool = True,
    ) -> None:
        self.rulebook_repository = rulebook_repository
        self.audit_repository = audit_repository
        self.measurement_collector = measurement_collector
        if not callable(rule_evaluator):
            raise TypeError("rule_evaluator must be callable.")
        self.rule_evaluator = rule_evaluator
        if sample_revision_resolver is not None and not hasattr(
            sample_revision_resolver, "resolve_samples",
        ):
            raise TypeError("sample_revision_resolver must provide resolve_samples().")
        if manifest_repository is not None and not all(
            hasattr(manifest_repository, name)
            for name in ("get_manifest", "read_asset_bytes", "asset_settings")
        ):
            raise TypeError("manifest_repository must provide manifest and asset operations.")
        if not callable(snapshot_capturer):
            raise TypeError("snapshot_capturer must be callable.")
        self.sample_revision_resolver = sample_revision_resolver
        self.manifest_repository = manifest_repository
        self.snapshot_capturer = snapshot_capturer
        if type(lease_seconds) is not int or lease_seconds < 1:
            raise ValueError("lease_seconds must be a positive integer.")
        if sample_max_seconds is None:
            sample_max_seconds = load_audit_settings().sample_max_seconds
        if type(sample_max_seconds) is not int or not 1 <= sample_max_seconds <= 86_400:
            raise ValueError("sample_max_seconds must be an integer between 1 and 86400.")
        if type(isolate_sample_computation) is not bool:
            raise TypeError("isolate_sample_computation must be a boolean.")
        self.lease_seconds = lease_seconds
        self.sample_max_seconds = sample_max_seconds
        self.isolate_sample_computation = isolate_sample_computation

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

    @staticmethod
    def _snapshot_manifest_identity(
        manifest: SampleAssetManifest,
        *,
        dataset_id: str,
        sample_id: str,
        sample_revision: int,
    ) -> SampleAssetManifest:
        if not isinstance(manifest, SampleAssetManifest):
            raise BatchAuditError("The snapshot provider returned an invalid manifest.")
        if (
            manifest.dataset_id != dataset_id
            or manifest.sample_id != sample_id
            or manifest.sample_revision != sample_revision
        ):
            raise BatchAuditError("The snapshot manifest does not match the authoritative sample revision.")
        return SampleAssetManifest.model_validate(manifest.model_dump(mode="json"))

    def _prepare_snapshot_request(
        self, request: BatchAuditRequest,
        sample_manifests: Mapping[str, SampleAssetManifest] | None = None,
    ) -> tuple[BatchAuditRequest, dict[str, SampleAssetManifest]]:
        if sample_manifests is not None:
            if (request.source_mode is not AuditSourceMode.SNAPSHOT
                    or set(sample_manifests) != set(request.sample_ids)
                    or set(request.sample_revisions) != set(request.sample_ids)):
                raise BatchAuditError("Catalogue audits require an exact manifest and revision for every sample.")
            # Imported revisions already own immutable bytes. Never try to
            # recapture them from a legacy root or discarded upload scratch.
            return request, {
                sample_id: self._snapshot_manifest_identity(
                    sample_manifests[sample_id], dataset_id=request.dataset_id,
                    sample_id=sample_id, sample_revision=request.sample_revisions[sample_id],
                )
                for sample_id in request.sample_ids
            }
        if request.source_mode is AuditSourceMode.LEGACY:
            return request, {}
        if self.sample_revision_resolver is None or self.manifest_repository is None:
            raise BatchAuditError(
                "Snapshot audits require a sample revision resolver and manifest repository."
            )
        try:
            resolved = self.sample_revision_resolver.resolve_samples(
                dataset_id=request.dataset_id,
                sample_ids=tuple(request.sample_ids),
                requested_revisions=dict(request.sample_revisions),
            )
        except BatchAuditError:
            raise
        except Exception as error:
            raise BatchAuditError("Authoritative sample revisions could not be resolved.") from error
        if not isinstance(resolved, Mapping) or set(resolved) != set(request.sample_ids):
            raise BatchAuditError("The sample revision resolver must return every requested sample exactly once.")

        revisions: dict[str, int] = {}
        manifests: dict[str, SampleAssetManifest] = {}
        for sample_id in request.sample_ids:
            selected = resolved[sample_id]
            if not isinstance(selected, ResolvedSampleRevision):
                raise BatchAuditError("The sample revision resolver returned an invalid result.")
            if type(selected.sample_revision) is not int or selected.sample_revision < 1:
                raise BatchAuditError("The sample revision resolver returned an invalid revision.")
            requested = request.sample_revisions.get(sample_id)
            if requested is not None and requested != selected.sample_revision:
                raise BatchAuditError("The requested revision differs from the authoritative revision.")
            revision = selected.sample_revision
            try:
                manifest = self.manifest_repository.get_manifest(
                    request.dataset_id, sample_id, revision,
                )
            except SampleAssetManifestNotFoundError:
                manifest = self.snapshot_capturer(
                    dataset_id=request.dataset_id,
                    sample_id=sample_id,
                    sample_revision=revision,
                    source_root=selected.source_root,
                    repository=self.manifest_repository,
                )
            manifests[sample_id] = self._snapshot_manifest_identity(
                manifest,
                dataset_id=request.dataset_id,
                sample_id=sample_id,
                sample_revision=revision,
            )
            revisions[sample_id] = revision

        request_values = request.model_dump()
        request_values["sample_revisions"] = revisions
        return BatchAuditRequest.model_validate(request_values), manifests

    def start(
        self,
        request: BatchAuditRequest,
        *,
        audit_id: str | None = None,
        job_id: str | None = None,
        now: datetime | None = None,
        sample_manifests: Mapping[str, SampleAssetManifest] | None = None,
    ) -> tuple[AuditRun, Job]:
        """Validate and pin all inputs before creating the queued job."""
        resolved, _ = self._resolve_request(request)
        resolved, manifests = self._prepare_snapshot_request(resolved, sample_manifests)
        if resolved.source_mode is AuditSourceMode.SNAPSHOT:
            return self.audit_repository.start_audit(
                resolved,
                sample_manifests=manifests,
                audit_id=audit_id,
                job_id=job_id,
                now=now,
            )
        return self.audit_repository.start_audit(
            resolved, audit_id=audit_id, job_id=job_id, now=now,
        )

    def _renew(self, claim):
        job = self._storage_retry(
            None,
            lambda: self.audit_repository.jobs.renew_claim(
                claim, lease_seconds=self.lease_seconds,
            ),
            reconcile_unknown_commit=True,
        )
        return replace(
            claim,
            job=job,
            lease_until=datetime.now(UTC) + timedelta(seconds=self.lease_seconds),
        )

    def _checkpoint(
        self, sample_claim: AuditSampleClaim, *, renew: bool = True,
    ) -> AuditSampleClaim:
        started_at = datetime.now(UTC)
        self._storage_retry(sample_claim, lambda: self.audit_repository.checkpoint_sample_claim(
            sample_claim,
            renew=renew,
            lease_seconds=self.lease_seconds,
        ), reconcile_unknown_commit=True)
        if not renew:
            return sample_claim
        return AuditSampleClaim.model_validate({
            **sample_claim.model_dump(),
            "lease_until": min(
                started_at + timedelta(seconds=self.lease_seconds),
                sample_claim.processing_deadline,
            ),
        })

    def _storage_retry(
        self, sample_claim: AuditSampleClaim | None, operation: Callable[[], StorageResult], *,
        reconcile_unknown_commit: bool = False,
        allow_expired: bool = False,
    ) -> StorageResult:
        for retry_number in range(3):
            try:
                return operation()
            except RetryableWorkflowStorageError as error:
                if error.commit_outcome_unknown and not reconcile_unknown_commit:
                    # Callers must explicitly establish replay/reconciliation
                    # safety before retrying a possibly committed mutation.
                    raise
                if retry_number >= 2:
                    raise
                delay = self._retry_delay(retry_number + 1)
                if sample_claim is not None and not allow_expired:
                    remaining = (
                        sample_claim.processing_deadline - datetime.now(UTC)
                    ).total_seconds()
                    if remaining <= 0 and not error.commit_outcome_unknown:
                        raise SampleProcessingTimeoutError(
                            "The sample processing deadline has expired."
                        ) from error
                    delay = min(delay, max(0.0, remaining))
                time.sleep(delay)
        raise AssertionError("unreachable")

    @staticmethod
    def _stop_process(process: Any) -> None:
        if process is None:
            return
        if getattr(process, "pid", None) is None:
            return
        if process.is_alive():
            process.terminate()
            process.join(timeout=1)
        if process.is_alive() and hasattr(process, "kill"):
            process.kill()
            process.join(timeout=1)
        if process.is_alive():
            raise SampleComputationError("The isolated sample process could not be cleaned up.")

    def _snapshot_for_claim(
        self, request: BatchAuditRequest, sample_claim: AuditSampleClaim,
    ) -> SampleAssetManifest | None:
        if request.source_mode is AuditSourceMode.LEGACY:
            if sample_claim.source_mode is not AuditSourceMode.LEGACY:
                raise BatchAuditError("A legacy audit received a snapshot sample claim.")
            return None
        if (
            sample_claim.source_mode is not AuditSourceMode.SNAPSHOT
            or sample_claim.manifest_dataset_id != request.dataset_id
            or self.manifest_repository is None
        ):
            raise BatchAuditError("A snapshot audit received an invalid sample source claim.")
        try:
            manifest = self._storage_retry(
                sample_claim,
                lambda: self.manifest_repository.get_manifest(
                    sample_claim.manifest_dataset_id,
                    sample_claim.sample_id,
                    sample_claim.sample_revision,
                ),
            )
            return self._snapshot_manifest_identity(
                manifest,
                dataset_id=sample_claim.manifest_dataset_id,
                sample_id=sample_claim.sample_id,
                sample_revision=sample_claim.sample_revision,
            )
        except SampleAssetManifestNotFoundError as error:
            raise SampleDataError("The immutable sample snapshot could not be read.") from error

    def _compute_isolated(
        self, request: BatchAuditRequest, rulebook: Rulebook, sample_claim,
    ) -> tuple[Mapping[str, Any], RuleEvaluationResult]:
        """Supervise snapshot preparation, collection and evaluation together."""
        collector = self.measurement_collector
        try:
            pickle.dumps((collector, self.rule_evaluator, rulebook))
        except Exception as error:  # noqa: BLE001 - preflight serialization boundary
            raise SampleComputationError(
                "The sample computation cannot be serialized for isolated execution."
            ) from error

        context = multiprocessing.get_context("forkserver")
        forkserver.set_forkserver_preload(_SAMPLE_PRELOAD)
        receive_connection, send_connection = context.Pipe(duplex=False)
        process = None
        resources = ExitStack()
        watchdog = _SampleWatchdog(sample_claim, lambda: self._stop_process(process))

        def checkpoint(*, renew: bool) -> None:
            watchdog.raise_if_expired()
            started_at = time.monotonic()
            try:
                self._checkpoint(sample_claim, renew=renew)
            except Exception:
                watchdog.raise_if_expired()
                raise
            watchdog.raise_if_expired()
            if renew:
                watchdog.renewed(started_at, self.lease_seconds)

        watchdog.start()
        try:
            checkpoint(renew=True)
            manifest = self._snapshot_for_claim(request, sample_claim)
            checkpoint(renew=True)
            collection_root = request.dataset_root
            asset_settings = None
            if manifest is not None:
                collection_root = str(resources.enter_context(sample_work_directory()))
                asset_settings = self.manifest_repository.asset_settings
            watchdog.raise_if_expired()
            process = context.Process(
                target=run_sample_child,
                args=(
                    send_connection,
                    collector,
                    self.rule_evaluator,
                    rulebook,
                    collection_root,
                    sample_claim.sample_id,
                    request.allowed_labels,
                    request.connectivity,
                    sample_claim.audit_id,
                    sample_claim.sample_revision,
                ),
                kwargs={"snapshot_manifest": manifest, "asset_settings": asset_settings,
                        "feedback_runtime":request.feedback_runtime,
                        "enclosure_runtime": request.enclosure_runtime,
                        "reviewer_feedback": request.reviewer_feedback.get(sample_claim.sample_id),
                        "processing_deadline": sample_claim.processing_deadline,
                        "environment": dict(os.environ)},
                daemon=True,
            )
            try:
                process.start()
            except Exception as error:  # noqa: BLE001 - child startup boundary
                raise SampleComputationError(
                    "The isolated sample process could not start."
                ) from error
            send_connection.close()
            watchdog.raise_if_expired()

            interval = min(0.5, max(0.1, self.lease_seconds / 3))
            chunks: list[bytes] = []
            received = False
            last_checkpoint = time.monotonic()
            while not received:
                watchdog.raise_if_expired()
                if receive_connection.poll(interval):
                    # Frames limit memory per receive. The independent watchdog
                    # still terminates the sender if a partial frame stalls.
                    try:
                        chunk = receive_connection.recv_bytes()
                    except (EOFError, OSError) as error:
                        raise SampleComputationError(
                            "The isolated sample result transport failed."
                        ) from error
                    if chunk:
                        chunks.append(chunk)
                    else:
                        received = True
                    if time.monotonic() - last_checkpoint >= interval:
                        checkpoint(renew=True)
                        last_checkpoint = time.monotonic()
                elif not process.is_alive():
                    if receive_connection.poll(0.2):
                        continue
                    break
                else:
                    checkpoint(renew=True)
            process.join(timeout=1)
            if not received:
                raise SampleComputationError(
                    "The isolated sample process returned no result."
                )
            checkpoint(renew=False)
            try:
                result = pickle.loads(b"".join(chunks))
            except Exception as error:  # noqa: BLE001 - child result is not trusted as valid data
                raise SampleComputationError(
                    "The isolated sample result could not be decoded."
                ) from error
            watchdog.raise_if_expired()
            kind = result[0] if isinstance(result, tuple) and result else None
            if kind == "success":
                if (
                    len(result) != 4
                    or not isinstance(result[1], Mapping)
                    or not isinstance(result[2], list)
                    or type(result[3]) is not int
                    or result[3] < 0
                ):
                    raise SampleComputationError("The isolated sample result had an invalid shape.")
                return result[1], RuleEvaluationResult(
                    findings=result[2], internal_check_failures=result[3],
                )
            detail = result[2] if isinstance(result, tuple) and len(result) > 2 else ""
            error_type = result[1] if isinstance(result, tuple) and len(result) > 1 else "unknown"
            logger.error(
                "Isolated sample computation failed",
                extra={
                    "sample_id": sample_claim.sample_id,
                    "error_type": error_type,
                    "diagnostic": detail,
                },
            )
            if kind == "source_data":
                raise SampleDataError("Source data could not be collected.")
            if kind == "transient":
                raise TransientSampleError("The sample collector reported a transient failure.")
            raise SampleComputationError("The isolated sample computation failed.")
        except Exception:
            watchdog.raise_if_expired()
            raise
        finally:
            try:
                watchdog.stop()
                self._stop_process(process)
            finally:
                receive_connection.close()
                send_connection.close()
                # Preserve the active processing exception through temporary
                # directory cleanup, just as a normal `with` statement would.
                resources.__exit__(*sys.exc_info())

    def _collect_sample(self, request: BatchAuditRequest, sample_claim) -> Mapping[str, Any]:
        manifest = self._snapshot_for_claim(request, sample_claim)
        if manifest is not None and manifest.annotation_format != "penn_fudan":
            from .json_audit import audit_json_sample
            return audit_json_sample(manifest, self.manifest_repository.asset_settings, request.allowed_labels)
        with ExitStack() as resources:
            collection_root = request.dataset_root
            if manifest is not None:
                root = resources.enter_context(sample_work_directory())
                materialize_sample(
                    manifest, asset_settings=self.manifest_repository.asset_settings,
                    target_root=root,
                )
                collection_root = str(root)
            return self.measurement_collector(
                collection_root,
                sample_claim.sample_id,
                request.allowed_labels,
                request.connectivity,
            )

    def _retry_delay(self, retry_number: int) -> float:
        return min(2.0, 0.1 * (2 ** max(0, retry_number - 1))) * random.uniform(0.5, 1.5)

    def _prepare_retry(self, sample_claim, error: BaseException):
        if sample_claim.attempt_count >= 3:
            logger.error(
                "Sample retries exhausted",
                extra={
                    "audit_id": sample_claim.audit_id,
                    "sample_id": sample_claim.sample_id,
                    "diagnostic": str(error),
                },
            )
            return sample_claim, False
        operation_id = "\x1f".join((
            "sample_attempt",
            sample_claim.audit_id,
            sample_claim.sample_id,
            str(sample_claim.sample_revision),
            str(sample_claim.attempt_count),
        ))
        attempt = self._storage_retry(
            sample_claim,
            lambda: self.audit_repository.increment_sample_attempt(
                sample_claim, operation_id=operation_id,
            ),
            reconcile_unknown_commit=True,
        )
        if attempt <= sample_claim.attempt_count:
            return sample_claim, False
        # JobClaim is a dataclass and uses dataclasses.replace above.  The
        # sample claim is a frozen Pydantic model, so rebuild it through
        # validation rather than using dataclasses.replace/model_copy(update=).
        sample_claim = AuditSampleClaim.model_validate({
            **sample_claim.model_dump(),
            "attempt_count": attempt,
        })
        sample_claim = self._checkpoint(sample_claim, renew=True)
        time.sleep(self._retry_delay(attempt))
        sample_claim = self._checkpoint(sample_claim, renew=True)
        return sample_claim, True

    def _failure(
        self, sample_claim, *, reason: str, detail: str,
    ) -> AuditFailure:
        return AuditFailure(
            failure_id=_failure_id(
                sample_claim.audit_id, sample_claim.sample_id,
                sample_claim.sample_revision, reason,
            ),
            audit_id=sample_claim.audit_id,
            sample_id=sample_claim.sample_id,
            sample_revision=sample_claim.sample_revision,
            stage="sample_processing",
            reason=reason,
            detail=detail,
            created_at=datetime.now(UTC),
        )

    def _persist_failure(self, sample_claim, failure: AuditFailure, measurements=None) -> Job:
        operation_id = f"sample_failure\x1f{sample_claim.claim_token}"
        return self._storage_retry(
            sample_claim,
            lambda: self.audit_repository.persist_sample_failure(
                sample_claim,
                failure,
                measurements=measurements if isinstance(measurements, Mapping) else None,
                operation_id=operation_id,
            ),
            reconcile_unknown_commit=True,
        )

    def _persist_success(self, sample_claim, *, measurements, findings, incomplete) -> Job:
        operation_id = f"sample_success\x1f{sample_claim.claim_token}"
        return self._storage_retry(
            sample_claim,
            lambda: self.audit_repository.persist_sample_success(
                sample_claim,
                measurements=measurements,
                findings=findings,
                incomplete=incomplete,
                operation_id=operation_id,
            ),
            reconcile_unknown_commit=True,
        )

    def _process_sample(self, request: BatchAuditRequest, rulebook: Rulebook, sample_claim) -> Job:
        """Run one sample with explicit checkpoints and at most three attempts."""
        measurements: Mapping[str, Any] | None = None
        while True:
            try:
                if self.isolate_sample_computation:
                    measurements, evaluation = self._compute_isolated(
                        request, rulebook, sample_claim,
                    )
                else:
                    measurements = self._collect_sample(request, sample_claim)
                    # Checkpoint after loading/collection and immediately
                    # before evaluation when running in the explicit local
                    # test seam. Isolated mode supervises both phases in the
                    # child and checkpoints continuously from the parent.
                    self._checkpoint(sample_claim, renew=True)
                    self._checkpoint(sample_claim, renew=False)
                    measurements, evaluation = run_rule_checks(
                        self.rule_evaluator, rulebook, measurements,
                        manifest=self._snapshot_for_claim(request, sample_claim),
                        asset_settings=(self.manifest_repository.asset_settings
                                        if self.manifest_repository is not None else None),
                        feedback_runtime=request.feedback_runtime, enclosure_runtime=request.enclosure_runtime,
                        reviewer_feedback=request.reviewer_feedback.get(sample_claim.sample_id),
                        processing_deadline=sample_claim.processing_deadline,
                        checkpoint=lambda: self._checkpoint(sample_claim, renew=False),
                        audit_id=sample_claim.audit_id, sample_id=sample_claim.sample_id,
                        sample_revision=sample_claim.sample_revision,
                    )
                # Preserve the collector's source-processing status. The
                # durable incomplete flag also records evaluator health.
                stored_measurements = deepcopy(dict(measurements))
                incomplete = (
                    stored_measurements.get("status") == "incomplete"
                    or bool(evaluation.internal_check_failures)
                )
                self._checkpoint(sample_claim, renew=True)
                self._checkpoint(sample_claim, renew=False)
                return self._persist_success(
                    sample_claim,
                    measurements=stored_measurements,
                    findings=evaluation.findings,
                    incomplete=incomplete,
                )
            except TransientSampleError as error:
                sample_claim, should_retry = self._prepare_retry(sample_claim, error)
                if should_retry:
                    continue
                failure = self._failure(
                    sample_claim,
                    reason="retry_exhausted",
                    detail="The sample exhausted its processing attempts.",
                )
                return self._persist_failure(sample_claim, failure, measurements)

    def run_audit(self, audit_id: str, *, worker_id: str) -> Job:
        """Claim and process the complete audit sequentially.

        Expected source-data exceptions are recorded as sample failures and the
        worker moves to the next item. Programming, rule-evaluation and storage
        errors fail the job instead. Lease loss is a worker-recovery signal and
        is allowed to escape so a stale worker cannot write again.
        """
        acquisition_id = uuid4().hex
        claim = self._storage_retry(
            None,
            lambda: self.audit_repository.claim_audit(
                audit_id,
                worker_id=worker_id,
                lease_seconds=self.lease_seconds,
                acquisition_id=acquisition_id,
            ),
            reconcile_unknown_commit=True,
        )
        if claim is None:
            return self.audit_repository.get_job(audit_id)
        return self._run_claimed_audit(claim)

    def run_claimed_job(self, claim: JobClaim) -> Job:
        """Process an audit claim returned by the queue without claiming again."""
        prepared = self.audit_repository.prepare_claimed_audit(claim)
        return self._run_claimed_audit(prepared)

    def _run_claimed_audit(self, claim: JobClaim) -> Job:
        """Execute a claim already acquired or verified by the audit repository."""
        audit_id = claim.resource_id
        assert audit_id is not None
        if claim.job.status in (JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED):
            return claim.job
        try:
            request = self.audit_repository.get_request(audit_id)
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
                sample_claim = self._storage_retry(
                    None,
                    lambda: self.audit_repository.claim_next_sample(
                        claim,
                        lease_seconds=self.lease_seconds,
                        processing_seconds=self.sample_max_seconds,
                    ),
                    reconcile_unknown_commit=True,
                )
                if sample_claim is None:
                    current = self.audit_repository.get_job(audit_id)
                    if current.status in (
                        JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED,
                    ):
                        return current
                    return self.audit_repository.finalize_audit(claim)
                try:
                    return_job = self._process_sample(request, rulebook, sample_claim)
                except SampleProcessingTimeoutError:
                    timeout_failure = self._failure(
                        sample_claim,
                        reason="processing_timeout",
                        detail="The sample exceeded its processing time limit.",
                    )
                    try:
                        return_job = self._storage_retry(
                            sample_claim,
                            lambda: self.audit_repository.record_sample_timeout(
                                sample_claim, timeout_failure,
                            ),
                            reconcile_unknown_commit=True,
                            allow_expired=True,
                        )
                    except JobCancellationError:
                        try:
                            return_job = self._storage_retry(
                                sample_claim,
                                lambda: self.audit_repository.cancel_sample_claim(sample_claim),
                                reconcile_unknown_commit=True,
                                allow_expired=True,
                            )
                        except JobClaimLostError:
                            return_job = self.audit_repository.get_job(audit_id)
                except JobCancellationError:
                    try:
                        return_job = self._storage_retry(
                            sample_claim,
                            lambda: self.audit_repository.cancel_sample_claim(sample_claim),
                            reconcile_unknown_commit=True,
                            allow_expired=True,
                        )
                    except JobClaimLostError:
                        return_job = self.audit_repository.get_job(audit_id)
                except JobClaimLostError:
                    raise
                except (OSError, UnicodeError, SampleDataError) as error:
                    logger.warning(
                        "Expected source-data failure",
                        extra={
                            "audit_id": audit_id,
                            "sample_id": sample_claim.sample_id,
                            "diagnostic": str(error),
                        },
                    )
                    failure = self._failure(
                        sample_claim,
                        reason="source_data_unavailable",
                        detail="The sample source data could not be processed.",
                    )
                    return_job = self._persist_failure(sample_claim, failure)
                if return_job.status in (
                    JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED,
                ):
                    return return_job
                claim = replace(claim, job=return_job)
        except JobClaimLostError:
            raise
        except JobCancellationError:
            return self.audit_repository.get_job(audit_id)
        except Exception:  # noqa: BLE001 - convert worker failure to a readable job
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
        """Claim an audit by ID; use run_claimed_job() for an existing claim."""
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
