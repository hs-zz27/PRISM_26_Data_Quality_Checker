"""Authored offline model/supervisor regressions; all inference is replaced."""

from datetime import UTC, datetime, timedelta
import json
import multiprocessing
import os
from pathlib import Path
from threading import Event

from pydantic import ValidationError
import pytest

from backend.app.models.job import Job, JobKind, JobStatus, Progress, ResultReference
from backend.app.models.rule import Rulebook
from backend.app.models.rule_extraction import ExtractionClaim, ExtractionInput, ExtractionPolicy, RuleExtractionRequest
from backend.app.services import extraction_jobs as service
from backend.app.inference.ollama_client import OllamaClientError
from backend.app.storage.extraction_repository import safe_error
from backend.app.storage.workflow_repository import JobClaimLostError, RetryableWorkflowStorageError, WorkflowStorageError
from backend.tests.extraction_fakes import fake_child
from backend.tests.test_extraction_repository import model_settings

@pytest.mark.parametrize("body", [{}, {"guideline_text": None}, {"guideline_text": 3},
    {"guideline_text": True}, {"guideline_text": []}, {"guideline_text": " \n\t"},
    {"guideline_text": "valid", "model": "client"}, {"guideline_text": "valid", "rules": []}])
def test_public_request_is_strict_and_extra_forbidding(body):
    with pytest.raises(ValidationError): RuleExtractionRequest.model_validate(body)

def test_public_request_preserves_exact_unicode_newlines_and_whitespace():
    text = "\n  véhicule\tperson  \n"
    assert RuleExtractionRequest(guideline_text=text).guideline_text == text

@pytest.mark.parametrize("overrides", [{"max_seconds": 0}, {"lease_seconds": True},
    {"renew_seconds": float("nan")}, {"poll_seconds": 0.0},
    {"lease_seconds": 4, "renew_seconds": 2.0}, {"renew_seconds": 0.1, "poll_seconds": 0.2}])
def test_private_policy_rejects_nonfinite_and_unsafe_cadence(overrides):
    with pytest.raises(ValidationError): ExtractionPolicy(**overrides)

class CapturePipe:
    def __init__(self): self.payload = None; self.closed = False
    def send_bytes(self, value): self.payload = json.loads(value)
    def close(self): self.closed = True

@pytest.mark.parametrize("reason,code", list(service.REASON_CODES.items()) + [("future_reason", "EXTRACTION_FAILED")])
def test_every_supported_model_failure_is_safely_mapped(monkeypatch, reason, code):
    def fail(*args, **kwargs):
        raise OllamaClientError(reason, "PRIVATE_RAW_OUTPUT_AND_HOST")
    monkeypatch.setattr(service, "extract_rulebook", fail)
    pipe = CapturePipe()
    service.extraction_child(pipe, "Source.", model_settings().model_dump(), "rulebook_" + "a" * 32)
    assert pipe.payload == {"error": code} and pipe.closed
    assert "PRIVATE" not in json.dumps(pipe.payload)

def test_reason_inventory_covers_actual_client_source():
    # Source inspection only; no inference requests or brittle parent-spawn monkeypatches.
    import ast
    import inspect
    from backend.app.inference import ollama_client
    tree = ast.parse(inspect.getsource(ollama_client))
    reasons = {node.args[0].value for node in ast.walk(tree)
               if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id == "OllamaClientError" and node.args
               and isinstance(node.args[0], ast.Constant)}
    assert reasons == set(service.REASON_CODES)

def test_child_reconstructs_reserved_identity_and_preserves_sync_extraction_api(monkeypatch):
    monkeypatch.setattr(service, "extract_rulebook", lambda text, settings:
        Rulebook(rulebook_id="original_sync_id", guideline_text=text))
    pipe = CapturePipe()
    reserved = "rulebook_" + "b" * 32
    service.extraction_child(pipe, "\nOriginal\t", model_settings().model_dump(), reserved)
    assert pipe.payload["draft"]["rulebook_id"] == reserved
    assert pipe.payload["draft"]["guideline_text"] == "\nOriginal\t"
    assert pipe.payload["draft"]["rules"] == []

class FakeRepository:
    def __init__(self, claim, marker, action=None, stop=None, uncertain=False, unresolved=False):
        self.claim, self.marker, self.action, self.stop = claim, marker, action, stop
        self.published = []
        self.failures = []
        self.renewals = 0
        self.saved = claim.job
        self.uncertain, self.unresolved = uncertain, unresolved
    def checkpoint(self, claim, *, renew=False):
        assert claim == self.claim
        if renew: self.renewals += 1
        if self.marker.exists():
            if self.action == "lease": raise JobClaimLostError("Lost")
            if self.action == "storage": raise WorkflowStorageError("Unavailable")
            if self.action == "shutdown": self.stop.set()
            if self.action == "cancel":
                self.saved = Job.model_validate({**self.saved.model_dump(), "status": "cancelled"})
        return self.saved
    def publish(self, claim, payload):
        assert claim == self.claim
        self.published.append(Rulebook.model_validate(payload))
        self.saved = Job.model_validate({**self.saved.model_dump(), "status": "completed",
            "progress": Progress(phase="completed", processed=1, total=1, fraction=1.0),
            "result": ResultReference(kind="rulebook", id=claim.request.rulebook_id, version=1,
                href=f"/api/v1/rulebooks/{claim.request.rulebook_id}?version=1")})
        if self.uncertain:
            raise RetryableWorkflowStorageError("Unknown", commit_outcome_unknown=True)
        return self.saved
    def fail(self, claim, code):
        self.failures.append(code)
        self.saved = Job.model_validate({**self.saved.model_dump(), "status": "failed",
                                        "error": safe_error(code, claim.job.job_id)})
        return self.saved
    def readback(self, claim):
        return claim.job if self.unresolved else self.saved

def scenario(tmp_path, mode, *, max_seconds=5, action=None, uncertain=False, unresolved=False):
    marker = tmp_path / "child_started"
    text = json.dumps({"mode": mode, "marker": str(marker)})
    now = datetime.now(UTC)
    policy = ExtractionPolicy(max_seconds=max_seconds, lease_seconds=10, poll_seconds=0.05, renew_seconds=1.0)
    request = ExtractionInput(job_id="job_rule_extraction_test", rulebook_id="rulebook_" + "c" * 32,
        guideline_text=text, settings=model_settings(), policy=policy,
        started_at=now, deadline=now + timedelta(seconds=max_seconds))
    job = Job.model_validate({**Job.queued(job_id=request.job_id, kind=JobKind.RULE_EXTRACTION,
                                        total=1, now=now).model_dump(), "status": "running"})
    claim = ExtractionClaim(job=job, request=request, worker_id="test_worker", claim_token="token",
                            lease_until=now + timedelta(seconds=10))
    stop = Event()
    repo = FakeRepository(claim, marker, action=action, stop=stop, uncertain=uncertain, unresolved=unresolved)
    return claim, repo, stop, marker

def assert_reaped(marker):
    assert marker.exists(), "Interprocess marker proves the spawn target actually started."
    pid = int(marker.read_text())
    assert pid not in {child.pid for child in multiprocessing.active_children()}
    with pytest.raises(ProcessLookupError): os.kill(pid, 0)

@pytest.mark.parametrize("mode", ["empty", "large"])
def test_spawn_drains_large_payload_before_join_and_only_parent_publishes(tmp_path, mode):
    claim, repo, stop, marker = scenario(tmp_path, mode)
    job = service.supervise(claim, repository=repo, stop=stop, child_target=fake_child)
    assert job.status is JobStatus.COMPLETED and len(repo.published) == 1
    assert repo.published[0].rulebook_id == claim.request.rulebook_id
    assert repo.published[0].guideline_text == claim.request.guideline_text
    assert not repo.failures
    assert_reaped(marker)

@pytest.mark.parametrize("mode", ["eof", "crash", "malformed", "nonfinite"])
def test_spawn_missing_or_invalid_results_never_become_empty_success(tmp_path, mode):
    claim, repo, stop, marker = scenario(tmp_path, mode)
    result = service.supervise(claim, repository=repo, stop=stop, child_target=fake_child)
    assert result.status is JobStatus.FAILED and not repo.published
    assert repo.failures == ["EXTRACTION_FAILED"]
    assert_reaped(marker)

@pytest.mark.parametrize("mode", ["hang", "partial"])
@pytest.mark.parametrize("action", ["cancel", "lease", "shutdown", "storage"])
def test_checked_boundaries_stop_and_reap_even_partial_pipe_frames(tmp_path, mode, action):
    claim, repo, stop, marker = scenario(tmp_path, mode, action=action)
    if action == "storage":
        with pytest.raises(WorkflowStorageError):
            service.supervise(claim, repository=repo, stop=stop, child_target=fake_child)
    else:
        result = service.supervise(claim, repository=repo, stop=stop, child_target=fake_child)
        if action == "cancel": assert result.status is JobStatus.CANCELLED
        elif action == "lease": assert result is None and not repo.failures
        else: assert result.error.code == "EXTRACTION_INTERRUPTED"
    assert not repo.published
    assert_reaped(marker)

def test_hard_deadline_does_not_renew_indefinitely_or_publish_hung_work(tmp_path):
    claim, repo, stop, marker = scenario(tmp_path, "hang", max_seconds=2)
    result = service.supervise(claim, repository=repo, stop=stop, child_target=fake_child)
    assert result.error.code == "EXTRACTION_TIMEOUT"
    assert repo.renewals <= 2 and not repo.published
    assert_reaped(marker)

@pytest.mark.parametrize("unresolved", [False, True])
def test_unknown_commit_readback_never_reruns_or_overwrites_with_failure(tmp_path, unresolved):
    claim, repo, stop, marker = scenario(tmp_path, "empty", uncertain=True, unresolved=unresolved)
    if unresolved:
        with pytest.raises(service.ExtractionOutcomeUnresolved):
            service.supervise(claim, repository=repo, stop=stop, child_target=fake_child)
    else:
        result = service.supervise(claim, repository=repo, stop=stop, child_target=fake_child)
        assert result.status is JobStatus.COMPLETED
    assert len(repo.published) == 1 and repo.failures == []
    assert_reaped(marker)

def test_safe_model_error_payload_is_persisted_without_fabricated_draft(tmp_path):
    claim, repo, stop, marker = scenario(tmp_path, "error")
    result = service.supervise(claim, repository=repo, stop=stop, child_target=fake_child)
    assert result.error.code == "MODEL_UNAVAILABLE" and not repo.published
    assert_reaped(marker)

def test_partial_frame_reader_never_waits_for_the_rest_of_a_frame():
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    try:
        reader = service._FrameReader(receive)
        os.write(send.fileno(), b"\x00\x00")
        assert reader.poll() is None
        send.close()
        with pytest.raises(ValueError): reader.poll()
    finally:
        receive.close()
        send.close()

def test_bad_saved_configuration_has_explicit_safe_category():
    pipe = CapturePipe()
    settings = model_settings().model_dump()
    settings["host"] = "http://remote.example"
    service.extraction_child(pipe, "Source.", settings, "rulebook_" + "d" * 32)
    assert pipe.payload == {"error": "MODEL_CONFIGURATION_ERROR"} and pipe.closed

def test_once_empty_does_not_start_child_or_model(monkeypatch):
    from backend.app.workers import rule_extraction as worker
    calls = []
    class EmptyRepository:
        def recover_stale(self): calls.append("recover"); return []
        def claim_next(self, *, worker_id): calls.append("claim"); return None
    monkeypatch.setattr(worker, "ExtractionRepository", EmptyRepository)
    def forbidden(*args, **kwargs): pytest.fail("Empty --once must not start work.")
    monkeypatch.setattr(worker, "supervise", forbidden)
    assert worker.main(["--once"]) == 0 and calls == ["recover", "claim"]

def test_once_processes_at_most_one_claim(monkeypatch):
    from backend.app.workers import rule_extraction as worker
    calls = []
    class OneRepository:
        def recover_stale(self): calls.append("recover"); return []
        def claim_next(self, *, worker_id): calls.append("claim"); return "one_claim"
    monkeypatch.setattr(worker, "ExtractionRepository", OneRepository)
    def supervise_once(claim, *, repository, stop):
        assert claim == "one_claim"
        calls.append("supervise")
    monkeypatch.setattr(worker, "supervise", supervise_once)
    assert worker.main(["--once"]) == 0 and calls == ["recover", "claim", "supervise"]

def test_unavailable_readback_does_not_fabricate_success_or_retry(tmp_path):
    claim, repo, stop, marker = scenario(tmp_path, "empty", uncertain=True)
    def unavailable(claim): raise WorkflowStorageError("Unavailable")
    repo.readback = unavailable
    with pytest.raises(service.ExtractionOutcomeUnresolved):
        service.supervise(claim, repository=repo, stop=stop, child_target=fake_child)
    assert len(repo.published) == 1 and not repo.failures
    assert_reaped(marker)

def test_frame_reader_rejects_duplicate_keys_and_extra_frames():
    context = multiprocessing.get_context("spawn")
    for payload in (b'{"error":"EXTRACTION_FAILED","error":"MODEL_UNAVAILABLE"}',):
        receive, send = context.Pipe(duplex=False)
        try:
            reader = service._FrameReader(receive)
            send.send_bytes(payload)
            with pytest.raises(ValueError): reader.poll()
        finally:
            receive.close(); send.close()
    receive, send = context.Pipe(duplex=False)
    try:
        reader = service._FrameReader(receive)
        send.send_bytes(b'{"error":"EXTRACTION_FAILED"}')
        assert reader.poll() == {"error": "EXTRACTION_FAILED"}
        send.send_bytes(b'{}')
        with pytest.raises(ValueError): reader.poll()
    finally:
        receive.close(); send.close()
