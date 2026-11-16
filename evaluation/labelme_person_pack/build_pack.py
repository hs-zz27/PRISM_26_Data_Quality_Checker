#!/usr/bin/env python3
"""Build a larger LabelMe person pack with graded, body-part-aware seeded errors.

Run from the repository root with the repository virtual environment. No model, database or service is needed.
Photos are copied byte for byte and never edited; outputs stay local (see .gitignore).

Outputs under this folder:
  archives/labelme_pack_clean.zip    person polygons as the annotators drew them, exact source numbers
  archives/labelme_pack_errors.zip   the same photos with one seeded error in three files out of four
  answer_key.json                    what was injected, where, how large (never upload this)
  verification.json                  source and archive hashes
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
import random

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
_SPEC = importlib.util.spec_from_file_location("labelme_flow_build", REPO / "evaluation/labelme_end_to_end/build_dataset.py")
base = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(base)

SEED = 20261114
ANNOTATIONS = REPO / ".cache/downloads/coco2017-annotations/person_keypoints_val2017.json"
IMAGES = REPO / ".cache/downloads/coco2017-val-images"
MAX_PEOPLE = 10           # files have 1 to 10 person annotations
MIN_HEIGHT = 100          # an outline error needs an outline at least this tall, in pixels
PART_MIN_HEIGHT = 150     # a body-part error needs a taller person, so the part is clearly visible
CONTROL_EVERY = 4         # every fourth file (in seeded order) is left unchanged
PART_SHARE = (0.02, 0.40)  # a removed body part must be this share of the outline's area
MAX_GAP = 0.08            # ... and no further away than this share of the person's height
NEIGHBOUR_SHARE = 0.25    # an overlap error needs a neighbour between this share and 1/this share of the outline's area
BODY_POINTS = ("nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder",
               "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip",
               "left_knee", "right_knee", "left_ankle", "right_ankle")
# error name: (family, level). A level is a share of the person's height, or of the neighbour's area for overlap.
ERRORS = {
    "head_missing": ("part", "head"), "arm_missing": ("part", "arm"), "leg_missing": ("part", "leg"),
    "loose_3": ("loose", 0.03), "loose_6": ("loose", 0.06), "loose_12": ("loose", 0.12),
    "shrunk_3": ("shrunk", 0.03), "shrunk_6": ("shrunk", 0.06),
    "overlap_10": ("overlap", 0.10), "overlap_30": ("overlap", 0.30),
    "wrong_label": ("label", None), "annotation_removed": ("removed", None),
}
# (check expected to report it, region kind it should report). The overlap check does not exist yet.
EXPECTED = {"part": ("person_coverage", "missing"), "shrunk": ("person_coverage", "missing"),
            "loose": ("person_coverage", "extra"), "overlap": (None, None),
            "label": ("allowed_labels", None), "removed": ("unannotated_people", None)}


# ── source photos as LabelMe files ──────────────────────────────────────────
def load_documents(annotations: Path = ANNOTATIONS, images: Path = IMAGES):
    """({stem: LabelMe document}, {stem: the COCO annotation behind each shape}) for the photos on disk.

    A photo is used when it has 1 to MAX_PEOPLE people and every one is drawn as polygons (no crowd regions).
    """
    source = json.loads(annotations.read_text(encoding="utf-8"))
    people: dict[int, list[dict]] = {}
    for annotation in source["annotations"]:
        people.setdefault(annotation["image_id"], []).append(annotation)
    documents, owners = {}, {}
    for image in sorted(source["images"], key=lambda item: item["file_name"]):
        found = sorted(people.get(image["id"], []), key=lambda item: item["id"])
        if not (images / image["file_name"]).is_file() or not 1 <= len(found) <= MAX_PEOPLE:
            continue
        if any(person["iscrowd"] or not isinstance(person["segmentation"], list) for person in found):
            continue
        stem = Path(image["file_name"]).stem
        documents[stem] = base.labelme_document(image, found)
        owners[stem] = [person for person in found for _ in person["segmentation"]]
    return documents, owners


# ── outlines as pixels ──────────────────────────────────────────────────────
def raster(points, size) -> np.ndarray:
    mask = np.zeros((size[1], size[0]), np.uint8)
    cv2.fillPoly(mask, [np.rint(np.asarray(points, float)).astype(np.int32)], 1)
    return mask


def outline(mask: np.ndarray):
    """The largest region of the mask as polygon points, or None when nothing usable is left."""
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    points = cv2.approxPolyDP(max(contours, key=cv2.contourArea), 1.0, True).reshape(-1, 2)
    return [[float(x), float(y)] for x, y in points] if len(points) >= 3 else None


def disk(radius: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))


def extent(mask: np.ndarray):
    ys, xs = np.nonzero(mask)
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if len(xs) else None


def body_point(annotation: dict, name: str):
    x, y, seen = annotation["keypoints"][3 * BODY_POINTS.index(name):3 * BODY_POINTS.index(name) + 3]
    return (float(x), float(y)) if seen else None


# ── body-part regions, from the body points the COCO annotators marked ──────
def head_regions(annotation, mask, height):
    shoulders = [body_point(annotation, f"{side}_shoulder") for side in ("left", "right")]
    head = [p for p in (body_point(annotation, name) for name in BODY_POINTS[:5]) if p]
    if None in shoulders or not head:
        return []
    cx, cy = (sum(p[i] for p in head) / len(head) for i in (0, 1))
    shoulder_y = (shoulders[0][1] + shoulders[1][1]) / 2
    if shoulder_y - cy < 0.05 * height:  # not upright: "above the shoulders" would not be the head
        return []
    half = max(0.9 * abs(shoulders[0][0] - shoulders[1][0]), 0.12 * height)
    region = np.zeros_like(mask)
    region[:int(cy + 0.6 * (shoulder_y - cy)), max(0, int(cx - half)):int(cx + half) + 1] = 1
    return [region]


def limb_regions(annotation, mask, height, upper, lower, beyond, radius_share):
    """One region per side whose two joints are marked: the limb from `upper` to past `lower`."""
    regions = []
    for side in ("left", "right"):
        start, end = body_point(annotation, f"{side}_{upper}"), body_point(annotation, f"{side}_{lower}")
        if start and end:
            tip = (end[0] + beyond * (end[0] - start[0]), end[1] + beyond * (end[1] - start[1]))
            region = np.zeros_like(mask)
            cv2.line(region, tuple(map(round, start)), tuple(map(round, tip)), 1,
                     2 * max(round(radius_share * height), 6))
            regions.append(region)
    return regions


def part_masks(part, annotation, mask, height):
    if part == "head":
        regions = head_regions(annotation, mask, height)
    elif part == "arm":
        regions = limb_regions(annotation, mask, height, "elbow", "wrist", 0.5, 0.05)
    else:
        regions = limb_regions(annotation, mask, height, "knee", "ankle", 0.4, 0.06)
    # opening drops the one-pixel slivers a cut can leave along the old outline
    return [cv2.morphologyEx(mask & (1 - region), cv2.MORPH_OPEN, disk(1)) for region in regions]


# ── seeded errors ───────────────────────────────────────────────────────────
def measure(before: np.ndarray, points, size) -> dict:
    """How the altered outline differs from the source outline, in pixels."""
    after = raster(points, size)
    removed, added = before & (1 - after), after & (1 - before)
    area = int(before.sum())
    changed = removed | added
    # the outline is re-traced, so it can differ by a pixel anywhere; the box is where it really changed
    solid = cv2.morphologyEx(changed, cv2.MORPH_OPEN, disk(1))
    return {"removed_px": int(removed.sum()), "added_px": int(added.sum()),
            "removed_share": round(int(removed.sum()) / area, 4), "added_share": round(int(added.sum()) / area, 4),
            "changed_box": extent(solid if solid.any() else changed)}


def altered_points(document, index, annotation, kind):
    """(new points, answer-key details) for an outline error, or None when it cannot be applied cleanly."""
    family, level = ERRORS[kind]
    size = (document["imageWidth"], document["imageHeight"])
    shapes = document["shapes"]
    mask = raster(shapes[index]["points"], size)
    box = extent(mask)
    if box is None:
        return None
    height = box[3] - box[1]
    if family == "part":
        if height < PART_MIN_HEIGHT:
            return None
        for candidate in part_masks(level, annotation, mask, height):
            points = outline(candidate)
            details = points and measure(mask, points, size)
            if details and PART_SHARE[0] <= details["removed_share"] <= PART_SHARE[1] and details["added_share"] < 0.01:
                return points, {"body_part": level, **details}
        return None
    if family in ("loose", "shrunk"):
        radius = max(round(level * height), 3)
        changed = cv2.dilate(mask, disk(radius)) if family == "loose" else cv2.erode(mask, disk(radius))
        points = outline(changed)
        details = points and measure(mask, points, size)
        share = details and details["added_share" if family == "loose" else "removed_share"]
        if not details or share < 0.05 or (family == "shrunk" and details["removed_share"] > 0.8):
            return None
        return points, {"level": level, "distance_px": radius, **details}
    # overlap: grow this outline into one neighbour until it covers `level` of the neighbour's area
    distance = cv2.distanceTransform(1 - mask, cv2.DIST_L2, 5)  # how far each pixel is from this outline
    for other, shape in enumerate(shapes):
        if other == index or shape["group_id"] is not None or shape["label"] != "person":
            continue
        neighbour = raster(shape["points"], size)
        area = int(neighbour.sum())
        tall = base.bounds(shape["points"])[3] - base.bounds(shape["points"])[1] >= MIN_HEIGHT
        # a neighbour of comparable size that this outline does not already cover
        own = int(mask.sum())
        if (not tall or not NEIGHBOUR_SHARE * own <= area <= own / NEIGHBOUR_SHARE
                or (mask & neighbour).sum() / area > 0.03):
            continue
        gap = float(distance[neighbour > 0].min())  # background between the two people, in pixels
        if gap > max(MAX_GAP * height, 6):  # only people who stand next to each other
            continue
        # the neighbour plus the strip of background between the two, so the grown outline stays in one piece
        reach = (cv2.distanceTransform(1 - neighbour, cv2.DIST_L2, 5) <= gap + 2).astype(np.uint8)
        for radius in range(int(gap) + 2, max(int(0.6 * height), 3), 2):
            points = outline(mask | (reach & (distance <= radius).astype(np.uint8)))
            share = float((raster(points, size) & neighbour).sum()) / area if points else 0.0
            if share >= level:
                if share <= level + 0.15:
                    return points, {"level": level, "overlap_share_of_neighbour": round(share, 4),
                                    "neighbour_shape_key": f"/shapes/{other}", **measure(mask, points, size)}
                break
    return None


def candidates(document: dict, kind: str) -> list[int]:
    family = ERRORS[kind][0]
    if family in ("label", "removed"):
        return base.candidates(document, kind)
    return [index for index, shape in enumerate(document["shapes"])
            if shape["group_id"] is None and shape["label"] == "person"
            and base.bounds(shape["points"])[3] - base.bounds(shape["points"])[1] >= MIN_HEIGHT]


def inject(document: dict, owners: list[dict], kind: str, index: int):
    """(altered document, answer-key entry), or None when this error cannot be applied cleanly."""
    family = ERRORS[kind][0]
    check, region_kind = EXPECTED[family]
    if family in ("label", "removed"):
        made = base.inject(document, kind, index)
        if made is None:
            return None
        altered, entry = made
    else:
        result = altered_points(document, index, owners[index], kind)
        if result is None:
            return None
        altered = copy.deepcopy(document)
        altered["shapes"][index]["points"], details = result
        entry = {"error": kind, "shape_key": f"/shapes/{index}", "source_shape_key": f"/shapes/{index}",
                 "source_box": [round(v, 2) for v in base.bounds(document["shapes"][index]["points"])],
                 "details": details}
        # An injected error is an annotation mistake, never a broken file: it may not add a structural issue.
        if base.structural_codes(altered) != base.structural_codes(document):
            return None
    entry.update(family=family, expected_check=check, expected_region_kind=region_kind)
    return altered, entry


def plan_errors(documents: dict[str, dict], owners: dict[str, list[dict]], seed: int = SEED):
    """({stem: altered document}, answer-key cases). At most one error per file; every fourth file is a control.

    Each file takes the error type used least so far that can be applied to it, which keeps the types balanced.
    """
    rng = random.Random(seed)
    order = sorted(documents)
    rng.shuffle(order)
    used = dict.fromkeys(ERRORS, 0)
    altered, cases = {}, []
    for position, stem in enumerate(order):
        document = documents[stem]
        groups = {shape["group_id"] for shape in document["shapes"]} - {None}
        case = {"file": f"{stem}.json", "image": f"{stem}.jpg", "shapes": len(document["shapes"]),
                "people": sum(shape["group_id"] is None for shape in document["shapes"]) + len(groups),
                "error": None}
        altered[stem] = document
        if position % CONTROL_EVERY != CONTROL_EVERY - 1:
            for kind in sorted(ERRORS, key=used.get):  # the least-used error that fits this file
                options = candidates(document, kind)
                rng.shuffle(options)
                done = next((made for made in (inject(document, owners[stem], kind, i) for i in options) if made), None)
                if done:
                    altered[stem], entry = done
                    case.update(entry)
                    used[kind] += 1
                    break
        cases.append(case)
    return altered, sorted(cases, key=lambda item: item["file"])


def build(output: Path = HERE, seed: int = SEED, annotations: Path = ANNOTATIONS, images: Path = IMAGES) -> dict:
    output = output.resolve()
    if output == images.resolve() or images.resolve() in output.parents:
        raise SystemExit("Refusing to write into the source photo folder.")
    documents, owners = load_documents(annotations, images)
    if not documents:
        raise SystemExit(f"No usable photos in {images}. See README.md for how to get them.")
    photos = {stem: (images / f"{stem}.jpg").read_bytes() for stem in documents}
    altered, cases = plan_errors(documents, owners, seed)
    clean = {f"{stem}.json": base.encode(document) for stem, document in documents.items()}
    errors = {f"{stem}.json": base.encode(document) for stem, document in altered.items()}
    for stem, photo in photos.items():
        clean[f"{stem}.jpg"] = errors[f"{stem}.jpg"] = photo
    archives = {name: base.write_zip(output / "archives" / name, members)
                for name, members in (("labelme_pack_clean.zip", clean), ("labelme_pack_errors.zip", errors))}
    key = {
        "seed": seed,
        "note": "Never upload this file or use it in prompts, feedback or guidelines.",
        "images": len(documents),
        "annotations": {"clean": sum(len(d["shapes"]) for d in documents.values()),
                        "errors": sum(len(d["shapes"]) for d in altered.values())},
        "errors_by_type": {kind: sum(case["error"] == kind for case in cases) for kind in ERRORS},
        "errors_injected": sum(case["error"] is not None for case in cases),
        "cases": cases,
    }
    (output / "answer_key.json").write_bytes(base.encode(key))
    (output / "verification.json").write_bytes(base.encode({
        "seed": seed, "archives_sha256": archives, "source_annotation_sha256": base.sha(annotations.read_bytes()),
        "photos_sha256": {f"{stem}.jpg": base.sha(photo) for stem, photo in sorted(photos.items())}}))
    return key


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=HERE)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--annotations", type=Path, default=ANNOTATIONS)
    parser.add_argument("--images", type=Path, default=IMAGES)
    args = parser.parse_args()
    built = build(args.output, args.seed, args.annotations, args.images)
    print(f"{built['images']} photos, {built['annotations']['clean']} annotations, "
          f"{built['errors_injected']} seeded errors")
    for name, count in built["errors_by_type"].items():
        print(f"  {name:20} {count}")
