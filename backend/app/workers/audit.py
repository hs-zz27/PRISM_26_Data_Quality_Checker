"""Explicit sequential audit worker; HTTP submission only queues work."""

import argparse
import signal
import sys
from threading import Event
from uuid import uuid4

from ..core.config import AssetStorageConfigurationError, AuditConfigurationError, DatabaseConfigurationError
from ..models.job import JobKind
from ..services.batch_audit import BatchAuditService
from ..storage.repository import RulebookRepository, RulebookStorageError
from .poll_retry import PollRetry
from ..storage.workflow_repository import (
    AuditRepository, JobClaimLostError, SampleAssetManifestRepository, WorkflowStorageError,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Recover stale audits, then process at most one audit.")
    arguments = parser.parse_args(argv)
    stop = Event()

    def request_stop(signum, frame):
        # Let the existing supervisor finish the current audit safely. Use the
        # job cancellation endpoint for prompt cancellation of active work.
        stop.set()

    previous = {sig: signal.signal(sig, request_stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        repository = AuditRepository()
        service = BatchAuditService(
            audit_repository=repository,
            rulebook_repository=RulebookRepository(settings=repository.settings),
            manifest_repository=SampleAssetManifestRepository(settings=repository.settings),
        )
        worker_id = f"audit_worker_{uuid4().hex}"
        poll_retry = PollRetry("Audit worker")
        while not stop.is_set():
            try:
                service.recover()
                claim = repository.jobs.claim_next_job(
                    worker_id=worker_id, lease_seconds=service.lease_seconds, kind=JobKind.AUDIT,
                )
            except (WorkflowStorageError, RulebookStorageError) as error:
                if not poll_retry.should_retry(error, stop.wait):
                    raise
                continue
            poll_retry.reset()
            if claim is not None:
                try:
                    service.run_claimed_job(claim)
                except JobClaimLostError:
                    # Existing ownership checks protect writes; another worker
                    # or the next recovery pass owns the durable outcome.
                    pass
            if arguments.once:
                return 0
            if claim is None:
                stop.wait(1.0)
        return 0
    except (DatabaseConfigurationError, AssetStorageConfigurationError, AuditConfigurationError,
            WorkflowStorageError, RulebookStorageError):
        print("Audit worker stopped; check storage and configuration before restarting.", file=sys.stderr)
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
