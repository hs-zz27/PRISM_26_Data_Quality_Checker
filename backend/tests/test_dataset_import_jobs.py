"""Authored supervision/transport tests. None were executed for this handoff."""
from datetime import UTC, datetime, timedelta
import json
import multiprocessing
import time

import pytest

from backend.app.models.dataset_import import ImportClaim, ImportPolicy, ImportRequest
from backend.app.models.job import Job, JobKind
from backend.app.services import dataset_import_jobs as module
from backend.app.storage.dataset_import_repository import LeaseAcknowledgement, DatasetImportRepository
from backend.app.storage.dataset_repository import DatasetCommitOutcomeUnknownError
from backend.app.core.dataset_import import ImportOutcomeUnresolved, ImportResourceError
from backend.app.services.dataset_upload import submit_archive
from backend.tests.dataset_import_fakes import entries, staged, zip_bytes
from backend.tests.dataset_fixtures import dataset_database_settings, empty_dataset_database_settings


def claim():
    now = datetime.now(UTC)
    request = ImportRequest(job_id="job_a", dataset_id="dataset_a", archive_key="input.zip",
                            archive_sha256="a"*64, archive_size_bytes=3, policy=ImportPolicy(),
                            started_at=now, deadline=now+timedelta(seconds=900))
    job = Job.queued(job_id=request.job_id, kind=JobKind.DATASET_IMPORT, total=None, now=now)
    job = Job.model_validate({**job.model_dump(), "status": "running"})
    return ImportClaim(job=job, request=request, worker_id="worker", claim_token="token",
                       lease_until=now+timedelta(seconds=30))


def test_transport_reads_only_atomic_final_file_and_bounds_actual_bytes(tmp_path):
    (tmp_path / "message.part").write_bytes(b'{"incomplete":')
    assert module._read_message(tmp_path, 1024) is None
    (tmp_path / "message.json").write_bytes(b"x"*1025)
    with pytest.raises(ImportResourceError):
        module._read_message(tmp_path, 1024)


@pytest.mark.parametrize("payload", [b'{"kind":"done","kind":"error"}', b'{"value":NaN}', b'[]', b'not JSON'])
def test_transport_rejects_ambiguous_or_invalid_json(tmp_path, payload):
    (tmp_path / "message.json").write_bytes(payload)
    with pytest.raises(ValueError):
        module._read_message(tmp_path, 1024)


def test_emit_waits_for_ack_and_does_not_overwrite_pending_result(tmp_path):
    # A real spawned producer can be terminated even if publication/ack fails.
    # A timed join on a non-daemon thread cannot provide this cleanup guarantee.
    process = multiprocessing.get_context("spawn").Process(
        target=module._emit, args=(tmp_path, {"kind": "done"}, 1024), daemon=True,
    )
    try:
        process.start()
        until = time.monotonic() + 2
        while not (tmp_path / "message.json").exists() and time.monotonic() < until:
            if not process.is_alive():
                break
            time.sleep(.01)
        assert module._read_message(tmp_path, 1024) == {"kind": "done"}
        assert process.is_alive()  # Must still be awaiting parent acknowledgement.
        (tmp_path / "message.json").unlink()
        process.join(timeout=2)
        assert not process.is_alive() and process.exitcode == 0
    finally:
        # Stop before removing the message: no late publication after cleanup.
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
            stopped = not process.is_alive()
            if stopped:
                process.close()
            assert stopped, "Producer did not stop within bounded cleanup."
        (tmp_path / "message.json").unlink(missing_ok=True)
        (tmp_path / "message.part").unlink(missing_ok=True)


class FakeProcess:
    pid = 123
    exitcode = None
    def __init__(self):
        self.alive, self.terminated, self.killed = True, False, False
    def is_alive(self):
        return self.alive
    def terminate(self):
        self.terminated, self.alive = True, False
    def kill(self):
        self.killed, self.alive = True, False
    def join(self, timeout):
        assert timeout <= 1


def test_watchdog_stops_computation_independently_of_parent_database_wait():
    process = FakeProcess()
    now = time.monotonic()
    watchdog = module._Watchdog(process, lease_end=now+.05, deadline_end=now+1)
    watchdog.thread.start()
    try:
        assert watchdog.fired.wait(2)
        watchdog.thread.join(timeout=2)
        assert process.terminated
    finally:
        watchdog.close()


def test_lease_ack_uses_pre_call_time_and_never_extends_hard_deadline():
    process = FakeProcess()
    watchdog = module._Watchdog(process, lease_end=11, deadline_end=15)
    watchdog.acknowledge(10, LeaseAcknowledgement(claim().job, 3, 20))
    assert watchdog.lease_end == 13 and watchdog.deadline_end == 15


def test_unknown_heartbeat_commit_never_starts_child_or_replays(tmp_path):
    _, _, settings = staged(tmp_path, b"zip")
    class Uncertain:
        calls = 0
        def heartbeat(self, value):
            self.calls += 1
            raise DatasetCommitOutcomeUnknownError("lost ack")
    repository = Uncertain()
    class ForbiddenContext:
        def Process(self, **kwargs):
            pytest.fail("Unknown ownership must not start a child.")
    with pytest.raises(ImportOutcomeUnresolved):
        module.run_claimed_import(repository, claim(), settings=settings, context=ForbiddenContext())
    assert repository.calls == 1


def test_service_to_spawned_worker_end_to_end_opt_in(dataset_database_settings, tmp_path):
    # This test is intentionally opt-in through the existing PostgreSQL fixture.
    # It uses only three tiny synthetic assets and never starts Ollama/a detector.
    _, _, settings = staged(tmp_path, zip_bytes(entries()))
    repository = DatasetImportRepository(settings=dataset_database_settings)
    started = submit_archive([zip_bytes(entries())], name="Synthetic zero objects",
                              repository=repository, settings=settings)
    claimed = repository.claim_next(worker_id="test_spawn")
    result = module.run_claimed_import(repository, claimed, settings=settings)
    assert result.status.value == "completed"
    dataset = repository.get_dataset(started.dataset.dataset_id)
    assert dataset.status.value == "ready" and dataset.object_count == 0 and dataset.sample_count == 1
    sample = repository.get_sample(dataset.dataset_id, "a")
    assert sample.revision == 1 and sample.assets.mask.url.endswith("?revision=1")
    assert list((settings.root / "runs").iterdir()) == []
    assert (settings.root / claimed.request.archive_key).is_file()


def test_later_resource_failure_retains_already_published_sample(dataset_database_settings, tmp_path):
    from io import BytesIO
    from PIL import Image
    from backend.app.models.dataset_import import ImportPolicy
    from backend.app.core.dataset_import import ImportSettings
    huge = BytesIO()
    Image.new("RGB", (4, 4)).save(huge, format="PNG")
    content = zip_bytes(entries() + [("PNGImages/z.png", huge.getvalue())])
    _, _, effective = staged(tmp_path, content)
    settings = ImportSettings(root=effective.root, asset_root=effective.asset_root,
                              policy=ImportPolicy(max_image_pixels=6))
    repository = DatasetImportRepository(settings=dataset_database_settings)
    started = submit_archive([content], name="Partial", repository=repository, settings=settings)
    claimed = repository.claim_next(worker_id="partial")
    result = module.run_claimed_import(repository, claimed, settings=settings)
    assert result.status.value == "failed" and result.error.code == "IMPORT_RESOURCE_LIMIT"
    assert repository.get_dataset(started.dataset.dataset_id).status.value == "failed"
    assert repository.list_samples(started.dataset.dataset_id).total == 1
    assert repository.get_sample(started.dataset.dataset_id, "a").revision == 1
    assert (settings.asset_root / repository.resolve_revision(started.dataset.dataset_id, "a").manifest.raw_mask.storage_key).is_file()


def test_cancel_after_publication_retains_files_and_stops_child(dataset_database_settings, tmp_path, monkeypatch):
    _, _, settings = staged(tmp_path, zip_bytes(entries()))
    repository = DatasetImportRepository(settings=dataset_database_settings)
    started = submit_archive([zip_bytes(entries())], name="Cancel", repository=repository, settings=settings)
    claimed = repository.claim_next(worker_id="cancel")
    original = repository._publish_import_sample
    def publish_then_cancel(*args, **kwargs):
        value = original(*args, **kwargs)
        repository.cancel(claimed.job.job_id)
        return value
    monkeypatch.setattr(repository, "_publish_import_sample", publish_then_cancel)
    result = module.run_claimed_import(repository, claimed, settings=settings)
    assert result.status.value == "cancelled"
    assert repository.get_dataset(started.dataset.dataset_id).status.value == "cancelled"
    pin = repository.resolve_revision(started.dataset.dataset_id, "a")
    assert (settings.asset_root / pin.manifest.raw_mask.storage_key).is_file()
    assert repository.list_samples(started.dataset.dataset_id).total == 1


def _slow_child(*args):
    time.sleep(10)  # Test-only blocked computation, killed by real parent supervision.


def test_finite_worker_deadline_terminates_blocked_child(dataset_database_settings, tmp_path, monkeypatch):
    from backend.app.core.dataset_import import ImportSettings
    _, _, effective = staged(tmp_path, zip_bytes(entries()))
    settings = ImportSettings(root=effective.root, asset_root=effective.asset_root,
                              policy=ImportPolicy(max_seconds=1))
    repository = DatasetImportRepository(settings=dataset_database_settings)
    started = submit_archive([zip_bytes(entries())], name="Timeout", repository=repository, settings=settings)
    claimed = repository.claim_next(worker_id="timeout")
    monkeypatch.setattr(module, "_child", _slow_child)
    result = module.run_claimed_import(repository, claimed, settings=settings)
    repository.recover_stale()
    job = repository.read_job(started.job.job_id) if result is None else result
    assert job.status.value == "failed" and job.error.code == "IMPORT_TIMEOUT"
    assert list((settings.root / "runs").iterdir()) == []
