"""Explicit dataset worker. Importing the app never starts this process."""

import argparse
import signal
import sys
import threading
from uuid import uuid4

from ..core.dataset_import import ImportOutcomeUnresolved, load_import_settings
from ..services.dataset_import_jobs import run_claimed_import
from ..storage.dataset_import_repository import DatasetImportRepository
from ..storage.dataset_repository import DatasetCommitOutcomeUnknownError, DatasetStorageError
from .poll_retry import PollRetry


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run bounded single-attempt dataset imports.")
    parser.add_argument("--once", action="store_true", help="Recover stale work, then attempt at most one queued import.")
    args = parser.parse_args(argv)
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda _signal, _frame: stopped.set())
    try:
        settings = load_import_settings()
        repository = DatasetImportRepository()
        worker_id = "dataset_worker_" + uuid4().hex
        poll_retry = PollRetry("Dataset import worker")
        while not stopped.is_set():
            try:
                repository.recover_stale()
                claim = repository.claim_next(worker_id=worker_id)
            except DatasetStorageError as error:
                # DatasetCommitOutcomeUnknownError is flagged and never retried.
                if not poll_retry.should_retry(error, stopped.wait):
                    raise
                continue
            poll_retry.reset()
            if claim is not None:
                run_claimed_import(repository, claim, settings=settings, stop_event=stopped)
            if args.once:
                return 0
            stopped.wait(settings.policy.poll_seconds)
    except (ImportOutcomeUnresolved, DatasetCommitOutcomeUnknownError):
        print("Import commit outcome is unresolved. Reconcile persisted state before restarting; no replay was attempted.", file=sys.stderr)
        return 2
    except (DatasetStorageError, ValueError, OSError):
        print("Dataset import worker stopped because a required local dependency could not be used.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
