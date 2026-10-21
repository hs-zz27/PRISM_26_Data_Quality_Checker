"""Opt-in isolated PostgreSQL regressions. Authored only; do not execute here."""
from contextlib import contextmanager
from datetime import timedelta

from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

from backend.app.core.export import AuditNotExportable, AuditNotReady, ExportOutcomeUnknown, ExportNotReady
from backend.app.models.audit import BatchAuditRequest
from backend.app.models.export import ExportPolicy, ExportRequest, Artifact, PublishedArtifacts
from backend.app.models.job import ApiError, Job, JobKind, ResultReference
from backend.app.models.review import SampleReview
from backend.app.storage.database import CommitOutcomeUnknownError, DatabaseError, database_connection
from backend.app.storage import export_repository as module
from backend.app.storage.export_repository import ExportRepository
from backend.app.storage.workflow_repository import (
    AuditRepository, JobRepository, JobClaim, JobClaimLostError, SampleAssetManifestRepository,
    SampleReviewRepository,
)
from backend.tests.dataset_fixtures import empty_dataset_database_settings, dataset_database_settings
from backend.tests.export_support import synthetic


def seed(settings, tmp_path, *, decisions=('accepted',), status='completed', images=True, missing=None):
    frozen, _, _ = synthetic(tmp_path, decisions=decisions, include_images=images, missing=missing)
    manifests = {s.entry.sample_id: s.manifest for s in frozen.samples}
    assets = SampleAssetManifestRepository(settings=settings)
    for manifest in manifests.values():
        assets.save_manifest(manifest)
    request = BatchAuditRequest(dataset_id='dataset_demo', dataset_root='unused-private-legacy-field',
                                rulebook_id='rulebook_demo', rulebook_version=3, source_mode='snapshot',
                                sample_ids=list(manifests), sample_revisions={name: 2 for name in manifests})
    audit, job = AuditRepository(settings=settings).start_audit(request, sample_manifests=manifests)
    result = ResultReference(kind='audit', id=audit.audit_id, version=None, href=f'/api/v1/audits/{audit.audit_id}')
    with database_connection(settings) as connection:
        now = connection.execute('SELECT clock_timestamp() AS now').fetchone()['now']
        connection.execute("UPDATE audit_samples SET status='completed' WHERE audit_id=%s", (audit.audit_id,))
        connection.execute("UPDATE audit_runs SET status=%s,processed_count=sample_count WHERE audit_id=%s", (status, audit.audit_id))
        connection.execute(
            """UPDATE jobs SET status=%s,result=%s,error=%s,claim_token=%s,lease_until=%s,updated_at=%s
               WHERE job_id=%s""",
            (status, Jsonb(result.model_dump(mode='json')) if status == 'completed' else None,
             Jsonb(ApiError(code='AUDIT_FAILED', message='Synthetic audit failure.', request_id='test').model_dump()) if status == 'failed' else None,
             'synthetic' if status in {'running', 'cancelling'} else None,
             now + timedelta(seconds=300) if status in {'running', 'cancelling'} else None, now, job.job_id),
        )
    reviews = SampleReviewRepository(settings=settings)
    for sample, decision in zip(frozen.samples, decisions):
        reviews.save_sample_review(SampleReview(
            audit_id=audit.audit_id, sample_id=sample.entry.sample_id, sample_revision=2,
            revision=1, decision=decision, reviewer_id='reviewer_a', comment='Synthetic review.', updated_at=now,
        ), expected_review_version=0)
    return audit, reviews


def request(audit, *, selection='accepted_only', images=True):
    return ExportRequest(audit_id=audit.audit_id, format='penn_fudan', selection=selection, include_images=images)


def artifacts(claim):
    return PublishedArtifacts(
        archive=Artifact(key=claim.claim_token + '/archive.zip', size_bytes=1, sha256='a' * 64),
        manifest=Artifact(key=claim.claim_token + '/manifest.json', size_bytes=1, sha256='b' * 64),
    )


@pytest.mark.parametrize('status', ['completed', 'failed', 'cancelled'])
def test_terminal_audits_and_full_ordered_frozen_view(dataset_database_settings, tmp_path, status):
    settings = dataset_database_settings
    audit, reviews = seed(settings, tmp_path, decisions=('accepted', 'rejected', 'pending_review', 'needs_correction'), status=status)
    repository = ExportRepository(settings=settings)
    started = repository.enqueue(request(audit, selection='all_reviewed'), policy=ExportPolicy())
    assert started.export.included_count is None and started.export.download_url is None
    frozen = repository.read_frozen(started.export.export_id)
    assert [e.ordinal for e in frozen.manifest.entries] == [0, 1, 2, 3]
    assert [e.included for e in frozen.manifest.entries] == [True, True, False, False]
    assert frozen.samples[0].entry.sample_revision == 2 and frozen.samples[0].entry.review_revision == 1
    assert frozen.samples[1].entry.review_decision.value == 'rejected'
    page = repository.list_exports()
    assert page.total == 1 and page.items[0] == repository.get_export(started.export.export_id)
    assert repository.list_exports(offset=100).items == []
    with pytest.raises(ExportNotReady):
        repository.completed_artifacts(started.export.export_id)
    # Reopening is frozen only for a NEW export.
    old = reviews.get_sample_review(audit.audit_id, 'sample_0', 2)
    reviews.save_sample_review(SampleReview(**{**old.model_dump(), 'revision': 2, 'decision': 'pending_review'}), expected_review_version=1)
    assert repository.read_frozen(started.export.export_id) == frozen
    second = repository.enqueue(request(audit), policy=ExportPolicy())
    assert not repository.read_frozen(second.export.export_id).manifest.entries[0].included


@pytest.mark.parametrize('status', ['queued', 'running', 'cancelling'])
def test_active_audits_are_not_ready(dataset_database_settings, tmp_path, status):
    audit, _ = seed(dataset_database_settings, tmp_path, status=status)
    with pytest.raises(AuditNotReady):
        ExportRepository(settings=dataset_database_settings).enqueue(request(audit), policy=ExportPolicy())


def test_legacy_audit_is_not_recaptured(dataset_database_settings, tmp_path):
    audit, _ = seed(dataset_database_settings, tmp_path)
    with database_connection(dataset_database_settings) as connection:
        connection.execute("UPDATE audit_runs SET source_mode='legacy' WHERE audit_id=%s", (audit.audit_id,))
    with pytest.raises(AuditNotExportable):
        ExportRepository(settings=dataset_database_settings).enqueue(request(audit), policy=ExportPolicy())


@pytest.mark.parametrize('images,expected', [(True, False), (False, True)])
def test_image_requirement_is_frozen(dataset_database_settings, tmp_path, images, expected):
    audit, _ = seed(dataset_database_settings, tmp_path, missing={(0, 'original_image'): 'missing'})
    repository = ExportRepository(settings=dataset_database_settings)
    result = repository.enqueue(request(audit, images=images), policy=ExportPolicy())
    entry = repository.read_frozen(result.export.export_id).manifest.entries[0]
    assert entry.included is expected


def test_accepted_failed_sample_is_not_machine_vetoed(dataset_database_settings, tmp_path):
    audit, _ = seed(dataset_database_settings, tmp_path)
    with database_connection(dataset_database_settings) as connection:
        connection.execute("UPDATE audit_samples SET status='failed' WHERE audit_id=%s", (audit.audit_id,))
    repository = ExportRepository(settings=dataset_database_settings)
    result = repository.enqueue(request(audit), policy=ExportPolicy())
    assert repository.read_frozen(result.export.export_id).manifest.entries[0].included


def test_review_write_between_selection_queries_uses_one_snapshot(dataset_database_settings, tmp_path, monkeypatch):
    settings = dataset_database_settings
    audit, reviews = seed(settings, tmp_path)
    real_connection = module.database_connection
    changed = False
    class View:
        def __init__(self, connection):
            self.connection = connection
        def execute(self, sql, parameters=()):
            nonlocal changed
            result = self.connection.execute(sql, parameters)
            if 'SELECT * FROM audit_runs' in sql and not changed:
                changed = True
                old = reviews.get_sample_review(audit.audit_id, 'sample_0', 2)
                reviews.save_sample_review(SampleReview(**{**old.model_dump(), 'revision': 2, 'decision': 'rejected'}), expected_review_version=1)
            return result
    @contextmanager
    def connection(settings, *, readonly=False):
        with real_connection(settings, readonly=readonly) as current:
            yield View(current) if readonly else current
    monkeypatch.setattr(module, 'database_connection', connection)
    repository = ExportRepository(settings=settings)
    result = repository.enqueue(request(audit), policy=ExportPolicy())
    frozen = repository.read_frozen(result.export.export_id)
    assert changed and frozen.manifest.entries[0].included
    assert frozen.manifest.entries[0].review_revision == 1
    assert reviews.get_sample_review(audit.audit_id, 'sample_0', 2).revision == 2


def test_unknown_acceptance_commit_is_not_retried(dataset_database_settings, tmp_path, monkeypatch):
    audit, _ = seed(dataset_database_settings, tmp_path)
    real_connection = module.database_connection
    @contextmanager
    def connection(settings, *, readonly=False):
        with real_connection(settings, readonly=readonly) as current:
            yield current
        if not readonly:
            raise CommitOutcomeUnknownError('Synthetic lost acknowledgement.')
    monkeypatch.setattr(module, 'database_connection', connection)
    repository = ExportRepository(settings=dataset_database_settings)
    with pytest.raises(ExportOutcomeUnknown):
        repository.enqueue(request(audit), policy=ExportPolicy())
    monkeypatch.setattr(module, 'database_connection', real_connection)
    assert repository.list_exports().total == 1


def test_enqueue_rollback_leaves_no_partial_job_or_export(dataset_database_settings, tmp_path, monkeypatch):
    audit, _ = seed(dataset_database_settings, tmp_path)
    real_connection = module.database_connection
    class View:
        def __init__(self, connection):
            self.connection = connection
        def execute(self, sql, parameters=()):
            result = self.connection.execute(sql, parameters)
            if 'INSERT INTO reviewed_export_inputs' in sql:
                raise RuntimeError('Synthetic known rollback.')
            return result
    @contextmanager
    def connection(settings, *, readonly=False):
        with real_connection(settings, readonly=readonly) as current:
            yield current if readonly else View(current)
    monkeypatch.setattr(module, 'database_connection', connection)
    repository = ExportRepository(settings=dataset_database_settings)
    with pytest.raises(RuntimeError):
        repository.enqueue(request(audit), policy=ExportPolicy())
    monkeypatch.setattr(module, 'database_connection', real_connection)
    with real_connection(dataset_database_settings, readonly=True) as current:
        assert current.execute('SELECT count(*) AS n FROM reviewed_exports').fetchone()['n'] == 0
        assert current.execute('SELECT count(*) AS n FROM reviewed_export_inputs').fetchone()['n'] == 0
        assert current.execute("SELECT count(*) AS n FROM jobs WHERE kind='export'").fetchone()['n'] == 0


def test_generic_execution_cannot_mutate_managed_export(dataset_database_settings, tmp_path):
    settings = dataset_database_settings
    audit, _ = seed(settings, tmp_path)
    repository = ExportRepository(settings=settings)
    result = repository.enqueue(request(audit), policy=ExportPolicy())
    jobs = JobRepository(settings=settings)
    assert jobs.claim_next_job(worker_id='generic', kind=JobKind.EXPORT) is None
    with pytest.raises(ValueError):
        jobs.claim_job(result.job.job_id, worker_id='generic')
    with pytest.raises(ValueError):
        jobs.create_job(Job.queued(job_id='forbidden', kind=JobKind.EXPORT, total=0))
    claim = repository.claim_next(worker_id='export_worker')
    generic = JobClaim(job=claim.job, worker_id=claim.worker_id, claim_token=claim.claim_token,
                       lease_until=claim.deadline, resource_kind='export', resource_id=result.export.export_id)
    mutations = [lambda: jobs.renew_claim(generic), lambda: jobs.update_progress(generic, claim.job.progress),
                 lambda: jobs.complete_job(generic, ResultReference(kind='export', id=result.export.export_id,
                                                                  href=f'/api/v1/exports/{result.export.export_id}')),
                 lambda: jobs.fail_job(generic, ApiError(code='X', message='Synthetic.', request_id='test')),
                 lambda: jobs.finalize_cancellation(generic)]
    for mutation in mutations:
        with pytest.raises(ValueError):
            mutation()
    assert jobs.recover_stale_jobs() == []
    # Existing public dispatch path updates job and export in ONE transaction.
    cancelled = AuditRepository(settings=settings).cancel_job(result.job.job_id)
    assert cancelled.status.value == 'cancelling'
    assert repository.get_export(result.export.export_id).status.value == 'cancelling'
    assert repository.finish(claim, artifacts=artifacts(claim)).status.value == 'cancelled'
    assert repository.get_export(result.export.export_id).download_url is None


def test_stale_attempt_is_failed_not_requeued(dataset_database_settings, tmp_path):
    settings = dataset_database_settings
    audit, _ = seed(settings, tmp_path)
    repository = ExportRepository(settings=settings)
    result = repository.enqueue(request(audit), policy=ExportPolicy())
    claim = repository.claim_next(worker_id='worker')
    with database_connection(settings) as connection:
        connection.execute("UPDATE jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE job_id=%s", (result.job.job_id,))
    assert repository.recover_stale()[0].status.value == 'failed'
    assert repository.claim_next(worker_id='replacement') is None
    with pytest.raises(JobClaimLostError):
        repository.finish(claim, artifacts=artifacts(claim))


def test_newer_catalogue_head_does_not_replace_old_audited_pin(dataset_database_settings, tmp_path):
    from backend.app.models.dataset_import import SamplePublication
    from backend.tests.dataset_fixtures import seed_import, prepared_sample, seed_historical_revision
    settings = dataset_database_settings
    audit, _ = seed(settings, tmp_path)
    manifest = SampleAssetManifestRepository(settings=settings).get_manifest('dataset_demo', 'sample_0', 2)
    seed_import(settings, dataset_id='dataset_demo', started=False)
    example = prepared_sample('dataset_demo', 'sample_0', revision=2)
    publication = SamplePublication(manifest=manifest, sample=example.sample,
                                    ordinal=0, annotation_object_count=None)
    with database_connection(settings) as connection:
        connection.execute("INSERT INTO dataset_samples (dataset_id,sample_id,ordinal) VALUES ('dataset_demo','sample_0',0)")
        connection.execute(
            """INSERT INTO dataset_sample_revisions (dataset_id,sample_id,sample_revision,sample_document,
               manifest_document,annotation_object_count,created_at) VALUES (%s,%s,2,%s,%s,NULL,clock_timestamp())""",
            ('dataset_demo', 'sample_0', example.sample.model_dump_json(), Jsonb(manifest.model_dump(mode='json'))))
        connection.execute("INSERT INTO dataset_sample_heads VALUES ('dataset_demo','sample_0',2)")
    seed_historical_revision(settings, publication, revision=3, advance_head=True)
    repository = ExportRepository(settings=settings)
    result = repository.enqueue(request(audit), policy=ExportPolicy())
    pin = repository.read_frozen(result.export.export_id).samples[0]
    assert pin.manifest == manifest and pin.entry.sample_revision == 2 and pin.entry.included


def test_queued_cancel_is_coupled_and_terminal_cancel_is_conflict(dataset_database_settings, tmp_path):
    from backend.app.storage.workflow_repository import JobAlreadyFinishedError
    settings = dataset_database_settings
    audit, _ = seed(settings, tmp_path)
    repository = ExportRepository(settings=settings)
    result = repository.enqueue(request(audit), policy=ExportPolicy())
    jobs = AuditRepository(settings=settings)
    assert jobs.cancel_job(result.job.job_id).status.value == 'cancelled'
    resource = repository.get_export(result.export.export_id)
    assert resource.status.value == 'cancelled' and resource.included_count is None
    with pytest.raises(JobAlreadyFinishedError):
        jobs.cancel_job(result.job.job_id)
    assert repository.read_frozen(result.export.export_id).manifest.entries[0].included


def test_publication_job_and_artifact_metadata_roll_back_together(dataset_database_settings, tmp_path, monkeypatch):
    settings = dataset_database_settings
    audit, _ = seed(settings, tmp_path)
    repository = ExportRepository(settings=settings)
    result = repository.enqueue(request(audit), policy=ExportPolicy())
    claim = repository.claim_next(worker_id='worker')
    real_connection = module.database_connection
    class View:
        def __init__(self, connection):
            self.connection = connection
        def execute(self, sql, parameters=()):
            result = self.connection.execute(sql, parameters)
            if 'UPDATE jobs SET status=%s,progress=' in sql:
                raise RuntimeError('Synthetic publication rollback.')
            return result
    @contextmanager
    def connection(settings, *, readonly=False):
        with real_connection(settings, readonly=readonly) as current:
            yield current if readonly else View(current)
    monkeypatch.setattr(module, 'database_connection', connection)
    with pytest.raises(RuntimeError):
        repository.finish(claim, artifacts=artifacts(claim))
    monkeypatch.setattr(module, 'database_connection', real_connection)
    resource = repository.get_export(result.export.export_id)
    assert resource.status.value == 'running' and resource.included_count is None and resource.download_url is None


def test_renewal_never_changes_original_hard_deadline(dataset_database_settings, tmp_path):
    settings = dataset_database_settings
    audit, _ = seed(settings, tmp_path)
    repository = ExportRepository(settings=settings)
    repository.enqueue(request(audit), policy=ExportPolicy(max_seconds=20))
    claim = repository.claim_next(worker_id='worker')
    repository.heartbeat(claim, processed=0)
    with database_connection(settings, readonly=True) as connection:
        row = connection.execute(
            'SELECT j.lease_until,i.started_at,i.deadline FROM jobs j '
            'JOIN reviewed_export_inputs i ON i.export_id=j.resource_id WHERE j.job_id=%s',
            (claim.job.job_id,),
        ).fetchone()
    assert row['deadline'] == claim.deadline
    assert row['deadline'] == row['started_at'] + timedelta(seconds=20)
    assert row['lease_until'] <= row['deadline']


def test_stale_cancelling_is_cancelled_and_not_replayed(dataset_database_settings, tmp_path):
    settings = dataset_database_settings
    audit, _ = seed(settings, tmp_path)
    repository = ExportRepository(settings=settings)
    started = repository.enqueue(request(audit), policy=ExportPolicy())
    claim = repository.claim_next(worker_id='worker')
    jobs = AuditRepository(settings=settings)
    assert jobs.cancel_job(started.job.job_id).status.value == 'cancelling'
    assert jobs.cancel_job(started.job.job_id).status.value == 'cancelling'
    with database_connection(settings) as connection:
        connection.execute("UPDATE jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE job_id=%s",
                           (started.job.job_id,))
    assert repository.recover_stale()[0].status.value == 'cancelled'
    assert repository.claim_next(worker_id='replacement') is None
    with pytest.raises(JobClaimLostError):
        repository.heartbeat(claim)


def test_replaced_token_cannot_publish_progress_or_failure(dataset_database_settings, tmp_path):
    settings = dataset_database_settings
    audit, _ = seed(settings, tmp_path)
    repository = ExportRepository(settings=settings)
    repository.enqueue(request(audit), policy=ExportPolicy())
    claim = repository.claim_next(worker_id='worker')
    with database_connection(settings) as connection:
        connection.execute("UPDATE jobs SET claim_token=%s WHERE job_id=%s", ('f' * 32, claim.job.job_id))
    for mutation in (lambda: repository.heartbeat(claim, processed=1),
                     lambda: repository.finish(claim, artifacts=artifacts(claim)),
                     lambda: repository.finish(claim, code='EXPORT_FAILED')):
        with pytest.raises(JobClaimLostError):
            mutation()


def test_zero_inclusions_publish_coherent_empty_progress(dataset_database_settings, tmp_path):
    settings = dataset_database_settings
    audit, _ = seed(settings, tmp_path, decisions=('pending_review',))
    repository = ExportRepository(settings=settings)
    started = repository.enqueue(request(audit), policy=ExportPolicy())
    assert started.job.progress.total == 0 and started.job.progress.fraction == 1.0
    claim = repository.claim_next(worker_id='worker')
    finished = repository.finish(claim, artifacts=artifacts(claim))
    assert finished.progress.processed == finished.progress.total == 0
    assert finished.progress.fraction == 1.0
    resource = repository.get_export(started.export.export_id)
    assert resource.included_count == 0 and resource.excluded_count == 1


def test_collection_validates_frozen_input_instead_of_advertising_corruption(tmp_path, monkeypatch):
    from backend.app.core.export import ExportDependencyError
    frozen, _, _ = synthetic(tmp_path)
    class Cursor:
        def __init__(self, value):
            self.value = value
        def fetchone(self):
            return self.value
        def fetchall(self):
            return self.value
    class Connection:
        def execute(self, sql, parameters=()):
            if 'count(*)' in sql:
                return Cursor({'n': 1})
            if 'SELECT export_id FROM reviewed_exports' in sql:
                return Cursor([{'export_id': frozen.manifest.export_id}])
            if 'SELECT * FROM reviewed_exports' in sql:
                return Cursor({'export_id': frozen.manifest.export_id})
            if 'SELECT * FROM reviewed_export_inputs' in sql:
                return Cursor({'frozen': {'not_a_frozen_export': True}})
            raise AssertionError('Unexpected query in corrupt-input test.')
    @contextmanager
    def connection(*, write=False):
        assert not write
        yield Connection()
    repository = object.__new__(ExportRepository)
    monkeypatch.setattr(repository, '_connection', connection)
    with pytest.raises(ExportDependencyError):
        repository.list_exports()


def test_completed_resource_rejects_incomplete_job_progress(tmp_path):
    from backend.app.core.export import ExportDependencyError
    from backend.app.models.export import Export, PublishedArtifacts, Artifact
    from backend.app.models.job import Progress
    from backend.tests.export_support import NOW
    frozen, _, _ = synthetic(tmp_path)
    identity = frozen.manifest.export_id
    resource = Export(audit_id='audit_demo', format='penn_fudan', selection='accepted_only',
                      include_images=True, export_id=identity, job_id='job_test', status='completed',
                      included_count=1, excluded_count=0, created_at=NOW,
                      download_url=f'/api/v1/exports/{identity}/download',
                      manifest_url=f'/api/v1/exports/{identity}/manifest')
    job = Job(job_id='job_test', kind='export', status='completed', created_at=NOW, updated_at=NOW,
              progress=Progress(phase='exporting', processed=0, total=1, fraction=0.0),
              result=ResultReference(kind='export', id=identity, href=f'/api/v1/exports/{identity}'))
    evidence = PublishedArtifacts(archive=Artifact(key='a' * 32 + '/archive.zip', size_bytes=1, sha256='b' * 64),
                                  manifest=Artifact(key='a' * 32 + '/manifest.json', size_bytes=1, sha256='c' * 64))
    row = dict(export_id=identity, audit_id='audit_demo', job_id='job_test', status='completed',
               request={name: getattr(resource, name) for name in ('audit_id','format','selection','include_images')},
               created_at=NOW, included_count=1, excluded_count=0, artifacts=evidence.model_dump(mode='json'))
    with pytest.raises(ExportDependencyError, match='progress is inconsistent'):
        ExportRepository._public(row, job)


def test_recovery_terminalizes_abandoned_missing_lease(dataset_database_settings, tmp_path):
    settings = dataset_database_settings
    audit, _ = seed(settings, tmp_path)
    repository = ExportRepository(settings=settings)
    started = repository.enqueue(request(audit), policy=ExportPolicy())
    repository.claim_next(worker_id='worker')
    # The normal write boundary rejects a missing lease for a running job.
    with pytest.raises(DatabaseError) as rejected:
        with database_connection(settings) as connection:
            connection.execute('UPDATE jobs SET lease_until=NULL,claimed_by=NULL,claim_token=NULL WHERE job_id=%s',
                               (started.job.job_id,))
    assert rejected.value.__cause__.sqlstate == '23514'
    assert repository.get_export(started.export.export_id).status.value == 'running'

    # Explicit corruption fixture, restricted to this test's disposable schema.
    # Preserve the token and worker so recovery specifically exercises a NULL
    # lease, rather than an unrelated missing-owner or expired-lease branch.
    with database_connection(settings) as connection:
        constraints = connection.execute(
            """SELECT conname,pg_get_constraintdef(oid) AS definition FROM pg_constraint
               WHERE conrelid='jobs'::regclass AND contype='c'
                 AND pg_get_constraintdef(oid) LIKE '%lease_until IS NOT NULL%'"""
        ).fetchall()
        assert len(constraints) == 1
        constraint = constraints[0]
        connection.execute(sql.SQL('ALTER TABLE jobs DROP CONSTRAINT {}').format(
            sql.Identifier(constraint['conname'])))
        connection.execute('UPDATE jobs SET lease_until=NULL WHERE job_id=%s', (started.job.job_id,))
        connection.execute(sql.SQL('ALTER TABLE jobs ADD CONSTRAINT {} {} NOT VALID').format(
            sql.Identifier(constraint['conname']), sql.SQL(constraint['definition'])))
    from backend.app.core.export import ExportDependencyError
    with pytest.raises(ExportDependencyError):
        repository.get_export(started.export.export_id)
    assert repository.recover_stale()[0].status.value == 'failed'
    assert repository.get_export(started.export.export_id).status.value == 'failed'
    with database_connection(settings) as connection:
        connection.execute(sql.SQL('ALTER TABLE jobs VALIDATE CONSTRAINT {}').format(
            sql.Identifier(constraint['conname'])))


def test_publication_waiting_for_job_lock_rechecks_expiry(dataset_database_settings, tmp_path):
    # Authored, opt-in timing regression; never run in the handoff environment.
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import time
    settings = dataset_database_settings
    audit, _ = seed(settings, tmp_path)
    repository = ExportRepository(settings=settings)
    repository.enqueue(request(audit), policy=ExportPolicy(max_seconds=2))
    claim = repository.claim_next(worker_id='worker')
    attempted = Event()
    class WaitingRepository(ExportRepository):
        def _lock(self, connection, job_id):
            attempted.set()
            return super()._lock(connection, job_id)
    waiter = WaitingRepository(settings=settings)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with database_connection(settings) as connection:
            connection.execute('SELECT * FROM jobs WHERE job_id=%s FOR UPDATE', (claim.job.job_id,)).fetchone()
            future = executor.submit(waiter.finish, claim, artifacts=artifacts(claim))
            assert attempted.wait(timeout=3)
            now = connection.execute('SELECT clock_timestamp() AS now').fetchone()['now']
            delay = max(0.0, (claim.deadline - now).total_seconds()) + 0.1
            assert delay <= 3.0
            time.sleep(delay)
        with pytest.raises(JobClaimLostError):
            future.result(timeout=10)
    assert repository.get_export(claim.frozen.manifest.export_id).status.value == 'running'
    assert repository.recover_stale()[0].status.value == 'failed'


def test_failed_export_without_processing_attempt_is_corrupt(dataset_database_settings, tmp_path):
    from backend.app.core.export import ExportDependencyError
    settings = dataset_database_settings
    audit, _ = seed(settings, tmp_path)
    repository = ExportRepository(settings=settings)
    started = repository.enqueue(request(audit), policy=ExportPolicy())
    error = ApiError(code='EXPORT_FAILED', message='Synthetic inconsistent stored state.', request_id='test')
    with database_connection(settings) as connection:
        connection.execute("UPDATE reviewed_exports SET status='failed' WHERE export_id=%s", (started.export.export_id,))
        connection.execute("UPDATE jobs SET status='failed',error=%s WHERE job_id=%s",
                           (Jsonb(error.model_dump(mode='json')), started.job.job_id))
    with pytest.raises(ExportDependencyError, match='allocation disagree'):
        repository.get_export(started.export.export_id)
    with pytest.raises(ExportDependencyError, match='allocation disagree'):
        repository.list_exports()


def test_saved_progress_fraction_must_agree_with_counts(dataset_database_settings, tmp_path):
    from backend.app.core.export import ExportDependencyError
    settings = dataset_database_settings
    audit, _ = seed(settings, tmp_path)
    repository = ExportRepository(settings=settings)
    started = repository.enqueue(request(audit), policy=ExportPolicy())
    with database_connection(settings) as connection:
        connection.execute('UPDATE jobs SET progress=%s WHERE job_id=%s',
                           (Jsonb({'phase': 'queued', 'processed': 0, 'total': 1, 'fraction': 0.5}),
                            started.job.job_id))
    with pytest.raises(ExportDependencyError, match='progress differs'):
        repository.get_export(started.export.export_id)
    with pytest.raises(ExportDependencyError, match='progress differs'):
        repository.list_exports()
