#!/usr/bin/env python3
"""Measure how much the outlines of two different people share, in human-drawn files and in the seeded errors.

For every pair of people in a file whose outlines come near each other it records:
  shared_px            pixels inside both outlines
  share_of_smaller     shared_px as a share of the smaller outline
  thick_px             shared pixels left after a thin strip along the boundary is ignored. "Thin" uses the
                       tightness tolerance of the person check: the larger of 8 px and 4% of the height of the
                       shorter of the two people. A patch must also be at least 2 x tolerance^2 pixels.
  owner                which of the two people the shared pixels belong to, according to the person model's
                       saved output: "first", "second", "both" (the model also gives them to both) or "neither"

No threshold is applied here. The point is to see what natural annotation looks like next to a real error.

  .venv/bin/python -B evaluation/labelme_person_pack/overlap.py
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import zipfile

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"labelme_person_pack_{name}", HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pack, scorer = _load("build_pack"), _load("score_pack")
BANDS = ((0.0, 0.01), (0.01, 0.05), (0.05, 0.10), (0.10, 0.30), (0.30, 1.01))


def people_masks(document: dict) -> list[tuple[str, np.ndarray]]:
    """[(stable key of the first shape, filled outline)] with the parts of one person joined."""
    size = (document["imageWidth"], document["imageHeight"])
    people: dict[object, tuple[str, np.ndarray]] = {}
    for index, shape in enumerate(document["shapes"]):
        if shape["label"] != "person" or shape["shape_type"] != "polygon":
            continue
        key = ("group", shape["group_id"]) if shape["group_id"] is not None else ("shape", index)
        mask = pack.raster(shape["points"], size).astype(bool)
        people[key] = (people[key][0], people[key][1] | mask) if key in people else (f"/shapes/{index}", mask)
    return list(people.values())


def matched(mask: np.ndarray, segmentation):
    """The detected person this outline matches best, or None."""
    best, score = None, 0.3
    for person in segmentation.people:
        union = (mask | person.mask).sum()
        iou = (mask & person.mask).sum() / union if union else 0.0
        if iou >= score:
            best, score = person, iou
    return best


def thick_pixels(shared: np.ndarray, height: int) -> int:
    """Shared pixels in patches thicker than the tightness tolerance (the same rule person_coverage uses)."""
    tolerance = max(8.0, 0.04 * height)
    radius = max(1, int(round(tolerance / 2)))
    opened = cv2.morphologyEx(shared.astype(np.uint8), cv2.MORPH_OPEN, pack.disk(radius))
    count, _, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
    return int(sum(stats[i, cv2.CC_STAT_AREA] for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= 2 * tolerance ** 2))


def pairs(document: dict, segmentation) -> list[dict]:
    """One record per pair of people whose outlines share at least one pixel."""
    found = people_masks(document)
    out = []
    for i, (first_key, first) in enumerate(found):
        for second_key, second in found[i + 1:]:
            shared = first & second
            count = int(shared.sum())
            if not count:
                continue
            thick = thick_pixels(shared, min(np.ptp(np.nonzero(m)[0]) + 1 for m in (first, second)))
            a, b = matched(first, segmentation), matched(second, segmentation)
            in_a = float((shared & a.mask).sum()) / count if a is not None else 0.0
            in_b = float((shared & b.mask).sum()) / count if b is not None and b is not a else 0.0
            owner = ("both" if in_a >= 0.5 and in_b >= 0.5 else "first" if in_a >= 0.5
                     else "second" if in_b >= 0.5 else "neither")
            out.append({"first": first_key, "second": second_key, "shared_px": count, "thick_px": thick,
                        "share_of_smaller": round(count / int(min(first.sum(), second.sum())), 4), "owner": owner})
    return out


def measure(archive: Path, folder: Path = scorer.DETECTIONS) -> dict:
    files, people, near, records = 0, 0, 0, []
    with zipfile.ZipFile(archive) as source:
        for name in sorted(n for n in source.namelist() if n.endswith(".json")):
            document = json.loads(source.read(name))
            segmentation = scorer.detections(folder, name[:-5], (document["imageWidth"], document["imageHeight"]))
            count = len(people_masks(document))
            files, people, near = files + 1, people + count, near + count * (count - 1) // 2
            records += [{"file": name, **record} for record in pairs(document, segmentation)]
    return {"files": files, "people": people, "pairs_of_people": near, "overlapping": records}


def summary(name: str, result: dict) -> str:
    records = result["overlapping"]
    bands = " | ".join(str(sum(low <= r["share_of_smaller"] < high for r in records)) for low, high in BANDS)
    return (f"| {name} | {result['files']} | {result['people']} | {result['pairs_of_people']} | {len(records)} | "
            f"{bands} | {sum(r['thick_px'] > 0 for r in records)} |")


if __name__ == "__main__":
    header = ("| Dataset | Files | People | Pairs of people | Pairs sharing any pixel | under 1% | 1 to 5% | 5 to 10% | "
              "10 to 30% | 30% or more | With a thick shared patch |\n|---|---|---|---|---|---|---|---|---|---|---|")
    results = {name: measure(HERE / "archives" / f"{name}.zip")
               for name in ("labelme_pack_clean", "labelme_crowd_all", "labelme_pack_errors")}
    print(header)
    for name, result in results.items():
        print(summary(name, result))
    key = json.loads((HERE / "answer_key.json").read_text(encoding="utf-8"))
    seeded = {(case["file"], case["shape_key"], case["details"]["neighbour_shape_key"]): case["error"]
              for case in key["cases"] if case.get("family") == "overlap"}
    rows = [(seeded.get((r["file"], r["first"], r["second"])) or seeded.get((r["file"], r["second"], r["first"])), r)
            for r in results["labelme_pack_errors"]["overlapping"]]
    for label, wanted in (("seeded overlap errors", True), ("all other pairs in the error set", False)):
        chosen = [r for kind, r in rows if bool(kind) == wanted]
        owners = {owner: sum(r["owner"] == owner for r in chosen) for owner in ("first", "second", "both", "neither")}
        print(f"\n{label}: {len(chosen)} pairs, {sum(r['thick_px'] > 0 for r in chosen)} with a thick shared patch; who the shared pixels belong to: {owners}")
    for level in ("overlap_10", "overlap_30"):
        chosen = [r for kind, r in rows if kind == level]
        print(f"  {level}: {sum(r['thick_px'] > 0 for r in chosen)} of {len(chosen)} with a thick shared patch")
    natural = results["labelme_pack_clean"]["overlapping"]
    thick = [r for r in natural if r["thick_px"]]
    print("\nnatural pairs with a thick patch, by who owns the shared pixels:",
          {owner: sum(r["owner"] == owner for r in thick) for owner in ("first", "second", "both", "neither")})
    print("\nlargest natural overlaps:", sorted((r["share_of_smaller"], r["shared_px"], r["file"]) for r in natural)[-8:])
    (HERE / "overlap_results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
