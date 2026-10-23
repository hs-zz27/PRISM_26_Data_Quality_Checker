"""Explicit, opt-in settings for the pedestrian-box enclosure check.

Read from the environment or the project .env file when an audit is accepted. There is no
default model, digest or budget, no download and no implicit selection: with the check
disabled (the default) a rule using it stays unavailable. The frozen copy holds no secrets.
"""

import os
from collections.abc import Mapping

from dotenv import dotenv_values
from pydantic import ValidationError

from ..models.enclosure import EnclosureExecutionPin, EnclosureRuntime
from .config import DEFAULT_ENV_PATH, AuditConfigurationError, load_audit_settings

SAMPLE_DEADLINE_MARGIN_SECONDS = 30
_TRUE, _FALSE = {"1", "true", "yes"}, {"", "0", "false", "no"}


class EnclosureConfigurationError(AuditConfigurationError):
    """The enclosure settings are present but invalid. Reported to clients as a safe 503."""


def _integer(values: Mapping[str, str], name: str, default: int | None = None) -> int | None:
    raw = (values.get(name) or "").strip()
    if not raw:
        return default
    if not raw.lstrip("-").isdigit():
        raise EnclosureConfigurationError(f"{name} must be an integer.")
    return int(raw)


def load_enclosure_runtime(*, values: Mapping[str, str] | None = None,
                           sample_max_seconds: int | None = None) -> EnclosureRuntime | None:
    """Return the runtime to freeze into an accepted audit, or None when the check is disabled."""
    merged = {**dotenv_values(DEFAULT_ENV_PATH, interpolate=False), **os.environ} if values is None else dict(values)
    flag = (merged.get("ENCLOSURE_ENABLED") or "").strip().lower()
    if flag in _FALSE:
        return None
    if flag not in _TRUE:
        raise EnclosureConfigurationError("ENCLOSURE_ENABLED must be true or false.")
    missing = [n for n in ("ENCLOSURE_MODEL", "ENCLOSURE_MODEL_DIGEST", "ENCLOSURE_SAMPLE_BUDGET_SECONDS")
               if not (merged.get(n) or "").strip()]
    if missing:
        raise EnclosureConfigurationError(f"Missing required settings: {', '.join(missing)}.")
    from ..services.enclosure_prompt import INSTRUCTION_SHA256, SYSTEM_PROMPT_SHA256
    deadline = load_audit_settings().sample_max_seconds if sample_max_seconds is None else sample_max_seconds
    budget = _integer(merged, "ENCLOSURE_SAMPLE_BUDGET_SECONDS")
    if budget is None or budget > deadline - SAMPLE_DEADLINE_MARGIN_SECONDS:
        raise EnclosureConfigurationError(
            "ENCLOSURE_SAMPLE_BUDGET_SECONDS must leave at least "
            f"{SAMPLE_DEADLINE_MARGIN_SECONDS} seconds below AUDIT_SAMPLE_MAX_SECONDS ({deadline})."
        )
    try:
        pin = EnclosureExecutionPin(
            host=(merged.get("ENCLOSURE_HOST") or "http://127.0.0.1:11434").strip(),
            model=merged["ENCLOSURE_MODEL"].strip(), model_digest=merged["ENCLOSURE_MODEL_DIGEST"].strip(),
            system_prompt_sha256=SYSTEM_PROMPT_SHA256, instruction_sha256=INSTRUCTION_SHA256,
            num_ctx=_integer(merged, "ENCLOSURE_NUM_CTX", 16384), num_predict=_integer(merged, "ENCLOSURE_NUM_PREDICT", 2048),
            num_thread=_integer(merged, "ENCLOSURE_NUM_THREAD"),
            request_timeout_seconds=_integer(merged, "ENCLOSURE_REQUEST_TIMEOUT_SECONDS", 600),
            sample_budget_seconds=budget, max_boxes=_integer(merged, "ENCLOSURE_MAX_BOXES", 12),
            max_request_bytes=_integer(merged, "ENCLOSURE_MAX_REQUEST_BYTES", 64 * 1024**2),
            max_response_bytes=_integer(merged, "ENCLOSURE_MAX_RESPONSE_BYTES", 65_536),
            keep_alive_seconds=_integer(merged, "ENCLOSURE_KEEP_ALIVE_SECONDS", 300),
        )
    except ValidationError as error:
        raise EnclosureConfigurationError("The enclosure settings are invalid.") from error
    return EnclosureRuntime(execution=pin)
