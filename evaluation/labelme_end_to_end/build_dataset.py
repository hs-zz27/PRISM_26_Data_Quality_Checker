#!/usr/bin/env python3
"""Build the LabelMe polygon test datasets and their seeded annotation errors.

Run from the repository root with the repository virtual environment. No model, database, service or download
is needed. Photos are copied byte for byte and never edited; outputs stay local (see .gitignore).

Outputs under this folder:
  archives/labelme_people_clean.zip    person polygons as the annotators drew them
  archives/labelme_people_errors.zip   the same photos with one seeded error in most files
  archives/labelme_native_examples.zip the upstream LabelMe example files, unchanged
  answer_key.json                      what was injected and where (never upload this)
  verification.json                    source and archive hashes
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import random
import sys
import zipfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.app.datasets.annotation_validation import _geometry_issues  # noqa: E402
from backend.app.datasets.labelme import parse_labelme  # noqa: E402
from backend.app.models.annotation_validation import ValidationLimits  # noqa: E402

SEED = 20261113
COCO_DIR = REPO / "datasets" / "coco_people_30"
COCO_JSON = COCO_DIR / "annotations" / "instances_people30.json"
NATIVE_DIR = REPO / "datasets" / "labelme_people_examples"
NATIVE_STEMS = ("2011_000003", "2011_000006", "2011_000025")
MAX_PEOPLE = 10          # files in the people set have 1 to 10 person annotations
MIN_HEIGHT = 100         # an error is injected only into an outline at least this tall, in pixels
CUT_FRACTION = 0.2       # share of the outline's height removed by head_cut and feet_cut
WIDEN_FACTOR = 1.6       # how far extra_background stretches one side of the outline
MIN_WIDEN = 1.3          # the stretch is skipped when the image edge leaves less room than this
SHIFT_FRACTION = 0.35    # shifted moves the outline sideways by this share of its width
CONTROL_EVERY = 4        # every fourth file (in seeded order) is left unchanged
ERROR_TYPES = ("head_cut", "feet_cut", "extra_background", "shifted", "wrong_label", "annotation_removed")
WRONG_LABEL = "car"
ZIP_DATE = (1980, 1, 1, 0, 0, 0)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(value) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()


# ── COCO person polygons as LabelMe files ───────────────────────────────────
def labelme_document(image: dict, people: list[dict]) -> dict:
    """One LabelMe file: a polygon shape per outline part, exact source numbers, no boxes.

    A person drawn in several parts becomes several shapes that share a group_id, which is how LabelMe
    records one object made of parts.
    """
    shapes, group = [], 0
    for person in people:
        parts = person["segmentation"]
        group_id = None
        if len(parts) > 1:
            group += 1
            group_id = group
        for part in parts:
            shapes.append({
                "label": "person",
                "points": [[part[i], part[i + 1]] for i in range(0, len(part), 2)],
                "group_id": group_id,
                "description": f"COCO annotation {person['id']}",
                "shape_type": "polygon",
                "flags": {},
            })
    return {"version": "5.4.1", "flags": {}, "shapes": shapes, "imagePath": image["file_name"],
            "imageData": None, "imageHeight": image["height"], "imageWidth": image["width"]}


def people_documents() -> dict[str, dict]:
    """{file stem: LabelMe document} for every source photo with 1 to MAX_PEOPLE person annotations."""
    source = json.loads(COCO_JSON.read_text(encoding="utf-8"))
    person_ids = {c["id"] for c in source["categories"] if c["name"] == "person"}
    people: dict[int, list[dict]] = {}
    for annotation in source["annotations"]:
        if annotation["category_id"] in person_ids:
            if annotation.get("iscrowd") or not isinstance(annotation["segmentation"], list):
                raise SystemExit("The source subset must contain polygon person annotations only.")
            people.setdefault(annotation["image_id"], []).append(annotation)
    documents = {}
    for image in sorted(source["images"], key=lambda item: item["file_name"]):
        found = sorted(people.get(image["id"], []), key=lambda item: item["id"])
        if 1 <= len(found) <= MAX_PEOPLE:
            documents[Path(image["file_name"]).stem] = labelme_document(image, found)
    return documents


# ── seeded errors ───────────────────────────────────────────────────────────
def bounds(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _tidy(points):
    """Drop repeated neighbours and the middle of three neighbours on one horizontal line."""
    out = []
    for point in points:
        if not out or out[-1] != point:
            out.append(point)
    while len(out) > 1 and out[0] == out[-1]:
        out.pop()
    changed = True
    while changed and len(out) > 3:
        changed = False
        for i in range(len(out)):
            before, here, after = out[i - 1], out[i], out[(i + 1) % len(out)]
            if before[1] == here[1] == after[1]:
                del out[i]
                changed = True
                break
    return out


def head_cut(points, size):
    """Flatten the top CUT_FRACTION of the outline, so the head is left outside it."""
    _, top, _, bottom = bounds(points)
    line = round(top + CUT_FRACTION * (bottom - top), 2)
    return _tidy([[x, max(y, line)] for x, y in points]), {"cut_line_y": line}


def feet_cut(points, size):
    """Flatten the bottom CUT_FRACTION of the outline, so the feet are left outside it."""
    _, top, _, bottom = bounds(points)
    line = round(bottom - CUT_FRACTION * (bottom - top), 2)
    return _tidy([[x, min(y, line)] for x, y in points]), {"cut_line_y": line}


def extra_background(points, size):
    """Stretch one side of the outline away from its middle, so it takes in background."""
    left, _, right, _ = bounds(points)
    middle = (left + right) / 2
    room_right = (size[0] - 1 - middle) / (right - middle)
    room_left = (middle - 0) / (middle - left)
    side, room = ("right", room_right) if room_right >= room_left else ("left", room_left)
    factor = min(WIDEN_FACTOR, room)
    if factor < MIN_WIDEN:
        return None
    def move(x):
        on_side = x > middle if side == "right" else x < middle
        return round(middle + (x - middle) * factor, 2) if on_side else x
    return [[move(x), y] for x, y in points], {"side": side, "factor": round(factor, 3)}


def shifted(points, size):
    """Slide the whole outline sideways, so it misses part of the person and covers background."""
    left, _, right, _ = bounds(points)
    distance = round(SHIFT_FRACTION * (right - left), 2)
    if right + distance <= size[0] - 1:
        offset = distance
    elif left - distance >= 0:
        offset = -distance
    else:
        return None
    return [[round(x + offset, 2), y] for x, y in points], {"offset_x": offset}


GEOMETRY_ERRORS = {"head_cut": head_cut, "feet_cut": feet_cut, "extra_background": extra_background,
                   "shifted": shifted}


def structural_codes(document: dict) -> list[str]:
    """Issue codes the application's reader and geometry validation report for this file."""
    parsed = parse_labelme(document)
    record = parsed.images[0]
    size = (document["imageWidth"], document["imageHeight"])
    codes = [item.code for item in (*parsed.issues, *record.issues)]
    for annotation in record.annotations:
        codes += [item.code for item in annotation.issues]
        codes += [item.code for item in _geometry_issues(annotation, size, ValidationLimits())]
    return sorted(codes)


def _overlap_share(box, other):
    width = min(box[2], other[2]) - max(box[0], other[0])
    height = min(box[3], other[3]) - max(box[1], other[1])
    if width <= 0 or height <= 0:
        return 0.0
    return width * height / ((box[2] - box[0]) * (box[3] - box[1]))


def candidates(document: dict, kind: str) -> list[int]:
    """Shape indexes that can take this error: one-part outlines at least MIN_HEIGHT tall.

    A removed annotation must also stand apart from the others (under 20% of its box inside another), so a
    reviewer can tell which person lost their annotation.
    """
    shapes = document["shapes"]
    boxes = [bounds(shape["points"]) for shape in shapes]
    out = []
    for index, shape in enumerate(shapes):
        box = boxes[index]
        if shape["group_id"] is not None or box[3] - box[1] < MIN_HEIGHT:
            continue
        if kind == "annotation_removed":
            if len(shapes) < 2 or any(_overlap_share(box, other) >= 0.2
                                      for position, other in enumerate(boxes) if position != index):
                continue
        out.append(index)
    return out


def inject(document: dict, kind: str, index: int):
    """(altered document, answer-key entry), or None when this error cannot be applied cleanly."""
    altered = copy.deepcopy(document)
    shape = altered["shapes"][index]
    entry = {"error": kind, "shape_key": f"/shapes/{index}", "source_shape_key": f"/shapes/{index}",
             "source_box": [round(v, 2) for v in bounds(shape["points"])]}
    if kind == "wrong_label":
        shape["label"] = WRONG_LABEL
        entry["details"] = {"label": WRONG_LABEL}
    elif kind == "annotation_removed":
        del altered["shapes"][index]
        entry["shape_key"] = None  # the annotation no longer exists; later shapes move up by one
        entry["details"] = {"removed": "the whole annotation"}
    else:
        result = GEOMETRY_ERRORS[kind](shape["points"], (document["imageWidth"], document["imageHeight"]))
        if result is None:
            return None
        shape["points"], entry["details"] = result
        if len(shape["points"]) < 3:
            return None
    # An injected error is an annotation mistake, never a broken file: it may not add a structural issue.
    if structural_codes(altered) != structural_codes(document):
        return None
    return altered, entry


def plan_errors(documents: dict[str, dict], seed: int = SEED):
    """({stem: altered document}, answer-key cases). At most one error per file; some files stay unchanged."""
    rng = random.Random(seed)
    order = sorted(documents)
    rng.shuffle(order)
    altered, cases, turn = {}, [], 0
    for position, stem in enumerate(order):
        document = documents[stem]
        groups = {shape["group_id"] for shape in document["shapes"]} - {None}
        case = {"file": f"{stem}.json", "image": f"{stem}.jpg", "shapes": len(document["shapes"]),
                "people": sum(shape["group_id"] is None for shape in document["shapes"]) + len(groups),
                "error": None}
        altered[stem] = document
        if position % CONTROL_EVERY != CONTROL_EVERY - 1:
            for attempt in range(len(ERROR_TYPES)):
                kind = ERROR_TYPES[(turn + attempt) % len(ERROR_TYPES)]
                options = candidates(document, kind)
                rng.shuffle(options)
                done = next((made for made in (inject(document, kind, index) for index in options) if made), None)
                if done:
                    altered[stem], entry = done
                    case.update(entry)
                    turn += 1
                    break
        cases.append(case)
    return altered, sorted(cases, key=lambda item: item["file"])


# ── archives ────────────────────────────────────────────────────────────────
def write_zip(path: Path, members: dict[str, bytes]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
        for name in sorted(members):
            archive.writestr(zipfile.ZipInfo(name, ZIP_DATE), members[name])
    return sha(path.read_bytes())


def build(output: Path = HERE, seed: int = SEED) -> dict:
    output = output.resolve()
    if any(output == folder or folder in output.parents for folder in (COCO_DIR, NATIVE_DIR)):
        raise SystemExit("Refusing to write into a source dataset folder.")
    documents = people_documents()
    photos = {stem: (COCO_DIR / "images" / f"{stem}.jpg").read_bytes() for stem in documents}
    altered, cases = plan_errors(documents, seed)
    clean = {f"{stem}.json": encode(document) for stem, document in documents.items()}
    errors = {f"{stem}.json": encode(document) for stem, document in altered.items()}
    for stem, photo in photos.items():
        clean[f"{stem}.jpg"] = errors[f"{stem}.jpg"] = photo
    native = {f"{stem}{suffix}": (NATIVE_DIR / f"{stem}{suffix}").read_bytes()
              for stem in NATIVE_STEMS for suffix in (".json", ".jpg")}
    archives = {
        "labelme_people_clean.zip": write_zip(output / "archives" / "labelme_people_clean.zip", clean),
        "labelme_people_errors.zip": write_zip(output / "archives" / "labelme_people_errors.zip", errors),
        "labelme_native_examples.zip": write_zip(output / "archives" / "labelme_native_examples.zip", native),
    }
    injected = [case for case in cases if case["error"]]
    key = {
        "seed": seed,
        "note": "Never upload this file or use it in prompts, feedback or guidelines.",
        "datasets": {
            "labelme_people_clean": {"archive": "archives/labelme_people_clean.zip", "images": len(documents),
                                     "annotations": sum(len(d["shapes"]) for d in documents.values()),
                                     "errors_injected": 0},
            "labelme_people_errors": {"archive": "archives/labelme_people_errors.zip", "images": len(altered),
                                      "annotations": sum(len(d["shapes"]) for d in altered.values()),
                                      "errors_injected": len(injected)},
            "labelme_native_examples": {"archive": "archives/labelme_native_examples.zip",
                                        "images": len(NATIVE_STEMS),
                                        "annotations": sum(len(json.loads(native[f"{stem}.json"])["shapes"])
                                                           for stem in NATIVE_STEMS),
                                        "errors_injected": 0},
        },
        "errors_by_type": {kind: sum(case["error"] == kind for case in cases) for kind in ERROR_TYPES},
        "cases": cases,
    }
    (output / "answer_key.json").write_bytes(encode(key))
    (output / "verification.json").write_bytes(encode({
        "seed": seed, "archives_sha256": archives,
        "source_annotation_sha256": sha(COCO_JSON.read_bytes()),
        "photos_sha256": {f"{stem}.jpg": sha(photo) for stem, photo in sorted(photos.items())},
        "native_sha256": {name: sha(data) for name, data in sorted(native.items())},
    }))
    return key


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=HERE)
    parser.add_argument("--seed", type=int, default=SEED)
    arguments = parser.parse_args()
    built = build(arguments.output, arguments.seed)
    for name, facts in built["datasets"].items():
        print(f"{name}: {facts['images']} images, {facts['annotations']} annotations, "
              f"{facts['errors_injected']} errors injected")
    print("errors by type:", built["errors_by_type"])
