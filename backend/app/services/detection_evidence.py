"""Run the CPU person detector on one image and compare it with that image's annotations.

The service reuses the merged detector and matcher and returns typed evidence
only: no findings, verdicts or reviewer decisions. Constructing the service is
the explicit setup step. Importing this module loads no model, reads no
checkpoint and runs no inference.

Threshold semantics: the detector is asked for every person candidate it
produces (`DETECTOR_INFERENCE_CUTOFF`, 0.0) and the matcher applies the caller's
confidence threshold, so low-scoring candidates are reported as filtered rather
than silently dropped. Candidates torchvision discards internally were never
examined.

Model reuse holds within one service instance. Separate spawned audit processes
each load their own copy.
"""

import hashlib
import logging
import re
from collections.abc import Collection, Mapping, Sequence
from math import isfinite
from numbers import Integral, Real
from pathlib import Path
from typing import Protocol

from PIL import Image

from ..inference.person_detector import (
    MODEL_NAME,
    InferenceError,
    PersonDetector,
    WeightsLoadError,
)
from ..models.annotation import AnnotationObject
from ..models.detection import DetectionResult
from ..models.detection_evidence import (
    DetectionEvidence,
    EvidenceStatus,
    ExcludedAnnotation,
    ModelIdentity,
    UnavailableReason,
)
from ..models.detection_matching import DetectionMatchingResult
from .detection_matching import match_detections_to_annotations

logger = logging.getLogger(__name__)

DETECTOR_INFERENCE_CUTOFF = 0.0


class DetectorLike(Protocol):
    """The one method the service needs from a detector."""

    def detect(self, image: Image.Image, min_score: float) -> DetectionResult: ...


def file_sha256(path: Path) -> str | None:
    """Return the SHA-256 of a file, or None when it cannot be read."""
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


class DetectionEvidenceService:
    """Collects detection evidence for one image at a time and reuses one detector.

    Pass exactly one of `detector` (any object with `detect`, used by tests) or
    `checkpoint_path` (an existing local checkpoint; nothing is downloaded).
    `person_labels` names the annotation labels that mean "person"; labels are
    matched exactly, and other labels are reported as excluded, never compared.
    `expected_checkpoint_sha256` makes the service refuse any other checkpoint: a
    file whose hash differs is never loaded and the evidence reports
    `checkpoint_mismatch`.

    A missing or unusable checkpoint does not raise here. It is reported as
    `detector_unavailable` evidence on every call, and the original exception is
    kept on `setup_failure` for debugging.
    """

    def __init__(
        self,
        *,
        person_labels: Collection[str],
        detector: DetectorLike | None = None,
        checkpoint_path: str | Path | None = None,
        expected_checkpoint_sha256: str | None = None,
    ):
        self._person_labels = _checked_labels(person_labels)
        if (detector is None) == (checkpoint_path is None):
            raise ValueError("Provide exactly one of detector or checkpoint_path.")
        if expected_checkpoint_sha256 is not None and checkpoint_path is None:
            raise ValueError("expected_checkpoint_sha256 applies only with checkpoint_path.")
        if expected_checkpoint_sha256 is not None and not (
            isinstance(expected_checkpoint_sha256, str)
            and re.fullmatch(r"[0-9a-f]{64}", expected_checkpoint_sha256)
        ):
            raise ValueError("expected_checkpoint_sha256 must be 64 lowercase hexadecimal characters.")
        self._expected_checkpoint_sha256 = expected_checkpoint_sha256
        self._detector = detector
        self._checkpoint_sha256: str | None = None
        self._setup_failure: WeightsLoadError | None = None
        self._setup_reason: UnavailableReason | None = None
        self._from_checkpoint = checkpoint_path is not None
        if checkpoint_path is not None:
            self._set_up_checkpoint(Path(checkpoint_path))

    @property
    def setup_failure(self) -> WeightsLoadError | None:
        """The internal checkpoint error, for debugging. It is never put into evidence."""
        return self._setup_failure

    def _set_up_checkpoint(self, path: Path) -> None:
        self._checkpoint_sha256 = file_sha256(path)
        expected = self._expected_checkpoint_sha256
        if expected is not None and self._checkpoint_sha256 not in (None, expected):
            # Never load weights other than the ones the caller asked for.
            self._setup_failure = WeightsLoadError("The checkpoint does not match the expected SHA-256.")
            self._setup_reason = UnavailableReason.CHECKPOINT_MISMATCH
            logger.warning("Person detector checkpoint does not match the expected SHA-256")
            return
        try:
            self._detector = PersonDetector(path)
        except WeightsLoadError as error:
            self._setup_failure = error
            self._setup_reason = (
                UnavailableReason.WEIGHTS_UNUSABLE if path.is_file() else UnavailableReason.WEIGHTS_MISSING
            )
            logger.warning("Person detector weights are unavailable (%s)", self._setup_reason.value, exc_info=error)
            return
        if file_sha256(path) != self._checkpoint_sha256:
            # The file changed while loading, so the hash may not describe the loaded weights.
            self._detector = None
            self._setup_failure = WeightsLoadError("The checkpoint changed while it was being loaded.")
            self._setup_reason = UnavailableReason.WEIGHTS_UNUSABLE
            logger.warning("Person detector checkpoint changed during setup")

    def collect(
        self,
        image: Image.Image,
        annotations: Sequence[AnnotationObject] | None,
        *,
        min_confidence: float,
        min_iou: float,
    ) -> DetectionEvidence:
        """Detect people in `image` and compare them with `annotations`.

        `annotations` uses the project's one-based, inclusive `AnnotationObject`
        shape. An empty list is a real sample with no objects. Pass None when the
        annotations could not be read: detection still runs, but no comparison is
        made and no detection is called unmatched.

        Both thresholds are required. Invalid thresholds or arguments raise
        ValueError or TypeError. Expected detector failures become evidence, and
        unexpected errors propagate.
        """
        confidence = _checked_threshold(min_confidence, "min_confidence", allow_zero=True)
        iou = _checked_threshold(min_iou, "min_iou", allow_zero=False)
        if not isinstance(image, Image.Image):
            raise TypeError("image must be a PIL image")
        if annotations is not None and (
            isinstance(annotations, (str, bytes)) or not isinstance(annotations, Sequence)
        ):
            raise TypeError("annotations must be a sequence of records, or None when unavailable")

        width, height = image.size
        records = [] if annotations is None else annotations
        forwarded, original_indexes, excluded = _scope_annotations(records, self._person_labels)
        shared = {
            "image_width": width,
            "image_height": height,
            "min_confidence": confidence,
            "min_iou": iou,
            "person_labels": list(self._person_labels),
            "annotations_available": annotations is not None,
            "annotation_count": None if annotations is None else len(annotations),
            "excluded_annotations": excluded,
        }

        if self._detector is None:
            return DetectionEvidence(
                status=EvidenceStatus.DETECTOR_UNAVAILABLE, reason=self._setup_reason,
                detector_identity=self._identity(None), **shared,
            )
        try:
            output = self._detector.detect(image, DETECTOR_INFERENCE_CUTOFF)
        except WeightsLoadError as error:
            logger.warning("Person detector weights became unavailable", exc_info=error)
            return DetectionEvidence(
                status=EvidenceStatus.DETECTOR_UNAVAILABLE, reason=UnavailableReason.WEIGHTS_UNUSABLE,
                detector_identity=self._identity(None), **shared,
            )
        except InferenceError as error:
            logger.warning("Person detection failed", exc_info=error)
            return DetectionEvidence(
                status=EvidenceStatus.INFERENCE_FAILED, reason=UnavailableReason.INFERENCE_FAILED,
                detector_identity=self._identity(None), **shared,
            )

        if annotations is None:
            return DetectionEvidence(
                status=EvidenceStatus.COMPARISON_UNAVAILABLE,
                reason=UnavailableReason.ANNOTATIONS_UNAVAILABLE,
                detector_identity=self._identity(output), detector_output=output, **shared,
            )
        matching = _with_original_indexes(
            match_detections_to_annotations(
                output.detections, forwarded, (width, height), min_confidence=confidence, min_iou=iou,
            ),
            original_indexes,
        )
        status = EvidenceStatus.COMPARED if matching.comparison_complete else EvidenceStatus.COMPARED_INCOMPLETE
        return DetectionEvidence(
            status=status, detector_identity=self._identity(output),
            detector_output=output, matching=matching, **shared,
        )

    def _identity(self, output: DetectionResult | None) -> ModelIdentity:
        if self._from_checkpoint:
            return ModelIdentity(
                architecture=MODEL_NAME, checkpoint_source="local_checkpoint",
                checkpoint_sha256=self._checkpoint_sha256,
                expected_checkpoint_sha256=self._expected_checkpoint_sha256,
            )
        return ModelIdentity(
            architecture=None if output is None else output.model_name,
            checkpoint_source="injected_detector",
        )


def _checked_labels(person_labels: object) -> tuple[str, ...]:
    if isinstance(person_labels, (str, bytes)) or not isinstance(person_labels, Collection):
        raise ValueError("person_labels must be a collection of annotation labels")
    labels = list(person_labels)
    if not labels or any(not isinstance(label, str) or not label for label in labels):
        raise ValueError("person_labels must name at least one non-empty annotation label")
    return tuple(sorted(set(labels)))


def _checked_threshold(value: object, name: str, *, allow_zero: bool) -> float:
    number = None
    if not isinstance(value, bool) and isinstance(value, Real):
        try:
            number = float(value)
        except OverflowError:
            number = None
    in_range = number is not None and isfinite(number) and number <= 1 and (number >= 0 if allow_zero else number > 0)
    if not in_range:
        bounds = "between 0 and 1" if allow_zero else "greater than 0 and at most 1"
        raise ValueError(f"{name} must be a finite number {bounds}")
    return number


def _scope_annotations(records: Sequence[object], person_labels: tuple[str, ...]):
    """Split records into those to compare and those outside the label scope.

    A record whose label cannot be read is passed on so the matcher reports it as
    invalid. Returns (records to match, their original indexes, excluded records).
    """
    forwarded: list[object] = []
    original_indexes: list[int] = []
    excluded: list[ExcludedAnnotation] = []
    for index, record in enumerate(records):
        label = record.get("label") if isinstance(record, Mapping) else None
        if isinstance(label, str) and label not in person_labels:
            excluded.append(ExcludedAnnotation(index=index, object_id=_readable_object_id(record), label=label))
        else:
            forwarded.append(record)
            original_indexes.append(index)
    return forwarded, original_indexes, excluded


def _readable_object_id(record: Mapping) -> int | None:
    object_id = record.get("object_id")
    if isinstance(object_id, bool) or not isinstance(object_id, Integral):
        return None
    return int(object_id)


def _with_original_indexes(result: DetectionMatchingResult, original: list[int]) -> DetectionMatchingResult:
    """Translate annotation references from filtered positions back to the caller's indexes."""

    def annotation(ref):
        return ref.model_copy(update={"index": original[ref.index]})

    def pair(item):
        return item.model_copy(update={"annotation": annotation(item.annotation)})

    return result.model_copy(update={
        "matches": [pair(item) for item in result.matches],
        "ambiguous_groups": [
            group.model_copy(update={
                "annotations": [annotation(ref) for ref in group.annotations],
                "pairs": [pair(item) for item in group.pairs],
            })
            for group in result.ambiguous_groups
        ],
        "unmatched_annotations": [annotation(ref) for ref in result.unmatched_annotations],
        "invalid_records": [
            record.model_copy(update={"index": original[record.index]})
            if record.record_type == "annotation" else record
            for record in result.invalid_records
        ],
    })
