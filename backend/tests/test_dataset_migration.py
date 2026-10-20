"""Authored additive migration and relational-constraint regressions; unrun."""

import json

import pytest
from psycopg.types.json import Jsonb

from backend.app.storage import database
from backend.app.storage.database import DatabaseError, database_connection, initialize_database
from backend.app.storage.dataset_repository import DatasetRepository
from backend.tests.dataset_fixtures import (
    dataset_database_settings, empty_dataset_database_settings, prepared_sample,
    read_sql, seed_import, write_sql,
)

LEGACY_TABLES = (
    "jobs", "rulebook_versions", "rulebook_reviews", "audit_runs", "audit_samples",
    "audit_measurements", "findings", "finding_reviews", "sample_reviews", "sample_asset_manifests",
)


def snapshot(settings):
    return {table: read_sql(settings, f"SELECT to_jsonb(t) AS document FROM {table} t ORDER BY to_jsonb(t)::text")
            for table in LEGACY_TABLES}


def test_upgrade_keeps_existing_manifests_audits_findings_and_reviews_exact(
    empty_dataset_database_settings, tmp_path, monkeypatch,
):
    settings = empty_dataset_database_settings
    original_directory = database.MIGRATIONS_DIRECTORY
    baseline = tmp_path / "baseline_migrations"
    baseline.mkdir()
    # Author the upgrade scenario using the exact old 001..009 scripts.
    for source in sorted(original_directory.glob("[0-9][0-9][0-9]_*.sql")):
        if int(source.name[:3]) <= 9:
            (baseline / source.name).write_bytes(source.read_bytes())
    with monkeypatch.context() as patch:
        patch.setattr(database, "MIGRATIONS_DIRECTORY", baseline)
        initialize_database(settings)
    with database_connection(settings) as connection:
        connection.execute("""INSERT INTO jobs
            (job_id,kind,status,progress,created_at,updated_at,resource_kind,resource_id)
            VALUES ('legacy_job','audit','queued',%s,clock_timestamp(),clock_timestamp(),'audit','legacy_audit')""",
            (Jsonb({"phase": "queued", "processed": 0, "total": 1, "fraction": 0.0}),))
        connection.execute("""INSERT INTO rulebook_versions VALUES ('legacy_book',2,'confirmed','{}')""")
        connection.execute("""INSERT INTO rulebook_reviews VALUES
            ('legacy_book',2,'legacy_reviewer','confirm',clock_timestamp())""")
        original = prepared_sample(dataset_id="legacy_dataset").manifest
        DatasetRepository._insert_manifest(connection, original)
        connection.execute("""INSERT INTO audit_runs
            (audit_id,job_id,dataset_id,dataset_root,rulebook_id,rulebook_version,status,request,sample_count,
             created_at,updated_at,source_mode) VALUES
            ('legacy_audit','legacy_job','legacy_dataset','legacy-opaque-root','legacy_book',2,'queued','{}',1,
             clock_timestamp(),clock_timestamp(),'snapshot')""")
        connection.execute("""INSERT INTO audit_samples
            (audit_id,sample_id,sample_revision,ordinal,status,manifest_dataset_id)
            VALUES ('legacy_audit','sample_a',1,0,'completed','legacy_dataset')""")
        connection.execute("""INSERT INTO audit_measurements VALUES
            ('legacy_audit','sample_a',1,'{"review_status":"pending_review"}',clock_timestamp())""")
        connection.execute("""INSERT INTO findings
            (finding_id,audit_id,sample_id,sample_revision,rulebook_id,rulebook_version,rule_id,outcome,
             document,created_at) VALUES
            ('legacy_finding','legacy_audit','sample_a',1,'legacy_book',2,'rule_1','pass','{}',clock_timestamp())""")
        connection.execute("""INSERT INTO finding_reviews VALUES
            ('legacy_finding',1,'accepted','legacy_reviewer','preserve',clock_timestamp())""")
        connection.execute("""INSERT INTO sample_reviews VALUES
            ('legacy_audit','sample_a',1,1,'accepted','legacy_reviewer','preserve',clock_timestamp())""")
    before = snapshot(settings)
    initialize_database(settings)
    assert snapshot(settings) == before
    for table in ("datasets", "dataset_import_requests", "dataset_samples",
                  "dataset_sample_revisions", "dataset_sample_heads"):
        assert read_sql(settings, f"SELECT count(*) AS n FROM {table}") == [{"n": 0}]
    assert read_sql(settings, "SELECT version FROM schema_migrations ORDER BY version") == [
        {"version": number} for number in range(1, 11)
    ]
    initialize_database(settings)
    assert snapshot(settings) == before


def test_dangling_sample_or_head_cannot_commit(dataset_database_settings):
    settings = dataset_database_settings
    seed_import(settings)
    with pytest.raises(DatabaseError):
        write_sql(settings, "INSERT INTO dataset_samples VALUES ('dataset_a','dangling',0)")
    with pytest.raises(DatabaseError):
        write_sql(settings, "INSERT INTO dataset_sample_heads VALUES ('dataset_a','missing',1)")
    assert read_sql(settings, "SELECT * FROM dataset_samples") == []
    assert read_sql(settings, "SELECT * FROM dataset_sample_heads") == []


def test_catalogue_sample_and_revision_are_append_only(dataset_database_settings):
    settings = dataset_database_settings
    _, _, claim = seed_import(settings)
    DatasetRepository(settings=settings)._publish_import_sample(claim, prepared_sample())
    for statement in (
        "UPDATE dataset_sample_revisions SET annotation_object_count=0",
        "DELETE FROM dataset_sample_revisions",
        "UPDATE dataset_samples SET ordinal=1",
        "DELETE FROM dataset_samples",
        "UPDATE dataset_import_requests SET archive_key='other/archive.zip'",
        "UPDATE dataset_import_requests SET deadline=deadline+interval '1 second'",
        "DELETE FROM dataset_import_requests",
        "UPDATE datasets SET name='different'",
        "DELETE FROM datasets",
        "UPDATE dataset_sample_heads SET sample_revision=1",
        "DELETE FROM dataset_sample_heads",
    ):
        with pytest.raises(DatabaseError):
            write_sql(settings, statement)


def test_wrong_manifest_identity_cannot_satisfy_revision_fk(dataset_database_settings):
    settings = dataset_database_settings
    _, _, claim = seed_import(settings)
    original = prepared_sample()
    repository = DatasetRepository(settings=settings)
    repository._publish_import_sample(claim, original)
    with pytest.raises(DatabaseError):
        with database_connection(settings) as connection:
            document = original.sample.model_dump(mode="json")
            document["revision"] = 2
            manifest_document = original.manifest.model_dump(mode="json")
            manifest_document["sample_revision"] = 2
            connection.execute("""INSERT INTO dataset_sample_revisions VALUES
                ('dataset_a','sample_a',2,%s,%s,NULL,clock_timestamp())""",
                (json.dumps(document, allow_nan=False), Jsonb(manifest_document)))
    assert repository.get_sample("dataset_a", "sample_a").revision == 1


def test_dataset_cannot_commit_without_its_private_ledger(dataset_database_settings):
    settings = dataset_database_settings
    with pytest.raises(DatabaseError):
        with database_connection(settings) as connection:
            connection.execute("""INSERT INTO jobs
                (job_id,kind,status,progress,created_at,updated_at,resource_kind,resource_id)
                VALUES ('orphan_job','dataset_import','queued',%s,clock_timestamp(),clock_timestamp(),'dataset','orphan')""",
                (Jsonb({"phase": "queued", "processed": 0, "total": None, "fraction": None}),))
            connection.execute("""INSERT INTO datasets
                (dataset_id,name,format,status,import_job_id,created_at)
                VALUES ('orphan','Synthetic','penn_fudan','importing','orphan_job',clock_timestamp())""")
    assert read_sql(settings, "SELECT * FROM jobs") == []
    assert read_sql(settings, "SELECT * FROM datasets") == []
