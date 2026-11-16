#!/usr/bin/env python3
"""Run the application's person model once on each source photo and save what it finds, for score_pack.py.

One <COCO image id>.npz per photo: person boxes, scores and packed masks. Photos that already have a file are
skipped. The model takes about 1 to 3 seconds per photo on a laptop CPU, so new photos are the only cost.

  .venv/bin/python -B evaluation/labelme_person_pack/save_detections.py
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.app.inference.person_segmenter import get_segmenter  # noqa: E402

IMAGES = REPO / ".cache/downloads/coco2017-val-images"
DETECTIONS = REPO / ".cache/validation/missing-person-20261109/cache"

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--images", type=Path, default=IMAGES)
    parser.add_argument("--detections", type=Path, default=DETECTIONS)
    parser.add_argument("--limit", type=int, default=None, help="stop after this many new photos")
    args = parser.parse_args()
    args.detections.mkdir(parents=True, exist_ok=True)
    todo = [path for path in sorted(args.images.glob("*.jpg"))
            if not (args.detections / f"{int(path.stem)}.npz").is_file()][:args.limit]
    print(f"{len(todo)} photos without saved model output")
    segmenter = get_segmenter() if todo else None
    start = time.monotonic()
    for number, path in enumerate(todo, 1):
        found = segmenter.segment(Image.open(path).convert("RGB")).people
        masks = (np.packbits(np.array([person.mask for person in found], dtype=bool).reshape(len(found), -1), axis=1)
                 if found else np.zeros((0, 0), np.uint8))
        np.savez_compressed(args.detections / f"{int(path.stem)}.npz",
                            boxes=np.array([person.box for person in found], dtype=np.int32).reshape(-1, 4),
                            scores=np.array([person.score for person in found], dtype=np.float32), masks=masks)
        if number % 20 == 0 or number == len(todo):
            print(f"{number}/{len(todo)} photos, {time.monotonic() - start:.0f} s", flush=True)
