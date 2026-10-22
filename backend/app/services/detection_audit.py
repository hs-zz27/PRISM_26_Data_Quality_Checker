"""Add frozen-configuration detection evidence to one sample's measurements.

The wrapper runs inside the supervised audit child, so model loading, checkpoint
hashing and inference count against the sample's deadline. It holds only plain,
picklable configuration. No model is created in, or sent from, the API process,
and Torch is imported lazily, so importing this module stays light.

Evidence is stored under one measurement key and rides the existing measurement
persistence and sample-detail read path. Nothing here creates findings, changes
reviewer decisions or runs when stored evidence is read.
"""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from PIL import Image

from ..core.person_detection import DetectionConfigurationError, load_checkpoint_location
from ..datasets.penn_fudan import (
    load_annotation_text,
    load_image,
    parse_annotation_metadata,
    parse_annotations,
)
from ..models.annotation import AnnotationObject
from ..models.detection_evidence import (
    DetectionEvidence,
    DetectionNotRun,
    DetectorConfiguration,
    EvidenceStatus,
)
from .audit import audit_sample

EVIDENCE_KEY = "detection_evidence"

# An unset checkpoint location behaves like a missing file and is reported that way.
_NOT_CONFIGURED = Path("checkpoint-not-configured")
_INCOMPLETE_STATUSES = {
    EvidenceStatus.DETECTOR_UNAVAILABLE,
    EvidenceStatus.INFERENCE_FAILED,
    EvidenceStatus.COMPARISON_UNAVAILABLE,
}
_DETAILS = {
    "weights_missing": "The detector checkpoint was not found on this worker.",
    "weights_unusable": "The detector checkpoint could not be loaded.",
    "checkpoint_mismatch": "The detector checkpoint does not match the one frozen for this audit.",
    "inference_failed": "Person detection failed for this sample.",
    "annotations_unavailable": "The annotations could not be used, so detections were not compared with them.",
}

# Process-local reuse of one loaded detector. It is never pickled or shared, and a
# freshly spawned audit child starts empty, so each child pays its own setup cost.
_services: dict[tuple, Any] = {}


def clear_detector_cache() -> None:
    _services.clear()


def _service_for(configuration: DetectorConfiguration):
    from .detection_evidence import DetectionEvidenceService  # imports Torch; reached only in the child

    try:
        location = load_checkpoint_location()
    except DetectionConfigurationError:
        location = None
    path = _NOT_CONFIGURED if location is None else location
    key = (str(path), configuration.checkpoint_sha256, tuple(configuration.person_labels))
    service = _services.get(key)
    if service is None:
        if len(_services) >= 2:
            _services.clear()
        service = _services[key] = DetectionEvidenceService(
            person_labels=configuration.person_labels,
            checkpoint_path=path,
            expected_checkpoint_sha256=configuration.checkpoint_sha256,
        )
    return service


def _usable_annotations(dataset_root: str | Path, image_id: str) -> list[AnnotationObject] | None:
    """Return parsed annotations, or None when they cannot be compared with detector boxes.

    An empty list is a real sample with no objects. Boxes are only compared when
    the file declares the one-based origin the existing checks require.
    """
    try:
        text = load_annotation_text(dataset_root, image_id)
    except (OSError, UnicodeError):
        return None
    try:
        objects = parse_annotations(text)
    except ValueError:
        return None
    if parse_annotation_metadata(text)["coordinate_origin"] != (1, 1):
        return None
    return objects


def collect_detection_evidence(
    dataset_root: str | Path,
    image_id: str,
    configuration: DetectorConfiguration,
    *,
    service_for: Callable[[DetectorConfiguration], Any] = _service_for,
) -> DetectionEvidence | DetectionNotRun:
    """Run detection and matching for one materialized sample, as the configuration says.

    Masks are never read, so a missing mask cannot block image-to-box evidence.
    """
    if not configuration.enabled:
        return DetectionNotRun(status="disabled", configuration=configuration)
    try:
        image = load_image(dataset_root, image_id)
    except (OSError, ValueError, Image.DecompressionBombError):
        return DetectionNotRun(status="image_unavailable", configuration=configuration)
    return service_for(configuration).collect(
        image, _usable_annotations(dataset_root, image_id),
        min_confidence=configuration.min_confidence, min_iou=configuration.min_iou,
    )


def add_detection_evidence(
    measurements: Mapping[str, Any],
    dataset_root: str | Path,
    image_id: str,
    configuration: DetectorConfiguration,
    *,
    service_for: Callable[[DetectorConfiguration], Any] = _service_for,
) -> dict[str, Any]:
    """Return the measurements plus detection evidence, without changing the input.

    When enabled detection could not complete, the sample is marked incomplete
    with a detection error, so unrelated passing checks cannot read as a clean result.
    """
    evidence = collect_detection_evidence(dataset_root, image_id, configuration, service_for=service_for)
    report = dict(measurements)
    report[EVIDENCE_KEY] = evidence.model_dump(mode="json")
    if isinstance(evidence, DetectionEvidence) and evidence.status in _INCOMPLETE_STATUSES:
        code = evidence.reason.value
        report["errors"] = [
            *report.get("errors", []),
            {"stage": "detection", "reason": f"detection_{code}", "detail": _DETAILS[code]},
        ]
        report["status"] = "incomplete"
    return report


class DetectionAwareCollector:
    """A four-argument collector that adds detection evidence to the base collector's result.

    Holds only the frozen configuration and a module-level function, so spawned
    audit children can unpickle it. Existing callers and injected collectors that
    use the plain four-argument shape are unaffected.
    """

    def __init__(
        self,
        configuration: DetectorConfiguration,
        base_collector: Callable[..., Any] = audit_sample,
    ) -> None:
        if not isinstance(configuration, DetectorConfiguration):
            raise TypeError("configuration must be a DetectorConfiguration instance.")
        if not callable(base_collector):
            raise TypeError("base_collector must be callable.")
        self.configuration = configuration
        self.base_collector = base_collector

    def __call__(
        self, dataset_root: str | Path, image_id: str, allowed_labels: Any, connectivity: int = 8,
    ) -> dict[str, Any]:
        measurements = self.base_collector(dataset_root, image_id, allowed_labels, connectivity)
        return add_detection_evidence(measurements, dataset_root, image_id, self.configuration)
