"""person_coverage: compare each person annotation with an independent person outline and say where it differs.

The outline comes from a local Mask R-CNN that sees only the photo. Plain code makes the decision, with the
rule's tolerance T = max(min_tolerance_px, tolerance_fraction x detected person height):

- box: fail when the person extends past a box side by more than T (missing head/feet/side), or the box extends
  past the person by more than T (extra background);
- mask or polygon: fail when a missing region (person not covered) or an extra region (annotation outside the
  person) remains after ignoring slivers thinner than about T and is at least 2 x T^2 pixels.

An annotation may include what the person carries (backpack, umbrella, handbag, suitcase); missing parts are
always measured against the person alone. No matching person means ``unsure`` (needs review) and a missing
model means ``unavailable``: neither can ever produce a pass. Validated offline on Penn-Fudan (tuning) and
COCO val2017 (held-out): see .cache/validation/rcnn-benchmark-20261106/REPORT.md.
"""
from io import BytesIO
from math import ceil, floor
from typing import Any, Mapping

import cv2
import numpy as np
from PIL import Image, ImageDraw

from ..checks.registry import validate_check_parameters
from ..models.finding import FindingOutcome
from .evaluation import RuleEvaluationResult, _json_safe, _make_finding

CHECK = "person_coverage"
MIN_MATCH_IOU = 0.3
MIN_BLOB = 2.0  # x T^2 pixels
_PART = {"top": "head/top", "bottom": "feet/bottom", "left": "left side", "right": "right side", "middle": "middle"}


def _extent(mask):
    ys, xs = np.nonzero(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _box_iou(a, b):
    inter = max(0, min(a[2], b[2]) - max(a[0], b[0]) + 1) * max(0, min(a[3], b[3]) - max(a[1], b[1]) + 1)
    area = lambda r: (r[2] - r[0] + 1) * (r[3] - r[1] + 1)
    return inter / (area(a) + area(b) - inter)


def _mask_iou(a, b):
    return float((a & b).sum()) / max(1, int((a | b).sum()))


def _tolerance(person_box, params) -> float:
    return max(float(params.min_tolerance_px), params.tolerance_fraction * (person_box[3] - person_box[1] + 1))


def _with_carried(person, carried, tolerance):
    near = cv2.dilate(person.mask.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=max(1, int(tolerance))) > 0
    union = person.mask.copy()
    for item in carried:
        if (item & near).any():
            union |= item
    return union


def _side(region_box, person_box):
    cx, cy = (region_box[0] + region_box[2]) / 2, (region_box[1] + region_box[3]) / 2
    x0, y0, x1, y1 = person_box
    fy, fx = (cy - y0) / max(1, y1 - y0), (cx - x0) / max(1, x1 - x0)
    return ("top" if fy < 0.2 else "bottom" if fy > 0.8 else "left" if fx < 0.3 else "right" if fx > 0.7 else "middle")


def judge_box(box, segmentation, params):
    """box: inclusive pixel extent (x0, y0, x1, y1). Returns (outcome, result)."""
    if not segmentation.people:
        return FindingOutcome.UNSURE, {"reason": "no_person_detected"}
    person = max(segmentation.people, key=lambda p: _box_iou(box, p.box))
    match = _box_iou(box, person.box)
    if match < MIN_MATCH_IOU:
        return FindingOutcome.UNSURE, {"reason": "no_matching_person", "best_match_iou": round(match, 3)}
    t = _tolerance(person.box, params)
    px0, py0, px1, py1 = person.box
    ex0, ey0, ex1, ey1 = _extent(_with_carried(person, segmentation.carried, t))
    regions = []
    for side, gap in (("top", box[1] - py0), ("bottom", py1 - box[3]), ("left", box[0] - px0), ("right", px1 - box[2])):
        if gap > t:
            regions.append({"kind": "missing", "side": side, "pixels": int(gap),
                            "box": {"top": [px0, py0, px1, box[1]], "bottom": [px0, box[3], px1, py1],
                                    "left": [px0, py0, box[0], py1], "right": [box[2], py0, px1, py1]}[side]})
    for side, gap in (("top", ey0 - box[1]), ("bottom", box[3] - ey1), ("left", ex0 - box[0]), ("right", box[2] - ex1)):
        if gap > t:
            regions.append({"kind": "extra", "side": side, "pixels": int(gap),
                            "box": {"top": [box[0], box[1], box[2], ey0], "bottom": [box[0], ey1, box[2], box[3]],
                                    "left": [box[0], box[1], ex0, box[3]], "right": [ex1, box[1], box[2], box[3]]}[side]})
    result = {"geometry": "box", "tolerance_px": round(t, 1), "person_box": list(person.box),
              "person_score": person.score, "match_iou": round(match, 3), "regions": regions}
    return (FindingOutcome.FAIL if regions else FindingOutcome.PASS), result


def judge_region(mask, segmentation, params, geometry):
    """mask: HxW bool annotation (mask or filled polygon). Returns (outcome, result)."""
    if not segmentation.people:
        return FindingOutcome.UNSURE, {"reason": "no_person_detected"}
    person = max(segmentation.people, key=lambda p: _mask_iou(mask, p.mask))
    match = _mask_iou(mask, person.mask)
    if match < MIN_MATCH_IOU:
        return FindingOutcome.UNSURE, {"reason": "no_matching_person", "best_match_iou": round(match, 3)}
    t = _tolerance(person.box, params)
    radius = max(1, int(round(t / 2)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    allowed = _with_carried(person, segmentation.carried, t)
    regions = []
    for kind, region in (("missing", person.mask & ~mask), ("extra", mask & ~allowed)):
        cleaned = cv2.morphologyEx(region.astype(np.uint8), cv2.MORPH_OPEN, kernel)
        count, _, stats, _ = cv2.connectedComponentsWithStats(cleaned, connectivity=8)
        for i in range(1, count):
            x, y, w, h, area = (int(v) for v in stats[i])
            if area >= MIN_BLOB * t * t:
                box = [x, y, x + w - 1, y + h - 1]
                regions.append({"kind": kind, "side": _side(box, person.box), "area_px": area, "box": box})
    result = {"geometry": geometry, "tolerance_px": round(t, 1), "person_box": list(person.box),
              "person_score": person.score, "match_iou": round(match, 3), "regions": regions}
    return (FindingOutcome.FAIL if regions else FindingOutcome.PASS), result


def describe(region) -> str:
    part = _PART[region["side"]]
    size = f"{region['pixels']} px" if "pixels" in region else f"{region['area_px']} px area"
    return (f"{part} of the person is outside the annotation ({size})" if region["kind"] == "missing"
            else f"extra background at the {region['side']} ({size})")


# ── annotation geometry in image pixels ──────────────────────────────────────
def _object_geometries(annotation, annotation_format, size, instance_mask):
    """[(geometry, kind, value)] where kind is 'box' (inclusive pixel extent) or 'region' (bool mask)."""
    width, height = size
    out = []
    if annotation_format == "penn_fudan":
        x0, y0, x1, y1 = annotation["bbox"]
        out.append(("box", "box", (x0 - 1, y0 - 1, x1 - 1, y1 - 1)))  # 1-based inclusive -> 0-based pixels
        if instance_mask is not None:
            region = instance_mask == int(annotation["object_id"])
            if region.any():
                out.append(("mask", "region", region))
        return out
    bbox = annotation.get("bbox")
    if bbox and annotation.get("bbox_origin") == "supplied":
        x0, y0, x1, y1 = bbox
        out.append(("box", "box", (max(0, floor(x0)), max(0, floor(y0)), min(width - 1, ceil(x1) - 1),
                                   min(height - 1, ceil(y1) - 1))))
    polygons = [p for p in annotation.get("polygons") or [] if len(p) >= 3]
    if polygons:
        canvas = Image.new("L", (width, height), 0)
        draw = ImageDraw.Draw(canvas)
        for polygon in polygons:
            draw.polygon([(float(x), float(y)) for x, y in polygon], fill=1, outline=1)
        out.append(("polygon", "region", np.array(canvas, bool)))
    return out


def _to_annotation_coordinates(result, annotation_format):
    """Report boxes in the annotation's own convention (Penn-Fudan: 1-based inclusive pixels)."""
    shift = 1 if annotation_format == "penn_fudan" else 0
    end = 0 if annotation_format == "penn_fudan" else 1  # continuous boxes end after the last pixel
    fix = lambda b: [b[0] + shift, b[1] + shift, b[2] + shift + end, b[3] + shift + end]
    if "person_box" in result:
        result["person_box"] = fix(result["person_box"])
    for region in result.get("regions", []):
        region["box"] = fix(region["box"])
    result["coordinate_system"] = "one_based_inclusive" if annotation_format == "penn_fudan" else "zero_based_continuous"
    return result


def object_coverage(annotation, annotation_format, size, instance_mask, segmentation, params):
    """One object's outcome over every supplied geometry, and the evidence."""
    judged = []
    for geometry, kind, value in _object_geometries(annotation, annotation_format, size, instance_mask):
        outcome, result = (judge_box(value, segmentation, params) if kind == "box"
                           else judge_region(value, segmentation, params, geometry))
        judged.append((outcome, _to_annotation_coordinates({"geometry": geometry, **result}, annotation_format)))
    if not judged:
        return FindingOutcome.UNAVAILABLE, "no_supplied_geometry", {"geometries": []}
    outcomes = [o for o, _ in judged]
    problems = [f"{r['geometry']}: {describe(region)}" for _, r in judged for region in r.get("regions", [])]
    if FindingOutcome.FAIL in outcomes:
        outcome, reason = FindingOutcome.FAIL, "; ".join(problems)[:500]
    elif FindingOutcome.UNSURE in outcomes:
        outcome = FindingOutcome.UNSURE
        reason = next(r["reason"] for o, r in judged if o is FindingOutcome.UNSURE)
    else:
        outcome, reason = FindingOutcome.PASS, None
    return outcome, reason, {"problems": problems, "geometries": [r for _, r in judged]}


# ── audit step ───────────────────────────────────────────────────────────────
def augment_person_coverage(evaluation, rulebook, measurements, manifest, asset_settings, *, audit_id,
                            sample_id, sample_revision, segmenter_factory=None):
    """Replace the evaluator's placeholder person_coverage findings with measured ones."""
    rules = [r for r in rulebook.rules if r.check_name == CHECK]
    if not rules:
        return evaluation
    findings = [f for f in evaluation.findings if f.check_name != CHECK]

    def add(rule, outcome, reason, object_id, occurrence, evidence):
        findings.append(_make_finding(rule=rule, outcome=outcome, reason=reason, evidence=evidence,
                                      audit_id=audit_id, sample_id=sample_id, sample_revision=sample_revision,
                                      object_id=object_id, occurrence=occurrence,
                                      rulebook_id=rulebook.rulebook_id, rulebook_version=rulebook.version))

    rows = list((measurements or {}).get("objects") or [])
    try:
        if manifest is None or asset_settings is None:
            raise LookupError("source_data_unavailable")
        from ..storage.workflow_repository import read_manifest_asset_file
        _, image_bytes = read_manifest_asset_file(manifest, "original_image", asset_settings)
        image = Image.open(BytesIO(image_bytes))
        image.load()
        instance_mask = None
        if manifest.annotation_format == "penn_fudan" and manifest.raw_mask.availability.value == "available":
            _, mask_bytes = read_manifest_asset_file(manifest, "raw_mask", asset_settings)
            instance_mask = np.array(Image.open(BytesIO(mask_bytes)))
        if segmenter_factory is None:
            from ..inference.person_segmenter import get_segmenter as segmenter_factory
        segmenter = segmenter_factory()
        segmentation = segmenter.segment(image)
        identity = dict(getattr(segmenter, "identity", {}))
    except Exception as error:  # every failure is visible as unavailable, never as a pass
        code = str(error) if isinstance(error, LookupError) else "person_segmenter_unavailable"
        for rule in rules:
            add(rule, FindingOutcome.UNAVAILABLE, code, None, "sample", {"status": "unavailable", "reason": code,
                                                                         "detail": type(error).__name__})
        return RuleEvaluationResult(findings=findings, internal_check_failures=evaluation.internal_check_failures)

    for rule in rules:
        params = validate_check_parameters(CHECK, rule.parameters)
        if not rows:
            add(rule, FindingOutcome.UNAVAILABLE, "evidence_unavailable", None, "sample",
                {"status": "unavailable", "reason": "no_annotations"})
            continue
        for index, row in enumerate(rows):
            annotation = row["annotation"]
            outcome, reason, result = object_coverage(annotation, manifest.annotation_format, image.size,
                                                      instance_mask, segmentation, params)
            add(rule, outcome, reason, int(annotation["object_id"]), f"object:{index}",
                {"status": "completed", "reason": None, "detail": None, "result": _json_safe(result),
                 "annotation": _json_safe(annotation), "segmenter": identity, "source_index": index})
    return RuleEvaluationResult(findings=findings, internal_check_failures=evaluation.internal_check_failures)
