"""Managed, single-attempt dataset lifecycle. Parent owns every SQL transition.

Lock order: job -> request -> dataset -> catalogue/manifest rows. No automatic
transaction retry or archive replay. Files and PostgreSQL are separate systems.
"""

from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4
from urllib.parse import quote

from psycopg.types.json import Jsonb

from ..core.dataset_import import ERROR_MESSAGES
from ..models.dataset import Dataset, DatasetImportStarted, source_issue
from ..models.dataset_import import ImportClaim, ImportRequest
from ..models.job import ApiError, Job, JobKind, JobStatus, Progress, ResultReference
from .dataset_repository import (
    DatasetRepository, DatasetStorageError, _identifier,
)
from .workflow_repository import (
    JobAlreadyFinishedError, JobClaimLostError, JobNotFoundError,
    _decode_job, _job_row_values,
)


@dataclass(frozen=True)
class LeaseAcknowledgement:
    job: Job
    lease_remaining: float
    deadline_remaining: float


def _now(connection):
    return connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]


class DatasetImportRepository(DatasetRepository):
    def enqueue(self, request: ImportRequest, *, name: str, format="penn_fudan"):
        if request.format != format:
            raise ValueError("Import format must match the durable dataset format.")
        request = ImportRequest.model_validate(request.model_dump())
        if request.started_at is not None:
            raise ValueError("New imports must not already have an attempt.")
        with self._connection(write=True) as connection:
            now = _now(connection)
            dataset = Dataset(dataset_id=request.dataset_id, name=name, format=format,
                              status="importing", sample_count=None, object_count=None,
                              import_job_id=request.job_id, created_at=now)
            job = Job.queued(job_id=request.job_id, kind=JobKind.DATASET_IMPORT, total=None, now=now)
            connection.execute(
                """INSERT INTO jobs (job_id,kind,status,progress,result,error,created_at,updated_at,
                   resource_kind,resource_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'dataset',%s)""",
                _job_row_values(job) + (dataset.dataset_id,),
            )
            connection.execute(
                """INSERT INTO datasets (dataset_id,name,format,status,sample_count,object_count,
                   import_job_id,created_at,issues) VALUES (%s,%s,%s,'importing',NULL,NULL,%s,%s,'[]')""",
                (dataset.dataset_id, dataset.name, dataset.format.value, request.job_id, now),
            )
            connection.execute(
                """INSERT INTO dataset_import_requests (job_id,dataset_id,archive_key,archive_sha256,
                   archive_size_bytes,policy) VALUES (%s,%s,%s,%s,%s,%s)""",
                (request.job_id, request.dataset_id, request.archive_key, request.archive_sha256,
                 request.archive_size_bytes, Jsonb(request.policy.model_dump(mode="json"))),
            )
            result = DatasetImportStarted(dataset=dataset, job=job)
        return result

    @staticmethod
    def _job(connection, job_id):
        row = connection.execute("SELECT * FROM jobs WHERE job_id=%s", (job_id,)).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        return _decode_job(row)

    @staticmethod
    def _request_dataset(connection, row):
        if row is None or row["kind"] != "dataset_import":
            raise JobClaimLostError("Not a managed dataset import.")
        private = connection.execute(
            "SELECT * FROM dataset_import_requests WHERE job_id=%s FOR UPDATE", (row["job_id"],),
        ).fetchone()
        try:
            request = ImportRequest.model_validate(dict(private))
        except (TypeError, ValueError) as error:
            raise DatasetStorageError("The import request ledger is invalid.") from error
        dataset = connection.execute(
            "SELECT * FROM datasets WHERE dataset_id=%s FOR UPDATE", (request.dataset_id,),
        ).fetchone()
        if (dataset is None or dataset["import_job_id"] != row["job_id"]
                or row["resource_kind"] != "dataset" or row["resource_id"] != request.dataset_id):
            raise DatasetStorageError("Import resource identity is inconsistent.")
        request = ImportRequest.model_validate({**request.model_dump(), "format": dataset["format"]})
        return request, dataset

    def claim_next(self, *, worker_id: str):
        worker_id = _identifier(worker_id, "worker_id")
        with self._connection(write=True) as connection:
            row = connection.execute(
                """SELECT * FROM jobs WHERE kind='dataset_import' AND status='queued' AND attempt=0
                   ORDER BY created_at,job_id FOR UPDATE SKIP LOCKED LIMIT 1""",
            ).fetchone()
            if row is None:
                return None
            request, dataset = self._request_dataset(connection, row)
            if request.started_at is not None or dataset["status"] != "importing":
                raise DatasetStorageError("A queued import already has an attempt or terminal dataset.")
            now = _now(connection)
            token = uuid4().hex
            deadline = now + timedelta(seconds=request.policy.max_seconds)
            lease = now + timedelta(seconds=request.policy.lease_seconds)
            connection.execute(
                "UPDATE dataset_import_requests SET started_at=%s,deadline=%s WHERE job_id=%s",
                (now, deadline, row["job_id"]),
            )
            connection.execute(
                """UPDATE jobs SET status='running',attempt=1,claimed_by=%s,claim_token=%s,
                   lease_until=%s,updated_at=%s WHERE job_id=%s""", (worker_id, token, lease, now, row["job_id"]),
            )
            request = ImportRequest.model_validate({**request.model_dump(), "started_at": now, "deadline": deadline})
            claim = ImportClaim(job=self._job(connection, row["job_id"]), request=request,
                                worker_id=worker_id, claim_token=token, lease_until=lease)
        return claim

    @staticmethod
    def _owned(row, request, dataset, claim, now):
        if (request != claim.request or row["attempt"] != 1
                or row["claimed_by"] != claim.worker_id or row["claim_token"] != claim.claim_token
                or row["status"] not in {"running", "cancelling"}
                or row["lease_until"] is None or row["lease_until"] <= now
                or dataset["status"] != "importing"):
            raise JobClaimLostError("The import worker no longer owns this attempt.")

    @staticmethod
    def _progress(processed, total, phase):
        return Progress(phase=phase, processed=processed, total=total,
                        fraction=None if total is None else (1.0 if total == 0 else processed / total))

    def _terminal(self, connection, row, dataset, *, status, now, code=None, objects=None):
        progress = Progress.model_validate(row["progress"])
        ready = status == "ready"
        job_status = "completed" if ready else "cancelled" if status == "cancelled" else "failed"
        details = list(dataset["issues"])
        if not ready and status != "cancelled":
            details.append(source_issue(
            "invalid_archive" if code == "INVALID_ARCHIVE" else "invalid_layout" if code == "INVALID_LAYOUT"
            else "resource_limit" if code == "IMPORT_RESOURCE_LIMIT" else "import_failed",
            field="archive" if code == "INVALID_ARCHIVE" else "layout" if code == "INVALID_LAYOUT" else None,
            ).model_dump())
        result = ResultReference(kind="dataset", id=dataset["dataset_id"], version=None,
                                 href=f"/api/v1/datasets/{quote(dataset['dataset_id'], safe='')}") if ready else None
        error = ApiError(code=code, message=ERROR_MESSAGES[code], request_id=row["job_id"]) if code else None
        connection.execute(
            "UPDATE datasets SET status=%s,issues=%s,object_count=%s WHERE dataset_id=%s",
            (status, Jsonb(details), objects if ready else None, dataset["dataset_id"]),
        )
        connection.execute(
            """UPDATE jobs SET status=%s,progress=%s,result=%s,error=%s,claimed_by=NULL,
               claim_token=NULL,lease_until=NULL,updated_at=%s WHERE job_id=%s""",
            (job_status, Jsonb(self._progress(progress.processed, progress.total, job_status).model_dump()),
             None if result is None else Jsonb(result.model_dump(mode="json")),
             None if error is None else Jsonb(error.model_dump(mode="json")), now, row["job_id"]),
        )
        return self._job(connection, row["job_id"])

    def heartbeat(self, claim: ImportClaim) -> LeaseAcknowledgement:
        claim = ImportClaim.model_validate(claim.model_dump())
        with self._connection(write=True) as connection:
            row, request, dataset = self._lock_import(connection, claim)
            now = _now(connection)
            self._owned(row, request, dataset, claim, now)
            if row["status"] == "cancelling":
                job = self._terminal(connection, row, dataset, status="cancelled", now=now)
            elif request.deadline <= now:
                job = self._terminal(connection, row, dataset, status="failed", now=now, code="IMPORT_TIMEOUT")
            else:
                lease = now + timedelta(seconds=request.policy.lease_seconds)
                connection.execute("UPDATE jobs SET lease_until=%s,updated_at=%s WHERE job_id=%s",
                                   (lease, now, row["job_id"]))
                job = self._job(connection, row["job_id"])
                sampled = _now(connection)
                return LeaseAcknowledgement(job, max(0, (lease - sampled).total_seconds()),
                                            max(0, (request.deadline - sampled).total_seconds()))
        return LeaseAcknowledgement(job, 0, 0)

    def discover(self, claim: ImportClaim, total: int, *, unsupported=False):
        if type(total) is not int or not 1 <= total <= claim.request.policy.max_samples or type(unsupported) is not bool:
            raise ValueError("Invalid discovery boundary.")
        claim = ImportClaim.model_validate(claim.model_dump())
        with self._connection(write=True) as connection:
            row, request, dataset = self._lock_import(connection, claim)
            now = self._authorize(connection, row, request, dataset, claim)
            if dataset["sample_count"] is not None:
                raise DatasetStorageError("Discovery may be recorded only once.")
            issues = [source_issue("unsupported_extra", field="archive").model_dump()] if unsupported else []
            connection.execute("UPDATE datasets SET sample_count=%s,issues=%s WHERE dataset_id=%s",
                               (total, Jsonb(issues), dataset["dataset_id"]))
            connection.execute("UPDATE jobs SET progress=%s,updated_at=%s WHERE job_id=%s",
                               (Jsonb(self._progress(0, total, "importing").model_dump()), now, row["job_id"]))
            self._authorize(connection, row, request, dataset, claim)

    def record_progress(self, claim: ImportClaim):
        """A separate owned boundary reflects only already-committed sample heads."""
        claim = ImportClaim.model_validate(claim.model_dump())
        with self._connection(write=True) as connection:
            row, request, dataset = self._lock_import(connection, claim)
            now = self._authorize(connection, row, request, dataset, claim)
            n = connection.execute("SELECT count(*) AS n FROM dataset_sample_heads WHERE dataset_id=%s",
                                   (dataset["dataset_id"],)).fetchone()["n"]
            if dataset["sample_count"] is None:
                raise DatasetStorageError("Progress has no discovery boundary.")
            connection.execute("UPDATE jobs SET progress=%s,updated_at=%s WHERE job_id=%s",
                               (Jsonb(self._progress(n, dataset["sample_count"], "importing").model_dump()),
                                now, row["job_id"]))
            self._authorize(connection, row, request, dataset, claim)

    def finish(self, claim: ImportClaim, *, code: str | None = None):
        if code is not None and code not in ERROR_MESSAGES:
            raise ValueError("Unsupported terminal import code.")
        claim = ImportClaim.model_validate(claim.model_dump())
        with self._connection(write=True) as connection:
            row, request, dataset = self._lock_import(connection, claim)
            now = _now(connection)
            self._owned(row, request, dataset, claim, now)
            if row["status"] == "cancelling":
                return self._terminal(connection, row, dataset, status="cancelled", now=now)
            if code is None:
                # All heads must be revision 1, fully published, and match stored identities.
                stats = connection.execute(
                    """SELECT count(*) AS n,count(r.annotation_object_count) AS known,
                       sum(r.annotation_object_count) AS objects,min(s.ordinal) AS first,max(s.ordinal) AS last,
                       bool_and(h.sample_revision=1 AND jsonb_build_object(
                           'dataset_id',m.dataset_id,'sample_id',m.sample_id,'sample_revision',m.sample_revision,
                           'annotation_format',m.annotation_format,'image_key',m.image_key,
                           'original_image',jsonb_build_object(
                               'availability',m.original_image_availability,'storage_key',m.original_image_storage_key,
                               'content_type',m.original_image_content_type,'sha256',m.original_image_sha256,
                               'size_bytes',m.original_image_size_bytes),
                           'raw_mask',jsonb_build_object(
                               'availability',m.raw_mask_availability,'storage_key',m.raw_mask_storage_key,
                               'content_type',m.raw_mask_content_type,'sha256',m.raw_mask_sha256,
                               'size_bytes',m.raw_mask_size_bytes),
                           'annotation_text',jsonb_build_object(
                               'availability',m.annotation_text_availability,'storage_key',m.annotation_text_storage_key,
                               'content_type',m.annotation_text_content_type,'sha256',m.annotation_text_sha256,
                               'size_bytes',m.annotation_text_size_bytes))=
                           (jsonb_build_object('annotation_format','penn_fudan','image_key',NULL)
                            || r.manifest_document)) AS exact
                       FROM dataset_samples s JOIN dataset_sample_heads h USING(dataset_id,sample_id)
                       JOIN dataset_sample_revisions r ON (r.dataset_id,r.sample_id,r.sample_revision)=
                           (h.dataset_id,h.sample_id,h.sample_revision)
                       JOIN sample_asset_manifests m ON (m.dataset_id,m.sample_id,m.sample_revision)=
                           (r.dataset_id,r.sample_id,r.sample_revision) WHERE s.dataset_id=%s""",
                    (dataset["dataset_id"],),
                ).fetchone()
                total = dataset["sample_count"]
                if (total is None or stats["n"] != total or total == 0 or stats["first"] != 0
                        or stats["last"] != total - 1 or not stats["exact"]):
                    raise DatasetStorageError("The catalogue is not completely published.")
                objects = int(stats["objects"]) if stats["known"] == total else None
                # Check time AFTER aggregation/index waits before coupled terminal SQL.
                now = _now(connection)
                self._owned(row, request, dataset, claim, now)
                if request.deadline <= now:
                    code = "IMPORT_TIMEOUT"
                else:
                    self._authorize(connection, row, request, dataset, claim)
                    row["progress"] = self._progress(total, total, "completed").model_dump()
                    return self._terminal(connection, row, dataset, status="ready", now=now, objects=objects)
            now = _now(connection)
            self._owned(row, request, dataset, claim, now)
            if request.deadline <= now:
                code = "IMPORT_TIMEOUT"
            return self._terminal(connection, row, dataset,
                                  status="invalid" if code in {"INVALID_ARCHIVE", "INVALID_LAYOUT"} else "failed",
                                  now=now, code=code)

    def _cancel_locked(self, connection, row):
        """Same-transaction dispatch from JobRepository; the job is ALREADY locked."""
        request, dataset = self._request_dataset(connection, row)
        now = _now(connection)
        if row["status"] in {"completed", "failed", "cancelled"}:
            raise JobAlreadyFinishedError(row["job_id"], JobStatus(row["status"]))
        if row["status"] == "queued":
            return self._terminal(connection, row, dataset, status="cancelled", now=now)
        if row["status"] != "cancelling":
            connection.execute("UPDATE jobs SET status='cancelling',updated_at=%s WHERE job_id=%s",
                               (now, row["job_id"]))
        return self._job(connection, row["job_id"])

    def cancel(self, job_id: str):
        job_id = _identifier(job_id, "job_id")
        with self._connection(write=True) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=%s FOR UPDATE", (job_id,)).fetchone()
            if row is None:
                raise JobNotFoundError(job_id)
            result = self._cancel_locked(connection, row)
        return result

    def recover_stale(self):
        recovered = []
        with self._connection(write=True) as connection:
            rows = connection.execute(
                """SELECT j.* FROM jobs j JOIN dataset_import_requests r USING(job_id)
                   WHERE j.kind='dataset_import' AND j.status IN ('running','cancelling')
                     AND (j.lease_until<=clock_timestamp() OR r.deadline<=clock_timestamp())
                   ORDER BY j.created_at,j.job_id FOR UPDATE OF j SKIP LOCKED LIMIT 100""",
            ).fetchall()
            for row in rows:
                request, dataset = self._request_dataset(connection, row)
                now = _now(connection)
                if row["lease_until"] > now and request.deadline > now:
                    continue
                recovered.append(self._terminal(
                    connection, row, dataset, status="cancelled" if row["status"] == "cancelling" else "failed",
                    now=now, code=None if row["status"] == "cancelling" else
                    "IMPORT_TIMEOUT" if request.deadline <= now else "IMPORT_INTERRUPTED",
                ))
        return recovered

    def read_job(self, job_id):
        with self._connection() as connection:
            return self._job(connection, _identifier(job_id, "job_id"))
