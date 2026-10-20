"""Authored managed lifecycle/SQL regressions. Opt-in PostgreSQL; NOT executed."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Event

import pytest

from backend.app.models.dataset_import import ImportClaim
from backend.app.models.job import ApiError, Job, JobKind, Progress, ResultReference
from backend.app.storage.dataset_import_repository import DatasetImportRepository
from backend.app.storage.dataset_repository import DatasetCommitOutcomeUnknownError, DatasetStorageError
from backend.app.storage.workflow_repository import AuditRepository, JobAlreadyFinishedError, JobClaim, JobClaimLostError, JobRepository
from backend.tests.dataset_fixtures import (
    dataset_database_settings, empty_dataset_database_settings, prepared_sample, read_sql, seed_import, write_sql,
)


@pytest.fixture
def repository(dataset_database_settings):
    return DatasetImportRepository(settings=dataset_database_settings)


def test_queued_claim_is_single_attempt_and_two_claimants_cannot_share(repository):
    dataset, request, _ = seed_import(repository.settings, started=False)
    def acquire(worker):
        return DatasetImportRepository(settings=repository.settings).claim_next(worker_id=worker)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(acquire, ["one", "two"]))
    assert sum(claim is not None for claim in claims) == 1
    claim = next(claim for claim in claims if claim is not None)
    assert claim.request.archive_key == request.archive_key
    assert claim.request.started_at is not None and claim.request.deadline is not None
    assert repository.claim_next(worker_id="third") is None
    assert read_sql(repository.settings, "SELECT attempt FROM jobs")[0]["attempt"] == 1


def test_dirty_samples_ready_null_aggregate_and_warning_preserved(repository):
    _, _, claim = seed_import(repository.settings)
    repository.discover(claim, 1, unsupported=True)
    publication = prepared_sample()
    repository._publish_import_sample(claim, publication)
    repository.record_progress(claim)
    job = repository.finish(claim)
    dataset = repository.get_dataset("dataset_a")
    assert dataset.status.value == "ready" and dataset.object_count is None
    assert dataset.sample_count == 1 and dataset.issues
    assert job.status.value == "completed" and job.progress.processed == 1
    assert job.result.kind == "dataset" and job.result.version is None
    assert job.result.href == "/api/v1/datasets/dataset_a"
    assert repository.get_sample("dataset_a", "sample_a").objects[0].object_id == -1


@pytest.mark.parametrize("code,status", [("INVALID_ARCHIVE", "invalid"), ("INVALID_LAYOUT", "invalid"),
                                        ("IMPORT_STORAGE_FAILED", "failed"), ("IMPORT_RESOURCE_LIMIT", "failed"),
                                        ("IMPORT_INTERRUPTED", "failed"), ("IMPORT_TIMEOUT", "failed")])
def test_terminal_classification_safe_public_errors(repository, code, status):
    _, _, claim = seed_import(repository.settings)
    job = repository.finish(claim, code=code)
    assert job.status.value == "failed" and job.error.code == code
    assert repository.get_dataset("dataset_a").status.value == status
    public = job.model_dump_json()
    assert claim.claim_token not in public and claim.request.archive_key not in public
    assert claim.request.archive_sha256 not in public


@pytest.mark.parametrize("public_boundary", ["generic", "audit", "managed"])
def test_queued_cancel_couples_dataset_and_job(repository, public_boundary):
    dataset, _, _ = seed_import(repository.settings, started=False)
    if public_boundary == "generic":
        result = JobRepository(settings=repository.settings).request_cancellation(dataset.import_job_id)
    elif public_boundary == "audit":
        result = AuditRepository(settings=repository.settings).cancel_job(dataset.import_job_id)
    else:
        result = repository.cancel(dataset.import_job_id)
    assert result.status.value == "cancelled"
    assert repository.get_dataset(dataset.dataset_id).status.value == "cancelled"
    assert repository.list_samples(dataset.dataset_id).items == []
    with pytest.raises(JobAlreadyFinishedError):
        repository.cancel(dataset.import_job_id)


def test_running_cancel_is_idempotent_wins_completion_and_retains_evidence(repository):
    _, _, claim = seed_import(repository.settings)
    repository.discover(claim, 2)
    repository._publish_import_sample(claim, prepared_sample())
    first = repository.cancel(claim.job.job_id)
    second = repository.cancel(claim.job.job_id)
    assert first == second and first.status.value == "cancelling"
    terminal = repository.finish(claim, code="IMPORT_TIMEOUT")
    assert terminal.status.value == "cancelled" and terminal.error is None
    assert repository.get_dataset("dataset_a").status.value == "cancelled"
    assert repository.list_samples("dataset_a").total == 1
    assert repository.get_sample("dataset_a", "sample_a").revision == 1


@pytest.mark.parametrize("cancel", [False, True])
def test_stale_single_attempt_fails_or_cancels_never_requeues(repository, cancel):
    _, _, claim = seed_import(repository.settings)
    if cancel:
        repository.cancel(claim.job.job_id)
    write_sql(repository.settings, "UPDATE jobs SET lease_until=clock_timestamp()-interval '1 second'")
    assert JobRepository(settings=repository.settings).recover_stale_jobs() == []
    jobs = repository.recover_stale()
    assert len(jobs) == 1 and jobs[0].status.value == ("cancelled" if cancel else "failed")
    if not cancel:
        assert jobs[0].error.code == "IMPORT_INTERRUPTED"
    assert repository.claim_next(worker_id="replacement") is None
    with pytest.raises(JobClaimLostError):
        repository._publish_import_sample(claim, prepared_sample())


def test_foreign_claim_cannot_renew_progress_or_finalize(repository):
    _, _, claim = seed_import(repository.settings)
    changed = ImportClaim.model_validate({**claim.model_dump(), "claim_token": "foreign"})
    for operation in [lambda: repository.heartbeat(changed), lambda: repository.discover(changed, 1),
                      lambda: repository.record_progress(changed), lambda: repository.finish(changed, code="IMPORT_FAILED"),
                      lambda: repository._publish_import_sample(changed, prepared_sample())]:
        with pytest.raises(JobClaimLostError):
            operation()
    assert repository.get_dataset("dataset_a").status.value == "importing"


def test_incomplete_catalogue_cannot_be_ready(repository):
    _, _, claim = seed_import(repository.settings)
    repository.discover(claim, 2)
    repository._publish_import_sample(claim, prepared_sample())
    with pytest.raises(DatasetStorageError):
        repository.finish(claim)
    assert repository.get_dataset("dataset_a").status.value == "importing"


def test_generic_execution_checks_locked_kind_not_forged_claim(repository):
    _, _, managed = seed_import(repository.settings)
    jobs = JobRepository(settings=repository.settings)
    # Caller supplies an audit snapshot for a stored dataset_import job.
    fake = Job.model_validate({**managed.job.model_dump(), "kind": "audit"})
    forged = JobClaim(job=fake, worker_id=managed.worker_id, claim_token=managed.claim_token,
                      lease_until=managed.lease_until, resource_kind=None, resource_id=None)
    with pytest.raises(ValueError):
        jobs.create_job(Job.queued(job_id="new", kind=JobKind.DATASET_IMPORT, total=None))
    with pytest.raises(ValueError):
        jobs.claim_job(managed.job.job_id, worker_id="generic")
    assert jobs.claim_next_job(worker_id="generic") is None
    for operation in [lambda: jobs.renew_claim(forged), lambda: jobs.update_progress(forged, Progress(phase="x", processed=0)),
                      lambda: jobs.complete_job(forged, ResultReference(kind="dataset", id="x", href="/x")),
                      lambda: jobs.fail_job(forged, ApiError(code="X", message="safe", request_id="x")),
                      lambda: jobs.finalize_cancellation(forged)]:
        with pytest.raises(ValueError):
            operation()
    assert jobs.get_job(managed.job.job_id).status.value == "running"


def test_terminal_transaction_rolls_back_dataset_when_job_write_fails(repository, monkeypatch):
    _, _, claim = seed_import(repository.settings)
    original = repository._job
    def fail(*args, **kwargs):
        raise DatasetStorageError("Injected precommit failure.")
    monkeypatch.setattr(repository, "_job", fail)
    with pytest.raises(DatasetStorageError):
        repository.finish(claim, code="IMPORT_FAILED")
    monkeypatch.setattr(repository, "_job", original)
    assert repository.read_job(claim.job.job_id).status.value == "running"
    assert repository.get_dataset("dataset_a").status.value == "importing"


def test_unknown_terminal_commit_is_not_replayed(repository, monkeypatch):
    _, _, claim = seed_import(repository.settings)
    original = repository._connection
    @contextmanager
    def uncertain(*, write=False):
        with original(write=write) as connection:
            yield connection
        if write:
            raise DatasetCommitOutcomeUnknownError("Injected lost acknowledgement.")
    monkeypatch.setattr(repository, "_connection", uncertain)
    with pytest.raises(DatasetCommitOutcomeUnknownError):
        repository.finish(claim, code="IMPORT_FAILED")
    monkeypatch.setattr(repository, "_connection", original)
    assert repository.read_job(claim.job.job_id).status.value == "failed"
    with pytest.raises(JobClaimLostError):
        repository.finish(claim, code="IMPORT_FAILED")


def test_known_zero_object_count_is_not_unknown(repository):
    _, _, claim = seed_import(repository.settings)
    repository.discover(claim, 1)
    raw = prepared_sample().model_dump()
    raw["sample"]["objects"] = []
    raw["annotation_object_count"] = 0
    from backend.app.models.dataset_import import SamplePublication
    repository._publish_import_sample(claim, SamplePublication.model_validate(raw))
    repository.record_progress(claim)
    repository.finish(claim)
    assert repository.get_dataset("dataset_a").object_count == 0


def test_lease_expired_while_waiting_for_job_lock_cannot_renew(repository):
    from backend.app.storage.database import database_connection
    _, _, claim = seed_import(repository.settings)
    began = Event()
    def waiting_heartbeat():
        began.set()
        with pytest.raises(JobClaimLostError):
            repository.heartbeat(claim)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with database_connection(repository.settings) as connection:
            connection.execute("SELECT * FROM jobs WHERE job_id=%s FOR UPDATE", (claim.job.job_id,))
            connection.execute("UPDATE jobs SET lease_until=clock_timestamp()+interval '0.2 seconds'")
            future = pool.submit(waiting_heartbeat)
            assert began.wait(2)
            # Explicit test-only wait; production code must sample time AFTER the lock.
            connection.execute("SELECT pg_sleep(0.3)")
        future.result(timeout=5)
    assert repository.read_job(claim.job.job_id).status.value == "running"
    assert repository.recover_stale()[0].status.value == "failed"


def test_committed_cancel_wins_waiting_finish_without_losing_sample(repository):
    from backend.app.storage.database import database_connection
    _, _, claim = seed_import(repository.settings)
    repository.discover(claim, 1)
    repository._publish_import_sample(claim, prepared_sample())
    repository.record_progress(claim)
    began = Event()
    def finish_after_lock():
        began.set()
        return repository.finish(claim)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with database_connection(repository.settings) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=%s FOR UPDATE", (claim.job.job_id,)).fetchone()
            repository._cancel_locked(connection, row)
            future = pool.submit(finish_after_lock)
            assert began.wait(2)
        assert future.result(timeout=5).status.value == "cancelled"
    assert repository.list_samples("dataset_a").total == 1
    assert repository.get_dataset("dataset_a").status.value == "cancelled"


def test_original_deadline_expiry_is_timeout_not_resume(repository):
    from datetime import UTC, datetime
    _, _, claim = seed_import(repository.settings, now=datetime.now(UTC)-timedelta(days=2))
    terminal = repository.recover_stale()[0]
    assert terminal.status.value == "failed" and terminal.error.code == "IMPORT_TIMEOUT"
    assert repository.get_dataset("dataset_a").status.value == "failed"
    assert repository.claim_next(worker_id="restart") is None


def test_already_completed_import_rejects_late_cancel(repository):
    _, _, claim = seed_import(repository.settings)
    repository.discover(claim, 1)
    repository._publish_import_sample(claim, prepared_sample())
    repository.record_progress(claim)
    completed = repository.finish(claim)
    with pytest.raises(JobAlreadyFinishedError):
        JobRepository(settings=repository.settings).request_cancellation(claim.job.job_id)
    assert repository.read_job(claim.job.job_id) == completed
    assert repository.get_dataset("dataset_a").status.value == "ready"
