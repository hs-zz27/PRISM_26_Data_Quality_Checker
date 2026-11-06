"""Capabilities and bounded, read-only readiness probes.

Nothing here loads a model, runs inference, hashes a checkpoint, downloads a
file, opens a database connection or generates text. Ollama is probed with one
short model-list request. Every probe is injectable, so tests need no Ollama and
no weights. Results carry no private paths, credentials or raw error text.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..checks.registry import CHECK_REGISTRY
from ..core import person_model
from ..core.config import OllamaSettings, load_ollama_settings
from ..models.system import (
    Capabilities,
    CheckCapability,
    OllamaStatus,
    PersonModelStatus,
    SystemStatus,
)

OLLAMA_PROBE_SECONDS = 2.0


def build_capabilities() -> Capabilities:
    """Describe the build from the real check registry; no service is started."""
    return Capabilities(
        dataset_formats=["penn_fudan", "coco", "labelme"],
        guideline_input_formats=["plain_text"],
        coordinate_convention="format_specific",
        checks=[CheckCapability(name=name, implementation="implemented") for name in sorted(CHECK_REGISTRY)],
        execution_device="cpu",
    )


@dataclass(frozen=True)
class OllamaProbe:
    reachable: bool
    model_names: tuple[str, ...] | None  # None when the installed models are unknown


def probe_ollama(
    settings: OllamaSettings,
    *,
    timeout_seconds: float = OLLAMA_PROBE_SECONDS,
    transport: httpx.BaseTransport | None = None,
) -> OllamaProbe:
    """List installed models with one short metadata request; never generates text."""
    try:
        with httpx.Client(
            base_url=settings.host, timeout=timeout_seconds, trust_env=False,
            follow_redirects=False, transport=transport,
        ) as client:
            response = client.get("/api/tags")
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError:
        return OllamaProbe(reachable=True, model_names=None)
    except httpx.RequestError:
        return OllamaProbe(reachable=False, model_names=None)
    except ValueError:  # the server answered, but not with JSON
        return OllamaProbe(reachable=True, model_names=None)
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return OllamaProbe(reachable=True, model_names=None)
    names = {
        value for item in models if isinstance(item, dict)
        for value in (item.get("name"), item.get("model")) if isinstance(value, str)
    }
    return OllamaProbe(reachable=True, model_names=tuple(sorted(names)))


def model_is_installed(configured: str, installed: Iterable[str]) -> bool:
    """Ollama lists models with a tag; an untagged configured name means ':latest'."""
    wanted = {configured, configured if ":" in configured else f"{configured}:latest"}
    return any(name in wanted for name in installed)


class SystemStatusService:
    def __init__(
        self,
        *,
        ollama_settings: Callable[[], OllamaSettings] = load_ollama_settings,
        ollama_probe: Callable[[OllamaSettings], OllamaProbe] = probe_ollama,
        person_model_checkpoint: Callable[[], Path | None] = person_model.checkpoint_path,
    ) -> None:
        self._ollama_settings = ollama_settings
        self._ollama_probe = ollama_probe
        self._person_model_checkpoint = person_model_checkpoint

    def status(self) -> SystemStatus:
        return SystemStatus(ollama=self._ollama(), person_model=self._person_model(), execution_device="cpu")

    def _person_model(self) -> PersonModelStatus:
        try:
            checkpoint = "present" if self._person_model_checkpoint() is not None else "missing"
        except OSError:
            checkpoint = "unreadable"
        return PersonModelStatus(model=person_model.MODEL_NAME, checkpoint_file=checkpoint)

    def _ollama(self) -> OllamaStatus:
        try:
            settings = self._ollama_settings()
        except (OSError, ValueError):  # unreadable or invalid configuration
            return OllamaStatus(configuration="invalid", reachable=False, model=None, model_available=None)
        probe = self._ollama_probe(settings)
        available = None if probe.model_names is None else model_is_installed(settings.model, probe.model_names)
        return OllamaStatus(
            configuration="valid", reachable=probe.reachable, model=settings.model, model_available=available,
        )
