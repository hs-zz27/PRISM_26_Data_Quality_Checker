"""Bounded, parent-owned extraction supervision; the child never persists.

Linux/POSIX pipe reads are nonblocking, including partial Connection frames.
No recv()/join-before-drain path can hide cancellation behind a partial payload.
"""

from collections.abc import Callable
from datetime import UTC, datetime
import json
import multiprocessing
from multiprocessing.connection import Connection
import os
import struct
import tomllib
from threading import Event
from time import monotonic
from typing import Any

from pydantic import ValidationError

from ..core.config import (
    OllamaSettings, load_ollama_settings,
)
from ..inference.ollama_client import OllamaClientError
from ..models.job import Job, JobStatus
from ..models.rule import Rulebook
from ..models.rule_extraction import ExtractionClaim, ExtractionPolicy, RuleExtractionRequest
from ..storage.extraction_repository import (
    ERROR_MESSAGES, ExtractionPublicationExpired, ExtractionRepository, ExtractionSubmissionError,
)
from ..storage.workflow_repository import (
    JobClaimLostError, RetryableWorkflowStorageError, WorkflowStorageError,
)
from .rule_extraction import extract_rulebook

# Transport/memory guard, not a guideline length limit or model token estimate.
MAX_RESULT_BYTES = 64 * 1024 * 1024
REASON_CODES = {
    "model_unavailable": "MODEL_UNAVAILABLE",
    "server_unavailable": "OLLAMA_UNAVAILABLE",
    "request_timeout": "MODEL_REQUEST_TIMEOUT",
    "invalid_rule_proposals": "INVALID_RULE_PROPOSALS",
    "incomplete_response": "INVALID_RULE_PROPOSALS",
    "invalid_response": "MODEL_PROTOCOL_ERROR",
    "request_failed": "MODEL_PROTOCOL_ERROR",
    "invalid_model_metadata": "MODEL_PROTOCOL_ERROR",
    "remote_model": "MODEL_CONFIGURATION_ERROR",
}

class ExtractionOutcomeUnresolved(RuntimeError):
    """Stop without retrying work or pretending an uncertain write rolled back."""

def submit_extraction(
    request: RuleExtractionRequest, *, repository: ExtractionRepository,
    settings: OllamaSettings | None = None, policy: ExtractionPolicy | None = None,
) -> Job:
    """The submission path loads server configuration but never contacts Ollama."""
    if not isinstance(request, RuleExtractionRequest):
        raise TypeError("Expected RuleExtractionRequest.")
    snapshot = RuleExtractionRequest.model_validate(request.model_dump())
    # Keep configuration failures narrow: internal repository validation defects
    # remain server defects rather than globally becoming a client/503 category.
    try:
        effective = load_ollama_settings() if settings is None else settings
        effective = OllamaSettings.model_validate(effective.model_dump())
        bounds = ExtractionPolicy() if policy is None else ExtractionPolicy.model_validate(policy.model_dump())
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, ValidationError) as error:
        raise ExtractionSubmissionError("Extraction could not be durably accepted.") from error
    try:
        return repository.enqueue(snapshot, settings=effective, policy=bounds)
    except WorkflowStorageError as error:
        raise ExtractionSubmissionError("Extraction could not be durably accepted.") from error

def extraction_child(send: Connection, text: str, settings: dict[str, Any],
                     rulebook_id: str) -> None:
    """Importable spawn target. Arguments contain no DB/client/parent resources."""
    try:
        configured = OllamaSettings.model_validate(settings)
    except ValidationError:
        configured = None
    try:
        if configured is None:
            envelope = {"error": "MODEL_CONFIGURATION_ERROR"}
        else:
            envelope = _extract_payload(text, configured, rulebook_id)
    except OllamaClientError as error:
        envelope = {"error": REASON_CODES.get(error.reason, "EXTRACTION_FAILED")}
    except (ValidationError, ValueError):
        envelope = {"error": "INVALID_RULE_PROPOSALS"}
    except Exception:
        envelope = {"error": "EXTRACTION_FAILED"}
    try:
        encoded = json.dumps(envelope, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_RESULT_BYTES:
            encoded = b'{"error":"EXTRACTION_FAILED"}'
        send.send_bytes(encoded)
    except (BrokenPipeError, EOFError, OSError):
        pass  # Supervisor has stopped accepting this result.
    finally:
        send.close()

def _extract_payload(text: str, configured: OllamaSettings, rulebook_id: str) -> dict[str, Any]:
    draft = extract_rulebook(text, settings=configured)
    # Preserve the synchronous API and replace only its server-generated ID.
    draft = Rulebook.model_validate({**draft.model_dump(), "rulebook_id": rulebook_id})
    return {"draft": draft.model_dump(mode="json")}

class _FrameReader:
    """Parse exactly one multiprocessing send_bytes frame without blocking recv.

    Connection framing uses a signed network-order 32-bit length, or -1 and
    an unsigned 64-bit length. Payloads beyond the explicit memory budget fail.
    Reading bounded chunks returns control even when a child writes endlessly.
    """

    def __init__(self, pipe: Connection):
        self.pipe = pipe
        self.buffer = bytearray()
        self.eof = False
        self.payload: dict[str, Any] | None = None
        os.set_blocking(pipe.fileno(), False)

    def poll(self) -> dict[str, Any] | None:
        for _ in range(16):
            try:
                chunk = os.read(self.pipe.fileno(), 65536)
            except BlockingIOError:
                break
            if not chunk:
                self.eof = True
                break
            self.buffer.extend(chunk)
            if len(self.buffer) > MAX_RESULT_BYTES + 12:
                raise ValueError("Extraction result exceeds the transport budget.")
        if len(self.buffer) < 4:
            if self.eof:
                raise ValueError("Child exited without a complete result frame.")
            return None
        size = struct.unpack("!i", self.buffer[:4])[0]
        header = 4
        if size == -1:
            if len(self.buffer) < 12:
                if self.eof:
                    raise ValueError("Truncated extended frame header.")
                return None
            size = struct.unpack("!Q", self.buffer[4:12])[0]
            header = 12
        if size < 0 or size > MAX_RESULT_BYTES:
            raise ValueError("Invalid extraction result frame size.")
        if len(self.buffer) < size + header:
            if self.eof:
                raise ValueError("Child exited with a partial result frame.")
            return None
        if len(self.buffer) != size + header:
            raise ValueError("Expected exactly one child result frame.")
        if self.payload is not None:
            return self.payload

        def invalid_constant(value):
            raise ValueError("Non-finite JSON is forbidden.")
        def unique_object(items):
            document = {}
            for key, value in items:
                if key in document:
                    raise ValueError("Duplicate JSON object key.")
                document[key] = value
            return document
        payload = json.loads(bytes(self.buffer[header:]).decode("utf-8"),
                             parse_constant=invalid_constant, object_pairs_hook=unique_object)
        if not isinstance(payload, dict):
            raise ValueError("Child result must be an object.")
        self.payload = payload
        return payload

def _reconcile(repository: ExtractionRepository, claim: ExtractionClaim) -> Job:
    try:
        job = repository.readback(claim)
    except WorkflowStorageError as error:
        raise ExtractionOutcomeUnresolved("Extraction commit could not be reconciled.") from error
    if job.status not in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
        raise ExtractionOutcomeUnresolved("Extraction commit remains unresolved.")
    return job

def _finish(repository, claim, *, payload=None, code=None) -> Job:
    try:
        if code is not None:
            return repository.fail(claim, code)
        return repository.publish(claim, payload)
    except ExtractionPublicationExpired:
        # The insert rolled back. A separate failure requires current DB ownership.
        return _finish(repository, claim, code="EXTRACTION_TIMEOUT")
    except RetryableWorkflowStorageError as error:
        if error.commit_outcome_unknown:
            return _reconcile(repository, claim)
        raise

def supervise(
    claim: ExtractionClaim, *, repository: ExtractionRepository,
    stop: Event | None = None, child_target: Callable = extraction_child,
) -> Job | None:
    """Process one existing claim. None means lease loss, not durable failure.

    `child_target` is a top-level importable seam for offline spawn tests, not
    a public request option. Cleanup never publishes or reruns inference.
    """
    claim = ExtractionClaim.model_validate(claim.model_dump())
    policy = claim.request.policy
    stop = Event() if stop is None else stop
    # A monotonic ceiling prevents wall-clock adjustments extending local work.
    remaining = max(0.0, (claim.request.deadline - datetime.now(UTC)).total_seconds())
    local_deadline = monotonic() + min(float(policy.max_seconds), remaining)
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    child = context.Process(target=child_target, args=(
        send, claim.request.guideline_text, claim.request.settings.model_dump(mode="json"),
        claim.request.rulebook_id,
    ))
    started = False
    answer = None
    try:
        if stop.is_set():
            return _finish(repository, claim, code="EXTRACTION_INTERRUPTED")
        state = repository.checkpoint(claim)
        if state.status is not JobStatus.RUNNING:
            return state
        try:
            child.start()
        except (OSError, RuntimeError):
            return _finish(repository, claim, code="EXTRACTION_FAILED")
        started = True
        send.close()
        reader = _FrameReader(receive)
        renew_at = monotonic() + policy.renew_seconds
        while True:
            if stop.is_set():
                _stop_child(child)
                return _finish(repository, claim, code="EXTRACTION_INTERRUPTED")
            if monotonic() >= local_deadline:
                _stop_child(child)
                return _finish(repository, claim, code="EXTRACTION_TIMEOUT")
            renew = monotonic() >= renew_at
            state = repository.checkpoint(claim, renew=renew)
            if state.status is not JobStatus.RUNNING:
                _stop_child(child)
                return state
            if renew:
                renew_at = monotonic() + policy.renew_seconds
            try:
                answer = reader.poll()
            except (ValueError, UnicodeError, OSError, RecursionError):
                _stop_child(child)
                return _finish(repository, claim, code="EXTRACTION_FAILED")
            if answer is not None:
                # Drain first; only then wait for a bounded clean child exit. A target
                # that sends a result and keeps running still obeys the deadline.
                if not child.is_alive():
                    try:
                        reader.poll()
                    except (ValueError, UnicodeError, OSError, RecursionError):
                        return _finish(repository, claim, code="EXTRACTION_FAILED")
                    child.join(timeout=0)
                    if child.exitcode != 0:
                        return _finish(repository, claim, code="EXTRACTION_FAILED")
                    break
                # Do not parse the same completed buffer again while waiting.
                while child.is_alive():
                    if stop.wait(policy.poll_seconds):
                        _stop_child(child)
                        return _finish(repository, claim, code="EXTRACTION_INTERRUPTED")
                    if monotonic() >= local_deadline:
                        _stop_child(child)
                        return _finish(repository, claim, code="EXTRACTION_TIMEOUT")
                    renew = monotonic() >= renew_at
                    state = repository.checkpoint(claim, renew=renew)
                    if state.status is not JobStatus.RUNNING:
                        _stop_child(child)
                        return state
                    if renew:
                        renew_at = monotonic() + policy.renew_seconds
                    try:
                        reader.poll()
                    except (ValueError, UnicodeError, OSError, RecursionError):
                        _stop_child(child)
                        return _finish(repository, claim, code="EXTRACTION_FAILED")
                try:
                    reader.poll()
                except (ValueError, UnicodeError, OSError, RecursionError):
                    return _finish(repository, claim, code="EXTRACTION_FAILED")
                child.join(timeout=0)
                if child.exitcode != 0:
                    return _finish(repository, claim, code="EXTRACTION_FAILED")
                break
            # Reader can hold >1MiB pending after child exit. Keep draining until
            # EOF/full frame, rather than interpreting exit as an empty success.
            stop.wait(policy.poll_seconds)
        if set(answer) == {"error"} and isinstance(answer["error"], str) and answer["error"] in ERROR_MESSAGES:
            return _finish(repository, claim, code=answer["error"])
        if set(answer) != {"draft"} or not isinstance(answer["draft"], dict):
            return _finish(repository, claim, code="EXTRACTION_FAILED")
        if stop.is_set():
            return _finish(repository, claim, code="EXTRACTION_INTERRUPTED")
        if monotonic() >= local_deadline:
            return _finish(repository, claim, code="EXTRACTION_TIMEOUT")
        return _finish(repository, claim, payload=answer["draft"])
    except JobClaimLostError:
        # Expired/foreign owners cannot record even a cancellation or timeout.
        return None
    except RetryableWorkflowStorageError as error:
        if error.commit_outcome_unknown:
            return _reconcile(repository, claim)
        raise
    finally:
        send.close()
        receive.close()
        if started or child.pid is not None:
            _stop_child(child)
        child.close()

def _stop_child(child) -> None:
    if child.is_alive():
        child.terminate()
    child.join(timeout=2.0)
    if child.is_alive():
        child.kill()
        child.join(timeout=2.0)
    if child.is_alive():
        raise ExtractionOutcomeUnresolved("The local child could not be reaped.")
