#!/usr/bin/env python3
"""Score the boundary-fit measure against the mentor's labels and the LabelMe person pack.

  .venv/bin/python -B evaluation/boundary_fit/evaluate.py              # the measure as built
  .venv/bin/python -B evaluation/boundary_fit/evaluate.py --variants   # also the edge finders that were tried
  .venv/bin/python -B evaluation/boundary_fit/evaluate.py --person-check  # also today's person check (needs its model file)

Needs the local COCO 2017 person file and photos (see evaluation/labelme_person_pack/README.md). The pack
part is skipped when its archives have not been built. No model, service or GPU is used.
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import io
import json
from pathlib import Path
import sys
import zlib
import zipfile

import cv2
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from backend.app.services import boundary_fit as fit_module  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("labelme_flow_build", REPO / "evaluation/labelme_end_to_end/build_dataset.py")
labelme = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(labelme)

COCO = REPO / ".cache/downloads/coco2017-annotations/person_keypoints_val2017.json"
PHOTO_DIRS = (REPO / "datasets/coco_people_30/images", REPO / ".cache/downloads/coco2017-val-images")
PACK = REPO / "evaluation/labelme_person_pack"
MIN_HEIGHT = 100


def _median_canny(channel, sigma):
    blurred = cv2.GaussianBlur(channel, (0, 0), sigma)
    median = float(np.median(blurred))
    return cv2.Canny(blurred, int(max(0, 0.66 * median)), int(min(255, 1.33 * median)))


def _grey(image):
    return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)


def _boosted(image):
    return cv2.createCLAHE(2.0, (8, 8)).apply(_grey(image))


def _colour_edges(image):
    lab = cv2.cvtColor(image, cv2.COLOR_RGB2LAB)
    out = np.zeros(lab.shape[:2], np.uint8)
    for channel in cv2.split(lab)[1:]:
        blurred = cv2.GaussianBlur(channel, (0, 0), 1.5)
        strength = np.hypot(cv2.Sobel(blurred, cv2.CV_32F, 1, 0), cv2.Sobel(blurred, cv2.CV_32F, 0, 1))
        high = max(float(np.percentile(strength, 95)), 40.0)
        out |= cv2.Canny(blurred, 0.5 * high, high, L2gradient=True)
    return out


def _distance(edges):
    return cv2.distanceTransform(255 - edges, cv2.DIST_L2, 5)


VARIANTS = {
    "brightness thresholds, one blur": lambda im: _distance(_median_canny(_grey(im), 0.8)),
    "brightness thresholds, two blurs": lambda im: _distance(_median_canny(_grey(im), 0.8) | _median_canny(_grey(im), 2.0)),
    "contrast boosted": lambda im: _distance(_median_canny(_boosted(im), 0.8)),
    "contrast-spread thresholds, two blurs (built)": fit_module.edge_distance,
    "contrast boosted, two blurs": lambda im: _distance(_median_canny(_boosted(im), 0.8) | _median_canny(_boosted(im), 2.0)),
    "boosted, two blurs, colour": lambda im: _distance(_median_canny(_boosted(im), 0.8) | _median_canny(_boosted(im), 2.0)
                                                      | _colour_edges(im)),
}


def people(document):
    """[[shape indexes]] per person: parts sharing a group_id are one person."""
    groups = collections.OrderedDict()
    for index, shape in enumerate(document["shapes"]):
        if shape["label"] == "person":
            groups.setdefault(("g", shape["group_id"]) if shape["group_id"] is not None else ("s", index), []).append(index)
    return list(groups.values())


def score(distance, document, indexes):
    return fit_module.boundary_fit(distance, [document["shapes"][i]["points"] for i in indexes])


def mentor_cases():
    """[(photo stem, document, photo, {coco id: (label, shape indexes)})] for the example sheet."""
    labels = json.loads((HERE / "sir_labels.json").read_text(encoding="utf-8"))
    source = json.loads(COCO.read_text(encoding="utf-8"))
    images = {image["file_name"][:-4]: image for image in source["images"]}
    found = collections.defaultdict(list)
    for annotation in source["annotations"]:
        found[annotation["image_id"]].append(annotation)
    out = []
    for stem, verdicts in labels["photos"].items():
        image = images[stem]
        drawn = sorted((a for a in found[image["id"]] if not a["iscrowd"] and isinstance(a["segmentation"], list)),
                       key=lambda a: a["id"])
        document = labelme.labelme_document(image, drawn)
        path = next(d / f"{stem}.jpg" for d in PHOTO_DIRS if (d / f"{stem}.jpg").is_file())
        photo = np.array(Image.open(path).convert("RGB"))
        wanted = {cid: "tight" for cid in verdicts["tight"]} | {cid: "not tight" for cid in verdicts["not_tight"]}
        by_id = {}
        for indexes in people(document):
            cid = int(document["shapes"][indexes[0]]["description"].rsplit(" ", 1)[1])
            if cid in wanted:
                by_id[cid] = (wanted[cid], indexes)
        assert set(by_id) == set(wanted), f"{stem}: labelled people not found"
        out.append((stem, document, photo, by_id))
    return out, labels["names"]


def mentor_table(cases, names, edge=fit_module.edge_distance, show=True):
    rows = []
    for stem, document, photo, by_id in cases:
        distance = edge(photo)
        for cid, (label, indexes) in by_id.items():
            result = score(distance, document, indexes)
            rows.append((label, stem, cid, indexes, result))
    rows.sort(key=lambda row: -(row[4].share_on_edge or 0))
    if show:
        print("| Mentor | Photo | Annotation | On the boundary | Busy background | Stretches off it | Provisional result |")
        print("|---|---|---|---|---|---|---|")
        for label, stem, cid, indexes, result in rows:
            name = names.get(str(cid), "")
            keys = ", ".join(f"/shapes/{i}" for i in indexes)
            print(f"| {label} | {stem} | {keys}{' (' + name + ')' if name else ''} | {100 * result.share_on_edge:.1f}% | "
                  f"{100 * result.busy_share:.0f}% | {len(result.regions)} | {fit_module.provisional_outcome(result)} |")
    good = [r[4].share_on_edge for r in rows if r[0] == "tight"]
    rest = [r[4].share_on_edge for r in rows if r[0] != "tight"]
    return good, rest


def person_check_table(cases):
    """What today's person check says about the same labelled people, judged part by part as audits do now."""
    from backend.app.checks.registry import validate_check_parameters
    from backend.app.inference.person_segmenter import get_segmenter
    from backend.app.services import person_coverage

    params = validate_check_parameters("person_coverage", {})
    segmenter = get_segmenter()
    counts = {"tight": collections.Counter(), "not tight": collections.Counter()}
    for stem, document, photo, by_id in cases:
        found = segmenter.segment(Image.fromarray(photo))
        size = (document["imageWidth"], document["imageHeight"])
        for cid, (label, indexes) in by_id.items():
            outcomes = [person_coverage.object_coverage(
                {"object_id": i, "label": "person", "polygons": [document["shapes"][i]["points"]]},
                "labelme", size, None, found, params)[0].value for i in indexes]
            counts[label]["fail" if "fail" in outcomes else "unsure" if "unsure" in outcomes else "pass"] += 1
    print("\n| Mentor's verdict | People | Person check today: pass | fail | unsure |")
    print("|---|---|---|---|---|")
    for label, count in counts.items():
        print(f"| {label} | {sum(count.values())} | {count['pass']} | {count['fail']} | {count['unsure']} |")


def pack_tables(edge=fit_module.edge_distance, every=1, show=True):
    clean_path, error_path = PACK / "archives/labelme_pack_clean.zip", PACK / "archives/labelme_pack_errors.zip"
    if not clean_path.is_file():
        if show:
            print("\nThe LabelMe person pack is not built; its tables are skipped.")
        return None
    clean, errors = zipfile.ZipFile(clean_path), zipfile.ZipFile(error_path)
    key = json.loads((PACK / "answer_key.json").read_text(encoding="utf-8"))
    photos, maps = {}, {}

    def distance(name):
        if name not in maps:
            photos[name] = np.array(Image.open(io.BytesIO(clean.read(name))).convert("RGB"))
            maps[name] = edge(photos[name])
        return maps[name]

    shares, drops, busy, outcomes = [], [], 0, collections.Counter()
    for name in sorted(n for n in clean.namelist() if n.endswith(".json"))[::every]:
        document = json.loads(clean.read(name))
        size = (document["imageWidth"], document["imageHeight"])
        for indexes in people(document):
            points = document["shapes"][indexes[0]]["points"]
            ys = [p[1] for p in points]
            if len(indexes) > 1 or max(ys) - min(ys) < MIN_HEIGHT:
                continue
            result = score(distance(name[:-5] + ".jpg"), document, indexes)
            shares.append(result.share_on_edge)
            busy += result.busy
            outcomes[fit_module.provisional_outcome(result)] += 1
            moved = fit_module._moved_outward([points], size, 4)
            if moved:
                drops.append(result.share_on_edge - fit_module.boundary_fit(distance(name[:-5] + ".jpg"), moved).share_on_edge)
    fell = collections.defaultdict(list)
    for case in key["cases"]:
        if case.get("family") not in ("loose", "shrunk", "part") or (every > 1 and zlib.crc32(case["file"].encode()) % every):
            continue
        index = int(case["shape_key"].rsplit("/", 1)[1])
        before = fit_module.boundary_fit(distance(case["image"]), [json.loads(clean.read(case["file"]))["shapes"][index]["points"]])
        after = fit_module.boundary_fit(distance(case["image"]), [json.loads(errors.read(case["file"]))["shapes"][index]["points"]])
        fell[case["error"]].append((before.share_on_edge, after.share_on_edge))
    if show:
        values = np.array(shares)
        print(f"\nPack: {len(values)} one-part human outlines at least {MIN_HEIGHT} px tall, {len(maps)} photos.")
        print("On the boundary, 10th / 25th / 50th / 75th / 90th percentile:",
              " / ".join(f"{v:.1f}%" for v in 100 * np.percentile(values, [10, 25, 50, 75, 90])))
        print(f"Provisional results: {dict(outcomes)}; busy background: {busy}")
        print(f"Same outline moved outward 4 px: median fall {100 * float(np.median(drops)):.1f} points; fell in "
              f"{100 * float(np.mean(np.array(drops) > 0)):.0f}%")
        print("\n| Seeded error | Outlines | On the boundary before | After | Fell by 10 points or more |")
        print("|---|---|---|---|---|")
        for kind, pairs in fell.items():
            pairs = np.array(pairs)
            print(f"| `{kind}` | {len(pairs)} | {100 * np.median(pairs[:, 0]):.1f}% | {100 * np.median(pairs[:, 1]):.1f}% | "
                  f"{100 * float(np.mean(pairs[:, 0] - pairs[:, 1] >= 0.10)):.0f}% |")
    return shares, drops, fell


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--variants", action="store_true", help="also compare the edge finders that were tried")
    parser.add_argument("--person-check", action="store_true", help="also run today's person check on the labelled people")
    args = parser.parse_args()
    cases, names = mentor_cases()
    good, rest = mentor_table(cases, names)
    print(f"\nThe mentor's {len(good)} tight outlines: {', '.join(f'{100 * g:.1f}%' for g in good)}. "
          f"Best of the {len(rest)} not tight: {100 * max(rest):.1f}%. Not-tight outlines scoring at least as well as "
          f"the weaker tight one: {sum(r >= min(good) for r in rest)}.")
    if args.person_check:
        person_check_table(cases)
    pack_tables()
    if args.variants:
        print("\n| Edge finder | Weaker tight outline | Best not tight | Not tight at or above it | Pack median | "
              "Moved 4 px: median fall | loose_3 fell 10+ | shrunk_3 fell 10+ |")
        print("|---|---|---|---|---|---|---|---|")
        for name, edge in VARIANTS.items():
            good, rest = mentor_table(cases, names, edge, show=False)
            shares, drops, fell = pack_tables(edge, every=1, show=False) or ([], [], {})
            fell_by = {k: 100 * float(np.mean([a - b >= 0.10 for a, b in v])) for k, v in fell.items()}
            print(f"| {name} | {100 * min(good):.1f}% | {100 * max(rest):.1f}% | {sum(r >= min(good) for r in rest)} of {len(rest)} | "
                  f"{100 * float(np.median(shares)):.1f}% | {100 * float(np.median(drops)):.1f} | "
                  f"{fell_by.get('loose_3', 0):.0f}% | {fell_by.get('shrunk_3', 0):.0f}% |")
