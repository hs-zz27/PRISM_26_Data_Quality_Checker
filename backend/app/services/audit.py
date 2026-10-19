"""Collect independent sample checks and retain unavailable inputs for review."""

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from pathlib import Path
import logging
from typing import Any, TypeVar

import numpy as np

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
    ASSET_LOAD_FAILURE_DETAIL,
    AuditMeasurements,
    CheckEnvelope,
    MeasurementResult,
    ProcessingError,
)


T = TypeVar("T")
BOX_MASK_CHECKS = ("box_mask_alignment", "mask_outside_box", "mask_box_fill")
DEPENDENT_CHECK_REASON = "dependent_check_execution_failed"


logger = logging.getLogger(__name__)


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
    except (OSError, UnicodeError):
        logger.warning(
            "Sample asset could not be loaded",
            extra={"sample_id": image_id, "asset": asset},
            exc_info=True,
        )
        errors.append({
            "stage": "load", "reason": f"{asset}_load_failed",
            "detail": ASSET_LOAD_FAILURE_DETAIL,
        })
        return None


def _run_attributed_check(
    check_name: str,
    sample_id: str,
    errors: list[ProcessingError],
    operation: Callable[..., T],
    *arguments: Any,
    **keyword_arguments: Any,
) -> T | None:
    """Isolate ordinary check defects while resource/infrastructure errors escape.

    Small inputs are private copies. Masks have immutable bytes backing and can
    be shared without copying their pixels for every object/check. Snapshot
    failures occur outside the recoverable boundary.
    """
    def snapshot(value: Any) -> Any:
        return value if isinstance(value, np.ndarray) else deepcopy(value)

    arguments = tuple(snapshot(value) for value in arguments)
    keyword_arguments = {name: snapshot(value) for name, value in keyword_arguments.items()}
    try:
        return operation(*arguments, **keyword_arguments)
    except (ArithmeticError, LookupError, TypeError, ValueError, AttributeError, AssertionError):
        logger.exception(
            "Sample check execution failed",
            extra={"sample_id": sample_id, "check_name": check_name},
        )
        errors.append({
            "stage": "check",
            "reason": "check_execution_failed",
            "detail": "The check could not complete.",
        })
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
        else:
            # Immutable backing also prevents a check from simply enabling
            # NumPy's writeable flag and corrupting later measurements.
            mask = np.frombuffer(mask.tobytes(), dtype=mask.dtype).reshape(mask.shape)

    dimensions = None
    dimensions_check_failed = False
    if mask_reason is not None:
        checks["mask_dimensions"] = _unavailable(mask_reason)
    elif image is None:
        checks["mask_dimensions"] = _unavailable("image_load_failed")
    else:
        dimensions = _run_attributed_check(
            "mask_dimensions", image_id, errors,
            check_mask_dimensions, image, mask,
        )
        if dimensions is None:
            dimensions_check_failed = True
            checks["mask_dimensions"] = _unavailable("check_execution_failed")
        else:
            checks["mask_dimensions"] = _completed(dimensions)

    objects: list[AnnotationObject] | None = None
    annotation_reason = "annotation_load_failed" if annotation_text is None else None
    origin_reason: str | None = "coordinate_origin_unavailable"
    metadata_check_failed = False
    if annotation_text is not None:
        try:
            objects = parse_annotations(annotation_text)
        except ValueError as error:
            annotation_reason = "annotation_parse_failed"
            errors.append({"stage": "parse", "reason": annotation_reason, "detail": str(error)})
        metadata = parse_annotation_metadata(annotation_text)
        actual_size = (*image.size, len(image.getbands())) if image is not None else None
        metadata_result = _run_attributed_check(
            "annotation_metadata", image_id, errors,
            check_annotation_metadata,
            metadata, actual_size, len(objects) if objects is not None else None,
        )
        if metadata_result is None:
            metadata_check_failed = True
            origin_reason = DEPENDENT_CHECK_REASON
            checks["annotation_metadata"] = _unavailable("check_execution_failed")
        else:
            checks["annotation_metadata"] = _measured(metadata_result)
        if not metadata_check_failed:
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
    labels_check_failed = False
    if label_configuration is not None:
        labels = _run_attributed_check(
            "allowed_labels", image_id, errors,
            check_allowed_labels, objects, label_configuration,
        )
        if labels is None:
            labels_check_failed = True
            checks["labels"] = _unavailable("check_execution_failed")
        else:
            checks["labels"] = _completed(labels)
    counts = Counter(obj["object_id"] for obj in objects)
    duplicate_ids = {object_id for object_id, count in counts.items() if count > 1}
    invalid_ids = {object_id for object_id in counts if object_id <= 0}
    checks["annotation_ids"] = _completed({
        "valid": not duplicate_ids and not invalid_ids,
        "duplicate_ids": sorted(duplicate_ids),
        "invalid_ids": sorted(invalid_ids),
    })
    mask_ids_check_failed = False
    if mask_reason is None:
        ids = _run_attributed_check(
            "mask_ids", image_id, errors,
            check_mask_ids, objects, mask,
        )
        if ids is None:
            mask_ids_check_failed = True
            checks["mask_ids"] = _unavailable("check_execution_failed")
        else:
            checks["mask_ids"] = _completed({
                name: sorted(value) if isinstance(value, set) else value
                for name, value in ids.items()
            })
    else:
        checks["mask_ids"] = _unavailable(mask_reason)

    geometry_reason = origin_reason or ("image_load_failed" if image is None else None)
    if geometry_reason is None:
        pairs = _run_attributed_check(
            "box_overlaps", image_id, errors,
            measure_pairwise_box_iou, objects, image.size,
        )
        if pairs is None:
            checks["box_overlaps"] = _unavailable("check_execution_failed")
        else:
            checks["box_overlaps"] = _completed({"pairs": pairs})
    else:
        checks["box_overlaps"] = _unavailable(geometry_reason)

    for index, obj in enumerate(objects):
        object_checks: dict[str, CheckEnvelope] = {}
        if label_configuration is not None and labels_check_failed:
            object_checks["label"] = _unavailable("check_execution_failed")
        elif labels is not None:
            object_checks["label"] = _completed(labels["object_results"][index])
        box = None
        box_check_failed = False
        if geometry_reason is None:
            box = _run_attributed_check(
                "box_validity", image_id, errors,
                check_box_validity, obj["bbox"], image.size,
            )
            if box is None:
                box_check_failed = True
                object_checks["box_validity"] = _unavailable("check_execution_failed")
            else:
                object_checks["box_validity"] = _completed(box)
        else:
            object_checks["box_validity"] = _unavailable(geometry_reason)

        instance_reason = mask_reason
        if obj["object_id"] in invalid_ids:
            instance_reason = "invalid_object_id"
        elif obj["object_id"] in duplicate_ids:
            instance_reason = "duplicate_object_id"
        elif mask_ids_check_failed:
            instance_reason = DEPENDENT_CHECK_REASON

        comparison_reason = instance_reason or geometry_reason
        if comparison_reason is None and dimensions_check_failed:
            comparison_reason = DEPENDENT_CHECK_REASON
        if comparison_reason is None and dimensions is not None and not dimensions["matches"]:
            comparison_reason = "dimensions_mismatch"
        if comparison_reason is None and box_check_failed:
            comparison_reason = DEPENDENT_CHECK_REASON
        if comparison_reason is None and box is not None and not box["valid"]:
            comparison_reason = box["reason"]

        if instance_reason is None:
            measurements = _run_attributed_check(
                "mask_instance_measurements", image_id, errors,
                measure_instance_mask, obj, mask, connectivity,
                include_box_measurements=comparison_reason is None,
            )
            if measurements is None:
                for name in ("mask_components", *BOX_MASK_CHECKS):
                    object_checks[name] = _unavailable("check_execution_failed")
            else:
                object_checks.update({name: _measured(value) for name, value in measurements.items()})
        else:
            object_checks["mask_components"] = _unavailable(instance_reason)
        if comparison_reason is not None:
            for name in BOX_MASK_CHECKS:
                object_checks[name] = _unavailable(comparison_reason)
        report["objects"].append({"annotation": obj, "checks": object_checks})

    report["status"] = "incomplete" if errors else "completed"
    return report
