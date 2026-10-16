"""Collect independent sample checks and retain unavailable inputs for review."""

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, TypeVar

from ..checks.geometry import check_box_validity, measure_pairwise_box_iou
from ..checks.labels import check_allowed_labels
from ..checks.metadata import check_annotation_metadata
from ..checks.segmentation import (
    check_mask_dimensions,
    check_mask_ids,
    measure_instance_mask,
    validate_connectivity,
    validate_instance_mask,
)
from ..datasets.penn_fudan import (
    load_annotation_text,
    load_image,
    load_mask,
    parse_annotation_metadata,
    parse_annotations,
)
from ..models.annotation import AnnotationObject
from ..models.measurements import (
    AuditMeasurements,
    CheckEnvelope,
    MeasurementResult,
    ProcessingError,
)


T = TypeVar("T")
BOX_MASK_CHECKS = ("box_mask_alignment", "mask_outside_box", "mask_box_fill")


def _unavailable(reason: str, detail: str | None = None) -> CheckEnvelope:
    return {"status": "unavailable", "reason": reason, "detail": detail, "result": None}


def _completed(result: Mapping[str, Any]) -> CheckEnvelope:
    """Processing completed; the result may describe invalid source data."""
    return {"status": "completed", "reason": None, "detail": None, "result": dict(result)}


def _measured(result: MeasurementResult) -> CheckEnvelope:
    """All measurement functions explicitly declare availability and its reason."""
    return {
        "status": "completed" if result["measurement_available"] else "unavailable",
        "reason": result["reason"],
        "detail": None,
        "result": dict(result),
    }


def _load_asset(
    loader: Callable[[str | Path, str], T],
    dataset_root: str | Path,
    image_id: str,
    asset: str,
    errors: list[ProcessingError],
) -> T | None:
    try:
        return loader(dataset_root, image_id)
    except (OSError, UnicodeError) as error:
        errors.append({"stage": "load", "reason": f"{asset}_load_failed", "detail": str(error)})
        return None


def audit_sample(
    dataset_root: str | Path, image_id: str, allowed_labels: Iterable[str] | None, connectivity: int = 8,
) -> AuditMeasurements:
    """Collect measurements without rule evaluation or final reviewer decisions.

    Load each asset independently. Only checks needing a failed input become
    unavailable. Metadata failures remain visible and unusable coordinate origins
    block box comparisons. A completed report means processing finished, not that
    the sample passed. Failed asset loading/parsing leaves the report incomplete.
    ``allowed_labels=None`` skips label measurements for rule-independent batch
    collection; existing callers may continue supplying a label collection.
    """
    label_configuration = None if allowed_labels is None else check_allowed_labels(
        [], allowed_labels,
    )["allowed_labels"]
    validate_connectivity(connectivity)
    errors: list[ProcessingError] = []
    image = _load_asset(load_image, dataset_root, image_id, "image", errors)
    annotation_text = _load_asset(load_annotation_text, dataset_root, image_id, "annotation", errors)
    mask = _load_asset(load_mask, dataset_root, image_id, "mask", errors)
    checks: dict[str, CheckEnvelope] = {}
    report: AuditMeasurements = {
        "image_id": image_id,
        "image_size": image.size if image is not None else None,
        "annotation_text": annotation_text,
        "allowed_labels": [] if label_configuration is None else label_configuration,
        "status": "incomplete",
        "review_status": "pending_review",
        "errors": errors,
        "checks": checks,
        "objects": [],
    }

    mask_reason = "mask_load_failed" if mask is None else None
    if mask is not None:
        try:
            validate_instance_mask(mask)
        except ValueError as error:
            mask_reason = "invalid_mask"
            errors.append({"stage": "mask", "reason": mask_reason, "detail": str(error)})

    dimensions = None
    if mask_reason is not None:
        checks["mask_dimensions"] = _unavailable(mask_reason)
    elif image is None:
        checks["mask_dimensions"] = _unavailable("image_load_failed")
    else:
        dimensions = check_mask_dimensions(image, mask)
        checks["mask_dimensions"] = _completed(dimensions)

    objects: list[AnnotationObject] | None = None
    annotation_reason = "annotation_load_failed" if annotation_text is None else None
    origin_reason: str | None = "coordinate_origin_unavailable"
    if annotation_text is not None:
        try:
            objects = parse_annotations(annotation_text)
        except ValueError as error:
            annotation_reason = "annotation_parse_failed"
            errors.append({"stage": "parse", "reason": annotation_reason, "detail": str(error)})
        metadata = parse_annotation_metadata(annotation_text)
        actual_size = (*image.size, len(image.getbands())) if image is not None else None
        checks["annotation_metadata"] = _measured(check_annotation_metadata(
            metadata, actual_size, len(objects) if objects is not None else None,
        ))
        if metadata["coordinate_origin"] == (1, 1):
            origin_reason = None
        elif metadata["coordinate_origin"] is not None:
            origin_reason = "unsupported_coordinate_origin"
    else:
        checks["annotation_metadata"] = _unavailable(annotation_reason)

    if objects is None:
        names = ("annotation_ids", "box_overlaps", "mask_ids")
        if label_configuration is not None:
            names = (*names, "labels")
        for name in names:
            checks[name] = _unavailable(annotation_reason)
        return report

    labels = None
    if label_configuration is not None:
        labels = check_allowed_labels(objects, label_configuration)
        checks["labels"] = _completed(labels)
    counts = Counter(obj["object_id"] for obj in objects)
    duplicate_ids = {object_id for object_id, count in counts.items() if count > 1}
    invalid_ids = {object_id for object_id in counts if object_id <= 0}
    checks["annotation_ids"] = _completed({
        "valid": not duplicate_ids and not invalid_ids,
        "duplicate_ids": sorted(duplicate_ids),
        "invalid_ids": sorted(invalid_ids),
    })
    if mask_reason is None:
        ids = check_mask_ids(objects, mask)
        checks["mask_ids"] = _completed({
            name: sorted(value) if isinstance(value, set) else value for name, value in ids.items()
        })
    else:
        checks["mask_ids"] = _unavailable(mask_reason)

    geometry_reason = origin_reason or ("image_load_failed" if image is None else None)
    if geometry_reason is None:
        checks["box_overlaps"] = _completed({"pairs": measure_pairwise_box_iou(objects, image.size)})
    else:
        checks["box_overlaps"] = _unavailable(geometry_reason)

    for index, obj in enumerate(objects):
        object_checks: dict[str, CheckEnvelope] = {}
        if labels is not None:
            object_checks["label"] = _completed(labels["object_results"][index])
        box = None
        if geometry_reason is None:
            box = check_box_validity(obj["bbox"], image.size)
            object_checks["box_validity"] = _completed(box)
        else:
            object_checks["box_validity"] = _unavailable(geometry_reason)

        instance_reason = mask_reason
        if obj["object_id"] in invalid_ids:
            instance_reason = "invalid_object_id"
        elif obj["object_id"] in duplicate_ids:
            instance_reason = "duplicate_object_id"

        comparison_reason = instance_reason or geometry_reason
        if comparison_reason is None and not dimensions["matches"]:
            comparison_reason = "dimensions_mismatch"
        if comparison_reason is None and not box["valid"]:
            comparison_reason = box["reason"]

        if instance_reason is None:
            measurements = measure_instance_mask(
                obj, mask, connectivity, include_box_measurements=comparison_reason is None,
            )
            object_checks.update({name: _measured(value) for name, value in measurements.items()})
        else:
            object_checks["mask_components"] = _unavailable(instance_reason)
        if comparison_reason is not None:
            for name in BOX_MASK_CHECKS:
                object_checks[name] = _unavailable(comparison_reason)
        report["objects"].append({"annotation": obj, "checks": object_checks})

    report["status"] = "incomplete" if errors else "completed"
    return report
