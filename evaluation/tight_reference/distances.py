#!/usr/bin/env python3
"""Measure how far COCO's outlines lie from the reference outlines, and test that against the mentor's verdicts.

  .venv/bin/python -B evaluation/tight_reference/distances.py

For every person kept as a reference (review.json), COCO's outline is compared with the machine-drawn
reference in drawn/ by backend/app/services/boundary_distance.py. The mentor judged 21 of COCO's outlines
(evaluation/boundary_fit/sir_labels.json): two tight, the rest not. If the measure agrees with him, his two
tight outlines lie closer to their references than every outline he rejected, and how close they lie gives the
tolerance. Writes distance_results.json.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from backend.app.services import boundary_distance as bd  # noqa: E402
from evaluation.tight_reference.compare import match, people  # noqa: E402

SIR_LABELS = REPO / "evaluation/boundary_fit/sir_labels.json"
DIAGONAL_FRACTIONS = (0.005, 0.0075, 0.01, 0.02)  # published boundary tolerances, as shares of the diagonal


def sir_verdicts(path: Path = SIR_LABELS) -> tuple[dict[int, str], dict[int, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    verdicts = {}
    for photo in data["photos"].values():
        verdicts.update({cid: "tight" for cid in photo["tight"]})
        verdicts.update({cid: "not tight" for cid in photo["not_tight"]})
    return verdicts, {int(k): v for k, v in data["names"].items()}


def measure(kit: Path, drawn: Path, review: Path, labels: Path = SIR_LABELS) -> list[dict]:
    manifest = json.loads((kit / "kit_manifest.json").read_text(encoding="utf-8"))
    kept = json.loads(review.read_text(encoding="utf-8"))["people"]
    verdicts, _ = sir_verdicts(labels)
    rows = []
    for photo in manifest["photos"]:
        original = json.loads((kit / photo["file"]).read_text(encoding="utf-8"))
        redrawn = json.loads((drawn / photo["file"]).read_text(encoding="utf-8"))
        size = (original["imageWidth"], original["imageHeight"])
        before, after = people(original), people(redrawn)
        for key, cid in match(before, after, size).items():
            if cid is None or kept.get(str(cid), {}).get("verdict") != "reference":
                continue
            coco, reference = bd.polygons_mask(before[cid], size), bd.polygons_mask(after[key], size)
            row = {"file": photo["file"], "category": photo["category"], "coco_annotation": cid,
                   "mentor": verdicts.get(cid), "diagonal_px": float(np.hypot(*size)), "by_tolerance": {}}
            for fraction in DIAGONAL_FRACTIONS:
                result = bd.boundary_distance(coco, reference, bd.tolerance_for(size, fraction))
                row["by_tolerance"][str(fraction)] = {
                    "tolerance_px": result.tolerance_px, "outward_share": result.outward_share,
                    "inward_share": result.inward_share, "boundary_iou": result.boundary_iou,
                    "agreement": result.agreement, "too_small": result.too_small}
            row.update(outward_px=result.outward_px, outward_max_px=result.outward_max_px,
                       inward_px=result.inward_px, inward_max_px=result.inward_max_px,
                       mask_iou=result.mask_iou, reference_height_px=result.reference_height_px)
            rows.append(row)
    return rows


def calibrate(rows: list[dict]) -> dict:
    """The tolerance the mentor's tight outlines need, and what it does to every other outline."""
    def worst(row):  # the larger one-sided distance, as a share of the photo's diagonal
        return max(row["outward_px"], row["inward_px"]) / row["diagonal_px"]

    tight = [r for r in rows if r["mentor"] == "tight"]
    rejected = [r for r in rows if r["mentor"] == "not tight"]
    others = [r for r in rows if r["mentor"] is None]
    if not tight:
        return {}
    fraction = max(worst(r) for r in tight)
    return {
        "tolerance_diagonal_fraction": fraction,
        "tolerance_px_at_640x480": fraction * 800.0,
        "tight_passing": sum(worst(r) <= fraction for r in tight), "tight": len(tight),
        "rejected_failing": sum(worst(r) > fraction for r in rejected), "rejected": len(rejected),
        "closest_rejected_diagonal_fraction": min((worst(r) for r in rejected), default=None),
        "others_failing": sum(worst(r) > fraction for r in others), "others": len(others),
    }


def report(rows: list[dict], calibration: dict) -> str:
    _, names = sir_verdicts()
    one = str(0.01)
    lines = ["| Person | Mentor | Outward 95% | Outward max | Inward 95% | Inward max | Mask IoU | Boundary IoU at 1% | Agreement |",
             "|---|---|---|---|---|---|---|---|---|"]
    judged = sorted((r for r in rows if r["mentor"]), key=lambda r: (r["mentor"] != "tight", r["outward_px"]))
    for r in judged:
        t = r["by_tolerance"][one]
        name = names.get(r["coco_annotation"], "")
        lines.append(f"| {r['file'][:-5]} / {r['coco_annotation']}{' ' + name if name else ''} | {r['mentor']} | "
                     f"{r['outward_px']:.1f} px | {r['outward_max_px']:.1f} px | {r['inward_px']:.1f} px | "
                     f"{r['inward_max_px']:.1f} px | {r['mask_iou']:.3f} | {t['boundary_iou']:.3f} | {t['agreement']:.3f} |")
    lines += ["", "| Kind of photo | People | Outward 95% (median) | Inward 95% (median) | Agreement at 1% (median) |",
              "|---|---|---|---|---|"]
    for kind in dict.fromkeys(r["category"] for r in rows):
        group = [r for r in rows if r["category"] == kind]
        med = lambda values: float(np.median(values))  # noqa: E731
        lines.append(f"| {kind} | {len(group)} | {med([r['outward_px'] for r in group]):.1f} px | "
                     f"{med([r['inward_px'] for r in group]):.1f} px | "
                     f"{med([r['by_tolerance'][one]['agreement'] for r in group]):.3f} |")
    if calibration:
        c = calibration
        lines += ["", f"Tolerance from the mentor's tight outlines: {100 * c['tolerance_diagonal_fraction']:.2f}% of the "
                  f"diagonal (about {c['tolerance_px_at_640x480']:.1f} px on a 640x480 photo). At it, "
                  f"{c['tight_passing']} of {c['tight']} tight outlines pass, {c['rejected_failing']} of "
                  f"{c['rejected']} rejected outlines fail (the closest rejected one is at "
                  f"{100 * c['closest_rejected_diagonal_fraction']:.2f}%), and {c['others_failing']} of {c['others']} "
                  f"other COCO outlines fail."]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--kit", type=Path, default=HERE / "kit")
    parser.add_argument("--drawn", type=Path, default=HERE / "drawn")
    parser.add_argument("--review", type=Path, default=HERE / "review.json")
    args = parser.parse_args()
    measured = measure(args.kit, args.drawn, args.review)
    calibration = calibrate(measured)
    (HERE / "distance_results.json").write_text(
        json.dumps({"calibration": calibration, "people": measured}, indent=2) + "\n", encoding="utf-8")
    print(report(measured, calibration))
