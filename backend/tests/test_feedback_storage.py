"""Phase 1 isolated PostgreSQL snapshots; authored, not executed."""
import json
from contextlib import contextmanager
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from psycopg.types.json import Jsonb
import pytest
from backend.app.core.feedback import FeedbackError
from backend.app.models.audit import BatchAuditRequest
from backend.app.models.dataset import Sample, source_issue
from backend.app.models.dataset_import import SamplePublication
from backend.app.models.feedback import ExecutionPin, FeedbackRuntime, FeedbackPolicyCreate, GroundTruthCreate, case_view
from backend.app.models.finding import FindingReview, FindingOutcome
from backend.app.models.job import Job, JobKind
from backend.app.storage.feedback_repository import FeedbackRepository
from backend.app.storage.dataset_repository import DatasetRepository
from backend.app.storage.repository import RulebookRepository
from backend.app.storage.workflow_repository import AuditRepository, FindingRepository, JobRepository, JobClaimLostError
from backend.app.storage.database import database_connection, CommitOutcomeUnknownError, initialize_database
from backend.app.services.evaluation import _make_finding
from backend.tests.dataset_fixtures import dataset_database_settings, empty_dataset_database_settings, seed_import, read_sql, write_sql
from backend.tests.feedback_fixtures import assets, case, book, NOW, comparison, record
from base64 import urlsafe_b64encode

def seed(settings, tmp_path, monkeypatch):
    pin = ExecutionPin(model='test-vision', model_digest='sha256:' + 'a' * 64, num_ctx=131072)
    source = [case(tmp_path, 'training'), case(tmp_path, 'heldout')]
    _, _, claim = seed_import(settings)
    catalogue = DatasetRepository(settings=settings)
    for ordinal, value in enumerate(source):
        sample = Sample(dataset_id='dataset_a', sample_id=value.manifest.sample_id, revision=1, image_size=(4, 3), integrity_status='invalid', objects=[{'object_id': 1, 'label': 'person', 'bbox': (1, 1, 4, 3)}], issues=[source_issue('missing_mask', field='mask')])
        catalogue._publish_import_sample(claim, SamplePublication(manifest=value.manifest, sample=sample, ordinal=ordinal, annotation_object_count=1))
    write_sql(settings, "UPDATE datasets SET status='ready',sample_count=2,object_count=2 WHERE dataset_id='dataset_a'")
    RulebookRepository(settings=settings).save_rulebook(book())
    audits = []
    finding_repo = FindingRepository(settings=settings)
    for value in source:
        request = BatchAuditRequest(dataset_id='dataset_a', dataset_root='', rulebook_id=book().rulebook_id, rulebook_version=1, source_mode='snapshot', feedback_runtime=FeedbackRuntime(execution=pin), sample_ids=[value.manifest.sample_id], sample_revisions={value.manifest.sample_id: 1})
        audit, job = AuditRepository(settings=settings).start_audit(request, sample_manifests={value.manifest.sample_id: value.manifest})
        with database_connection(settings) as connection:
            connection.execute("UPDATE audit_samples SET status='completed' WHERE audit_id=%s", (audit.audit_id,))
            connection.execute("UPDATE audit_runs SET status='completed',processed_count=1 WHERE audit_id=%s", (audit.audit_id,))
            connection.execute("UPDATE jobs SET status='completed',progress=%s,result=%s WHERE job_id=%s", (Jsonb({'phase': 'completed', 'processed': 1, 'total': 1, 'fraction': 1.0}), Jsonb({'kind': 'audit', 'id': audit.audit_id, 'version': None, 'href': '/api/v1/audits/' + audit.audit_id}), job.job_id))
        finding = _make_finding(rule=book().rules[0], outcome=FindingOutcome.FAIL, reason='Synthetic model proposal.', evidence={'model_proposal': value.model_proposal.model_dump(mode='json') if value.model_proposal else record(value.manifest, value.target).model_dump(mode='json'), 'execution': pin.model_dump(mode='json')}, audit_id=audit.audit_id, sample_id=value.manifest.sample_id, sample_revision=1, object_id=None, occurrence='sample', rulebook_id=book().rulebook_id, rulebook_version=1)
        finding_repo.save_findings([finding])
        finding_repo.save_finding_review(finding.finding_id, FindingReview(revision=1, resolution='accepted', reviewer_id='human', comment='Reviewed the synthetic annotation evidence.', updated_at=NOW), expected_review_version=0)
        audits.append((audit, finding, value))
    repository = FeedbackRepository(settings=settings, asset_settings=assets(tmp_path))
    return (repository, audits, finding_repo)


def resources(repository, audits):
    first = next((value for value in repository.examples().items if value.finding_id == audits[0][1].finding_id))
    policy = repository.create_policy(FeedbackPolicyCreate(name='Reviewed policy', reviewer_id='human', example_ids=[first.example_id]))
    audit, _, value = audits[1]
    truth = repository.create_truth(GroundTruthCreate(reviewer_id='human', cases=[dict(audit_id=audit.audit_id, sample_id=value.manifest.sample_id, sample_revision=1, rule_id=value.target.rule.rule_id, expected='fail', reason='Explicit human held-out violation.')]))
    return (policy, truth)


def test_current_reviews_freeze_and_no_asset_reads_during_projection(dataset_database_settings, tmp_path, monkeypatch):
    repository, audits, findings = seed(dataset_database_settings, tmp_path, monkeypatch)
    monkeypatch.setattr(repository, '_verify', lambda cases: None)
    policy, truth = resources(repository, audits)
    original = repository.get_policy(policy.policy_id).model_dump_json()
    findings.save_finding_review(audits[0][1].finding_id, FindingReview(revision=2, resolution='rejected', reviewer_id='other', comment='Reconsidered the synthetic finding.', updated_at=NOW + timedelta(seconds=1)), expected_review_version=1)
    assert repository.get_policy(policy.policy_id).model_dump_json() == original
    assert all((v.finding_id != audits[0][1].finding_id for v in repository.examples().items))
    stale = 'example_' + urlsafe_b64encode(audits[0][1].finding_id.encode()).decode().rstrip('=') + '.1'
    with pytest.raises(FeedbackError) as caught:
        repository.create_policy(FeedbackPolicyCreate(name='Stale', reviewer_id='human', example_ids=[stale]))
    assert caught.value.code == 'FEEDBACK_CONFLICT'
    assert repository.get_truth(truth.ground_truth_id).cases[0].review_revision == 0


def test_review_change_during_policy_acceptance_rolls_back(dataset_database_settings, tmp_path, monkeypatch):
    repository, audits, findings = seed(dataset_database_settings, tmp_path, monkeypatch)
    example = next((e for e in repository.examples().items if e.finding_id == audits[0][1].finding_id))
    original = repository._verify

    def concurrent_review(cases):
        original(cases)
        findings.save_finding_review(audits[0][1].finding_id, FindingReview(revision=2, resolution='rejected', reviewer_id='other', comment='Reconsidered the synthetic finding.', updated_at=NOW + timedelta(seconds=1)), expected_review_version=1)
    monkeypatch.setattr(repository, '_verify', concurrent_review)
    with pytest.raises(FeedbackError) as caught:
        repository.create_policy(FeedbackPolicyCreate(name='Concurrent', reviewer_id='human', example_ids=[example.example_id]))
    assert caught.value.code == 'FEEDBACK_CONFLICT' and repository.policies().total == 0



def test_bad_source_cannot_create_a_policy(dataset_database_settings, tmp_path, monkeypatch):
    repository, audits, _ = seed(dataset_database_settings, tmp_path, monkeypatch)
    example = next(e for e in repository.examples().items if e.finding_id == audits[0][1].finding_id)
    manifest = audits[0][2].manifest
    (repository.asset_settings.root / manifest.original_image.storage_key).write_bytes(b'changed')
    with pytest.raises(FeedbackError):
        repository.create_policy(FeedbackPolicyCreate(name='Bad source', reviewer_id='human', example_ids=[example.example_id]))
    assert repository.policies().total == 0


def test_frozen_metadata_reads_do_not_open_assets(dataset_database_settings, tmp_path, monkeypatch):
    repository, audits, _ = seed(dataset_database_settings, tmp_path, monkeypatch)
    policy, truth = resources(repository, audits)
    import backend.app.services.feedback_evidence as module
    monkeypatch.setattr(module, 'evidence', lambda *args: (_ for _ in ()).throw(AssertionError('metadata read opened an asset')))
    assert repository.get_policy(policy.policy_id) == policy
    assert repository.get_truth(truth.ground_truth_id) == truth
    assert repository.policies().total == 1
    assert repository.examples().total == 2
