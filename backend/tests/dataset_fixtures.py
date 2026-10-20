"""Isolated PostgreSQL fixtures following the existing test_repository pattern.

No fixture creates a connection until explicitly invoked by a local pytest run.
This handoff does not execute these tests or migrations.
"""

from datetime import timedelta
from hashlib import sha256
import os
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
from pydantic import SecretStr
import pytest

from backend.app.core.config import DatabaseSettings
from backend.app.models.asset_manifest import AssetManifestEntry, SampleAssetManifest
from backend.app.models.dataset import Dataset, Sample, source_issue
from backend.app.models.dataset_import import ImportClaim, ImportPolicy, ImportRequest, SamplePublication
from backend.app.models.job import Job, JobKind
from backend.app.storage.database import database_connection, initialize_database
from backend.app.storage.workflow_repository import _decode_job, _job_row_values


@pytest.fixture
def empty_dataset_database_settings():
    conninfo = os.environ.get("PRISM_TEST_DATABASE_URL")
    if not conninfo:
        pytest.skip("Set PRISM_TEST_DATABASE_URL to run PostgreSQL integration tests.")
    schema = f"prism_dataset_{uuid4().hex}"
    with psycopg.connect(conninfo, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        yield DatabaseSettings(conninfo=SecretStr(make_conninfo(
            conninfo, options=f"-c search_path={schema}",
        )))
    finally:
        with psycopg.connect(conninfo, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def dataset_database_settings(empty_dataset_database_settings):
    initialize_database(empty_dataset_database_settings)
    return empty_dataset_database_settings


def read_sql(settings, statement, parameters=()):
    with database_connection(settings, readonly=True) as connection:
        return connection.execute(statement, parameters).fetchall()


def write_sql(settings, statement, parameters=()):
    with database_connection(settings) as connection:
        connection.execute(statement, parameters)


def seed_import(settings, dataset_id="dataset_a", *, started=True, now=None):
    """Test-only seeding, NOT an acceptance/worker implementation or public API."""
    with database_connection(settings) as connection:
        if now is None:
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
        job = Job.queued(job_id=f"job_{dataset_id}", kind=JobKind.DATASET_IMPORT, total=None, now=now)
        dataset = Dataset(dataset_id=dataset_id, name="Synthetic source", format="penn_fudan",
                          status="importing", sample_count=None, object_count=None,
                          import_job_id=job.job_id, created_at=now)
        policy = ImportPolicy(max_seconds=86_400, lease_seconds=300)
        request = ImportRequest(job_id=job.job_id, dataset_id=dataset_id,
                                archive_key=f"uploads/{uuid4().hex}/archive.zip",
                                archive_sha256=sha256(b"synthetic").hexdigest(), archive_size_bytes=9,
                                policy=policy, started_at=now if started else None,
                                deadline=now + timedelta(seconds=policy.max_seconds) if started else None)
        connection.execute(
            """INSERT INTO jobs (job_id,kind,status,progress,result,error,created_at,updated_at,
               resource_kind,resource_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'dataset',%s)""",
            _job_row_values(job) + (dataset_id,),
        )
        connection.execute(
            """INSERT INTO datasets (dataset_id,name,format,status,sample_count,object_count,
               import_job_id,created_at,issues) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (dataset_id, dataset.name, dataset.format.value, dataset.status.value, None, None,
             job.job_id, now, Jsonb([])),
        )
        connection.execute(
            """INSERT INTO dataset_import_requests (job_id,dataset_id,archive_key,archive_sha256,
               archive_size_bytes,policy,started_at,deadline) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (job.job_id, dataset_id, request.archive_key, request.archive_sha256,
             request.archive_size_bytes, Jsonb(policy.model_dump(mode="json")),
             request.started_at, request.deadline),
        )
        if started:
            lease = now + timedelta(seconds=300)
            connection.execute(
                """UPDATE jobs SET status='running',attempt=1,claimed_by='test_worker',
                   claim_token='test_token',lease_until=%s WHERE job_id=%s""", (lease, job.job_id),
            )
            row = connection.execute("SELECT * FROM jobs WHERE job_id=%s", (job.job_id,)).fetchone()
            claim = ImportClaim(job=_decode_job(row), request=request, worker_id="test_worker",
                                claim_token="test_token", lease_until=lease)
        else:
            claim = None
    return dataset, request, claim


def prepared_sample(dataset_id="dataset_a", sample_id="sample_a", *, ordinal=0, revision=1):
    """Synthetic readable-but-malformed bytes: retained evidence, not a PNG claim."""
    def entry(role):
        content = role.encode()
        return AssetManifestEntry(availability="available", storage_key=f"test/{uuid4().hex}/{role}",
                                  content_type="text/plain" if role == "annotation" else "image/png",
                                  sha256=sha256(content).hexdigest(), size_bytes=len(content))
    manifest = SampleAssetManifest(dataset_id=dataset_id, sample_id=sample_id,
                                   sample_revision=revision, original_image=entry("image"),
                                   raw_mask=AssetManifestEntry(availability="missing"),
                                   annotation_text=entry("annotation"))
    sample = Sample(dataset_id=dataset_id, sample_id=sample_id, revision=revision,
                    image_size=None, integrity_status="invalid",
                    objects=[{"object_id": -1, "label": "", "bbox": (-3, 5, -8, 0)},
                             {"object_id": -1, "label": "person", "bbox": (5, 5, 1, 1)}],
                    issues=[source_issue("missing_mask", field="mask"),
                            source_issue("invalid_image", field="image"),
                            source_issue("invalid_annotation", field="annotation")])
    return SamplePublication(manifest=manifest, sample=sample, ordinal=ordinal,
                             annotation_object_count=None)


def seed_historical_revision(settings, original, *, revision=2, advance_head=True):
    """Test-only future correction analogue, not a new revision writer in the app."""
    from backend.app.storage.dataset_repository import DatasetRepository
    raw = original.model_dump()
    raw["manifest"]["sample_revision"] = revision
    raw["sample"]["revision"] = revision
    changed = SamplePublication.model_validate(raw)
    with database_connection(settings) as connection:
        DatasetRepository._insert_manifest(connection, changed.manifest)
        connection.execute(
            """INSERT INTO dataset_sample_revisions
               (dataset_id,sample_id,sample_revision,sample_document,manifest_document,
                annotation_object_count,created_at) VALUES (%s,%s,%s,%s,%s,%s,clock_timestamp())""",
            (changed.sample.dataset_id, changed.sample.sample_id, revision,
             changed.sample.model_dump_json(), Jsonb(changed.manifest.model_dump(mode="json")),
             changed.annotation_object_count),
        )
        if advance_head:
            connection.execute(
                "UPDATE dataset_sample_heads SET sample_revision=%s WHERE dataset_id=%s AND sample_id=%s",
                (revision, changed.sample.dataset_id, changed.sample.sample_id),
            )
    return changed
