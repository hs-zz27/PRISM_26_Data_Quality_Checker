"""Collect JSON-format measurements without inferring annotation policy."""
from dataclasses import asdict
import json

from ..datasets.json_sample import inspect_sample, display_objects
from ..models.annotation_validation import ValidationLimits


def completed(result):
    return {"status": "completed", "reason": None, "detail": None, "result": result}


def unavailable(reason):
    return {"status": "unavailable", "reason": reason, "detail": None, "result": None}


def audit_json_sample(manifest, asset_settings, allowed_labels=None):
    document, record, size, issues = inspect_sample(manifest, asset_settings, ValidationLimits(),
                                                   max_annotation_bytes=64 * 1024**2)
    objects = display_objects(record)
    checks = {name: unavailable("pixel_mask_not_supplied_in_supported_encoding")
              for name in ("mask_ids", "mask_dimensions")}
    metadata_issues = [issue for issue in issues if issue.code in {
        "invalid_dimensions", "dimension_mismatch", "unassigned_annotations", "duplicate_image_id",
        "invalid_image_id", "duplicate_image_reference", "missing_image_reference",
    }]
    checks["annotation_metadata"] = (completed({
        "matches": not metadata_issues, "declared": {"object_count": len(objects),
        "image_size": [record.width, record.height]}, "actual_object_count": len(objects),
        "actual_image_size": size, "issues": [asdict(issue) for issue in metadata_issues],
    }) if size else unavailable("image_evidence_unavailable"))
    # LabelMe shapes are indexed locally and group_id is not an object ID.
    if document.format == "coco":
        ids = [obj.source_id for obj in record.annotations]
        checks["annotation_ids"] = completed({"valid": all(type(i) is int and i > 0 for i in ids)
                                               and len(set(ids)) == len(ids)
                                               and not any(i.code in {"duplicate_annotation_id", "invalid_annotation_id"}
                                                           for obj in record.annotations for i in obj.issues), "source_ids": ids})
    else:
        checks["annotation_ids"] = unavailable("format_has_no_object_id_field")
    rows = []
    for obj, source in zip(objects, record.annotations):
        box = obj.bbox
        box_check = unavailable("supplied_box_unavailable")
        if source.bbox_origin == "supplied" and box is not None and size:
            x1, y1, x2, y2 = box
            box_check = completed({"valid": 0 <= x1 < x2 <= size[0] and 0 <= y1 < y2 <= size[1],
                                   "bbox": box, "coordinate_system": obj.coordinate_system})
        related = [issue for issue in issues if issue.location == source.key or issue.location.startswith(source.key + "/")]
        polygon_issues = [issue for issue in related if "polygon" in issue.code]
        polygon_check = unavailable("polygon_evidence_unavailable")
        if source.polygons and size:
            polygon_check = (unavailable("polygon_topology_limit") if any(i.severity == "unsupported" for i in polygon_issues)
                else completed({"valid": not polygon_issues, "issues": [asdict(i) for i in polygon_issues]}))
        per_object = {name: unavailable("pixel_mask_not_supplied_in_supported_encoding") for name in
                      ("box_mask_alignment", "mask_outside_box", "mask_box_fill", "mask_components")}
        per_object.update(box_validity=box_check, polygon_validity=polygon_check)
        rows.append({"annotation": obj.model_dump(mode="json"), "checks": per_object})
    # Continuous-area IoU only for explicitly supplied, valid boxes.
    if size and all(row["checks"]["box_validity"].get("result", {}).get("valid")
                    if row["checks"]["box_validity"]["result"] is not None else False for row in rows):
        pairs = []
        for i, first in enumerate(objects):
            for second in objects[i + 1:]:
                a, b = first.bbox, second.bbox
                intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
                union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - intersection
                pairs.append({"object_ids": [first.object_id, second.object_id], "measurement_available": True,
                              "iou": intersection / union})
        checks["box_overlaps"] = completed({"pairs": pairs})
    else:
        checks["box_overlaps"] = unavailable("supplied_valid_boxes_unavailable")
    return {"image_id": manifest.sample_id, "image_size": size,
        "annotation_format": document.format, "coordinate_system": "zero_based_continuous",
        "annotation_text": json.dumps({"image": record.source, "annotations": [obj.source for obj in record.annotations]}, allow_nan=False),
        "allowed_labels": allowed_labels or [], "status": "completed" if size else "incomplete",
        "review_status": "pending_review", "errors": [], "checks": checks, "objects": rows,
        "source_diagnostics": [asdict(issue) for issue in issues]}
