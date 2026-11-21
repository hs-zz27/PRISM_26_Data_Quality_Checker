#!/usr/bin/env python3
"""Check the distance measure with a reference drawn without help from the outline being judged.

  .venv/bin/python -B evaluation/tight_reference/independent.py

The references in drawn/ start from COCO's outline (its box, points inside it, a band around it), which an
audit cannot do: an outline missing a head would give a reference missing the head too. Here each person's
reference comes from the person model alone. Segment Anything is prompted with the model's box for the person,
points deep inside the model's mask and points on the other people it found, and keeps the candidate that best
matches the model's mask. The outline is used only to pick which detected person it is about, as
person_coverage does. On the mentor's five example photos, COCO's outlines are then measured against these
references and compared with his verdicts and with the checked references. Writes independent_results.json.
"""

from __future__ import annotations

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
from backend.app.services import boundary_distance as bd  # noqa: E402
from evaluation.tight_reference import draw  # noqa: E402
from evaluation.tight_reference.compare import people  # noqa: E402
from evaluation.tight_reference.distances import sir_verdicts  # noqa: E402

MIN_MATCH_IOU = 0.3  # an outline is about the detected person it overlaps most, if at least this much
BAD_REFERENCE = 0.01  # a reference farther than this share of the diagonal from the checked one is badly off
AGREEMENT_CUTS = (0.80, 0.85, 0.90)


def reference_from_detection(predictor, detected, index: int) -> np.ndarray:
    """Segment Anything's outline of detected person ``index``, prompted from the person model only."""
    mask = detected[index].mask
    others = np.zeros_like(mask)
    for other, person in enumerate(detected):
        if other != index:
            others |= person.mask
    height = int(mask.any(axis=1).sum())
    spacing = max(10, int(0.15 * height))
    core = cv2.erode(mask.astype(np.uint8), draw.disk(max(3, int(0.03 * height))))
    if not core.any():
        core = mask.astype(np.uint8)
    neighbours = cv2.erode(others.astype(np.uint8), draw.disk(2)) & (1 - mask.astype(np.uint8))
    positive = draw._interior_points(core, 3, spacing)
    negative = draw._interior_points(neighbours, 3, spacing)
    coords = np.array(positive + negative, float).reshape(-1, 2)
    labels = np.array([1] * len(positive) + [0] * len(negative))
    x0, y0, x1, y1 = detected[index].box
    masks, scores, _ = predictor.predict(point_coords=coords if len(coords) else None,
                                         point_labels=labels if len(coords) else None,
                                         box=np.array([x0, y0, x1 + 1, y1 + 1], float), multimask_output=True)
    best = max(range(len(masks)), key=lambda i: (draw._iou(masks[i], mask), float(scores[i])))
    return masks[best] & ~(cv2.erode(others.astype(np.uint8), draw.disk(2)) > 0)


def run(kit: Path = HERE / "kit", drawn: Path = HERE / "drawn", review: Path = HERE / "review.json") -> list[dict]:
    from segment_anything import SamPredictor, sam_model_registry

    from backend.app.inference.person_segmenter import get_segmenter

    manifest = json.loads((kit / "kit_manifest.json").read_text(encoding="utf-8"))
    kept = json.loads(review.read_text(encoding="utf-8"))["people"]
    verdicts, _ = sir_verdicts()
    segmenter = get_segmenter()
    predictor = SamPredictor(sam_model_registry["vit_b"](checkpoint=str(draw.SAM_CHECKPOINT)).eval())
    rows = []
    for entry in manifest["photos"]:
        document = json.loads((kit / entry["file"]).read_text(encoding="utf-8"))
        checked = people(json.loads((drawn / entry["file"]).read_text(encoding="utf-8")))
        image = Image.open(kit / entry["image"]).convert("RGB")
        size = image.size
        predictor.set_image(np.array(image))
        detected = segmenter.segment(image).people
        for cid, polygons in people(document).items():
            coco = bd.polygons_mask(polygons, size)
            overlaps = [draw._iou(coco, person.mask) for person in detected]
            row = {"file": entry["file"], "category": entry["category"], "coco_annotation": cid,
                   "mentor": verdicts.get(cid),
                   "kept_reference": kept.get(str(cid), {}).get("verdict") == "reference",
                   "diagonal_px": float(np.hypot(*size)), "matched": False}
            if overlaps and max(overlaps) >= MIN_MATCH_IOU:
                reference = reference_from_detection(predictor, detected, int(np.argmax(overlaps)))
                result = bd.boundary_distance(coco, reference, 1.0)
                row.update(matched=True, outward_px=result.outward_px, inward_px=result.inward_px,
                           model_iou=round(max(overlaps), 3),
                           model_vs_reference_iou=round(draw._iou(reference, detected[int(np.argmax(overlaps))].mask), 3))
                if row["kept_reference"] and cid in checked:  # how far it is from the checked reference
                    against = bd.boundary_distance(reference, bd.polygons_mask(checked[cid], size), 1.0)
                    row["reference_vs_checked_px"] = max(against.outward_px, against.inward_px)
            rows.append(row)
    return rows


def report(rows: list[dict]) -> str:
    worst = lambda r: max(r["outward_px"], r["inward_px"]) / r["diagonal_px"]  # noqa: E731
    lines = ["| Person | Mentor | Outward 95% | Inward 95% | Worse side, % of diagonal | Independent vs checked reference |",
             "|---|---|---|---|---|---|"]
    for r in sorted((r for r in rows if r["matched"] and r["mentor"]), key=worst):
        vs = f"{r['reference_vs_checked_px']:.1f} px" if "reference_vs_checked_px" in r else "no checked reference"
        lines.append(f"| {r['file'][:-5]} / {r['coco_annotation']} | {r['mentor']} | {r['outward_px']:.1f} px | "
                     f"{r['inward_px']:.1f} px | {100 * worst(r):.2f}% | {vs} |")
    tight = [worst(r) for r in rows if r["matched"] and r["mentor"] == "tight"]
    rejected = [worst(r) for r in rows if r["matched"] and r["mentor"] == "not tight"]
    unmatched = [r["coco_annotation"] for r in rows if not r["matched"]]
    if tight and rejected:
        lines += ["", f"Tight outlines at most {100 * max(tight):.2f}% of the diagonal; rejected ones from "
                  f"{100 * min(rejected):.2f}%. {sum(x > max(tight) for x in rejected)} of {len(rejected)} rejected "
                  f"outlines lie farther than both tight ones. Not matched to a detected person: {unmatched or 'none'}."]
    checked = [r for r in rows if "reference_vs_checked_px" in r]
    bad = [r for r in checked if r["reference_vs_checked_px"] / r["diagonal_px"] > BAD_REFERENCE]
    lines += ["", f"Against the {len(checked)} checked references (all kinds of photo), the reference drawn from the "
              f"person model alone is off by more than {100 * BAD_REFERENCE:.0f}% of the diagonal for {len(bad)}.",
              "", "| Person model vs reference IoU below | Flagged | Of them badly off | Badly off not flagged |",
              "|---|---|---|---|"]
    for cut in AGREEMENT_CUTS:
        flagged = [r for r in checked if r["model_vs_reference_iou"] < cut]
        lines.append(f"| {cut:.2f} | {len(flagged)} | {sum(r in bad for r in flagged)} | "
                     f"{sum(r not in flagged for r in bad)} |")
    return "\n".join(lines)


if __name__ == "__main__":
    measured = run()
    (HERE / "independent_results.json").write_text(json.dumps(measured, indent=2) + "\n", encoding="utf-8")
    print(report(measured))
