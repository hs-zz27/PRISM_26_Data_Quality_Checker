"""The LabelMe person pack: each seeded error changes the right place by the right amount, and the build is reproducible.

No application service or model runs here. Tests that need the local COCO photos (kept out of Git) are skipped
with a reason when those are absent; the error-injection tests need nothing.
"""

import copy
import importlib.util
import json
from pathlib import Path
import zipfile

import numpy as np
import pytest

SPEC = importlib.util.spec_from_file_location("labelme_person_pack", Path(__file__).resolve().parents[1] / "build_pack.py")
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)

needs_sources = pytest.mark.skipif(not p.ANNOTATIONS.is_file() or not any(p.IMAGES.glob("*.jpg")),
                                   reason="local COCO photos are not downloaded (see README.md)")
SIZE = (700, 500)
BLOCKS = {"head": (180, 40, 220, 90), "torso": (160, 90, 240, 250), "arm_a": (120, 100, 160, 240),
          "arm_b": (240, 100, 280, 240), "leg_a": (160, 250, 195, 400), "leg_b": (205, 250, 240, 400)}
JOINTS = {"nose": (200, 65), "left_shoulder": (240, 100), "right_shoulder": (160, 100), "left_elbow": (260, 160),
          "left_wrist": (260, 220), "left_knee": (222, 320), "left_ankle": (222, 390)}


def figure(shift=0):
    """(shape, COCO-style annotation) for a 360 px tall upright figure made of blocks, moved right by `shift`."""
    mask = np.zeros((SIZE[1], SIZE[0]), np.uint8)
    for left, top, right, bottom in BLOCKS.values():
        mask[top:bottom, left + shift:right + shift] = 1
    keypoints = []
    for name in p.BODY_POINTS:
        x, y = JOINTS.get(name, (0, 0))
        keypoints += [x + shift, y, 2] if name in JOINTS else [0, 0, 0]
    shape = {"label": "person", "points": p.outline(mask), "group_id": None, "description": "", "shape_type": "polygon",
             "flags": {}}
    return shape, {"keypoints": keypoints}


def document(*shifts):
    figures = [figure(shift) for shift in shifts]
    doc = {"version": "5.4.1", "flags": {}, "shapes": [shape for shape, _ in figures], "imagePath": "photo.jpg",
           "imageData": None, "imageHeight": SIZE[1], "imageWidth": SIZE[0]}
    return doc, [annotation for _, annotation in figures]


def inside(box, block, slack=3):
    return (box[0] >= block[0] - slack and box[1] >= block[1] - slack
            and box[2] <= block[2] + slack and box[3] <= block[3] + slack)


@pytest.mark.parametrize("kind,block", [("head_missing", "head"), ("arm_missing", "arm_b"), ("leg_missing", "leg_b")])
def test_a_body_part_error_removes_that_part_and_nothing_else(kind, block):
    doc, owners = document(0)
    altered, entry = p.inject(doc, owners, kind, 0)
    details = entry["details"]
    assert details["body_part"] == kind.split("_")[0] and inside(details["changed_box"], BLOCKS[block])
    assert 0.02 <= details["removed_share"] <= 0.4 and details["added_share"] < 0.01
    assert (entry["expected_check"], entry["expected_region_kind"]) == ("person_coverage", "missing")
    assert altered["shapes"][0]["points"] != doc["shapes"][0]["points"]


def test_a_body_part_error_needs_the_body_points_and_a_tall_person():
    doc, owners = document(0)
    assert p.inject(doc, [{"keypoints": [0] * 51}], "arm_missing", 0) is None
    small = copy.deepcopy(doc)
    small["shapes"][0]["points"] = [[x / 3, y / 3] for x, y in doc["shapes"][0]["points"]]
    assert p.inject(small, owners, "head_missing", 0) is None


def test_loose_and_shrunk_levels_move_the_outline_by_a_share_of_the_height():
    doc, owners = document(0)
    added = []
    for kind, level in (("loose_3", 0.03), ("loose_6", 0.06), ("loose_12", 0.12)):
        _, entry = p.inject(doc, owners, kind, 0)
        assert entry["details"]["distance_px"] == round(level * 360) and entry["details"]["removed_px"] == 0
        assert entry["expected_region_kind"] == "extra"
        added.append(entry["details"]["added_share"])
    assert added == sorted(added) and added[0] > 0.05
    _, entry = p.inject(doc, owners, "shrunk_3", 0)
    assert entry["details"]["added_px"] == 0 and entry["details"]["removed_share"] > 0.05
    assert entry["expected_region_kind"] == "missing"


@pytest.mark.parametrize("kind,level", [("overlap_10", 0.10), ("overlap_30", 0.30)])
def test_an_overlap_error_grows_one_outline_into_its_neighbour(kind, level):
    doc, owners = document(0, 170)  # the second figure stands 10 px to the right of the first
    altered, entry = p.inject(doc, owners, kind, 0)
    details = entry["details"]
    assert details["neighbour_shape_key"] == "/shapes/1" and level <= details["overlap_share_of_neighbour"] <= level + 0.15
    size = (doc["imageWidth"], doc["imageHeight"])
    grown, neighbour = p.raster(altered["shapes"][0]["points"], size), p.raster(doc["shapes"][1]["points"], size)
    assert (grown & neighbour).sum() / neighbour.sum() == pytest.approx(details["overlap_share_of_neighbour"], abs=1e-4)
    assert altered["shapes"][1] == doc["shapes"][1] and entry["expected_check"] is None


def test_an_overlap_error_needs_a_neighbour_standing_close():
    doc, owners = document(0, 400)
    assert p.inject(doc, owners, "overlap_10", 0) is None
    alone, owner = document(0)
    assert p.inject(alone, owner, "overlap_10", 0) is None


def test_injecting_never_changes_the_source_document_or_other_shapes():
    doc, owners = document(0, 170)
    before = copy.deepcopy(doc)
    for kind in p.ERRORS:
        made = p.inject(doc, owners, kind, 0)
        assert made is not None, kind
        altered, entry = made
        assert doc == before and entry["error"] == kind
        if entry["shape_key"]:
            assert altered["shapes"][1] == before["shapes"][1]
        assert p.base.structural_codes(altered) == p.base.structural_codes(before)


def test_the_plan_is_seeded_balanced_and_leaves_every_fourth_file_unchanged():
    documents, owners = {}, {}
    for number in range(48):
        documents[f"file{number:02}"], owners[f"file{number:02}"] = document(0, 170)
    altered, cases = p.plan_errors(documents, owners, seed=7)
    again, same = p.plan_errors(documents, owners, seed=7)
    assert altered == again and cases == same
    assert p.plan_errors(documents, owners, seed=8)[1] != cases
    counts = [sum(case["error"] == kind for case in cases) for kind in p.ERRORS]
    assert sum(case["error"] is None for case in cases) == 12 and counts == [3] * 12
    for case in cases:
        assert (altered[case["file"][:-5]] == documents[case["file"][:-5]]) == (case["error"] is None)


@needs_sources
def test_the_build_is_reproducible_and_clean_files_keep_the_exact_source_numbers(tmp_path):
    first, second = p.build(tmp_path / "a"), p.build(tmp_path / "b")
    assert first == second and first["errors_injected"] > 0
    hashes = [json.loads((tmp_path / name / "verification.json").read_text())["archives_sha256"] for name in "ab"]
    assert hashes[0] == hashes[1]
    source = json.loads(p.ANNOTATIONS.read_text())
    parts = {}
    for annotation in source["annotations"]:
        if isinstance(annotation["segmentation"], list):
            parts[annotation["id"]] = [[[part[i], part[i + 1]] for i in range(0, len(part), 2)]
                                       for part in annotation["segmentation"]]
    with zipfile.ZipFile(tmp_path / "a/archives/labelme_pack_clean.zip") as clean, \
            zipfile.ZipFile(tmp_path / "a/archives/labelme_pack_errors.zip") as errors:
        assert clean.namelist() == errors.namelist() and len(clean.namelist()) == 2 * first["images"]
        for case in first["cases"]:
            original, altered = json.loads(clean.read(case["file"])), json.loads(errors.read(case["file"]))
            assert clean.read(case["image"]) == errors.read(case["image"])
            assert all(shape["shape_type"] == "polygon" for shape in original["shapes"])
            assert 1 <= case["people"] <= p.MAX_PEOPLE
            seen: dict[int, list] = {}
            for shape in original["shapes"]:
                seen.setdefault(int(shape["description"].rsplit(" ", 1)[1]), []).append(shape["points"])
            assert all(parts[number] == points for number, points in seen.items())
            changed = [i for i, shape in enumerate(original["shapes"])
                       if i >= len(altered["shapes"]) or altered["shapes"][i] != shape]
            if case["error"] is None:
                assert altered == original
            elif case["error"] != "annotation_removed":
                assert changed == [int(case["shape_key"].rsplit("/", 1)[1])]


SCORE = importlib.util.spec_from_file_location("labelme_person_pack_score", Path(__file__).resolve().parents[1] / "score_pack.py")
scorer = importlib.util.module_from_spec(SCORE)
SCORE.loader.exec_module(scorer)


def test_the_scorer_reports_a_cut_head_in_the_right_place_and_passes_the_unchanged_outline():
    from types import SimpleNamespace
    doc, owners = document(0)
    size = (doc["imageWidth"], doc["imageHeight"])
    mask = p.raster(doc["shapes"][0]["points"], size).astype(bool)
    found = SimpleNamespace(people=[SimpleNamespace(mask=mask, box=(120, 40, 279, 399), score=0.99)], carried=[])
    coverage = scorer.validate_check_parameters("person_coverage", {})
    missing = scorer.validate_check_parameters("unannotated_people", {})
    shapes, unannotated = scorer.judge_file(doc, found, coverage, missing)
    assert shapes[0] == ("pass", []) and unannotated == []
    altered, entry = p.inject(doc, owners, "head_missing", 0)
    (outcome, regions), = scorer.judge_file(altered, found, coverage, missing)[0].values()
    assert outcome == "fail" and [region["kind"] for region in regions] == ["missing"]
    assert scorer.touches(regions[0]["box"], entry["details"]["changed_box"])
    removed, _ = p.inject(*document(0, 300), "annotation_removed", 0)
    people = [found.people[0], SimpleNamespace(mask=np.roll(mask, 300, axis=1), box=(420, 40, 579, 399), score=0.99)]
    _, unannotated = scorer.judge_file(removed, SimpleNamespace(people=people, carried=[]), coverage, missing)
    assert len(unannotated) == 1 and scorer.box_overlap(unannotated[0], [120, 40, 280, 400]) > 0.9


CROWD = importlib.util.spec_from_file_location("labelme_person_pack_crowd", Path(__file__).resolve().parents[1] / "crowd.py")
crowd = importlib.util.module_from_spec(CROWD)
CROWD.loader.exec_module(crowd)


def test_crowd_variants_keep_one_some_or_all_of_the_people():
    import random
    found = [{"id": number, "bbox": [0, 0, 20, 30 if number == 0 else 200]} for number in range(9)]
    assert crowd.kept_people(found, "all", random.Random(1)) == found
    some = crowd.kept_people(found, "some", random.Random(1))
    assert len(some) == 4 and some == sorted(some, key=lambda person: person["id"])
    for seed in range(20):
        (one,) = crowd.kept_people(found, "one", random.Random(seed))
        assert one["id"] != 0  # the one annotated person is never the tiny one


@needs_sources
def test_the_crowd_build_is_reproducible_and_accounts_for_every_person(tmp_path):
    first, second = crowd.build(tmp_path / "a"), crowd.build(tmp_path / "b")
    assert first == second and first["photos"] > 0
    with zipfile.ZipFile(tmp_path / "a/archives/labelme_crowd_one.zip") as one, \
            zipfile.ZipFile(tmp_path / "a/archives/labelme_crowd_all.zip") as everyone:
        for case in first["cases"]:
            assert case["people"] >= crowd.MIN_CROWD and case["one"]["annotated"] == 1
            assert 1 < case["some"]["annotated"] < case["people"] and not case["all"]["left_out"]
            for variant in crowd.VARIANTS:
                assert case[variant]["annotated"] + len(case[variant]["left_out"]) == case["people"]
            kept, full = json.loads(one.read(case["file"])), json.loads(everyone.read(case["file"]))
            source_points = [shape["points"] for shape in full["shapes"]]  # group numbers differ, points do not
            assert all(shape["points"] in source_points for shape in kept["shapes"])
            assert len({shape["description"] for shape in kept["shapes"]}) == 1


OVERLAP = importlib.util.spec_from_file_location("labelme_person_pack_overlap", Path(__file__).resolve().parents[1] / "overlap.py")
overlap = importlib.util.module_from_spec(OVERLAP)
OVERLAP.loader.exec_module(overlap)


def test_a_thin_shared_strip_is_not_a_thick_patch_but_a_grown_outline_is():
    from types import SimpleNamespace
    doc, owners = document(0, 170)
    size = (doc["imageWidth"], doc["imageHeight"])
    people = [SimpleNamespace(mask=p.raster(shape["points"], size).astype(bool)) for shape in doc["shapes"]]
    found = SimpleNamespace(people=people)
    assert overlap.pairs(doc, found) == []  # the two figures stand 10 px apart
    strip = copy.deepcopy(doc)  # the second outline's left side is drawn 13 px into the first person's arm
    strip["shapes"][1]["points"] = [[x - 13 if x < 300 else x, y] for x, y in doc["shapes"][1]["points"]]
    (thin,) = overlap.pairs(strip, found)
    assert thin["shared_px"] > 0 and thin["thick_px"] == 0
    grown, entry = p.inject(doc, owners, "overlap_30", 0)
    (record,) = overlap.pairs(grown, found)
    assert record["thick_px"] > 0 and record["owner"] == "second"
    assert record["share_of_smaller"] == pytest.approx(entry["details"]["overlap_share_of_neighbour"], abs=0.02)
