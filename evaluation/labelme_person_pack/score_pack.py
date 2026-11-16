#!/usr/bin/env python3
"""Score the application's measured person checks against the pack's answer key, without the application.

It calls the same functions the audit worker calls (backend/app/services/person_coverage.py) on every shape of
both archives. The person model is not run here: its saved output for each photo is read from --detections
(one <COCO image id>.npz per photo, written by .cache/validation/missing-person-20261109/cache_detections.py).
Those files hold the detected people only, not carried objects such as backpacks, so "extra background" can be
reported slightly more often here than in the application.

Run build_pack.py first. Prints the tables and writes results.json next to this file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import zipfile

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.app.checks.registry import validate_check_parameters  # noqa: E402
from backend.app.services import person_coverage as pc  # noqa: E402

DETECTIONS = REPO / ".cache/validation/missing-person-20261109/cache"
MIN_HEIGHT = 100


def detections(folder: Path, stem: str, size):
    data = np.load(folder / f"{int(stem)}.npz")
    width, height = size
    masks = (np.unpackbits(data["masks"], axis=1)[:, :width * height].reshape(-1, height, width).astype(bool)
             if len(data["scores"]) else [])
    people = [SimpleNamespace(mask=mask, box=tuple(int(v) for v in box), score=float(score))
              for mask, box, score in zip(masks, data["boxes"], data["scores"])]
    return SimpleNamespace(people=people, carried=[])


def box_overlap(a, b) -> float:
    """Intersection over union of two [x0, y0, x1, y1] boxes."""
    width, height = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    return width * height / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - width * height)


def touches(a, b) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def judge_file(document: dict, segmentation, coverage, missing):
    """({shape index: (outcome, regions)}, boxes of people reported as having no annotation)."""
    size = (document["imageWidth"], document["imageHeight"])
    rows = [{"annotation": {"object_id": index, "label": shape["label"], "polygons": [shape["points"]]}}
            for index, shape in enumerate(document["shapes"])]
    shapes = {}
    for index, row in enumerate(rows):
        outcome, _, result = pc.object_coverage(row["annotation"], "labelme", size, None, segmentation, coverage)
        shapes[index] = (outcome.value, [region for g in result["geometries"] for region in g.get("regions", [])])
    _, _, result = pc.unannotated_people(rows, "labelme", size, None, segmentation, missing)
    return shapes, [person["box"] for person in result["unannotated"]]


def score(pack: Path = HERE, folder: Path = DETECTIONS) -> dict:
    key = json.loads((pack / "answer_key.json").read_text(encoding="utf-8"))
    coverage = validate_check_parameters("person_coverage", {})
    missing = validate_check_parameters("unannotated_people", {})
    clean_zip = zipfile.ZipFile(pack / "archives/labelme_pack_clean.zip")
    error_zip = zipfile.ZipFile(pack / "archives/labelme_pack_errors.zip")
    clean = {"outlines": 0, "fail": 0, "unsure": 0, "tall_one_part": 0, "tall_one_part_fail": 0,
             "photos": 0, "photos_with_unannotated_report": 0}
    cases = []
    for case in key["cases"]:
        stem = case["file"][:-5]
        original = json.loads(clean_zip.read(case["file"]))
        segmentation = detections(folder, stem, (original["imageWidth"], original["imageHeight"]))
        before, reported_before = judge_file(original, segmentation, coverage, missing)
        clean["photos"] += 1
        clean["photos_with_unannotated_report"] += bool(reported_before)
        for index, (outcome, _) in before.items():
            shape = original["shapes"][index]
            ys = [point[1] for point in shape["points"]]
            tall = shape["group_id"] is None and max(ys) - min(ys) >= MIN_HEIGHT
            clean["outlines"] += 1
            clean["fail"] += outcome == "fail"
            clean["unsure"] += outcome == "unsure"
            clean["tall_one_part"] += tall
            clean["tall_one_part_fail"] += tall and outcome == "fail"
        if case["error"] is None:
            continue
        after, reported = judge_file(json.loads(error_zip.read(case["file"])), segmentation, coverage, missing)
        source = int(case["source_shape_key"].rsplit("/", 1)[1])
        row = {"file": case["file"], "error": case["error"], "family": case["family"],
               "outcome_before": before[source][0]}
        if case["family"] == "removed":
            row["found"] = any(box_overlap(box, case["source_box"]) >= 0.3 for box in reported)
            row["outcome"] = "fail" if reported else "pass"
        elif case["family"] == "label":
            row["outcome"], row["found"] = "not scored", None  # allowed_labels is a plain label comparison
        else:
            outcome, regions = after[int(case["shape_key"].rsplit("/", 1)[1])]
            kind, where = case["expected_region_kind"], case["details"]["changed_box"]
            row["outcome"] = outcome
            row["region_kinds"] = sorted({region["kind"] for region in regions})
            if case["family"] == "overlap":  # no overlap check exists; record what the person check says
                row["found"] = None
            else:
                row["found"] = outcome == "fail"
                row["right_kind"] = any(region["kind"] == kind for region in regions)
                row["right_place"] = any(region["kind"] == kind and touches(region["box"], where) for region in regions)
        cases.append(row)
    return {"seed": key["seed"], "error_types": list(key["errors_by_type"]), "detections": str(folder.relative_to(REPO)) if folder.is_relative_to(REPO) else str(folder),
            "clean": clean, "cases": cases}


def table(result: dict) -> str:
    lines = ["| Error | Injected | Reported (fail) | Unsure | Passed | Right kind and place | Already failing before |",
             "|---|---|---|---|---|---|---|"]
    for kind in result["error_types"]:
        rows = [row for row in result["cases"] if row["error"] == kind]
        if not rows:
            continue
        if rows[0]["family"] == "label":
            lines.append(f"| `{kind}` | {len(rows)} | not scored here | | | | |")
            continue
        count = lambda name: sum(row["outcome"] == name for row in rows)  # noqa: E731
        if rows[0]["family"] == "removed":  # the person is reported where the annotation used to be
            placed = sum(bool(row["found"]) for row in rows)
        else:
            placed = sum(bool(row.get("right_place")) for row in rows) if "right_place" in rows[0] else "-"
        lines.append(f"| `{kind}` | {len(rows)} | {count('fail')} | {count('unsure')} | {count('pass')} | {placed} | "
                     f"{sum(row['outcome_before'] == 'fail' for row in rows)} |")
    c = result["clean"]
    lines += ["", f"Unchanged outlines: {c['fail']} of {c['outlines']} reported, {c['unsure']} unsure. "
              f"One-part outlines at least {MIN_HEIGHT} px tall: {c['tall_one_part_fail']} of {c['tall_one_part']} "
              f"reported. Photos with a person reported as unannotated: {c['photos_with_unannotated_report']} of "
              f"{c['photos']}."]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pack", type=Path, default=HERE)
    parser.add_argument("--detections", type=Path, default=DETECTIONS)
    args = parser.parse_args()
    if not (args.pack / "answer_key.json").is_file():
        raise SystemExit("Run build_pack.py first.")
    if not args.detections.is_dir():
        raise SystemExit(f"No saved person-model output in {args.detections}. See README.md.")
    scored = score(args.pack, args.detections)
    (args.pack / "results.json").write_text(json.dumps(scored, indent=2) + "\n", encoding="utf-8")
    print(table(scored))
