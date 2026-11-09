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

unannotated_people uses the same detected people the other way round: each annotation claims the person it
matches, and a person that no annotation claims, that lies mostly outside every annotation, and that is tall
and certain enough to count as clearly visible, is reported as missing an annotation. Both checks share one
model run per image.
"""
from io import BytesIO
from math import ceil, floor

import cv2
import numpy as np
from PIL import Image, ImageDraw

from ..checks.registry import validate_check_parameters
from ..models.finding import FindingOutcome
from .evaluation import RuleEvaluationResult, _json_safe, _make_finding

CHECK = "person_coverage"
MISSING_CHECK = "unannotated_people"
CHECKS = (CHECK, MISSING_CHECK)
MIN_MATCH_IOU = 0.3
MIN_BLOB = 2.0  # x T^2 pixels
# A found person mostly inside some annotation belongs to it: a second detection of an annotated person,
# or someone inside a group box. Only people mostly outside every annotation can be missing one.
MAX_COVERED_FRACTION = 0.5
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
    # "edge" is where that box side belongs: the person's edge, or the edge of the person and what they carry.
    regions = []
    for side, gap, edge in (("top", box[1] - py0, py0), ("bottom", py1 - box[3], py1),
                            ("left", box[0] - px0, px0), ("right", px1 - box[2], px1)):
        if gap > t:
            regions.append({"kind": "missing", "side": side, "pixels": int(gap), "edge": edge,
                            "box": {"top": [px0, py0, px1, box[1]], "bottom": [px0, box[3], px1, py1],
                                    "left": [px0, py0, box[0], py1], "right": [box[2], py0, px1, py1]}[side]})
    for side, gap, edge in (("top", ey0 - box[1], ey0), ("bottom", box[3] - ey1, ey1),
                            ("left", ex0 - box[0], ex0), ("right", box[2] - ex1, ex1)):
        if gap > t:
            regions.append({"kind": "extra", "side": side, "pixels": int(gap), "edge": edge,
                            "box": {"top": [box[0], box[1], box[2], ey0], "bottom": [box[0], ey1, box[2], box[3]],
                                    "left": [box[0], box[1], ex0, box[3]], "right": [ex1, box[1], box[2], box[3]]}[side]})
    result = {"geometry": "box", "tolerance_px": round(t, 1), "person_box": list(person.box),
              "person_score": person.score, "match_iou": round(match, 3), "regions": regions}
    return (FindingOutcome.FAIL if regions else FindingOutcome.PASS), result


def _matched_person(mask, segmentation):
    """(person, match IoU) for a region annotation, or (None, why it cannot be judged)."""
    if not segmentation.people:
        return None, {"reason": "no_person_detected"}
    person = max(segmentation.people, key=lambda p: _mask_iou(mask, p.mask))
    match = _mask_iou(mask, person.mask)
    if match < MIN_MATCH_IOU:
        return None, {"reason": "no_matching_person", "best_match_iou": round(match, 3)}
    return person, match


def _region_differences(mask, person, carried, params):
    """(tolerance, kernel, patches) where a region annotation differs from the person beyond the tolerance.

    Each patch is (kind, raw difference, the patch's pixels, its region record); slivers thinner than about
    the tolerance and patches under MIN_BLOB x T^2 pixels are not patches.
    """
    t = _tolerance(person.box, params)
    radius = max(1, int(round(t / 2)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    allowed = _with_carried(person, carried, t)
    patches = []
    for kind, raw in (("missing", person.mask & ~mask), ("extra", mask & ~allowed)):
        cleaned = cv2.morphologyEx(raw.astype(np.uint8), cv2.MORPH_OPEN, kernel)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(cleaned, connectivity=8)
        for i in range(1, count):
            x, y, w, h, area = (int(v) for v in stats[i])
            if area >= MIN_BLOB * t * t:
                box = [x, y, x + w - 1, y + h - 1]
                patches.append((kind, raw, labels == i,
                                {"kind": kind, "side": _side(box, person.box), "area_px": area, "box": box}))
    return t, kernel, patches


def judge_region(mask, segmentation, params, geometry):
    """mask: HxW bool annotation (mask or filled polygon). Returns (outcome, result)."""
    person, match = _matched_person(mask, segmentation)
    if person is None:
        return FindingOutcome.UNSURE, match
    t, _, patches = _region_differences(mask, person, segmentation.carried, params)
    regions = [record for *_, record in patches]
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
        out.append(("polygon", "region", _raster(polygons, size)))
    return out


def _raster(polygons, size):
    """Filled polygons as an HxW bool mask, boundary pixels included (how every outline is judged)."""
    canvas = Image.new("L", size, 0)
    draw = ImageDraw.Draw(canvas)
    for polygon in polygons:
        draw.polygon([(float(x), float(y)) for x, y in polygon], fill=1, outline=1)
    return np.array(canvas, bool)


def _box_in_annotation_coordinates(box, annotation_format):
    """An inclusive 0-based pixel box in the annotation's own convention (Penn-Fudan: 1-based inclusive)."""
    shift = 1 if annotation_format == "penn_fudan" else 0
    end = 0 if annotation_format == "penn_fudan" else 1  # continuous boxes end after the last pixel
    return [box[0] + shift, box[1] + shift, box[2] + shift + end, box[3] + shift + end]


def _to_annotation_coordinates(result, annotation_format):
    """Report boxes in the annotation's own convention (Penn-Fudan: 1-based inclusive pixels)."""
    shift = 1 if annotation_format == "penn_fudan" else 0
    end = 0 if annotation_format == "penn_fudan" else 1  # continuous boxes end after the last pixel
    fix = lambda b: _box_in_annotation_coordinates(b, annotation_format)
    if "person_box" in result:
        result["person_box"] = fix(result["person_box"])
    for region in result.get("regions", []):
        region["box"] = fix(region["box"])
        if "edge" in region:  # a top or left edge starts at its pixel; a bottom or right edge ends after it
            region["edge"] += shift + (end if region["side"] in ("bottom", "right") else 0)
    result["coordinate_system"] = "one_based_inclusive" if annotation_format == "penn_fudan" else "zero_based_continuous"
    return result


_SIDE_INDEX = {"left": 0, "top": 1, "right": 2, "bottom": 3}


def measured_box_fix(annotation, result):
    """The annotation's box with each failing side moved to its measured edge, and a plain description.

    Sides that passed keep their original coordinates. None when the box geometry has no measured fix.
    """
    box = next((g for g in (result or {}).get("geometries", []) if g.get("geometry") == "box"), None)
    regions = [r for r in (box or {}).get("regions", []) if "edge" in r]
    if not regions or not annotation.get("bbox"):
        return None
    old = list(annotation["bbox"])
    new = list(old)
    moves = []
    for region in regions:
        index = _SIDE_INDEX[region["side"]]
        new[index] = region["edge"]
        shift = new[index] - old[index]
        direction = ("up" if shift < 0 else "down") if index % 2 else ("left" if shift < 0 else "right")
        purpose = (f"to include the {_PART[region['side']]}" if region["kind"] == "missing"
                   else "to remove extra background")
        moves.append(f"{region['side'].capitalize()} edge moved {direction} {round(abs(shift))} px {purpose}.")
    return new, " ".join(moves)


MAX_OUTLINE_POINTS = 128  # the correction editor's limit per polygon
MAX_OUTLINE_PARTS = 8


def _trace(mask):
    """Polygons (lists of [x, y]) tracing a bool mask, each valid and within the editor's limits; or None."""
    from ..datasets.annotation_validation import _polygon_issues
    from ..models.annotation_validation import ValidationLimits
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    contours = sorted((c for c in contours if cv2.contourArea(c) >= 20), key=cv2.contourArea, reverse=True)
    polygons = []
    for contour in contours[:MAX_OUTLINE_PARTS]:
        for epsilon in (1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0):
            points = [(float(x), float(y)) for x, y in cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2)]
            if (3 <= len(points) <= MAX_OUTLINE_POINTS and len(set(points)) >= 3
                    and not _polygon_issues(points, "proposal", ValidationLimits())):
                polygons.append([list(point) for point in points])
                break
        else:
            return None
    return polygons or None


def measured_outline_fix(annotation, annotation_format, size, segmentation, params):
    """The annotation's polygons redrawn with only the flagged parts fixed, and a plain description; or None.

    A flagged missing part of the person is added and a flagged patch of extra background is removed; the
    rest keeps the annotator's outline. None for Penn-Fudan masks (a new mask image is needed), when nothing
    is flagged, or when the redrawn outline would still fail the check.
    """
    if annotation_format == "penn_fudan":
        return None
    region = next((value for geometry, _, value in _object_geometries(annotation, annotation_format, size, None)
                   if geometry == "polygon"), None)
    if region is None:
        return None
    person, _ = _matched_person(region, segmentation)
    if person is None:
        return None
    _, kernel, patches = _region_differences(region, person, segmentation.carried, params)
    if not patches:
        return None
    fixed = region.copy()
    changes = []
    for kind, raw, patch, record in patches:
        near = cv2.dilate(patch.astype(np.uint8), kernel) > 0  # reach the outline the cleaned patch stops short of
        if kind == "missing":
            fixed |= raw & near
            changes.append(f"added the person's {_PART[record['side']]} ({record['area_px']} px)")
        else:
            fixed &= ~(raw & near)
            changes.append(f"removed extra background at the {record['side']} ({record['area_px']} px)")
    polygons = _trace(fixed)
    if polygons is None or (annotation_format == "labelme" and len(polygons) != 1):  # LabelMe: one polygon per shape
        return None
    if judge_region(_raster(polygons, size), segmentation, params, "polygon")[0] is not FindingOutcome.PASS:
        return None
    return polygons, "Outline: " + "; ".join(changes) + "."


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


def unannotated_people(rows, annotation_format, size, instance_mask, segmentation, params):
    """The detected people that no annotation accounts for. Returns (outcome, reason, result).

    Each annotation claims the detected person it matches best (its mask or polygon by mask IoU, else its box
    by box IoU; at least MIN_MATCH_IOU, the same match the person check uses). An unclaimed person counts as
    missing an annotation only when it lies mostly outside every annotation and is at least
    ``min_person_height_px`` tall with a score of at least ``min_score``; the rest are counted, not reported.
    """
    width, height = size
    people = segmentation.people
    covered = np.zeros((height, width), bool)
    claimed = set()
    for row in rows:
        geometries = _object_geometries(row["annotation"], annotation_format, size, instance_mask)
        # An outline (mask or polygon) says where its person is; a box also covers whoever stands behind them.
        shapes = [g for g in geometries if g[1] == "region"] or geometries
        for _, kind, value in shapes:
            if kind == "box":
                x0, y0, x1, y1 = (int(v) for v in value)
                covered[max(0, y0):max(0, y1 + 1), max(0, x0):max(0, x1 + 1)] = True
            else:
                covered |= value
        if not people or not shapes:
            continue
        _, kind, value = shapes[0]
        matches = [_mask_iou(value, p.mask) if kind == "region" else _box_iou(value, p.box) for p in people]
        best = int(np.argmax(matches))
        if matches[best] >= MIN_MATCH_IOU:
            claimed.add(best)
    missing = []
    not_counted = {"inside_an_annotation": 0, "too_small": 0, "low_score": 0}
    for index, person in enumerate(people):
        if index in claimed:
            continue
        x0, y0, x1, y1 = person.box
        tall = y1 - y0 + 1
        inside = float((person.mask & covered).sum()) / max(1, int(person.mask.sum()))
        if inside >= MAX_COVERED_FRACTION:
            not_counted["inside_an_annotation"] += 1
        elif tall < params.min_person_height_px:
            not_counted["too_small"] += 1
        elif person.score < params.min_score:
            not_counted["low_score"] += 1
        else:
            missing.append({"box": _box_in_annotation_coordinates(person.box, annotation_format), "height_px": tall,
                            "score": person.score, "inside_annotations": round(inside, 3)})
    result = {"people_found": len(people), "annotations": len(rows), "people_annotated": len(claimed),
              "unannotated": missing, "not_counted": not_counted,
              "min_person_height_px": params.min_person_height_px, "min_score": params.min_score,
              "coordinate_system": "one_based_inclusive" if annotation_format == "penn_fudan" else "zero_based_continuous"}
    if not missing:
        return FindingOutcome.PASS, None, result
    where = "; ".join(f"at {m['box']} ({m['height_px']} px tall, {round(m['score'] * 100)}% sure)" for m in missing)
    count = "1 person has" if len(missing) == 1 else f"{len(missing)} people have"
    return FindingOutcome.FAIL, f"{count} no annotation: {where}"[:500], result


# ── audit step ───────────────────────────────────────────────────────────────
def augment_person_coverage(evaluation, rulebook, measurements, manifest, asset_settings, *, audit_id,
                            sample_id, sample_revision, segmenter_factory=None):
    """Replace the evaluator's placeholder person_coverage and unannotated_people findings with measured ones."""
    rules = [r for r in rulebook.rules if r.check_name in CHECKS]
    if not rules:
        return evaluation
    findings = [f for f in evaluation.findings if f.check_name not in CHECKS]

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
        params = validate_check_parameters(rule.check_name, rule.parameters)
        if rule.check_name == MISSING_CHECK:  # an image with no annotations can still have people missing them
            outcome, reason, result = unannotated_people(rows, manifest.annotation_format, image.size,
                                                         instance_mask, segmentation, params)
            add(rule, outcome, reason, None, "sample",
                {"status": "completed", "reason": None, "detail": None, "result": _json_safe(result),
                 "segmenter": identity})
            continue
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
