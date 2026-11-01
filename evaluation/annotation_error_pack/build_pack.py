#!/usr/bin/env python3
"""Rebuild the annotation evaluation pack from the tracked source datasets.

Run from the repository root:

    python -B evaluation/annotation_error_pack/build_pack.py           # build, then verify, into this folder
    python -B evaluation/annotation_error_pack/build_pack.py --check   # rebuild in a temp dir; require identical bytes
    python -B evaluation/annotation_error_pack/build_pack.py --out DIR # build somewhere else (never inside datasets/)

What it does, and nothing more:

* reads datasets/coco_people_30, datasets/labelme_people_examples and datasets/labelme_l01_example without writing to them;
* checks every source file against pinned SHA-256 values and the upstream download manifests, and
  every target object against its expected identifiers and original values, stopping on any mismatch;
* applies exactly one documented change to one field of one object for each altered case;
* writes four upload ZIPs, eight comparison PNGs and answer_key.json into the output directory;
* verifies the result: counts, references, importer layout rules, image identity, single-field
  differences, reader diagnostics (existing versus new) and unchanged source hashes.

Selection is fixed in COCO_CASES and LABELME_CASES; nothing is chosen at random. Text sources are
compared after CRLF->LF normalisation because Git for Windows may check them out with CRLF line
endings; the packaged JSON is always the LF (upstream) form, so archives are identical on every
platform. ZIP entries are stored (not deflated) with fixed metadata for the same reason.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import sys
import tempfile
import zipfile
from decimal import Decimal
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
COCO_ROOT = REPO / "datasets" / "coco_people_30"
COCO_JSON = COCO_ROOT / "annotations" / "instances_people30.json"
COCO_MANIFEST = COCO_ROOT / "download_manifest.json"
LABELME_ROOT = REPO / "datasets" / "labelme_people_examples"
LABELME_MANIFEST = LABELME_ROOT / "download_manifest.json"
L01_ROOT = REPO / "datasets" / "labelme_l01_example"
L01_MANIFEST = L01_ROOT / "download_manifest.json"
PROTECTED_ROOTS = (REPO / "datasets", REPO / "PennFudanPed")

ARCHIVES = {
    ("coco", "baseline"): "coco_baseline_5.zip",
    ("coco", "altered"): "coco_errors_5.zip",
    ("labelme", "baseline"): "labelme_baseline_3.zip",
    ("labelme", "altered"): "labelme_errors_3.zip",
}
COCO_PACKAGED_JSON = "annotations/instances.json"   # images live under images/: one importer candidate each
ZIP_DATE = (1980, 1, 1, 0, 0, 0)
REVIEWER = "claude-ai-assistant (visual inspection); human reviewer confirmation pending"
COMPARISONS = ("C01", "C02", "C03", "C04", "C05", "L01", "L02", "L03")

# --------------------------------------------------------------------------- pinned sources
# SHA-256 of the exact source bytes (text after CRLF->LF normalisation). They equal the hashes in
# the upstream download manifests, which are checked as well.
COCO_JSON_SHA256 = "5831ea6bf37eaef78783b7425343f9ac2e614d3e9668bbcc6b307a6a830481d0"
IMAGE_SHA256 = {
    "000000199055.jpg": "b1dda03e61f7c502aaf30959faf6dd86e522372106a7389e5934f0e8b931c604",
    "000000001000.jpg": "24bb77a31928404e45a0454b06f6a0bd54a8db103590c9d7917288a1e0269f05",
    "000000153229.jpg": "6fa2b9017bab8ceb1635e1eff3ffd43225127d2149f0d465fdc5e30d41174003",
    "000000100624.jpg": "853ddfac5c8827aa25601a84670c8b162830421f9759ef0e21f9be980aa1ae98",
    "000000085157.jpg": "027adfd5c8d13be607a05da2098af34e7350b5f70a5bfd16f266e8c802a3a59b",
    "2011_000003.jpg": "94bc550055447e1fab3982571d7114eb4a02a868054f34cb15f95cadf3e48c9a",
    "2011_000006.jpg": "a0d04eb84ed3cd07282d61833d219c11fd6d98f53dc0135d4b3c720b26cfb244",
    "258421.jpg": "fffae741a0fd8453cf199338cf1988ad992fb38208b37aac28808e25244572a8",
}
LABELME_JSON_SHA256 = {
    "2011_000003.json": "0aa7c87bd4f11d3ab38d63fa9bc2a11640d77c0eca2a13968e852478f63021dc",
    "2011_000006.json": "8f7f13768d9b25927f3dd19d317f33807b13d571d56ec801eea57798999ec9b8",
    "258421.json": "6dff908bc14e61aee78aef2a9e83a027027f2635c6920efa14de14b0efa7268a",
}

# --------------------------------------------------------------------------- the cases
# Every identifier and expected original value is explicit; the build stops if any differs.
COCO_CASES = [
    {"case": "C01", "file": "000000199055.jpg", "image_id": 199055, "size": (480, 640),
     "annotation_id": 424921, "source_pointer": "/annotations/91",
     "expect": {"category": "person", "bbox": [143.5, 34.61, 248.25, 565.38], "parts": [43, 11]},
     "edit": {"op": "raise_bottom", "fraction": "0.2"},
     "description": "Bottom edge raised by 20% of the original box height (height x 0.8); x, y and width unchanged."},
    {"case": "C02", "file": "000000001000.jpg", "image_id": 1000, "size": (640, 480),
     "annotation_id": 1234574, "source_pointer": "/annotations/139",
     "expect": {"category": "person", "bbox": [115.16, 152.13, 83.23, 228.41], "parts": [22]},
     "edit": {"op": "lower_top", "fraction": "0.2"},
     "description": "Top edge lowered by 20% of the original box height; bottom edge, x and width unchanged."},
    {"case": "C03", "file": "000000153229.jpg", "image_id": 153229, "size": (640, 457),
     "annotation_id": 495199, "source_pointer": "/annotations/93",
     "expect": {"category": "person", "bbox": [339.4, 147.26, 155.77, 297.1], "parts": [34]},
     "edit": {"op": "extend_edge", "edge": "top", "fraction": "0.2", "minimum_px": "10"},
     "description": "Top edge moved outward (upward) by 20% of the original box height into clear background."},
    {"case": "C04", "file": "000000100624.jpg", "image_id": 100624, "size": (640, 427),
     "annotation_id": 494051, "source_pointer": "/annotations/215",
     "expect": {"category": "person", "bbox": [32.62, 51.82, 289.79, 369.42], "parts": [34]},
     "edit": {"op": "relabel", "from": "person", "to": "car"},
     "description": "category_id changed from the source person category to the source car category."},
    {"case": "C05", "file": "000000085157.jpg", "image_id": 85157, "size": (640, 480),
     "annotation_id": 430467, "source_pointer": "/annotations/169",
     "expect": {"category": "person", "bbox": [230.31, 113.13, 113.01, 148.52], "parts": [34]},
     "edit": {"op": "shift_polygon", "part": 0, "fraction": "0.1", "direction": "right"},
     "description": "Every vertex of segmentation part 0 shifted right by 10% of the image width."},
]
LABELME_CASES = [
    {"case": "L01", "file": "258421.json", "image": "258421.jpg", "size": (768, 1152),
     "status": "ready", "shape": 0,
     "expect": {"label": "person", "group_id": None, "points": 27, "shape_type": "polygon"},
     "edit": {"op": "shift_points", "axis": "x", "fraction": "0.1", "direction": "right"},
     "description": "Every point of shape 0 shifted right by 10% of the image width (76.8 px)."},
    {"case": "L02", "file": "2011_000006.json", "image": "2011_000006.jpg", "size": (500, 375),
     "status": "ready", "shape": 2,
     "expect": {"label": "person", "group_id": None, "points": 16, "shape_type": "polygon"},
     "edit": {"op": "shift_points", "axis": "y", "fraction": "0.1", "direction": "down"},
     "description": "Every point of shape 2 shifted down by 10% of the image height."},
    {"case": "L03", "file": "2011_000003.json", "image": "2011_000003.jpg", "size": (500, 338),
     "status": "ready", "shape": 0,
     "expect": {"label": "person", "group_id": None, "points": 41, "shape_type": "polygon"},
     "edit": {"op": "relabel", "from": "person", "to": "car"},
     "description": "Shape label changed from person to car."},
]

# --------------------------------------------------------------------------- review notes
# Visual inspection of each target at 2-3x zoom (crops of the source image with box and polygon).
# Status per guideline: satisfied | not_satisfied | uncertain | not_applicable.
NO_PIXEL_TRUTH = ("Box edges were compared with polygon extremes (measured) and polygon extremes with the "
                  "visible person by eye at 2-3x zoom; the exact 3-pixel tolerance was not pixel-measured "
                  "against a true person boundary.")
REVIEW = {
    "C01": {
        "applicable": [2, 5, 6, 7, 8, 9, 12], "primary": 6,
        "baseline": {2: ("satisfied", "A man standing on a skateboard."),
                     5: ("satisfied", "Top of the hair touches the box top edge."),
                     6: ("satisfied", "Both white shoes are inside the box; the box bottom is at the lowest shoe point."),
                     7: ("satisfied", "The raised right hand and the left elbow reach the side edges and are inside."),
                     8: ("not_applicable", "Fully visible; the skateboard only partly covers the right shoe, which "
                                           "is outlined by its own polygon part."),
                     9: ("satisfied", "Box equals the polygon extent on all four sides (0.00 px). " + NO_PIXEL_TRUTH),
                     12: ("not_applicable", "Evidence is clear.")},
        "altered": {6: ("not_satisfied", "The bottom edge is 113.076 px higher and excludes both feet and the "
                                         "lower legs."),
                    7: ("not_satisfied", "Covered more specifically by guideline 6: the feet are visible body "
                                         "parts outside the box.")},
        "related": ["Segmentation now extends 113.076 px below the box bottom edge (box/polygon disagreement); "
                    "the polygon itself is unchanged.",
                    "The 'area' field is unchanged; it describes the unchanged polygon."],
        "unrelated": ["No reader or geometry diagnostics in this image.",
                      "The skateboard annotation (id 638445) overlaps the person's feet; this is expected scene "
                      "overlap, not an annotation error."],
        "uncertainty": ["The person polygon is coarse (43 + 11 vertices) around the raised arm.", NO_PIXEL_TRUTH],
    },
    "C02": {
        "applicable": [2, 5, 6, 7, 8, 9, 12], "primary": 5,
        "baseline": {2: ("satisfied", "A child standing with hands on hips, wearing a cap."),
                     5: ("satisfied", "The top of the cap touches the box top edge."),
                     6: ("satisfied", "Both shoes are inside the box bottom edge."),
                     7: ("satisfied", "Both elbows reach the side edges and are inside."),
                     8: ("not_applicable", "Fully visible; a neighbour's arm enters the box from the right."),
                     9: ("satisfied", "Box equals the polygon extent on all four sides (0.00 px). " + NO_PIXEL_TRUTH),
                     12: ("not_applicable", "Evidence is clear.")},
        "altered": {5: ("not_satisfied", "The top edge is 45.682 px lower and excludes the whole head and cap "
                                         "(the head is about 35-40 px tall)."),
                    7: ("not_satisfied", "Covered more specifically by guideline 5: the head is a visible body "
                                         "part outside the box.")},
        "related": ["Segmentation now extends 45.682 px above the box top edge (box/polygon disagreement); the "
                    "polygon itself is unchanged.",
                    "The 'area' field is unchanged; it describes the unchanged polygon."],
        "unrelated": ["Source /annotations/143 (person 1303041) and /annotations/144 (person 1304800) carry "
                      "repeated_polygon_vertex and self_intersecting_polygon diagnostics; they are not the target.",
                      "Crowded group scene: several person boxes overlap the target box."],
        "uncertainty": ["The target polygon is coarse (22 vertices) and cuts a V between the legs.", NO_PIXEL_TRUTH],
    },
    "C03": {
        "applicable": [2, 5, 6, 7, 8, 9, 12], "primary": 9,
        "baseline": {2: ("satisfied", "A player in a green shirt and red cap, seen from behind."),
                     5: ("satisfied", "The top of the cap touches the box top edge."),
                     6: ("satisfied", "The standing foot is at the bottom edge and the raised foot at the right edge."),
                     7: ("satisfied", "Arms and the raised leg are inside the box."),
                     8: ("not_applicable", "Fully visible."),
                     9: ("satisfied", "Box equals the polygon extent on all four sides (0.00 px). " + NO_PIXEL_TRUTH),
                     12: ("not_applicable", "Evidence is clear.")},
        "altered": {9: ("not_satisfied", "The top edge is 59.42 px above the top of the cap, far more than the "
                                         "proposed 3-pixel tolerance; the added strip is clear background.")},
        "related": ["The box now extends 59.42 px above the top of the polygon (box/polygon disagreement); the "
                    "polygon itself is unchanged."],
        "unrelated": ["EXISTING on the target itself (not introduced by this case): source /annotations/93 has "
                      "repeated_polygon_vertex and self_intersecting_polygon diagnostics near the left leg. They "
                      "are present in both the baseline and the altered archive.",
                      "Because of that self-intersection, guideline 10 (polygon follows the person) is uncertain "
                      "for this target in both versions; guideline 10 is not tested by this case."],
        "uncertainty": [NO_PIXEL_TRUTH],
    },
    "C04": {
        "applicable": [1, 2, 3, 4, 12], "primary": 4,
        "baseline": {1: ("satisfied", "A visible person with a person label is in scope."),
                     2: ("satisfied", "A woman talking on a phone, cut off by the bottom image edge."),
                     3: ("not_applicable", "The object is not a car."),
                     4: ("satisfied", "Labelled person and visibly a person."),
                     12: ("not_applicable", "Evidence is clear.")},
        "altered": {4: ("not_satisfied", "Labelled car, but the outlined object is visibly a person."),
                    1: ("satisfied", "Still in scope: the guidelines apply to image content, so the car label "
                                     "does not remove the person from evaluation.")},
        "related": ["Only the category changes; the geometry still outlines a person, so a label-versus-content "
                    "check is the only way to detect it.",
                    "The car category already exists in the source categories; no category was created."],
        "unrelated": ["No reader or geometry diagnostics in this image.",
                      "BASELINE CONFLICT, recorded not resolved: the target's box and polygon include the worn "
                      "backpack on the left, which also has its own annotation (backpack 1170623). The guidelines "
                      "do not say whether worn items count as part of the person, so guidelines 9 and 10 are "
                      "uncertain for this target. They are not tested by this case.",
                      "The woman is cut off by the bottom image edge (guideline 8 applies; guideline 6 does not)."],
        "uncertainty": ["Guideline 9/10 status depends on a worn-item decision the guidelines do not make."],
    },
    "C05": {
        "applicable": [2, 8, 10, 11, 12], "primary": 10,
        "baseline": {2: ("satisfied", "A seated woman behind a table, talking."),
                     8: ("satisfied", "The lower body is hidden by the table and glasses; the polygon stops at the "
                                      "table and invents no hidden anatomy."),
                     10: ("satisfied", "The polygon follows the visible head, hair, arms and hands (coarse, "
                                       "34 vertices)."),
                     11: ("satisfied", "Single polygon part outlining this person."),
                     12: ("not_applicable", "Evidence is clear.")},
        "altered": {10: ("not_satisfied", "The whole outline is displaced 64 px to the right of the person."),
                    11: ("uncertain", "The displaced part now outlines mostly background and part of the "
                                      "neighbouring person; whether that counts as outlining a different person "
                                      "is a reviewer judgement.")},
        "related": ["The polygon now extends 64 px beyond the box right edge and leaves a 64 px gap inside the box "
                    "left edge (box/polygon disagreement); the box is unchanged.",
                    "The 'area' field is unchanged; a translation does not change polygon area."],
        "unrelated": ["Source /annotations/167 (chair 387255) and /annotations/174 (bowl 709624) carry "
                      "repeated_polygon_vertex and self_intersecting_polygon diagnostics; they are not the target.",
                      "Crowded dining scene with many overlapping annotations."],
        "uncertainty": ["The baseline polygon is coarse; its fit was judged by eye."],
    },
    "L01": {
        "applicable": [2, 8, 10, 11, 12], "primary": 10,
        "baseline": {2: ("satisfied", "The target is the man on the right, wearing a white shirt and tan trousers."),
                     8: ("satisfied", "Where the two people overlap, the outline follows the visible portion of the target."),
                     10: ("satisfied", "The coarse 27-vertex outline follows the target's hair, clothing, visible arm and shoes."),
                     11: ("satisfied", "One polygon with group_id null outlines the target person."),
                     12: ("not_applicable", "The deliberate horizontal displacement is visually distinguishable.")},
        "altered": {10: ("not_satisfied", "The outline is translated right by 76.8 px onto the track and background, away from the target."),
                    11: ("uncertain", "The shifted outline mainly covers background; whether background alone violates guideline 11 is not established.")},
        "related": ["The polygon-derived box moves right by 76.8 px; no supplied bounding box is edited."],
        "unrelated": ["/shapes/1 outlines the woman on the left and is unchanged; she is not the evaluation target.",
                      "Reader/geometry diagnostics for this new pair must be checked by the owner-run verification commands."],
        "uncertainty": ["Harkamal Singh confirmed the baseline fit and altered failure on 2026-10-31. Minor inaccuracies remain around the overlapping arms and lower body; no pixel-exact fit is claimed.",
                        "The woman's outline was also visually reviewed: generally accurate, with minor boundary ambiguity where the two people overlap.",
                        "The primary result concerns guideline 10 only, not whole-image annotation quality."],
        "visual_verification": {"status": "human_confirmed", "reviewer": "Harkamal Singh",
                                "review_date": "2026-10-31", "primary_guideline": 10,
                                "method": "Human review of .cache/visuals/L01-review.png, a 2x crop showing the original green outline and the red outline shifted right by 76.8 px. Canonical builder output and automated checks remain unverified."},
    },
    "L02": {
        "applicable": [2, 8, 10, 11, 12], "primary": 10,
        "baseline": {2: ("satisfied", "A seated woman in a green top on a sofa."),
                     8: ("satisfied", "Partly hidden by the sofa arm and her neighbour; only visible parts are "
                                      "outlined."),
                     10: ("satisfied", "The coarse 16-point outline follows her head, hair, top, arm and jeans."),
                     11: ("satisfied", "Single shape with group_id null."),
                     12: ("not_applicable", "Evidence is clear.")},
        "altered": {10: ("not_satisfied", "The whole outline is displaced 37.5 px downward onto the sofa.")},
        "related": ["The polygon-derived box (LabelMe stores no box) moves down by the same 37.5 px."],
        "unrelated": ["/shapes/0 and /shapes/1 (the other two women) carry self_intersecting_polygon diagnostics; "
                      "/shapes/3 (chair) carries polygon_outside_image. None is the target.",
                      "The image contains an __ignore__ region (/shapes/5) and grouped sofa parts (group_id 0)."],
        "uncertainty": ["The baseline outline is coarse; its fit was judged by eye."],
    },
    "L03": {
        "applicable": [1, 2, 3, 4, 12], "primary": 4,
        "baseline": {1: ("satisfied", "A visible person with a person label is in scope."),
                     2: ("satisfied", "A crouching man, fully visible from hat to sandals."),
                     3: ("not_applicable", "The object is not a car."),
                     4: ("satisfied", "Labelled person and visibly a person."),
                     12: ("not_applicable", "Evidence is clear.")},
        "altered": {4: ("not_satisfied", "Labelled car, but the outlined object is visibly a person."),
                    1: ("satisfied", "Still in scope: the car label does not remove the person from evaluation.")},
        "related": ["Only the label changes; the outline still fits a person."],
        "unrelated": ["No reader or geometry diagnostics in 2011_000003.",
                      "/shapes/2 is a 4-point person part grouped (group_id 0) with /shapes/1 near the bottle; it "
                      "plausibly outlines that man's hand. Not the target; not verified."],
        "uncertainty": [],
    },
}


class PackError(RuntimeError):
    """A source, identifier, layout or output check failed; nothing is guessed or repaired."""


# --------------------------------------------------------------------------- helpers
def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def text_bytes(path: Path) -> bytes:
    """Source text bytes in their canonical LF form (Git for Windows may add CRLF)."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def dec(value) -> Decimal:
    """Exact decimal of a JSON number as written (repr is the shortest round-trip form)."""
    return Decimal(repr(value)) if isinstance(value, float) else Decimal(value)


def num(value: Decimal):
    """Back to a JSON number without binary-float noise; integral values stay ints."""
    return int(value) if value == value.to_integral_value() else float(value)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PackError(message)


def coco_dumps(document) -> bytes:
    return (json.dumps(document) + "\n").encode("utf-8")        # the source file's own style


def labelme_dumps(document) -> bytes:
    return json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8")   # upstream style


def snapshot(roots) -> dict:
    """Raw SHA-256 of every file under the given roots (used to prove sources were not modified)."""
    found = {}
    for root in roots:
        if root.is_dir():
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    found[path.relative_to(REPO).as_posix()] = sha256(path.read_bytes())
    return found


# --------------------------------------------------------------------------- source checks
def load_sources():
    for path in (COCO_JSON, COCO_MANIFEST, LABELME_MANIFEST, L01_MANIFEST):
        require(path.is_file(), f"Missing source file: {path}")
    coco_raw = text_bytes(COCO_JSON)
    require(sha256(coco_raw) == COCO_JSON_SHA256,
            f"{COCO_JSON.name} differs from the pinned version (SHA-256 {sha256(coco_raw)}).")
    coco_manifest = json.loads(text_bytes(COCO_MANIFEST))
    require(coco_manifest.get("subset_annotation_sha256") == COCO_JSON_SHA256,
            "The COCO download manifest does not record the pinned annotation hash.")
    manifest_images = {s["file_name"]: s["sha256"] for s in coco_manifest.get("samples", [])}
    labelme_manifest = {f["file"]: f["sha256"] for f in json.loads(text_bytes(LABELME_MANIFEST))["files"]}
    l01_manifest = {f["file"]: f["sha256"] for f in json.loads(text_bytes(L01_MANIFEST))["files"]}

    images = {}
    for case in COCO_CASES:
        path = COCO_ROOT / "images" / case["file"]
        require(path.is_file(), f"Missing COCO image: {path}")
        data = path.read_bytes()
        require(sha256(data) == IMAGE_SHA256[case["file"]] == manifest_images.get(case["file"]),
                f"COCO image {case['file']} differs from the pinned/manifest SHA-256.")
        images[case["file"]] = data
    labelme = {}
    for case in LABELME_CASES:
        root = L01_ROOT if case["case"] == "L01" else LABELME_ROOT
        manifest = l01_manifest if case["case"] == "L01" else labelme_manifest
        jpath, ipath = root / case["file"], root / case["image"]
        require(jpath.is_file(), f"Missing LabelMe annotation: {jpath}")
        require(ipath.is_file(), f"Missing LabelMe image: {ipath}")
        raw, data = text_bytes(jpath), ipath.read_bytes()
        require(sha256(raw) == LABELME_JSON_SHA256[case["file"]] == manifest.get(case["file"]),
                f"LabelMe annotation {case['file']} differs from the pinned/manifest SHA-256.")
        require(sha256(data) == IMAGE_SHA256[case["image"]] == manifest.get(case["image"]),
                f"LabelMe image {case['image']} differs from the pinned/manifest SHA-256.")
        require(labelme_dumps(json.loads(raw)) == raw,
                f"{case['file']} no longer round-trips in the upstream JSON style; an edit would reformat it.")
        labelme[case["file"]] = (raw, data)
        images[case["image"]] = data
    return json.loads(coco_raw), images, labelme


def coco_category_ids(document) -> dict:
    by_name = {}
    for category in document["categories"]:
        by_name.setdefault(category["name"], []).append(category["id"])
    ids = {}
    for name in ("person", "car"):
        require(len(by_name.get(name, [])) == 1, f"COCO categories must define exactly one '{name}'.")
        ids[name] = by_name[name][0]
    return ids


# --------------------------------------------------------------------------- COCO edits
def coco_edit(case, annotation, categories, size):
    """Apply the case's single edit in place; return (field, before, after, transformation)."""
    edit, (width, height) = case["edit"], size
    if edit["op"] == "relabel":
        before, after = categories[edit["from"]], categories[edit["to"]]
        annotation["category_id"] = after
        return "category_id", before, after, {
            "type": "relabel", "from_category": {"id": before, "name": edit["from"]},
            "to_category": {"id": after, "name": edit["to"]}, "pixel_displacement": None}
    if edit["op"] == "shift_polygon":
        part = annotation["segmentation"][edit["part"]]
        delta = dec(width) * Decimal(edit["fraction"])
        xs = [dec(v) for v in part[0::2]]
        feasible = {name: all(Decimal(0) <= x + sign * delta <= dec(width) for x in xs)
                    for name, sign in (("right", 1), ("left", -1))}
        require(feasible[edit["direction"]],
                f"{case['case']}: shifting {edit['direction']} by {delta} px would move vertices outside the image.")
        sign = 1 if edit["direction"] == "right" else -1
        before = copy.deepcopy(annotation["segmentation"])
        annotation["segmentation"][edit["part"]] = [num(dec(v) + sign * delta) if i % 2 == 0 else v
                                                    for i, v in enumerate(part)]
        return "segmentation", before, copy.deepcopy(annotation["segmentation"]), {
            "type": "translate_polygon_part", "part_index": edit["part"], "axis": "x",
            "direction": edit["direction"], "fraction_of_image_width": edit["fraction"],
            "pixel_displacement": num(sign * delta), "vertex_count": len(part) // 2,
            "vertex_order_preserved": True,
            "directions_keeping_all_vertices_inside": sorted(n for n, ok in feasible.items() if ok)}
    x, y, w, h = (dec(v) for v in annotation["bbox"])
    fraction = Decimal(edit["fraction"])
    before = list(annotation["bbox"])
    if edit["op"] == "raise_bottom":
        delta = h * fraction
        new = [x, y, w, h - delta]
        record = {"type": "move_bottom_edge_up", "edge": "bottom", "pixel_displacement": num(delta),
                  "rule": "height = 0.8 x original height; x, y and width unchanged"}
    elif edit["op"] == "lower_top":
        delta = h * fraction
        new = [x, y + delta, w, h - delta]
        record = {"type": "move_top_edge_down", "edge": "top", "pixel_displacement": num(delta),
                  "rule": "y = original y + 0.2 x original height; height = 0.8 x original height"}
    elif edit["op"] == "extend_edge":
        edge = edit["edge"]
        require(edge in ("top", "bottom", "left", "right"), f"{case['case']}: unknown edge {edge!r}.")
        delta = (h if edge in ("top", "bottom") else w) * fraction
        require(delta >= Decimal(edit["minimum_px"]),
                f"{case['case']}: the extension of {delta} px is under the {edit['minimum_px']} px minimum.")
        new = {"top": [x, y - delta, w, h + delta], "bottom": [x, y, w, h + delta],
               "left": [x - delta, y, w + delta, h], "right": [x, y, w + delta, h]}[edge]
        nx, ny, nw, nh = new
        require(nx >= 0 and ny >= 0 and nx + nw <= dec(width) and ny + nh <= dec(height),
                f"{case['case']}: extending the {edge} edge by {delta} px would leave the image.")
        record = {"type": "move_edge_outward", "edge": edge, "pixel_displacement": num(delta),
                  "rule": f"{edge} edge moved outward by 0.2 x the original box "
                          f"{'height' if edge in ('top', 'bottom') else 'width'}"}
    else:
        raise PackError(f"Unknown COCO edit {edit['op']!r}")
    require(new[2] > 0 and new[3] > 0, f"{case['case']}: the edited box must keep a positive width and height.")
    annotation["bbox"] = [num(v) for v in new]
    return "bbox", before, list(annotation["bbox"]), record


def build_coco(source, images):
    categories = coco_category_ids(source)
    ids = [case["image_id"] for case in COCO_CASES]
    require(len(set(ids)) == 5 and len({c["file"] for c in COCO_CASES}) == 5, "COCO cases need five distinct images.")
    records = {im["id"]: im for im in source["images"]}
    for case in COCO_CASES:
        image = records.get(case["image_id"])
        require(image is not None, f"{case['case']}: image id {case['image_id']} is not in the source.")
        require(image["file_name"] == case["file"] and (image["width"], image["height"]) == case["size"],
                f"{case['case']}: image record {case['image_id']} has an unexpected file name or size.")
    baseline = dict(source)
    baseline["images"] = [im for im in source["images"] if im["id"] in ids]
    baseline["annotations"] = [a for a in source["annotations"] if a["image_id"] in ids]
    baseline = copy.deepcopy(baseline)
    altered = copy.deepcopy(baseline)
    results = []
    for case in COCO_CASES:
        position = int(case["source_pointer"].rsplit("/", 1)[1])
        require(position < len(source["annotations"]) and source["annotations"][position].get("id") == case["annotation_id"],
                f"{case['case']}: {case['source_pointer']} is not annotation {case['annotation_id']}.")
        matches = [i for i, a in enumerate(altered["annotations"]) if a["id"] == case["annotation_id"]]
        require(len(matches) == 1, f"{case['case']}: annotation {case['annotation_id']} must occur exactly once.")
        target, expect = altered["annotations"][matches[0]], case["expect"]
        require(target["image_id"] == case["image_id"], f"{case['case']}: the annotation belongs to another image.")
        require(target["category_id"] == categories[expect["category"]], f"{case['case']}: the target is not a person.")
        require(target["bbox"] == expect["bbox"], f"{case['case']}: bbox {target['bbox']} differs from {expect['bbox']}.")
        require(isinstance(target["segmentation"], list)
                and [len(p) // 2 for p in target["segmentation"]] == expect["parts"],
                f"{case['case']}: polygon parts differ from the expected vertex counts {expect['parts']}.")
        require(target.get("iscrowd") == 0, f"{case['case']}: crowd annotations are not used.")
        field, before, after, transformation = coco_edit(case, target, categories, case["size"])
        results.append({"case": case, "packaged_index": matches[0], "field": field, "before": before,
                        "after": after, "transformation": transformation, "categories": categories})
    files = {"baseline": {COCO_PACKAGED_JSON: coco_dumps(baseline)},
             "altered": {COCO_PACKAGED_JSON: coco_dumps(altered)}}
    for case in COCO_CASES:
        for variant in files:
            files[variant][f"images/{case['file']}"] = images[case["file"]]
    return files, results, baseline, altered


# --------------------------------------------------------------------------- LabelMe edits
def build_labelme(labelme):
    files = {"baseline": {}, "altered": {}}
    results = []
    require(len({c["image"] for c in LABELME_CASES}) == 3, "LabelMe cases need three distinct images.")
    for case in LABELME_CASES:
        raw, image = labelme[case["file"]]
        document = json.loads(raw)
        require(document.get("imagePath") == case["image"] and document.get("imageData") is None,
                f"{case['case']}: {case['file']} must reference the external image {case['image']}.")
        require((document.get("imageWidth"), document.get("imageHeight")) == case["size"],
                f"{case['case']}: {case['file']} declares an unexpected image size.")
        files["baseline"][case["file"]] = raw
        files["baseline"][case["image"]] = image
        files["altered"][case["image"]] = image
        if case["status"] == "blocked":
            require(all(shape.get("label") != "person" for shape in document["shapes"]),
                    f"{case['case']}: {case['file']} now contains a person shape; re-evaluate the documented blocker.")
            files["altered"][case["file"]] = raw
            results.append({"case": case, "field": None, "document": document})
            continue
        shapes = document["shapes"]
        require(case["shape"] < len(shapes), f"{case['case']}: shape {case['shape']} is missing.")
        shape, expect = shapes[case["shape"]], case["expect"]
        require(shape.get("label") == expect["label"] and shape.get("group_id") == expect["group_id"]
                and shape.get("shape_type") == expect["shape_type"] and len(shape.get("points", [])) == expect["points"],
                f"{case['case']}: shape {case['shape']} differs from the expected person polygon.")
        edit, (width, height) = case["edit"], case["size"]
        baseline_document = json.loads(raw)
        if edit["op"] == "relabel":
            field, before, after = "label", shape["label"], edit["to"]
            shape["label"] = after
            transformation = {"type": "relabel", "from": before, "to": after, "pixel_displacement": None}
        else:
            axis = 0 if edit["axis"] == "x" else 1
            limit = dec(width if axis == 0 else height)
            delta = limit * Decimal(edit["fraction"])
            names = ("right", "left") if axis == 0 else ("down", "up")
            feasible = {name: all(Decimal(0) <= dec(p[axis]) + sign * delta <= limit for p in shape["points"])
                        for name, sign in zip(names, (1, -1))}
            require(edit["direction"] in names, f"{case['case']}: direction {edit['direction']!r} does not fit the axis.")
            require(feasible[edit["direction"]],
                    f"{case['case']}: shifting {edit['direction']} by {delta} px would move points outside the image.")
            sign = 1 if edit["direction"] == names[0] else -1
            field, before = "points", copy.deepcopy(shape["points"])
            shape["points"] = [[num(dec(c) + sign * delta) if i == axis else c for i, c in enumerate(p)]
                               for p in shape["points"]]
            after = copy.deepcopy(shape["points"])
            transformation = {"type": "translate_polygon", "axis": edit["axis"], "direction": edit["direction"],
                              "fraction_of_image_" + ("width" if axis == 0 else "height"): edit["fraction"],
                              "pixel_displacement": num(sign * delta), "vertex_count": len(after),
                              "vertex_order_preserved": True,
                              "directions_keeping_all_points_inside": sorted(n for n, ok in feasible.items() if ok)}
        files["altered"][case["file"]] = labelme_dumps(document)
        results.append({"case": case, "field": field, "before": before, "after": after,
                        "transformation": transformation, "document": baseline_document, "altered_document": document})
    return files, results


# --------------------------------------------------------------------------- answer key
def _assessment(review, variant):
    """Per-guideline status for the target; altered statuses override the baseline ones."""
    rows = {n: {"status": s, "note": note} for n, (s, note) in review["baseline"].items()}
    if variant == "altered":
        for n, (s, note) in review["altered"].items():
            rows[n] = {"status": s, "note": note}
    return {str(n): rows[n] for n in review["applicable"] if n in rows}


def _expected_result(assessment, primary):
    status = assessment.get(str(primary), {}).get("status")
    return {"satisfied": "pass", "not_satisfied": "fail", "uncertain": "uncertain"}.get(status, "not_determined")


def _source_issue_pointers(texts, source, baseline):
    """Add packaged pointers for '/annotations/N' source pointers mentioned in a note."""
    import re
    mapping = {}
    ids = [a["id"] for a in baseline["annotations"]]
    for text in texts:
        for match in re.findall(r"/annotations/(\d+)", text):
            source_id = source["annotations"][int(match)]["id"]
            if source_id in ids:
                mapping[f"/annotations/{match}"] = f"/annotations/{ids.index(source_id)}"
    return mapping


def build_answer_key(coco_results, labelme_results, coco_source, coco_baseline, images):
    entries = []
    zips = {fmt: {v: ARCHIVES[(fmt, v)] for v in ("baseline", "altered")} for fmt in ("coco", "labelme")}
    for result in coco_results:
        case, review = result["case"], REVIEW[result["case"]["case"]]
        target = coco_source["annotations"][int(case["source_pointer"].rsplit("/", 1)[1])]
        categories = result["categories"]
        names = {v: k for k, v in categories.items()}
        for variant in ("baseline", "altered"):
            altered = variant == "altered"
            assessment = _assessment(review, variant)
            current = names[result["after"]] if altered and result["field"] == "category_id" else "person"
            entries.append({
                "case_id": f"{case['case']}-{variant}",
                "variant": variant,
                "paired_case_id": f"{case['case']}-{'baseline' if altered else 'altered'}",
                "status": "ready",
                "annotation_format": "coco",
                "zip_file": zips["coco"][variant],
                "annotation_file_in_zip": COCO_PACKAGED_JSON,
                "image_file": case["file"],
                "image_file_in_zip": f"images/{case['file']}",
                "image_sha256": IMAGE_SHA256[case["file"]],
                "source_image_id": case["image_id"],
                "source_annotation_id": case["annotation_id"],
                "source_json_pointer": case["source_pointer"],
                "packaged_json_pointer": f"/annotations/{result['packaged_index']}",
                "labelme_group_id": None,
                "original_target_label": "person",
                "current_target_label": current,
                "category_ids_from_source": categories,
                "edited_field": result["field"] if altered else None,
                "original_field_value": result["before"] if altered else None,
                "modified_field_value": result["after"] if altered else None,
                "transformation": ({"description": case["description"], **result["transformation"]}
                                   if altered else None),
                "applicable_guidelines": review["applicable"],
                "primary_guideline": review["primary"],
                "expected_target_result": _expected_result(assessment, review["primary"]),
                "guideline_assessment": assessment,
                "related_findings_from_edit": review["related"] if altered else [],
                "known_unrelated_issues": review["unrelated"],
                "known_unrelated_issue_packaged_pointers": _source_issue_pointers(review["unrelated"], coco_source, coco_baseline),
                "visual_verification": {"status": "verified", "reviewer": REVIEWER,
                                        "method": "2-3x zoomed source crop with box and polygon; comparisons/" +
                                                  case["case"] + ".png"},
                "reviewer": REVIEWER,
                "uncertainty_and_limitations": review["uncertainty"],
                "whole_image_reviewed": False,
            })
    for result in labelme_results:
        case, review = result["case"], REVIEW[result["case"]["case"]]
        blocked = case["status"] == "blocked"
        for variant in ("baseline", "altered"):
            altered = variant == "altered"
            entry = {
                "case_id": f"{case['case']}-{variant}",
                "variant": variant,
                "paired_case_id": f"{case['case']}-{'baseline' if altered else 'altered'}",
                "status": "blocked" if blocked else "ready",
                "annotation_format": "labelme",
                "zip_file": zips["labelme"][variant],
                "annotation_file_in_zip": case["file"],
                "image_file": case["image"],
                "image_file_in_zip": case["image"],
                "image_sha256": IMAGE_SHA256[case["image"]],
                "source_image_id": None,
                "source_annotation_id": None,
                "applicable_guidelines": review["applicable"],
                "primary_guideline": review["primary"],
                "reviewer": REVIEWER,
                "whole_image_reviewed": False,
            }
            if blocked:
                entry.update({
                    "required_case": case["required"], "blocker": case["blocker"],
                    "source_json_pointer": None, "packaged_json_pointer": None, "labelme_group_id": None,
                    "original_target_label": None, "current_target_label": None,
                    "edited_field": None, "original_field_value": None, "modified_field_value": None,
                    "transformation": None, "expected_target_result": "not_applicable_blocked",
                    "guideline_assessment": {}, "related_findings_from_edit": [],
                    "packaged_annotation_identical_to_baseline": True,
                    "known_unrelated_issues": review["unrelated"],
                    "visual_verification": {"status": "verified_no_person_present", "reviewer": REVIEWER,
                                            "method": "source overlay and comparisons/L01.png"},
                    "uncertainty_and_limitations": review["uncertainty"],
                })
            else:
                assessment = _assessment(review, variant)
                shape = result["document"]["shapes"][case["shape"]]
                pointer = f"/shapes/{case['shape']}"
                entry.update({
                    "source_json_pointer": pointer, "packaged_json_pointer": pointer,
                    "labelme_group_id": shape.get("group_id"),
                    "original_target_label": "person",
                    "current_target_label": result["after"] if altered and result["field"] == "label" else "person",
                    "edited_field": result["field"] if altered else None,
                    "original_field_value": result["before"] if altered else None,
                    "modified_field_value": result["after"] if altered else None,
                    "transformation": ({"description": case["description"], **result["transformation"]}
                                       if altered else None),
                    "expected_target_result": _expected_result(assessment, review["primary"]),
                    "guideline_assessment": assessment,
                    "related_findings_from_edit": review["related"] if altered else [],
                    "known_unrelated_issues": review["unrelated"],
                    "visual_verification": {"status": "verified", "reviewer": REVIEWER,
                                            "method": "3x zoomed source crop with polygon; comparisons/" +
                                                      case["case"] + ".png"},
                    "uncertainty_and_limitations": review["uncertainty"],
                })
                if "visual_verification" in review:
                    entry["visual_verification"] = copy.deepcopy(review["visual_verification"])
                    entry["reviewer"] = review["visual_verification"]["reviewer"]
            entries.append(entry)
    order = {f"{c}-{v}": i for i, (v, c) in enumerate(
        [(v, c) for v in ("baseline", "altered") for c in ("C01", "C02", "C03", "C04", "C05")]
        + [(v, c) for v in ("baseline", "altered") for c in ("L01", "L02", "L03")])}
    entries.sort(key=lambda e: order[e["case_id"]])
    return {
        "description": "Answer key for the PRISM annotation evaluation pack. Keep it out of upload archives "
                       "and out of model prompts during evaluation.",
        "guidelines": "guidelines.txt (team-authored development guidelines, not Samsung-provided requirements)",
        "coordinate_systems": {"coco": "zero-based continuous pixels, bbox [x, y, width, height]",
                               "labelme": "zero-based continuous pixels, points [[x, y], ...]"},
        "entries": entries,
    }


# --------------------------------------------------------------------------- comparisons
def _font(size):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _dashed(draw, points, fill, width, dash=8, gap=6):
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        if not length:
            continue
        ux, uy, at = (x2 - x1) / length, (y2 - y1) / length, 0.0
        while at < length:
            stop = min(at + dash, length)
            draw.line([(x1 + ux * at, y1 + uy * at), (x1 + ux * stop, y1 + uy * stop)], fill=fill, width=width)
            at += dash + gap


def _wrap(draw, text, font, width):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and draw.textlength(trial, font=font) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + ([line] if line else [])


def _panel(image_bytes, scale, others, target, ghost, tag):
    from PIL import Image, ImageDraw
    with Image.open(io.BytesIO(image_bytes)) as source:
        base = source.convert("RGB")
    base = base.resize((round(base.width * scale), round(base.height * scale)), Image.LANCZOS)
    draw = ImageDraw.Draw(base)
    s = lambda p: (p[0] * scale, p[1] * scale)                     # noqa: E731
    for kind, geometry in others:                                  # context: other objects, thin grey
        if kind == "box":
            x0, y0, x1, y1 = geometry
            draw.rectangle([*s((x0, y0)), *s((x1, y1))], outline=(170, 170, 170), width=1)
        else:
            draw.line([s(p) for p in geometry] + [s(geometry[0])], fill=(170, 170, 170), width=1)
    for kind, geometry in ghost:                                   # original geometry, dashed white
        if kind == "box":
            x0, y0, x1, y1 = geometry
            outline = [s((x0, y0)), s((x1, y0)), s((x1, y1)), s((x0, y1)), s((x0, y0))]
        else:
            outline = [s(p) for p in geometry] + [s(geometry[0])]
        _dashed(draw, outline, (255, 255, 255), 2)
    color = target["color"]
    for kind, geometry in target["geometry"]:
        if kind == "box":
            x0, y0, x1, y1 = geometry
            draw.rectangle([*s((x0, y0)), *s((x1, y1))], outline=(0, 0, 0), width=5)
            draw.rectangle([*s((x0, y0)), *s((x1, y1))], outline=color, width=3)
        else:
            points = [s(p) for p in geometry] + [s(geometry[0])]
            draw.line(points, fill=(0, 0, 0), width=5)
            draw.line(points, fill=target["poly_color"], width=3)
    if tag:
        # Above the union of the current and original geometry, so the tag never hides the change.
        points = [p for _, g in target["geometry"] + ghost for p in ((g[0], g[1]), (g[2], g[3]))
                  if _ == "box"] + [p for _, g in target["geometry"] + ghost if _ == "polygon" for p in g]
        anchor = (min(p[0] for p in points), min(p[1] for p in points)) if points else tag[0]
        font = _font(15)
        x, y = s(anchor)
        w = draw.textlength(tag[1], font=font) + 10
        x = min(max(0, x), base.width - w)
        y = max(0, y - 24)
        draw.rectangle([x, y, x + w, y + 20], fill=color)
        draw.text((x + 5, y + 2), tag[1], fill=(0, 0, 0), font=font)
    return base


def _coco_geometry(annotation):
    x, y, w, h = annotation["bbox"]
    shapes = [("box", (x, y, x + w, y + h))]
    shapes += [("polygon", list(zip(part[0::2], part[1::2]))) for part in annotation["segmentation"]]
    return shapes


def render_comparison(path, title, lines, image_bytes, left, right,
                      labels=("BASELINE (unchanged)", "ALTERED (deliberate error)")):
    from PIL import Image, ImageDraw
    with Image.open(io.BytesIO(image_bytes)) as probe:
        width, height = probe.size
    scale = min(2.0, 720 / max(width, height))
    panels = [_panel(image_bytes, scale, *left), _panel(image_bytes, scale, *right)]
    gutter, margin = 24, 16
    canvas_w = panels[0].width * 2 + gutter + margin * 2
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    title_font, label_font, body_font = _font(22), _font(18), _font(15)
    body = [line for text in lines for line in _wrap(measure, text, body_font, canvas_w - margin * 2)]
    top = margin + 34 + 28
    canvas = Image.new("RGB", (canvas_w, top + panels[0].height + 16 + 22 * len(body) + margin), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    draw.text((margin, margin), title, fill=(0, 0, 0), font=title_font)
    for index, (panel, label) in enumerate(zip(panels, labels)):
        x = margin + index * (panel.width + gutter)
        draw.text((x, margin + 34), label, fill=(0, 110, 0) if index == 0 else (180, 0, 0), font=label_font)
        canvas.paste(panel, (x, top))
    y = top + panels[0].height + 12
    for line in body:
        draw.text((margin, y), line, fill=(20, 20, 20), font=body_font)
        y += 22
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=False)
    atomic_write(path, buffer.getvalue())


def render_all(out_dir, coco_results, labelme_results, coco_baseline, coco_altered, images):
    green, red, cyan, magenta = (40, 220, 60), (255, 40, 40), (0, 230, 230), (255, 60, 220)
    legend = ("Target object: thick green box (baseline) / red box (altered); its polygon parts in cyan / magenta. "
              "Dashed white = the target's original geometry, shown on the altered side for reference. "
              "Thin grey = other annotations in the image (context only).")
    legend_labelme = ("Target shape: thick green polygon (baseline) / red polygon (altered). Dashed white = the target's "
                      "original points, shown on the altered side for reference. Thin grey = other shapes in the "
                      "image (context only).")
    for result in coco_results:
        case = result["case"]
        image_id = case["image_id"]
        index = result["packaged_index"]
        base_ann, alt_ann = coco_baseline["annotations"][index], coco_altered["annotations"][index]
        others = [shape for a in coco_baseline["annotations"] if a["image_id"] == image_id and a["id"] != case["annotation_id"]
                  for shape in _coco_geometry(a)]
        names = {v: k for k, v in result["categories"].items()}
        base_label, alt_label = names[base_ann["category_id"]], names.get(alt_ann["category_id"], "?")
        ghost = []
        if result["field"] == "bbox":
            ghost = [_coco_geometry(base_ann)[0]]
        elif result["field"] == "segmentation":
            ghost = [("polygon", list(zip(p[0::2], p[1::2]))) for p in base_ann["segmentation"]]
        t = result["transformation"]
        if result["field"] == "bbox":
            change = (f"bbox {result['before']} -> {result['after']} ({t['rule']}; {t['edge']} edge moved "
                      f"{abs(t['pixel_displacement'])} px).")
        elif result["field"] == "segmentation":
            change = (f"segmentation part {t['part_index']} translated {t['direction']} by "
                      f"{abs(t['pixel_displacement'])} px (10% of image width); {t['vertex_count']} vertices, "
                      f"order preserved; bbox unchanged.")
        else:
            change = f"category_id {result['before']} (person) -> {result['after']} (car); geometry unchanged."
        render_comparison(
            out_dir / f"{case['case']}.png",
            f"{case['case']} | COCO {case['file']} | target annotation id {case['annotation_id']}",
            [f"Deliberate change: {case['description']}", change, legend],
            images[case["file"]],
            (others, {"geometry": _coco_geometry(base_ann), "color": green, "poly_color": cyan}, [],
             (base_ann["bbox"][:2], f"{base_label} #{case['annotation_id']}")),
            (others, {"geometry": _coco_geometry(alt_ann), "color": red, "poly_color": magenta}, ghost,
             (alt_ann["bbox"][:2], f"{alt_label} #{case['annotation_id']}")))
    for result in labelme_results:
        case = result["case"]
        document = result["document"]
        polygons = [s for s in document["shapes"] if s.get("shape_type") == "polygon"]
        if case["status"] == "blocked":
            others = [("polygon", [tuple(p) for p in s["points"]]) for s in polygons]
            labels = ", ".join(f"/shapes/{i} {s['label']}" for i, s in enumerate(document["shapes"]))
            render_comparison(
                out_dir / f"{case['case']}.png", f"{case['case']} | LabelMe {case['image']} | BLOCKED - not altered",
                [f"Required: {case['required']}.", f"Blocker: {case['blocker']}", f"Shapes in this image: {labels}.",
                 "Both panels are identical: the packaged annotation is byte-identical in the baseline and errors ZIPs.",
                 "On inspection no person is visible in this image (the bus cab is empty)."],
                images[case["image"]],
                (others, {"geometry": [], "color": green, "poly_color": cyan}, [], None),
                (others, {"geometry": [], "color": red, "poly_color": magenta}, [], None),
                labels=("BASELINE ZIP (unchanged)", "ERRORS ZIP (unchanged - case blocked)"))
            continue
        index = case["shape"]
        base_shape = document["shapes"][index]
        alt_shape = result["altered_document"]["shapes"][index]
        others = [("polygon", [tuple(p) for p in s["points"]]) for i, s in enumerate(document["shapes"])
                  if i != index and s.get("shape_type") == "polygon"]
        ghost = [("polygon", [tuple(p) for p in base_shape["points"]])] if result["field"] == "points" else []
        t = result["transformation"]
        change = (f"label '{result['before']}' -> '{result['after']}'; points unchanged."
                  if result["field"] == "label" else
                  f"all {t['vertex_count']} points translated {t['direction']} by {abs(t['pixel_displacement'])} px "
                  f"(10% of image {'width' if t['axis'] == 'x' else 'height'}); order preserved; group_id unchanged.")
        anchor = lambda shape: (min(p[0] for p in shape["points"]), min(p[1] for p in shape["points"]))  # noqa: E731
        render_comparison(
            out_dir / f"{case['case']}.png",
            f"{case['case']} | LabelMe {case['image']} | target /shapes/{index} (group_id {base_shape.get('group_id')})",
            [f"Deliberate change: {case['description']}", change, legend_labelme],
            images[case["image"]],
            (others, {"geometry": [("polygon", [tuple(p) for p in base_shape["points"]])], "color": green,
                      "poly_color": green}, [], (anchor(base_shape), f"{base_shape['label']} /shapes/{index}")),
            (others, {"geometry": [("polygon", [tuple(p) for p in alt_shape["points"]])], "color": red,
                      "poly_color": red}, ghost, (anchor(alt_shape), f"{alt_shape['label']} /shapes/{index}")))


# --------------------------------------------------------------------------- outputs
def write_zip(path: Path, members: dict) -> None:
    """Deterministic, importer-friendly ZIP: stored entries, fixed metadata, sorted names, no directories."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, ZIP_DATE)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, members[name])
    atomic_write(path, buffer.getvalue())


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def same_output(new: Path, old: Path) -> bool:
    """Archives and JSON must match byte for byte. PNG compression bytes can differ between zlib
    builds (seen between Windows and Linux with the same Pillow version), so PNGs are compared by
    decoded pixels."""
    if not old.is_file():
        return False
    if new.read_bytes() == old.read_bytes():
        return True
    if new.suffix != ".png":
        return False
    from PIL import Image, ImageChops
    with Image.open(new) as a, Image.open(old) as b:
        return a.size == b.size and ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is None


def check_output_dir(out: Path) -> Path:
    out = out.resolve()
    for root in PROTECTED_ROOTS:
        root = root.resolve()
        require(not (out == root or root in out.parents or out in root.parents),
                f"Refusing to write into, or into a parent of, a source dataset directory: {out}")
    return out


# --------------------------------------------------------------------------- verification
def json_diff(a, b, path=""):
    """Every JSON pointer at which two documents differ."""
    if type(a) is not type(b):
        return [path or "/"]
    if isinstance(a, dict):
        return [p for key in sorted(set(a) | set(b))
                for p in (json_diff(a[key], b[key], f"{path}/{key}") if key in a and key in b else [f"{path}/{key}"])]
    if isinstance(a, list):
        if len(a) != len(b):
            return [path or "/"]
        return [p for i, (x, y) in enumerate(zip(a, b)) for p in json_diff(x, y, f"{path}/{i}")]
    return [] if a == b else [path or "/"]


def importer_candidates(file_name, annotation_path, names, fmt):
    """Mirror json_zip._resolve_image's search: count archive files the importer would match."""
    parent = PurePosixPath(annotation_path).parent
    bases = [parent] if fmt == "labelme" else [parent, parent / "images", parent.parent,
                                                 parent.parent / "images", PurePosixPath("."), PurePosixPath("images")]
    found = set()
    for base in bases:
        candidate = (base / file_name).as_posix()
        candidate = candidate[2:] if candidate.startswith("./") else candidate
        if candidate in names:
            found.add(candidate)
    return found


def inspect_archive(fmt, members):
    """Read and inspect a packaged archive with the project's own reader and image inspector.

    Returns {json member: {"diagnostics": [(code, location)], "images": [(file, availability, w, h)]}}.
    Reader, image and geometry diagnostics are all included, exactly as the preview tool sees them.
    """
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from backend.app.datasets.annotation_validation import inspect_document
    from backend.app.datasets.coco import read_coco
    from backend.app.datasets.labelme import read_labelme
    results = {}
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for name, data in members.items():
            target = root.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        for name in sorted(n for n in members if n.endswith(".json")):
            path = root.joinpath(*PurePosixPath(name).parts)
            document = (read_coco if fmt == "coco" else read_labelme)(path)
            inspection = inspect_document(document, root / "images" if fmt == "coco" else path.parent)
            found = {(i.code, i.location) for i in inspection.reader_issues + inspection.geometry_issues}
            found |= {(i.code, i.location or "(image)") for image in inspection.images for i in image.issues}
            results[name] = {"diagnostics": sorted(found),
                             "images": [(image.file_name, image.availability, image.actual_width, image.actual_height)
                                        for image in inspection.images]}
    return results


def verify(out: Path, source_before: dict | None = None) -> dict:
    checks, problems = [], []

    def check(name, ok, detail=""):
        checks.append({"check": name, "passed": bool(ok), "detail": detail})
        if not ok:
            problems.append(f"{name}: {detail}")

    archives = {}
    for (fmt, variant), name in ARCHIVES.items():
        path = out / "archives" / name
        check(f"{name} exists", path.is_file(), str(path))
        if not path.is_file():
            continue
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            check(f"{name}: CRC test", archive.testzip() is None)
            members = {i.filename: archive.read(i) for i in infos}
        check(f"{name}: no directory entries, all stored, regular files, safe names",
              all(not i.is_dir() and i.compress_type == zipfile.ZIP_STORED and (i.external_attr >> 16) == 0o100644
                  and "\\" not in i.filename and not i.filename.startswith("/") and ".." not in i.filename.split("/")
                  for i in infos))
        lowered = [n.casefold() for n in members]
        check(f"{name}: no case-insensitive name collisions", len(set(lowered)) == len(lowered))
        archives[(fmt, variant)] = members

    for variant in ("baseline", "altered"):
        members = archives.get(("coco", variant), {})
        name = ARCHIVES[("coco", variant)]
        jsons = [n for n in members if n.endswith(".json")]
        images = sorted(n for n in members if n.lower().endswith((".jpg", ".jpeg", ".png")))
        check(f"{name}: exactly one annotation JSON and five images, nothing else",
              len(jsons) == 1 and len(images) == 5 and len(members) == 6, f"{sorted(members)}")
        if len(jsons) != 1:
            continue
        doc = json.loads(members[jsons[0]])
        ids = {im["id"] for im in doc["images"]}
        used = {a["category_id"] for a in doc["annotations"]}
        defined = {c["id"] for c in doc["categories"]}
        check(f"{name}: five image records", len(doc["images"]) == 5, str(sorted(ids)))
        check(f"{name}: every annotation belongs to a packaged image", all(a["image_id"] in ids for a in doc["annotations"]))
        check(f"{name}: every used category is defined", used <= defined, str(sorted(used - defined)))
        check(f"{name}: annotation ids unique", len({a["id"] for a in doc["annotations"]}) == len(doc["annotations"]))
        check(f"{name}: positive box width and height",
              all(a["bbox"][2] > 0 and a["bbox"][3] > 0 for a in doc["annotations"]))
        for record in doc["images"]:
            found = importer_candidates(record["file_name"], jsons[0], set(members), "coco")
            check(f"{name}: {record['file_name']} resolves to exactly one archive file (importer rule)",
                  len(found) == 1, str(sorted(found)))
        check(f"{name}: no unreferenced image files",
              {f"images/{r['file_name']}" for r in doc["images"]} == set(images))
    source = json.loads(text_bytes(COCO_JSON))
    expected_annotations = [a for a in source["annotations"] if a["image_id"] in {c["image_id"] for c in COCO_CASES}]
    base_doc = json.loads(archives[("coco", "baseline")][COCO_PACKAGED_JSON])
    alt_doc = json.loads(archives[("coco", "altered")][COCO_PACKAGED_JSON])
    check("coco baseline: annotations identical to the source annotations of the five images",
          base_doc["annotations"] == expected_annotations)
    check("coco baseline: info, licenses and all categories identical to the source",
          all(base_doc[k] == source[k] for k in ("info", "licenses", "categories")))

    for variant in ("baseline", "altered"):
        members = archives.get(("labelme", variant), {})
        name = ARCHIVES[("labelme", variant)]
        jsons = sorted(n for n in members if n.endswith(".json"))
        images = sorted(n for n in members if n.lower().endswith((".jpg", ".jpeg", ".png")))
        check(f"{name}: exactly three annotation JSON files and three images, nothing else",
              len(jsons) == 3 and len(images) == 3 and len(members) == 6, f"{sorted(members)}")
        for json_name in jsons:
            doc = json.loads(members[json_name])
            found = importer_candidates(doc["imagePath"], json_name, set(members), "labelme")
            check(f"{name}: {json_name} imagePath resolves beside the JSON (importer rule)",
                  len(found) == 1 and doc.get("imageData") is None, f"{doc['imagePath']} -> {sorted(found)}")

    for fmt in ("coco", "labelme"):
        base, alt = archives.get((fmt, "baseline"), {}), archives.get((fmt, "altered"), {})
        for member in sorted(n for n in base if n.lower().endswith((".jpg", ".png"))):
            same = member in alt and sha256(base[member]) == sha256(alt[member])
            source_hash = IMAGE_SHA256.get(PurePosixPath(member).name)
            check(f"{fmt}: {member} identical in baseline and altered and equal to the source image",
                  same and sha256(base[member]) == source_hash, sha256(base.get(member, b"")))

    # single-field differences relative to the packaged baseline
    for case in COCO_CASES:
        index = [a["id"] for a in base_doc["annotations"]].index(case["annotation_id"])
        field = {"raise_bottom": "bbox", "lower_top": "bbox", "extend_edge": "bbox", "relabel": "category_id",
                 "shift_polygon": "segmentation"}[case["edit"]["op"]]
        prefix = f"/annotations/{index}/{field}"
        mine = [p for p in json_diff(base_doc, alt_doc)
                if p.startswith(f"/annotations/{index}/") or p == f"/annotations/{index}"]
        check(f"{case['case']}: only annotation {case['annotation_id']} field '{field}' differs from the baseline",
              mine and all(p == prefix or p.startswith(prefix + "/") for p in mine), str(mine[:6]))
    all_coco = json_diff(base_doc, alt_doc)
    allowed = []
    for case in COCO_CASES:
        index = [a["id"] for a in base_doc["annotations"]].index(case["annotation_id"])
        allowed.append(f"/annotations/{index}/")
    check("coco: no differences outside the five documented target fields",
          all(any(p.startswith(prefix) for prefix in allowed) for p in all_coco), str(all_coco[:8]))
    for case in LABELME_CASES:
        base = json.loads(archives[("labelme", "baseline")][case["file"]])
        alt = json.loads(archives[("labelme", "altered")][case["file"]])
        diffs = json_diff(base, alt)
        if case["status"] == "blocked":
            check(f"{case['case']}: blocked case is byte-identical in both archives",
                  archives[("labelme", "baseline")][case["file"]] == archives[("labelme", "altered")][case["file"]])
            continue
        field = "label" if case["edit"]["op"] == "relabel" else "points"
        prefix = f"/shapes/{case['shape']}/{field}"
        check(f"{case['case']}: only {prefix} differs from the baseline",
              diffs and all(p == prefix or p.startswith(prefix + "/") for p in diffs), str(diffs[:6]))
        check(f"{case['case']}: shape order and group_id preserved",
              [s.get("group_id") for s in base["shapes"]] == [s.get("group_id") for s in alt["shapes"]]
              and len(base["shapes"]) == len(alt["shapes"]))
        check(f"{case['case']}: baseline annotation is the exact upstream bytes",
              sha256(archives[("labelme", "baseline")][case["file"]]) == LABELME_JSON_SHA256[case["file"]])

    # reader, image and geometry diagnostics: existing versus introduced by the deliberate edits
    diagnostics = {}
    for fmt in ("coco", "labelme"):
        before_all = inspect_archive(fmt, archives[(fmt, "baseline")])
        after_all = inspect_archive(fmt, archives[(fmt, "altered")])
        for member, before in before_all.items():
            after = after_all[member]
            for variant, result in (("baseline", before), ("altered", after)):
                check(f"{fmt} {variant} {member}: every referenced image opens with the declared size",
                      result["images"] and all(a == "available" and w and h for _, a, w, h in result["images"]),
                      str(result["images"]))
            old, new = set(before["diagnostics"]), set(after["diagnostics"])
            diagnostics[f"{fmt}:{member}"] = {
                "existing": [f"{c} {loc}" for c, loc in sorted(old)],
                "introduced_by_edit": [f"{c} {loc}" for c, loc in sorted(new - old)],
                "removed_by_edit": [f"{c} {loc}" for c, loc in sorted(old - new)]}
    check("no diagnostic introduced or removed by the deliberate edits (all edits stay structurally valid)",
          all(not d["introduced_by_edit"] and not d["removed_by_edit"] for d in diagnostics.values()),
          json.dumps({k: d["introduced_by_edit"] + d["removed_by_edit"] for k, d in diagnostics.items()}))

    # answer key and comparisons
    key_path = out / "answer_key.json"
    check("answer_key.json exists", key_path.is_file())
    if key_path.is_file():
        key = json.loads(key_path.read_bytes())
        ids = [e["case_id"] for e in key["entries"]]
        wanted = [f"{c}-{v}" for v in ("baseline", "altered") for c in ("C01", "C02", "C03", "C04", "C05")] + \
                 [f"{c}-{v}" for v in ("baseline", "altered") for c in ("L01", "L02", "L03")]
        check("answer key has exactly the 16 required entries", sorted(ids) == sorted(wanted) and len(ids) == 16, str(ids))
        check("answer key pairs point at each other", all(
            next(e for e in key["entries"] if e["case_id"] == entry["paired_case_id"])["paired_case_id"] == entry["case_id"]
            for entry in key["entries"]))
    for name in COMPARISONS:
        path = out / "comparisons" / f"{name}.png"
        check(f"comparisons/{name}.png exists", path.is_file() and path.stat().st_size > 0)
    for archive in (out / "archives").glob("*.zip"):
        with zipfile.ZipFile(archive) as handle:
            names = handle.namelist()
        check(f"{archive.name}: no answer key, guidelines, overlays or reports inside",
              not any(PurePosixPath(n).name in {"answer_key.json", "guidelines.txt", "README.md", "verification.md"}
                      or "overlay" in n or "validation" in n for n in names))

    if source_before is not None:
        after = snapshot(PROTECTED_ROOTS)
        changed = sorted(k for k in set(source_before) | set(after) if source_before.get(k) != after.get(k))
        check("source dataset files unchanged (raw SHA-256 before and after)", not changed, str(changed[:5]))
    return {"passed": not problems, "problems": problems, "checks": checks, "reader_diagnostics": diagnostics}


# --------------------------------------------------------------------------- run
def build(out: Path, *, comparisons: bool = True) -> dict:
    out = check_output_dir(out)
    coco_source, images, labelme = load_sources()
    coco_files, coco_results, coco_baseline, coco_altered = build_coco(coco_source, images)
    labelme_files, labelme_results = build_labelme(labelme)
    for (fmt, variant), name in ARCHIVES.items():
        write_zip(out / "archives" / name, coco_files[variant] if fmt == "coco" else labelme_files[variant])
    key = build_answer_key(coco_results, labelme_results, coco_source, coco_baseline, images)
    atomic_write(out / "answer_key.json", (json.dumps(key, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    if comparisons:
        render_all(out / "comparisons", coco_results, labelme_results, coco_baseline, coco_altered, images)
    return {"coco": coco_results, "labelme": labelme_results}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=HERE, help="Output directory (default: this folder)")
    parser.add_argument("--check", action="store_true",
                        help="Rebuild temporarily and compare against --out (archives/JSON bytes, PNG pixels)")
    parser.add_argument("--no-verify", action="store_true", help="Skip the verification step")
    args = parser.parse_args(argv)
    try:
        if args.check:
            output_dir = check_output_dir(args.out)
            with tempfile.TemporaryDirectory() as temp:
                build(Path(temp))
                produced = sorted(p for p in Path(temp).rglob("*") if p.is_file())
                differing = [str(p.relative_to(temp)) for p in produced
                             if not same_output(p, output_dir / p.relative_to(temp))]
            if differing:
                print(f"Rebuild differs from the outputs in {output_dir}:\n  " + "\n  ".join(differing))
                return 1
            print(f"Rebuild matches all {len(produced)} generated files in {output_dir} "
                  "(archives and answer key byte-identical; PNGs pixel-identical)")
            return 0
        before = snapshot(PROTECTED_ROOTS)
        build(args.out)
        if args.no_verify:
            return 0
        report = verify(check_output_dir(args.out), source_before=before)
        print(json.dumps(report, indent=2))
        return 0 if report["passed"] else 1
    except PackError as error:
        print(f"build_pack: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
