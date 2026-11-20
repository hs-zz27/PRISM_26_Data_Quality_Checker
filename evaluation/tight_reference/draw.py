#!/usr/bin/env python3
"""Draw tight outlines for the reference kit by machine, as a stand-in for redrawing them by hand.

For each person, COCO's outline is tightened with OpenCV's GrabCut: the inside of COCO's outline is
"probably the person", a band around it "probably background", a core where COCO's outline and the person
model agree is "certainly the person", and other people's bodies are "certainly background". The visible
pieces of a partly hidden person become separate parts with one Group ID. A result that strays too far from
COCO's person keeps COCO's outline and is listed as not redrawn.

These outlines are machine-made. They show what a tight outline looks like and are tighter than a person
would draw, so they only become a reference once the mentor has approved them (README.md).

  .venv/bin/python -B evaluation/tight_reference/draw.py          # reads kit/, writes drawn/

Uses the application's person model file; nothing is downloaded. Photos and COCO files are not changed.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

import cv2
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

BAND_FRACTION, MIN_BAND_PX = 0.06, 10      # how far outside COCO's outline the person may extend
CORE_FRACTION, MIN_CORE_PX = 0.04, 3       # how far inside COCO's outline is certainly the person
ITERATIONS = 5
MIN_PART_SHARE = 0.01                      # a visible piece smaller than this share of the person is dropped
SLIT_HOLE_SHARE = 0.004                    # a background hole larger than this share is cut open, not filled
EPSILON_PX = 1.0                           # outline simplification: points stay within this of the cut
MIN_IOU, AREA_RANGE = 0.6, (0.6, 1.3)      # safety checks against COCO's person


def raster(polygons, size) -> np.ndarray:
    mask = np.zeros((size[1], size[0]), np.uint8)
    for polygon in polygons:
        cv2.fillPoly(mask, [np.rint(np.asarray(polygon, float)).astype(np.int32)], 1)
    return mask


def disk(radius: int) -> np.ndarray:
    radius = max(int(radius), 1)
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1,) * 2)


def people(document):
    """[(shape indexes, polygons)] per person: parts sharing a group_id are one person."""
    groups = {}
    for index, shape in enumerate(document["shapes"]):
        if shape["label"] == "person":
            key = ("g", shape["group_id"]) if shape["group_id"] is not None else ("s", index)
            groups.setdefault(key, []).append(index)
    return [(indexes, [document["shapes"][i]["points"] for i in indexes]) for indexes in groups.values()]


def _iou(a, b):
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 0.0


def _skeleton(mask: np.ndarray) -> np.ndarray:
    """The middle line of every part of a mask, however thin (a morphological skeleton)."""
    cross = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    skeleton = np.zeros(mask.shape, bool)
    current = mask.astype(np.uint8)
    while current.any():
        eroded = cv2.erode(current, cross)
        skeleton |= (current > 0) & ~(cv2.dilate(eroded, cross) > 0)
        current = eroded
    return skeleton.astype(np.uint8)


def _open_holes(mask: np.ndarray, min_area: int) -> np.ndarray:
    """Fill small background holes; cut a one-pixel slit from each large hole to the outside, as annotators do.

    Small holes are filled first so a fill never plugs a slit. Each slit starts inside its hole, ends on the
    true outside (not another hole) and is a 4-connected line, so the outline tracer cannot step across it.
    """
    out = mask.copy()
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if hierarchy is None:
        return out
    solid = np.zeros_like(mask)
    holes = []
    for index, contour in enumerate(contours):
        if hierarchy[0][index][3] < 0:
            cv2.drawContours(solid, [contour], -1, 1, -1)
        else:
            holes.append(contour)
    large = []
    for contour in holes:
        if cv2.contourArea(contour) < min_area:
            cv2.drawContours(out, [contour], -1, 1, -1)
        else:
            large.append(contour)
    outside_y, outside_x = np.nonzero(solid == 0)
    if not large or not len(outside_x):
        return out
    to_outside = cv2.distanceTransform(solid, cv2.DIST_L2, 3)
    for contour in large:
        region = np.zeros_like(mask)
        cv2.drawContours(region, [contour], -1, 1, -1)
        hole_y, hole_x = np.nonzero((region > 0) & (mask == 0))
        if not len(hole_x):
            continue
        first = int(np.argmin(to_outside[hole_y, hole_x]))
        start = (int(hole_x[first]), int(hole_y[first]))
        nearest = int(np.argmin((outside_x - start[0]) ** 2 + (outside_y - start[1]) ** 2))
        cv2.line(out, start, (int(outside_x[nearest]), int(outside_y[nearest])), 0, 1, lineType=cv2.LINE_4)
    return out


def tighten(photo: np.ndarray, polygons, others: np.ndarray, found_mask: np.ndarray | None):
    """(list of polygons, report). ``others`` marks other people's bodies; ``found_mask`` is the person model's."""
    height_px, width_px = photo.shape[:2]
    original = raster(polygons, (width_px, height_px))
    ys, xs = np.nonzero(original)
    if not len(xs):
        return None, {"reason": "empty outline"}
    person_height = ys.max() - ys.min() + 1
    band = max(MIN_BAND_PX, int(round(BAND_FRACTION * person_height)))
    core_px = max(MIN_CORE_PX, int(round(CORE_FRACTION * person_height)))
    x0, y0 = max(0, xs.min() - band - 4), max(0, ys.min() - band - 4)
    x1, y1 = min(width_px, xs.max() + band + 5), min(height_px, ys.max() + band + 5)
    crop = np.ascontiguousarray(photo[y0:y1, x0:x1][:, :, ::-1])  # GrabCut expects BGR
    inside = original[y0:y1, x0:x1]
    grabcut = np.full(inside.shape, cv2.GC_BGD, np.uint8)
    grabcut[cv2.dilate(inside, disk(band)) > 0] = cv2.GC_PR_BGD
    grabcut[inside > 0] = cv2.GC_PR_FGD
    core = cv2.erode(inside, disk(core_px))
    if found_mask is not None:
        agreed = cv2.erode(inside, disk(2)) & cv2.erode(found_mask[y0:y1, x0:x1].astype(np.uint8), disk(2))
        # Shrinking erases thin limbs; their middle line, where COCO and the person model agree, stays certain.
        core = (core & cv2.erode(found_mask[y0:y1, x0:x1].astype(np.uint8), disk(2))) | _skeleton(agreed)
    if not core.any():
        core = cv2.erode(inside, disk(2))
    neighbours = cv2.erode(others[y0:y1, x0:x1], disk(2)) & (1 - inside)
    grabcut[neighbours > 0] = cv2.GC_BGD
    grabcut[core > 0] = cv2.GC_FGD
    background, foreground = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(crop, grabcut, None, background, foreground, ITERATIONS, cv2.GC_INIT_WITH_MASK)
    person = np.isin(grabcut, (cv2.GC_FGD, cv2.GC_PR_FGD)).astype(np.uint8)
    person = cv2.morphologyEx(person, cv2.MORPH_OPEN, disk(1))
    person = cv2.morphologyEx(person, cv2.MORPH_CLOSE, disk(1))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(person, connectivity=8)
    keep = np.zeros_like(person)
    for label in range(1, count):
        piece = labels == label
        if stats[label, cv2.CC_STAT_AREA] >= MIN_PART_SHARE * inside.sum() and (piece & (core > 0)).any() | (
                (piece & (inside > 0)).sum() >= 0.5 * piece.sum()):
            keep[piece] = 1
    keep = _open_holes(keep, int(SLIT_HOLE_SHARE * inside.sum()))
    full = np.zeros((height_px, width_px), np.uint8)
    full[y0:y1, x0:x1] = keep
    report = {"iou_with_coco": round(_iou(full, original), 3),
              "area_ratio": round(float(full.sum() / max(original.sum(), 1)), 3)}
    if report["iou_with_coco"] < MIN_IOU or not AREA_RANGE[0] <= report["area_ratio"] <= AREA_RANGE[1]:
        return None, {**report, "reason": "strayed from COCO's person"}
    contours, _ = cv2.findContours(full, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    parts = []
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        if cv2.contourArea(contour) < MIN_PART_SHARE * original.sum():
            continue
        simple = cv2.approxPolyDP(contour, EPSILON_PX, True).reshape(-1, 2)
        if len(simple) >= 3:
            parts.append([[float(x), float(y)] for x, y in simple])
    if not parts:
        return None, {**report, "reason": "nothing left after cleaning"}
    return parts, {**report, "parts": len(parts)}


def draw_document(document, photo, found):
    """(redrawn document, per-person reports). ``found`` is the person model's segmentation of the photo."""
    size = (document["imageWidth"], document["imageHeight"])
    everyone = people(document)
    masks = [raster(polygons, size) for _, polygons in everyone]
    matched = []
    for mask in masks:
        best, best_iou = None, 0.3
        for person in (found.people if found is not None else []):
            iou = _iou(mask > 0, person.mask)
            if iou >= best_iou:
                best, best_iou = person.mask, iou
        matched.append(best)
    out = copy.deepcopy(document)
    out["shapes"] = [s for s in out["shapes"] if s["label"] != "person"]
    reports, next_group = [], max([s["group_id"] or 0 for s in document["shapes"]] + [0]) + 1
    for number, (indexes, polygons) in enumerate(everyone):
        others = np.zeros_like(masks[number])
        for other, mask in enumerate(masks):
            if other != number:
                others |= mask
                if matched[other] is not None:
                    others |= matched[other].astype(np.uint8)
        parts, report = tighten(photo, polygons, others, matched[number])
        first = document["shapes"][indexes[0]]
        reports.append({"coco_annotation": first["description"], "shapes": indexes, **report,
                        "redrawn": parts is not None})
        if parts is None:
            out["shapes"] += [copy.deepcopy(document["shapes"][i]) for i in indexes]
            continue
        group = first["group_id"] if first["group_id"] is not None else (next_group if len(parts) > 1 else None)
        if group == next_group:
            next_group += 1
        for points in parts:
            out["shapes"].append({"label": "person", "points": points, "group_id": group,
                                  "description": first["description"], "shape_type": "polygon", "flags": {}})
    return out, reports


def main(kit: Path, output: Path):
    from backend.app.inference.person_segmenter import get_segmenter

    manifest = json.loads((kit / "kit_manifest.json").read_text(encoding="utf-8"))
    segmenter = get_segmenter()
    output.mkdir(parents=True, exist_ok=True)
    log = []
    for photo_entry in manifest["photos"]:
        document = json.loads((kit / photo_entry["file"]).read_text(encoding="utf-8"))
        image = Image.open(kit / photo_entry["image"]).convert("RGB")
        redrawn, reports = draw_document(document, np.array(image), segmenter.segment(image))
        (output / photo_entry["file"]).write_text(json.dumps(redrawn, indent=2) + "\n", encoding="utf-8")
        log.append({"file": photo_entry["file"], "category": photo_entry["category"], "people": reports})
        done = sum(r["redrawn"] for r in reports)
        print(f"{photo_entry['file']}: {done} of {len(reports)} people redrawn", flush=True)
    (output / "drawn_manifest.json").write_text(json.dumps(
        {"method": "OpenCV GrabCut seeded from COCO's outline and the person model; machine-drawn, not yet "
                   "approved by the mentor", "photos": log}, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--kit", type=Path, default=HERE / "kit")
    parser.add_argument("--output", type=Path, default=HERE / "drawn")
    args = parser.parse_args()
    main(args.kit, args.output)
