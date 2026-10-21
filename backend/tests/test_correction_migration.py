"""Opt-in additive 011 -> 012 upgrade; preserves prior rows, no fabricated history."""
from backend.app.storage import database
from backend.tests.dataset_fixtures import empty_dataset_database_settings,read_sql,seed_import


def test_upgrade_preserves_existing_import_and_leaves_new_ledgers_empty(empty_dataset_database_settings,tmp_path,monkeypatch):
    settings=empty_dataset_database_settings
    latest=database.MIGRATIONS_DIRECTORY
    before=tmp_path/'prior';before.mkdir()
    for source in latest.glob('[0-9][0-9][0-9]_*.sql'):
        if int(source.name[:3])<=11:
            (before/source.name).write_bytes(source.read_bytes())
    with monkeypatch.context() as patch:
        patch.setattr(database,'MIGRATIONS_DIRECTORY',before)
        database.initialize_database(settings)
    seed_import(settings,started=False)
    saved=read_sql(settings,'SELECT * FROM datasets ORDER BY dataset_id')
    jobs=read_sql(settings,'SELECT * FROM jobs ORDER BY job_id')
    database.initialize_database(settings)
    assert read_sql(settings,'SELECT * FROM datasets ORDER BY dataset_id')==saved
    assert read_sql(settings,'SELECT * FROM jobs ORDER BY job_id')==jobs
    assert [row['version'] for row in read_sql(settings,'SELECT version FROM schema_migrations ORDER BY version')]==list(range(1,13))
    for table in ('annotation_revision_provenance','correction_mask_uploads','audit_recheck_lineage'):
        assert read_sql(settings,'SELECT count(*) AS n FROM '+table)==[{'n':0}]
    database.initialize_database(settings)
