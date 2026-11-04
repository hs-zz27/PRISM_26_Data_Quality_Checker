"""Opt-in isolated PostgreSQL cases. Authored/unrun; uses only synthetic images."""
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
from backend.app.models.feedback import ExecutionPin, FeedbackRuntime, FeedbackPolicyCreate, GroundTruthCreate, FeedbackEvaluationCreate, FeedbackPolicyActivate, case_view
from backend.app.models.finding import FindingReview, FindingOutcome
from backend.app.models.job import Job, JobKind
from backend.app.storage.feedback_repository import FeedbackRepository
from backend.app.storage.feedback_jobs import FeedbackJobRepository
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
    monkeypatch.setattr('backend.app.services.feedback.load_execution_pin', lambda: pin)
    monkeypatch.setattr('backend.app.storage.feedback_jobs.load_execution_pin', lambda: pin)
    monkeypatch.setenv('FEEDBACK_MODEL_DIGEST', pin.model_digest)
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
        request = BatchAuditRequest(dataset_id='dataset_a', dataset_root='', rulebook_id=book().rulebook_id, rulebook_version=1, source_mode='snapshot', sample_ids=[value.manifest.sample_id], sample_revisions={value.manifest.sample_id: 1})
        audit, job = AuditRepository(settings=settings).start_audit(request, sample_manifests={value.manifest.sample_id: value.manifest})
        with database_connection(settings) as connection:
            connection.execute("UPDATE audit_samples SET status='completed' WHERE audit_id=%s", (audit.audit_id,))
            connection.execute("UPDATE audit_runs SET status='completed',processed_count=1 WHERE audit_id=%s", (audit.audit_id,))
            connection.execute("UPDATE jobs SET status='completed',progress=%s,result=%s WHERE job_id=%s", (Jsonb({'phase': 'completed', 'processed': 1, 'total': 1, 'fraction': 1.0}), Jsonb({'kind': 'audit', 'id': audit.audit_id, 'version': None, 'href': '/api/v1/audits/' + audit.audit_id}), job.job_id))
        finding = _make_finding(rule=book().rules[0], outcome=FindingOutcome.FAIL, reason='Synthetic model proposal.', evidence={'model_proposal': value.model_proposal.model_dump(mode='json') if value.model_proposal else record(value.manifest, value.target).model_dump(mode='json'), 'execution': pin.model_dump(mode='json')}, audit_id=audit.audit_id, sample_id=value.manifest.sample_id, sample_revision=1, object_id=None, occurrence='sample', rulebook_id=book().rulebook_id, rulebook_version=1)
        finding_repo.save_findings([finding])
        finding_repo.save_finding_review(finding.finding_id, FindingReview(revision=1, resolution='accepted', reviewer_id='human', comment='Reviewed the synthetic annotation evidence.', updated_at=NOW), expected_review_version=0)
        audits.append((audit, finding, value))
    repository = FeedbackJobRepository(settings=settings, asset_settings=assets(tmp_path))
    return (repository, audits, finding_repo)

def resources(repository, audits):
    first = next((value for value in repository.examples().items if value.finding_id == audits[0][1].finding_id))
    policy = repository.create_policy(FeedbackPolicyCreate(name='Reviewed policy', reviewer_id='human', example_ids=[first.example_id]))
    audit, _, value = audits[1]
    truth = repository.create_truth(GroundTruthCreate(reviewer_id='human', cases=[dict(audit_id=audit.audit_id, sample_id=value.manifest.sample_id, sample_revision=1, rule_id=value.target.rule.rule_id, expected='fail', reason='Explicit human held-out violation.')]))
    return (policy, truth)

def queue(repository, policy, truth):
    return repository.enqueue(FeedbackEvaluationCreate(policy_id=policy.policy_id, ground_truth_id=truth.ground_truth_id, reviewer_id='human'))

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

def test_owned_comparison_publication_activation_and_future_audit_pin(dataset_database_settings, tmp_path, monkeypatch):
    repository, audits, _ = seed(dataset_database_settings, tmp_path, monkeypatch)
    policy, truth = resources(repository, audits)
    started = queue(repository, policy, truth)
    assert started.evaluation.metrics is None and started.job.kind.value == 'feedback_evaluation'
    assert JobRepository(settings=repository.settings).claim_next_job(worker_id='generic', kind=JobKind.FEEDBACK_EVALUATION) is None
    claim = repository.claim('feedback-worker')
    assert claim is not None
    running = JobRepository(settings=repository.settings).get_job(started.job.job_id)
    assert running.status.value == 'running'
    assert running.progress.phase == 'comparing'
    assert running.progress.processed == 0 and running.progress.total == len(claim[3].truth.cases)
    assert running.progress.fraction == 0.0
    for ordinal, value in enumerate(claim[3].truth.cases):
        assert repository.save_case(claim, ordinal, comparison(value))
    repository.complete(claim)
    completed = repository.get_evaluation(started.evaluation.evaluation_id)
    assert completed.status == 'completed' and completed.metrics.candidate.tp == 1
    activated = repository.activate(policy.policy_id, FeedbackPolicyActivate(evaluation_id=completed.evaluation_id, reviewer_id='human'))
    assert activated.status == 'active'
    assert repository.activate(policy.policy_id, FeedbackPolicyActivate(evaluation_id=completed.evaluation_id, reviewer_id='human')) == activated
    request = BatchAuditRequest(dataset_id='dataset_a', dataset_root='', rulebook_id=book().rulebook_id, rulebook_version=1, source_mode='snapshot', sample_ids=['heldout'], sample_revisions={'heldout': 1}, feedback_policy_id=policy.policy_id)
    audit, _ = AuditRepository(settings=repository.settings).start_audit(request, sample_manifests={'heldout': audits[1][2].manifest})
    saved = read_sql(repository.settings, 'SELECT request FROM audit_runs WHERE audit_id=%s', (audit.audit_id,))[0]['request']
    assert saved['feedback_runtime']['execution'] == claim[3].runtime.model_dump(mode='json')
    assert saved['feedback_runtime']['policy'] == claim[3].policy.model_dump(mode='json')
    restored = AuditRepository(settings=repository.settings).get_request(audit.audit_id)
    assert restored.feedback_runtime is not None
    assert restored.feedback_runtime.execution == claim[3].runtime
    assert restored.feedback_runtime.policy == claim[3].policy

@pytest.mark.parametrize('running', [False, True])
def test_public_cancellation_updates_managed_job(dataset_database_settings, tmp_path, monkeypatch, running):
    repository, audits, _ = seed(dataset_database_settings, tmp_path, monkeypatch)
    policy, truth = resources(repository, audits)
    started = queue(repository, policy, truth)
    claim = repository.claim('worker') if running else None
    result = AuditRepository(settings=repository.settings).cancel_job(started.job.job_id)
    assert result.status.value == ('cancelling' if running else 'cancelled')
    if running:
        assert repository.checkpoint(claim) is False
    assert repository.get_evaluation(started.evaluation.evaluation_id).status == 'cancelled'
    with pytest.raises(FeedbackError):
        repository.activate(policy.policy_id, FeedbackPolicyActivate(evaluation_id=started.evaluation.evaluation_id, reviewer_id='human'))

def test_expired_or_replaced_claim_cannot_write_or_retry(dataset_database_settings, tmp_path, monkeypatch):
    repository, audits, _ = seed(dataset_database_settings, tmp_path, monkeypatch)
    policy, truth = resources(repository, audits)
    started = queue(repository, policy, truth)
    claim = repository.claim('worker')
    write_sql(repository.settings, "UPDATE jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE job_id=%s", (started.job.job_id,))
    with pytest.raises(JobClaimLostError):
        repository.save_case(claim, 0, comparison(claim[3].truth.cases[0]))
    repository.recover()
    assert repository.get_evaluation(started.evaluation.evaluation_id).status == 'failed'
    assert repository.claim('new-worker') is None

def test_unknown_acceptance_commit_never_retries_or_fabricates_success(dataset_database_settings, tmp_path, monkeypatch):
    repository, audits, _ = seed(dataset_database_settings, tmp_path, monkeypatch)
    policy, truth = resources(repository, audits)
    from backend.app.storage import feedback_repository as module
    original = module.database_connection

    @contextmanager
    def unknown(settings, *, readonly=False):
        with original(settings, readonly=readonly) as connection:
            yield connection
        if not readonly:
            raise CommitOutcomeUnknownError('Lost acknowledgement.')
    monkeypatch.setattr(module, 'database_connection', unknown)
    with pytest.raises(FeedbackError) as caught:
        queue(repository, policy, truth)
    assert caught.value.code == 'DEPENDENCY_UNAVAILABLE'
    assert repository.evaluations().total == 1

def test_migration_preserves_prior_history_and_is_repeatable(dataset_database_settings):
    from backend.app.storage.database import MIGRATIONS_DIRECTORY
    before = read_sql(dataset_database_settings, 'SELECT version, applied_at FROM schema_migrations ORDER BY version')
    initialize_database(dataset_database_settings)
    assert read_sql(dataset_database_settings, 'SELECT version, applied_at FROM schema_migrations ORDER BY version') == before
    expected = [int(path.name[:3]) for path in sorted(MIGRATIONS_DIRECTORY.glob('[0-9][0-9][0-9]_*.sql'))]
    assert [row['version'] for row in before] == expected
    assert 13 in expected  # Feedback migration must be present, even after later additions.
    assert read_sql(dataset_database_settings, 'SELECT count(*) AS n FROM feedback_sets')[0]['n'] == 0

def test_generic_create_rejects_managed_feedback_without_database_access():
    from unittest.mock import Mock
    from pydantic import SecretStr
    from backend.app.core.config import DatabaseSettings
    repository = JobRepository(settings=DatabaseSettings(conninfo=SecretStr('dbname=unused')))
    repository._connection = Mock(side_effect=AssertionError('must reject before SQL'))
    with pytest.raises(ValueError):
        repository.create_job(Job.queued(job_id='job', kind=JobKind.FEEDBACK_EVALUATION, total=1, now=NOW))
    repository._connection.assert_not_called()

def test_all_generic_mutation_seams_reject_feedback_and_recovery_excludes_it(dataset_database_settings, tmp_path, monkeypatch):
    repository, audits, _ = seed(dataset_database_settings, tmp_path, monkeypatch)
    policy, truth = resources(repository, audits)
    started = queue(repository, policy, truth)
    owned = repository.claim('worker')
    from backend.app.storage.workflow_repository import JobClaim
    from backend.app.models.job import Progress, ResultReference, ApiError
    jobs = JobRepository(settings=repository.settings)
    row = read_sql(repository.settings, 'SELECT lease_until FROM jobs WHERE job_id=%s', (started.job.job_id,))[0]
    claim = JobClaim(job=jobs.get_job(started.job.job_id), worker_id=owned[1], claim_token=owned[2], lease_until=row['lease_until'])
    operations = [lambda: jobs.claim_job(started.job.job_id, worker_id='other'), lambda: jobs.renew_claim(claim), lambda: jobs.update_progress(claim, Progress(phase='fake', processed=0, total=1, fraction=0.0)), lambda: jobs.complete_job(claim, ResultReference(kind='feedback_evaluation', id=started.evaluation.evaluation_id, href='/api/v1/feedback/evaluations/' + started.evaluation.evaluation_id)), lambda: jobs.fail_job(claim, ApiError(code='FAIL', message='Synthetic.', request_id='test')), lambda: jobs.finalize_cancellation(claim)]
    for operation in operations:
        with pytest.raises(ValueError):
            operation()
    write_sql(repository.settings, "UPDATE jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE job_id=%s", (started.job.job_id,))
    assert jobs.recover_stale_jobs() == []
    assert repository.get_evaluation(started.evaluation.evaluation_id).status == 'running'

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

def test_enqueue_known_failure_is_full_transaction_rollback(dataset_database_settings, tmp_path, monkeypatch):
    repository, audits, _ = seed(dataset_database_settings, tmp_path, monkeypatch)
    policy, truth = resources(repository, audits)
    before = read_sql(repository.settings, 'SELECT count(*) AS n FROM jobs')[0]['n']
    monkeypatch.setattr(repository, '_evaluation', lambda *a: (_ for _ in ()).throw(RuntimeError('Synthetic programming failure.')))
    with pytest.raises(RuntimeError):
        queue(repository, policy, truth)
    assert read_sql(repository.settings, 'SELECT count(*) AS n FROM jobs')[0]['n'] == before
    assert read_sql(repository.settings, 'SELECT count(*) AS n FROM feedback_evaluations')[0]['n'] == 0

def seed_pre_feedback_audit(settings, tmp_path):
    """Seed schema 12 using its columns, independently of today's repository writers."""
    from types import SimpleNamespace
    from backend.tests.export_support import synthetic

    frozen, _, _ = synthetic(tmp_path)
    manifest = frozen.samples[0].manifest
    audit = SimpleNamespace(audit_id='audit_pre_feedback', job_id='job_pre_feedback',
                            rulebook_id='rulebook_demo', rulebook_version=3)
    values = [manifest.dataset_id, manifest.sample_id, manifest.sample_revision]
    for role in ('original_image', 'raw_mask', 'annotation_text'):
        asset = getattr(manifest, role)
        values.extend((asset.availability.value, asset.storage_key, asset.content_type, asset.sha256, asset.size_bytes))
    request = dict(dataset_id=manifest.dataset_id, dataset_root='unused-private-legacy-field',
        rulebook_id=audit.rulebook_id, rulebook_version=audit.rulebook_version, source_mode='snapshot',
        sample_ids=[manifest.sample_id], sample_revisions={manifest.sample_id: manifest.sample_revision})
    result = dict(kind='audit', id=audit.audit_id, version=None, href=f'/api/v1/audits/{audit.audit_id}')
    with database_connection(settings) as connection:
        connection.execute('''INSERT INTO sample_asset_manifests (
            dataset_id, sample_id, sample_revision,
            original_image_availability, original_image_storage_key, original_image_content_type,
            original_image_sha256, original_image_size_bytes,
            raw_mask_availability, raw_mask_storage_key, raw_mask_content_type, raw_mask_sha256, raw_mask_size_bytes,
            annotation_text_availability, annotation_text_storage_key, annotation_text_content_type,
            annotation_text_sha256, annotation_text_size_bytes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''', values)
        connection.execute('''INSERT INTO jobs (
            job_id, kind, status, progress, result, created_at, updated_at, resource_kind, resource_id)
            VALUES (%s,'audit','completed',%s,%s,%s,%s,'audit',%s)''',
            (audit.job_id, Jsonb(dict(phase='completed', processed=1, total=1, fraction=1.0)),
             Jsonb(result), NOW, NOW, audit.audit_id))
        connection.execute('''INSERT INTO audit_runs (
            audit_id, job_id, dataset_id, dataset_root, rulebook_id, rulebook_version,
            status, request, sample_count, processed_count, created_at, updated_at, source_mode)
            VALUES (%s,%s,%s,%s,%s,%s,'completed',%s,1,1,%s,%s,'snapshot')''',
            (audit.audit_id, audit.job_id, manifest.dataset_id, request['dataset_root'],
             audit.rulebook_id, audit.rulebook_version, Jsonb(request), NOW, NOW))
        connection.execute('''INSERT INTO audit_samples (
            audit_id, sample_id, sample_revision, ordinal, status, finished_at, manifest_dataset_id)
            VALUES (%s,%s,%s,0,'completed',%s,%s)''',
            (audit.audit_id, manifest.sample_id, manifest.sample_revision, NOW, manifest.dataset_id))
        connection.execute('''INSERT INTO sample_reviews (
            audit_id, sample_id, sample_revision, revision, decision, reviewer_id, comment, updated_at)
            VALUES (%s,%s,%s,1,'accepted','old-human','Preserve this historical sample review.',%s)''',
            (audit.audit_id, manifest.sample_id, manifest.sample_revision, NOW))
    return audit


def test_additive_migration_preserves_seeded_audits_findings_and_reviews(empty_dataset_database_settings, tmp_path, monkeypatch):
    from shutil import copyfile
    from backend.app.storage import database as module
    directory = tmp_path / 'migrations'
    directory.mkdir()
    original = module.MIGRATIONS_DIRECTORY
    for path in sorted(original.glob('*.sql')):
        if int(path.name[:3]) <= 12:
            copyfile(path, directory / path.name)
    monkeypatch.setattr(module, 'MIGRATIONS_DIRECTORY', directory)
    initialize_database(empty_dataset_database_settings)
    audit = seed_pre_feedback_audit(empty_dataset_database_settings, tmp_path)
    from backend.app.models.rule import Rule
    rule = Rule(rule_id='old_rule', description='Existing allowed labels.', check_name='allowed_labels', parameters={'allowed_labels': ['person']}, origin='reviewer', confirmation_state='confirmed')
    old = _make_finding(rule=rule, outcome=FindingOutcome.PASS, reason=None, evidence={}, audit_id=audit.audit_id, sample_id='sample_0', sample_revision=2, object_id=1, occurrence='old', rulebook_id=audit.rulebook_id, rulebook_version=audit.rulebook_version)
    findings = FindingRepository(settings=empty_dataset_database_settings)
    findings.save_findings([old])
    findings.save_finding_review(old.finding_id, FindingReview(revision=1, resolution='accepted', reviewer_id='old-human', comment='Preserve this historical review.', updated_at=NOW), expected_review_version=0)
    tables = ['jobs', 'audit_runs', 'audit_samples', 'findings', 'finding_reviews', 'sample_reviews', 'sample_asset_manifests']

    def snapshots():
        return {table: sorted((row['document'] for row in read_sql(empty_dataset_database_settings, 'SELECT to_jsonb(t) AS document FROM ' + table + ' t')), key=lambda value: json.dumps(value, sort_keys=True)) for table in tables}
    before = snapshots()
    history = read_sql(empty_dataset_database_settings, 'SELECT version, applied_at FROM schema_migrations ORDER BY version')
    copyfile(original / '013_feedback_improvement.sql', directory / '013_feedback_improvement.sql')
    initialize_database(empty_dataset_database_settings)
    assert snapshots() == before
    assert read_sql(empty_dataset_database_settings, 'SELECT version, applied_at FROM schema_migrations WHERE version <= 12 ORDER BY version') == history
    assert read_sql(empty_dataset_database_settings, 'SELECT count(*) AS n FROM feedback_sets')[0]['n'] == 0

    # Finish the real upgrade path, allowing additive columns but preserving every
    # historical value, hash, annotation identity and attributed review.
    monkeypatch.setattr(module, 'MIGRATIONS_DIRECTORY', original)
    initialize_database(empty_dataset_database_settings)
    upgraded = snapshots()
    for table, rows in before.items():
        fields = set(rows[0]) if rows else set()
        preserved = [{key: row[key] for key in fields} for row in upgraded[table]]
        assert sorted(preserved, key=lambda value: json.dumps(value, sort_keys=True)) == rows
    assert upgraded['sample_asset_manifests'][0]['annotation_format'] == 'penn_fudan'
    assert upgraded['sample_asset_manifests'][0]['image_key'] is None
    initialize_database(empty_dataset_database_settings)
    assert snapshots() == upgraded
    assert findings.get_finding(old.finding_id).review.reviewer_id == 'old-human'



def test_replacement_token_rejects_stale_publication(dataset_database_settings, tmp_path, monkeypatch):
    repository, audits, _ = seed(dataset_database_settings, tmp_path, monkeypatch)
    policy, truth = resources(repository, audits)
    started = queue(repository, policy, truth)
    stale = repository.claim('worker')
    write_sql(repository.settings, "UPDATE jobs SET claim_token='replacement' WHERE job_id=%s", (started.job.job_id,))
    for operation in (lambda: repository.checkpoint(stale), lambda: repository.save_case(stale, 0, comparison(stale[3].truth.cases[0])), lambda: repository.complete(stale), lambda: repository.fail(stale)):
        with pytest.raises(JobClaimLostError):
            operation()
    assert repository.get_evaluation(started.evaluation.evaluation_id).status == 'running'


def test_unavailable_comparison_cannot_activate(dataset_database_settings, tmp_path, monkeypatch):
    repository, audits, _ = seed(dataset_database_settings, tmp_path, monkeypatch)
    policy, truth = resources(repository, audits)
    started = queue(repository, policy, truth)
    claim = repository.claim('worker')
    result = comparison(claim[3].truth.cases[0], candidate='unavailable')
    result.candidate.model_verified = False
    repository.save_case(claim, 0, result)
    repository.complete(claim)
    with pytest.raises(FeedbackError) as caught:
        repository.activate(policy.policy_id, FeedbackPolicyActivate(evaluation_id=started.evaluation.evaluation_id, reviewer_id='human'))
    assert caught.value.code == 'FEEDBACK_NOT_READY'
    assert repository.get_policy(policy.policy_id).status == 'draft'


def test_corrupt_queued_attempt_is_not_replayed(dataset_database_settings, tmp_path, monkeypatch):
    repository, audits, _ = seed(dataset_database_settings, tmp_path, monkeypatch)
    policy, truth = resources(repository, audits)
    started = queue(repository, policy, truth)
    write_sql(repository.settings, 'UPDATE jobs SET attempt=1 WHERE job_id=%s', (started.job.job_id,))
    with pytest.raises(FeedbackError) as caught:
        repository.claim('worker')
    assert caught.value.code == 'DEPENDENCY_UNAVAILABLE'
    assert repository.get_evaluation(started.evaluation.evaluation_id).status == 'queued'
