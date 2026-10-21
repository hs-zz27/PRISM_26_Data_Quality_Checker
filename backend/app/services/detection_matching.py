"""Compare person detections with annotation boxes and report overlap evidence.

Matching is deliberately conservative. Every pair whose IoU reaches the minimum
is an edge in a detection-annotation graph. A connected component with exactly
one detection and one annotation is a match; any larger component is reported
as ambiguous and left for review. No assignment, greedy tie-break or confidence
ranking is applied, and detections are not de-duplicated.

Cost: each considered detection is compared with each valid annotation, so the
work grows as O(detections * annotations), which is small for one image.
Limitation: in crowded scenes, overlapping people form larger components, so
more groups are reported as ambiguous and need human review.

This module produces evidence only: no findings, verdicts or corrections.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from math import isfinite
from numbers import Integral, Real
from typing import Any

from ..checks.geometry import check_box_validity
from ..models.annotation import AnnotationObject, ImageSize
from ..models.detection import PersonDetection
from ..models.detection_matching import (
    AmbiguousGroup,
    AnnotationRef,
    DetectionMatchingResult,
    DetectionRef,
    InvalidRecord,
    PairEvidence,
)

ContinuousBox = tuple[float, float, float, float]


class _Invalid(Exception):
    """Internal signal that one input record cannot be compared."""

    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def match_detections_to_annotations(
    detections: Sequence[PersonDetection | Mapping[str, Any]],
    annotations: Sequence[AnnotationObject],
    image_size: ImageSize,
    *,
    min_confidence: float,
    min_iou: float,
) -> DetectionMatchingResult:
    """Match detections to annotations by IoU and return evidence for review.

    `detections` holds `PersonDetection` objects or mappings with `bbox` and
    `score`. Boxes are continuous, zero-based [xmin, ymin, xmax, ymax] in the
    original image. `annotations` use the project's `AnnotationObject` shape
    with one-based inclusive integer boxes. `image_size` is (width, height).

    Annotation boxes become [xmin - 1, ymin - 1, xmax, ymax], so [1, 1, 1, 1]
    is [0, 0, 1, 1]. IoU uses width = xmax - xmin with no +1 and no rounding;
    boxes that only touch along an edge have IoU 0. Both thresholds are
    required: `min_confidence` is in [0, 1] and `min_iou` in (0, 1]. A detection
    exactly at `min_confidence` is considered, and a pair exactly at `min_iou`
    is eligible.

    Detections below `min_confidence` are reported separately and are not
    compared. A malformed record, such as a bad box, score, object ID or label,
    is reported in `invalid_records` with a reason, never repaired or clipped.
    If it has several problems, only the first is reported (box before score).
    Any invalid record makes `comparison_complete` False.

    Invalid arguments or thresholds raise ValueError, and a non-sequence record
    list raises TypeError.
    """
    width, height = _read_image_size(image_size)
    confidence_floor = _finite_number(min_confidence)
    if confidence_floor is None or not 0 <= confidence_floor <= 1:
        raise ValueError("min_confidence must be a finite number between 0 and 1")
    iou_floor = _finite_number(min_iou)
    if iou_floor is None or not 0 < iou_floor <= 1:
        raise ValueError("min_iou must be a finite number greater than 0 and at most 1")
    _require_record_sequence(detections, "detections")
    _require_record_sequence(annotations, "annotations")

    invalid_detections: list[InvalidRecord] = []
    invalid_annotations: list[InvalidRecord] = []
    filtered: list[DetectionRef] = []
    considered: dict[int, tuple[ContinuousBox, float]] = {}
    for index, record in enumerate(detections):
        try:
            box, score = _read_detection(record, width, height)
        except _Invalid as problem:
            invalid_detections.append(InvalidRecord(
                record_type="detection", index=index, reason=problem.reason, detail=problem.detail,
            ))
            continue
        if score < confidence_floor:
            filtered.append(DetectionRef(index=index, score=score))
        else:
            considered[index] = (box, score)

    valid_annotations: dict[int, tuple[int, str, ContinuousBox]] = {}
    for index, record in enumerate(annotations):
        try:
            valid_annotations[index] = _read_annotation(record, (width, height))
        except _Invalid as problem:
            invalid_annotations.append(InvalidRecord(
                record_type="annotation", index=index, object_id=_readable_object_id(record),
                reason=problem.reason, detail=problem.detail,
            ))

    def detection_ref(index: int) -> DetectionRef:
        return DetectionRef(index=index, score=considered[index][1])

    def annotation_ref(index: int) -> AnnotationRef:
        object_id, label, _ = valid_annotations[index]
        return AnnotationRef(index=index, object_id=object_id, label=label)

    # Edges come out sorted by (detection index, annotation index).
    edges: list[tuple[int, int, float]] = []
    for detection_index, (detection_box, _) in considered.items():
        for annotation_index, (_, _, annotation_box) in valid_annotations.items():
            iou = _continuous_iou(detection_box, annotation_box)
            if iou >= iou_floor:
                edges.append((detection_index, annotation_index, iou))

    matches: list[PairEvidence] = []
    ambiguous: list[AmbiguousGroup] = []
    for group in _connected_groups(edges):
        detection_indexes = sorted({edge[0] for edge in group})
        annotation_indexes = sorted({edge[1] for edge in group})
        pairs = [
            PairEvidence(detection=detection_ref(d), annotation=annotation_ref(a), iou=iou)
            for d, a, iou in group
        ]
        if len(detection_indexes) == 1 and len(annotation_indexes) == 1:
            matches.append(pairs[0])
        else:
            ambiguous.append(AmbiguousGroup(
                detections=[detection_ref(d) for d in detection_indexes],
                annotations=[annotation_ref(a) for a in annotation_indexes],
                pairs=pairs,
            ))

    linked_detections = {edge[0] for edge in edges}
    linked_annotations = {edge[1] for edge in edges}
    invalid_records = invalid_detections + invalid_annotations
    return DetectionMatchingResult(
        image_width=width, image_height=height,
        min_confidence=confidence_floor, min_iou=iou_floor,
        matches=matches,
        ambiguous_groups=ambiguous,
        unmatched_detections=[detection_ref(i) for i in considered if i not in linked_detections],
        unmatched_annotations=[annotation_ref(i) for i in valid_annotations if i not in linked_annotations],
        confidence_filtered_detections=filtered,
        invalid_records=invalid_records,
        comparison_complete=not invalid_records,
    )


def _finite_number(value: object) -> float | None:
    """Return a plain float for a finite real number, or None. Booleans are not numbers here."""
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if isfinite(number) else None


def _read_image_size(image_size: object) -> tuple[int, int]:
    if isinstance(image_size, (str, bytes)) or not isinstance(image_size, Sequence) or len(image_size) != 2:
        raise ValueError("image_size must be a (width, height) pair")
    if any(isinstance(side, bool) or not isinstance(side, Integral) or side <= 0 for side in image_size):
        raise ValueError("image width and height must be positive integers")
    return int(image_size[0]), int(image_size[1])


def _require_record_sequence(records: object, name: str) -> None:
    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
        raise TypeError(f"{name} must be a sequence of records")


def _read_detection(record: object, width: int, height: int) -> tuple[ContinuousBox, float]:
    if isinstance(record, PersonDetection):
        raw_box = (record.x_min, record.y_min, record.x_max, record.y_max)
        raw_score = record.score
    elif isinstance(record, Mapping):
        raw_box = record.get("bbox")
        raw_score = record.get("score")
    else:
        raise _Invalid("malformed_record", "Expected a PersonDetection or a mapping with 'bbox' and 'score'.")

    box = _read_detection_box(raw_box, width, height)
    score = _finite_number(raw_score)
    if score is None or not 0 <= score <= 1:
        raise _Invalid("invalid_score", "Confidence must be a finite number between 0 and 1.")
    return box, score


def _read_detection_box(raw: object, width: int, height: int) -> ContinuousBox:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or len(raw) != 4:
        raise _Invalid("invalid_box", "Expected four coordinates [xmin, ymin, xmax, ymax].")
    values = [_finite_number(value) for value in raw]
    if any(value is None for value in values):
        raise _Invalid("invalid_box", "Coordinates must be finite numbers.")
    x_min, y_min, x_max, y_max = values
    if x_min > x_max or y_min > y_max:
        raise _Invalid("invalid_box", "Minimum coordinates exceed maximum coordinates.")
    if (x_max - x_min) * (y_max - y_min) == 0:
        raise _Invalid("zero_area_box", "Box has zero area.")
    if x_min < 0 or y_min < 0 or x_max > width or y_max > height:
        raise _Invalid("box_outside_image", "Box extends beyond the image bounds.")
    return x_min, y_min, x_max, y_max


def _read_annotation(record: object, image_size: ImageSize) -> tuple[int, str, ContinuousBox]:
    if not isinstance(record, Mapping):
        raise _Invalid("malformed_record", "Expected a mapping with 'object_id', 'label' and 'bbox'.")
    object_id = _readable_object_id(record)
    if object_id is None:
        raise _Invalid("invalid_object_id", "object_id must be an integer.")
    label = record.get("label")
    if not isinstance(label, str):
        raise _Invalid("invalid_label", "label must be a string.")

    validity = check_box_validity(record.get("bbox"), image_size)
    if not validity["valid"]:
        raise _Invalid(validity["reason"], validity["detail"])
    xmin, ymin, xmax, ymax = validity["bbox"]
    # One-based inclusive pixels to zero-based pixel edges.
    return object_id, label, (float(xmin - 1), float(ymin - 1), float(xmax), float(ymax))


def _readable_object_id(record: object) -> int | None:
    if not isinstance(record, Mapping):
        return None
    object_id = record.get("object_id")
    if isinstance(object_id, bool) or not isinstance(object_id, Integral):
        return None
    return int(object_id)


def _continuous_iou(first: ContinuousBox, second: ContinuousBox) -> float:
    width = min(first[2], second[2]) - max(first[0], second[0])
    height = min(first[3], second[3]) - max(first[1], second[1])
    if width <= 0 or height <= 0:
        return 0.0
    intersection = width * height
    union = _area(first) + _area(second) - intersection
    # Rounding can push a near-identical pair a hair above 1.
    return min(1.0, intersection / union)


def _area(box: ContinuousBox) -> float:
    return (box[2] - box[0]) * (box[3] - box[1])


def _connected_groups(edges: list[tuple[int, int, float]]) -> list[list[tuple[int, int, float]]]:
    """Group edges by connected component; groups follow their lowest detection index."""
    parent: dict[tuple[str, int], tuple[str, int]] = {}

    def find(node: tuple[str, int]) -> tuple[str, int]:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for detection_index, annotation_index, _ in edges:
        detection_root = find(("detection", detection_index))
        annotation_root = find(("annotation", annotation_index))
        if detection_root != annotation_root:
            parent[annotation_root] = detection_root

    groups: dict[tuple[str, int], list[tuple[int, int, float]]] = defaultdict(list)
    for edge in edges:
        groups[find(("detection", edge[0]))].append(edge)
    return list(groups.values())