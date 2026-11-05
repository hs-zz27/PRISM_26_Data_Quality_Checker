"""Frozen selection and parent-owned, single-attempt managed export lifecycle.

Selection uses one read-only repeatable-read snapshot, then an atomic enqueue.
Lifecycle lock order is job -> export -> private input. No asset I/O in SQL work.
No automatic retry, requeue or inference. All ownership checks use server time
sampled after acquiring locks; a cancellation committed first wins publication.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
import json
import logging
import re
from typing import Literal
from uuid import uuid4

from psycopg.types.json import Jsonb
from pydantic import AwareDatetime, Field, ValidationError

from ..core.config import DatabaseSettings, load_database_settings
from ..core.export import (
    ERROR_MESSAGES, AuditNotExportable, AuditNotReady, ExportDependencyError,
    ExportNotFound, ExportNotReady, ExportOutcomeUnknown,
)
from ..models.export import (
    CapturedAvailability, Export, ExportClaim, ExportManifest, ExportManifestEntry,
    ExportPage, ExportPolicy, ExportRequest, ExportStarted, FrozenExport, FrozenSample,
    PublishedArtifacts, Record, Revision, exclusion,
    REPORT_COMMENT_CHARS, REPORT_MAX_ISSUES, REPORT_REASON_CHARS,
    ReportCorrection, ReportIssue, SampleReport,
)
from ..models.job import ApiError, Job, JobKind, JobStatus, Progress, ResultReference
from ..models.review import SampleReviewDecision
from ..models.rule import NonBlankText
from .database import CommitOutcomeUnknownError, DatabaseError, database_connection
from .audit_attempts import AUDIT_FAMILY, LATEST_SAMPLES
from .workflow_repository import (
    AuditNotFoundError, JobAlreadyFinishedError, JobClaimLostError, JobNotFoundError,
    SampleAssetManifestRepository, _decode_audit, _decode_job, _job_row_values,
)


logger = logging.getLogger(__name__)


class _ReviewPin(Record):
    audit_id: NonBlankText
    sample_id: NonBlankText
    sample_revision: Revision
    revision: int = Field(ge=1)
    decision: SampleReviewDecision = Field(strict=False)
    reviewer_id: NonBlankText
    comment: str
    updated_at: AwareDatetime


class _FindingRow(Record):
    """Narrow, strictly typed projection; reasons are bounded in SQL before transfer."""
    audit_id: NonBlankText
    sample_id: NonBlankText
    sample_revision: Revision
    rule_id: NonBlankText
    object_id: int | None
    outcome: Literal["pass", "fail", "unsure", "unavailable"]
    resolution: Literal["pending", "accepted", "rejected", "unsure"]
    reason: str | None
    reason_cut: bool | None


class _ProvenancePin(Record):
    parent_revision: Revision
    reviewer_id: NonBlankText
    reason: str


def _clip(text, limit, *, cut=False):
    """Single-line, bounded spreadsheet text; any shortening is marked with an ellipsis."""
    if text is None:
        return None
    flat = " ".join(text.split())
    if len(flat) > limit:
        return flat[:limit - 1] + "…"
    if cut:  # The source was already shortened before transfer.
        return (flat[:limit - 1] if len(flat) >= limit else flat) + "…"
    return flat


_OUTCOME_ORDER = {"fail": 0, "unsure": 1, "unavailable": 2}

_REPORT_FINDINGS = """
    SELECT f.audit_id, f.sample_id, f.sample_revision, f.rule_id, f.object_id, f.outcome,
           COALESCE(latest.resolution, 'pending') AS resolution,
           left(f.document->>'reason', %s) AS reason,
           char_length(f.document->>'reason') > %s AS reason_cut
    FROM findings f
    LEFT JOIN LATERAL (
        SELECT resolution FROM finding_reviews WHERE finding_id=f.finding_id
        ORDER BY revision DESC LIMIT 1
    ) latest ON TRUE
    WHERE f.audit_id = ANY(%s)
    ORDER BY f.audit_id, f.sample_id, f.sample_revision, f.rule_id,
             f.object_id NULLS FIRST, f.finding_id
"""


def _reports(connection, rows, reviews, provenances):
    """Per-sample reports from the selection snapshot, keyed by the exact result pin."""
    keys = [(row["audit_id"], row["sample_id"], row["sample_revision"]) for row in rows]
    if not keys:
        return []
    grouped = {key: [] for key in keys}
    fetched = REPORT_REASON_CHARS * 2  # whitespace may collapse; the flag marks real cuts
    for raw in connection.execute(
            _REPORT_FINDINGS, (fetched, fetched, sorted({key[0] for key in keys}))).fetchall():
        finding = _decode(_FindingRow, dict(raw))
        bucket = grouped.get((finding.audit_id, finding.sample_id, finding.sample_revision))
        if bucket is not None:  # Other family attempts are not this export's results.
            bucket.append(finding)
    reports = []
    for key, review, raw_provenance in zip(keys, reviews, provenances):
        provenance = None if raw_provenance is None else _decode(_ProvenancePin, raw_provenance)
        findings = grouped[key]
        counts = {outcome: sum(f.outcome == outcome for f in findings)
                  for outcome in ("pass", "fail", "unsure", "unavailable")}
        failing = sorted((f for f in findings if f.outcome != "pass"),
                         key=lambda f: _OUTCOME_ORDER[f.outcome])  # stable: keeps SQL order within outcome
        issues = [ReportIssue(rule_id=f.rule_id, object_id=f.object_id, outcome=f.outcome,
                              resolution=f.resolution,
                              reason=_clip(f.reason, REPORT_REASON_CHARS, cut=bool(f.reason_cut)))
                  for f in failing[:REPORT_MAX_ISSUES]]
        correction = None if provenance is None else ReportCorrection(
            parent_revision=provenance.parent_revision, reviewer_id=provenance.reviewer_id,
            reason=_clip(provenance.reason, REPORT_REASON_CHARS))
        reports.append(SampleReport(
            pass_count=counts["pass"], fail_count=counts["fail"], unsure_count=counts["unsure"],
            unavailable_count=counts["unavailable"], issues=issues,
            issues_omitted=len(failing) - len(issues),
            review_comment="" if review is None else _clip(review.comment, REPORT_COMMENT_CHARS),
            correction=correction))
    return reports


def _decode(model, value):
    try:
        return model.model_validate_json(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError, RecursionError) as error:
        raise ExportDependencyError("Saved export metadata is invalid.") from error


def _now(connection):
    return connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]


def _progress(processed, total, phase):
    return Progress(phase=phase, processed=processed, total=total,
                    fraction=1.0 if total == 0 else processed / total)


@dataclass(frozen=True)
class ExportLease:
    job: Job
    lease_remaining: float
    deadline_remaining: float


class ExportRepository:
    def __init__(self, *, settings=None):
        if settings is not None and not isinstance(settings, DatabaseSettings):
            raise TypeError('settings must be DatabaseSettings.')
        self.settings = load_database_settings() if settings is None else DatabaseSettings.model_validate(settings.model_dump())

    @contextmanager
    def _connection(self, *, write=False):
        try:
            with database_connection(self.settings, readonly=not write) as connection:
                yield connection
        except CommitOutcomeUnknownError as error:
            raise ExportOutcomeUnknown("Export commit requires reconciliation.") from error
        except DatabaseError as error:
            raise ExportDependencyError("Export storage is unavailable.") from error

    def _selection(self, request, policy, export_id):
        # Every audit, pin, manifest and current review below shares THIS snapshot.
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM audit_runs WHERE audit_id=%s",
                                     (request.audit_id,)).fetchone()
            if row is None:
                raise AuditNotFoundError(request.audit_id)
            audit = _decode_audit(row)
            if audit.status.value not in {"completed", "failed", "cancelled"}:
                raise AuditNotReady()
            if audit.source_mode.value != "snapshot":
                raise AuditNotExportable()
            if audit.sample_count > policy.max_samples:
                raise ExportDependencyError("Export selection exceeds the configured sample limit.")
            selected_at = _now(connection)
            if request.include_rechecks:
                active = connection.execute(
                    AUDIT_FAMILY + """SELECT 1 FROM audit_family family
                        JOIN audit_runs a ON a.audit_id=family.audit_id
                        WHERE a.status IN ('queued','running','cancelling') LIMIT 1""",
                    (request.audit_id,),
                ).fetchone()
                if active is not None:
                    raise AuditNotReady()
            prefix = LATEST_SAMPLES if request.include_rechecks else ""
            source = ("""selected_samples chosen JOIN audit_samples s
                ON (s.audit_id,s.sample_id,s.sample_revision)=
                   (chosen.audit_id,chosen.sample_id,chosen.sample_revision)"""
                if request.include_rechecks else "audit_samples s")
            ordinal_column = "chosen.ordinal" if request.include_rechecks else "s.ordinal"
            selection_filter = "TRUE" if request.include_rechecks else "s.audit_id=%s"
            rows = connection.execute(
                prefix + f"""SELECT s.*, {ordinal_column} AS selection_ordinal,
                   to_jsonb(m) AS registered_manifest,to_jsonb(r) AS current_review,
                   CASE WHEN p.sample_revision IS NULL THEN NULL ELSE jsonb_build_object(
                       'parent_revision',p.parent_revision,'reviewer_id',p.reviewer_id,
                       'reason',p.reason) END AS correction_provenance
                   FROM {source}
                   LEFT JOIN sample_asset_manifests m
                     ON (m.dataset_id,m.sample_id,m.sample_revision)=
                        (s.manifest_dataset_id,s.sample_id,s.sample_revision)
                   LEFT JOIN annotation_revision_provenance p
                     ON (p.dataset_id,p.sample_id,p.sample_revision)=
                        (s.manifest_dataset_id,s.sample_id,s.sample_revision)
                   LEFT JOIN LATERAL (
                       SELECT r.audit_id,r.sample_id,r.sample_revision,r.revision,
                              r.decision,r.reviewer_id,r.comment,r.updated_at FROM sample_reviews r
                       WHERE (r.audit_id,r.sample_id,r.sample_revision)=
                             (s.audit_id,s.sample_id,s.sample_revision)
                       ORDER BY r.revision DESC LIMIT 1
                   ) r ON TRUE
                   WHERE {selection_filter} ORDER BY {ordinal_column} LIMIT %s""",
                (request.audit_id, policy.max_samples + 1),
            ).fetchall()
            if len(rows) != audit.sample_count or len(rows) > policy.max_samples:
                raise ExportDependencyError("The audit pin ledger is inconsistent.")
            samples, pinned_reviews, provenances = [], [], []
            for ordinal, row in enumerate(rows):
                if (row["selection_ordinal"] != ordinal or row["manifest_dataset_id"] != audit.dataset_id
                        or row["registered_manifest"] is None):
                    raise ExportDependencyError("An audit manifest association is invalid.")
                manifest = SampleAssetManifestRepository._decode(row["registered_manifest"])
                if manifest.annotation_format != request.format:
                    raise AuditNotExportable()
                review = None if row["current_review"] is None else _decode(
                    _ReviewPin, row["current_review"])
                if review is not None and (
                    (review.audit_id, review.sample_id, review.sample_revision)
                    != (row["audit_id"], row["sample_id"], row["sample_revision"])
                ):
                    raise ExportDependencyError("The current review does not match its audit pin.")
                availability = CapturedAvailability(**{
                    role: getattr(manifest, role).availability
                    for role in CapturedAvailability.model_fields
                })
                decision = SampleReviewDecision.PENDING_REVIEW if review is None else review.decision
                reason = exclusion(decision, request.selection, request.include_images, availability, request.format)
                entry = ExportManifestEntry(
                    result_audit_id=row["audit_id"],
                    sample_id=row["sample_id"], sample_revision=row["sample_revision"],
                    ordinal=ordinal, processing_state=row["status"], included=reason is None,
                    review_decision=decision, review_revision=0 if review is None else review.revision,
                    reviewer_id=None if review is None else review.reviewer_id,
                    reviewed_at=None if review is None else review.updated_at,
                    availability=availability, reason=reason,
                )
                samples.append(FrozenSample(entry=entry, manifest=manifest))
                pinned_reviews.append(review)
                provenances.append(row["correction_provenance"])
            # Same REPEATABLE READ snapshot as the decisions above. Reports are
            # optional detail: unreadable report facts never block the export.
            # Database failures still propagate through _connection unchanged.
            try:
                reports = _reports(connection, rows, pinned_reviews, provenances)
            except (ValidationError, ExportDependencyError):
                logger.warning("Export %s: review report details could not be recorded.", export_id)
                reports = [None] * len(samples)
            document = ExportManifest(
                format=request.format,
                export_id=export_id, audit_id=audit.audit_id, dataset_id=audit.dataset_id,
                source_mode="snapshot", rulebook_id=audit.rulebook_id,
                rulebook_version=audit.rulebook_version, selection=request.selection,
                include_images=request.include_images, selected_at=selected_at,
                include_rechecks=request.include_rechecks,
                entries=[s.entry for s in samples],
            )
            reported = [s.model_copy(update={"report": report}) for s, report in zip(samples, reports)]
            frozen = FrozenExport(manifest=document, samples=reported, policy=policy)
            if len(frozen.model_dump_json().encode("utf-8")) > policy.max_metadata_bytes:
                # Reports are optional detail: never fail an export that fits without them.
                frozen = FrozenExport(manifest=document, samples=samples, policy=policy)
            if len(frozen.model_dump_json().encode("utf-8")) > policy.max_metadata_bytes:
                raise ExportDependencyError("Frozen export metadata exceeds its resource limit.")
        return frozen

    def enqueue(self, request: ExportRequest, *, policy: ExportPolicy):
        # Dump/reparse, rather than trusting previously validated mutable lists.
        request = ExportRequest.model_validate_json(request.model_dump_json())
        policy = ExportPolicy.model_validate_json(policy.model_dump_json())
        export_id, job_id = "export_" + uuid4().hex, "job_" + uuid4().hex
        try:
            frozen = self._selection(request, policy, export_id)
        except (ValidationError, KeyError, TypeError) as error:
            raise ExportDependencyError('The persisted audit selection metadata is invalid.') from error
        with self._connection(write=True) as connection:
            now = _now(connection)
            total = sum(e.included for e in frozen.manifest.entries)
            job = Job.queued(job_id=job_id, kind=JobKind.EXPORT, total=total, now=now)
            resource = Export(**request.model_dump(), export_id=export_id, job_id=job_id,
                              status="queued", created_at=now)
            connection.execute(
                """INSERT INTO jobs (job_id,kind,status,progress,result,error,created_at,updated_at,
                   resource_kind,resource_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'export',%s)""",
                _job_row_values(job) + (export_id,),
            )
            connection.execute(
                """INSERT INTO reviewed_exports (export_id,audit_id,job_id,status,request,created_at)
                   VALUES (%s,%s,%s,'queued',%s,%s)""",
                (export_id, request.audit_id, job_id, Jsonb(request.model_dump(mode="json")), now),
            )
            connection.execute(
                "INSERT INTO reviewed_export_inputs (export_id,frozen) VALUES (%s,%s)",
                (export_id, Jsonb(frozen.model_dump(mode="json"))),
            )
            result = ExportStarted(export=resource, job=job)
        # Return only after the context manager acknowledges commit.
        return result

    @staticmethod
    def _public(row, job):
        try:
            request = _decode(ExportRequest, row["request"])
            if (request.audit_id != row["audit_id"] or job.job_id != row["job_id"]
                    or job.kind is not JobKind.EXPORT or job.status.value != row["status"]
                    or job.created_at != row["created_at"]):
                raise ExportDependencyError("Export/job identity is inconsistent.")
            complete = job.status is JobStatus.COMPLETED
            if complete:
                if (job.progress.total is None or job.progress.processed != job.progress.total
                        or job.progress.fraction != 1.0
                        or row["included_count"] != job.progress.total):
                    raise ExportDependencyError("Completed export progress is inconsistent.")
                _decode(PublishedArtifacts, row["artifacts"])
                if (job.result is None or job.result.kind != "export"
                        or job.result.id != row["export_id"] or job.result.version is not None
                        or job.result.href != f"/api/v1/exports/{row['export_id']}"):
                    raise ExportDependencyError("Export job result is inconsistent.")
            elif row["artifacts"] is not None or job.result is not None:
                raise ExportDependencyError("An unfinished export advertises an artifact.")
            return Export(**request.model_dump(), export_id=row["export_id"], job_id=row["job_id"],
                          status=job.status, created_at=row["created_at"],
                          included_count=row["included_count"], excluded_count=row["excluded_count"],
                          download_url=f"/api/v1/exports/{row['export_id']}/download" if complete else None,
                          manifest_url=f"/api/v1/exports/{row['export_id']}/manifest" if complete else None)
        except (KeyError, TypeError, ValueError) as error:
            raise ExportDependencyError("Export metadata failed validation.") from error

    @staticmethod
    def _job(connection, job_id):
        row = connection.execute("SELECT * FROM jobs WHERE job_id=%s", (job_id,)).fetchone()
        if row is None:
            raise ExportDependencyError("Export job is missing.")
        return _decode_job(row)

    def _load(self, connection, export_id, *, lock=False, recovering=False):
        suffix = " FOR UPDATE" if lock else ""
        row = connection.execute("SELECT * FROM reviewed_exports WHERE export_id=%s" + suffix,
                                 (export_id,)).fetchone()
        if row is None:
            raise ExportNotFound()
        private = connection.execute("SELECT * FROM reviewed_export_inputs WHERE export_id=%s" + suffix,
                                     (export_id,)).fetchone()
        if private is None:
            raise ExportDependencyError("Export input is missing.")
        frozen = _decode(FrozenExport, private["frozen"])
        if (frozen.manifest.export_id != export_id
                or ExportRequest.model_validate({k: getattr(frozen.manifest, k)
                                                for k in ExportRequest.model_fields})
                != _decode(ExportRequest, row["request"])):
            raise ExportDependencyError("Frozen export provenance is inconsistent.")
        if (private["started_at"] is None) != (private["deadline"] is None):
            raise ExportDependencyError("Export attempt timing is inconsistent.")
        if private["started_at"] is not None and (
            private["deadline"] != private["started_at"] + timedelta(seconds=frozen.policy.max_seconds)
        ):
            raise ExportDependencyError("Export hard deadline was changed.")
        saved_job = connection.execute('SELECT * FROM jobs WHERE job_id=%s', (row['job_id'],)).fetchone()
        if (saved_job is None or saved_job['kind'] != 'export'
                or saved_job['resource_kind'] != 'export' or saved_job['resource_id'] != export_id):
            raise ExportDependencyError('The export job resource association is invalid.')
        decoded_job = _decode_job(saved_job)
        public = self._public(row, decoded_job)
        expected_total = sum(e.included for e in frozen.manifest.entries)
        progress = decoded_job.progress
        expected_fraction = 1.0 if expected_total == 0 else progress.processed / expected_total
        if progress.total != expected_total or progress.fraction != expected_fraction:
            raise ExportDependencyError('Export progress differs from frozen selection.')
        allocated = private['started_at'] is not None
        if not allocated and progress.processed != 0:
            raise ExportDependencyError('An unstarted export reports processed samples.')
        if saved_job['attempt'] != (1 if allocated else 0):
            raise ExportDependencyError('Export attempt count is inconsistent.')
        if ((public.status is JobStatus.QUEUED and allocated)
                or (not allocated and public.status not in {JobStatus.QUEUED, JobStatus.CANCELLED})):
            raise ExportDependencyError('Export lifecycle and attempt allocation disagree.')
        active = public.status in {JobStatus.RUNNING, JobStatus.CANCELLING}
        if active and not recovering:
            from pydantic import TypeAdapter
            from ..models.export import Identifier
            try:
                TypeAdapter(Identifier).validate_python(saved_job['claimed_by'], strict=True)
                token, lease = saved_job['claim_token'], saved_job['lease_until']
                if (not isinstance(token, str) or re.fullmatch(r'[0-9a-f]{32}', token) is None
                        or lease is None or lease > private['deadline']):
                    raise ValueError('Invalid active claim.')
            except (ValueError, TypeError) as error:
                raise ExportDependencyError('Active export ownership metadata is invalid.') from error
        elif not active and any(saved_job[name] is not None for name in ('claimed_by', 'claim_token', 'lease_until')):
            raise ExportDependencyError('An inactive export retains active ownership metadata.')
        if len(frozen.model_dump_json().encode('utf-8')) > frozen.policy.max_metadata_bytes:
            raise ExportDependencyError('Frozen input exceeds its saved metadata policy.')
        if public.status is JobStatus.COMPLETED:
            self._check_completed(public, frozen, _decode(PublishedArtifacts, row['artifacts']))
        return row, private, frozen

    def get_export(self, export_id):
        from pydantic import TypeAdapter
        from ..models.export import Identifier
        export_id = TypeAdapter(Identifier).validate_python(export_id, strict=True)
        with self._connection() as connection:
            row, private, frozen = self._load(connection, export_id)
            result = self._public(row, self._job(connection, row["job_id"]))
            if result.status is JobStatus.COMPLETED:
                self._check_completed(result, frozen, _decode(PublishedArtifacts, row["artifacts"]))
        return result

    @staticmethod
    def _check_completed(resource, frozen, artifacts):
        included = sum(e.included for e in frozen.manifest.entries)
        if (resource.included_count != included
                or resource.excluded_count != len(frozen.samples) - included
                or artifacts.archive.size_bytes > frozen.policy.max_archive_bytes
                or artifacts.manifest.size_bytes > frozen.policy.max_metadata_bytes):
            raise ExportDependencyError("Completed export metadata is inconsistent.")

    def list_exports(self, *, limit=20, offset=0):
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 9_223_372_036_854_775_807:
            raise ValueError("Invalid export pagination.")
        with self._connection() as connection:
            total = connection.execute("SELECT count(*) AS n FROM reviewed_exports").fetchone()["n"]
            rows = connection.execute(
                """SELECT export_id FROM reviewed_exports
                   ORDER BY created_at DESC,export_id DESC LIMIT %s OFFSET %s""",
                (limit, offset),
            ).fetchall()
            items = []
            for identity in rows:
                # Keep the SAME readonly snapshot and validate the full saved input
                # exactly as detail/artifact reads do. Listing must not downgrade
                # corrupt frozen metadata into apparently successful resources.
                # The page is bounded to 100; no assets/files or recovery are read.
                row, private, frozen = self._load(connection, identity['export_id'])
                items.append(self._public(row, self._job(connection, row['job_id'])))
        return ExportPage(items=items, limit=limit, offset=offset, total=total,
                          next_offset=offset + len(items) if offset + len(items) < total else None)

    def read_frozen(self, export_id):
        """Private worker/review seam. Never a completed-artifact HTTP response."""
        with self._connection() as connection:
            return self._load(connection, export_id)[2]

    def completed_artifacts(self, export_id):
        with self._connection() as connection:
            row, private, frozen = self._load(connection, export_id)
            resource = self._public(row, self._job(connection, row["job_id"]))
            if resource.status is not JobStatus.COMPLETED:
                raise ExportNotReady()
            artifacts = _decode(PublishedArtifacts, row["artifacts"])
            self._check_completed(resource, frozen, artifacts)
        return frozen, artifacts

    def _lock(self, connection, job_id):
        job = connection.execute("SELECT * FROM jobs WHERE job_id=%s FOR UPDATE", (job_id,)).fetchone()
        if job is None:
            raise JobNotFoundError(job_id)
        row, private, frozen = self._locked_resource(connection, job)
        return job, row, private, frozen

    def _locked_resource(self, connection, job, *, recovering=False):
        if job["kind"] != "export" or job["resource_kind"] != "export":
            raise JobClaimLostError("Not a managed export job.")
        row, private, frozen = self._load(connection, job["resource_id"], lock=True, recovering=recovering)
        if row["job_id"] != job["job_id"] or row["status"] != job["status"]:
            raise ExportDependencyError("Managed export identity is inconsistent.")
        self._public(row, _decode_job(job))
        return row, private, frozen

    def claim_next(self, *, worker_id):
        from pydantic import TypeAdapter
        from ..models.export import Identifier
        worker_id = TypeAdapter(Identifier).validate_python(worker_id, strict=True)
        with self._connection(write=True) as connection:
            job = connection.execute(
                """SELECT * FROM jobs WHERE kind='export' AND status='queued'
                   ORDER BY created_at,job_id FOR UPDATE SKIP LOCKED LIMIT 1""",
            ).fetchone()
            if job is None:
                return None
            row, private, frozen = self._locked_resource(connection, job)
            if job["attempt"] != 0 or private["started_at"] is not None:
                raise ExportDependencyError("An export attempt cannot be replayed.")
            now = _now(connection)
            deadline = now + timedelta(seconds=frozen.policy.max_seconds)
            token = uuid4().hex
            lease = min(deadline, now + timedelta(seconds=frozen.policy.lease_seconds))
            connection.execute("UPDATE reviewed_export_inputs SET started_at=%s,deadline=%s WHERE export_id=%s",
                               (now, deadline, row["export_id"]))
            connection.execute("UPDATE reviewed_exports SET status='running' WHERE export_id=%s", (row["export_id"],))
            connection.execute(
                """UPDATE jobs SET status='running',attempt=1,claimed_by=%s,claim_token=%s,
                   lease_until=%s,updated_at=%s WHERE job_id=%s""",
                (worker_id, token, lease, now, job["job_id"]),
            )
            result = ExportClaim(job=self._job(connection, job["job_id"]), frozen=frozen,
                                 worker_id=worker_id, claim_token=token, deadline=deadline)
        return result

    @staticmethod
    def _owned(job, private, frozen, claim, now, *, allow_deadline=False):
        if (job["kind"] != "export" or job["resource_kind"] != "export"
                or job["resource_id"] != claim.frozen.manifest.export_id
                or job["job_id"] != claim.job.job_id or job["attempt"] != 1
                or job["claimed_by"] != claim.worker_id or job["claim_token"] != claim.claim_token
                or job["status"] not in {"running", "cancelling"}
                or job["lease_until"] is None or job["lease_until"] <= now
                or private["deadline"] != claim.deadline or frozen != claim.frozen):
            raise JobClaimLostError("The export worker no longer owns this attempt.")
        if not allow_deadline and private["deadline"] <= now:
            raise JobClaimLostError("The export processing deadline expired.")

    def _terminal(self, connection, job, row, frozen, *, status, now, code=None, artifacts=None):
        total = sum(e.included for e in frozen.manifest.entries)
        progress = _decode(Progress, job["progress"])
        if progress.total != total:
            raise ExportDependencyError("Export progress total is inconsistent.")
        complete = status == "completed"
        result = ResultReference(kind="export", id=row["export_id"], version=None,
                                 href=f"/api/v1/exports/{row['export_id']}") if complete else None
        error = ApiError(code=code, message=ERROR_MESSAGES[code], request_id=job["job_id"]) if code else None
        connection.execute(
            """UPDATE reviewed_exports SET status=%s,included_count=%s,excluded_count=%s,artifacts=%s
               WHERE export_id=%s""",
            (status, total if complete else None, len(frozen.samples) - total if complete else None,
             Jsonb(artifacts.model_dump(mode="json")) if complete else None, row["export_id"]),
        )
        connection.execute(
            """UPDATE jobs SET status=%s,progress=%s,result=%s,error=%s,claimed_by=NULL,
               claim_token=NULL,lease_until=NULL,updated_at=%s WHERE job_id=%s""",
            (status, Jsonb(_progress(total if complete else progress.processed, total, status).model_dump()),
             None if result is None else Jsonb(result.model_dump(mode="json")),
             None if error is None else Jsonb(error.model_dump(mode="json")), now, job["job_id"]),
        )
        return self._job(connection, job["job_id"])

    def heartbeat(self, claim: ExportClaim, *, processed=None):
        claim = ExportClaim.model_validate_json(claim.model_dump_json())
        with self._connection(write=True) as connection:
            job, row, private, frozen = self._lock(connection, claim.job.job_id)
            now = _now(connection)
            self._owned(job, private, frozen, claim, now, allow_deadline=True)
            if job["status"] == "cancelling":
                result = self._terminal(connection, job, row, frozen, status="cancelled", now=now)
            elif private["deadline"] <= now:
                result = self._terminal(connection, job, row, frozen, status="failed", now=now, code="EXPORT_TIMEOUT")
            else:
                old = _decode(Progress, job["progress"])
                n = old.processed if processed is None else processed
                if type(n) is not int or not old.processed <= n <= old.total:
                    raise ExportDependencyError("Export child progress is invalid.")
                lease = min(private["deadline"], now + timedelta(seconds=frozen.policy.lease_seconds))
                connection.execute("UPDATE jobs SET progress=%s,lease_until=%s,updated_at=%s WHERE job_id=%s",
                                   (Jsonb(_progress(n, old.total, "exporting").model_dump()), lease, now, job["job_id"]))
                result = self._job(connection, job["job_id"])
                sampled = _now(connection)
                acknowledgement = ExportLease(result, max(0.0, (lease - sampled).total_seconds()),
                                              max(0.0, (private["deadline"] - sampled).total_seconds()))
                return acknowledgement
        return ExportLease(result, 0.0, 0.0)

    def finish(self, claim: ExportClaim, *, artifacts=None, code=None):
        claim = ExportClaim.model_validate_json(claim.model_dump_json())
        if (artifacts is None) == (code is None) or (code is not None and code not in ERROR_MESSAGES):
            raise ValueError("Finish requires either verified artifacts or one safe error code.")
        if artifacts is not None:
            artifacts = PublishedArtifacts.model_validate_json(artifacts.model_dump_json())
            if artifacts.archive.key.split('/')[0] != claim.claim_token:
                raise ExportDependencyError("Artifacts do not belong to the current attempt.")
        with self._connection(write=True) as connection:
            job, row, private, frozen = self._lock(connection, claim.job.job_id)
            now = _now(connection)
            self._owned(job, private, frozen, claim, now, allow_deadline=True)
            if job["status"] == "cancelling":
                status, code, artifacts = "cancelled", None, None
            elif private["deadline"] <= now:
                status, code, artifacts = "failed", "EXPORT_TIMEOUT", None
            else:
                status = "completed" if artifacts is not None else "failed"
            if artifacts is not None:
                if (artifacts.archive.size_bytes > frozen.policy.max_archive_bytes
                        or artifacts.manifest.size_bytes > frozen.policy.max_metadata_bytes):
                    raise ExportDependencyError("Export artifacts exceed frozen policy.")
            return self._terminal(connection, job, row, frozen, status=status, now=now,
                                  code=code, artifacts=artifacts)

    def _cancel_locked(self, connection, job):
        """Called with JobRepository's job-row lock and existing SQL transaction."""
        row, private, frozen = self._locked_resource(connection, job)
        now = _now(connection)
        if job["status"] in {"completed", "failed", "cancelled"}:
            raise JobAlreadyFinishedError(job["job_id"], JobStatus(job["status"]))
        if job["status"] == "queued":
            return self._terminal(connection, job, row, frozen, status="cancelled", now=now)
        if job["status"] != "cancelling":
            connection.execute("UPDATE jobs SET status='cancelling',updated_at=%s WHERE job_id=%s", (now, job["job_id"]))
            connection.execute("UPDATE reviewed_exports SET status='cancelling' WHERE export_id=%s", (row["export_id"],))
        return self._job(connection, job["job_id"])

    def recover_stale(self):
        """Terminalize interrupted attempts; never resurrect, replay or delete files."""
        result = []
        with self._connection(write=True) as connection:
            rows = connection.execute(
                """SELECT j.* FROM jobs j JOIN reviewed_export_inputs i ON i.export_id=j.resource_id
                   WHERE j.kind='export' AND j.status IN ('running','cancelling')
                     AND (j.lease_until IS NULL OR j.lease_until<=clock_timestamp()
                          OR i.deadline<=clock_timestamp())
                   ORDER BY j.created_at,j.job_id FOR UPDATE OF j SKIP LOCKED LIMIT 100""",
            ).fetchall()
            for job in rows:
                # A missing lease/owner is abandoned work, not authority to
                # execute. Recovery may clear that claim while still validating
                # provenance, attempt timing, job identity and frozen membership.
                row, private, frozen = self._locked_resource(connection, job, recovering=True)
                now = _now(connection)
                if (job["lease_until"] is not None and job["lease_until"] > now
                        and private["deadline"] is not None and private["deadline"] > now):
                    continue
                status = "cancelled" if job["status"] == "cancelling" else "failed"
                result.append(self._terminal(
                    connection, job, row, frozen, status=status, now=now,
                    code=None if status == "cancelled" else "EXPORT_TIMEOUT"
                    if private["deadline"] is not None and private["deadline"] <= now else "EXPORT_INTERRUPTED",
                ))
        return result
