"""Bounded waiting for brief storage outages while a worker looks for work.

Only the idle poll (recover stale work, then claim) is retried. No claimed job
is in flight there, so retrying it is equivalent to restarting the worker.
Errors whose commit outcome is unknown are never retried: callers must stop
and reconcile, exactly as before. After the last delay the original error is
re-raised so persistent misconfiguration still stops the worker.
"""

import sys
from collections.abc import Callable

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
        if getattr(error, "commit_outcome_unknown", False) or self.failures >= len(self.delays):
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
