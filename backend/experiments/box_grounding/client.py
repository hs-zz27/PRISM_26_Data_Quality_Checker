"""Bounded local Ollama calls that mirror the production guideline client's safeguards.

The production client reads the root .env and needs the database stack, so it cannot run
in this portable environment. This module keeps its safeguards: the host is explicit and
loopback-only; the model digest and vision capability are verified before and after every
call; request and streamed response bytes are bounded; responses must be complete, fit the
context window, parse as structured JSON without duplicate fields, and echo the expected
identity exactly. Nothing is repaired. A request or parse failure becomes an explicit
"unavailable" reason and is never turned into an empty pass. There are no hidden retries.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from .prompts import PreparedRequest
from .settings import ExecutionSettings

METADATA_RESPONSE_LIMIT = 1024 * 1024
OLLAMA_METRICS = ("total_duration", "load_duration", "prompt_eval_count", "prompt_eval_duration",
                  "eval_count", "eval_duration", "done_reason")
# Reasons that point to a broken or misconfigured server rather than one hard sample.
INFRASTRUCTURE_REASONS = frozenset({
    "connection_failed", "http_error", "request_timeout", "identity_unavailable",
    "model_identity_changed", "local_vision_unverified",
})


class InferenceUnavailable(RuntimeError):
    """The result cannot be trusted. The message is a short, stable reason code."""


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON fields instead of silently keeping the last one."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate response field.")
        result[key] = value
    return result


@dataclass(frozen=True)
class ModelCall:
    """One verified-or-unavailable model response. Exactly one of prediction/reason is set."""

    prediction: BaseModel | None
    reason: str | None
    model_verified: bool
    chat_attempted: bool
    duration_ms: float  # whole call, including the verification requests (as in production)
    chat_ms: float | None  # the /api/chat request alone
    metrics: dict[str, Any]
    content: str | None
    request_sha256: str


class OllamaClient:
    """A sequential client. Clients are never shared across threads and never run concurrently."""

    def __init__(self, execution: ExecutionSettings, *, transport: httpx.BaseTransport | None = None):
        self.execution = execution
        self.client = httpx.Client(base_url=execution.host, trust_env=False, follow_redirects=False,
                                   timeout=execution.timeout_seconds, transport=transport)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "OllamaClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def request(self, method: str, path: str, body: bytes | None = None, *,
                response_limit: int | None = None) -> dict[str, Any]:
        limit = self.execution.max_response_bytes if response_limit is None else response_limit
        if body is not None and len(body) > self.execution.max_request_bytes:
            raise InferenceUnavailable("context_limit")
        try:
            with self.client.stream(method, path, content=body,
                                    headers={"Content-Type": "application/json"}) as response:
                response.raise_for_status()
                parts: list[bytes] = []
                total = 0
                for part in response.iter_bytes():
                    total += len(part)
                    if total > limit:
                        raise InferenceUnavailable("response_limit")
                    parts.append(part)
                result = json.loads(b"".join(parts), object_pairs_hook=unique_object)
        except httpx.TimeoutException as error:
            raise InferenceUnavailable("request_timeout") from error
        except httpx.HTTPStatusError as error:
            raise InferenceUnavailable("http_error") from error
        except httpx.TransportError as error:
            raise InferenceUnavailable("connection_failed") from error
        except (httpx.HTTPError, ValueError, UnicodeError, RecursionError) as error:
            raise InferenceUnavailable("invalid_response") from error
        if not isinstance(result, dict) or result.get("error"):
            raise InferenceUnavailable("invalid_response")
        return result

    def installed_digest(self) -> str:
        """The digest of the one installed model with the configured tag."""
        tag = self.execution.model
        models = self.request("GET", "/api/tags", response_limit=METADATA_RESPONSE_LIMIT).get("models")
        if not isinstance(models, list):
            raise InferenceUnavailable("identity_unavailable")
        matched = [m for m in models if isinstance(m, dict) and m.get("name") in {tag, tag + ":latest"}]
        digest = matched[0].get("digest") if len(matched) == 1 else None
        if isinstance(digest, str) and re.fullmatch("[0-9a-f]{64}", digest):
            digest = "sha256:" + digest
        if not isinstance(digest, str) or not re.fullmatch("sha256:[0-9a-f]{64}", digest):
            raise InferenceUnavailable("identity_unavailable")
        return digest

    def server_version(self) -> str | None:
        try:
            version = self.request("GET", "/api/version", response_limit=METADATA_RESPONSE_LIMIT).get("version")
        except InferenceUnavailable:
            return None
        return version if isinstance(version, str) else None

    def verify(self) -> None:
        """The pinned local model is still installed, unchanged and vision-capable."""
        if self.installed_digest() != self.execution.model_digest:
            raise InferenceUnavailable("model_identity_changed")
        metadata = self.request("POST", "/api/show", json.dumps({"model": self.execution.model}).encode(),
                                response_limit=METADATA_RESPONSE_LIMIT)
        capabilities = metadata.get("capabilities")
        if (metadata.get("remote_host") or metadata.get("remote_model")
                or not isinstance(capabilities, list) or "vision" not in capabilities):
            raise InferenceUnavailable("local_vision_unverified")

    def check_response(self, response: dict[str, Any]) -> str:
        execution = self.execution
        if response.get("model") not in {execution.model, execution.model + ":latest"}:
            raise InferenceUnavailable("model_identity_changed")
        count = response.get("prompt_eval_count")
        if type(count) is not int or not 0 < count < execution.num_ctx - execution.num_predict:
            raise InferenceUnavailable("context_unverified")
        if response.get("done") is not True or response.get("done_reason") == "length":
            raise InferenceUnavailable("incomplete_response")
        message = response.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise InferenceUnavailable("invalid_response")
        return message["content"]

    def chat(self, prepared: PreparedRequest, schema: type[BaseModel], identity: dict[str, Any]) -> ModelCall:
        """Verify, request, validate and verify again. Never retries and never repairs."""
        start = time.monotonic()
        prediction: BaseModel | None = None
        reason: str | None = None
        verified = attempted = False
        chat_ms: float | None = None
        metrics: dict[str, Any] = {}
        content: str | None = None
        try:
            self.verify()
            attempted = True
            chat_start = time.monotonic()
            try:
                response = self.request("POST", "/api/chat", prepared.body)
            finally:
                chat_ms = (time.monotonic() - chat_start) * 1000
            metrics = {key: response[key] for key in OLLAMA_METRICS
                       if isinstance(response.get(key), (int, str))}
            content = self.check_response(response)
            try:
                json.loads(content, object_pairs_hook=unique_object)
                prediction = schema.model_validate_json(content)
            except (ValidationError, ValueError, RecursionError) as error:
                raise InferenceUnavailable("invalid_proposal") from error
            if any(getattr(prediction, field) != value for field, value in identity.items()):
                prediction = None
                raise InferenceUnavailable("identity_mismatch")
            self.verify()
            verified = True
        except InferenceUnavailable as error:
            prediction, reason = None, str(error)
        return ModelCall(prediction=prediction, reason=reason, model_verified=verified,
                         chat_attempted=attempted, duration_ms=(time.monotonic() - start) * 1000,
                         chat_ms=chat_ms, metrics=metrics, content=content,
                         request_sha256=prepared.sha256)
