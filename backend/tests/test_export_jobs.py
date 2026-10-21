"""Authored spawn/supervision regressions; not executed by this handoff."""
from datetime import timedelta
from threading import Event

import pytest

from backend.app.core.export import ExportOutcomeUnknown
from backend.app.models.export import ExportClaim
from backend.app.models.job import Job, JobKind, Progress, ResultReference
from backend.app.services.export_jobs import run_claimed_export, _read_status
from backend.app.storage.export_repository import ExportLease
from backend.app.storage.workflow_repository import JobClaimLostError
from backend.tests.export_support import synthetic, NOW


class Repository:
    def __init__(self, claim, *, cancel_at=None, lose_at=None, unknown_finish=False):
        self.claim, self.calls, self.finishes = claim, 0, []
        self.cancel_at, self.lose_at, self.unknown_finish = cancel_at, lose_at, unknown_finish
        self.processed = 0

    def heartbeat(self, claim, *, processed=None):
        self.calls += 1
        if self.calls == self.lose_at:
            raise JobClaimLostError('Synthetic ownership replacement.')
        if processed is not None:
            self.processed = processed
        status = 'cancelled' if self.calls == self.cancel_at else 'running'
        job = Job.model_validate({**claim.job.model_dump(), 'status': status,
                                  'progress': Progress(phase=status, processed=self.processed,
                                                       total=claim.job.progress.total,
                                                       fraction=1.0 if claim.job.progress.total == 0 else
                                                       self.processed / claim.job.progress.total)})
        return ExportLease(job, 30.0 if status == 'running' else 0.0,
                           60.0 if status == 'running' else 0.0)

    def finish(self, claim, *, artifacts=None, code=None):
        self.finishes.append((artifacts, code))
        if self.unknown_finish:
            raise ExportOutcomeUnknown('Synthetic lost publication acknowledgement.')
        total = claim.job.progress.total
        processed = total if artifacts else self.processed
        return Job.model_validate({**claim.job.model_dump(), 'status': 'completed' if artifacts else 'failed',
            'progress': Progress(phase='completed' if artifacts else 'failed', processed=processed,
                                 total=total, fraction=1.0 if total == 0 else processed / total),
            'result': ResultReference(kind='export', id=claim.frozen.manifest.export_id,
                                      href=f'/api/v1/exports/{claim.frozen.manifest.export_id}') if artifacts else None,
            'error': None if artifacts else {'code': code, 'message': 'Synthetic export failure.', 'request_id': 'test', 'details': []}})


def claimed(tmp_path):
    frozen, settings, _ = synthetic(tmp_path)
    job = Job.queued(job_id='job_export', kind=JobKind.EXPORT, total=1, now=NOW)
    job = Job.model_validate({**job.model_dump(), 'status': 'running'})
    claim = ExportClaim(job=job, frozen=frozen, worker_id='worker', claim_token='b' * 32,
                        deadline=NOW + timedelta(seconds=900))
    return claim, settings


def test_real_spawn_has_small_file_result_and_parent_only_publication(tmp_path):
    claim, settings = claimed(tmp_path)
    repository = Repository(claim)
    job = run_claimed_export(repository, claim, settings=settings, stop_event=Event())
    assert job.status.value == 'completed'
    assert len(repository.finishes) == 1 and repository.finishes[0][1] is None
    assert (settings.root / claim.claim_token / 'result.json').stat().st_size < 4096
    assert repository.finishes[0][0].archive.key == claim.claim_token + '/archive.zip'


def test_unknown_publication_keeps_possible_committed_files_and_does_not_retry(tmp_path):
    claim, settings = claimed(tmp_path)
    repository = Repository(claim, unknown_finish=True)
    with pytest.raises(ExportOutcomeUnknown):
        run_claimed_export(repository, claim, settings=settings, stop_event=Event())
    assert len(repository.finishes) == 1
    assert (settings.root / claim.claim_token / 'archive.zip').is_file()
    assert (settings.root / claim.claim_token / 'manifest.json').is_file()


def test_owned_cancellation_stops_child_without_publication(tmp_path):
    claim, settings = claimed(tmp_path)
    repository = Repository(claim, cancel_at=2)
    job = run_claimed_export(repository, claim, settings=settings, stop_event=Event())
    assert job.status.value == 'cancelled'
    assert not repository.finishes


def test_ownership_loss_never_finalizes_foreign_claim(tmp_path):
    claim, settings = claimed(tmp_path)
    repository = Repository(claim, lose_at=2)
    with pytest.raises(JobClaimLostError):
        run_claimed_export(repository, claim, settings=settings, stop_event=Event())
    assert not repository.finishes


def test_status_duplicate_keys_are_rejected(tmp_path):
    from backend.app.core.export import ExportDependencyError
    from backend.app.services.export_files import create_attempt
    _, settings = claimed(tmp_path)
    directory = create_attempt(settings.root, 'c' * 32)
    (directory / 'result.json').write_bytes(b'{"kind":"done","kind":"error"}')
    with pytest.raises(ExportDependencyError):
        _read_status(settings.root, 'c' * 32, 'result.json')


def test_cleanup_failure_preserves_unknown_heartbeat_outcome(tmp_path, monkeypatch):
    from backend.app.core.export import ExportDependencyError
    from backend.app.services import export_jobs as module
    claim, settings = claimed(tmp_path)
    repository = Repository(claim)
    def heartbeat(*args, **kwargs):
        raise ExportOutcomeUnknown('Synthetic unknown heartbeat commit.')
    def cleanup(*args):
        raise ExportDependencyError('Synthetic secondary cleanup failure.')
    monkeypatch.setattr(repository, 'heartbeat', heartbeat)
    monkeypatch.setattr(module, '_cleanup', cleanup)
    with pytest.raises(ExportOutcomeUnknown, match='unknown heartbeat'):
        module.run_claimed_export(repository, claim, settings=settings, stop_event=Event())
    assert not repository.finishes


def test_cleanup_failure_without_primary_error_stops_as_dependency(tmp_path, monkeypatch):
    from backend.app.core.export import ExportDependencyError
    from backend.app.services import export_jobs as module
    claim, settings = claimed(tmp_path)
    repository = Repository(claim, cancel_at=1)
    def cleanup(*args):
        raise RuntimeError('Synthetic join failure.')
    monkeypatch.setattr(module, '_cleanup', cleanup)
    with pytest.raises(ExportDependencyError, match='cleanup requires operator review'):
        module.run_claimed_export(repository, claim, settings=settings, stop_event=Event())
    assert not repository.finishes


def test_cleanup_revokes_child_and_reaps_even_if_watcher_join_fails(monkeypatch):
    from backend.app.services import export_jobs as module
    stopped, done = Event(), Event()
    calls = []
    class Watcher:
        ident = 1
        def join(self, **kwargs):
            assert stopped.is_set() and done.is_set()
            raise RuntimeError('Synthetic watcher join failure.')
    monkeypatch.setattr(module, '_reap', lambda process, event: calls.append((process, event.is_set())))
    with pytest.raises(RuntimeError):
        module._cleanup('synthetic_process', stopped, done, Watcher())
    assert calls == [('synthetic_process', True)]


def test_cleanup_does_not_join_an_unstarted_watcher(monkeypatch):
    from threading import Thread
    from backend.app.services import export_jobs as module
    module._cleanup(None, Event(), Event(), Thread(target=lambda: None))
