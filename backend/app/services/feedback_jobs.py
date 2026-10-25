"""Sequential, bounded, spawn-isolated feedback inference with parent-owned SQL."""
import json
import multiprocessing
import time
from threading import Condition, Thread
from ..core.feedback import FeedbackError
from ..models.feedback import CaseComparison
from ..inference.annotation_guideline import GuidelineClient, unit_id
from ..storage.workflow_repository import JobClaimLostError
from .sample_worker import _clear_database_environment

def compare_child(channel, inputs, ordinal, asset_settings):
    _clear_database_environment()
    client = None
    try:
        case = inputs.truth.cases[ordinal]
        if inputs.runtime.enclosure is not None:
            from .enclosure_feedback import EnclosureFeedbackClient
            client = EnclosureFeedbackClient(inputs.runtime, inputs.policy)
        else:
            client = GuidelineClient(inputs.runtime)
        baseline = client.predict(case.manifest, case.target, [], asset_settings)
        candidate = client.predict(case.manifest, case.target, inputs.policy.cases, asset_settings)
        result = CaseComparison(case_id=unit_id(case.manifest, case.target), baseline=baseline, candidate=candidate)
        payload = json.dumps({'result': result.model_dump(mode='json')}, ensure_ascii=False, allow_nan=False).encode()
        if len(payload) > 524288:
            raise ValueError('Comparison response exceeds bound.')
        channel.send_bytes(payload)
    except BaseException:
        try:
            channel.send_bytes(b'{"fatal":true}')
        except (OSError, EOFError):
            pass
    finally:
        if client is not None:
            client.close()
        channel.close()

def stop_process(process):
    if process is None:
        return
    if process.is_alive():
        process.terminate()
    process.join(timeout=2)
    if process.is_alive():
        process.kill()
        process.join(timeout=2)

class OwnershipWatchdog:
    """Terminate active computation even if a SQL checkpoint blocks the parent."""

    def __init__(self, process):
        self.process = process
        self.condition = Condition()
        self.closed = False
        self.lease_end = time.monotonic() + 25
        self.expired = False
        self.thread = Thread(target=self.run, daemon=True)

    def run(self):
        with self.condition:
            while not self.closed:
                remaining = self.lease_end - time.monotonic()
                if remaining <= 0:
                    self.expired = True
                    break
                self.condition.wait(remaining)
            else:
                return
        stop_process(self.process)

    def renewed(self, started):
        with self.condition:
            if self.expired:
                raise JobClaimLostError('Feedback lease expired during checkpoint.')
            self.lease_end = started + 25
            self.condition.notify_all()

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        self.thread.join()

def run_claim(repository, claim):
    inputs = claim[3]
    for ordinal in range(len(inputs.truth.cases)):
        if not repository.checkpoint(claim):
            return
        context = multiprocessing.get_context('spawn')
        receive, send = context.Pipe(duplex=False)
        process = context.Process(target=compare_child, args=(send, inputs, ordinal, repository.asset_settings), daemon=True)
        watch = OwnershipWatchdog(process)
        started = False
        try:
            process.start()
            started = True
            send.close()
            watch.thread.start()
            last = time.monotonic()
            case_seconds = (inputs.runtime.enclosure.sample_budget_seconds if inputs.runtime.enclosure is not None
                            else inputs.runtime.timeout_seconds)
            deadline = last + (2 * case_seconds + 60 if inputs.runtime.enclosure is not None
                               else min(1200, 2 * case_seconds + 60))
            while not receive.poll(0.2):
                if watch.expired:
                    raise JobClaimLostError('Feedback lease expired.')
                now = time.monotonic()
                if now >= deadline:
                    raise TimeoutError('Feedback case deadline expired.')
                if now - last >= 5:
                    checkpoint_started = time.monotonic()
                    if not repository.checkpoint(claim):
                        return
                    watch.renewed(checkpoint_started)
                    last = time.monotonic()
                if not process.is_alive():
                    raise RuntimeError('Feedback child exited without a result.')
            payload = json.loads(receive.recv_bytes(maxlength=524288))
            if watch.expired:
                raise JobClaimLostError('Feedback lease expired.')
            if payload.get('fatal'):
                raise RuntimeError('Feedback child failed.')
            result = CaseComparison.model_validate_json(json.dumps(payload['result'], allow_nan=False))
            if not repository.checkpoint(claim):
                return
            if not repository.save_case(claim, ordinal, result):
                return
        except (FeedbackError, JobClaimLostError):
            raise
        except Exception:
            repository.fail(claim)
            raise
        finally:
            if watch.thread.ident is not None:
                watch.close()
            if started:
                stop_process(process)
            receive.close()
            send.close()
            if started:
                process.close()
    repository.complete(claim)
