"""Capabilities and bounded, read-only readiness probes.

Nothing here loads a model, runs inference, hashes a checkpoint, downloads a
file, opens a database connection or generates text. Ollama is probed with one
short model-list request. Every probe is injectable, so tests need no Ollama and
no weights. Results carry no private paths, credentials or raw error text.
"""

import importlib.util
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..checks.registry import CHECK_REGISTRY
from ..core.config import OllamaSettings, load_ollama_settings
from ..core.person_detection import (
    DETECTOR_ARCHITECTURE,
    DetectionConfigurationError,
    DetectionSettings,
    load_checkpoint_location,
    load_detection_settings,
)
from ..models.system import (
    Capabilities,
    CheckCapability,
    DetectorStatus,
    EvidenceFeature,
    OllamaStatus,
    SystemStatus,
)

OLLAMA_PROBE_SECONDS = 2.0

_EVIDENCE_FEATURES = (
    EvidenceFeature(
        name="person_detection", implementation="implemented",
        description=(
            "CPU person detector run inside audit sample processing when enabled for an audit. "
            "Stored as the detection_evidence measurement; not a guideline check and creates no findings."
        ),
    ),
    EvidenceFeature(
        name="detection_annotation_matching", implementation="implemented",
        description=(
            "Conservative matching of detections to person annotations, stored inside "
            "detection_evidence. Ambiguity is reported for review, never resolved automatically."
        ),
    ),
)


def build_capabilities() -> Capabilities:
    """Describe the build from the real check registry; no service is started."""
    return Capabilities(
        dataset_formats=["penn_fudan", "coco", "labelme"],
        guideline_input_formats=["plain_text"],
        coordinate_convention="format_specific",
        checks=[CheckCapability(name=name, implementation="implemented") for name in sorted(CHECK_REGISTRY)],
        evidence_features=list(_EVIDENCE_FEATURES),
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


def detector_packages_available() -> bool:
    """Whether Torch and torchvision are installed, found without importing either."""
    return all(importlib.util.find_spec(name) is not None for name in ("torch", "torchvision"))


def validation_not_recorded() -> str:
    """No validation record exists yet, so the honest answer is always 'unknown'."""
    return "unknown"


class SystemStatusService:
    def __init__(
        self,
        *,
        ollama_settings: Callable[[], OllamaSettings] = load_ollama_settings,
        ollama_probe: Callable[[OllamaSettings], OllamaProbe] = probe_ollama,
        detection_settings: Callable[[], DetectionSettings] = load_detection_settings,
        checkpoint_location: Callable[[], Path | None] = load_checkpoint_location,
        packages_available: Callable[[], bool] = detector_packages_available,
        validation_status: Callable[[], str] = validation_not_recorded,
    ) -> None:
        self._ollama_settings = ollama_settings
        self._ollama_probe = ollama_probe
        self._detection_settings = detection_settings
        self._checkpoint_location = checkpoint_location
        self._packages_available = packages_available
        self._validation_status = validation_status

    def status(self) -> SystemStatus:
        return SystemStatus(ollama=self._ollama(), detector=self._detector(), execution_device="cpu")

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

    def _detector(self) -> DetectorStatus:
        try:
            configuration = "enabled" if self._detection_settings().enabled else "disabled"
        except DetectionConfigurationError:
            configuration = "invalid"
        try:
            location = self._checkpoint_location()
        except DetectionConfigurationError:
            location = None
        if location is None:
            checkpoint = "not_configured"
        else:
            try:
                checkpoint = "present" if location.is_file() else "missing"
            except OSError:
                checkpoint = "unreadable"
        return DetectorStatus(
            integration_supported=self._packages_available(), configuration=configuration,
            checkpoint_file=checkpoint, validation=self._validation_status(), architecture=DETECTOR_ARCHITECTURE,
        )
