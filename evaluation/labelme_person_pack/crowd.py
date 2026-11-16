#!/usr/bin/env python3
"""Build and score the crowd datasets: the same crowd photos with one, some or all of the people annotated.

A crowd photo has at least MIN_CROWD people, every one drawn as polygons by the COCO annotators (photos with a
COCO "crowd" region are left out, because the people inside such a region were never drawn one by one).

Outputs under this folder (kept out of Git):
  archives/labelme_crowd_all.zip    every person annotated
  archives/labelme_crowd_some.zip   about half of the people annotated, the rest left out
  archives/labelme_crowd_one.zip    one person annotated, the rest left out
  crowd_answer_key.json             which people were left out of each file, and where they are

  .venv/bin/python -B evaluation/labelme_person_pack/crowd.py build
  .venv/bin/python -B evaluation/labelme_person_pack/crowd.py score
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import random

HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location("labelme_person_pack_build", HERE / "build_pack.py")
pack = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pack)
base = pack.base

SEED = 20261115
MIN_CROWD = 5        # a crowd photo has at least this many people
KEEP_MIN_HEIGHT = 60  # the one annotated person is at least this tall when the photo has such a person
VARIANTS = ("all", "some", "one")
SIZES = (("100 px or taller", 100, 10 ** 9), ("40 to 100 px", 40, 100), ("under 40 px", 0, 40))


def crowd_photos(annotations: Path = pack.ANNOTATIONS, images: Path = pack.IMAGES):
    """[(image record, its people)] for every photo on disk that counts as a crowd."""
    source = json.loads(annotations.read_text(encoding="utf-8"))
    people: dict[int, list[dict]] = {}
    for annotation in source["annotations"]:
        people.setdefault(annotation["image_id"], []).append(annotation)
    out = []
    for image in sorted(source["images"], key=lambda item: item["file_name"]):
        found = sorted(people.get(image["id"], []), key=lambda item: item["id"])
        if (len(found) >= MIN_CROWD and (images / image["file_name"]).is_file()
                and not any(p["iscrowd"] or not isinstance(p["segmentation"], list) for p in found)):
            out.append((image, found))
    return out


def kept_people(found: list[dict], variant: str, rng: random.Random) -> list[dict]:
    """The people who keep their annotation in this variant, in source order."""
    if variant == "all":
        return list(found)
    if variant == "one":
        tall = [person for person in found if person["bbox"][3] >= KEEP_MIN_HEIGHT] or found
        return [rng.choice(tall)]
    chosen = set(rng.sample(range(len(found)), len(found) // 2))
    return [person for index, person in enumerate(found) if index in chosen]


def build(output: Path = HERE, seed: int = SEED, annotations: Path = pack.ANNOTATIONS,
          images: Path = pack.IMAGES) -> dict:
    photos = crowd_photos(annotations, images)
    if not photos:
        raise SystemExit(f"No crowd photos in {images}.")
    rng = random.Random(seed)
    members = {variant: {} for variant in VARIANTS}
    cases = []
    for image, found in photos:
        stem = Path(image["file_name"]).stem
        photo = (images / image["file_name"]).read_bytes()
        case = {"file": f"{stem}.json", "image": image["file_name"], "people": len(found)}
        for variant in VARIANTS:
            kept = kept_people(found, variant, rng)
            kept_ids = {person["id"] for person in kept}
            members[variant][f"{stem}.json"] = base.encode(base.labelme_document(image, kept))
            members[variant][image["file_name"]] = photo
            case[variant] = {"annotated": len(kept), "left_out": [
                {"coco_annotation": person["id"], "height_px": round(person["bbox"][3]),
                 "box": [round(person["bbox"][0], 2), round(person["bbox"][1], 2),
                         round(person["bbox"][0] + person["bbox"][2], 2), round(person["bbox"][1] + person["bbox"][3], 2)]}
                for person in found if person["id"] not in kept_ids]}
        cases.append(case)
    archives = {variant: base.write_zip(output / "archives" / f"labelme_crowd_{variant}.zip", members[variant])
                for variant in VARIANTS}
    key = {"seed": seed, "note": "Never upload this file or use it in prompts, feedback or guidelines.",
           "photos": len(photos), "people": sum(case["people"] for case in cases),
           "annotated": {variant: sum(case[variant]["annotated"] for case in cases) for variant in VARIANTS},
           "archives_sha256": archives, "cases": cases}
    (output / "crowd_answer_key.json").write_bytes(base.encode(key))
    return key


def score(output: Path = HERE, folder: Path | None = None) -> dict:
    """What `unannotated_people` reports on each variant, from the person model's saved output."""
    import zipfile
    spec = importlib.util.spec_from_file_location("labelme_person_pack_score", HERE / "score_pack.py")
    scorer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scorer)
    folder = folder or scorer.DETECTIONS
    key = json.loads((output / "crowd_answer_key.json").read_text(encoding="utf-8"))
    coverage = scorer.validate_check_parameters("person_coverage", {})
    missing = scorer.validate_check_parameters("unannotated_people", {})
    result = {}
    for variant in VARIANTS:
        archive = zipfile.ZipFile(output / "archives" / f"labelme_crowd_{variant}.zip")
        row = {"photos": key["photos"], "annotated": key["annotated"][variant], "left_out": 0, "photos_reported": 0,
               "reports": 0, "reports_matching_nobody_left_out": 0,
               "found_by_size": {name: [0, 0] for name, _, _ in SIZES}}
        for case in key["cases"]:
            document = json.loads(archive.read(case["file"]))
            found = scorer.detections(folder, case["file"][:-5], (document["imageWidth"], document["imageHeight"]))
            _, reported = scorer.judge_file(document, found, coverage, missing)
            left_out = case[variant]["left_out"]
            row["left_out"] += len(left_out)
            row["photos_reported"] += bool(reported)
            row["reports"] += len(reported)
            row["reports_matching_nobody_left_out"] += sum(
                not any(scorer.box_overlap(box, person["box"]) >= 0.3 for person in left_out) for box in reported)
            for person in left_out:
                name = next(name for name, low, high in SIZES if low <= person["height_px"] < high)
                row["found_by_size"][name][1] += 1
                row["found_by_size"][name][0] += any(scorer.box_overlap(box, person["box"]) >= 0.3 for box in reported)
        result[variant] = row
    (output / "crowd_results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def table(result: dict) -> str:
    lines = ["| Dataset | Photos | People annotated | People left out | Photos reported | Left out and reported: "
             + " | ".join(name for name, _, _ in SIZES) + " | Reports matching nobody left out |",
             "|---|---|---|---|---|---|---|---|---|"]
    for variant, row in result.items():
        sizes = " | ".join(f"{hit} of {total}" if total else "-" for hit, total in row["found_by_size"].values())
        lines.append(f"| `labelme_crowd_{variant}` | {row['photos']} | {row['annotated']} | {row['left_out']} | "
                     f"{row['photos_reported']} | {sizes} | {row['reports_matching_nobody_left_out']} |")
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=("build", "score"))
    parser.add_argument("--output", type=Path, default=HERE)
    args = parser.parse_args()
    if args.step == "build":
        built = build(args.output)
        print(f"{built['photos']} crowd photos, {built['people']} people; annotated: {built['annotated']}")
    else:
        print(table(score(args.output)))
