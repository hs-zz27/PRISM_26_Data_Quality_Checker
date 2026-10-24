"""Run an explicitly started, single-process extraction supervisor."""

import argparse
import signal
from threading import Event
from uuid import uuid4

from ..core.config import DatabaseConfigurationError
from ..services.extraction_jobs import ExtractionOutcomeUnresolved, supervise
from ..storage.extraction_repository import ExtractionRepository
from ..storage.workflow_repository import WorkflowStorageError
from .poll_retry import PollRetry

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process at most one queued extraction, then exit.")
    arguments = parser.parse_args(argv)
    stop = Event()
    def request_stop(signum, frame):
        stop.set()
    previous = {sig: signal.signal(sig, request_stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        repository = ExtractionRepository()
        worker_id = f"extraction_worker_{uuid4().hex}"
        poll_retry = PollRetry("Extraction worker")
        while not stop.is_set():
            try:
                repository.recover_stale()
                claim = repository.claim_next(worker_id=worker_id)
            except WorkflowStorageError as error:
                if not poll_retry.should_retry(error, stop.wait):
                    raise
                continue
            poll_retry.reset()
            if claim is not None:
                supervise(claim, repository=repository, stop=stop)
            if arguments.once:
                return 0
            if claim is None:
                stop.wait(1.0)
        return 0
    except (DatabaseConfigurationError, WorkflowStorageError, ExtractionOutcomeUnresolved):
        # Do not log exception chains that may contain SQL parameters or settings.
        print("Extraction worker stopped; storage/configuration/outcome requires operator review.")
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)

if __name__ == "__main__":
    raise SystemExit(main())
