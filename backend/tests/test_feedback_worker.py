"""Opt-in real spawn/SQL plumbing against a loopback protocol stub, NOT model accuracy."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread, Event
import json
import multiprocessing
import pytest
from backend.app.services.feedback_jobs import run_claim, OwnershipWatchdog
from backend.app.storage.workflow_repository import AuditRepository, FindingRepository, SampleAssetManifestRepository
from backend.app.services.batch_audit import BatchAuditService
from backend.app.services.audit_submission import AuditSubmissionService
from backend.app.models.audit_submission import AuditRequest
from backend.app.models.feedback import FeedbackPolicyActivate
from backend.app.models.job import JobKind
from backend.app.storage.recheck_repository import RecheckRepository
from backend.app.models.correction import RecheckRequest
from backend.app.storage.repository import RulebookRepository
from backend.tests.feedback_fixtures import assets, book
from backend.tests.test_feedback_repository import seed, resources, queue
from backend.tests.dataset_fixtures import dataset_database_settings, empty_dataset_database_settings

@contextmanager
def local_protocol(monkeypatch):
    calls = []

    class Handler(BaseHTTPRequestHandler):

        def log_message(self, *args):
            return

        def do_GET(self):
            self.respond({'models': [{'name': 'test-vision', 'digest': 'a' * 64}]})

        def respond(self, body):
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            size = int(self.headers['Content-Length'])
            assert size < 64 * 1024 ** 2
            body = json.loads(self.rfile.read(size))
            calls.append((self.path, body))
            if self.path == '/api/show':
                self.respond({'capabilities': ['vision']})
                return
            target = json.loads(body['messages'][1]['content'])
            result = {'case_id': target['case_id'], 'rule_id': target['rule_id'], 'outcome': 'fail' if target['examples'] else 'pass', 'reason': 'Synthetic protocol result.', 'evidence_references': ['image']}
            self.respond({'model': 'test-vision', 'done': True, 'done_reason': 'stop', 'prompt_eval_count': 1024, 'message': {'role': 'assistant', 'content': json.dumps(result)}})
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('OLLAMA_BASE_URL', f'http://127.0.0.1:{server.server_port}')
    monkeypatch.setenv('OLLAMA_MODEL', 'test-vision')
    try:
        yield calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

def test_spawn_comparison_then_active_policy_audit_and_linked_recheck(dataset_database_settings, tmp_path, monkeypatch):
    repository, audits, _ = seed(dataset_database_settings, tmp_path, monkeypatch)
    policy, truth = resources(repository, audits)
    started = queue(repository, policy, truth)
    claim = repository.claim('owned-worker')
    with local_protocol(monkeypatch) as calls:
        run_claim(repository, claim)
        result = repository.get_evaluation(started.evaluation.evaluation_id)
        assert result.status == 'completed' and result.metrics.assessment == 'improved'
        assert len([p for p, _ in calls if p == '/api/chat']) == 2
        repository.activate(policy.policy_id, FeedbackPolicyActivate(evaluation_id=result.evaluation_id, reviewer_id='human'))
        audit_repo = AuditRepository(settings=repository.settings)
        manifests = SampleAssetManifestRepository(settings=repository.settings, asset_settings=assets(tmp_path))
        service = BatchAuditService(rulebook_repository=RulebookRepository(settings=repository.settings), audit_repository=audit_repo, manifest_repository=manifests, sample_max_seconds=60)
        submission = AuditSubmissionService(catalogue=repository_catalogue(repository), batch=service)
        accepted = submission.start(AuditRequest(dataset_id='dataset_a', rulebook_id=book().rulebook_id, rulebook_version=1, sample_ids=['heldout'], feedback_policy_id=policy.policy_id))
        owned = audit_repo.jobs.claim_next_job(worker_id='audit-worker', kind=JobKind.AUDIT, lease_seconds=300)
        assert service.run_claimed_job(owned).status.value == 'completed'
        findings = FindingRepository(settings=repository.settings).list_findings(accepted.audit.audit_id)
        assert len(findings) == 1 and findings[0].outcome.value == 'fail'
        assert findings[0].review.resolution.value == 'pending' and findings[0].review.revision == 0
        linked = RecheckRepository(settings=repository.settings).start(accepted.audit.audit_id, RecheckRequest(sample_ids=['heldout'], sample_revisions={'heldout': 1}, reviewer_id='human'))
        parent_request = audit_repo.get_request(accepted.audit.audit_id)
        child_request = audit_repo.get_request(linked.audit.audit_id)
        assert child_request.feedback_runtime == parent_request.feedback_runtime
        owned = audit_repo.jobs.claim_next_job(worker_id='audit-worker', kind=JobKind.AUDIT, lease_seconds=300)
        assert service.run_claimed_job(owned).status.value == 'completed'
        child_findings = FindingRepository(settings=repository.settings).list_findings(linked.audit.audit_id)
        assert child_findings[0].review.revision == 0
    assert not any((p.is_alive() for p in multiprocessing.active_children()))

def repository_catalogue(repository):
    from backend.app.storage.dataset_repository import DatasetRepository
    return DatasetRepository(settings=repository.settings)

def test_cancelled_queued_job_never_enters_spawn(dataset_database_settings, tmp_path, monkeypatch):
    repository, audits, _ = seed(dataset_database_settings, tmp_path, monkeypatch)
    policy, truth = resources(repository, audits)
    started = queue(repository, policy, truth)
    AuditRepository(settings=repository.settings).cancel_job(started.job.job_id)
    assert repository.claim('worker') is None

def test_watchdog_independently_terminates_computation():
    from unittest.mock import Mock
    process = Mock()
    process.is_alive.return_value = False
    watch = OwnershipWatchdog(process)
    watch.lease_end = 0
    watch.thread.start()
    watch.thread.join(timeout=5)
    assert watch.expired is True and process.join.called
    watch.close()
