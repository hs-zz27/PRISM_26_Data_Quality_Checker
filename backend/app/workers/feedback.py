"""Explicit feedback comparison worker. Nothing runs on application import."""
import argparse
import time
from uuid import uuid4
from ..storage.feedback_jobs import FeedbackJobRepository
from ..services.feedback_jobs import run_claim

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    arguments = parser.parse_args()
    repository = FeedbackJobRepository()
    worker = 'feedback_' + uuid4().hex
    while True:
        repository.recover()
        claim = repository.claim(worker)
        if claim is not None:
            run_claim(repository, claim)
        if arguments.once:
            return
        if claim is None:
            time.sleep(1)
if __name__ == '__main__':
    main()
