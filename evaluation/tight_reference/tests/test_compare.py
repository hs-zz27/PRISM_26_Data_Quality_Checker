"""Matching redrawn reference outlines to COCO's originals, and scoring them (compare.py)."""
import importlib.util
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

SPEC = importlib.util.spec_from_file_location("tight_reference_compare", Path(__file__).resolve().parents[1] / "compare.py")
c = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(c)
W, H = 400, 300


def shape(points, coco_id=None, group_id=None, label="person"):
    return {"label": label, "points": points, "group_id": group_id, "shape_type": "polygon", "flags": {},
            "description": f"COCO annotation {coco_id}" if coco_id else ""}


def box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def document(*shapes):
    return {"version": "5.4.1", "flags": {}, "shapes": list(shapes), "imagePath": "photo.jpg", "imageData": None,
            "imageHeight": H, "imageWidth": W}


def test_parts_sharing_a_group_are_one_person_and_new_people_get_their_own_key():
    found = c.people(document(shape(box(10, 10, 50, 100), 7, group_id=1), shape(box(10, 120, 50, 200), group_id=1),
                              shape(box(200, 10, 260, 200)), shape(box(300, 10, 320, 40), 9, label="bag")))
    assert list(found)[0] == 7 and len(found[7]) == 2
    assert [k for k in found if isinstance(k, tuple)] == [("new", 1)] and 9 not in found


def test_a_person_is_matched_by_id_then_by_overlap_and_an_added_person_by_nothing():
    original = {7: [box(10, 10, 50, 100)], 8: [box(200, 10, 260, 200)]}
    redrawn = {7: [box(12, 12, 48, 98)], ("new", 1): [box(205, 15, 255, 195)], ("new", 2): [box(330, 200, 380, 290)]}
    assert c.match(original, redrawn, (W, H)) == {7: 7, ("new", 1): 8, ("new", 2): None}


def test_a_tightened_outline_scores_higher_and_the_missing_and_added_are_counted(tmp_path):
    kit, returned = tmp_path / "kit", tmp_path / "returned"
    kit.mkdir(), returned.mkdir()
    image = np.full((H, W, 3), 30, np.uint8)
    cv2.rectangle(image, (100, 50), (180, 260), (220, 220, 220), -1)
    cv2.rectangle(image, (250, 60), (320, 250), (220, 220, 220), -1)
    Image.fromarray(image).save(kit / "photo.jpg")
    loose = document(shape(box(85, 35, 195, 275), 1), shape(box(250, 60, 320, 250), 2))
    tight = document(shape(box(100, 50, 180, 260), 1), shape(box(20, 20, 60, 60)))  # person 2 dropped, one added
    (kit / "photo.json").write_text(json.dumps(loose))
    (returned / "photo.json").write_text(json.dumps(tight))
    (kit / "kit_manifest.json").write_text(json.dumps({"photos": [
        {"file": "photo.json", "image": "photo.jpg", "category": "single", "people": 2},
        {"file": "other.json", "image": "other.jpg", "category": "crowd", "people": 5}]}))
    result = c.compare(kit, returned)
    (row,) = result["people"]
    assert row["coco_annotation"] == 1 and row["after"] > 0.9 > row["before"]
    assert (result["added_people"], result["missing_people"], result["missing_files"]) == (1, 1, ["other.json"])
    assert result["suggested_mark"] is not None and result["redrawn_passing_mark"] == 1.0
