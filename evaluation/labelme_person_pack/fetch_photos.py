#!/usr/bin/env python3
"""Download more COCO 2017 validation photos of people, so build_pack.py can make a larger pack.

Only photos the pack can use are chosen: 1 to 10 people, every one drawn as polygons. The choice is seeded, so
the same command always fetches the same photos. Photos already on disk are skipped. Each photo is about 160 KB.

  .venv/bin/python -B evaluation/labelme_person_pack/fetch_photos.py --count 500
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import urllib.request

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ANNOTATIONS = REPO / ".cache/downloads/coco2017-annotations/person_keypoints_val2017.json"
IMAGES = REPO / ".cache/downloads/coco2017-val-images"
SOURCE = "http://images.cocodataset.org/val2017/"
SEED = 20261114
MAX_PEOPLE = 10


def usable_photos(annotations: Path = ANNOTATIONS) -> list[str]:
    """File names of every validation photo with 1 to MAX_PEOPLE people, all drawn as polygons."""
    source = json.loads(annotations.read_text(encoding="utf-8"))
    people: dict[int, list[dict]] = {}
    for annotation in source["annotations"]:
        people.setdefault(annotation["image_id"], []).append(annotation)
    return sorted(image["file_name"] for image in source["images"]
                  if 1 <= len(found := people.get(image["id"], [])) <= MAX_PEOPLE
                  and not any(p["iscrowd"] or not isinstance(p["segmentation"], list) for p in found))


def choose(names: list[str], have: set[str], count: int, seed: int = SEED) -> list[str]:
    order = list(names)
    random.Random(seed).shuffle(order)
    return [name for name in order if name not in have][:count]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, required=True, help="how many more photos to download")
    parser.add_argument("--images", type=Path, default=IMAGES)
    parser.add_argument("--annotations", type=Path, default=ANNOTATIONS)
    args = parser.parse_args()
    args.images.mkdir(parents=True, exist_ok=True)
    names = usable_photos(args.annotations)
    wanted = choose(names, {path.name for path in args.images.glob("*.jpg")}, args.count)
    print(f"{len(names)} usable photos in the validation set; downloading {len(wanted)}")
    for number, name in enumerate(wanted, 1):
        with urllib.request.urlopen(SOURCE + name, timeout=60) as response:
            data = response.read()
        if not data.startswith(b"\xff\xd8"):
            raise SystemExit(f"{name}: the download is not a JPEG; stopped.")
        (args.images / name).write_bytes(data)
        if number % 25 == 0 or number == len(wanted):
            print(f"{number}/{len(wanted)}", flush=True)
