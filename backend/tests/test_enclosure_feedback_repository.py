"""PostgreSQL integration for reviewed per-box examples and pinned policy reuse."""
from datetime import timedelta

from psycopg.types.json import Jsonb
import pytest
from fastapi.testclient import TestClient

from backend.app.core.feedback import FeedbackError
from backend.app.main import create_app
from backend.app.models.audit import BatchAuditRequest
from backend.app.models.correction import RecheckRequest
from backend.app.models.dataset import Sample, source_issue
from backend.app.models.dataset_import import SamplePublication
from backend.app.models.enclosure import EnclosureRuntime
from backend.app.models.feedback import FeedbackPolicyCreate, GroundTruthCreate, FeedbackEvaluationCreate, FeedbackPolicyActivate, CaseComparison
from backend.app.models.finding import FindingOutcome, FindingReview
from backend.app.models.rule import Rulebook
from backend.app.services.evaluation import _make_finding
from backend.app.services.enclosure_feedback import EnclosureFeedbackClient
from backend.app.routers.feedback import get_feedback_repository
from backend.app.storage.database import database_connection
from backend.app.storage.dataset_repository import DatasetRepository
from backend.app.storage.feedback_jobs import FeedbackJobRepository
from backend.app.storage.recheck_repository import RecheckRepository
from backend.app.storage.repository import RulebookRepository
from backend.app.storage.workflow_repository import AuditRepository, FindingRepository
from backend.tests.dataset_fixtures import dataset_database_settings, empty_dataset_database_settings, seed_import, write_sql
from backend.tests.feedback_fixtures import NOW, assets
from backend.tests.test_enclosure_feedback import enclosure_case
from backend.tests.test_enclosure_integration import FakeOllama


def seed(settings, tmp_path, monkeypatch, *, execution_pin=None):
    monkeypatch.setenv('AUDIT_SAMPLE_MAX_SECONDS', '900')
    examples = [enclosure_case(tmp_path, name, approved=True, execution_pin=execution_pin) for name in ('training', 'heldout')]
    _, _, claim = seed_import(settings)
    catalogue = DatasetRepository(settings=settings)
    target = examples[0].target
    rulebook = Rulebook(rulebook_id=target.rulebook_id, version=1,
        guideline_text=target.rule.description, rules=[target.rule], confirmation_state='confirmed')
    RulebookRepository(settings=settings).save_rulebook(rulebook)
    for ordinal, value in enumerate(examples):
        sample = Sample(dataset_id='dataset_a', sample_id=value.manifest.sample_id, revision=1,
            image_size=(4, 3), integrity_status='invalid',
            objects=[{'object_id': 1, 'label': 'person', 'bbox': (1, 1, 4, 3)}],
            issues=[source_issue('missing_mask', field='mask')])
        catalogue._publish_import_sample(claim, SamplePublication(manifest=value.manifest, sample=sample,
            ordinal=ordinal, annotation_object_count=1))
    write_sql(settings, "UPDATE datasets SET status='ready',sample_count=2,object_count=2 WHERE dataset_id='dataset_a'")
    audit_repo = AuditRepository(settings=settings)
    findings = FindingRepository(settings=settings)
    audits = []
    for value in examples:
        audit, job = audit_repo.start_audit(BatchAuditRequest(dataset_id='dataset_a', dataset_root='',
            source_mode='snapshot', rulebook_id=rulebook.rulebook_id, rulebook_version=1,
            sample_ids=[value.manifest.sample_id], sample_revisions={value.manifest.sample_id: 1},
            enclosure_runtime=EnclosureRuntime(execution=value.model_execution.enclosure)),
            sample_manifests={value.manifest.sample_id: value.manifest})
        with database_connection(settings) as connection:
            connection.execute("UPDATE audit_samples SET status='completed' WHERE audit_id=%s", (audit.audit_id,))
            connection.execute("UPDATE audit_runs SET status='completed',processed_count=1 WHERE audit_id=%s", (audit.audit_id,))
            connection.execute("UPDATE jobs SET status='completed',progress=%s,result=%s WHERE job_id=%s",
                (Jsonb({'phase':'completed', 'processed':1, 'total':1, 'fraction':1.0}),
                 Jsonb({'kind':'audit','id':audit.audit_id,'href':'/api/v1/audits/'+audit.audit_id}), job.job_id))
        finding = _make_finding(rule=target.rule, outcome=FindingOutcome.PASS, reason='all_boxes_pass',
            evidence={'enclosure': value.model_proposal.enclosure.model_dump(mode='json')},
            audit_id=audit.audit_id, sample_id=value.manifest.sample_id, sample_revision=1,
            object_id=None, occurrence='sample', rulebook_id=rulebook.rulebook_id, rulebook_version=1)
        findings.save_findings([finding])
        findings.save_finding_review(finding.finding_id, FindingReview(revision=1, resolution='accepted',
            reviewer_id='human', comment='Checked the single framed box.', updated_at=NOW), expected_review_version=0)
        audits.append((audit, finding, value))
    return FeedbackJobRepository(settings=settings, asset_settings=assets(tmp_path)), audit_repo, findings, audits


def test_enclosure_examples_comparison_activation_audit_and_recheck(dataset_database_settings, tmp_path, monkeypatch):
    repository, audit_repo, findings, audits = seed(dataset_database_settings, tmp_path, monkeypatch)
    training, heldout = audits
    # An explicit single-box override is a usable example; retain the original model pass.
    findings.save_finding_review(training[1].finding_id, FindingReview(revision=2, resolution='rejected',
        reviewer_id='human', comment='This synthetic target should fail.', updated_at=NOW+timedelta(seconds=1)), expected_review_version=1)
    example = next(c for c in repository.examples().items if c.finding_id == training[1].finding_id)
    assert example.expected == 'fail' and example.review_revision == 2
    policy = repository.create_policy(FeedbackPolicyCreate(name='Per-box feedback', reviewer_id='human', example_ids=[example.example_id]))
    truth = repository.create_truth(GroundTruthCreate(reviewer_id='human', cases=[dict(audit_id=heldout[0].audit_id,
        sample_id='heldout', sample_revision=1, rule_id=example.reference.rule_id, expected='pass', reason='Independent synthetic truth.')]))
    monkeypatch.setattr('backend.app.storage.feedback_jobs.load_execution_pin', lambda: pytest.fail('Must use the saved enclosure pin.'))
    started = repository.enqueue(FeedbackEvaluationCreate(policy_id=policy.policy_id, ground_truth_id=truth.ground_truth_id, reviewer_id='human'))
    claim = repository.claim('comparison')
    inputs = claim[3]
    target = inputs.truth.cases[0]
    fake = FakeOllama(digest='ee'*32)
    client = EnclosureFeedbackClient(inputs.runtime, inputs.policy, transport=fake.transport())
    baseline = client.predict(target.manifest, target.target, [], assets(tmp_path))
    candidate = client.predict(target.manifest, target.target, inputs.policy.cases, assets(tmp_path))
    repository.save_case(claim, 0, CaseComparison(case_id=baseline.prediction.case_id, baseline=baseline, candidate=candidate))
    repository.complete(claim)
    evaluation = repository.get_evaluation(started.evaluation.evaluation_id)
    assert evaluation.status == 'completed'
    # This fixture has one compliant target and no violations. Both arms pass,
    # so violation-positive precision/recall/F1 are undefined, not an equal score.
    assert evaluation.metrics.assessment == 'inconclusive'
    for arm in (evaluation.metrics.baseline, evaluation.metrics.candidate):
        assert arm.tn == arm.intended == 1
        assert arm.tp == arm.fp == arm.fn == 0
        assert arm.coverage == 1.0
        assert arm.precision is arm.recall is arm.f1 is None
    assert evaluation.results[0].candidate.enclosure.feedback_policy_id == policy.policy_id
    repository.activate(policy.policy_id, FeedbackPolicyActivate(evaluation_id=evaluation.evaluation_id, reviewer_id='human'))
    enabled = EnclosureRuntime(execution=inputs.runtime.enclosure)
    monkeypatch.setattr('backend.app.core.enclosure.load_enclosure_runtime', lambda: enabled)
    accepted, job = audit_repo.start_audit(BatchAuditRequest(dataset_id='dataset_a', dataset_root='',
        source_mode='snapshot', rulebook_id=target.target.rulebook_id, rulebook_version=1,
        sample_ids=['heldout'], sample_revisions={'heldout':1}, feedback_policy_id=policy.policy_id),
        sample_manifests={'heldout': target.manifest})
    frozen = audit_repo.get_request(accepted.audit_id)
    assert frozen.feedback_runtime.policy == inputs.policy
    assert frozen.enclosure_runtime.execution == inputs.runtime.enclosure
    audit_repo.cancel_job(job.job_id)
    monkeypatch.setattr('backend.app.core.enclosure.load_enclosure_runtime', lambda: pytest.fail('Rechecks must not reload defaults.'))
    linked = RecheckRepository(settings=dataset_database_settings).start(accepted.audit_id,
        RecheckRequest(sample_ids=['heldout'], sample_revisions={'heldout':1}, reviewer_id='human'))
    assert audit_repo.get_request(linked.audit.audit_id).feedback_runtime == frozen.feedback_runtime
    assert audit_repo.get_request(linked.audit.audit_id).enclosure_runtime == frozen.enclosure_runtime
    app = create_app()
    app.dependency_overrides[get_feedback_repository] = lambda: repository
    with TestClient(app) as http:
        response = http.get(f'/api/v1/feedback/evaluations/{evaluation.evaluation_id}')
        assert response.status_code == 200
        assert response.json()['results'][0]['candidate']['enclosure']['boxes'][0]['model_verified']


def test_old_example_revision_is_rejected_after_human_review_changes(dataset_database_settings, tmp_path, monkeypatch):
    repository, _, findings, audits = seed(dataset_database_settings, tmp_path, monkeypatch)
    finding = audits[0][1]
    identifier = next(c.example_id for c in repository.examples().items if c.finding_id == finding.finding_id)
    findings.save_finding_review(finding.finding_id, FindingReview(revision=2, resolution='rejected',
        reviewer_id='human', comment='Reconsidered the box.', updated_at=NOW+timedelta(seconds=1)), expected_review_version=1)
    with pytest.raises(FeedbackError) as error:
        repository.create_policy(FeedbackPolicyCreate(name='Stale selection', reviewer_id='human', example_ids=[identifier]))
    assert error.value.code == 'FEEDBACK_CONFLICT'


def test_spawn_worker_uses_per_box_protocol_and_persists_both_arms(dataset_database_settings, tmp_path, monkeypatch):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import httpx
    from backend.app.services.feedback_jobs import run_claim
    from backend.tests.test_enclosure_core import pin

    fake = FakeOllama(digest='ee'*32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_model(self):
            length = int(self.headers.get('Content-Length', '0'))
            reply = fake.handle(httpx.Request(self.command, 'http://127.0.0.1'+self.path,
                content=self.rfile.read(length)))
            self.send_response(reply.status_code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(reply.content)))
            self.end_headers()
            self.wfile.write(reply.content)

        do_GET = handle_model
        do_POST = handle_model

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        execution = pin(host=f'http://127.0.0.1:{server.server_port}')
        repository, _, _, audits = seed(dataset_database_settings, tmp_path, monkeypatch, execution_pin=execution)
        example = next(c for c in repository.examples().items if c.finding_id == audits[0][1].finding_id)
        policy = repository.create_policy(FeedbackPolicyCreate(name='Spawn policy', reviewer_id='human', example_ids=[example.example_id]))
        truth = repository.create_truth(GroundTruthCreate(reviewer_id='human', cases=[dict(
            audit_id=audits[1][0].audit_id, sample_id='heldout', sample_revision=1,
            rule_id=example.reference.rule_id, expected='pass', reason='Explicit synthetic held-out answer.')]))
        started = repository.enqueue(FeedbackEvaluationCreate(policy_id=policy.policy_id,
            ground_truth_id=truth.ground_truth_id, reviewer_id='human'))
        claim = repository.claim('spawn-enclosure')
        run_claim(repository, claim)
        result = repository.get_evaluation(started.evaluation.evaluation_id)
        assert result.status == 'completed'
        assert result.results[0].baseline.enclosure.boxes[0].model_verified
        assert result.results[0].candidate.enclosure.feedback_policy_id == policy.policy_id
        assert len(fake.chat_bodies) == 2
        baseline, candidate = [json.loads(body['messages'][1]['content']) for body in fake.chat_bodies]
        assert 'examples' not in baseline and candidate['examples'][0]['expected'] == 'pass'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
