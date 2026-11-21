#!/usr/bin/env python3
"""Test whether Segment Anything's disagreement with itself shows which machine outlines cannot be trusted.

  .venv/bin/python -B evaluation/tight_reference/agreement.py           # runs the models, about 3 minutes
  .venv/bin/python -B evaluation/tight_reference/agreement.py --saved   # reprints from agreement_results.json

Every person in the kit is drawn again with each of draw.py's other prompts (the box alone, the box and points
inside, a wider box, points without a box) and compared with the reference drawing, boundary to boundary
(backend/app/services/boundary_distance.py). A person whose outline moves when the prompt changes has a
boundary the model is unsure of. review.json says which drawings were kept as references after a check by eye
and which were left out (behind a fence, in the dark, drawing errors); if disagreement is higher for the ones
left out, an audit could send such people to a reviewer instead of trusting the reference. Writes
agreement_results.json, which also records how long the model takes on this machine.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from backend.app.services import boundary_distance as bd  # noqa: E402
from evaluation.tight_reference import draw  # noqa: E402
from evaluation.tight_reference.compare import people  # noqa: E402

RESULTS = HERE / "agreement_results.json"
KEEP_SHARE = 0.90  # the threshold examined sends this share of the kept references through


def spread(reference, variants: dict, size) -> dict:
    """For each other prompt, the farther one-sided distance from the reference (95% of the boundary), in px."""
    out = {}
    for name, polygons in variants.items():
        if not polygons:
            out[name] = None
            continue
        result = bd.boundary_distance(bd.polygons_mask(polygons, size), bd.polygons_mask(reference, size), 1.0)
        out[name] = round(max(result.outward_px, result.inward_px), 2)
    return out


def run(kit: Path, review: Path, checkpoint: Path = draw.SAM_CHECKPOINT) -> dict:
    from segment_anything import SamPredictor, sam_model_registry

    from backend.app.inference.person_segmenter import get_segmenter

    manifest = json.loads((kit / "kit_manifest.json").read_text(encoding="utf-8"))
    verdicts = json.loads(review.read_text(encoding="utf-8"))["people"]
    segmenter = get_segmenter()
    predictor = SamPredictor(sam_model_registry["vit_b"](checkpoint=str(checkpoint)).eval())
    rows, encode_seconds, predict_seconds, predictions = [], [], 0.0, 0
    for entry in manifest["photos"]:
        document = json.loads((kit / entry["file"]).read_text(encoding="utf-8"))
        image = Image.open(kit / entry["image"]).convert("RGB")
        photo, size = np.array(image), image.size
        started = time.perf_counter()
        predictor.set_image(photo)
        encode_seconds.append(time.perf_counter() - started)
        found = segmenter.segment(image)
        drawings = {}
        for prompt in draw.PROMPTS:
            started = time.perf_counter()
            redrawn, _ = draw.draw_document(document, photo, found, predictor, prompt)
            predict_seconds += time.perf_counter() - started
            predictions += len(people(document))
            drawings[prompt] = people(redrawn)
        for cid, reference in drawings[draw.PROMPTS[0]].items():
            if isinstance(cid, tuple):
                continue
            verdict = verdicts.get(str(cid), {})
            others = {p: drawings[p].get(cid) for p in draw.PROMPTS[1:]}
            rows.append({"file": entry["file"], "category": entry["category"], "coco_annotation": cid,
                         "verdict": verdict.get("verdict"), "reason": verdict.get("reason"),
                         "diagonal_px": float(np.hypot(*size)), "spread_px": spread(reference, others, size)})
        print(f"{entry['file']}: {len(drawings[draw.PROMPTS[0]])} people", flush=True)
    return {"people": rows, "timing": {
        "device": "cpu", "photos": len(encode_seconds),
        "encode_seconds_median": round(float(np.median(encode_seconds)), 2),
        "seconds_per_person_and_prompt": round(predict_seconds / max(predictions, 1), 3)}}


def disagreement(row) -> float:
    """The largest spread over the other prompts, as a share of the photo's diagonal; a failed drawing counts fully."""
    values = [v for v in row["spread_px"].values() if v is not None]
    if len(values) < len(row["spread_px"]):
        return float("inf")
    return max(values) / row["diagonal_px"]


def analyse(result: dict) -> dict:
    kept = [r for r in result["people"] if r["verdict"] == "reference"]
    left_out = [r for r in result["people"] if r["verdict"] == "excluded"]
    k, x = np.array([disagreement(r) for r in kept]), np.array([disagreement(r) for r in left_out])
    # Chance that a left-out person disagrees more than a kept one (ties count half): the area under the ROC curve.
    auc = float(np.mean([(a > b) + 0.5 * (a == b) for a in x for b in k])) if len(k) and len(x) else None
    threshold = float(np.quantile(k[np.isfinite(k)], KEEP_SHARE)) if np.isfinite(k).any() else None
    return {"kept": len(kept), "left_out": len(left_out), "auc": auc, "threshold_diagonal_fraction": threshold,
            "kept_over_threshold": int(np.sum(k > threshold)) if threshold is not None else None,
            "left_out_over_threshold": int(np.sum(x > threshold)) if threshold is not None else None,
            "kept_median": float(np.median(k)) if len(k) else None,
            "left_out_median": float(np.median(x)) if len(x) else None}


def report(result: dict, summary: dict) -> str:
    lines = ["| Person | Kind of photo | Verdict | Spread over the other prompts (largest) | Reason left out |",
             "|---|---|---|---|---|"]
    for row in sorted(result["people"], key=disagreement, reverse=True):
        value = disagreement(row)
        shown = "a prompt failed" if not np.isfinite(value) else f"{100 * value:.2f}% ({value * row['diagonal_px']:.1f} px)"
        lines.append(f"| {row['file'][:-5]} / {row['coco_annotation']} | {row['category']} | {row['verdict']} | "
                     f"{shown} | {row['reason'] or ''} |")
    s, t = summary, result["timing"]
    lines += ["", f"Median spread: kept {100 * s['kept_median']:.2f}% of the diagonal, left out "
              f"{100 * s['left_out_median']:.2f}%. Chance a left-out person spreads more than a kept one: {s['auc']:.2f}.",
              f"A threshold passing {100 * KEEP_SHARE:.0f}% of the {s['kept']} kept references "
              f"({100 * s['threshold_diagonal_fraction']:.2f}% of the diagonal) catches {s['left_out_over_threshold']} "
              f"of the {s['left_out']} left out.",
              f"Timing on {t['device']}: {t['encode_seconds_median']} s to read a photo (median of {t['photos']}), "
              f"{t['seconds_per_person_and_prompt']} s per person and prompt."]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--kit", type=Path, default=HERE / "kit")
    parser.add_argument("--review", type=Path, default=HERE / "review.json")
    parser.add_argument("--checkpoint", type=Path, default=draw.SAM_CHECKPOINT)
    parser.add_argument("--saved", action="store_true", help="reprint the saved results without running the models")
    args = parser.parse_args()
    if args.saved:
        measured = json.loads(RESULTS.read_text(encoding="utf-8"))
    else:
        measured = run(args.kit, args.review, args.checkpoint)
    summary = analyse(measured)
    RESULTS.write_text(json.dumps({**measured, "summary": summary}, indent=2) + "\n", encoding="utf-8")
    print(report(measured, summary))
