"""Server-side person-detection settings and the configuration frozen into each audit.

Settings are read from the environment, or the project .env file, when an audit
is accepted. The frozen copy holds no file paths. The checkpoint location stays
worker-local: it is resolved when a sample is processed and the file is then
checked against the frozen SHA-256. Nothing here loads a model or downloads weights.
"""

import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, ValidationError

from ..models.detection_evidence import (
    EVIDENCE_SCHEMA_VERSION,
    DetectorConfiguration,
    PreprocessingIdentity,
)
from ..models.detection_matching import MATCHING_POLICY
from .config import DEFAULT_ENV_PATH, AuditConfigurationError

# Kept here so the API process need not import Torch; a test checks it against the detector.
DETECTOR_ARCHITECTURE = "fasterrcnn_mobilenet_v3_large_320_fpn"

_ENABLED = "DETECTION_ENABLED"
_CHECKPOINT_PATH = "DETECTION_CHECKPOINT_PATH"
_CHECKPOINT_SHA256 = "DETECTION_CHECKPOINT_SHA256"
_PERSON_LABELS = "DETECTION_PERSON_LABELS"
_MIN_CONFIDENCE = "DETECTION_MIN_CONFIDENCE"
_MIN_IOU = "DETECTION_MIN_IOU"


class DetectionConfigurationError(AuditConfigurationError):
    """Detection settings are present but invalid. Reported to clients as a safe 503."""


class DetectionSettings(BaseModel):
    """Current server settings. The checkpoint path is private and never frozen into audits."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, arbitrary_types_allowed=True)

    enabled: bool
    checkpoint_path: Path | None = None
    checkpoint_sha256: str | None = None
    person_labels: tuple[str, ...] = ()
    min_confidence: float | None = None
    min_iou: float | None = None


def _values(env_path: str | Path | None) -> dict[str, str | None]:
    path = DEFAULT_ENV_PATH if env_path is None else Path(env_path)
    try:
        return {**dotenv_values(path, interpolate=False), **os.environ}
    except OSError:
        raise DetectionConfigurationError("Cannot read the detection environment file.") from None


def load_checkpoint_location(*, env_path: str | Path | None = None) -> Path | None:
    """Return this worker's checkpoint path, or None when none is configured.

    Only the location is read. Whether detection is enabled, and its thresholds,
    always come from the configuration frozen into the audit.
    """
    raw = (_values(env_path).get(_CHECKPOINT_PATH) or "").strip()
    return Path(raw) if raw else None


def load_detection_settings(*, env_path: str | Path | None = None) -> DetectionSettings:
    """Read the current settings. Detection is off unless DETECTION_ENABLED is true."""
    values = _values(env_path)
    switch = (values.get(_ENABLED) or "false").strip().lower()
    if switch not in ("true", "false"):
        raise DetectionConfigurationError(f"{_ENABLED} must be true or false.")
    if switch == "false":
        return DetectionSettings(enabled=False)

    path = (values.get(_CHECKPOINT_PATH) or "").strip()
    labels = tuple(label.strip() for label in (values.get(_PERSON_LABELS) or "").split(",") if label.strip())
    try:
        settings = DetectionSettings(
            enabled=True,
            checkpoint_path=Path(path) if path else None,
            checkpoint_sha256=(values.get(_CHECKPOINT_SHA256) or "").strip().lower() or None,
            person_labels=labels,
            min_confidence=float(values[_MIN_CONFIDENCE]) if values.get(_MIN_CONFIDENCE) else None,
            min_iou=float(values[_MIN_IOU]) if values.get(_MIN_IOU) else None,
        )
    except (TypeError, ValueError, ValidationError):
        raise DetectionConfigurationError(
            f"{_MIN_CONFIDENCE} and {_MIN_IOU} must be numbers."
        ) from None
    missing = [name for name, present in (
        (_CHECKPOINT_PATH, settings.checkpoint_path is not None),
        (_CHECKPOINT_SHA256, settings.checkpoint_sha256 is not None),
        (_PERSON_LABELS, bool(settings.person_labels)),
        (_MIN_CONFIDENCE, settings.min_confidence is not None),
        (_MIN_IOU, settings.min_iou is not None),
    ) if not present]
    if missing:
        raise DetectionConfigurationError(
            f"Detection is enabled but these settings are missing: {', '.join(missing)}."
        )
    return settings


def freeze_detector_configuration(settings: DetectionSettings) -> DetectorConfiguration:
    """Copy the settings an audit needs; the checkpoint path is deliberately left out."""
    if not isinstance(settings, DetectionSettings):
        raise TypeError("settings must be a DetectionSettings instance.")
    if not settings.enabled:
        return DetectorConfiguration(enabled=False)
    try:
        return DetectorConfiguration(
            enabled=True,
            architecture=DETECTOR_ARCHITECTURE,
            checkpoint_sha256=settings.checkpoint_sha256,
            min_confidence=settings.min_confidence,
            min_iou=settings.min_iou,
            person_labels=list(settings.person_labels),
            matching_policy=MATCHING_POLICY,
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
            preprocessing=PreprocessingIdentity(),
        )
    except ValidationError:
        raise DetectionConfigurationError(
            "Detection settings are invalid: check the checkpoint SHA-256, labels and thresholds."
        ) from None


def current_detector_configuration(*, env_path: str | Path | None = None) -> DetectorConfiguration:
    """Read the server settings now and freeze them for one new audit."""
    return freeze_detector_configuration(load_detection_settings(env_path=env_path))
