#!/usr/bin/env python3
"""Score the redrawn reference outlines against COCO's originals, and suggest a boundary-fit pass mark.

  .venv/bin/python -B evaluation/tight_reference/compare.py --returned path/to/returned_folder_or.zip
  .venv/bin/python -B evaluation/tight_reference/compare.py --returned evaluation/tight_reference/drawn \
      --review evaluation/tight_reference/review.json   # only the outlines checked and kept as references

The returned folder is the kit after redrawing in LabelMe. People are matched to the originals by the COCO id
in each shape's description, by their Group ID, and otherwise by overlap. Writes compare_results.json.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
import sys
import tempfile
import zipfile

import cv2
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from backend.app.services import boundary_fit as fit_module  # noqa: E402

KEEP_SHARE = 0.95  # the suggested mark passes this share of the redrawn outlines


def _coco_id(shape):
    words = (shape.get("description") or "").split()
    return int(words[-1]) if len(words) >= 3 and words[0] == "COCO" and words[-1].isdigit() else None


def people(document):
    """{key: [polygons]} per person. A key is the COCO id when known, else ("new", n)."""
    groups = collections.OrderedDict()
    for index, shape in enumerate(document["shapes"]):
        if shape.get("label") != "person" or shape.get("shape_type", "polygon") != "polygon" or len(shape["points"]) < 3:
            continue
        key = ("group", shape["group_id"]) if shape.get("group_id") is not None else ("shape", index)
        groups.setdefault(key, []).append(shape)
    out = {}
    for number, shapes in enumerate(groups.values()):
        ids = [i for i in (_coco_id(s) for s in shapes) if i is not None]
        out[ids[0] if ids else ("new", number)] = [s["points"] for s in shapes]
    return out


def _mask(polygons, size):
    mask = np.zeros((size[1], size[0]), np.uint8)
    for polygon in polygons:
        cv2.fillPoly(mask, [np.rint(np.asarray(polygon, float)).astype(np.int32)], 1)
    return mask.astype(bool)


def match(original, redrawn, size):
    """{redrawn key: original COCO id or None}. Unknown keys are matched by overlap (at least 0.3)."""
    out, taken = {}, set()
    for key in redrawn:
        if not isinstance(key, tuple) and key in original:
            out[key] = key
            taken.add(key)
    for key, polygons in redrawn.items():
        if key in out:
            continue
        mask = _mask(polygons, size)
        best, best_iou = None, 0.3
        for cid, original_polygons in original.items():
            if cid in taken:
                continue
            other = _mask(original_polygons, size)
            union = (mask | other).sum()
            iou = (mask & other).sum() / union if union else 0.0
            if iou >= best_iou:
                best, best_iou = cid, iou
        out[key] = best
        if best is not None:
            taken.add(best)
    return out


def compare(kit: Path, returned: Path, review: Path | None = None) -> dict:
    """Score every matched person, or with ``review`` only those whose verdict there is "reference"."""
    manifest = json.loads((kit / "kit_manifest.json").read_text(encoding="utf-8"))
    verdicts = json.loads(review.read_text(encoding="utf-8"))["people"] if review else None
    rows, added, missing, missing_files, left_out = [], 0, 0, [], 0
    for photo in manifest["photos"]:
        if not (returned / photo["file"]).is_file():
            missing_files.append(photo["file"])
            continue
        original = json.loads((kit / photo["file"]).read_text(encoding="utf-8"))
        redrawn = json.loads((returned / photo["file"]).read_text(encoding="utf-8"))
        size = (original["imageWidth"], original["imageHeight"])
        image = returned / photo["image"] if (returned / photo["image"]).is_file() else kit / photo["image"]
        distance = fit_module.edge_distance(np.array(Image.open(image).convert("RGB")))
        before_people, after_people = people(original), people(redrawn)
        matched = match(before_people, after_people, size)
        added += sum(cid is None for cid in matched.values())
        missing += len(set(before_people) - set(matched.values()))
        for key, cid in matched.items():
            if cid is None:
                continue
            if verdicts is not None and verdicts.get(str(cid), {}).get("verdict") != "reference":
                left_out += 1
                continue
            before = fit_module.boundary_fit(distance, before_people[cid])
            after = fit_module.boundary_fit(distance, after_people[key])
            rows.append({"file": photo["file"], "category": photo["category"], "coco_annotation": cid,
                         "before": before.share_on_edge, "after": after.share_on_edge, "after_busy": after.busy,
                         "points_before": sum(len(p) for p in before_people[cid]),
                         "points_after": sum(len(p) for p in after_people[key])})
    judged = [r for r in rows if r["after"] is not None and not r["after_busy"]]
    after = np.array([r["after"] for r in judged])
    before = np.array([r["before"] for r in judged if r["before"] is not None])
    mark = float(np.floor(100 * np.percentile(after, 100 * (1 - KEEP_SHARE))) / 100) if len(after) else None
    return {"people": rows, "added_people": added, "missing_people": missing, "missing_files": missing_files,
            "left_out_by_review": left_out,
            "suggested_mark": mark,
            "redrawn_passing_mark": float(np.mean(after >= mark)) if mark is not None else None,
            "originals_passing_mark": float(np.mean(before >= mark)) if mark is not None and len(before) else None,
            "redrawn_passing_provisional": float(np.mean(after >= fit_module.PROVISIONAL_MIN_SHARE)) if len(after) else None}


def _report(result):
    rows = result["people"]
    print("| Kind of photo | People | On the boundary before (median) | After (median) | Points before -> after (median) |")
    print("|---|---|---|---|---|")
    for kind in dict.fromkeys(r["category"] for r in rows):
        group = [r for r in rows if r["category"] == kind]
        med = lambda key: float(np.median([r[key] for r in group if r[key] is not None]))  # noqa: E731
        print(f"| {kind} | {len(group)} | {100 * med('before'):.1f}% | {100 * med('after'):.1f}% | "
              f"{med('points_before'):.0f} -> {med('points_after'):.0f} |")
    print(f"\nPeople added: {result['added_people']}; people missing from the redrawn files: {result['missing_people']}; "
          f"files not returned: {len(result['missing_files'])}; left out by the review: {result['left_out_by_review']}")
    if result["suggested_mark"] is not None:
        print(f"Suggested pass mark: {100 * result['suggested_mark']:.0f}% on the boundary. It passes "
              f"{100 * result['redrawn_passing_mark']:.0f}% of the redrawn outlines and "
              f"{100 * result['originals_passing_mark']:.0f}% of COCO's originals. The provisional mark "
              f"({100 * fit_module.PROVISIONAL_MIN_SHARE:.0f}%) passes {100 * result['redrawn_passing_provisional']:.0f}% "
              f"of the redrawn outlines.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--returned", type=Path, required=True, help="the redrawn folder, or a zip of it")
    parser.add_argument("--kit", type=Path, default=HERE / "kit")
    parser.add_argument("--review", type=Path, default=None, help="score only the people this review keeps")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as scratch:
        returned = args.returned
        if returned.suffix == ".zip":
            with zipfile.ZipFile(returned) as archive:
                archive.extractall(scratch)
            found = [p.parent for p in Path(scratch).rglob("kit_manifest.json")] or [p.parent for p in Path(scratch).rglob("*.json")]
            returned = found[0]
        result = compare(args.kit, returned, args.review)
    (HERE / "compare_results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    _report(result)
