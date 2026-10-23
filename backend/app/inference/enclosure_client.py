"""Bounded, sequential calls to a local Ollama server for the enclosure check.

Keeps the safeguards of the guideline client: loopback host, pinned model digest and vision
capability verified before and after every call, bounded request and streamed response bytes,
complete and context-verified responses, duplicate-field rejection and exact identity echoes.
Nothing is repaired, retried or substituted. Any failure is an explicit, safe reason code.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import re
import time
from typing import Any

import httpx
from pydantic import ValidationError

from ..models.enclosure import BoxResponse, EnclosureExecutionPin
from ..services.enclosure_prompt import PreparedRequest

_METADATA_LIMIT = 1024 * 1024


class InferenceUnavailable(RuntimeError):
    """The result cannot be trusted. The message is a short, stable, safe reason code."""


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate response field.")
        result[key] = value
    return result


@dataclass(frozen=True)
class BoxCall:
    response: BoxResponse | None
    failure: str | None
    model_verified: bool
    duration_ms: float
    chat_ms: float | None


class EnclosureClient:
    def __init__(self, pin: EnclosureExecutionPin, *, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.pin = pin
        self.clock = clock
        self.client = httpx.Client(base_url=pin.host, trust_env=False, follow_redirects=False,
                                   timeout=pin.request_timeout_seconds, transport=transport)

    def close(self) -> None:
        self.client.close()

    def remaining(self, deadline: float) -> float:
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise InferenceUnavailable("request_timeout")
        return remaining

    def request(self, method: str, path: str, body: bytes | None, deadline: float, limit: int) -> dict[str, Any]:
        if body is not None and len(body) > self.pin.max_request_bytes:
            raise InferenceUnavailable("context_limit")
        try:
            with self.client.stream(method, path, content=body, timeout=self.remaining(deadline),
                                    headers={"Content-Type": "application/json"}) as response:
                self.remaining(deadline)
                response.raise_for_status()
                parts: list[bytes] = []
                total = 0
                for part in response.iter_bytes():
                    self.remaining(deadline)
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
        self.remaining(deadline)
        if not isinstance(result, dict) or result.get("error"):
            raise InferenceUnavailable("invalid_response")
        return result

    def verify(self, deadline: float) -> None:
        models = self.request("GET", "/api/tags", None, deadline, _METADATA_LIMIT).get("models")
        tag = self.pin.model
        matched = [m for m in models if isinstance(m, dict) and m.get("name") in {tag, tag + ":latest"}] \
            if isinstance(models, list) else []
        digest = matched[0].get("digest") if len(matched) == 1 else None
        if isinstance(digest, str) and re.fullmatch("[0-9a-f]{64}", digest):
            digest = "sha256:" + digest
        if not isinstance(digest, str):
            raise InferenceUnavailable("identity_unavailable")
        if digest != self.pin.model_digest:
            raise InferenceUnavailable("model_identity_changed")
        shown = self.request("POST", "/api/show", json.dumps({"model": tag}).encode(), deadline, _METADATA_LIMIT)
        capabilities = shown.get("capabilities")
        if (shown.get("remote_host") or shown.get("remote_model")
                or not isinstance(capabilities, list) or "vision" not in capabilities):
            raise InferenceUnavailable("local_vision_unverified")

    def check(self, response: dict[str, Any]) -> str:
        pin = self.pin
        if response.get("model") not in {pin.model, pin.model + ":latest"}:
            raise InferenceUnavailable("model_identity_changed")
        count = response.get("prompt_eval_count")
        if type(count) is not int or not 0 < count < pin.num_ctx - pin.num_predict:
            raise InferenceUnavailable("context_unverified")
        if response.get("done") is not True or response.get("done_reason") == "length":
            raise InferenceUnavailable("incomplete_response")
        message = response.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise InferenceUnavailable("invalid_response")
        return message["content"]

    def chat(self, prepared: PreparedRequest, identity: dict[str, Any], timeout: float) -> BoxCall:
        """Verify, request, validate and verify again, all inside one time allowance."""
        start = self.clock()
        deadline = start + timeout
        response_model: BoxResponse | None = None
        failure: str | None = None
        verified, chat_ms = False, None
        try:
            self.verify(deadline)
            chat_start = self.clock()
            try:
                reply = self.request("POST", "/api/chat", prepared.body, deadline, self.pin.max_response_bytes)
            finally:
                chat_ms = (self.clock() - chat_start) * 1000
            content = self.check(reply)
            try:
                json.loads(content, object_pairs_hook=unique_object)
                response_model = BoxResponse.model_validate_json(content)
            except (ValidationError, ValueError, RecursionError) as error:
                raise InferenceUnavailable("invalid_proposal") from error
            if any(getattr(response_model, field) != value for field, value in identity.items()):
                response_model = None
                raise InferenceUnavailable("identity_mismatch")
            self.verify(deadline)
            self.remaining(deadline)
            verified = True
        except InferenceUnavailable as error:
            response_model, failure = None, str(error)
        return BoxCall(response_model, failure, verified, (self.clock() - start) * 1000, chat_ms)
