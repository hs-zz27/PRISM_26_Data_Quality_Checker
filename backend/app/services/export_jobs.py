"""Finite export supervisor: SQL in parent, bounded file-only work in one child.

No ZIP or live connection crosses IPC. Small bounded status files avoid blocked
Pipe reads and join-before-drain queues. Each SQL ownership acknowledgement is
conservatively shortened by the full observed call duration, including commit.
"""
import json
import logging
import multiprocessing
import os
import sys
import threading
import time

from ..core.export import (
    ERROR_MESSAGES, ExportDependencyError, ExportLimitError, ExportNameError,
    ExportSettings, ExportStopped,
)
from ..models.export import FrozenExport, PublishedArtifacts
from ..models.job import JobStatus
from ..storage.workflow_repository import JobClaimLostError
from .export_archive import build_archive, manifest_bytes
from .export_files import (
    create_attempt, directory_fd, identify, open_relative, write_private,
)

STATUS_BYTES = 4096
LOGGER = logging.getLogger(__name__)


def _status(directory, filename, document):
    payload = json.dumps(document, allow_nan=False, separators=(',', ':')).encode('ascii')
    if len(payload) > STATUS_BYTES:
        raise ExportLimitError()
    temporary = filename + '.part'
    # This directory belongs exclusively to this claim; only this child writes.
    with directory_fd(directory, private=True) as parent:
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
    write_private(directory, temporary, payload)
    with directory_fd(directory, private=True) as parent:
        os.replace(temporary, filename, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)


def _read_status(root, token, filename):
    # Absence is expected while the child works; other storage failures are not.
    try:
        with open_relative(root, token + '/' + filename) as handle:
            content = handle.read(STATUS_BYTES + 1)
    except ExportDependencyError as error:
        cause = error.__cause__
        if isinstance(cause, FileNotFoundError):
            return None
        raise
    if len(content) > STATUS_BYTES:
        raise ExportDependencyError('Worker status exceeded its bound.')
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('Duplicate worker status key.')
            result[key] = value
        return result
    try:
        result = json.loads(content, object_pairs_hook=pairs,
                            parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Invalid constant')))
        if type(result) is not dict:
            raise ValueError('Status must be an object.')
        return result
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ExportDependencyError('Worker status is invalid.') from error


def _child(settings_json, token, stopped, deadline_end, parent_pid):
    # Isolation from database capabilities is not an adversarial-code sandbox.
    for key in tuple(os.environ):
        if key in {'DATABASE_URL', 'PRISM_TEST_DATABASE_URL'} or key.startswith(('PG', 'POSTGRES_')):
            os.environ.pop(key, None)
    def watchdog():
        while True:
            if os.getppid() != parent_pid or stopped.is_set() or time.monotonic() >= deadline_end:
                os._exit(124)
            time.sleep(0.05)
    threading.Thread(target=watchdog, daemon=True, name='export-child-bound').start()
    settings = ExportSettings.model_validate_json(settings_json)
    directory = settings.root / token
    try:
        with open_relative(settings.root, token + '/input.json') as handle:
            content = handle.read(settings.policy.max_metadata_bytes + 1)
        if len(content) > settings.policy.max_metadata_bytes:
            raise ExportLimitError()
        frozen = FrozenExport.model_validate_json(content)
        if frozen.policy != settings.policy:
            raise ExportDependencyError('Worker resource policy changed.')
        build_archive(frozen, settings=settings, directory=directory, stop_event=stopped,
                      deadline_end=deadline_end,
                      progress=lambda n: _status(directory, 'progress.json', {'processed': n}))
        _status(directory, 'result.json', {'kind': 'done'})
    except BaseException as error:
        code = ('EXPORT_RESOURCE_LIMIT' if isinstance(error, ExportLimitError)
                else 'EXPORT_INVALID_NAMES' if isinstance(error, ExportNameError)
                else 'EXPORT_STORAGE_FAILED' if isinstance(error, (ExportDependencyError, OSError))
                else 'EXPORT_INTERRUPTED' if isinstance(error, ExportStopped) else 'EXPORT_FAILED')
        _status(directory, 'result.json', {'kind': 'error', 'code': code})


def _reap(process, stopped):
    stopped.set()
    if process.pid is None:
        process.close()
        return
    process.join(timeout=0.5)
    if process.is_alive():
        process.terminate()
        process.join(timeout=1.0)
    if process.is_alive():
        process.kill()
        process.join(timeout=1.0)
    if process.is_alive():
        raise ExportDependencyError('The export child could not be reaped.')
    process.close()


def _cleanup(process, stopped, watch_done, watch_thread):
    # Always revoke child permission before any potentially failing join. Reap
    # even if watcher cleanup fails, and do not join a thread that never started.
    stopped.set()
    watch_done.set()
    try:
        if watch_thread is not None and watch_thread.ident is not None:
            watch_thread.join(timeout=1.0)
    finally:
        if process is not None:
            _reap(process, stopped)


def run_claimed_export(repository, claim, *, settings, stop_event):
    settings = ExportSettings.model_validate_json(settings.model_dump_json())
    # Accepted policy is authoritative across restarts/config changes. Storage
    # roots are deployment configuration and must remain stable operationally.
    settings = ExportSettings(**{**settings.model_dump(), 'policy': claim.frozen.policy})
    context = multiprocessing.get_context('spawn')
    stopped = context.Event()
    process = None
    deadline_end = lease_end = time.monotonic()
    directory = None
    terminal = None
    last_processed = 0
    last_ping = time.monotonic()
    watch_done = threading.Event()
    locally_expired = threading.Event()
    watch_thread = None

    def heartbeat(processed=None):
        nonlocal lease_end, deadline_end, last_ping
        started = time.monotonic()
        ack = repository.heartbeat(claim, processed=processed)
        ended = time.monotonic()
        last_ping = ended
        # Subtract the entire SQL roundtrip: pessimistic but never extends time.
        elapsed = ended - started
        lease_end = ended + max(0.0, ack.lease_remaining - elapsed)
        candidate = ended + max(0.0, ack.deadline_remaining - elapsed)
        deadline_end = candidate if deadline_end <= started else min(deadline_end, candidate)
        return ack.job

    def check():
        nonlocal terminal
        if locally_expired.is_set() or stop_event.is_set() or time.monotonic() >= min(lease_end, deadline_end):
            raise ExportStopped()
        if time.monotonic() - last_ping >= 0.5:
            job = heartbeat()
            if job.status is not JobStatus.RUNNING:
                terminal = job
                raise ExportStopped()
            if locally_expired.is_set():
                raise ExportStopped()

    def watch_permission():
        # Runs independently while the parent may be blocked on SQL/filesystem.
        # Child's own watchdog exits on this event, including mid-compression.
        while not watch_done.wait(0.02):
            if stop_event.is_set() or time.monotonic() >= min(lease_end, deadline_end):
                locally_expired.set()
                stopped.set()
                return

    try:
        job = heartbeat()
        if job.status is not JobStatus.RUNNING:
            return job
        check()
        directory = create_attempt(settings.root, claim.claim_token)
        content = claim.frozen.model_dump_json().encode('utf-8')
        if len(content) > claim.frozen.policy.max_metadata_bytes:
            return repository.finish(claim, code='EXPORT_RESOURCE_LIMIT')
        write_private(directory, 'input.json', content)
        check()
        process = context.Process(target=_child, args=(settings.model_dump_json(), claim.claim_token,
                                                      stopped, deadline_end, os.getpid()), daemon=False)
        watch_thread = threading.Thread(target=watch_permission, daemon=True,
                                        name='export-parent-permission-bound')
        watch_thread.start()
        process.start()
        next_heartbeat = time.monotonic()
        while True:
            check()
            result = _read_status(settings.root, claim.claim_token, 'result.json')
            progress = _read_status(settings.root, claim.claim_token, 'progress.json')
            processed = last_processed
            if progress is not None:
                if (set(progress) != {'processed'} or type(progress['processed']) is not int
                        or not last_processed <= progress['processed'] <= claim.job.progress.total):
                    raise ExportDependencyError('Worker progress is inconsistent.')
                processed = progress['processed']
            if time.monotonic() >= next_heartbeat or processed != last_processed:
                job = heartbeat(processed)
                last_processed = processed
                if job.status is not JobStatus.RUNNING:
                    terminal = job
                    break
                next_heartbeat = time.monotonic() + 0.5
            if result is not None:
                if result == {'kind': 'done'}:
                    # Result is emitted only after file/directory flush. Child is
                    # stopped/reaped before parent verification and publication.
                    _reap(process, stopped)
                    process = None
                    archive = identify(settings.root, claim.claim_token + '/archive.zip',
                                       claim.frozen.policy.max_archive_bytes, check=check)
                    manifest = identify(settings.root, claim.claim_token + '/manifest.json',
                                        claim.frozen.policy.max_metadata_bytes, check=check)
                    with open_relative(settings.root, manifest.key) as handle:
                        document = handle.read(claim.frozen.policy.max_metadata_bytes + 1)
                    if document != manifest_bytes(claim.frozen):
                        raise ExportDependencyError('Published manifest differs from frozen input.')
                    # Renew ownership after potentially long verification, before
                    # entering the publication transaction. That transaction also
                    # rechecks server time after all job/resource/input locks.
                    job = heartbeat(claim.job.progress.total)
                    if job.status is not JobStatus.RUNNING:
                        terminal = job
                    else:
                        check()
                        terminal = repository.finish(claim, artifacts=PublishedArtifacts(
                            archive=archive, manifest=manifest))
                    break
                if (set(result) != {'kind', 'code'} or result['kind'] != 'error'
                        or result['code'] not in ERROR_MESSAGES):
                    raise ExportDependencyError('Worker result is invalid.')
                terminal = repository.finish(claim, code=result['code'])
                break
            if not process.is_alive():
                # Drain the final atomic result once more after exit, avoiding a
                # poll-before-exit race. A missing/malformed result is not success.
                result = _read_status(settings.root, claim.claim_token, 'result.json')
                if result is None:
                    terminal = repository.finish(claim, code='EXPORT_FAILED')
                    break
                continue
            stop_event.wait(0.05)
    except ExportStopped:
        # The child is always stopped below. No writes after lease loss/deadline;
        # stale recovery owns terminalization if authorization has expired.
        stopped.set()
        if terminal is None and not locally_expired.is_set() and time.monotonic() < min(lease_end, deadline_end):
            terminal = repository.finish(claim, code='EXPORT_INTERRUPTED')
    except (ExportLimitError, ExportNameError) as error:
        stopped.set()
        terminal = repository.finish(claim, code='EXPORT_RESOURCE_LIMIT'
                                     if isinstance(error, ExportLimitError) else 'EXPORT_INVALID_NAMES')
    except ExportDependencyError:
        # In particular, unknown commits propagate and retain possible outputs.
        # Do not blindly issue another SQL mutation after an uncertain outcome.
        stopped.set()
        raise
    except JobClaimLostError:
        stopped.set()
        raise
    except Exception:
        stopped.set()
        terminal = repository.finish(claim, code='EXPORT_FAILED')
    finally:
        primary_error = sys.exc_info()[1]
        try:
            _cleanup(process, stopped, watch_done, watch_thread)
        except Exception as error:
            if primary_error is None:
                raise ExportDependencyError('Export process cleanup requires operator review.') from error
            # Private worker diagnostics only. A secondary join/reap failure must
            # not hide an unknown SQL commit or ownership-loss exception and must
            # never cause speculative finalization/retry or deletion of outputs.
            try:
                LOGGER.error('Export cleanup failed while preserving the primary failure.',
                             exc_info=(type(error), error, error.__traceback__))
            except Exception:
                # Best-effort private diagnostics cannot replace the primary
                # unknown-commit/claim-loss exception if a log handler fails.
                pass
    # No automatic filesystem cleanup. Attempt-owned partial/unregistered files
    # may remain for operator reconciliation, including on known failures.
    return terminal
