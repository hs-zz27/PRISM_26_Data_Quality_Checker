"""Catalogue reads and the *internal* claim-protected import publication seam.

No upload acceptance, claim acquisition, worker, route, decoding or filesystem I/O.
Lock order for future import writers: job -> request -> dataset -> sample identity
-> manifest/revision/head. Recheck database time after all potentially blocking SQL.
"""

from collections.abc import Iterator
from contextlib import contextmanager
import json
from typing import Any
from urllib.parse import quote

from psycopg.types.json import Jsonb

from ..core.config import DatabaseSettings, load_database_settings
from ..models.asset_manifest import SampleAssetManifest
from ..models.audit_read import AssetReference, SampleAssets
from ..models.dataset import Dataset, DatasetPage, Sample, SamplePage
from ..models.dataset_import import (
    CatalogueRevision, ImportClaim, ImportRequest, SamplePublication,
)
from .database import CommitOutcomeUnknownError, DatabaseError, database_connection
from .workflow_repository import (
    JobCancellationError, JobClaimLostError, SampleAssetManifestRepository,
    WorkflowStorageError,
)


class DatasetStorageError(WorkflowStorageError):
    """Safe category for unavailable/corrupt catalogue storage; no raw driver text."""


class DatasetCommitOutcomeUnknownError(DatasetStorageError):
    """Stop and reconcile; never automatically replay acceptance or computation."""

    commit_outcome_unknown = True


class DatasetNotFoundError(LookupError):
    def __init__(self, dataset_id: str) -> None:
        self.dataset_id = dataset_id
        super().__init__("The requested dataset does not exist.")


class DatasetSampleNotFoundError(LookupError):
    def __init__(self, dataset_id: str, sample_id: str) -> None:
        self.dataset_id, self.sample_id = dataset_id, sample_id
        super().__init__("The requested catalogue sample does not exist.")


class DatasetRevisionNotFoundError(LookupError):
    def __init__(self, dataset_id: str, sample_id: str, revision: int) -> None:
        self.dataset_id, self.sample_id, self.revision = dataset_id, sample_id, revision
        super().__init__("The requested catalogue revision does not exist.")


class DatasetPublicationConflictError(RuntimeError):
    """Identity, ordinal, metadata or immutable manifest differs from publication."""


class DatasetImportDeadlineError(RuntimeError):
    """The original finite import deadline has expired; Phase 2 terminalizes it."""


def _identifier(value: str, field: str) -> str:
    if type(value) is not str or not value.strip() or len(value) > 255 or "\x00" in value:
        raise ValueError(f"{field} must be a nonblank identifier of at most 255 characters.")
    return value


def _integer(value: int, field: str, minimum: int, maximum: int = 9_223_372_036_854_775_807) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{field} is outside its supported integer range.")
    return value


def _pagination(limit: int, offset: int) -> None:
    _integer(limit, "limit", 1, 100)
    _integer(offset, "offset", 0)


def _decode(model, document):
    # JSON-mode validation keeps tuple/date decoding while rejecting booleans as
    # integers. Sample evidence uses JSON text to preserve escaped source NULs;
    # private manifest/ledger metadata may still use PostgreSQL JSONB.
    encoded = document if isinstance(document, str) else json.dumps(document, allow_nan=False)
    return model.model_validate_json(encoded)


_SAMPLE_VIEW = """
    SELECT s.ordinal, r.dataset_id, r.sample_id, r.sample_revision,
           r.sample_document, r.manifest_document, r.annotation_object_count,
           to_jsonb(m) AS registered_manifest
    FROM dataset_samples s
    JOIN dataset_sample_heads h USING (dataset_id, sample_id)
    JOIN dataset_sample_revisions r ON r.dataset_id=s.dataset_id AND r.sample_id=s.sample_id
    LEFT JOIN sample_asset_manifests m ON m.dataset_id=r.dataset_id
        AND m.sample_id=r.sample_id AND m.sample_revision=r.sample_revision
"""


class DatasetRepository:
    def __init__(self, *, settings: DatabaseSettings | None = None) -> None:
        if settings is not None and not isinstance(settings, DatabaseSettings):
            raise TypeError("settings must be a DatabaseSettings instance.")
        self.settings = load_database_settings() if settings is None else settings

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[Any]:
        try:
            with database_connection(self.settings, readonly=not write) as connection:
                yield connection
        except CommitOutcomeUnknownError as error:
            raise DatasetCommitOutcomeUnknownError("Catalogue commit requires reconciliation.") from error
        except DatabaseError as error:
            raise DatasetStorageError("Cannot read or write the dataset catalogue.") from error

    @staticmethod
    def _dataset(connection, dataset_id: str) -> Dataset:
        row = connection.execute("SELECT * FROM datasets WHERE dataset_id=%s", (dataset_id,)).fetchone()
        if row is None:
            raise DatasetNotFoundError(dataset_id)
        return DatasetRepository._decode_dataset(row)

    @staticmethod
    def _decode_dataset(row) -> Dataset:
        try:
            return _decode(Dataset, {
                **{key: value for key, value in row.items() if key != "created_at"},
                "created_at": row["created_at"].isoformat(),
            })
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise DatasetStorageError("A stored dataset failed validation.") from error

    @staticmethod
    def _publication(row) -> SamplePublication:
        try:
            manifest = SampleAssetManifestRepository._decode(row["registered_manifest"])
            saved_identity = _decode(SampleAssetManifest, row["manifest_document"])
            if manifest != saved_identity:
                raise ValueError("The registered manifest no longer matches its immutable catalogue identity.")
            sample = _decode(Sample, row["sample_document"])
            if (sample.dataset_id, sample.sample_id, sample.revision) != (
                row["dataset_id"], row["sample_id"], row["sample_revision"]
            ):
                raise ValueError("Stored revision identity mismatch.")
            return SamplePublication(
                manifest=manifest, sample=sample, ordinal=row["ordinal"],
                annotation_object_count=row["annotation_object_count"],
            )
        except (KeyError, TypeError, ValueError, WorkflowStorageError) as error:
            raise DatasetStorageError("A stored catalogue revision failed validation.") from error

    @staticmethod
    def _sample(publication: SamplePublication) -> Sample:
        source = publication.manifest
        sample = publication.sample

        def link(kind: str, available: bool) -> AssetReference | None:
            if not available:
                return None
            return AssetReference(
                url=(f"/api/v1/datasets/{quote(source.dataset_id, safe='')}/samples/"
                     f"{quote(source.sample_id, safe='')}/assets/{kind}"
                     f"?revision={source.sample_revision}"),
                content_type="image/png", revision=source.sample_revision,
            )

        image = source.original_image.availability.value == "available"
        mask = source.raw_mask.availability.value == "available"
        return Sample.model_validate({**sample.model_dump(), "assets": SampleAssets(
            image=link("image", image), thumbnail=link("thumbnail", image),
            mask=link("mask", mask), mask_preview=link("mask_preview", mask),
        ).model_dump()})

    def get_dataset(self, dataset_id: str) -> Dataset:
        dataset_id = _identifier(dataset_id, "dataset_id")
        with self._connection() as connection:
            return self._dataset(connection, dataset_id)

    def list_datasets(self, *, limit: int = 20, offset: int = 0) -> DatasetPage:
        _pagination(limit, offset)
        with self._connection() as connection:
            total = connection.execute("SELECT count(*) AS total FROM datasets").fetchone()["total"]
            rows = connection.execute(
                'SELECT * FROM datasets ORDER BY created_at, dataset_id COLLATE "C" LIMIT %s OFFSET %s',
                (limit, offset),
            ).fetchall()
            items = [self._decode_dataset(row) for row in rows]
        return DatasetPage(items=items, limit=limit, offset=offset, total=total,
                           next_offset=offset + len(items) if offset + len(items) < total else None)

    def list_samples(self, dataset_id: str, *, limit: int = 20, offset: int = 0) -> SamplePage:
        dataset_id = _identifier(dataset_id, "dataset_id")
        _pagination(limit, offset)
        with self._connection() as connection:
            self._dataset(connection, dataset_id)
            total = connection.execute(
                "SELECT count(*) AS total FROM dataset_samples WHERE dataset_id=%s", (dataset_id,),
            ).fetchone()["total"]
            # LIMIT the identity page before fetching revision JSON/manifest metadata.
            rows = connection.execute(
                """WITH selected AS MATERIALIZED (
                       SELECT * FROM dataset_samples WHERE dataset_id=%s
                       ORDER BY ordinal, sample_id COLLATE "C" LIMIT %s OFFSET %s
                   ) """ + _SAMPLE_VIEW.replace("FROM dataset_samples s", "FROM selected s") + """
                   WHERE r.sample_revision=h.sample_revision
                   ORDER BY s.ordinal, s.sample_id COLLATE "C""", (dataset_id, limit, offset),
            ).fetchall()
            if len(rows) != min(limit, max(0, total - offset)):
                raise DatasetStorageError("The catalogue has an incomplete sample head.")
            items = [self._sample(self._publication(row)) for row in rows]
        return SamplePage(items=items, limit=limit, offset=offset, total=total,
                          next_offset=offset + len(items) if offset + len(items) < total else None)

    def _get_publication(self, connection, dataset_id, sample_id, revision) -> SamplePublication:
        self._dataset(connection, dataset_id)
        exists = connection.execute(
            "SELECT 1 FROM dataset_samples WHERE dataset_id=%s AND sample_id=%s",
            (dataset_id, sample_id),
        ).fetchone()
        if exists is None:
            raise DatasetSampleNotFoundError(dataset_id, sample_id)
        selected = "h.sample_revision" if revision is None else "%s"
        parameters = (dataset_id, sample_id) if revision is None else (dataset_id, sample_id, revision)
        row = connection.execute(
            _SAMPLE_VIEW + f" WHERE s.dataset_id=%s AND s.sample_id=%s AND r.sample_revision={selected}",
            parameters,
        ).fetchone()
        if row is None:
            if revision is None:
                raise DatasetStorageError("The catalogue current head is missing.")
            raise DatasetRevisionNotFoundError(dataset_id, sample_id, revision)
        return self._publication(row)

    @staticmethod
    def _read_key(dataset_id, sample_id, revision):
        _identifier(dataset_id, "dataset_id")
        _identifier(sample_id, "sample_id")
        if revision is not None:
            _integer(revision, "revision", 1, 2_147_483_647)

    def get_sample(self, dataset_id: str, sample_id: str, *, revision: int | None = None) -> Sample:
        self._read_key(dataset_id, sample_id, revision)
        with self._connection() as connection:
            return self._sample(self._get_publication(connection, dataset_id, sample_id, revision))

    def resolve_revision(
        self, dataset_id: str, sample_id: str, *, revision: int | None = None,
    ) -> CatalogueRevision:
        """Return an exact stored pin, never max(manifest) or a fake live source root."""
        self._read_key(dataset_id, sample_id, revision)
        with self._connection() as connection:
            saved = self._get_publication(connection, dataset_id, sample_id, revision)
            return CatalogueRevision(dataset_id=dataset_id, sample_id=sample_id,
                                     revision=saved.sample.revision, manifest=saved.manifest)

    @staticmethod
    def _lock_import(connection, claim: ImportClaim):
        job = connection.execute(
            "SELECT * FROM jobs WHERE job_id=%s FOR UPDATE", (claim.request.job_id,),
        ).fetchone()
        if job is None or job["kind"] != "dataset_import":
            raise JobClaimLostError("This is not a managed import claim.")
        request = connection.execute(
            "SELECT * FROM dataset_import_requests WHERE job_id=%s FOR UPDATE",
            (claim.request.job_id,),
        ).fetchone()
        dataset = connection.execute(
            "SELECT * FROM datasets WHERE dataset_id=%s FOR UPDATE", (claim.request.dataset_id,),
        ).fetchone()
        try:
            saved = ImportRequest.model_validate(dict(request))
        except (TypeError, ValueError) as error:
            raise DatasetStorageError("The private import ledger failed validation.") from error
        if (saved != claim.request or dataset is None
                or dataset["import_job_id"] != job["job_id"]
                or saved.dataset_id != dataset["dataset_id"]
                or job["resource_kind"] != "dataset" or job["resource_id"] != saved.dataset_id):
            raise JobClaimLostError("Import claim and durable resource identities disagree.")
        return job, saved, dataset

    @staticmethod
    def _authorize(connection, job, request, dataset, claim):
        now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
        if (job["claimed_by"] != claim.worker_id or job["claim_token"] != claim.claim_token
                or job["attempt"] != 1 or job["status"] not in {"running", "cancelling"}
                or job["lease_until"] is None or job["lease_until"] <= now):
            raise JobClaimLostError("The worker no longer owns this import attempt.")
        if job["status"] == "cancelling":
            raise JobCancellationError("Import cancellation has already won.")
        if dataset["status"] != "importing":
            raise JobClaimLostError("The dataset is no longer importing.")
        if request.deadline is None or request.deadline <= now:
            raise DatasetImportDeadlineError("The import processing deadline has expired.")
        return now

    @staticmethod
    def _insert_manifest(connection, manifest: SampleAssetManifest) -> None:
        # Reuse the existing repository's exact column encoding/validation, but
        # deliberately do not call save_manifest(), which opens another transaction.
        columns = ["dataset_id", "sample_id", "sample_revision"]
        for role in ("original_image", "raw_mask", "annotation_text"):
            columns.extend(f"{role}_{suffix}" for suffix in
                           ("availability", "storage_key", "content_type", "sha256", "size_bytes"))
        inserted = connection.execute(
            "INSERT INTO sample_asset_manifests (" + ",".join(columns) + ") VALUES ("
            + ",".join(["%s"] * len(columns))
            + ") ON CONFLICT DO NOTHING RETURNING dataset_id",
            SampleAssetManifestRepository._row_values(manifest),
        ).fetchone()
        if inserted is None:
            # Even an identical legacy/staged manifest is not silently adopted.
            raise DatasetPublicationConflictError("An unowned manifest already occupies this identity.")

    def _publish_import_sample(self, claim: ImportClaim, prepared: SamplePublication) -> Sample:
        """Internal Phase 2 seam: one acknowledged SQL transaction per new sample.

        Files must already be privately prepared by a bounded capture adapter.
        Never pass this method as an ordinary capture_sample save_manifest callback:
        rollback/unknown-commit cleanup must retain ownership of prepared files.
        """
        if not isinstance(claim, ImportClaim) or not isinstance(prepared, SamplePublication):
            raise TypeError("Expected ImportClaim and SamplePublication models.")
        claim = ImportClaim.model_validate(claim.model_dump())
        prepared = SamplePublication.model_validate(prepared.model_dump())
        if prepared.sample.dataset_id != claim.request.dataset_id or prepared.sample.revision != 1:
            raise ValueError("An import publishes only revision 1 of its own dataset.")
        bounds = claim.request.policy
        if len(prepared.sample.objects) > bounds.max_objects_per_sample:
            raise ValueError("Prepared object metadata exceeds its persisted policy.")
        size = prepared.sample.image_size
        if size is not None and size[0] * size[1] > bounds.max_image_pixels:
            raise ValueError("Prepared dimensions exceed the persisted image policy.")
        sizes = []
        for role in ("original_image", "raw_mask", "annotation_text"):
            entry = getattr(prepared.manifest, role)
            if entry.size_bytes is not None:
                budget = bounds.max_annotation_bytes if role == "annotation_text" else bounds.max_file_bytes
                if entry.size_bytes > budget:
                    raise ValueError("Prepared asset identity exceeds its persisted byte policy.")
                sizes.append(entry.size_bytes)
        if sum(sizes) > bounds.max_expanded_bytes:
            raise ValueError("Prepared sample assets exceed the persisted expanded-byte policy.")
        if len(prepared.model_dump_json().encode("utf-8")) > claim.request.policy.max_result_bytes:
            raise ValueError("Prepared sample metadata exceeds its persisted result bound.")
        with self._connection(write=True) as connection:
            job, request, dataset = self._lock_import(connection, claim)
            now = self._authorize(connection, job, request, dataset, claim)
            current = connection.execute(
                "SELECT ordinal FROM dataset_samples WHERE dataset_id=%s AND sample_id=%s FOR UPDATE",
                (prepared.sample.dataset_id, prepared.sample.sample_id),
            ).fetchone()
            if current is not None:
                saved = self._get_publication(connection, prepared.sample.dataset_id,
                                              prepared.sample.sample_id, 1)
                head = connection.execute(
                    "SELECT sample_revision FROM dataset_sample_heads WHERE dataset_id=%s AND sample_id=%s",
                    (prepared.sample.dataset_id, prepared.sample.sample_id),
                ).fetchone()
                if saved != prepared or head is None or head["sample_revision"] != 1:
                    raise DatasetPublicationConflictError("Publication replay differs from saved evidence.")
            else:
                if prepared.ordinal >= request.policy.max_samples:
                    raise ValueError("Sample ordinal exceeds the persisted policy.")
                if dataset["sample_count"] is not None and prepared.ordinal >= dataset["sample_count"]:
                    raise ValueError("Sample ordinal exceeds the discovered sample count.")
                ordinal = connection.execute(
                    "SELECT 1 FROM dataset_samples WHERE dataset_id=%s AND ordinal=%s",
                    (prepared.sample.dataset_id, prepared.ordinal),
                ).fetchone()
                if ordinal is not None:
                    raise DatasetPublicationConflictError("Another sample occupies this ordinal.")
                connection.execute(
                    "INSERT INTO dataset_samples (dataset_id,sample_id,ordinal) VALUES (%s,%s,%s)",
                    (prepared.sample.dataset_id, prepared.sample.sample_id, prepared.ordinal),
                )
                self._insert_manifest(connection, prepared.manifest)
                connection.execute(
                    """INSERT INTO dataset_sample_revisions
                       (dataset_id,sample_id,sample_revision,sample_document,manifest_document,
                        annotation_object_count,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (prepared.sample.dataset_id, prepared.sample.sample_id, 1,
                     prepared.sample.model_dump_json(),
                     Jsonb(prepared.manifest.model_dump(mode="json")), prepared.annotation_object_count, now),
                )
                connection.execute(
                    "INSERT INTO dataset_sample_heads (dataset_id,sample_id,sample_revision) VALUES (%s,%s,1)",
                    (prepared.sample.dataset_id, prepared.sample.sample_id),
                )
            # Any manifest/FK/unique-index wait above can consume a lease/deadline.
            # All locks remain held through commit; cancellation cannot interleave.
            self._authorize(connection, job, request, dataset, claim)
            result = self._sample(prepared)
        return result
