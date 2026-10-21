"""Explicit sequential single-attempt export worker; app startup never runs it."""
import argparse
import signal
import sys
from threading import Event
from uuid import uuid4

from ..core.config import DatabaseConfigurationError
from ..core.export import ExportDependencyError, ExportOutcomeUnknown, load_export_settings
from ..services.export_jobs import run_claimed_export
from ..storage.export_repository import ExportRepository
from ..storage.workflow_repository import JobClaimLostError, WorkflowStorageError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='Recover stale exports, then attempt at most one queued export.')
    arguments = parser.parse_args(argv)
    stopped = Event()
    def request_stop(signum, frame):
        stopped.set()
    previous = {sig: signal.signal(sig, request_stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        settings = load_export_settings()
        repository = ExportRepository()
        worker_id = 'export_worker_' + uuid4().hex
        while not stopped.is_set():
            repository.recover_stale()
            claim = repository.claim_next(worker_id=worker_id)
            if claim is not None:
                try:
                    run_claimed_export(repository, claim, settings=settings, stop_event=stopped)
                except JobClaimLostError:
                    # Another recovery boundary owns the outcome. Never replay.
                    pass
                # Expired local supervision never writes through a stale claim.
                # Fresh locked recovery terminalizes expired work even with --once.
                repository.recover_stale()
            if arguments.once:
                return 0
            if claim is None:
                stopped.wait(settings.policy.poll_seconds)
        return 0
    except ExportOutcomeUnknown:
        print('Export commit outcome is unresolved. Reconcile list/detail/job state before restarting or resubmitting.', file=sys.stderr)
        return 2
    except (ExportDependencyError, DatabaseConfigurationError, WorkflowStorageError, OSError):
        print('Export worker stopped; private storage or configuration requires operator review.', file=sys.stderr)
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    raise SystemExit(main())
