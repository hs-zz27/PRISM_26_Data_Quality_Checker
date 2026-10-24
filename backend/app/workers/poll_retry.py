"""Bounded waiting for brief storage outages while a worker looks for work.

Only a known transient failure during the idle poll (recover, then claim) is
retried. Claim acquisition can itself commit, so inspect wrapped causes before
retrying; not having received a claim does not establish that nothing committed.
Errors whose commit outcome is unknown are never retried: callers must stop
and reconcile, exactly as before. After the last delay the original error is
re-raised so persistent misconfiguration still stops the worker.
"""

import sys
from collections.abc import Callable

from psycopg import OperationalError

from ..storage.database import CommitOutcomeUnknownError, is_retryable_database_error


def _can_retry(error: BaseException) -> bool:
    """Reject uncertain commits anywhere in the chain before classifying outages."""
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None:
        if id(current) in seen:
            return False
        seen.add(id(current))
        if isinstance(current, CommitOutcomeUnknownError) or getattr(current, "commit_outcome_unknown", False):
            return False
        chain.append(current)
        current = current.__cause__ or current.__context__
    # libpq connection refusals may have no server SQLSTATE. This exception is
    # limited to idle polling; claimed computations and persistence keep their
    # existing retry/reconciliation policies. Auth/config/schema errors stop.
    return any(
        is_retryable_database_error(item)
        or (isinstance(item, OperationalError) and item.sqlstate is None)
        for item in chain
    )


RETRY_DELAYS_SECONDS = (2, 4, 8, 16, 30)


class PollRetry:
    def __init__(self, worker_name: str, delays: tuple[int, ...] | None = None) -> None:
        self.worker_name = worker_name
        self.delays = RETRY_DELAYS_SECONDS if delays is None else delays
        self.failures = 0

    def reset(self) -> None:
        self.failures = 0

    def should_retry(self, error: BaseException, sleep: Callable[[float], object]) -> bool:
        """Wait before the next poll and return True, or return False to re-raise."""
        if self.failures >= len(self.delays) or not _can_retry(error):
            return False
        delay = self.delays[self.failures]
        self.failures += 1
        # Never print the exception chain: it may contain SQL parameters or settings.
        print(
            f"{self.worker_name}: storage unavailable while looking for work; "
            f"retrying in {delay}s ({self.failures}/{len(self.delays)}).",
            file=sys.stderr, flush=True,
        )
        sleep(delay)
        return True
