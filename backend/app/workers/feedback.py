"""Explicit feedback comparison worker. Nothing runs on application import."""
import argparse
import time
from uuid import uuid4
from ..storage.feedback_jobs import FeedbackJobRepository
from ..services.feedback_jobs import run_claim
from ..core.feedback import FeedbackError
from .poll_retry import PollRetry

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    arguments = parser.parse_args()
    repository = FeedbackJobRepository()
    worker = 'feedback_' + uuid4().hex
    poll_retry = PollRetry('Feedback worker')
    while True:
        try:
            repository.recover()
            claim = repository.claim(worker)
        except FeedbackError as error:
            # Only storage unavailability is transient; other codes keep stopping the worker.
            if error.code != 'DEPENDENCY_UNAVAILABLE' or not poll_retry.should_retry(error, time.sleep):
                raise
            continue
        poll_retry.reset()
        if claim is not None:
            run_claim(repository, claim)
        if arguments.once:
            return
        if claim is None:
            time.sleep(1)
if __name__ == '__main__':
    main()
