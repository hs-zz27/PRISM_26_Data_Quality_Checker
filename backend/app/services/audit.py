"""Coordinate dataset loading and checks; leave final decisions pending review."""

from collections import Counter

import numpy as np

from ..checks.geometry import check_box_validity, measure_pairwise_box_iou
from ..checks.labels import check_allowed_labels
from ..checks.segmentation import (
    check_mask_dimensions,
    check_mask_ids,
    measure_box_mask_alignment,
    measure_mask_box_fill,
    measure_mask_components,
    measure_mask_outside_box,
)
from ..datasets.penn_fudan import load_sample, parse_annotations


def _unavailable(reason, detail=None):
    """Describe a check that could not produce usable evidence."""
    return {"status": "unavailable", "reason": reason, "detail": detail, "result": None}


def _checked(result):
    """Keep a check's result, including evidence for unavailable measurements."""
    available = result.get("measurement_available", result.get("comparison_available", True))
    return {
        "status": "completed" if available else "unavailable",
        "reason": None if available else result["reason"],
        "detail": None,
        "result": result,
    }


def audit_sample(dataset_root, image_id, allowed_labels, connectivity=8):
    """Collect existing checks for one Penn-Fudan sample without final decisions.

    Allowed labels come from the caller. Bad caller configuration raises
    ValueError; sample loading/parsing errors are retained in the report.
    A completed status means processing finished, not that the data passed.
    Confirmed-rule evaluation, annotation metadata verification, model
    inference and persistence are separate later steps.
    """
    # Validate configuration before reading data, including one-shot iterables.
    allowed_labels = check_allowed_labels([], allowed_labels)["allowed_labels"]
    if not isinstance(connectivity, (int, np.integer)) or connectivity not in (4, 8):
        raise ValueError("Connectivity must be the integer 4 or 8.")

    check_names = ("mask_dimensions", "mask_ids", "annotation_ids", "labels", "box_overlaps")
    report = {
        "image_id": image_id,
        "image_size": None,
        "annotation_text": None,
        "allowed_labels": allowed_labels,
        "status": "incomplete",
        "review_status": "pending_review",
        "errors": [],
        "checks": {name: _unavailable("sample_not_loaded") for name in check_names},
        "objects": [],
    }

    try:
        image, annotation_text, mask = load_sample(dataset_root, image_id)
    except (OSError, UnicodeError) as error:
        report["errors"].append({"stage": "load", "reason": "sample_load_failed", "detail": str(error)})
        report["checks"] = {name: _unavailable("sample_load_failed") for name in check_names}
        return report

    report["image_size"] = image.size
    report["annotation_text"] = annotation_text
    try:
        objects = parse_annotations(annotation_text)
    except ValueError as error:
        report["errors"].append({"stage": "parse", "reason": "annotation_parse_failed", "detail": str(error)})
        report["checks"] = {name: _unavailable("annotation_parse_failed") for name in check_names}
        return report

    checks = report["checks"]
    labels = check_allowed_labels(objects, allowed_labels)
    checks["labels"] = _checked(labels)
    counts = Counter(obj["object_id"] for obj in objects)
    duplicate_ids = sorted(object_id for object_id, count in counts.items() if count > 1)
    invalid_ids = sorted(object_id for object_id in counts if object_id <= 0)
    checks["annotation_ids"] = _checked({
        "valid": not duplicate_ids and not invalid_ids,
        "duplicate_ids": duplicate_ids,
        "invalid_ids": invalid_ids,
    })
    checks["box_overlaps"] = _checked({
        "pairs": measure_pairwise_box_iou(objects, image.size),
    })

    mask_block_reason = None
    try:
        dimensions = check_mask_dimensions(image, mask)
        checks["mask_dimensions"] = _checked(dimensions)
        id_result = check_mask_ids(objects, mask)
        # The low-level ID check uses sets; reports use lists for JSON storage.
        checks["mask_ids"] = _checked({
            name: sorted(value) if isinstance(value, set) else value
            for name, value in id_result.items()
        })
    except ValueError as error:
        mask_block_reason = "invalid_mask"
        checks["mask_dimensions"] = _unavailable(mask_block_reason, str(error))
        checks["mask_ids"] = _unavailable(mask_block_reason, str(error))
        report["errors"].append({"stage": "mask", "reason": mask_block_reason, "detail": str(error)})

    for obj, label_result in zip(objects, labels["object_results"]):
        box_result = check_box_validity(obj["bbox"], image.size)
        object_checks = {
            "label": _checked(label_result),
            "box_validity": _checked(box_result),
        }
        # These measurements need an unambiguous positive ID and a usable mask.
        object_block_reason = mask_block_reason
        if obj["object_id"] in invalid_ids:
            object_block_reason = "invalid_object_id"
        elif obj["object_id"] in duplicate_ids:
            object_block_reason = "duplicate_object_id"

        if object_block_reason is None:
            object_checks["mask_components"] = _checked(
                measure_mask_components(mask, obj["object_id"], connectivity)
            )
        else:
            object_checks["mask_components"] = _unavailable(object_block_reason)

        comparison_block_reason = object_block_reason
        if comparison_block_reason is None and not dimensions["matches"]:
            comparison_block_reason = "dimensions_mismatch"
        if comparison_block_reason is None and not box_result["valid"]:
            comparison_block_reason = box_result["reason"]

        measurements = (
            ("box_mask_alignment", measure_box_mask_alignment),
            ("mask_outside_box", measure_mask_outside_box),
            ("mask_box_fill", measure_mask_box_fill),
        )
        for name, measure in measurements:
            if comparison_block_reason is None:
                object_checks[name] = _checked(measure(obj, mask))
            else:
                object_checks[name] = _unavailable(comparison_block_reason)

        report["objects"].append({"annotation": obj, "checks": object_checks})

    report["status"] = "completed"
    return report
