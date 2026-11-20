#!/usr/bin/env python3
"""Build the drawing kit for a set of tight reference outlines.

Picks a balanced set of photos and writes them as LabelMe files with COCO's outlines as the starting point, for
a person to redraw tightly in LabelMe. The redrawn files set the boundary-fit pass mark (see compare.py).
The choice is seeded, so the same command always picks the same photos. Photos are copied, never edited.

  .venv/bin/python -B evaluation/tight_reference/build_kit.py

Writes kit/ (photos, LabelMe files, kit_manifest.json) and tight_reference_kit.zip next to this file; both
stay out of Git because they contain third-party photos.
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
from pathlib import Path
import random
import shutil
import zipfile

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
_SPEC = importlib.util.spec_from_file_location("labelme_flow_build", REPO / "evaluation/labelme_end_to_end/build_dataset.py")
labelme = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(labelme)

SEED = 20261121
COCO = REPO / ".cache/downloads/coco2017-annotations/person_keypoints_val2017.json"
PHOTO_DIRS = (REPO / ".cache/downloads/coco2017-val-images", REPO / "datasets/coco_people_30/images")
MENTOR_SHEET = ("000000258388", "000000580197", "000000174371", "000000476514", "000000153229")
# category: (photos wanted, what it is)
CATEGORIES = {
    "single": (8, "one person, at least 150 px tall"),
    "close_pair": (5, "two people standing close together or touching"),
    "partly_hidden": (4, "a person partly hidden by someone in front, drawn in separate parts"),
    "crowd": (2, "five to ten people"),
    "mentor_sheet": (len(MENTOR_SHEET), "the photos of the example sheet the mentor reviewed"),
}
CLOSE_PX = 8


def _mask(annotation, size):
    mask = np.zeros((size[1], size[0]), np.uint8)
    for part in annotation["segmentation"]:
        cv2.fillPoly(mask, [np.rint(np.asarray(part, float).reshape(-1, 2)).astype(np.int32)], 1)
    return mask


def _close(a, b, size):
    grown = cv2.dilate(_mask(a, size), np.ones((2 * CLOSE_PX + 1,) * 2, np.uint8))
    return bool((grown & _mask(b, size)).any())


def category(image, people):
    """The category this photo can serve, or None."""
    size = (image["width"], image["height"])
    tall = [p for p in people if p["bbox"][3] >= 120]
    if len(people) == 1 and people[0]["bbox"][3] >= 150 and len(people[0]["segmentation"]) == 1:
        return "single"
    if len(people) == 2 and len(tall) == 2 and all(len(p["segmentation"]) == 1 for p in people) and _close(*people, size):
        return "close_pair"
    if 2 <= len(people) <= 4 and any(len(p["segmentation"]) >= 2 and p["bbox"][3] >= 120 for p in people):
        return "partly_hidden"
    if 5 <= len(people) <= 10 and len(tall) >= 3:
        return "crowd"
    return None


def choose(seed: int = SEED):
    """[(category, image, people)] in a seeded order."""
    source = json.loads(COCO.read_text(encoding="utf-8"))
    found = collections.defaultdict(list)
    for annotation in source["annotations"]:
        found[annotation["image_id"]].append(annotation)
    pool = collections.defaultdict(list)
    sheet = {}
    for image in sorted(source["images"], key=lambda item: item["file_name"]):
        people = sorted(found.get(image["id"], []), key=lambda a: a["id"])
        stem = image["file_name"][:-4]
        if not people or any(p["iscrowd"] or not isinstance(p["segmentation"], list) for p in people):
            continue
        if not any((d / image["file_name"]).is_file() for d in PHOTO_DIRS):
            continue
        if stem in MENTOR_SHEET:
            sheet[stem] = (image, people)
            continue
        kind = category(image, people)
        if kind:
            pool[kind].append((image, people))
    rng = random.Random(seed)
    chosen = []
    for kind, (wanted, _) in CATEGORIES.items():
        if kind == "mentor_sheet":
            chosen += [(kind, *sheet[stem]) for stem in MENTOR_SHEET]
            continue
        options = pool[kind]
        rng.shuffle(options)
        chosen += [(kind, image, people) for image, people in options[:wanted]]
    return chosen


def build(output: Path = HERE, seed: int = SEED) -> dict:
    kit = output / "kit"
    if kit.exists():
        shutil.rmtree(kit)
    kit.mkdir(parents=True)
    manifest = {"seed": seed, "categories": {k: v[1] for k, v in CATEGORIES.items()}, "photos": []}
    for kind, image, people in choose(seed):
        stem = image["file_name"][:-4]
        source = next(d / image["file_name"] for d in PHOTO_DIRS if (d / image["file_name"]).is_file())
        shutil.copyfile(source, kit / image["file_name"])
        document = labelme.labelme_document(image, people)
        (kit / f"{stem}.json").write_bytes(labelme.encode(document))
        manifest["photos"].append({"file": f"{stem}.json", "image": image["file_name"], "category": kind,
                                   "people": len(people), "shapes": len(document["shapes"]),
                                   "coco_annotations": [p["id"] for p in people]})
    manifest["people"] = sum(p["people"] for p in manifest["photos"])
    (kit / "kit_manifest.json").write_bytes(labelme.encode(manifest))
    shutil.copyfile(HERE / "DRAWING_GUIDE.md", kit / "DRAWING_GUIDE.md")
    with zipfile.ZipFile(output / "tight_reference_kit.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(kit.iterdir()):
            archive.write(path, f"tight_reference_kit/{path.name}")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=SEED)
    built = build(seed=parser.parse_args().seed)
    counts = collections.Counter(p["category"] for p in built["photos"])
    people = collections.Counter()
    for p in built["photos"]:
        people[p["category"]] += p["people"]
    for kind in CATEGORIES:
        print(f"{kind:14} {counts[kind]:2d} photos, {people[kind]:3d} people")
    print(f"total          {len(built['photos']):2d} photos, {built['people']:3d} people -> {HERE / 'tight_reference_kit.zip'}")
