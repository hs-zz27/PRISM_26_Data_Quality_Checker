"""Single-attempt extraction persistence. No inference, migration or startup work.

Lock order: jobs row -> private request row -> existing rulebook advisory lock.
Every authorization time is sampled from PostgreSQL *after* blocking locks.
Publication and terminal job state share one transaction/connection.
"""

from datetime import timedelta
import json
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from ..checks.registry import get_check_definition, validate_check_parameters, RuleParameterError
from ..core.config import OllamaSettings
from ..models.job import ApiError, Job, JobKind, JobStatus, Progress, ResultReference
from ..models.rule import ConfirmationState, Rulebook, RuleOrigin
from ..models.rule_extraction import (
    ExtractionClaim, ExtractionInput, ExtractionPolicy, RuleExtractionRequest,
)
from .repository import RulebookRepository, RulebookStorageError
from .workflow_repository import (
    JobClaimLostError, JobNotFoundError, WorkflowStorageError,
    _WorkflowRepository, _decode_job, _job_row_values, _require_identifier,
)

ERROR_MESSAGES = {
    "MODEL_UNAVAILABLE": "The selected local model is unavailable.",
    "OLLAMA_UNAVAILABLE": "The local model service is unavailable.",
    "MODEL_REQUEST_TIMEOUT": "The local model request timed out.",
    "INVALID_RULE_PROPOSALS": "The model did not return valid, source-linked rule proposals.",
    "MODEL_PROTOCOL_ERROR": "The local model response could not be verified.",
    "MODEL_CONFIGURATION_ERROR": "The saved model configuration cannot be used.",
    "EXTRACTION_TIMEOUT": "Extraction exceeded its processing time limit.",
    "EXTRACTION_INTERRUPTED": "Extraction was interrupted; submit a new request to try again.",
    "EXTRACTION_FAILED": "Extraction could not be completed.",
    "EXTRACTION_OUTPUT_CONFLICT": "The reserved rulebook identifier is already occupied.",
}
_TERMINAL = {"completed", "failed", "cancelled"}

class ExtractionSubmissionError(RuntimeError):
    """A narrow, safe HTTP-503 category for acceptance failures."""

def safe_error(code: str, job_id: str) -> ApiError:
    """Persist only allowlisted messages, never exception/model/configuration text."""
    if code not in ERROR_MESSAGES:
        raise ValueError("Unsupported extraction error code.")
    return ApiError(code=code, message=ERROR_MESSAGES[code], request_id=job_id)

def _now(connection):
    return connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]

def _decode_input(row) -> ExtractionInput:
    try:
        return ExtractionInput.model_validate(dict(row))
    except (ValidationError, TypeError, ValueError) as error:
        raise WorkflowStorageError("A private extraction request failed validation.") from error

def _validate_draft(draft: Rulebook | dict[str, Any], request: ExtractionInput) -> Rulebook:
    # Dump mutable nested models before validation: Pydantic otherwise trusts instances.
    raw = draft.model_dump() if isinstance(draft, Rulebook) else draft
    snapshot = Rulebook.model_validate(raw)
    json.dumps(snapshot.model_dump(mode="json"), allow_nan=False)
    if (snapshot.rulebook_id != request.rulebook_id or snapshot.version != 1
            or snapshot.guideline_text != request.guideline_text
            or snapshot.confirmation_state is not ConfirmationState.DRAFT):
        raise ValueError("Extraction output identity/state does not match its request.")
    for rule in snapshot.rules:
        if (rule.origin is not RuleOrigin.EXTRACTED
                or rule.confirmation_state is not ConfirmationState.DRAFT):
            raise ValueError("Extracted rules must retain extracted origin and draft state.")
        if rule.check_name is None:
            if rule.parameters:
                raise ValueError("Unsupported proposals must not invent executable parameters.")
        elif get_check_definition(rule.check_name) is None:
            raise ValueError("Extracted check mappings must use the authoritative registry.")
        else:
            validate_check_parameters(rule.check_name, rule.parameters)
    return snapshot

class ExtractionRepository(_WorkflowRepository):
    """Own all managed-kind execution mutations; generic reads/cancel still work."""

    def enqueue(
        self, request: RuleExtractionRequest, *, settings: OllamaSettings,
        policy: ExtractionPolicy | None = None,
    ) -> Job:
        if not isinstance(request, RuleExtractionRequest) or not isinstance(settings, OllamaSettings):
            raise TypeError("Expected extraction request and Ollama settings models.")
        submitted = RuleExtractionRequest.model_validate(request.model_dump())
        effective = OllamaSettings.model_validate(settings.model_dump())
        bounds = ExtractionPolicy() if policy is None else ExtractionPolicy.model_validate(policy.model_dump())
        job_id = f"job_rule_extraction_{uuid4().hex}"
        private = ExtractionInput(
            job_id=job_id, rulebook_id=f"rulebook_{uuid4().hex}",
            guideline_text=submitted.guideline_text, settings=effective, policy=bounds,
        )
        with self._connection(write=True) as connection:
            job = Job.queued(job_id=job_id, kind=JobKind.RULE_EXTRACTION, total=1, now=_now(connection))
            connection.execute(
                """INSERT INTO jobs
                   (job_id, kind, status, progress, result, error, created_at, updated_at,
                    resource_kind, resource_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'rulebook',%s)""",
                _job_row_values(job) + (private.rulebook_id,),
            )
            connection.execute(
                """INSERT INTO rule_extraction_requests
                   (job_id, rulebook_id, guideline_text, settings, policy)
                   VALUES (%s,%s,%s,%s,%s)""",
                (job_id, private.rulebook_id, private.guideline_text,
                 Jsonb(effective.model_dump(mode="json")), Jsonb(bounds.model_dump(mode="json"))),
            )
            saved = self._job(connection, job_id)
        # Context exit includes commit acknowledgement. Unknown acceptance is NOT a 202.
        return saved

    @staticmethod
    def _job(connection, job_id: str) -> Job:
        row = connection.execute("SELECT * FROM jobs WHERE job_id=%s", (job_id,)).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        return _decode_job(row)

    @staticmethod
    def _lock_request(connection, row) -> ExtractionInput:
        if row is None or row["kind"] != "rule_extraction":
            raise JobClaimLostError("This is not a managed extraction job.")
        private = connection.execute(
            "SELECT * FROM rule_extraction_requests WHERE job_id=%s FOR UPDATE", (row["job_id"],),
        ).fetchone()
        if private is None:
            raise WorkflowStorageError("The extraction request ledger is missing.")
        request = _decode_input(private)
        if row["resource_kind"] != "rulebook" or row["resource_id"] != request.rulebook_id:
            raise WorkflowStorageError("Extraction resource identity is inconsistent.")
        return request

    @staticmethod
    def _claim_snapshot(claim: ExtractionClaim) -> ExtractionClaim:
        if not isinstance(claim, ExtractionClaim):
            raise TypeError("Expected an ExtractionClaim.")
        return ExtractionClaim.model_validate(claim.model_dump())

    @staticmethod
    def _owned(row, request: ExtractionInput, claim: ExtractionClaim, now) -> None:
        if (row["job_id"] != claim.job.job_id or request != claim.request
                or row["claimed_by"] != claim.worker_id or row["claim_token"] != claim.claim_token
                or row["attempt"] != 1 or row["status"] not in {"running", "cancelling"}
                or row["lease_until"] is None or row["lease_until"] <= now):
            raise JobClaimLostError("The worker no longer owns this extraction attempt.")

    def claim_next(self, *, worker_id: str) -> ExtractionClaim | None:
        worker_id = _require_identifier(worker_id, "worker_id")
        with self._connection(write=True) as connection:
            row = connection.execute(
                """SELECT j.* FROM jobs j
                   WHERE j.kind=%s AND j.status='queued' AND j.attempt=0
                     AND EXISTS (SELECT 1 FROM rule_extraction_requests r
                                 WHERE r.job_id=j.job_id AND r.started_at IS NULL)
                   ORDER BY j.created_at, j.job_id FOR UPDATE OF j SKIP LOCKED LIMIT 1""",
                ("rule_extraction",),
            ).fetchone()
            if row is None:
                return None
            request = self._lock_request(connection, row)
            if request.started_at is not None:
                raise WorkflowStorageError("A queued extraction already has an attempt.")
            now = _now(connection)
            deadline = now + timedelta(seconds=request.policy.max_seconds)
            lease = now + timedelta(seconds=request.policy.lease_seconds)
            token = uuid4().hex
            connection.execute(
                "UPDATE rule_extraction_requests SET started_at=%s, deadline=%s WHERE job_id=%s",
                (now, deadline, row["job_id"]),
            )
            connection.execute(
                """UPDATE jobs SET status='running', attempt=1, claimed_by=%s,
                   claim_token=%s, lease_until=%s, updated_at=%s, progress=%s WHERE job_id=%s""",
                (worker_id, token, lease, now,
                 Jsonb(Progress(phase="extracting", processed=0, total=1, fraction=0.0).model_dump()),
                 row["job_id"]),
            )
            request = ExtractionInput.model_validate({
                **request.model_dump(), "started_at": now, "deadline": deadline,
            })
            claimed = ExtractionClaim(
                job=self._job(connection, row["job_id"]), request=request,
                worker_id=worker_id, claim_token=token, lease_until=lease,
            )
        # An uncertain claim commit is never followed by inference; recovery handles it.
        return claimed

    def _lock_claim(self, connection, claim):
        row = connection.execute(
            "SELECT * FROM jobs WHERE job_id=%s FOR UPDATE", (claim.job.job_id,),
        ).fetchone()
        request = self._lock_request(connection, row)
        return row, request

    @staticmethod
    def _terminal(connection, row, *, status: str, now, code: str | None = None,
                  result: ResultReference | None = None) -> Job:
        if status not in _TERMINAL:
            raise ValueError("Expected a terminal extraction state.")
        error = safe_error(code, row["job_id"]) if code is not None else None
        progress = Progress(
            phase=status, processed=1 if status == "completed" else 0, total=1,
            fraction=1.0 if status == "completed" else 0.0,
        )
        connection.execute(
            """UPDATE jobs SET status=%s, progress=%s, result=%s, error=%s,
               claimed_by=NULL, claim_token=NULL, lease_until=NULL, updated_at=%s WHERE job_id=%s""",
            (status, Jsonb(progress.model_dump(mode="json")),
             None if result is None else Jsonb(result.model_dump(mode="json")),
             None if error is None else Jsonb(error.model_dump(mode="json")), now, row["job_id"]),
        )
        return ExtractionRepository._job(connection, row["job_id"])

    def checkpoint(self, claim: ExtractionClaim, *, renew: bool = False) -> Job:
        """Poll authoritative ownership; cancel/timeout may terminalize under a live lease."""
        claim = self._claim_snapshot(claim)
        with self._connection(write=True) as connection:
            row, request = self._lock_claim(connection, claim)
            now = _now(connection)
            self._owned(row, request, claim, now)
            if row["status"] == "cancelling":
                saved = self._terminal(connection, row, status="cancelled", now=now)
            elif request.deadline <= now:
                saved = self._terminal(connection, row, status="failed", now=now, code="EXTRACTION_TIMEOUT")
            else:
                if renew:
                    # Lease can outlast the computation deadline so a still-live owner can
                    # record timeout; checkpoint never renews at/after that deadline.
                    connection.execute(
                        "UPDATE jobs SET lease_until=%s, updated_at=%s WHERE job_id=%s",
                        (now + timedelta(seconds=request.policy.lease_seconds), now, row["job_id"]),
                    )
                saved = self._job(connection, row["job_id"])
        return saved

    def fail(self, claim: ExtractionClaim, code: str) -> Job:
        claim = self._claim_snapshot(claim)
        safe_error(code, claim.job.job_id)  # Reject unknown codes before opening storage.
        with self._connection(write=True) as connection:
            row, request = self._lock_claim(connection, claim)
            now = _now(connection)
            self._owned(row, request, claim, now)
            if row["status"] == "cancelling":
                saved = self._terminal(connection, row, status="cancelled", now=now)
            else:
                effective = "EXTRACTION_TIMEOUT" if request.deadline <= now else code
                saved = self._terminal(connection, row, status="failed", now=now, code=effective)
        return saved

    def publish(self, claim: ExtractionClaim, draft: Rulebook | dict[str, Any]) -> Job:
        claim = self._claim_snapshot(claim)
        with self._connection(write=True) as connection:
            row, request = self._lock_claim(connection, claim)
            now = _now(connection)
            self._owned(row, request, claim, now)
            if row["status"] == "cancelling":
                saved = self._terminal(connection, row, status="cancelled", now=now)
            elif request.deadline <= now:
                saved = self._terminal(connection, row, status="failed", now=now, code="EXTRACTION_TIMEOUT")
            else:
                try:
                    snapshot = _validate_draft(draft, request)
                except (ValidationError, ValueError, TypeError, RuleParameterError):
                    # Cancellation still precedes validation; no exception text is stored.
                    now = _now(connection)
                    self._owned(row, request, claim, now)
                    saved = self._terminal(
                        connection, row, status="failed", now=now,
                        code="EXTRACTION_TIMEOUT" if request.deadline <= now else "INVALID_RULE_PROPOSALS",
                    )
                else:
                    connection.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (request.rulebook_id,),
                    )
                    occupied = connection.execute(
                        "SELECT 1 FROM rulebook_versions WHERE rulebook_id=%s LIMIT 1", (request.rulebook_id,),
                    ).fetchone() is not None
                    # This timestamp is intentionally AFTER the advisory wait and queries.
                    now = _now(connection)
                    self._owned(row, request, claim, now)
                    if request.deadline <= now:
                        saved = self._terminal(connection, row, status="failed", now=now, code="EXTRACTION_TIMEOUT")
                    elif occupied:
                        saved = self._terminal(connection, row, status="failed", now=now, code="EXTRACTION_OUTPUT_CONFLICT")
                    else:
                        connection.execute(
                            """INSERT INTO rulebook_versions
                               (rulebook_id, version, confirmation_state, document) VALUES (%s,1,'draft',%s)""",
                            (request.rulebook_id, Jsonb(snapshot.model_dump(mode="json"))),
                        )
                        # An insert can itself wait (indexes/triggers). Roll back that insert
                        # if ownership or deadline elapsed; recovery, not a stale owner, wins.
                        now = _now(connection)
                        self._owned(row, request, claim, now)
                        if request.deadline <= now:
                            raise ExtractionPublicationExpired("Publication exceeded its deadline.")
                        saved = self._terminal(
                            connection, row, status="completed", now=now,
                            result=ResultReference(kind="rulebook", id=request.rulebook_id, version=1,
                                href=f"/api/v1/rulebooks/{request.rulebook_id}?version=1"),
                        )
        return saved

    def readback(self, claim: ExtractionClaim) -> Job:
        """Read only after an uncertain commit. Never retries inference or writes failure.

        A nonterminal response means the outcome is unresolved, NOT proven rollback.
        Completed state is accepted only with its original pinned, validated draft.
        """
        claim = self._claim_snapshot(claim)
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=%s", (claim.job.job_id,)).fetchone()
            if row is None or row["kind"] != "rule_extraction":
                raise WorkflowStorageError("Extraction reconciliation identity is missing.")
            private = connection.execute(
                "SELECT * FROM rule_extraction_requests WHERE job_id=%s", (row["job_id"],),
            ).fetchone()
            if private is None:
                raise WorkflowStorageError("Extraction reconciliation ledger is missing.")
            request = _decode_input(private)
            if (request != claim.request or row["resource_kind"] != "rulebook"
                    or row["resource_id"] != request.rulebook_id):
                raise WorkflowStorageError("Extraction reconciliation identity disagrees.")
            job = _decode_job(row)
            if job.status is JobStatus.COMPLETED:
                expected = ResultReference(kind="rulebook", id=request.rulebook_id, version=1,
                    href=f"/api/v1/rulebooks/{request.rulebook_id}?version=1")
                book_row = connection.execute(
                    "SELECT * FROM rulebook_versions WHERE rulebook_id=%s AND version=1", (request.rulebook_id,),
                ).fetchone()
                if job.result != expected or book_row is None:
                    raise WorkflowStorageError("The completed extraction pair is inconsistent.")
                try:
                    _validate_draft(RulebookRepository._decode(book_row), request)
                except (ValueError, TypeError, RuleParameterError, RulebookStorageError) as error:
                    raise WorkflowStorageError("The pinned extraction output is invalid.") from error
            elif job.result is not None:
                raise WorkflowStorageError("A noncompleted extraction advertises a result.")
        return job

    def recover_stale(self, *, limit: int = 100) -> list[Job]:
        """Only request-backed extractions; one locked, bounded batch, never requeue."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Recovery batch size must be between 1 and 1000.")
        recovered = []
        with self._connection(write=True) as connection:
            rows = connection.execute(
                """SELECT j.* FROM jobs j WHERE j.kind=%s
                   AND j.status IN ('running','cancelling')
                   AND (j.lease_until IS NULL OR j.lease_until <= clock_timestamp())
                   AND EXISTS (SELECT 1 FROM rule_extraction_requests r WHERE r.job_id=j.job_id)
                   ORDER BY j.created_at, j.job_id FOR UPDATE OF j SKIP LOCKED LIMIT %s""",
                ("rule_extraction", limit),
            ).fetchall()
            for row in rows:
                request = self._lock_request(connection, row)
                now = _now(connection)
                if row["lease_until"] is not None and row["lease_until"] > now:
                    continue
                if row["status"] == "cancelling":
                    saved = self._terminal(connection, row, status="cancelled", now=now)
                else:
                    code = ("EXTRACTION_TIMEOUT" if request.deadline is not None and request.deadline <= now
                            else "EXTRACTION_INTERRUPTED")
                    saved = self._terminal(connection, row, status="failed", now=now, code=code)
                connection.execute(
                    "UPDATE jobs SET recovery_count=recovery_count+1 WHERE job_id=%s", (row["job_id"],),
                )
                recovered.append(saved)
        return recovered

class ExtractionPublicationExpired(RuntimeError):
    """Known transaction rollback; caller may record timeout only under a live lease."""
