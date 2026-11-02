"""Authored opt-in real supervised rechecks, lineage and unchanged parent export evidence."""
from io import BytesIO
from threading import Event
from uuid import uuid4
import time

import pytest

from backend.app.core.config import AssetStorageSettings
from backend.app.core.correction import CorrectionError
from backend.app.core.export import ExportSettings
from backend.app.models.audit_submission import AuditRequest
from backend.app.models.correction import RecheckRequest
from backend.app.models.export import ExportPolicy, ExportRequest
from backend.app.models.job import JobKind, Progress, ApiError, ResultReference
from backend.app.models.review import SampleReview
from backend.app.services.audit_submission import AuditSubmissionService
from backend.app.services.batch_audit import BatchAuditService
from backend.app.services.export_archive import build_archive
from backend.app.services.export_files import create_attempt
from backend.app.storage.recheck_repository import RecheckRepository
from backend.app.storage.repository import RulebookRepository
from backend.app.storage.export_repository import ExportRepository
from backend.app.storage.workflow_repository import AuditRepository, JobRepository, SampleAssetManifestRepository, SampleReviewRepository
from backend.tests.test_audit_submission import rulebook
from backend.tests.test_correction_repository import request
from backend.tests.correction_fixtures import fixture
from backend.tests.dataset_fixtures import dataset_database_settings,empty_dataset_database_settings,read_sql,write_sql


def prepared(settings,tmp_path):
    repo,corrections,original=fixture(settings,tmp_path)
    books=RulebookRepository(settings=settings);books.save_rulebook(rulebook())
    audits=AuditRepository(settings=settings)
    manifests=SampleAssetManifestRepository(settings=settings,asset_settings=AssetStorageSettings(root=corrections.settings.asset_root))
    service=BatchAuditService(rulebook_repository=books,audit_repository=audits,manifest_repository=manifests,sample_max_seconds=30)
    parent=AuditSubmissionService(catalogue=repo,batch=service).start(AuditRequest(dataset_id='dataset_a',rulebook_id='book',rulebook_version=1))
    claim=audits.jobs.claim_next_job(worker_id='synthetic',kind=JobKind.AUDIT,lease_seconds=300)
    assert service.run_claimed_job(claim).status.value=='completed'
    return repo,corrections,parent,audits,service


def selection(revision=2):
    return RecheckRequest(sample_ids=['sample_a'],sample_revisions={'sample_a':revision},reviewer_id='reviewer')


def test_feedback_is_saved_before_work_and_not_inherited_by_later_rechecks(dataset_database_settings, tmp_path):
    repo, _, parent, audits, worker = prepared(dataset_database_settings, tmp_path)
    rechecks = RecheckRepository(settings=repo.settings)
    request = RecheckRequest(sample_ids=['sample_a'], sample_revisions={'sample_a': 1},
        reviewer_id='reviewer', feedback='Check the lower edge.', propose_correction=True)
    child = rechecks.start(parent.audit.audit_id, request)
    saved = audits.get_request(child.audit.audit_id)
    assert list(saved.reviewer_feedback) == ['sample_a']
    assert saved.reviewer_feedback['sample_a'].comment == request.feedback
    assert audits.get_request(parent.audit.audit_id).reviewer_feedback == {}
    assert repo.get_sample('dataset_a', 'sample_a').revision == 1
    claim = audits.jobs.claim_next_job(worker_id='synthetic', kind=JobKind.AUDIT, lease_seconds=300)
    assert worker.run_claimed_job(claim).status.value == 'completed'
    result = audits.get_measurements(child.audit.audit_id, 'sample_a', 1)
    assert result['reviewer_feedback']['comment'] == request.feedback
    assert result['correction_proposal']['status'] == 'unavailable'  # No pinned visual rule/model in this fixture.
    assert repo.get_sample('dataset_a', 'sample_a').revision == 1
    subsequent = rechecks.start(child.audit.audit_id, selection(1))
    assert audits.get_request(subsequent.audit.audit_id).reviewer_feedback == {}


def test_real_correct_recheck_fresh_review_and_byte_stable_parent_export(dataset_database_settings,tmp_path):
    settings=dataset_database_settings
    repo,corrections,parent,audits,worker=prepared(settings,tmp_path)
    reviews=SampleReviewRepository(settings=settings)
    reviews.save_sample_review(SampleReview(audit_id=parent.audit.audit_id,sample_id='sample_a',sample_revision=1,
        revision=1,decision='accepted',reviewer_id='reviewer',comment='Original evidence.',updated_at=parent.audit.created_at),expected_review_version=0)
    exports=ExportRepository(settings=settings)
    accepted=exports.enqueue(ExportRequest(audit_id=parent.audit.audit_id,format='penn_fudan',selection='accepted_only',include_images=True),policy=ExportPolicy())
    frozen=exports.read_frozen(accepted.export.export_id)
    config=ExportSettings(root=tmp_path/'exports',asset_root=corrections.settings.asset_root,import_root=tmp_path/'imports',policy=ExportPolicy())
    first=create_attempt(config.root,uuid4().hex)
    build_archive(frozen,settings=config,directory=first,stop_event=Event(),deadline_end=time.monotonic()+30,progress=lambda *_:None)
    old_archive=(first/'archive.zip').read_bytes()
    corrections.correct('dataset_a','sample_a',request())
    linked=RecheckRepository(settings=settings).start(parent.audit.audit_id,selection())
    assert linked.job.kind is JobKind.AUDIT and linked.parent_audit_id==parent.audit.audit_id
    lineage=read_sql(settings,'SELECT * FROM audit_recheck_lineage WHERE child_audit_id=%s',(linked.audit.audit_id,))[0]
    assert lineage['parent_audit_id']==parent.audit.audit_id and lineage['reviewer_id']=='reviewer'
    assert JobRepository(settings=settings).get_job(linked.job.job_id).status.value=='queued'
    assert audits.get_request(linked.audit.audit_id).sample_revisions=={'sample_a':2}
    assert (linked.audit.rulebook_id,linked.audit.rulebook_version)==('book',1)
    claim=audits.jobs.claim_next_job(worker_id='synthetic',kind=JobKind.AUDIT,lease_seconds=300)
    done=worker.run_claimed_job(claim)
    assert done.status.value=='completed' and done.result.href==f'/api/v1/audits/{linked.audit.audit_id}'
    assert reviews.get_sample_review(linked.audit.audit_id,'sample_a',2).decision.value=='pending_review'
    assert reviews.get_sample_review(parent.audit.audit_id,'sample_a',1).decision.value=='accepted'
    evidence=audits.get_measurements(linked.audit.audit_id,'sample_a',2)
    assert evidence is not None
    assert evidence['objects'][0]['annotation']['label']=='piéton'
    assert 'piéton' in evidence['annotation_text']
    assert audits.get_measurements(parent.audit.audit_id,'sample_a',1)['objects'][0]['annotation']['label']=='person'
    from backend.app.models.asset_manifest import AssetKind
    assert worker.manifest_repository.read_asset_bytes('dataset_a','sample_a',2,AssetKind.ANNOTATION_TEXT).decode('utf-8')==evidence['annotation_text']
    second=create_attempt(config.root,uuid4().hex)
    build_archive(exports.read_frozen(accepted.export.export_id),settings=config,directory=second,
        stop_event=Event(),deadline_end=time.monotonic()+30,progress=lambda *_:None)
    assert (second/'archive.zip').read_bytes()==old_archive
    child_export=exports.enqueue(ExportRequest(audit_id=linked.audit.audit_id,format='penn_fudan',selection='accepted_only',include_images=False),policy=ExportPolicy())
    assert not exports.read_frozen(child_export.export.export_id).manifest.entries[0].included


def test_stale_wrong_parent_source_and_queued_cancellation(dataset_database_settings,tmp_path):
    repo,corrections,parent,audits,_=prepared(dataset_database_settings,tmp_path)
    with pytest.raises(CorrectionError) as error:
        corrections.correct('dataset_a','sample_a',request(source_audit_id='unrelated'))
    assert error.value.code=='SOURCE_AUDIT_MISMATCH'
    corrections.correct('dataset_a','sample_a',request(source_audit_id=parent.audit.audit_id))
    linked_repo=RecheckRepository(settings=repo.settings)
    with pytest.raises(CorrectionError) as error:
        linked_repo.start(parent.audit.audit_id,selection(1))
    assert error.value.code=='STALE_SAMPLE_REVISION'
    linked=linked_repo.start(parent.audit.audit_id,selection())
    assert audits.cancel_job(linked.job.job_id).status.value=='cancelled'
    assert audits.get_audit(linked.audit.audit_id).status.value=='cancelled'
    assert audits.get_audit(parent.audit.audit_id).status.value=='completed'


def test_generic_recovery_and_terminal_mutations_cannot_detach_recheck(dataset_database_settings,tmp_path):
    repo,corrections,parent,audits,_=prepared(dataset_database_settings,tmp_path)
    corrections.correct('dataset_a','sample_a',request())
    linked=RecheckRepository(settings=repo.settings).start(parent.audit.audit_id,selection())
    jobs=JobRepository(settings=repo.settings)
    assert jobs.claim_next_job(worker_id='generic') is None
    with pytest.raises(ValueError):
        jobs.request_cancellation(linked.job.job_id)
    claim=jobs.claim_next_job(worker_id='audit',kind=JobKind.AUDIT,lease_seconds=300)
    for mutation in [lambda:jobs.update_progress(claim,Progress(phase='x',processed=0,total=1,fraction=0.0)),
        lambda:jobs.complete_job(claim,ResultReference(kind='audit',id=linked.audit.audit_id,href=f'/api/v1/audits/{linked.audit.audit_id}')),
        lambda:jobs.fail_job(claim,ApiError(code='FAILED',message='Synthetic.',request_id='test')),
        lambda:jobs.finalize_cancellation(claim)]:
        with pytest.raises(ValueError):mutation()
    # Mark audit as running using its real prepare seam before running cancellation.
    audits.prepare_claimed_audit(claim)
    assert audits.cancel_job(linked.job.job_id).status.value=='cancelling'
    write_sql(repo.settings,"UPDATE jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE job_id=%s",(linked.job.job_id,))
    assert jobs.recover_stale_jobs()==[]
    audits.recover_stale_jobs()
    assert jobs.get_job(linked.job.job_id).status.value=='cancelled'
    assert audits.get_audit(linked.audit.audit_id).status.value=='cancelled'


def test_parent_lifecycle_unknown_selection_and_stale_source_evidence(dataset_database_settings,tmp_path):
    repo,corrections,parent,audits,_=prepared(dataset_database_settings,tmp_path)
    corrections.correct('dataset_a','sample_a',request())
    with pytest.raises(CorrectionError) as error:
        corrections.correct('dataset_a','sample_a',request(revision=2,source_audit_id=parent.audit.audit_id))
    assert error.value.code=='SOURCE_AUDIT_MISMATCH'
    rechecks=RecheckRepository(settings=repo.settings)
    bad=RecheckRequest(sample_ids=['unknown'],sample_revisions={'unknown':1},reviewer_id='reviewer')
    with pytest.raises(CorrectionError) as error:rechecks.start(parent.audit.audit_id,bad)
    assert error.value.code=='VALIDATION_ERROR'
    child=rechecks.start(parent.audit.audit_id,selection())
    with pytest.raises(CorrectionError) as error:rechecks.start(child.audit.audit_id,selection())
    assert error.value.code=='AUDIT_NOT_READY'
    assert read_sql(repo.settings,'SELECT count(*) AS n FROM audit_recheck_lineage')==[{'n':1}]


def test_recheck_enqueue_rolls_back_if_lineage_insert_fails(dataset_database_settings,tmp_path,monkeypatch):
    from contextlib import contextmanager
    from backend.app.storage import correction_repository as module
    repo,corrections,parent,audits,_=prepared(dataset_database_settings,tmp_path)
    corrections.correct('dataset_a','sample_a',request())
    before=read_sql(repo.settings,'SELECT job_id FROM jobs ORDER BY job_id')
    real=module.database_connection
    class Proxy:
        def __init__(self,connection):self.connection=connection
        def execute(self,query,params=()):
            if 'INSERT INTO audit_recheck_lineage' in query:raise RuntimeError('Synthetic linkage failure')
            return self.connection.execute(query,params)
        def __getattr__(self,name):return getattr(self.connection,name)
    @contextmanager
    def connection(settings,*,readonly=False):
        with real(settings,readonly=readonly) as value:yield Proxy(value)
    monkeypatch.setattr(module,'database_connection',connection)
    with pytest.raises(RuntimeError):RecheckRepository(settings=repo.settings).start(parent.audit.audit_id,selection())
    assert read_sql(repo.settings,'SELECT job_id FROM jobs ORDER BY job_id')==before
    assert read_sql(repo.settings,'SELECT count(*) AS n FROM audit_recheck_lineage')==[{'n':0}]


def test_enqueue_holds_selected_head_until_commit(dataset_database_settings,tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    repo,corrections,parent,audits,_=prepared(dataset_database_settings,tmp_path)
    corrections.correct('dataset_a','sample_a',request())
    locked,release=Event(),Event()
    real=AuditRepository._start_audit_locked
    def pause(self,connection,batch,**kwargs):
        locked.set()
        if not release.wait(10):raise RuntimeError('Synthetic synchronization timeout')
        return real(self,connection,batch,**kwargs)
    monkeypatch.setattr(AuditRepository,'_start_audit_locked',pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        enqueue=pool.submit(RecheckRepository(settings=repo.settings).start,parent.audit.audit_id,selection())
        try:
            assert locked.wait(10)
            correction=pool.submit(corrections.correct,'dataset_a','sample_a',request(revision=2))
        finally:release.set()
        linked=enqueue.result(timeout=20)
        assert correction.result(timeout=20).revision==3
    assert audits.get_request(linked.audit.audit_id).sample_revisions=={'sample_a':2}
    assert repo.get_sample('dataset_a','sample_a').revision==3


@pytest.mark.parametrize('unsupported',['legacy','feedback'])
def test_parent_legacy_or_nonnull_policy_is_not_silently_reinterpreted(dataset_database_settings,tmp_path,unsupported):
    from copy import deepcopy
    repo,_,parent,_,_=prepared(dataset_database_settings,tmp_path)
    row=deepcopy(read_sql(repo.settings,'SELECT * FROM audit_runs WHERE audit_id=%s',(parent.audit.audit_id,))[0])
    if unsupported=='legacy':
        row['source_mode']='legacy';row['request']['source_mode']='legacy'
    else:row['request']['feedback_policy_id']='historical_policy'
    class Connection:
        def execute(self,*args):return self
        def fetchone(self):return row
    with pytest.raises(CorrectionError) as error:RecheckRepository._parent(Connection(),parent.audit.audit_id)
    assert error.value.code=='AUDIT_NOT_RECHECKABLE'


def test_corrupt_locked_catalogue_metadata_cannot_enqueue_child(dataset_database_settings,tmp_path,monkeypatch):
    from backend.app.core.correction import CorrectionStorageError
    from backend.app.storage.dataset_repository import DatasetStorageError
    _,_,parent,_,_=prepared(dataset_database_settings,tmp_path)
    repository=RecheckRepository(settings=dataset_database_settings)
    before_jobs=read_sql(dataset_database_settings,'SELECT count(*) AS n FROM jobs')
    before_audits=read_sql(dataset_database_settings,'SELECT count(*) AS n FROM audit_runs')
    def corrupt(*args):
        raise DatasetStorageError('Synthetic corrupt stored catalogue metadata')
    monkeypatch.setattr(repository,'_dataset',corrupt)
    with pytest.raises(CorrectionStorageError):
        repository.start(parent.audit.audit_id,selection(1))
    assert read_sql(dataset_database_settings,'SELECT count(*) AS n FROM jobs')==before_jobs
    assert read_sql(dataset_database_settings,'SELECT count(*) AS n FROM audit_runs')==before_audits
    assert read_sql(dataset_database_settings,'SELECT count(*) AS n FROM audit_recheck_lineage')==[{'n':0}]


@pytest.mark.parametrize('version', ['annotation-guideline-v3', 'annotation-guideline-v4', 'annotation-guideline-v5'])
def test_recheck_preserves_parent_visual_protocol_when_new_audit_default_changes(
        dataset_database_settings, tmp_path, monkeypatch, version):
    from backend.app.models.feedback import ExecutionPin
    from backend.tests.applicability_fixtures import visual_rule

    settings = dataset_database_settings
    repo, corrections, _ = fixture(settings, tmp_path)
    visual = visual_rule('visual', 'The bounding box follows the visible person.')
    books = RulebookRepository(settings=settings)
    books.save_rulebook(rulebook(guideline_text=visual.description, rules=[visual]))
    execution = ExecutionPin(model='test-vision', model_digest='sha256:' + 'a' * 64,
        prompt_version=version, preprocessing='original-and-annotation-overlay-v1', num_predict=2048)
    monkeypatch.setenv('FEEDBACK_MODEL_DIGEST', execution.model_digest)
    monkeypatch.setattr('backend.app.services.feedback.load_execution_pin', lambda: execution)
    audits = AuditRepository(settings=settings)
    manifests = SampleAssetManifestRepository(settings=settings,
        asset_settings=AssetStorageSettings(root=corrections.settings.asset_root))
    worker = BatchAuditService(rulebook_repository=books, audit_repository=audits, manifest_repository=manifests)
    parent = AuditSubmissionService(catalogue=repo, batch=worker).start(
        AuditRequest(dataset_id='dataset_a', rulebook_id='book', rulebook_version=1))
    # End the queued parent without any inference; its stored inputs remain immutable.
    assert audits.cancel_job(parent.job.job_id).status.value == 'cancelled'
    before = audits.get_request(parent.audit.audit_id)
    assert before.feedback_runtime.execution == execution

    monkeypatch.setenv('FEEDBACK_PROMPT_VERSION', 'annotation-guideline-v5')
    def cannot_repin():
        raise AssertionError('A recheck must not resolve new model or prompt defaults.')
    monkeypatch.setattr('backend.app.services.feedback.load_execution_pin', cannot_repin)
    child = RecheckRepository(settings=settings).start(parent.audit.audit_id, selection(1))
    after = audits.get_request(child.audit.audit_id)
    assert after.feedback_runtime == before.feedback_runtime
    assert after.feedback_runtime.execution.prompt_version == version
    assert after.sample_revisions == {'sample_a': 1}
    assert audits.get_request(parent.audit.audit_id) == before
