"""Authored HTTP and isolated-PG extraction lifecycle scenarios; left unrun."""

from contextlib import contextmanager

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from backend.app.core.config import DatabaseConfigurationError
from backend.app.main import create_app
from backend.app.models.job import JobStatus
from backend.app.models.rule_extraction import RuleExtractionRequest
from backend.app.routers import rulebooks as rulebook_routes, jobs as job_routes
from backend.app.services import extraction_jobs
from backend.app.storage.database import DatabaseError
from backend.app.storage.extraction_repository import ExtractionRepository, ExtractionSubmissionError
from backend.app.storage.repository import RulebookRepository
from backend.app.storage.workflow_repository import AuditRepository, JobRepository, WorkflowStorageError
from backend.tests.test_repository import empty_database_settings, database_settings
from backend.tests.test_extraction_repository import TEXT, draft_for, model_settings

@pytest.fixture
def api(database_settings, monkeypatch):
    repository = ExtractionRepository(settings=database_settings)
    application = create_app()
    application.dependency_overrides[rulebook_routes.get_extraction_repository] = lambda: repository
    application.dependency_overrides[rulebook_routes.get_rulebook_repository] = lambda: RulebookRepository(settings=database_settings)
    application.dependency_overrides[job_routes.get_job_repository] = lambda: JobRepository(settings=database_settings)
    application.dependency_overrides[job_routes.get_audit_repository] = lambda: AuditRepository(settings=database_settings)
    monkeypatch.setattr(extraction_jobs, "load_ollama_settings", model_settings)
    def no_model(*args, **kwargs):
        pytest.fail("HTTP submission and polling must not perform inference.")
    monkeypatch.setattr(extraction_jobs, "extract_rulebook", no_model)
    with TestClient(application, raise_server_exceptions=False) as client:
        yield client, repository

def submit(client):
    response = client.post("/api/v1/rulebooks/extractions", json={"guideline_text": TEXT})
    assert response.status_code == 202
    return response.json()

def test_submission_is_committed_private_queued_job_without_public_placeholder(api):
    client, repo = api
    body = submit(client)
    assert body["status"] == "queued" and body["kind"] == "rule_extraction"
    assert body["progress"] == {"phase": "queued", "processed": 0, "total": 1, "fraction": 0.0}
    assert body["result"] is None and body["error"] is None
    assert set(body) == {"job_id", "kind", "status", "progress", "result", "error", "created_at", "updated_at"}
    assert TEXT not in str(body) and "local:latest" not in str(body)
    assert client.get(f"/api/v1/jobs/{body['job_id']}").json() == body
    store = RulebookRepository(settings=repo.settings)
    assert store.list_rulebooks().total == 0
    claim = ExtractionRepository(settings=repo.settings).claim_next(worker_id="reopened")
    assert claim.request.guideline_text == TEXT and claim.request.settings == model_settings()

@pytest.mark.parametrize("body", [{}, {"guideline_text": None}, {"guideline_text": 1},
    {"guideline_text": False}, {"guideline_text": " \n\t"},
    *[{"guideline_text": "valid", key: "forbidden"} for key in (
        "job_id", "rulebook_id", "origin", "confirmation_state", "model", "host", "settings",
        "retry", "timeout", "reviewer_id", "rules")]])
def test_invalid_body_is_safe_422_without_acceptance_or_inference(api, body):
    client, repo = api
    response = client.post("/api/v1/rulebooks/extractions", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert repo.claim_next(worker_id="must_find_nothing") is None

@pytest.mark.parametrize("failure", ["model_config", "storage", "known_commit", "unknown_commit"])
def test_acceptance_failures_never_return_202_or_private_diagnostics(api, monkeypatch, failure):
    client, repo = api
    diagnostic = "PRIVATE_GUIDELINES_HOST_AND_SQL"
    if failure == "model_config":
        def broken(): raise OSError(diagnostic)
        monkeypatch.setattr(extraction_jobs, "load_ollama_settings", broken)
    elif failure == "storage":
        def broken(*args, **kwargs): raise WorkflowStorageError(diagnostic)
        monkeypatch.setattr(repo, "enqueue", broken)
    else:
        original = repo._connection
        @contextmanager
        def reject(*, write=False):
            if failure == "known_commit":
                with original(write=write) as connection:
                    yield connection
                    raise DatabaseError(diagnostic)
            else:
                with original(write=write) as connection:
                    yield connection
                from backend.app.storage.workflow_repository import RetryableWorkflowStorageError
                raise RetryableWorkflowStorageError(diagnostic, commit_outcome_unknown=True)
        monkeypatch.setattr(repo, "_connection", reject)
    response = client.post("/api/v1/rulebooks/extractions", json={"guideline_text": TEXT})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert diagnostic not in response.text and TEXT not in response.text

def test_database_configuration_is_narrow_safe_submission_error(monkeypatch):
    def unavailable(): raise DatabaseConfigurationError("PRIVATE_DATABASE_URL")
    monkeypatch.setattr(rulebook_routes, "ExtractionRepository", unavailable)
    with pytest.raises(ExtractionSubmissionError): rulebook_routes.get_extraction_repository()

def test_success_follow_pin_then_review_and_confirm_does_not_change_v1(api):
    client, repo = api
    queued = submit(client)
    claim = repo.claim_next(worker_id="parent_worker")
    running = client.get(f"/api/v1/jobs/{queued['job_id']}")
    assert running.status_code == 200 and running.json()["status"] == "running"
    repo.publish(claim, draft_for(claim))
    polled = client.get(f"/api/v1/jobs/{queued['job_id']}")
    assert polled.status_code == 200 and polled.json()["status"] == "completed"
    pin = polled.json()["result"]["href"]
    initial = client.get(pin).json()
    assert initial["version"] == 1 and initial["confirmation_state"] == "draft"
    assert all(rule["origin"] == "extracted" and rule["confirmation_state"] == "draft" for rule in initial["rules"])
    book_id = initial["rulebook_id"]
    assert client.get(f"/api/v1/rulebooks/{book_id}/reviews").json()["items"] == []
    rules = [{**rule, "confirmation_state": "confirmed"} for rule in initial["rules"]]
    saved = client.put(f"/api/v1/rulebooks/{book_id}", json={
        "expected_version": 1, "reviewer_id": "reviewer", "guideline_text": TEXT, "rules": rules,
    })
    assert saved.status_code == 200 and saved.json()["version"] == 2
    confirmed = client.post(f"/api/v1/rulebooks/{book_id}/confirm", json={
        "expected_version": 2, "reviewer_id": "reviewer",
    })
    assert confirmed.status_code == 200 and confirmed.json()["version"] == 3
    assert client.get(pin).json() == initial
    assert client.get(f"/api/v1/jobs/{queued['job_id']}").json() == polled.json()

@pytest.mark.parametrize("state", ["queued", "running", "completed", "failed", "cancelling", "cancelled"])
def test_every_extraction_state_uses_existing_http200_polling(api, state):
    client, repo = api
    job = submit(client)
    if state == "cancelled":
        response = client.post(f"/api/v1/jobs/{job['job_id']}/cancel")
        assert response.status_code == 202
    elif state != "queued":
        claim = repo.claim_next(worker_id="worker")
        if state == "completed": repo.publish(claim, draft_for(claim))
        elif state == "failed": repo.fail(claim, "MODEL_UNAVAILABLE")
        elif state == "cancelling": client.post(f"/api/v1/jobs/{job['job_id']}/cancel")
    polled = client.get(f"/api/v1/jobs/{job['job_id']}")
    assert polled.status_code == 200 and polled.json()["status"] == state
    if state == "failed":
        assert polled.json()["error"]["code"] == "MODEL_UNAVAILABLE"
        assert RulebookRepository(settings=repo.settings).list_rulebooks().total == 0
    if state in {"completed", "failed", "cancelled"}:
        conflict = client.post(f"/api/v1/jobs/{job['job_id']}/cancel")
        assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "JOB_ALREADY_FINISHED"

def test_running_cancel_is_acknowledged_without_output_and_repeated_request_is_supported(api):
    client, repo = api
    queued = submit(client)
    claim = repo.claim_next(worker_id="worker")
    for _ in range(2):
        response = client.post(f"/api/v1/jobs/{queued['job_id']}/cancel")
        assert response.status_code == 202 and response.json()["status"] == "cancelling"
    assert repo.checkpoint(claim).status is JobStatus.CANCELLED
    assert RulebookRepository(settings=repo.settings).list_rulebooks().total == 0

def test_manual_creation_remains_independent_and_does_not_queue_extraction(api):
    client, repo = api
    response = client.post("/api/v1/rulebooks", json={"guideline_text": "", "rules": []})
    assert response.status_code == 201
    assert response.json()["confirmation_state"] == "draft"
    assert repo.claim_next(worker_id="no_extraction") is None
