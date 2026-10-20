"""Authored opt-in PostgreSQL regressions; reuse the existing isolated fixtures.

These scenarios are intentionally NOT executed as part of this handoff.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Barrier

from pydantic import ValidationError
import pytest

from backend.app.core.config import OllamaSettings
from backend.app.models.job import Job, JobKind, JobStatus, Progress, ResultReference
from backend.app.models.rule import Rulebook
from backend.app.models.rule_extraction import ExtractionClaim, ExtractionPolicy, RuleExtractionRequest
from backend.app.storage import extraction_repository as extraction
from backend.app.storage import workflow_repository
from backend.app.storage.database import DatabaseError, database_connection, initialize_database
from backend.app.storage.extraction_repository import ExtractionRepository
from backend.app.storage.repository import RulebookNotFoundError, RulebookRepository
from backend.app.storage.workflow_repository import (
    AuditRepository, JobAlreadyFinishedError, JobClaim, JobClaimLostError,
    JobRepository, RetryableWorkflowStorageError, WorkflowStorageError,
)
from backend.tests.test_repository import empty_database_settings, database_settings
from backend.tests.test_workflow_repository import _run_after_lock_wait

TEXT = "\n  Allowed labels are person.\nvéhicule\t"

def model_settings():
    return OllamaSettings(model="local:latest", host="http://127.0.0.1:11434",
                          timeout_seconds=60.0, context_length=4096, max_output_tokens=2048)

@pytest.fixture
def repository(database_settings):
    return ExtractionRepository(settings=database_settings)

def accept(repository, **policy):
    return repository.enqueue(RuleExtractionRequest(guideline_text=TEXT), settings=model_settings(),
                              policy=ExtractionPolicy(**policy))

def claim_one(repository, **policy):
    job = accept(repository, **policy)
    claim = repository.claim_next(worker_id="worker_a")
    assert claim is not None and claim.job.job_id == job.job_id
    return claim

def draft_for(claim, *, empty=False):
    rules = [] if empty else [{
        "rule_id": "LBL-1", "description": "Use allowed labels.", "check_name": "allowed_labels",
        "parameters": {"allowed_labels": ["person"]}, "origin": "extracted",
        "source_sentence": "Allowed labels are person.", "confirmation_state": "draft",
    }]
    return {"rulebook_id": claim.request.rulebook_id, "version": 1,
            "guideline_text": TEXT, "rules": rules, "confirmation_state": "draft"}

def sql(repository, statement, parameters=()):
    with database_connection(repository.settings) as connection:
        return connection.execute(statement, parameters).fetchall() if statement.lstrip().upper().startswith("SELECT") else connection.execute(statement, parameters).rowcount

def no_output(repository, claim):
    with pytest.raises(RulebookNotFoundError):
        RulebookRepository(settings=repository.settings).get_rulebook(claim.request.rulebook_id)

def test_exact_inputs_are_private_restart_stable_and_acceptance_has_no_book(repository):
    job = accept(repository)
    row = sql(repository, "SELECT * FROM rule_extraction_requests WHERE job_id=%s", (job.job_id,))[0]
    assert row["guideline_text"] == TEXT
    assert row["settings"] == model_settings().model_dump()
    assert row["policy"] == ExtractionPolicy().model_dump()
    assert row["started_at"] is None and row["deadline"] is None
    assert set(job.model_dump()) == {"job_id", "kind", "status", "progress", "result", "error", "created_at", "updated_at"}
    reopened = ExtractionRepository(settings=repository.settings)
    claim = reopened.claim_next(worker_id="reopened_worker")
    assert claim.request.guideline_text == TEXT
    assert claim.request.settings == model_settings()
    no_output(repository, claim)
    assert sql(repository, "SELECT * FROM rulebook_reviews") == []
    assert claim.request.deadline - claim.request.started_at == timedelta(seconds=300)

def test_invalid_mutated_public_and_settings_models_rejected_before_connection(repository, monkeypatch):
    def forbid(**kwargs):
        pytest.fail("Invalid models must not open a connection.")
    monkeypatch.setattr(repository, "_connection", forbid)
    request = RuleExtractionRequest(guideline_text=TEXT)
    request.guideline_text = " \n"
    with pytest.raises(ValidationError):
        repository.enqueue(request, settings=model_settings())
    bad = model_settings()
    object.__setattr__(bad, "host", "http://example.com")
    with pytest.raises(ValidationError):
        repository.enqueue(RuleExtractionRequest(guideline_text=TEXT), settings=bad)

def test_enqueue_rollback_leaves_neither_row(repository, monkeypatch):
    original = repository._connection
    @contextmanager
    def reject(*, write=False):
        with original(write=write) as connection:
            yield connection
            if write:
                raise DatabaseError("Known precommit rollback.")
    with monkeypatch.context() as patch:
        patch.setattr(repository, "_connection", reject)
        with pytest.raises(WorkflowStorageError):
            accept(repository)
    assert sql(repository, "SELECT * FROM jobs") == []
    assert sql(repository, "SELECT * FROM rule_extraction_requests") == []

def test_two_workers_cannot_claim_the_same_queued_attempt(repository):
    accept(repository)
    barrier = Barrier(2)
    def take(name):
        barrier.wait(timeout=5)
        return ExtractionRepository(settings=repository.settings).claim_next(worker_id=name)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(take, ["worker_a", "worker_b"]))
    assert sum(claim is not None for claim in claims) == 1
    assert sql(repository, "SELECT attempt FROM jobs")[0]["attempt"] == 1

def test_extraction_and_generic_claim_recovery_do_not_steal_each_others_work(repository):
    generic = JobRepository(settings=repository.settings)
    audit = generic.create_job(Job.queued(job_id="queued_audit", kind=JobKind.AUDIT, total=1))
    job = accept(repository)
    extraction_claim = repository.claim_next(worker_id="extraction")
    assert extraction_claim.job.job_id == job.job_id
    audit_claim = generic.claim_next_job(worker_id="audit")
    assert audit_claim.job.job_id == audit.job_id
    sql(repository, "UPDATE jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE job_id IN (%s,%s)",
        (job.job_id, audit.job_id))
    assert [job.job_id for job in repository.recover_stale()] == [job.job_id]
    assert generic.get_job(audit.job_id).status is JobStatus.RUNNING
    assert [saved.job_id for saved in generic.recover_stale_jobs()] == [audit.job_id]
    assert generic.get_job(job.job_id).status is JobStatus.FAILED
    assert repository.claim_next(worker_id="no_retry") is None

@pytest.mark.parametrize("method", ["claim", "renew", "progress", "complete", "fail", "cancel_final"])
def test_generic_direct_mutations_reject_the_locked_managed_kind(repository, method):
    claim = claim_one(repository)
    generic = JobRepository(settings=repository.settings)
    forged = JobClaim(job=claim.job, worker_id=claim.worker_id, claim_token=claim.claim_token,
                      lease_until=claim.lease_until, resource_kind="audit", resource_id="forged")
    from backend.app.storage.extraction_repository import safe_error
    actions = {
        "claim": lambda: generic.claim_job(claim.job.job_id, worker_id="other"),
        "renew": lambda: generic.renew_claim(forged),
        "progress": lambda: generic.update_progress(forged, Progress(phase="x", processed=0, total=1, fraction=0.0)),
        "complete": lambda: generic.complete_job(forged, ResultReference(kind="rulebook", id="x", href="/x")),
        "fail": lambda: generic.fail_job(forged, safe_error("EXTRACTION_FAILED", claim.job.job_id)),
        "cancel_final": lambda: generic.finalize_cancellation(forged),
    }
    before = sql(repository, "SELECT * FROM jobs")
    with pytest.raises(ValueError, match="ExtractionRepository"):
        actions[method]()
    assert sql(repository, "SELECT * FROM jobs") == before

def test_generic_create_cannot_bypass_private_enqueue(repository):
    generic = JobRepository(settings=repository.settings)
    with pytest.raises(ValueError):
        generic.create_job(Job.queued(job_id="bypass", kind=JobKind.RULE_EXTRACTION, total=1))
    assert sql(repository, "SELECT * FROM jobs") == []

@pytest.mark.parametrize("empty", [False, True])
def test_atomic_publication_pins_v1_and_leaves_empty_review_history(repository, empty):
    claim = claim_one(repository)
    completed = repository.publish(claim, draft_for(claim, empty=empty))
    assert completed.status is JobStatus.COMPLETED and completed.error is None
    assert completed.result.href == f"/api/v1/rulebooks/{claim.request.rulebook_id}?version=1"
    store = RulebookRepository(settings=repository.settings)
    v1 = store.get_rulebook(claim.request.rulebook_id, version=1)
    assert v1.version == 1 and v1.confirmation_state.value == "draft"
    assert store.list_rulebook_reviews(v1.rulebook_id).items == []
    assert completed == repository.readback(claim)
    with pytest.raises(JobClaimLostError):
        repository.publish(claim, draft_for(claim))
    assert store.list_rulebook_versions(v1.rulebook_id).total == 1

def test_publication_known_rollback_is_not_a_success_or_an_orphan(repository, monkeypatch):
    claim = claim_one(repository)
    original = repository._connection
    @contextmanager
    def reject(*, write=False):
        with original(write=write) as connection:
            yield connection
            if write:
                raise DatabaseError("Known precommit rollback.")
    with monkeypatch.context() as patch:
        patch.setattr(repository, "_connection", reject)
        with pytest.raises(WorkflowStorageError):
            repository.publish(claim, draft_for(claim))
    no_output(repository, claim)
    assert repository.readback(claim).status is JobStatus.RUNNING

def test_unknown_commit_is_read_back_unchanged_and_never_failed(repository, monkeypatch):
    from backend.app.services.extraction_jobs import _finish
    claim = claim_one(repository)
    original = repository._connection
    @contextmanager
    def uncertain(*, write=False):
        with original(write=write) as connection:
            yield connection
        if write:
            raise RetryableWorkflowStorageError("Lost acknowledgement", commit_outcome_unknown=True)
    with monkeypatch.context() as patch:
        patch.setattr(repository, "_connection", uncertain)
        completed = _finish(repository, claim, payload=draft_for(claim))
    assert completed.status is JobStatus.COMPLETED
    assert repository.readback(claim) == completed

def test_reserved_id_collision_preserves_existing_versions_and_history(repository):
    claim = claim_one(repository)
    store = RulebookRepository(settings=repository.settings)
    existing = Rulebook(rulebook_id=claim.request.rulebook_id, guideline_text="Unrelated existing book.")
    store.save_rulebook(existing)
    saved = repository.publish(claim, draft_for(claim))
    assert saved.error.code == "EXTRACTION_OUTPUT_CONFLICT"
    assert store.get_rulebook(existing.rulebook_id) == existing
    assert store.list_rulebook_versions(existing.rulebook_id).total == 1
    assert store.list_rulebook_reviews(existing.rulebook_id).items == []

@pytest.mark.parametrize("corrupt", ["id", "version", "source", "book_state", "origin", "rule_state", "duplicate", "citation", "nan", "unknown_check", "parameters"])
def test_invalid_worker_drafts_fail_safely_without_output(repository, corrupt):
    claim = claim_one(repository)
    draft = draft_for(claim)
    if corrupt == "id": draft["rulebook_id"] = "other"
    elif corrupt == "version": draft["version"] = 2
    elif corrupt == "source": draft["guideline_text"] = TEXT.strip()
    elif corrupt == "book_state": draft["confirmation_state"] = "rejected"
    elif corrupt == "origin": draft["rules"][0]["origin"] = "reviewer"
    elif corrupt == "rule_state": draft["rules"][0]["confirmation_state"] = "confirmed"
    elif corrupt == "duplicate": draft["rules"].append(dict(draft["rules"][0]))
    elif corrupt == "citation": draft["rules"][0]["source_sentence"] = "Invented citation"
    elif corrupt == "nan": draft["rules"][0]["parameters"] = {"nested": [float("nan")]}
    elif corrupt == "unknown_check": draft["rules"][0]["check_name"] = "not_a_check"
    else: draft["rules"][0]["parameters"] = {}
    saved = repository.publish(claim, draft)
    assert saved.error.code == "INVALID_RULE_PROPOSALS" and saved.result is None
    no_output(repository, claim)

@pytest.mark.parametrize("tamper", ["worker", "token", "request", "lease", "db_worker", "db_token", "db_resource", "db_kind"])
@pytest.mark.parametrize("operation", ["checkpoint", "fail", "publish"])
def test_foreign_expired_or_inconsistent_claims_cannot_mutate(repository, tamper, operation):
    claim = claim_one(repository)
    raw = claim.model_dump()
    if tamper == "worker": raw["worker_id"] = "foreign"
    elif tamper == "token": raw["claim_token"] = "foreign"
    elif tamper == "request": raw["request"]["guideline_text"] = "Other text."
    elif tamper == "lease": sql(repository, "UPDATE jobs SET lease_until=clock_timestamp()-interval '1 second'")
    elif tamper == "db_worker": sql(repository, "UPDATE jobs SET claimed_by='foreign'")
    elif tamper == "db_token": sql(repository, "UPDATE jobs SET claim_token='foreign'")
    elif tamper == "db_resource": sql(repository, "UPDATE jobs SET resource_id='foreign'")
    else: sql(repository, "UPDATE jobs SET kind='audit'")
    supplied = ExtractionClaim.model_validate(raw)
    before = sql(repository, "SELECT * FROM jobs")
    with pytest.raises((JobClaimLostError, WorkflowStorageError)):
        if operation == "checkpoint": repository.checkpoint(supplied, renew=True)
        elif operation == "fail": repository.fail(supplied, "EXTRACTION_FAILED")
        else: repository.publish(supplied, draft_for(claim))
    assert sql(repository, "SELECT * FROM jobs") == before
    no_output(repository, claim)

@pytest.mark.parametrize("boundary", ["lease", "deadline"])
@pytest.mark.parametrize("lock", ["row", "advisory"])
def test_fresh_authorization_after_real_blocking_lock(repository, monkeypatch, boundary, lock):
    claim = claim_one(repository)
    before = claim.request.started_at
    after = claim.lease_until if boundary == "lease" else claim.request.deadline
    if boundary == "deadline":
        sql(repository, "UPDATE jobs SET lease_until=%s", (after + timedelta(seconds=10),))
    # Reuse the established real-lock-wait harness and its controlled live clock.
    monkeypatch.setattr(extraction, "_now", lambda connection: workflow_repository._timestamp())
    query, params = (("SELECT * FROM jobs WHERE job_id=%s FOR UPDATE", (claim.job.job_id,))
                     if lock == "row" else
                     ("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (claim.request.rulebook_id,)))
    def wait():
        return _run_after_lock_wait(repository, lambda: repository.publish(claim, draft_for(claim)),
            monkeypatch=monkeypatch, lock_query=query, lock_params=params, before=before, after=after)
    if boundary == "lease":
        with pytest.raises(JobClaimLostError): wait()
        assert JobRepository(settings=repository.settings).get_job(claim.job.job_id).status is JobStatus.RUNNING
    else:
        assert wait().error.code == "EXTRACTION_TIMEOUT"
    no_output(repository, claim)

@pytest.mark.parametrize("order", ["cancel_first", "complete_first"])
def test_existing_audit_aware_cancel_dispatch_preserves_race_order(repository, order):
    claim = claim_one(repository)
    dispatcher = AuditRepository(settings=repository.settings)
    if order == "cancel_first":
        assert dispatcher.cancel_job(claim.job.job_id).status is JobStatus.CANCELLING
        assert dispatcher.cancel_job(claim.job.job_id).status is JobStatus.CANCELLING
        assert repository.publish(claim, draft_for(claim)).status is JobStatus.CANCELLED
        no_output(repository, claim)
    else:
        repository.publish(claim, draft_for(claim))
        with pytest.raises(JobAlreadyFinishedError): dispatcher.cancel_job(claim.job.job_id)
        assert repository.readback(claim).status is JobStatus.COMPLETED

def test_queued_cancel_never_claims_or_publishes(repository):
    job = accept(repository)
    assert AuditRepository(settings=repository.settings).cancel_job(job.job_id).status is JobStatus.CANCELLED
    assert repository.claim_next(worker_id="worker") is None
    assert sql(repository, "SELECT * FROM rulebook_versions") == []

@pytest.mark.parametrize("cancelling,timeout", [(False, False), (False, True), (True, False), (True, True)])
def test_stale_attempt_fails_or_cancels_once_without_requeue(repository, cancelling, timeout, monkeypatch):
    claim = claim_one(repository)
    if cancelling:
        AuditRepository(settings=repository.settings).cancel_job(claim.job.job_id)
    now = claim.request.deadline if timeout else claim.lease_until
    monkeypatch.setattr(extraction, "_now", lambda connection: now)
    sql(repository, "UPDATE jobs SET lease_until=clock_timestamp()-interval '1 second'")
    recovered = repository.recover_stale()
    assert len(recovered) == 1
    if cancelling:
        assert recovered[0].status is JobStatus.CANCELLED
    else:
        assert recovered[0].error.code == ("EXTRACTION_TIMEOUT" if timeout else "EXTRACTION_INTERRUPTED")
    assert repository.recover_stale() == []
    assert repository.claim_next(worker_id="second") is None
    assert sql(repository, "SELECT attempt,recovery_count FROM jobs")[0] == {"attempt": 1, "recovery_count": 1}
    no_output(repository, claim)

def test_deadline_expiry_with_live_lease_records_timeout_but_expired_owner_cannot(repository, monkeypatch):
    claim = claim_one(repository)
    sql(repository, "UPDATE jobs SET lease_until=%s", (claim.request.deadline + timedelta(seconds=1),))
    monkeypatch.setattr(extraction, "_now", lambda connection: claim.request.deadline)
    assert repository.checkpoint(claim, renew=True).error.code == "EXTRACTION_TIMEOUT"
    with pytest.raises(JobClaimLostError): repository.fail(claim, "EXTRACTION_TIMEOUT")

def test_upgrade_keeps_prior_rulebook_reviews_and_audit_data(empty_database_settings, tmp_path, monkeypatch):
    from backend.app.storage import database
    from backend.app.models.audit import BatchAuditRequest
    from backend.app.models.review import RulebookReviewAction
    old_directory = tmp_path / "old_migrations"
    old_directory.mkdir()
    for path in database.MIGRATIONS_DIRECTORY.glob("[0-9][0-9][0-9]_*.sql"):
        if int(path.name[:3]) < 9:
            (old_directory / path.name).write_text(path.read_text(), encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(database, "MIGRATIONS_DIRECTORY", old_directory)
        initialize_database(empty_database_settings)
    store = RulebookRepository(settings=empty_database_settings)
    book = store.save_rulebook(Rulebook(rulebook_id="upgrade_book", guideline_text="Original"))
    reviewed = Rulebook.model_validate({**book.model_dump(), "version": 2})
    store.save_rulebook_review(reviewed, expected_version=1, reviewer_id="reviewer",
                               action=RulebookReviewAction.EDIT)
    audits = AuditRepository(settings=empty_database_settings)
    request = BatchAuditRequest(dataset_id="old_dataset", dataset_root="opaque-root",
                               rulebook_id=book.rulebook_id, rulebook_version=2, sample_ids=["old_sample"])
    audit, job = audits.start_audit(request, audit_id="old_audit", job_id="old_job")
    from backend.tests.test_workflow_repository import _saved_workflow_state
    before = _saved_workflow_state(empty_database_settings)
    initialize_database(empty_database_settings)
    initialize_database(empty_database_settings)
    assert _saved_workflow_state(empty_database_settings) == before
    assert store.get_rulebook(book.rulebook_id, version=1) == book
    assert store.list_rulebook_reviews(book.rulebook_id).total == 1
    assert audits.get_audit(audit.audit_id) == audit
    assert JobRepository(settings=empty_database_settings).get_job(job.job_id) == job

def test_expired_cancelling_owner_cannot_acknowledge_but_recovery_can(repository):
    claim = claim_one(repository)
    AuditRepository(settings=repository.settings).cancel_job(claim.job.job_id)
    sql(repository, "UPDATE jobs SET lease_until=clock_timestamp()-interval '1 second'")
    before = sql(repository, "SELECT * FROM jobs")
    with pytest.raises(JobClaimLostError): repository.checkpoint(claim)
    with pytest.raises(JobClaimLostError): repository.fail(claim, "EXTRACTION_TIMEOUT")
    with pytest.raises(JobClaimLostError): repository.publish(claim, draft_for(claim))
    assert sql(repository, "SELECT * FROM jobs") == before
    assert repository.recover_stale()[0].status is JobStatus.CANCELLED
    no_output(repository, claim)

def test_repeated_renewal_keeps_original_start_deadline_and_single_attempt(repository):
    claim = claim_one(repository)
    for _ in range(3):
        assert repository.checkpoint(claim, renew=True).status is JobStatus.RUNNING
    request = sql(repository, "SELECT started_at,deadline FROM rule_extraction_requests")[0]
    assert request == {"started_at": claim.request.started_at, "deadline": claim.request.deadline}
    assert sql(repository, "SELECT attempt FROM jobs")[0]["attempt"] == 1

def test_mutated_nested_draft_is_revalidated_instead_of_trusting_model_instance(repository):
    claim = claim_one(repository)
    draft = Rulebook.model_validate(draft_for(claim))
    draft.rules[0].source_sentence = "Citation absent from the saved request."
    assert repository.publish(claim, draft).error.code == "INVALID_RULE_PROPOSALS"
    no_output(repository, claim)
