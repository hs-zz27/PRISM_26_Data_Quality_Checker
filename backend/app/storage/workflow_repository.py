"""PostgreSQL persistence for jobs, batch audits, evidence and findings.

The repositories keep worker leases and domain records in the same database.
Every sample result is committed together with its measurement snapshot,
findings, sample state and job progress.  That transaction boundary is what
makes a restart safe: a worker either leaves a sample queued, or leaves the
complete sample result durably marked terminal.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ValidationError

from ..core.config import (
    AssetStorageSettings, DatabaseSettings, load_asset_storage_settings, load_database_settings,
)
from ..models.asset_manifest import AssetAvailability, AssetKind, SampleAssetManifest
from ..models.audit import AuditFailure, AuditRun, AuditSampleClaim, AuditSourceMode, BatchAuditRequest
from ..models.audit_submission import AuditPage
from ..models.finding import Finding, FindingResolution, FindingReview
from ..models.job import ApiError, Job, JobKind, JobStatus, Progress, ResultReference
from ..models.review import (
    FindingReviewPage, FindingReviewRecord,
    SampleReview, SampleReviewPage, SampleReviewRecord,
)
from .database import (
    CommitOutcomeUnknownError,
    DatabaseError,
    database_connection,
    is_retryable_database_error,
)

_FINDING_WRITE_BATCH_SIZE = 128

class WorkflowStorageError(RuntimeError):
    """The workflow database is unavailable or contains invalid records."""

class RetryableWorkflowStorageError(WorkflowStorageError):
    """A recognized transient database failure that may be retried."""

    def __init__(self, message: str, *, commit_outcome_unknown: bool = False) -> None:
        self.commit_outcome_unknown = commit_outcome_unknown
        super().__init__(message)

class JobNotFoundError(LookupError):
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"Job {job_id!r} was not found.")

class AuditNotFoundError(LookupError):
    def __init__(self, audit_id: str) -> None:
        self.audit_id = audit_id
        super().__init__(f"Audit {audit_id!r} was not found.")

class FindingNotFoundError(LookupError):
    def __init__(self, finding_id: str) -> None:
        self.finding_id = finding_id
        super().__init__(f"Finding {finding_id!r} was not found.")

class FindingReviewConflictError(RuntimeError):
    """A reviewer submitted a decision against an outdated review revision."""

    def __init__(self, finding_id: str, expected_review_version: int, actual_revision: int) -> None:
        self.finding_id = finding_id
        self.expected_review_version = expected_review_version
        self.actual_revision = actual_revision
        super().__init__(
            f"Finding {finding_id!r}: expected review revision {expected_review_version}, "
            f"but found {actual_revision}."
        )

class AuditSampleNotFoundError(LookupError):
    def __init__(self, audit_id: str, sample_id: str) -> None:
        self.audit_id = audit_id
        self.sample_id = sample_id
        super().__init__(f"Sample {sample_id!r} was not found in audit {audit_id!r}.")

class SampleRevisionConflictError(RuntimeError):
    """The requested sample revision is not a stored pin in this audit."""

    def __init__(
        self, audit_id: str, sample_id: str, expected_sample_revision: int,
        available_sample_revisions: list[int],
    ) -> None:
        self.audit_id = audit_id
        self.sample_id = sample_id
        self.expected_sample_revision = expected_sample_revision
        self.available_sample_revisions = tuple(available_sample_revisions)
        super().__init__(
            f"Sample {sample_id!r} in audit {audit_id!r}: sample revision "
            f"{expected_sample_revision} is not stored; available pins: {self.available_sample_revisions}."
        )

class SampleReviewConflictError(RuntimeError):
    """A newer reviewer decision exists for the same audited sample pin."""

    def __init__(
        self, audit_id: str, sample_id: str, sample_revision: int,
        expected_review_version: int, actual_revision: int,
    ) -> None:
        self.audit_id = audit_id
        self.sample_id = sample_id
        self.sample_revision = sample_revision
        self.expected_review_version = expected_review_version
        self.actual_revision = actual_revision
        super().__init__(
            f"Sample {sample_id!r} revision {sample_revision} in audit {audit_id!r}: "
            f"expected review revision {expected_review_version}, but found {actual_revision}."
        )

class SampleReviewStateError(RuntimeError):
    """The sample has not reached a processing state eligible for review."""

    def __init__(self, audit_id: str, sample_id: str, sample_revision: int, status: str) -> None:
        self.audit_id = audit_id
        self.sample_id = sample_id
        self.sample_revision = sample_revision
        self.status = status
        super().__init__(
            f"Sample {sample_id!r} revision {sample_revision} in audit {audit_id!r} "
            f"is {status}; only completed or failed samples can be reviewed."
        )

class SampleAssetManifestNotFoundError(LookupError):
    """No source manifest exists for the exact dataset sample revision."""

    def __init__(self, dataset_id: str, sample_id: str, sample_revision: int) -> None:
        self.dataset_id = dataset_id
        self.sample_id = sample_id
        self.sample_revision = sample_revision
        super().__init__(
            f"Source assets for sample {sample_id!r} revision {sample_revision} "
            f"in dataset {dataset_id!r} were not found."
        )

class SampleAssetManifestConflictError(RuntimeError):
    """A source manifest key already contains different immutable metadata."""

    def __init__(self, dataset_id: str, sample_id: str, sample_revision: int) -> None:
        self.dataset_id = dataset_id
        self.sample_id = sample_id
        self.sample_revision = sample_revision
        super().__init__(
            f"Source assets for sample {sample_id!r} revision {sample_revision} "
            f"in dataset {dataset_id!r} already have a different manifest."
        )

class AssetUnavailableError(LookupError):
    """The requested source asset was recorded as unavailable."""

    def __init__(
        self, dataset_id: str, sample_id: str, sample_revision: int,
        asset_kind: AssetKind, availability: str,
    ) -> None:
        self.dataset_id = dataset_id
        self.sample_id = sample_id
        self.sample_revision = sample_revision
        self.asset_kind = asset_kind
        self.availability = availability
        super().__init__(
            f"The {asset_kind.value} asset for sample {sample_id!r} revision "
            f"{sample_revision} is {availability}."
        )

class AssetIntegrityError(WorkflowStorageError):
    """A captured source asset no longer matches its immutable manifest."""

class AssetReadLimitError(RuntimeError):
    """An asset exceeds the caller's in-memory read budget."""

class JobAlreadyFinishedError(RuntimeError):
    """A cancellation request targeted a terminal job."""

    def __init__(self, job_id: str, status: JobStatus) -> None:
        self.job_id = job_id
        self.status = status
        super().__init__(f"Job {job_id!r} is already {status.value}.")

class JobClaimLostError(RuntimeError):
    """The worker lease was replaced or the job was cancelled/recovered."""

class SampleProcessingTimeoutError(RuntimeError):
    """A sample exceeded its persisted hard processing deadline."""

class JobCancellationError(RuntimeError):
    """A worker attempted to finalize work after cancellation won the race."""

_TIMEOUT_STAGE = "sample_processing"
_TIMEOUT_REASON = "processing_timeout"
_TIMEOUT_DETAIL = "The sample exceeded its processing time limit."

@dataclass(frozen=True)
class JobClaim:
    """Private lease data returned to a worker after an atomic claim."""

    job: Job
    worker_id: str
    claim_token: str
    lease_until: datetime
    resource_kind: str | None = None
    resource_id: str | None = None

def _require_identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")
    return value

def _require_positive_integer(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer.")
    return value

def _timestamp(value: datetime | None = None) -> datetime:
    result = datetime.now(UTC) if value is None else value
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware.")
    return result.astimezone(UTC)

def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(child) for child in value]
    if isinstance(value, (set, frozenset)):
        converted = [_json_safe(child) for child in value]
        try:
            return sorted(converted)
        except TypeError:
            return converted
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, BaseModel):
        return _json_safe(value.model_dump(mode="json"))
    item = getattr(value, "item", None)
    if callable(item):
        return _json_safe(item())
    if isinstance(value, datetime):
        return value.isoformat()
    raise ValueError(f"Value {type(value).__name__} cannot be stored as JSON.")

def _json_document(value: Any) -> dict[str, Any]:
    document = _json_safe(value)
    if not isinstance(document, dict):
        raise ValueError("Stored workflow documents must be JSON objects.")
    try:
        json.dumps(document, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("Stored workflow documents must contain finite JSON values.") from error
    return document

def _validate_measurement_document(document: Mapping[str, Any], sample_id: str) -> dict[str, Any]:
    """Enforce the minimum identity/review contract at the storage boundary."""
    normalized = _json_document(document)
    if normalized.get("image_id") != sample_id:
        raise ValueError("measurement evidence image_id must match the claimed sample_id.")
    if normalized.get("review_status") != "pending_review":
        raise ValueError("measurement evidence must remain pending human review.")
    if "status" in normalized and normalized["status"] not in ("completed", "incomplete"):
        raise ValueError("measurement evidence has an invalid processing status.")
    return normalized

def _job_row_values(job: Job) -> tuple[Any, ...]:
    return (
        job.job_id,
        job.kind.value,
        job.status.value,
        Jsonb(job.progress.model_dump(mode="json")),
        None if job.result is None else Jsonb(job.result.model_dump(mode="json")),
        None if job.error is None else Jsonb(job.error.model_dump(mode="json")),
        job.created_at,
        job.updated_at,
    )

def _decode_job(row: Mapping[str, Any]) -> Job:
    try:
        return Job.model_validate({
            "job_id": row["job_id"],
            "kind": row["kind"],
            "status": row["status"],
            "progress": row["progress"],
            "result": row["result"],
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })
    except (KeyError, ValidationError, TypeError) as error:
        raise WorkflowStorageError("A saved job failed validation.") from error

def _decode_audit(row: Mapping[str, Any]) -> AuditRun:
    try:
        return AuditRun.model_validate({
            "audit_id": row["audit_id"],
            "job_id": row["job_id"],
            "dataset_id": row["dataset_id"],
            "rulebook_id": row["rulebook_id"],
            "rulebook_version": row["rulebook_version"],
            "status": row["status"],
            "source_mode": row.get("source_mode", AuditSourceMode.LEGACY.value),
            "sample_count": row["sample_count"],
            "processed_count": row["processed_count"],
            "failed_count": row["failed_count"],
            "incomplete_count": row.get("incomplete_count", 0),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })
    except (KeyError, ValidationError, TypeError) as error:
        raise WorkflowStorageError("A saved audit failed validation.") from error

def _result_reference(audit_id: str) -> ResultReference:
    return ResultReference(
        kind="audit", id=audit_id, version=None, href=f"/api/v1/audits/{audit_id}"
    )

class _WorkflowRepository:
    def __init__(self, *, settings: DatabaseSettings | None = None) -> None:
        if settings is not None and not isinstance(settings, DatabaseSettings):
            raise TypeError("settings must be a DatabaseSettings instance.")
        self.settings = load_database_settings() if settings is None else settings

    @contextmanager
    def _connection(self, *, write: bool = False):
        try:
            with database_connection(self.settings, readonly=not write) as connection:
                yield connection
        except CommitOutcomeUnknownError as error:
            # Reconcile an uncertain commit independently of SQLSTATE. libpq
            # connection failures may have no server error code at all.
            raise RetryableWorkflowStorageError(
                "The workflow commit outcome requires reconciliation.",
                commit_outcome_unknown=True,
            ) from error
        except DatabaseError as error:
            if is_retryable_database_error(error):
                raise RetryableWorkflowStorageError(
                    "A transient workflow database failure occurred."
                ) from error
            raise WorkflowStorageError("Cannot read or write the workflow database.") from error

def _reject_managed_extraction(row) -> None:
    """Check the locked row, not caller-supplied claim fields, before mutation."""
    if row is not None and row["kind"] == "rule_extraction":
        raise ValueError("Use ExtractionRepository for managed extraction execution.")
    if row is not None and row["kind"] == "dataset_import":
        raise ValueError("Use DatasetImportRepository for managed import execution.")
    if row is not None and row["kind"] == "export":
        raise ValueError("Use ExportRepository for managed export execution.")

def _reject_linked_recheck(connection, row) -> None:
    # The AUDIT worker may claim/renew these jobs, but only AuditRepository may
    # change related lifecycle/progress. Leave unrelated historical jobs alone.
    if row is not None and row['resource_kind'] == 'audit' and connection.execute(
        'SELECT 1 FROM audit_recheck_lineage WHERE child_audit_id=%s',
        (row['resource_id'],),
    ).fetchone() is not None:
        raise ValueError('Use AuditRepository for linked recheck lifecycle changes.')


class JobRepository(_WorkflowRepository):
    """Persist jobs and perform lease-protected state transitions."""

    def create_job(
        self,
        job: Job,
        *,
        resource_kind: str | None = None,
        resource_id: str | None = None,
    ) -> Job:
        if not isinstance(job, Job):
            raise TypeError("job must be a Job instance.")
        snapshot = Job.model_validate(job.model_dump())
        if snapshot.kind is JobKind.RULE_EXTRACTION:
            raise ValueError("Use ExtractionRepository to enqueue managed extraction jobs.")
        if snapshot.kind is JobKind.DATASET_IMPORT:
            raise ValueError("Use DatasetImportRepository to enqueue managed import jobs.")
        if snapshot.kind is JobKind.EXPORT:
            raise ValueError("Use ExportRepository to enqueue managed export jobs.")
        if snapshot.status is not JobStatus.QUEUED:
            raise ValueError("new jobs must start in queued state.")
        if resource_kind is not None:
            _require_identifier(resource_kind, "resource_kind")
        if resource_id is not None:
            _require_identifier(resource_id, "resource_id")
        with self._connection(write=True) as connection:
            connection.execute(
                """INSERT INTO jobs
                   (job_id, kind, status, progress, result, error, created_at, updated_at,
                    resource_kind, resource_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                _job_row_values(snapshot) + (resource_kind, resource_id),
            )
        return snapshot

    def get_job(self, job_id: str) -> Job:
        job_id = _require_identifier(job_id, "job_id")
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = %s", (job_id,)).fetchone()
            if row is None:
                raise JobNotFoundError(job_id)
            return _decode_job(row)

    def get_job_context(self, job_id: str) -> tuple[str | None, str | None]:
        job_id = _require_identifier(job_id, "job_id")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT resource_kind, resource_id FROM jobs WHERE job_id = %s", (job_id,)
            ).fetchone()
            if row is None:
                raise JobNotFoundError(job_id)
            return row["resource_kind"], row["resource_id"]

    def claim_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> JobClaim | None:
        job_id = _require_identifier(job_id, "job_id")
        worker_id = _require_identifier(worker_id, "worker_id")
        lease_seconds = _require_positive_integer(lease_seconds, "lease_seconds")
        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = %s FOR UPDATE", (job_id,)
            ).fetchone()
            _reject_managed_extraction(row)
            _reject_linked_recheck(connection, row)
            if row is None:
                raise JobNotFoundError(job_id)
            timestamp = _timestamp(now)
            lease_until = timestamp + timedelta(seconds=lease_seconds)
            status = row["status"]
            stale = status == JobStatus.RUNNING.value and (
                row["lease_until"] is None or row["lease_until"] <= timestamp
            )
            if status != JobStatus.QUEUED.value and not stale:
                return None
            token = uuid4().hex
            connection.execute(
                """UPDATE jobs
                   SET status = 'running', claimed_by = %s, claim_token = %s,
                       lease_until = %s, attempt = attempt + 1, updated_at = %s
                   WHERE job_id = %s""",
                (worker_id, token, lease_until, timestamp, job_id),
            )
            updated = connection.execute("SELECT * FROM jobs WHERE job_id = %s", (job_id,)).fetchone()
            assert updated is not None
            job = _decode_job(updated)
            return JobClaim(
                job=job,
                worker_id=worker_id,
                claim_token=token,
                lease_until=lease_until,
                resource_kind=updated["resource_kind"],
                resource_id=updated["resource_id"],
            )

    def claim_next_job(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        now: datetime | None = None,
        kind: JobKind | None = None,
    ) -> JobClaim | None:
        """Claim the oldest eligible job, optionally restricted to one kind."""
        worker_id = _require_identifier(worker_id, "worker_id")
        lease_seconds = _require_positive_integer(lease_seconds, "lease_seconds")
        if kind is not None and not isinstance(kind, JobKind):
            raise TypeError("kind must be a JobKind or None.")
        kind_value = None if kind is None else kind.value
        with self._connection(write=True) as connection:
            cutoff = _timestamp(now)
            row = connection.execute(
                """SELECT * FROM jobs
                   WHERE kind NOT IN (%s, %s, %s) AND (status = 'queued'
                      OR (status = 'running' AND lease_until <= %s))
                     AND (%s::text IS NULL OR kind = %s)
                     AND (%s::text = 'audit' OR NOT EXISTS (
                         SELECT 1 FROM audit_recheck_lineage linked
                         WHERE linked.child_audit_id=jobs.resource_id AND jobs.resource_kind='audit'))
                   ORDER BY created_at ASC, job_id ASC
                   FOR UPDATE SKIP LOCKED LIMIT 1""",
                ("rule_extraction", "dataset_import", "export", cutoff, kind_value, kind_value, kind_value),
            ).fetchone()
            if row is None:
                return None
            timestamp = _timestamp(now)
            lease_until = timestamp + timedelta(seconds=lease_seconds)
            token = uuid4().hex
            connection.execute(
                """UPDATE jobs SET status = 'running', claimed_by = %s, claim_token = %s,
                       lease_until = %s, attempt = attempt + 1, updated_at = %s
                   WHERE job_id = %s""",
                (worker_id, token, lease_until, timestamp, row["job_id"]),
            )
            updated = connection.execute(
                "SELECT * FROM jobs WHERE job_id = %s", (row["job_id"],)
            ).fetchone()
            assert updated is not None
            return JobClaim(
                job=_decode_job(updated), worker_id=worker_id, claim_token=token,
                lease_until=lease_until, resource_kind=updated["resource_kind"],
                resource_id=updated["resource_id"],
            )

    def renew_claim(
        self,
        claim: JobClaim,
        *,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> Job:
        lease_seconds = _require_positive_integer(lease_seconds, "lease_seconds")
        with self._connection(write=True) as connection:
            # Lock first: an UPDATE predicate using a pre-wait timestamp could
            # otherwise renew a claim that expired while the row was locked.
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = %s FOR UPDATE", (claim.job.job_id,),
            ).fetchone()
            _reject_managed_extraction(row)
            if row is None or row["claim_token"] != claim.claim_token:
                raise JobClaimLostError("The worker no longer owns this job claim.")
            timestamp = _timestamp(now)
            lease_until = timestamp + timedelta(seconds=lease_seconds)
            result = connection.execute(
                """UPDATE jobs SET lease_until = %s, updated_at = %s
                   WHERE job_id = %s AND status = 'running' AND claim_token = %s
                     AND lease_until > %s""",
                (lease_until, timestamp, claim.job.job_id, claim.claim_token, timestamp),
            )
            if result.rowcount != 1:
                raise JobClaimLostError("The worker no longer owns this job claim.")
            row = connection.execute("SELECT * FROM jobs WHERE job_id = %s", (claim.job.job_id,)).fetchone()
            assert row is not None
            return _decode_job(row)

    def request_cancellation(self, job_id: str, *, now: datetime | None = None) -> Job:
        job_id = _require_identifier(job_id, "job_id")
        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = %s FOR UPDATE", (job_id,)
            ).fetchone()
            if row is None:
                raise JobNotFoundError(job_id)
            if row["kind"] == "export":
                # Reuse this transaction and locked job row; never nest a connection.
                from .export_repository import ExportRepository
                return ExportRepository(settings=self.settings)._cancel_locked(connection, row)
            if row["kind"] == "dataset_import":
                # Local import avoids a module cycle. Reuse THIS transaction/job lock;
                # never open a second transaction while holding the same job row.
                from .dataset_import_repository import DatasetImportRepository
                return DatasetImportRepository(settings=self.settings)._cancel_locked(connection, row)
            _reject_linked_recheck(connection, row)
            timestamp = _timestamp(now)
            status = JobStatus(row["status"])
            if status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                raise JobAlreadyFinishedError(job_id, status)
            new_status = JobStatus.CANCELLED if status is JobStatus.QUEUED else JobStatus.CANCELLING
            connection.execute(
                "UPDATE jobs SET status = %s, updated_at = %s WHERE job_id = %s",
                (new_status.value, timestamp, job_id),
            )
            updated = connection.execute("SELECT * FROM jobs WHERE job_id = %s", (job_id,)).fetchone()
            assert updated is not None
            return _decode_job(updated)

    def finalize_cancellation(self, claim: JobClaim, *, now: datetime | None = None) -> Job:
        """A worker acknowledges cancellation at a safe processing boundary."""
        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = %s FOR UPDATE", (claim.job.job_id,)
            ).fetchone()
            _reject_managed_extraction(row)
            _reject_linked_recheck(connection, row)
            timestamp = _timestamp(now)
            if row is None or row["claim_token"] != claim.claim_token:
                raise JobClaimLostError("The worker no longer owns this job claim.")
            if row["lease_until"] is None or row["lease_until"] <= timestamp:
                raise JobClaimLostError("The worker's job lease has expired.")
            if JobStatus(row["status"]) is not JobStatus.CANCELLING:
                raise JobClaimLostError("The job is not awaiting cancellation finalization.")
            progress = Progress.model_validate(row["progress"])
            fraction = progress.fraction
            if progress.total is not None:
                fraction = 1.0 if progress.total == 0 else progress.processed / progress.total
            cancelled_progress = Progress(
                phase="cancelled", processed=progress.processed,
                total=progress.total, fraction=fraction,
            )
            connection.execute(
                """UPDATE jobs SET status = 'cancelled', progress = %s, result = NULL,
                       error = NULL, claimed_by = NULL, claim_token = NULL, lease_until = NULL,
                       updated_at = %s WHERE job_id = %s AND claim_token = %s""",
                (
                    Jsonb(cancelled_progress.model_dump(mode="json")), timestamp,
                    claim.job.job_id, claim.claim_token,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM jobs WHERE job_id = %s", (claim.job.job_id,)
            ).fetchone()
            assert updated is not None
            return _decode_job(updated)

    def update_progress(self, claim: JobClaim, progress: Progress, *, now: datetime | None = None) -> Job:
        progress = Progress.model_validate(progress.model_dump())
        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = %s FOR UPDATE", (claim.job.job_id,),
            ).fetchone()
            _reject_managed_extraction(row)
            _reject_linked_recheck(connection, row)
            if row is None or row["claim_token"] != claim.claim_token:
                raise JobClaimLostError("The worker no longer owns this job claim.")
            timestamp = _timestamp(now)
            if JobStatus(row["status"]) is JobStatus.CANCELLING:
                raise JobCancellationError("Cancellation won before progress update.")
            if claim.resource_kind == "audit" and claim.resource_id is not None:
                sample = connection.execute(
                    """SELECT processing_deadline FROM audit_samples
                       WHERE audit_id = %s AND status = 'running'
                         AND claim_token = %s
                       LIMIT 1""",
                    (claim.resource_id, claim.claim_token),
                ).fetchone()
                if sample is not None and (
                    sample["processing_deadline"] is None
                    or sample["processing_deadline"] <= timestamp
                ):
                    raise SampleProcessingTimeoutError(
                        "The sample processing deadline has expired."
                    )
            result = connection.execute(
                """UPDATE jobs SET progress = %s, updated_at = %s
                   WHERE job_id = %s AND status IN ('running', 'cancelling') AND claim_token = %s
                     AND lease_until > %s""",
                (
                    Jsonb(progress.model_dump(mode="json")), timestamp,
                    claim.job.job_id, claim.claim_token, timestamp,
                ),
            )
            if result.rowcount != 1:
                raise JobClaimLostError("The worker no longer owns this job claim.")
            row = connection.execute("SELECT * FROM jobs WHERE job_id = %s", (claim.job.job_id,)).fetchone()
            assert row is not None
            return _decode_job(row)

    def complete_job(
        self, claim: JobClaim, result: ResultReference, *, now: datetime | None = None,
    ) -> Job:
        result = ResultReference.model_validate(result.model_dump())
        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = %s FOR UPDATE", (claim.job.job_id,)
            ).fetchone()
            _reject_managed_extraction(row)
            _reject_linked_recheck(connection, row)
            timestamp = _timestamp(now)
            if row is None or row["claim_token"] != claim.claim_token:
                raise JobClaimLostError("The worker no longer owns this job claim.")
            if row["lease_until"] is None or row["lease_until"] <= timestamp:
                raise JobClaimLostError("The worker's job lease has expired.")
            status = JobStatus(row["status"])
            if status is JobStatus.CANCELLING:
                raise JobCancellationError("Cancellation won before completion.")
            if status is not JobStatus.RUNNING:
                raise JobClaimLostError("A terminal job cannot be completed again.")
            connection.execute(
                """UPDATE jobs SET status = 'completed', result = %s, error = NULL,
                       claimed_by = NULL, claim_token = NULL, lease_until = NULL, updated_at = %s
                   WHERE job_id = %s AND claim_token = %s""",
                (Jsonb(result.model_dump(mode="json")), timestamp, claim.job.job_id, claim.claim_token),
            )
            updated = connection.execute("SELECT * FROM jobs WHERE job_id = %s", (claim.job.job_id,)).fetchone()
            assert updated is not None
            return _decode_job(updated)

    def fail_job(self, claim: JobClaim, error: ApiError, *, now: datetime | None = None) -> Job:
        error = ApiError.model_validate(error.model_dump())
        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = %s FOR UPDATE", (claim.job.job_id,)
            ).fetchone()
            _reject_managed_extraction(row)
            _reject_linked_recheck(connection, row)
            timestamp = _timestamp(now)
            if row is None or row["claim_token"] != claim.claim_token:
                raise JobClaimLostError("The worker no longer owns this job claim.")
            if row["lease_until"] is None or row["lease_until"] <= timestamp:
                raise JobClaimLostError("The worker's job lease has expired.")
            status = JobStatus(row["status"])
            if status is JobStatus.CANCELLING:
                raise JobCancellationError("Cancellation won before failure finalization.")
            if status is not JobStatus.RUNNING:
                raise JobClaimLostError("A terminal job cannot fail again.")
            connection.execute(
                """UPDATE jobs SET status = 'failed', error = %s, result = NULL,
                       claimed_by = NULL, claim_token = NULL, lease_until = NULL, updated_at = %s
                   WHERE job_id = %s AND claim_token = %s""",
                (Jsonb(error.model_dump(mode="json")), timestamp, claim.job.job_id, claim.claim_token),
            )
            updated = connection.execute("SELECT * FROM jobs WHERE job_id = %s", (claim.job.job_id,)).fetchone()
            assert updated is not None
            return _decode_job(updated)

    def recover_stale_jobs(self, *, now: datetime | None = None) -> list[Job]:
        """Requeue stale running jobs; stale cancellation is finalized safely."""
        recovered: list[Job] = []
        with self._connection(write=True) as connection:
            cutoff = _timestamp(now)
            rows = connection.execute(
                """SELECT * FROM jobs
                   WHERE kind NOT IN (%s, %s, %s) AND ((status = 'running' AND lease_until <= %s)
                      OR (status = 'cancelling' AND lease_until <= %s))
                     AND NOT EXISTS (SELECT 1 FROM audit_recheck_lineage linked
                         WHERE linked.child_audit_id=jobs.resource_id AND jobs.resource_kind='audit')
                   FOR UPDATE SKIP LOCKED""",
                ("rule_extraction", "dataset_import", "export", cutoff, cutoff),
            ).fetchall()
            timestamp = _timestamp(now)
            for row in rows:
                new_status = "queued" if row["status"] == "running" else "cancelled"
                connection.execute(
                    """UPDATE jobs SET status = %s, claimed_by = NULL, claim_token = NULL,
                           lease_until = NULL, recovery_count = recovery_count + 1,
                           updated_at = %s WHERE job_id = %s""",
                    (new_status, timestamp, row["job_id"]),
                )
                updated = connection.execute(
                    "SELECT * FROM jobs WHERE job_id = %s", (row["job_id"],)
                ).fetchone()
                assert updated is not None
                recovered.append(_decode_job(updated))
        return recovered

def _decode_finding(row: Mapping[str, Any]) -> Finding:
    """Read the original machine snapshot, including its initial review state."""
    try:
        finding = Finding.model_validate(row["document"])
    except (KeyError, ValidationError, TypeError) as error:
        raise WorkflowStorageError("A saved finding document failed validation.") from error
    if (
        finding.finding_id != row["finding_id"]
        or finding.audit_id != row["audit_id"]
        or finding.sample_id != row["sample_id"]
        or finding.sample_revision != row["sample_revision"]
        or finding.object_id != row["object_id"]
        or finding.rulebook_id != row["rulebook_id"]
        or finding.rulebook_version != row["rulebook_version"]
        or finding.rule_id != row["rule_id"]
        or finding.outcome.value != row["outcome"]
        or finding.review.resolution.value != row["review_resolution"]
    ):
        raise WorkflowStorageError("A saved finding document disagrees with its metadata.")
    return finding

_FINDINGS_WITH_REVIEWS = """
    SELECT findings.*,
           latest.revision AS current_review_revision,
           latest.resolution AS current_review_resolution,
           latest.reviewer_id AS current_review_reviewer_id,
           latest.comment AS current_review_comment,
           latest.updated_at AS current_review_updated_at
    FROM findings
    LEFT JOIN LATERAL (
        SELECT revision, resolution, reviewer_id, comment, updated_at
        FROM finding_reviews WHERE finding_id = findings.finding_id
        ORDER BY revision DESC LIMIT 1
    ) AS latest ON TRUE
"""

def _decode_reviewed_finding(row: Mapping[str, Any]) -> Finding:
    finding = _decode_finding(row)
    if row["current_review_revision"] is not None:
        try:
            finding.review = FindingReview.model_validate({
                "revision": row["current_review_revision"],
                "resolution": row["current_review_resolution"],
                "reviewer_id": row["current_review_reviewer_id"],
                "comment": row["current_review_comment"],
                "updated_at": row["current_review_updated_at"],
            })
        except ValidationError as error:
            raise WorkflowStorageError("A saved finding review failed validation.") from error
    return finding

class FindingRepository(_WorkflowRepository):
    """Store machine snapshots separately from append-only reviewer decisions."""

    @staticmethod
    def _snapshot_findings(findings: Sequence[Finding]) -> list[Finding]:
        if isinstance(findings, (str, bytes)):
            raise TypeError("findings must be a sequence of Finding instances.")
        snapshots: list[Finding] = []
        seen: set[str] = set()
        for finding in findings:
            if not isinstance(finding, Finding):
                raise TypeError("findings must contain Finding instances.")
            snapshot = Finding.model_validate(finding.model_dump())
            if (
                snapshot.review.resolution is not FindingResolution.PENDING
                or snapshot.review.revision != 0
                or snapshot.review.reviewer_id is not None
                or snapshot.review.updated_at is not None
            ):
                raise ValueError("new machine findings must remain pending review.")
            if snapshot.finding_id in seen:
                raise ValueError("finding IDs must be unique within one write.")
            seen.add(snapshot.finding_id)
            snapshots.append(snapshot)
        return snapshots

    def save_findings(self, findings: Sequence[Finding]) -> list[Finding]:
        snapshots = self._snapshot_findings(findings)
        if not snapshots:
            return []
        timestamp = datetime.now(UTC)
        with self._connection(write=True) as connection:
            for finding in snapshots:
                document = Jsonb(finding.model_dump(mode="json"))
                connection.execute(
                    """INSERT INTO findings
                       (finding_id, audit_id, sample_id, sample_revision, object_id,
                        rulebook_id, rulebook_version, rule_id, outcome, review_resolution,
                        document, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (finding_id) DO NOTHING""",
                    (
                        finding.finding_id, finding.audit_id, finding.sample_id,
                        finding.sample_revision, finding.object_id, finding.rulebook_id,
                        finding.rulebook_version, finding.rule_id, finding.outcome.value,
                        finding.review.resolution.value, document, timestamp,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM findings WHERE finding_id = %s", (finding.finding_id,)
                ).fetchone()
                assert row is not None
                stored = _decode_finding(row)
                if stored != finding:
                    raise WorkflowStorageError(
                        f"Finding {finding.finding_id!r} already exists with different evidence."
                    )
        return [Finding.model_validate(finding.model_dump()) for finding in snapshots]

    def get_finding(self, finding_id: str) -> Finding:
        finding_id = _require_identifier(finding_id, "finding_id")
        with self._connection() as connection:
            row = connection.execute(
                _FINDINGS_WITH_REVIEWS + " WHERE findings.finding_id = %s", (finding_id,)
            ).fetchone()
            if row is None:
                raise FindingNotFoundError(finding_id)
            return _decode_reviewed_finding(row)

    def list_findings(
        self,
        audit_id: str,
        *,
        sample_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Finding]:
        audit_id = _require_identifier(audit_id, "audit_id")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100.")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a nonnegative integer.")
        if sample_id is not None:
            sample_id = _require_identifier(sample_id, "sample_id")
        with self._connection() as connection:
            if sample_id is None:
                rows = connection.execute(
                    _FINDINGS_WITH_REVIEWS + """ WHERE findings.audit_id = %s
                       ORDER BY findings.finding_id LIMIT %s OFFSET %s""",
                    (audit_id, limit, offset),
                ).fetchall()
            else:
                rows = connection.execute(
                    _FINDINGS_WITH_REVIEWS + """ WHERE findings.audit_id = %s AND findings.sample_id = %s
                       ORDER BY findings.finding_id LIMIT %s OFFSET %s""",
                    (audit_id, sample_id, limit, offset),
                ).fetchall()
            return [_decode_reviewed_finding(row) for row in rows]

    def save_finding_review(
        self, finding_id: str, review: FindingReview, *, expected_review_version: int,
    ) -> FindingReview:
        """Append one decision, checking the expected revision under a row lock.

        Machine documents and their metadata are never rewritten. All saves
        for the same finding lock its existing row before reading the latest
        review, so competing decisions cannot both advance the same revision.
        """
        finding_id = _require_identifier(finding_id, "finding_id")
        if type(expected_review_version) is not int or expected_review_version < 0:
            raise ValueError("expected_review_version must be a nonnegative integer.")
        if not isinstance(review, FindingReview):
            raise TypeError("review must be a FindingReview instance.")
        record = FindingReviewRecord.model_validate({
            **review.model_dump(), "finding_id": finding_id,
        })
        if record.revision != expected_review_version + 1:
            raise ValueError("The saved review revision must equal expected_review_version + 1.")
        with self._connection(write=True) as connection:
            exists = connection.execute(
                "SELECT finding_id FROM findings WHERE finding_id = %s FOR UPDATE", (finding_id,),
            ).fetchone()
            if exists is None:
                raise FindingNotFoundError(finding_id)
            actual_revision = connection.execute(
                "SELECT COALESCE(MAX(revision), 0) AS revision FROM finding_reviews WHERE finding_id = %s",
                (finding_id,),
            ).fetchone()["revision"]
            if actual_revision != expected_review_version:
                raise FindingReviewConflictError(finding_id, expected_review_version, actual_revision)
            connection.execute(
                """INSERT INTO finding_reviews
                   (finding_id, revision, resolution, reviewer_id, comment, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (record.finding_id, record.revision, record.resolution.value,
                 record.reviewer_id, record.comment, record.updated_at),
            )
        return FindingReview.model_validate(record.model_dump(exclude={"finding_id"}))

    def list_finding_reviews(
        self, finding_id: str, *, limit: int = 20, offset: int = 0,
    ) -> FindingReviewPage:
        """Read attributed decisions in revision order; pending has no history row."""
        finding_id = _require_identifier(finding_id, "finding_id")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100.")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a nonnegative integer.")
        with self._connection() as connection:
            exists = connection.execute(
                "SELECT finding_id FROM findings WHERE finding_id = %s", (finding_id,),
            ).fetchone()
            if exists is None:
                raise FindingNotFoundError(finding_id)
            total = connection.execute(
                "SELECT COUNT(*) AS total FROM finding_reviews WHERE finding_id = %s", (finding_id,),
            ).fetchone()["total"]
            rows = connection.execute(
                """SELECT * FROM finding_reviews WHERE finding_id = %s
                   ORDER BY revision ASC LIMIT %s OFFSET %s""",
                (finding_id, limit, offset),
            ).fetchall()
            try:
                items = [FindingReviewRecord.model_validate(row) for row in rows]
            except ValidationError as error:
                raise WorkflowStorageError("A saved finding review failed validation.") from error
        next_offset = offset + len(items) if offset + len(items) < total else None
        return FindingReviewPage(
            items=items, limit=limit, offset=offset, total=total, next_offset=next_offset,
        )

class SampleReviewRepository(_WorkflowRepository):
    """Review explicit audit sample pins without rewriting processing evidence."""

    @staticmethod
    def _sample_key(audit_id: str, sample_id: str, sample_revision: int) -> tuple[str, str, int]:
        return (
            _require_identifier(audit_id, "audit_id"),
            _require_identifier(sample_id, "sample_id"),
            _require_positive_integer(sample_revision, "sample_revision"),
        )

    @staticmethod
    def _get_sample(connection: Any, key: tuple[str, str, int], *, lock: bool = False) -> Mapping[str, Any]:
        row = connection.execute(
            """SELECT status FROM audit_samples
               WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s"""
            + (" FOR UPDATE" if lock else ""), key,
        ).fetchone()
        if row is None:
            revisions = connection.execute(
                """SELECT sample_revision FROM audit_samples WHERE audit_id = %s AND sample_id = %s
                   ORDER BY sample_revision""", key[:2],
            ).fetchall()
            if not revisions:
                raise AuditSampleNotFoundError(key[0], key[1])
            raise SampleRevisionConflictError(
                *key, [item["sample_revision"] for item in revisions],
            )
        return row

    def get_sample_review(self, audit_id: str, sample_id: str, sample_revision: int) -> SampleReview:
        """Return the latest decision, or an unreviewed state for an existing pin."""
        key = self._sample_key(audit_id, sample_id, sample_revision)
        with self._connection() as connection:
            self._get_sample(connection, key)
            row = connection.execute(
                """SELECT * FROM sample_reviews
                   WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s
                   ORDER BY revision DESC LIMIT 1""", key,
            ).fetchone()
            if row is None:
                return SampleReview(audit_id=key[0], sample_id=key[1], sample_revision=key[2])
            try:
                record = SampleReviewRecord.model_validate(row)
            except ValidationError as error:
                raise WorkflowStorageError("A saved sample review failed validation.") from error
        return SampleReview.model_validate(record.model_dump())

    def save_sample_review(self, review: SampleReview, *, expected_review_version: int) -> SampleReview:
        """Append a decision after checking sample state and review revision atomically.

        Completed (including incomplete measurements) and failed samples are
        reviewable regardless of machine outcomes or the parent audit's status.
        Failures remain visible; accepting them is an explicit reviewer decision.
        No decision certifies that on-disk assets still match this audit's pin.
        """
        if type(expected_review_version) is not int or expected_review_version < 0:
            raise ValueError("expected_review_version must be a nonnegative integer.")
        if not isinstance(review, SampleReview):
            raise TypeError("review must be a SampleReview instance.")
        record = SampleReviewRecord.model_validate(review.model_dump())
        if record.revision != expected_review_version + 1:
            raise ValueError("The saved review revision must equal expected_review_version + 1.")
        key = (record.audit_id, record.sample_id, record.sample_revision)
        with self._connection(write=True) as connection:
            sample = self._get_sample(connection, key, lock=True)
            if sample["status"] not in ("completed", "failed"):
                raise SampleReviewStateError(*key, sample["status"])
            actual_revision = connection.execute(
                """SELECT COALESCE(MAX(revision), 0) AS revision FROM sample_reviews
                   WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s""", key,
            ).fetchone()["revision"]
            if actual_revision != expected_review_version:
                raise SampleReviewConflictError(*key, expected_review_version, actual_revision)
            connection.execute(
                """INSERT INTO sample_reviews
                   (audit_id, sample_id, sample_revision, revision, decision, reviewer_id, comment, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (*key, record.revision, record.decision.value,
                 record.reviewer_id, record.comment, record.updated_at),
            )
        return SampleReview.model_validate(record.model_dump())

    def list_sample_reviews(
        self, audit_id: str, sample_id: str, sample_revision: int, *, limit: int = 20, offset: int = 0,
    ) -> SampleReviewPage:
        """Read one audit/sample pin's history in ascending review revision order."""
        key = self._sample_key(audit_id, sample_id, sample_revision)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100.")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a nonnegative integer.")
        with self._connection() as connection:
            self._get_sample(connection, key)
            total = connection.execute(
                """SELECT COUNT(*) AS total FROM sample_reviews
                   WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s""", key,
            ).fetchone()["total"]
            rows = connection.execute(
                """SELECT * FROM sample_reviews
                   WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s
                   ORDER BY revision ASC LIMIT %s OFFSET %s""", (*key, limit, offset),
            ).fetchall()
            try:
                items = [SampleReviewRecord.model_validate(row) for row in rows]
            except ValidationError as error:
                raise WorkflowStorageError("A saved sample review failed validation.") from error
        next_offset = offset + len(items) if offset + len(items) < total else None
        return SampleReviewPage(
            items=items, limit=limit, offset=offset, total=total, next_offset=next_offset,
        )

def read_manifest_asset_file(
    manifest: SampleAssetManifest,
    asset_kind: AssetKind | str,
    asset_settings: AssetStorageSettings,
    *,
    max_bytes: int | None = None,
) -> tuple[Path, bytes]:
    """Read exact managed bytes using already resolved metadata, without SQL."""
    if max_bytes is not None and (type(max_bytes) is not int or max_bytes < 1):
        raise ValueError("max_bytes must be a positive integer.")
    try:
        kind = AssetKind(asset_kind)
    except (TypeError, ValueError):
        raise ValueError("asset_kind is not a supported sample asset.") from None
    entry = getattr(manifest, kind.value)
    if entry.availability is not AssetAvailability.AVAILABLE:
        raise AssetUnavailableError(
            manifest.dataset_id, manifest.sample_id, manifest.sample_revision,
            kind, entry.availability.value,
        )
    if entry.storage_key is None or entry.sha256 is None or entry.size_bytes is None:
        raise AssetIntegrityError("The stored sample asset manifest is incomplete.")
    if max_bytes is not None and entry.size_bytes > max_bytes:
        raise AssetReadLimitError("The stored sample asset exceeds the read byte limit.")
    try:
        root = asset_settings.root.resolve(strict=False)
        raw_candidate = root / entry.storage_key
        if raw_candidate.is_symlink():
            raise OSError
        candidate = raw_candidate.resolve(strict=False)
        candidate.relative_to(root)
        if not candidate.is_file():
            raise OSError
        with candidate.open("rb") as source:
            metadata = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size != entry.size_bytes
            ):
                raise OSError
            # An extra byte detects growth after fstat without reading an
            # unbounded replacement file into memory.
            content = source.read(entry.size_bytes + 1)
    except (OSError, RuntimeError, ValueError):
        raise AssetIntegrityError("The stored sample asset is unavailable or outside private storage.") from None
    if len(content) != entry.size_bytes or sha256(content).hexdigest() != entry.sha256:
        raise AssetIntegrityError("The stored sample asset does not match its manifest.")
    return candidate, content

class SampleAssetManifestRepository(_WorkflowRepository):
    """Register and resolve immutable source metadata for exact sample revisions."""

    def __init__(
        self, *, settings: DatabaseSettings | None = None,
        asset_settings: AssetStorageSettings | None = None,
    ) -> None:
        super().__init__(settings=settings)
        if asset_settings is not None and not isinstance(asset_settings, AssetStorageSettings):
            raise TypeError("asset_settings must be an AssetStorageSettings instance.")
        self.asset_settings = load_asset_storage_settings() if asset_settings is None else asset_settings

    @staticmethod
    def _key(dataset_id: str, sample_id: str, sample_revision: int) -> tuple[str, str, int]:
        return (
            _require_identifier(dataset_id, "dataset_id"),
            _require_identifier(sample_id, "sample_id"),
            _require_positive_integer(sample_revision, "sample_revision"),
        )

    @staticmethod
    def _row_values(manifest: SampleAssetManifest) -> tuple[Any, ...]:
        def values(entry: Any) -> tuple[Any, ...]:
            return (
                entry.availability.value, entry.storage_key, entry.content_type,
                entry.sha256, entry.size_bytes,
            )

        return (
            manifest.dataset_id, manifest.sample_id, manifest.sample_revision,
            *values(manifest.original_image), *values(manifest.raw_mask),
            *values(manifest.annotation_text),
        )

    @staticmethod
    def _decode(row: Mapping[str, Any]) -> SampleAssetManifest:
        try:
            return SampleAssetManifest.model_validate({
                "dataset_id": row["dataset_id"],
                "sample_id": row["sample_id"],
                "sample_revision": row["sample_revision"],
                "original_image": {
                    "availability": row["original_image_availability"],
                    "storage_key": row["original_image_storage_key"],
                    "content_type": row["original_image_content_type"],
                    "sha256": row["original_image_sha256"],
                    "size_bytes": row["original_image_size_bytes"],
                },
                "raw_mask": {
                    "availability": row["raw_mask_availability"],
                    "storage_key": row["raw_mask_storage_key"],
                    "content_type": row["raw_mask_content_type"],
                    "sha256": row["raw_mask_sha256"],
                    "size_bytes": row["raw_mask_size_bytes"],
                },
                "annotation_text": {
                    "availability": row["annotation_text_availability"],
                    "storage_key": row["annotation_text_storage_key"],
                    "content_type": row["annotation_text_content_type"],
                    "sha256": row["annotation_text_sha256"],
                    "size_bytes": row["annotation_text_size_bytes"],
                },
            })
        except (KeyError, TypeError, ValueError, ValidationError) as error:
            raise WorkflowStorageError("A saved sample asset manifest failed validation.") from error

    def save_manifest(self, manifest: SampleAssetManifest) -> SampleAssetManifest:
        """Insert one manifest, allowing only an identical replay for its key."""
        if not isinstance(manifest, SampleAssetManifest):
            raise TypeError("manifest must be a SampleAssetManifest instance.")
        snapshot = SampleAssetManifest.model_validate(manifest.model_dump(mode="json"))
        with self._connection(write=True) as connection:
            connection.execute(
                """INSERT INTO sample_asset_manifests (
                       dataset_id, sample_id, sample_revision,
                       original_image_availability, original_image_storage_key,
                       original_image_content_type, original_image_sha256,
                       original_image_size_bytes,
                       raw_mask_availability, raw_mask_storage_key,
                       raw_mask_content_type, raw_mask_sha256, raw_mask_size_bytes,
                       annotation_text_availability, annotation_text_storage_key,
                       annotation_text_content_type, annotation_text_sha256,
                       annotation_text_size_bytes
                   ) VALUES (
                       %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, %s, %s
                   ) ON CONFLICT (dataset_id, sample_id, sample_revision) DO NOTHING""",
                self._row_values(snapshot),
            )
            row = connection.execute(
                """SELECT * FROM sample_asset_manifests
                   WHERE dataset_id = %s AND sample_id = %s AND sample_revision = %s""",
                (snapshot.dataset_id, snapshot.sample_id, snapshot.sample_revision),
            ).fetchone()
            if row is None:
                raise WorkflowStorageError("The sample asset manifest could not be read after registration.")
            saved = self._decode(row)
        if saved != snapshot:
            raise SampleAssetManifestConflictError(
                snapshot.dataset_id, snapshot.sample_id, snapshot.sample_revision,
            )
        return SampleAssetManifest.model_validate(saved.model_dump(mode="json"))

    def get_manifest(self, dataset_id: str, sample_id: str, sample_revision: int) -> SampleAssetManifest:
        """Retrieve only the manifest for the exact dataset/sample/revision key."""
        key = self._key(dataset_id, sample_id, sample_revision)
        with self._connection() as connection:
            row = connection.execute(
                """SELECT * FROM sample_asset_manifests
                   WHERE dataset_id = %s AND sample_id = %s AND sample_revision = %s""",
                key,
            ).fetchone()
            if row is None:
                raise SampleAssetManifestNotFoundError(*key)
            return self._decode(row)

    def resolve_asset_path(
        self, dataset_id: str, sample_id: str, sample_revision: int,
        asset_kind: AssetKind | str,
    ) -> Path:
        """Resolve and verify one private captured file without exposing its root."""
        manifest = self.get_manifest(dataset_id, sample_id, sample_revision)
        path, _ = read_manifest_asset_file(manifest, asset_kind, self.asset_settings)
        return path

    def read_asset_bytes(
        self, dataset_id: str, sample_id: str, sample_revision: int,
        asset_kind: AssetKind | str,
        *, max_bytes: int | None = None,
    ) -> bytes:
        """Read and verify exact managed bytes for an immutable manifest entry."""
        manifest = self.get_manifest(dataset_id, sample_id, sample_revision)
        _, content = read_manifest_asset_file(
            manifest, asset_kind, self.asset_settings, max_bytes=max_bytes,
        )
        return content

class AuditRepository(_WorkflowRepository):
    """Persist audit work items and commit one complete sample at a time."""

    def __init__(self, *, settings: DatabaseSettings | None = None) -> None:
        super().__init__(settings=settings)
        self.jobs = JobRepository(settings=self.settings)

    @staticmethod
    def _snapshot_request(request: BatchAuditRequest) -> BatchAuditRequest:
        if not isinstance(request, BatchAuditRequest):
            raise TypeError("request must be a BatchAuditRequest instance.")
        snapshot = BatchAuditRequest.model_validate(request.model_dump())
        return snapshot

    def start_audit(
        self, request: BatchAuditRequest, *,
        sample_manifests: Mapping[str, SampleAssetManifest] | None = None,
        audit_id: str | None = None, job_id: str | None = None,
        now: datetime | None = None,
    ) -> tuple[AuditRun, Job]:
        # Keep ordinary submission validation before acquiring a connection.
        request = self._snapshot_request(request)
        with self._connection(write=True) as connection:
            return self._start_audit_locked(connection, request,
                sample_manifests=sample_manifests, audit_id=audit_id, job_id=job_id, now=now)

    def _start_audit_locked(
        self,
        connection,
        request: BatchAuditRequest,
        *,
        sample_manifests: Mapping[str, SampleAssetManifest] | None = None,
        audit_id: str | None = None,
        job_id: str | None = None,
        now: datetime | None = None,
    ) -> tuple[AuditRun, Job]:
        request = self._snapshot_request(request)
        if request.source_mode is AuditSourceMode.SNAPSHOT:
            if sample_manifests is None or set(sample_manifests) != set(request.sample_ids):
                raise ValueError("snapshot audits require one manifest for every sample.")
            manifests = {}
            for sample_id in request.sample_ids:
                manifest = sample_manifests[sample_id]
                if not isinstance(manifest, SampleAssetManifest):
                    raise TypeError("sample_manifests must contain SampleAssetManifest instances.")
                expected_revision = request.sample_revisions.get(sample_id)
                if (
                    manifest.dataset_id != request.dataset_id
                    or manifest.sample_id != sample_id
                    or expected_revision != manifest.sample_revision
                ):
                    raise ValueError("each snapshot manifest must match its audit sample identity.")
                manifests[sample_id] = SampleAssetManifest.model_validate(
                    manifest.model_dump(mode="json")
                )
        elif sample_manifests is not None:
            raise ValueError("legacy audits cannot contain sample manifests.")
        else:
            manifests = {}
        audit_id = f"audit_{uuid4().hex}" if audit_id is None else _require_identifier(audit_id, "audit_id")
        job_id = f"job_{uuid4().hex}" if job_id is None else _require_identifier(job_id, "job_id")
        timestamp = _timestamp(now)
        job = Job.queued(job_id=job_id, kind=JobKind.AUDIT, total=len(request.sample_ids), now=timestamp)
        audit = AuditRun(
            audit_id=audit_id,
            job_id=job_id,
            dataset_id=request.dataset_id,
            rulebook_id=request.rulebook_id,
            rulebook_version=request.rulebook_version,
            status=JobStatus.QUEUED,
            source_mode=request.source_mode,
            sample_count=len(request.sample_ids),
            processed_count=0,
            failed_count=0,
            incomplete_count=0,
            created_at=timestamp,
            updated_at=timestamp,
        )
        request_document = Jsonb(request.model_dump(mode="json"))
        connection.execute(
            """INSERT INTO jobs
               (job_id, kind, status, progress, result, error, created_at, updated_at,
                resource_kind, resource_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'audit', %s)""",
            _job_row_values(job) + (audit_id,),
        )
        connection.execute(
            """INSERT INTO audit_runs
                (audit_id, job_id, dataset_id, dataset_root, rulebook_id, rulebook_version,
                 status, source_mode, request, sample_count, processed_count, failed_count,
                 incomplete_count, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0, 0, %s, %s)""",
            (
                audit.audit_id, audit.job_id, audit.dataset_id, request.dataset_root,
                audit.rulebook_id, audit.rulebook_version, audit.status.value,
                request.source_mode.value,
                request_document, audit.sample_count, timestamp, timestamp,
            ),
        )
        with connection.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO audit_samples
                   (audit_id, sample_id, sample_revision, ordinal, status, manifest_dataset_id)
                   VALUES (%s, %s, %s, %s, 'queued', %s)""",
                [
                    (
                        audit_id, sample_id,
                        request.sample_revisions[sample_id]
                        if request.source_mode is AuditSourceMode.SNAPSHOT
                        else request.sample_revisions.get(sample_id, 1),
                        ordinal,
                        manifests[sample_id].dataset_id if manifests else None,
                    )
                    for ordinal, sample_id in enumerate(request.sample_ids)
                ],
            )
        return audit, job

    def get_audit(self, audit_id: str) -> AuditRun:
        audit_id = _require_identifier(audit_id, "audit_id")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM audit_runs WHERE audit_id = %s", (audit_id,)
            ).fetchone()
            if row is None:
                raise AuditNotFoundError(audit_id)
            return _decode_audit(row)

    def list_audits(self, *, limit: int = 20, offset: int = 0) -> AuditPage:
        """Page public summaries in a consistent snapshot, newest first."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100.")
        if type(offset) is not int or not 0 <= offset <= 9_223_372_036_854_775_807:
            raise ValueError("offset must be a supported nonnegative integer.")
        with self._connection() as connection:
            total = connection.execute("SELECT count(*) AS total FROM audit_runs").fetchone()["total"]
            rows = connection.execute(
                """SELECT audit_id, job_id, dataset_id, rulebook_id, rulebook_version,
                          source_mode, status, sample_count, processed_count, failed_count,
                          incomplete_count, created_at, updated_at
                   FROM audit_runs ORDER BY created_at DESC, audit_id COLLATE "C" DESC
                   LIMIT %s OFFSET %s""", (limit, offset),
            ).fetchall()
            items = [_decode_audit(row) for row in rows]
        return AuditPage(
            items=items, limit=limit, offset=offset, total=total,
            next_offset=offset + len(items) if offset + len(items) < total else None,
        )

    def get_request(self, audit_id: str) -> BatchAuditRequest:
        audit_id = _require_identifier(audit_id, "audit_id")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT request FROM audit_runs WHERE audit_id = %s", (audit_id,)
            ).fetchone()
            if row is None:
                raise AuditNotFoundError(audit_id)
            try:
                return BatchAuditRequest.model_validate(row["request"])
            except ValidationError as error:
                raise WorkflowStorageError("A saved audit request failed validation.") from error

    def get_job(self, audit_id: str) -> Job:
        audit_id = _require_identifier(audit_id, "audit_id")
        with self._connection() as connection:
            row = connection.execute(
                """SELECT jobs.* FROM jobs JOIN audit_runs ON audit_runs.job_id = jobs.job_id
                   WHERE audit_runs.audit_id = %s""",
                (audit_id,),
            ).fetchone()
            if row is None:
                raise AuditNotFoundError(audit_id)
            return _decode_job(row)

    def claim_audit(
        self,
        audit_id: str,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        acquisition_id: str | None = None,
        now: datetime | None = None,
    ) -> JobClaim | None:
        audit_id = _require_identifier(audit_id, "audit_id")
        worker_id = _require_identifier(worker_id, "worker_id")
        lease_seconds = _require_positive_integer(lease_seconds, "lease_seconds")
        token = uuid4().hex if acquisition_id is None else _require_identifier(
            acquisition_id, "acquisition_id",
        )
        with self._connection(write=True) as connection:
            row = connection.execute(
                """SELECT jobs.*
                   FROM jobs JOIN audit_runs ON audit_runs.job_id = jobs.job_id
                   WHERE audit_runs.audit_id = %s
                   FOR UPDATE OF jobs, audit_runs""",
                (audit_id,),
            ).fetchone()
            if row is None:
                raise AuditNotFoundError(audit_id)
            if row["resource_kind"] != "audit" or row["resource_id"] != audit_id:
                raise WorkflowStorageError("The audit job points to a different resource.")

            cutoff = _timestamp(now)
            status = JobStatus(row["status"])
            stale = status is JobStatus.RUNNING and (
                row["lease_until"] is None or row["lease_until"] <= cutoff
            )
            if stale and row["claim_token"] == token:
                raise JobClaimLostError("This acquisition expired and cannot be revived.")
            if status is not JobStatus.QUEUED and not stale:
                if (
                    status is JobStatus.RUNNING
                    and row["claimed_by"] == worker_id
                    and row["claim_token"] == token
                ):
                    existing = _decode_job(row)
                    return JobClaim(
                        job=existing,
                        worker_id=worker_id,
                        claim_token=row["claim_token"],
                        lease_until=row["lease_until"],
                        resource_kind=row["resource_kind"],
                        resource_id=row["resource_id"],
                    )
                return None

            timestamp = _timestamp(now)
            if stale:
                self._record_expired_sample_timeouts(
                    connection, audit_id=audit_id, now=now,
                    parent_lease_until=row["lease_until"],
                )
            # A stale worker can be replaced directly by the next worker. Reset
            # only its uncommitted sample; completed samples retain their
            # measurements and findings. Keeping this in the same transaction
            # as the new lease prevents two workers from owning the audit.
            if stale:
                connection.execute(
                    """UPDATE audit_samples SET status = 'queued', claimed_by = NULL,
                               claim_token = NULL, lease_until = NULL, processing_deadline = NULL,
                               terminal_operation_id = NULL
                       WHERE audit_id = %s AND status = 'running'""",
                    (audit_id,),
                )

            # The new lease starts after every abandoned-sample lock/reset has
            # completed, not from a timestamp captured before that wait.
            timestamp = _timestamp(now)
            lease_until = timestamp + timedelta(seconds=lease_seconds)
            connection.execute(
                """UPDATE jobs
                   SET status = 'running', claimed_by = %s, claim_token = %s,
                       lease_until = %s, attempt = attempt + 1, updated_at = %s
                   WHERE job_id = %s""",
                (worker_id, token, lease_until, timestamp, row["job_id"]),
            )
            progress, _, _, _ = self._progress(connection, audit_id, "processing")
            connection.execute(
                """UPDATE jobs SET progress = %s WHERE job_id = %s""",
                (Jsonb(progress.model_dump(mode="json")), row["job_id"]),
            )
            connection.execute(
                """UPDATE audit_runs SET status = 'running', updated_at = %s
                   WHERE audit_id = %s""",
                (timestamp, audit_id),
            )
            updated = connection.execute(
                "SELECT * FROM jobs WHERE job_id = %s", (row["job_id"],)
            ).fetchone()
            assert updated is not None
            return JobClaim(
                job=_decode_job(updated),
                worker_id=worker_id,
                claim_token=token,
                lease_until=lease_until,
                resource_kind=updated["resource_kind"],
                resource_id=updated["resource_id"],
            )

    def prepare_claimed_audit(
        self, claim: JobClaim, *, now: datetime | None = None,
    ) -> JobClaim:
        """Prepare an audit using an existing queue claim, retaining ownership.

        Generic queue claims update the job alone. Validate that ownership
        against the database before aligning the audit and recovering any
        unfinished sample left by a previous claimant.
        """
        if not isinstance(claim, JobClaim):
            raise TypeError("claim must be a JobClaim instance.")
        if claim.resource_kind != "audit" or claim.resource_id is None:
            raise ValueError("claim must belong to an audit job.")
        job_id = _require_identifier(claim.job.job_id, "job_id")
        with self._connection(write=True) as connection:
            row = connection.execute(
                """SELECT jobs.*, audit_runs.audit_id, audit_runs.status AS audit_status
                   FROM jobs JOIN audit_runs ON audit_runs.job_id = jobs.job_id
                   WHERE jobs.job_id = %s FOR UPDATE OF jobs, audit_runs""",
                (job_id,),
            ).fetchone()
            timestamp = _timestamp(now)
            status = self._assert_claim(row, claim, now=timestamp)
            if (
                row["kind"] != JobKind.AUDIT.value
                or row["resource_kind"] != "audit"
                or row["resource_id"] != row["audit_id"]
                or row["audit_id"] != claim.resource_id
                or row["claimed_by"] != claim.worker_id
            ):
                raise JobClaimLostError("The claim does not match this audit job.")
            audit_status = JobStatus(row["audit_status"])
            if audit_status not in (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.CANCELLING) or (
                audit_status is JobStatus.CANCELLING and status is JobStatus.RUNNING
            ):
                raise WorkflowStorageError("The audit state conflicts with the claimed job.")
            if status is JobStatus.RUNNING:
                self._record_expired_sample_timeouts(
                    connection, audit_id=row["audit_id"], now=now,
                    parent_lease_until=row["lease_until"],
                )

            # A queue takeover replaces the parent token before the audit is
            # dispatched. Its predecessor's uncommitted samples can be retried;
            # samples already running under this claim must remain untouched.
            connection.execute(
                """UPDATE audit_samples SET status = 'queued', claimed_by = NULL,
                        claim_token = NULL, lease_until = NULL, processing_deadline = NULL,
                        terminal_operation_id = NULL
                   WHERE audit_id = %s AND status = 'running'
                     AND claim_token IS DISTINCT FROM %s""",
                (row["audit_id"], claim.claim_token),
            )
            # Resetting an abandoned sample may itself wait for its row lock.
            timestamp = _timestamp(now)
            self._assert_claim(row, claim, now=timestamp)
            updated = self._apply_boundary(
                connection, audit_id=row["audit_id"], job_row=row, now=timestamp,
            )
            return JobClaim(
                job=_decode_job(updated),
                worker_id=row["claimed_by"],
                claim_token=row["claim_token"],
                lease_until=row["lease_until"],
                resource_kind=row["resource_kind"],
                resource_id=row["resource_id"],
            )

    @staticmethod
    def _progress(
        connection: Any, audit_id: str, phase: str,
    ) -> tuple[Progress, int, int, int]:
        counts = connection.execute(
            """SELECT
                   COUNT(*) FILTER (WHERE status IN ('completed', 'failed')) AS processed,
                   COUNT(*) FILTER (WHERE status = 'failed') AS failed,
                   COUNT(*) FILTER (WHERE incomplete) AS incomplete
               FROM audit_samples WHERE audit_id = %s""",
            (audit_id,),
        ).fetchone()
        audit = connection.execute(
            "SELECT sample_count FROM audit_runs WHERE audit_id = %s", (audit_id,)
        ).fetchone()
        assert counts is not None and audit is not None
        total = audit["sample_count"]
        processed = counts["processed"]
        fraction = 1.0 if total == 0 else processed / total
        return (
            Progress(phase=phase, processed=processed, total=total, fraction=fraction),
            processed,
            counts["failed"],
            counts["incomplete"],
        )

    @staticmethod
    def _assert_claim(
        row: Mapping[str, Any], claim: JobClaim, *, now: datetime,
    ) -> JobStatus:
        if (
            row is None
            or row["claim_token"] != claim.claim_token
            or row.get("claimed_by") != claim.worker_id
        ):
            raise JobClaimLostError("The worker no longer owns this job claim.")
        if row["lease_until"] is None or row["lease_until"] <= now:
            raise JobClaimLostError("The worker's job lease has expired.")
        status = JobStatus(row["status"])
        if status not in (JobStatus.RUNNING, JobStatus.CANCELLING):
            raise JobClaimLostError("The job is no longer processable by this worker.")
        return status

    @classmethod
    def _write_timeout_failure_locked(
        cls,
        connection: Any,
        *,
        audit_id: str,
        sample_id: str,
        sample_revision: int,
        failure_id: str,
        detail: str,
        now: datetime,
        operation_id: str | None,
        claim_token: str | None,
    ) -> None:
        connection.execute(
            """INSERT INTO audit_failures
               (failure_id, audit_id, sample_id, sample_revision, stage, reason, detail, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (audit_id, sample_id, sample_revision, stage, reason) DO NOTHING""",
            (
                failure_id, audit_id, sample_id, sample_revision,
                _TIMEOUT_STAGE, _TIMEOUT_REASON, detail, now,
            ),
        )
        stored_failure = connection.execute(
            """SELECT failure_id FROM audit_failures
               WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s
                 AND stage = %s AND reason = %s""",
            (audit_id, sample_id, sample_revision, _TIMEOUT_STAGE, _TIMEOUT_REASON),
        ).fetchone()
        assert stored_failure is not None
        token_clause = " AND claim_token = %s" if claim_token is not None else ""
        parameters: tuple[Any, ...] = (
            stored_failure["failure_id"], operation_id, now,
            audit_id, sample_id, sample_revision,
        ) + ((claim_token,) if claim_token is not None else ())
        result = connection.execute(
            f"""UPDATE audit_samples SET status = 'failed', failure_id = %s,
                       terminal_operation_id = %s, incomplete = FALSE,
                       claimed_by = NULL, claim_token = NULL, lease_until = NULL,
                       processing_deadline = NULL, finished_at = %s
                   WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s
                     AND status = 'running'{token_clause}""",
            parameters,
        )
        if result.rowcount != 1:
            raise JobClaimLostError("The worker no longer owns this sample claim.")

    @classmethod
    def _record_expired_sample_timeouts(
        cls,
        connection: Any,
        *,
        audit_id: str,
        now: datetime | None,
        parent_lease_until: datetime | None,
    ) -> None:
        if parent_lease_until is None:
            return
        samples = connection.execute(
            """SELECT sample_id, sample_revision, processing_deadline, lease_until
               FROM audit_samples
               WHERE audit_id = %s AND status = 'running'
               FOR UPDATE""",
            (audit_id,),
        ).fetchall()
        timestamp = _timestamp(now)
        samples = [
            sample for sample in samples
            if sample["processing_deadline"] is not None
            and sample["processing_deadline"] <= timestamp
            and parent_lease_until >= sample["processing_deadline"]
            and sample["lease_until"] is not None
            and sample["lease_until"] >= sample["processing_deadline"]
        ]
        for sample in samples:
            identity = "\x1f".join((
                audit_id, sample["sample_id"], str(sample["sample_revision"]),
                _TIMEOUT_REASON,
            ))
            failure_id = f"failure_{sha256(identity.encode('utf-8')).hexdigest()}"
            cls._write_timeout_failure_locked(
                connection,
                audit_id=audit_id,
                sample_id=sample["sample_id"],
                sample_revision=sample["sample_revision"],
                failure_id=failure_id,
                detail=_TIMEOUT_DETAIL,
                now=timestamp,
                operation_id=None,
                claim_token=None,
            )

    @classmethod
    def _apply_boundary(
        cls, connection: Any, *, audit_id: str, job_row: Mapping[str, Any], now: datetime,
    ) -> Mapping[str, Any]:
        status = JobStatus(job_row["status"])
        if status is JobStatus.CANCELLING:
            connection.execute(
                """UPDATE audit_samples SET status = 'cancelled', claimed_by = NULL,
                       claim_token = NULL, lease_until = NULL, processing_deadline = NULL,
                       finished_at = %s
                   WHERE audit_id = %s AND status = 'queued'""",
                (now, audit_id),
            )
        exhausted = [] if status is JobStatus.CANCELLING else connection.execute(
            """SELECT sample_id, sample_revision FROM audit_samples
               WHERE audit_id = %s AND status = 'queued' AND attempt_count >= 3
               FOR UPDATE""",
            (audit_id,),
        ).fetchall()
        for sample in exhausted:
            identity = "\x1f".join((
                audit_id, sample["sample_id"], str(sample["sample_revision"]),
                "retry_exhausted",
            ))
            failure_id = f"failure_{sha256(identity.encode('utf-8')).hexdigest()}"
            connection.execute(
                """INSERT INTO audit_failures
                   (failure_id, audit_id, sample_id, sample_revision, stage, reason, detail, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (audit_id, sample_id, sample_revision, stage, reason) DO NOTHING""",
                (
                    failure_id, audit_id, sample["sample_id"], sample["sample_revision"],
                    "sample_processing", "retry_exhausted",
                    "The sample exhausted its processing attempts.", now,
                ),
            )
            stored_failure = connection.execute(
                """SELECT failure_id FROM audit_failures
                   WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s
                     AND stage = 'sample_processing' AND reason = 'retry_exhausted'""",
                (audit_id, sample["sample_id"], sample["sample_revision"]),
            ).fetchone()
            assert stored_failure is not None
            connection.execute(
                """UPDATE audit_samples SET status = 'failed', failure_id = %s,
                       incomplete = FALSE, processing_deadline = NULL, finished_at = %s
                   WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s
                     AND status = 'queued'""",
                (
                    stored_failure["failure_id"], now, audit_id,
                    sample["sample_id"], sample["sample_revision"],
                ),
            )
        pending = connection.execute(
            """SELECT COUNT(*) AS pending FROM audit_samples
               WHERE audit_id = %s AND status IN ('queued', 'running')""",
            (audit_id,),
        ).fetchone()["pending"]
        if status is JobStatus.CANCELLING and pending == 0:
            new_status = JobStatus.CANCELLED
        elif status is JobStatus.RUNNING and pending == 0:
            new_status = JobStatus.COMPLETED
        else:
            new_status = status
        phase = (
            "completed" if new_status is JobStatus.COMPLETED else
            "cancelled" if new_status is JobStatus.CANCELLED else "processing"
        )
        progress, processed, failed, incomplete = cls._progress(connection, audit_id, phase)
        terminal = new_status in (JobStatus.COMPLETED, JobStatus.CANCELLED)
        result = Jsonb(_result_reference(audit_id).model_dump(mode="json")) if terminal else None
        connection.execute(
            """UPDATE jobs SET status = %s, progress = %s, result = %s, error = NULL,
                   claimed_by = CASE WHEN %s THEN NULL ELSE claimed_by END,
                   claim_token = CASE WHEN %s THEN NULL ELSE claim_token END,
                   lease_until = CASE WHEN %s THEN NULL ELSE lease_until END,
                   updated_at = %s
               WHERE job_id = %s""",
            (
                new_status.value, Jsonb(progress.model_dump(mode="json")), result,
                terminal, terminal, terminal, now, job_row["job_id"],
            ),
        )
        connection.execute(
            """UPDATE audit_runs SET status = %s, processed_count = %s, failed_count = %s,
                    incomplete_count = %s, updated_at = %s WHERE audit_id = %s""",
             (new_status.value, processed, failed, incomplete, now, audit_id),
        )
        updated = connection.execute(
            "SELECT * FROM jobs WHERE job_id = %s", (job_row["job_id"],)
        ).fetchone()
        assert updated is not None
        return updated

    def claim_next_sample(
        self,
        claim: JobClaim,
        *,
        lease_seconds: int = 60,
        processing_seconds: int = 900,
        now: datetime | None = None,
    ) -> AuditSampleClaim | None:
        lease_seconds = _require_positive_integer(lease_seconds, "lease_seconds")
        processing_seconds = _require_positive_integer(processing_seconds, "processing_seconds")
        if claim.resource_kind != "audit" or claim.resource_id is None:
            raise ValueError("claim must belong to an audit job.")
        audit_id = claim.resource_id
        with self._connection(write=True) as connection:
            job_row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = %s FOR UPDATE", (claim.job.job_id,)
            ).fetchone()
            active = connection.execute(
                """SELECT * FROM audit_samples
                   WHERE audit_id = %s AND status = 'running'
                     AND claim_token = %s AND claimed_by = %s
                   FOR UPDATE""",
                (audit_id, claim.claim_token, claim.worker_id),
            ).fetchone()
            sample = connection.execute(
                """SELECT * FROM audit_samples
                   WHERE audit_id = %s AND status = 'queued' AND attempt_count < 3
                   ORDER BY ordinal ASC FOR UPDATE SKIP LOCKED LIMIT 1""",
                (audit_id,),
            ).fetchone()
            audit_row = connection.execute(
                "SELECT source_mode FROM audit_runs WHERE audit_id = %s", (audit_id,),
            ).fetchone()
            if audit_row is None:
                raise AuditNotFoundError(audit_id)
            source_mode = AuditSourceMode(audit_row["source_mode"])
            timestamp = _timestamp(now)
            status = self._assert_claim(job_row, claim, now=timestamp)
            if status is JobStatus.CANCELLING:
                self._apply_boundary(connection, audit_id=audit_id, job_row=job_row, now=timestamp)
                return None
            if active is not None:
                return AuditSampleClaim(
                    audit_id=audit_id,
                    job_id=claim.job.job_id,
                    sample_id=active["sample_id"],
                    sample_revision=active["sample_revision"],
                    source_mode=source_mode,
                    manifest_dataset_id=active["manifest_dataset_id"],
                    worker_id=claim.worker_id,
                    claim_token=claim.claim_token,
                    lease_until=active["lease_until"],
                    processing_deadline=active["processing_deadline"],
                    attempt_count=active["attempt_count"],
                )
            if sample is None:
                return None
            processing_deadline = timestamp + timedelta(seconds=processing_seconds)
            parent_lease_until = job_row["lease_until"]
            if parent_lease_until is None or parent_lease_until <= timestamp:
                raise JobClaimLostError("The worker's job lease has expired.")
            lease_until = min(
                timestamp + timedelta(seconds=lease_seconds),
                parent_lease_until,
                processing_deadline,
            )
            attempt_count = sample["attempt_count"] + 1
            connection.execute(
                """UPDATE audit_samples SET status = 'running', claimed_by = %s,
                        claim_token = %s, lease_until = %s, processing_deadline = %s,
                        attempt_count = %s, terminal_operation_id = NULL,
                        started_at = COALESCE(started_at, %s)
                    WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s""",
                (
                    claim.worker_id, claim.claim_token, lease_until, processing_deadline,
                    attempt_count, timestamp,
                    audit_id, sample["sample_id"], sample["sample_revision"],
                ),
            )
            return AuditSampleClaim(
                audit_id=audit_id,
                job_id=claim.job.job_id,
                sample_id=sample["sample_id"],
                sample_revision=sample["sample_revision"],
                source_mode=source_mode,
                manifest_dataset_id=sample["manifest_dataset_id"],
                worker_id=claim.worker_id,
                claim_token=claim.claim_token,
                lease_until=lease_until,
                processing_deadline=processing_deadline,
                attempt_count=attempt_count,
            )

    def renew_sample_claim(
        self,
        claim: AuditSampleClaim,
        *,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> Job:
        """Renew the parent job and active sample lease atomically."""
        claim = self._snapshot_sample_claim(claim)
        lease_seconds = _require_positive_integer(lease_seconds, "lease_seconds")
        with self._connection(write=True) as connection:
            job_row, timestamp = self._lock_sample_claim(connection, claim, now=now)
            if JobStatus(job_row["status"]) is JobStatus.CANCELLING:
                raise JobClaimLostError("The job is cancelling and will not renew its lease.")
            sample_row = connection.execute(
                """SELECT processing_deadline FROM audit_samples
                   WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s""",
                (claim.audit_id, claim.sample_id, claim.sample_revision),
            ).fetchone()
            deadline = sample_row["processing_deadline"] if sample_row is not None else None
            if deadline is None:
                raise WorkflowStorageError("A running sample has no processing deadline.")
            if deadline <= timestamp:
                raise SampleProcessingTimeoutError("The sample processing deadline has expired.")
            lease_until = min(timestamp + timedelta(seconds=lease_seconds), deadline)
            connection.execute(
                """UPDATE jobs SET lease_until = %s, updated_at = %s
                   WHERE job_id = %s AND claim_token = %s""",
                (lease_until, timestamp, claim.job_id, claim.claim_token),
            )
            connection.execute(
                """UPDATE audit_samples SET lease_until = %s
                   WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s
                     AND claim_token = %s""",
                (
                    lease_until, claim.audit_id, claim.sample_id, claim.sample_revision,
                    claim.claim_token,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM jobs WHERE job_id = %s", (claim.job_id,)
            ).fetchone()
            assert updated is not None
            return _decode_job(updated)

    def checkpoint_sample_claim(
        self,
        claim: AuditSampleClaim,
        *,
        renew: bool = False,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> JobStatus:
        """Validate ownership, cancellation and deadline at a safe boundary."""
        claim = self._snapshot_sample_claim(claim)
        lease_seconds = _require_positive_integer(lease_seconds, "lease_seconds")
        with self._connection(write=True) as connection:
            job_row, timestamp = self._lock_sample_claim(
                connection, claim, now=now, allow_cancelling=True,
            )
            status = JobStatus(job_row["status"])
            if status is JobStatus.CANCELLING:
                raise JobCancellationError("Cancellation won at a sample checkpoint.")
            if renew:
                sample_row = connection.execute(
                    """SELECT processing_deadline FROM audit_samples
                       WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s""",
                    (claim.audit_id, claim.sample_id, claim.sample_revision),
                ).fetchone()
                deadline = sample_row["processing_deadline"] if sample_row is not None else None
                if deadline is None:
                    raise WorkflowStorageError("A running sample has no processing deadline.")
                lease_until = min(timestamp + timedelta(seconds=lease_seconds), deadline)
                connection.execute(
                    """UPDATE jobs SET lease_until = %s, updated_at = %s
                       WHERE job_id = %s AND claim_token = %s""",
                    (lease_until, timestamp, claim.job_id, claim.claim_token),
                )
                connection.execute(
                    """UPDATE audit_samples SET lease_until = %s
                       WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s
                         AND claim_token = %s""",
                    (
                        lease_until, claim.audit_id, claim.sample_id, claim.sample_revision,
                        claim.claim_token,
                    ),
                )
            return status

    def increment_sample_attempt(
        self,
        claim: AuditSampleClaim,
        *,
        operation_id: str,
        now: datetime | None = None,
    ) -> int:
        """Durably consume one attempt, idempotently replaying one operation."""
        claim = self._snapshot_sample_claim(claim)
        operation_id = _require_identifier(operation_id, "operation_id")
        with self._connection(write=True) as connection:
            self._lock_sample_claim(connection, claim, now=now)
            row = connection.execute(
                """SELECT attempt_count, last_attempt_operation_id FROM audit_samples
                   WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s
                   FOR UPDATE""",
                (claim.audit_id, claim.sample_id, claim.sample_revision),
            ).fetchone()
            assert row is not None
            if row["last_attempt_operation_id"] == operation_id:
                return row["attempt_count"]
            if row["attempt_count"] >= 3:
                return row["attempt_count"]
            updated = row["attempt_count"] + 1
            connection.execute(
                """UPDATE audit_samples SET attempt_count = %s, last_attempt_operation_id = %s
                   WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s
                   AND claim_token = %s""",
                (
                    updated, operation_id, claim.audit_id, claim.sample_id, claim.sample_revision,
                    claim.claim_token,
                ),
            )
            return updated

    def cancel_sample_claim(
        self, claim: AuditSampleClaim, *, now: datetime | None = None,
    ) -> Job:
        """Commit cancellation for a running sample at a safe boundary."""
        claim = self._snapshot_sample_claim(claim)
        operation_id = self._sample_operation_id(
            None, kind="sample_cancel", claim=claim,
        )
        with self._connection(write=True) as connection:
            try:
                job_row, timestamp = self._lock_sample_claim(
                    connection, claim, now=now, allow_cancelling=True,
                )
            except JobClaimLostError:
                reconciled = self._reconcile_sample_cancellation(
                    connection, claim, operation_id=operation_id,
                )
                if reconciled is None:
                    raise
                return _decode_job(reconciled)
            if JobStatus(job_row["status"]) is not JobStatus.CANCELLING:
                raise JobClaimLostError("The job is no longer cancelling.")
            connection.execute(
                """UPDATE audit_samples SET status = 'cancelled',
                       terminal_operation_id = %s, claimed_by = NULL,
                       claim_token = NULL, lease_until = NULL, processing_deadline = NULL,
                       finished_at = %s
                   WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s
                     AND claim_token = %s""",
                (
                    operation_id, timestamp, claim.audit_id,
                    claim.sample_id, claim.sample_revision,
                    claim.claim_token,
                ),
            )
            updated = self._apply_boundary(
                connection, audit_id=claim.audit_id, job_row=job_row, now=timestamp,
            )
            return _decode_job(updated)

    def record_sample_timeout(
        self, claim: AuditSampleClaim, failure: AuditFailure, *, now: datetime | None = None,
    ) -> Job:
        """Finalize a timed-out sample through an explicit recovery write."""
        claim = self._snapshot_sample_claim(claim)
        if not isinstance(failure, AuditFailure):
            raise TypeError("failure must be an AuditFailure instance.")
        failure = AuditFailure.model_validate(failure.model_dump())
        if (
            failure.audit_id != claim.audit_id
            or failure.sample_id != claim.sample_id
            or failure.sample_revision != claim.sample_revision
        ):
            raise ValueError("failure must belong to the claimed audit sample.")
        if failure.stage != "sample_processing" or failure.reason != "processing_timeout":
            raise ValueError("timeout finalization requires a processing_timeout failure.")
        operation_id = self._sample_operation_id(
            None, kind="sample_timeout", claim=claim,
        )
        with self._connection(write=True) as connection:
            try:
                job_row, timestamp = self._lock_sample_claim(
                    connection,
                    claim,
                    now=now,
                    allow_cancelling=True,
                    allow_deadline_expired=True,
                )
            except JobClaimLostError:
                reconciled = self._reconcile_sample_failure(
                    connection, claim, failure=failure, measurements=None,
                    operation_id=operation_id,
                )
                if reconciled is None:
                    raise
                return _decode_job(reconciled)
            if JobStatus(job_row["status"]) is JobStatus.CANCELLING:
                raise JobCancellationError("Cancellation won before timeout finalization.")
            sample = connection.execute(
                """SELECT processing_deadline FROM audit_samples
                   WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s""",
                (claim.audit_id, claim.sample_id, claim.sample_revision),
            ).fetchone()
            if sample is None or sample["processing_deadline"] is None or sample["processing_deadline"] > timestamp:
                raise ValueError("The sample processing deadline has not expired.")
            self._write_timeout_failure_locked(
                connection,
                audit_id=failure.audit_id,
                sample_id=failure.sample_id,
                sample_revision=failure.sample_revision,
                failure_id=failure.failure_id,
                detail=failure.detail,
                now=timestamp,
                operation_id=operation_id,
                claim_token=claim.claim_token,
            )
            updated = self._apply_boundary(
                connection, audit_id=claim.audit_id, job_row=job_row, now=timestamp,
            )
            return _decode_job(updated)

    @classmethod
    def _lock_sample_claim(
        cls,
        connection: Any,
        claim: AuditSampleClaim,
        *,
        now: datetime | None,
        allow_cancelling: bool = False,
        allow_deadline_expired: bool = False,
    ) -> tuple[Mapping[str, Any], datetime]:
        """Validate both leases after locking the job and sample, in that order."""
        job_row = connection.execute(
            "SELECT * FROM jobs WHERE job_id = %s FOR UPDATE", (claim.job_id,),
        ).fetchone()
        if job_row is None:
            raise JobClaimLostError("The worker's job no longer exists.")
        sample = connection.execute(
            """SELECT * FROM audit_samples
               WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s
               FOR UPDATE""",
            (claim.audit_id, claim.sample_id, claim.sample_revision),
        ).fetchone()
        audit = connection.execute(
            "SELECT source_mode FROM audit_runs WHERE audit_id = %s", (claim.audit_id,),
        ).fetchone()
        if audit is None:
            raise JobClaimLostError("The worker's audit no longer exists.")
        if (
            sample is None
            or AuditSourceMode(audit["source_mode"]) is not claim.source_mode
            or sample.get("manifest_dataset_id") != claim.manifest_dataset_id
        ):
            raise JobClaimLostError("The sample source identity no longer matches the claim.")
        timestamp = _timestamp(now)
        status = cls._assert_claim_identity(job_row, JobClaim(
            job=_decode_job(job_row), worker_id=claim.worker_id,
            claim_token=claim.claim_token, lease_until=claim.lease_until,
            resource_kind="audit", resource_id=claim.audit_id,
        ))
        if status is JobStatus.CANCELLING:
            if not allow_cancelling:
                raise JobCancellationError("Cancellation won at a sample boundary.")
            deadline = sample["processing_deadline"] if sample is not None else None
            deadline_controls_expiry = (
                deadline is not None
                and deadline <= timestamp
                and job_row["lease_until"] is not None
                and job_row["lease_until"] >= deadline
                and sample["lease_until"] == deadline
            )
            if (
                job_row["lease_until"] is None
                or job_row["lease_until"] <= timestamp
            ) and not deadline_controls_expiry:
                raise JobClaimLostError("The worker's job lease has expired.")
            if (
                sample is None
                or sample["status"] != "running"
                or sample["claim_token"] != claim.claim_token
                or sample.get("claimed_by") != claim.worker_id
            ):
                raise JobClaimLostError("The worker no longer owns this sample claim.")
            if (
                sample["lease_until"] is None
                or sample["lease_until"] <= timestamp
            ) and not deadline_controls_expiry:
                raise JobClaimLostError("The worker's sample lease has expired.")
            return job_row, timestamp
        if status is not JobStatus.RUNNING:
            raise JobClaimLostError("The job is no longer processable by this worker.")
        deadline = sample["processing_deadline"] if sample is not None else None
        deadline_controls_expiry = (
            deadline is not None
            and deadline <= timestamp
            and job_row["lease_until"] is not None
            and job_row["lease_until"] >= deadline
            and sample["lease_until"] == deadline
        )
        if job_row["lease_until"] is None or job_row["lease_until"] <= timestamp:
            if not deadline_controls_expiry:
                raise JobClaimLostError("The worker's job lease has expired.")
        if (
            sample is None
            or sample["status"] != "running"
            or sample["claim_token"] != claim.claim_token
            or sample.get("claimed_by") != claim.worker_id
        ):
            raise JobClaimLostError("The worker no longer owns this sample claim.")
        if sample["lease_until"] is None or sample["lease_until"] <= timestamp:
            if not deadline_controls_expiry:
                raise JobClaimLostError("The worker's sample lease has expired.")
        if deadline is None:
            raise WorkflowStorageError("A running sample has no processing deadline.")
        if deadline <= timestamp and not allow_deadline_expired:
            raise SampleProcessingTimeoutError("The sample processing deadline has expired.")
        return job_row, timestamp

    @staticmethod
    def _assert_claim_identity(row: Mapping[str, Any], claim: JobClaim) -> JobStatus:
        if (
            row is None
            or row["claim_token"] != claim.claim_token
            or row.get("claimed_by") != claim.worker_id
            or row.get("kind") != JobKind.AUDIT.value
            or row.get("resource_kind") != "audit"
            or row.get("resource_id") != claim.resource_id
        ):
            raise JobClaimLostError("The worker no longer owns this audit claim.")
        status = JobStatus(row["status"])
        if status not in (JobStatus.RUNNING, JobStatus.CANCELLING):
            raise JobClaimLostError("The job is no longer processable by this worker.")
        return status

    @classmethod
    def _recheck_sample_claim(
        cls, connection: Any, claim: AuditSampleClaim, *, now: datetime,
    ) -> None:
        """Recheck ownership immediately before a terminal sample write."""
        cls._lock_sample_claim(connection, claim, now=now)

    @staticmethod
    def _snapshot_sample_claim(claim: AuditSampleClaim) -> AuditSampleClaim:
        if not isinstance(claim, AuditSampleClaim):
            raise TypeError("claim must be an AuditSampleClaim instance.")
        return AuditSampleClaim.model_validate(claim.model_dump())

    @staticmethod
    def _snapshot_findings(
        findings: Sequence[Finding], *, audit_id: str, sample_id: str, sample_revision: int,
    ) -> list[Finding]:
        snapshots = FindingRepository._snapshot_findings(findings)
        for finding in snapshots:
            if (
                finding.audit_id != audit_id
                or finding.sample_id != sample_id
                or finding.sample_revision != sample_revision
            ):
                raise ValueError("all findings must belong to the claimed audit sample.")
        return snapshots

    @staticmethod
    def _insert_or_verify_measurements(
        connection: Any, *, audit_id: str, sample_id: str, sample_revision: int,
        document: dict[str, Any], timestamp: datetime,
    ) -> None:
        document = _validate_measurement_document(document, sample_id)
        connection.execute(
            """INSERT INTO audit_measurements
               (audit_id, sample_id, sample_revision, document, created_at)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (audit_id, sample_id, sample_revision) DO NOTHING""",
            (audit_id, sample_id, sample_revision, Jsonb(document), timestamp),
        )
        stored = connection.execute(
            """SELECT document FROM audit_measurements
               WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s""",
            (audit_id, sample_id, sample_revision),
        ).fetchone()
        assert stored is not None
        if stored["document"] != document:
            raise WorkflowStorageError("A sample already has different measurement evidence.")

    @staticmethod
    def _insert_or_verify_findings(
        connection: Any, findings: Sequence[Finding], timestamp: datetime,
    ) -> None:
        """Pipeline bounded inserts and verify their immutable evidence together."""
        with connection.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO findings
               (finding_id, audit_id, sample_id, sample_revision, object_id,
                rulebook_id, rulebook_version, rule_id, outcome, review_resolution,
                document, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (finding_id) DO NOTHING""",
                [(
                    finding.finding_id, finding.audit_id, finding.sample_id,
                    finding.sample_revision, finding.object_id, finding.rulebook_id,
                    finding.rulebook_version, finding.rule_id, finding.outcome.value,
                    finding.review.resolution.value,
                    Jsonb(finding.model_dump(mode="json")), timestamp,
                ) for finding in findings],
            )
        rows = connection.execute(
            "SELECT * FROM findings WHERE finding_id = ANY(%s)",
            ([finding.finding_id for finding in findings],),
        ).fetchall()
        stored = {row["finding_id"]: row for row in rows}
        for finding in findings:
            row = stored.get(finding.finding_id)
            if row is None or _decode_finding(row) != finding:
                raise WorkflowStorageError(
                    f"Finding {finding.finding_id!r} could not be saved with the expected evidence."
                )

    @staticmethod
    def _sample_operation_id(
        operation_id: str | None, *, kind: str, claim: AuditSampleClaim,
    ) -> str:
        return _require_identifier(
            operation_id or f"{kind}\x1f{claim.claim_token}", "operation_id",
        )

    @classmethod
    def _reconcile_sample_success(
        cls,
        connection: Any,
        claim: AuditSampleClaim,
        *,
        document: Mapping[str, Any],
        findings: Sequence[Finding],
        incomplete: bool,
        operation_id: str,
    ) -> Mapping[str, Any] | None:
        job = connection.execute(
            "SELECT * FROM jobs WHERE job_id = %s FOR UPDATE", (claim.job_id,)
        ).fetchone()
        sample = connection.execute(
            """SELECT * FROM audit_samples
               WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s
               FOR UPDATE""",
            (claim.audit_id, claim.sample_id, claim.sample_revision),
        ).fetchone()
        if job is None or sample is None or sample["status"] != "completed":
            return None
        if sample["terminal_operation_id"] != operation_id:
            return None
        if sample["incomplete"] is not incomplete:
            raise WorkflowStorageError("The committed sample completeness conflicts with this operation.")
        stored_measurement = connection.execute(
            """SELECT document FROM audit_measurements
               WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s""",
            (claim.audit_id, claim.sample_id, claim.sample_revision),
        ).fetchone()
        if stored_measurement is None or stored_measurement["document"] != dict(document):
            raise WorkflowStorageError("The committed sample measurement conflicts with this operation.")
        stored_findings = connection.execute(
            """SELECT document FROM findings
               WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s""",
            (claim.audit_id, claim.sample_id, claim.sample_revision),
        ).fetchall()
        expected = {finding.finding_id: finding.model_dump(mode="json") for finding in findings}
        actual = {
            row["document"].get("finding_id"): row["document"] for row in stored_findings
        }
        if actual != expected:
            raise WorkflowStorageError("The committed findings conflict with this operation.")
        return job

    @classmethod
    def _reconcile_sample_failure(
        cls,
        connection: Any,
        claim: AuditSampleClaim,
        *,
        failure: AuditFailure,
        measurements: Mapping[str, Any] | None,
        operation_id: str,
    ) -> Mapping[str, Any] | None:
        job = connection.execute(
            "SELECT * FROM jobs WHERE job_id = %s FOR UPDATE", (claim.job_id,)
        ).fetchone()
        sample = connection.execute(
            """SELECT * FROM audit_samples
               WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s
               FOR UPDATE""",
            (claim.audit_id, claim.sample_id, claim.sample_revision),
        ).fetchone()
        if (
            job is None
            or sample is None
            or sample["status"] != "failed"
            or sample["failure_id"] != failure.failure_id
        ):
            return None
        if sample["terminal_operation_id"] != operation_id:
            return None
        stored_failure = connection.execute(
            """SELECT failure_id, stage, reason, detail FROM audit_failures
               WHERE failure_id = %s""",
            (failure.failure_id,),
        ).fetchone()
        if stored_failure is None or any(
            stored_failure[field] != getattr(failure, field)
            for field in ("failure_id", "stage", "reason", "detail")
        ):
            raise WorkflowStorageError("The committed sample failure conflicts with this operation.")
        if measurements is not None:
            stored_measurement = connection.execute(
                """SELECT document FROM audit_measurements
                   WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s""",
                (claim.audit_id, claim.sample_id, claim.sample_revision),
            ).fetchone()
            if stored_measurement is None or stored_measurement["document"] != dict(measurements):
                raise WorkflowStorageError("The committed failure measurement conflicts with this operation.")
        return job

    @staticmethod
    def _reconcile_sample_cancellation(
        connection: Any, claim: AuditSampleClaim, *, operation_id: str,
    ) -> Mapping[str, Any] | None:
        job = connection.execute(
            "SELECT * FROM jobs WHERE job_id = %s FOR UPDATE", (claim.job_id,)
        ).fetchone()
        sample = connection.execute(
            """SELECT terminal_operation_id, status FROM audit_samples
               WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s
               FOR UPDATE""",
            (claim.audit_id, claim.sample_id, claim.sample_revision),
        ).fetchone()
        if (
            job is None
            or sample is None
            or sample["status"] != "cancelled"
            or sample["terminal_operation_id"] != operation_id
        ):
            return None
        return job

    def persist_sample_success(
        self,
        claim: AuditSampleClaim,
        *,
        measurements: Mapping[str, Any],
        findings: Sequence[Finding],
        incomplete: bool = False,
        operation_id: str | None = None,
        now: datetime | None = None,
    ) -> Job:
        claim = self._snapshot_sample_claim(claim)
        document = _json_document(deepcopy(dict(measurements)))
        finding_snapshots = self._snapshot_findings(
            findings,
            audit_id=claim.audit_id,
            sample_id=claim.sample_id,
            sample_revision=claim.sample_revision,
        )
        if type(incomplete) is not bool:
            raise TypeError("incomplete must be a boolean.")
        if document.get("status") == "incomplete":
            incomplete = True
        operation_id = self._sample_operation_id(
            operation_id, kind="sample_success", claim=claim,
        )
        with self._connection(write=True) as connection:
            try:
                job_row, timestamp = self._lock_sample_claim(connection, claim, now=now)
            except JobClaimLostError:
                reconciled = self._reconcile_sample_success(
                    connection, claim, document=document, findings=finding_snapshots,
                    incomplete=incomplete, operation_id=operation_id,
                )
                if reconciled is None:
                    raise
                return _decode_job(reconciled)
            audit = connection.execute(
                "SELECT rulebook_id, rulebook_version FROM audit_runs WHERE audit_id = %s",
                (claim.audit_id,),
            ).fetchone()
            assert audit is not None
            if any(
                finding.rulebook_id != audit["rulebook_id"]
                or finding.rulebook_version != audit["rulebook_version"]
                for finding in finding_snapshots
            ):
                raise ValueError("findings must use the audit's pinned rulebook version.")
            write_timestamp = timestamp if now is not None else _timestamp()
            self._recheck_sample_claim(connection, claim, now=write_timestamp)
            self._insert_or_verify_measurements(
                connection,
                audit_id=claim.audit_id,
                sample_id=claim.sample_id,
                sample_revision=claim.sample_revision,
                document=document,
                timestamp=write_timestamp,
            )
            for offset in range(0, len(finding_snapshots), _FINDING_WRITE_BATCH_SIZE):
                batch = finding_snapshots[offset:offset + _FINDING_WRITE_BATCH_SIZE]
                write_timestamp = timestamp if now is not None else _timestamp()
                self._recheck_sample_claim(connection, claim, now=write_timestamp)
                self._insert_or_verify_findings(connection, batch, write_timestamp)
            # The next batch's entry check, or this final check, detects expiry
            # during an insert and rolls back all chunks. Row locks prevent
            # ownership replacement while this transaction is active.
            write_timestamp = timestamp if now is not None else _timestamp()
            self._recheck_sample_claim(connection, claim, now=write_timestamp)
            result = connection.execute(
                """UPDATE audit_samples SET status = 'completed', incomplete = %s,
                       terminal_operation_id = %s,
                       claimed_by = NULL, claim_token = NULL, lease_until = NULL,
                       processing_deadline = NULL, finished_at = %s
                   WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s
                     AND status = 'running' AND claim_token = %s""",
                (
                    incomplete, operation_id, write_timestamp,
                    claim.audit_id, claim.sample_id, claim.sample_revision,
                    claim.claim_token,
                ),
            )
            if result.rowcount != 1:
                raise JobClaimLostError("The worker no longer owns this sample claim.")
            updated = self._apply_boundary(
                connection, audit_id=claim.audit_id, job_row=job_row, now=write_timestamp,
            )
            return _decode_job(updated)

    def persist_sample_failure(
        self,
        claim: AuditSampleClaim,
        failure: AuditFailure,
        *,
        measurements: Mapping[str, Any] | None = None,
        operation_id: str | None = None,
        now: datetime | None = None,
    ) -> Job:
        claim = self._snapshot_sample_claim(claim)
        if not isinstance(failure, AuditFailure):
            raise TypeError("failure must be an AuditFailure instance.")
        failure = AuditFailure.model_validate(failure.model_dump())
        if (
            failure.audit_id != claim.audit_id
            or failure.sample_id != claim.sample_id
            or failure.sample_revision != claim.sample_revision
        ):
            raise ValueError("failure must belong to the claimed audit sample.")
        measurement_document = (
            None if measurements is None else _json_document(deepcopy(dict(measurements)))
        )
        operation_id = self._sample_operation_id(
            operation_id, kind="sample_failure", claim=claim,
        )
        with self._connection(write=True) as connection:
            try:
                job_row, timestamp = self._lock_sample_claim(connection, claim, now=now)
            except JobClaimLostError:
                reconciled = self._reconcile_sample_failure(
                    connection, claim, failure=failure,
                    measurements=measurement_document, operation_id=operation_id,
                )
                if reconciled is None:
                    raise
                return _decode_job(reconciled)
            if measurement_document is not None:
                write_timestamp = timestamp if now is not None else _timestamp()
                self._recheck_sample_claim(connection, claim, now=write_timestamp)
                self._insert_or_verify_measurements(
                    connection,
                    audit_id=claim.audit_id,
                    sample_id=claim.sample_id,
                    sample_revision=claim.sample_revision,
                    document=measurement_document,
                    timestamp=write_timestamp,
                )
            write_timestamp = timestamp if now is not None else _timestamp()
            self._recheck_sample_claim(connection, claim, now=write_timestamp)
            connection.execute(
                """INSERT INTO audit_failures
                   (failure_id, audit_id, sample_id, sample_revision, stage, reason, detail, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (audit_id, sample_id, sample_revision, stage, reason) DO NOTHING""",
                (
                    failure.failure_id, failure.audit_id, failure.sample_id,
                    failure.sample_revision, failure.stage, failure.reason,
                    failure.detail, write_timestamp,
                ),
            )
            stored_failure = connection.execute(
                """SELECT failure_id FROM audit_failures
                   WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s
                     AND stage = %s AND reason = %s""",
                (
                    failure.audit_id, failure.sample_id, failure.sample_revision,
                    failure.stage, failure.reason,
                ),
            ).fetchone()
            assert stored_failure is not None
            result = connection.execute(
                """UPDATE audit_samples SET status = 'failed', failure_id = %s,
                       terminal_operation_id = %s,
                       incomplete = FALSE, claimed_by = NULL, claim_token = NULL,
                       lease_until = NULL, processing_deadline = NULL, finished_at = %s
                   WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s
                     AND status = 'running' AND claim_token = %s""",
                (
                    stored_failure["failure_id"], operation_id, write_timestamp,
                    claim.audit_id,
                    claim.sample_id, claim.sample_revision, claim.claim_token,
                ),
            )
            if result.rowcount != 1:
                raise JobClaimLostError("The worker no longer owns this sample claim.")
            updated = self._apply_boundary(
                connection, audit_id=claim.audit_id, job_row=job_row, now=write_timestamp,
            )
            return _decode_job(updated)

    def finalize_audit(self, claim: JobClaim, *, now: datetime | None = None) -> Job:
        if claim.resource_kind != "audit" or claim.resource_id is None:
            raise ValueError("claim must belong to an audit job.")
        with self._connection(write=True) as connection:
            job_row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = %s FOR UPDATE", (claim.job.job_id,)
            ).fetchone()
            timestamp = _timestamp(now)
            self._assert_claim(job_row, claim, now=timestamp)
            updated = self._apply_boundary(
                connection, audit_id=claim.resource_id, job_row=job_row, now=timestamp,
            )
            return _decode_job(updated)

    def fail_audit(
        self, claim: JobClaim, error: ApiError, *, now: datetime | None = None,
    ) -> Job:
        """Persist a fatal worker error while keeping job and audit states aligned."""
        if claim.resource_kind != "audit" or claim.resource_id is None:
            raise ValueError("claim must belong to an audit job.")
        error = ApiError.model_validate(error.model_dump())
        with self._connection(write=True) as connection:
            job_row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = %s FOR UPDATE", (claim.job.job_id,)
            ).fetchone()
            running_sample = connection.execute(
                """SELECT sample_id, sample_revision, claim_token, lease_until
                   FROM audit_samples
                   WHERE audit_id = %s AND status = 'running'
                   FOR UPDATE""",
                (claim.resource_id,),
            ).fetchone()
            timestamp = _timestamp(now)
            if job_row is None or job_row["claim_token"] != claim.claim_token:
                raise JobClaimLostError("The worker no longer owns this job claim.")
            if job_row["lease_until"] is None or job_row["lease_until"] <= timestamp:
                raise JobClaimLostError("The worker's job lease has expired.")
            status = JobStatus(job_row["status"])
            if status is JobStatus.CANCELLING:
                raise JobCancellationError("Cancellation won before failure finalization.")
            if status is not JobStatus.RUNNING:
                raise JobClaimLostError("A terminal job cannot fail again.")

            if running_sample is not None:
                if (
                    running_sample["claim_token"] != claim.claim_token
                    or running_sample["lease_until"] is None
                    or running_sample["lease_until"] <= timestamp
                ):
                    raise JobClaimLostError("The worker's sample lease has expired or was replaced.")
                identity = "\x1f".join((
                    claim.resource_id, running_sample["sample_id"],
                    str(running_sample["sample_revision"]), "audit_worker_failed",
                ))
                failure_id = f"failure_{sha256(identity.encode('utf-8')).hexdigest()}"
                connection.execute(
                    """INSERT INTO audit_failures
                       (failure_id, audit_id, sample_id, sample_revision, stage, reason, detail, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (audit_id, sample_id, sample_revision, stage, reason) DO NOTHING""",
                    (
                        failure_id, claim.resource_id, running_sample["sample_id"],
                        running_sample["sample_revision"], "audit_worker", "audit_worker_failed",
                        error.message, timestamp,
                    ),
                )
                stored_failure = connection.execute(
                    """SELECT failure_id FROM audit_failures
                       WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s
                         AND stage = 'audit_worker' AND reason = 'audit_worker_failed'""",
                    (claim.resource_id, running_sample["sample_id"], running_sample["sample_revision"]),
                ).fetchone()
                assert stored_failure is not None
                connection.execute(
                    """UPDATE audit_samples SET status = 'failed', failure_id = %s,
                           incomplete = FALSE, claimed_by = NULL, claim_token = NULL,
                           lease_until = NULL, processing_deadline = NULL, finished_at = %s
                       WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s""",
                    (
                        stored_failure["failure_id"], timestamp, claim.resource_id,
                        running_sample["sample_id"], running_sample["sample_revision"],
                    ),
                )

            # A terminal failed job must not leave work that appears queued
            # forever.  Completed samples remain untouched; unstarted samples
            # are explicitly cancelled and remain visible in the audit history.
            connection.execute(
                """UPDATE audit_samples SET status = 'cancelled', processing_deadline = NULL,
                       finished_at = %s
                   WHERE audit_id = %s AND status = 'queued'""",
                (timestamp, claim.resource_id),
            )

            progress, processed, failed, incomplete = self._progress(
                connection, claim.resource_id, "failed",
            )
            connection.execute(
                """UPDATE jobs SET status = 'failed', progress = %s, result = NULL,
                       error = %s, claimed_by = NULL, claim_token = NULL, lease_until = NULL,
                       updated_at = %s WHERE job_id = %s AND claim_token = %s""",
                (
                    Jsonb(progress.model_dump(mode="json")),
                    Jsonb(error.model_dump(mode="json")), timestamp,
                    claim.job.job_id, claim.claim_token,
                ),
            )
            connection.execute(
                """UPDATE audit_runs SET status = 'failed', processed_count = %s,
                       failed_count = %s, incomplete_count = %s, updated_at = %s
                    WHERE audit_id = %s""",
                (processed, failed, incomplete, timestamp, claim.resource_id),
            )
            updated = connection.execute(
                "SELECT * FROM jobs WHERE job_id = %s", (claim.job.job_id,)
            ).fetchone()
            assert updated is not None
            return _decode_job(updated)

    def request_cancellation(self, audit_id: str, *, now: datetime | None = None) -> Job:
        audit_id = _require_identifier(audit_id, "audit_id")
        with self._connection(write=True) as connection:
            row = connection.execute(
                """SELECT jobs.* FROM jobs JOIN audit_runs ON audit_runs.job_id = jobs.job_id
                   WHERE audit_runs.audit_id = %s FOR UPDATE""",
                (audit_id,),
            ).fetchone()
            if row is None:
                raise AuditNotFoundError(audit_id)
            timestamp = _timestamp(now)
            status = JobStatus(row["status"])
            if status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                raise JobAlreadyFinishedError(row["job_id"], status)
            if status is JobStatus.QUEUED:
                connection.execute(
                    """UPDATE audit_samples SET status = 'cancelled', processing_deadline = NULL,
                           finished_at = %s
                       WHERE audit_id = %s AND status = 'queued'""",
                    (timestamp, audit_id),
                )
                # The queued job has no active claimant, so it can become
                # terminal immediately and still retain a readable audit link.
                progress, processed, failed, incomplete = self._progress(
                    connection, audit_id, "cancelled",
                )
                connection.execute(
                    """UPDATE jobs SET status = 'cancelled', progress = %s, result = %s,
                           updated_at = %s WHERE job_id = %s""",
                    (
                        Jsonb(progress.model_dump(mode="json")),
                        Jsonb(_result_reference(audit_id).model_dump(mode="json")),
                        timestamp, row["job_id"],
                    ),
                )
                connection.execute(
                    """UPDATE audit_runs SET status = 'cancelled', processed_count = %s,
                            failed_count = %s, incomplete_count = %s, updated_at = %s
                        WHERE audit_id = %s""",
                    (processed, failed, incomplete, timestamp, audit_id),
                )
            elif status is JobStatus.RUNNING:
                connection.execute(
                    "UPDATE jobs SET status = 'cancelling', updated_at = %s WHERE job_id = %s",
                    (timestamp, row["job_id"]),
                )
                connection.execute(
                    "UPDATE audit_runs SET status = 'cancelling', updated_at = %s WHERE audit_id = %s",
                    (timestamp, audit_id),
                )
            updated = connection.execute(
                "SELECT * FROM jobs WHERE job_id = %s", (row["job_id"],)
            ).fetchone()
            assert updated is not None
            return _decode_job(updated)

    def cancel_job(self, job_id: str, *, now: datetime | None = None) -> Job:
        """Cancel an audit by its job ID, or use generic job cancellation."""
        job_id = _require_identifier(job_id, "job_id")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT kind, resource_kind, resource_id FROM jobs WHERE job_id = %s", (job_id,)
            ).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        if row["kind"] in {"dataset_import", "export"}:
            # The generic boundary rechecks the stored kind under its job-row lock.
            return self.jobs.request_cancellation(job_id)
        if row["resource_kind"] == "audit" and row["resource_id"] is not None:
            return self.request_cancellation(row["resource_id"], now=now)
        return self.jobs.request_cancellation(job_id, now=now)

    def recover_stale_jobs(self, *, now: datetime | None = None) -> list[Job]:
        """Recover audit leases and reset only work without an atomic result."""
        recovered: list[Job] = []
        with self._connection(write=True) as connection:
            cutoff = _timestamp(now)
            rows = connection.execute(
                """SELECT jobs.*, audit_runs.audit_id
                   FROM jobs JOIN audit_runs ON audit_runs.job_id = jobs.job_id
                    WHERE (
                              jobs.status = 'running'
                              AND (
                                  jobs.lease_until <= %s
                                  OR EXISTS (
                                      SELECT 1 FROM audit_samples active_sample
                                      WHERE active_sample.audit_id = audit_runs.audit_id
                                        AND active_sample.status = 'running'
                                        AND active_sample.processing_deadline <= %s
                                  )
                              )
                          )
                       OR (
                              jobs.status = 'cancelling'
                              AND (
                                  jobs.lease_until <= %s
                                  OR EXISTS (
                                      SELECT 1 FROM audit_samples active_sample
                                      WHERE active_sample.audit_id = audit_runs.audit_id
                                        AND active_sample.status = 'running'
                                        AND active_sample.processing_deadline <= %s
                                  )
                              )
                          )
                    FOR UPDATE SKIP LOCKED""",
                (cutoff, cutoff, cutoff, cutoff),
            ).fetchall()
            timestamp = _timestamp(now)
            for row in rows:
                audit_id = row["audit_id"]
                candidate_samples = connection.execute(
                    """SELECT sample_id, sample_revision, processing_deadline, lease_until
                       FROM audit_samples
                       WHERE audit_id = %s AND status = 'running'
                       FOR UPDATE""",
                    (audit_id,),
                ).fetchall()
                timestamp = _timestamp(now)
                expired_samples = [
                    sample for sample in candidate_samples
                    if sample["processing_deadline"] is not None
                    and sample["processing_deadline"] <= timestamp
                    and row["lease_until"] is not None
                    and row["lease_until"] >= sample["processing_deadline"]
                    and sample["lease_until"] is not None
                    and sample["lease_until"] >= sample["processing_deadline"]
                ]
                if row["status"] == JobStatus.CANCELLING.value:
                    expired_samples = []
                for sample in expired_samples:
                    identity = "\x1f".join((
                        audit_id, sample["sample_id"], str(sample["sample_revision"]),
                        _TIMEOUT_REASON,
                    ))
                    failure_id = f"failure_{sha256(identity.encode('utf-8')).hexdigest()}"
                    self._write_timeout_failure_locked(
                        connection,
                        audit_id=audit_id,
                        sample_id=sample["sample_id"],
                        sample_revision=sample["sample_revision"],
                        failure_id=failure_id,
                        detail=_TIMEOUT_DETAIL,
                        now=timestamp,
                        operation_id=None,
                        claim_token=None,
                    )
                if row["status"] == JobStatus.RUNNING.value:
                    # A successful sample always changes its work row to
                    # completed in the same transaction as its evidence.  A
                    # running row therefore has no committed result yet.
                    connection.execute(
                        """UPDATE audit_samples SET status = 'queued', claimed_by = NULL,
                               claim_token = NULL, lease_until = NULL, processing_deadline = NULL,
                               terminal_operation_id = NULL
                           WHERE audit_id = %s AND status = 'running'""",
                        (audit_id,),
                    )
                    progress, processed, failed, incomplete = self._progress(
                        connection, audit_id, "queued",
                    )
                    connection.execute(
                        """UPDATE jobs SET status = 'queued', progress = %s, claimed_by = NULL,
                               claim_token = NULL, lease_until = NULL, recovery_count = recovery_count + 1,
                               updated_at = %s WHERE job_id = %s""",
                        (Jsonb(progress.model_dump(mode="json")), timestamp, row["job_id"]),
                    )
                    connection.execute(
                        """UPDATE audit_runs SET status = 'queued', processed_count = %s,
                                failed_count = %s, incomplete_count = %s, updated_at = %s
                            WHERE audit_id = %s""",
                        (processed, failed, incomplete, timestamp, audit_id),
                    )
                else:
                    connection.execute(
                        """UPDATE audit_samples SET status = 'cancelled', claimed_by = NULL,
                               claim_token = NULL, lease_until = NULL, processing_deadline = NULL,
                               finished_at = %s
                           WHERE audit_id = %s AND status IN ('queued', 'running')""",
                        (timestamp, audit_id),
                    )
                    progress, processed, failed, incomplete = self._progress(
                        connection, audit_id, "cancelled",
                    )
                    connection.execute(
                        """UPDATE jobs SET status = 'cancelled', progress = %s, result = %s,
                               claimed_by = NULL, claim_token = NULL, lease_until = NULL,
                               recovery_count = recovery_count + 1, updated_at = %s WHERE job_id = %s""",
                        (
                            Jsonb(progress.model_dump(mode="json")),
                            Jsonb(_result_reference(audit_id).model_dump(mode="json")),
                            timestamp, row["job_id"],
                        ),
                    )
                    connection.execute(
                        """UPDATE audit_runs SET status = 'cancelled', processed_count = %s,
                                failed_count = %s, incomplete_count = %s, updated_at = %s
                            WHERE audit_id = %s""",
                        (processed, failed, incomplete, timestamp, audit_id),
                    )
                updated = connection.execute(
                    "SELECT * FROM jobs WHERE job_id = %s", (row["job_id"],)
                ).fetchone()
                assert updated is not None
                recovered.append(_decode_job(updated))
        return recovered

    def get_measurements(
        self, audit_id: str, sample_id: str, sample_revision: int,
    ) -> dict[str, Any] | None:
        audit_id = _require_identifier(audit_id, "audit_id")
        sample_id = _require_identifier(sample_id, "sample_id")
        sample_revision = _require_positive_integer(sample_revision, "sample_revision")
        with self._connection() as connection:
            row = connection.execute(
                """SELECT document FROM audit_measurements
                   WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s""",
                (audit_id, sample_id, sample_revision),
            ).fetchone()
            if row is None:
                return None
            try:
                return deepcopy(_validate_measurement_document(row["document"], sample_id))
            except (TypeError, ValueError) as error:
                raise WorkflowStorageError("A saved measurement document failed validation.") from error

    def get_failures(self, audit_id: str, sample_id: str | None = None) -> list[AuditFailure]:
        audit_id = _require_identifier(audit_id, "audit_id")
        if sample_id is not None:
            sample_id = _require_identifier(sample_id, "sample_id")
        with self._connection() as connection:
            if sample_id is None:
                rows = connection.execute(
                    "SELECT * FROM audit_failures WHERE audit_id = %s ORDER BY created_at, failure_id",
                    (audit_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM audit_failures WHERE audit_id = %s AND sample_id = %s
                       ORDER BY created_at, failure_id""",
                    (audit_id, sample_id),
                ).fetchall()
            try:
                return [AuditFailure.model_validate(row) for row in rows]
            except ValidationError as error:
                raise WorkflowStorageError("A saved audit failure failed validation.") from error
