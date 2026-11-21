"""Measuring COCO's outlines against the references and testing that against the mentor's verdicts (distances.py)."""
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("tight_reference_distances", Path(__file__).resolve().parents[1] / "distances.py")
d = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(d)


def shape(points, coco_id):
    return {"label": "person", "points": points, "group_id": None, "shape_type": "polygon", "flags": {},
            "description": f"COCO annotation {coco_id}"}


def box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def write(folder, name, *shapes):
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(json.dumps({"imageWidth": 640, "imageHeight": 480, "shapes": list(shapes)}))


def test_each_kept_person_is_measured_against_its_reference_and_joined_to_the_mentors_verdict(tmp_path):
    kit, drawn = tmp_path / "kit", tmp_path / "drawn"
    write(kit, "a.json", shape(box(100, 100, 200, 400), 1), shape(box(300, 100, 400, 400), 2),
          shape(box(450, 100, 550, 400), 3))
    write(drawn, "a.json", shape(box(102, 102, 198, 398), 1), shape(box(310, 110, 390, 390), 2),
          shape(box(450, 100, 550, 400), 3))
    (kit / "kit_manifest.json").write_text(json.dumps({"photos": [{"file": "a.json", "category": "single"}]}))
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"people": {"1": {"verdict": "reference"}, "2": {"verdict": "reference"},
                                             "3": {"verdict": "excluded"}}}))
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({"photos": {"a": {"tight": [1], "not_tight": [2, 3]}}, "names": {}}))
    rows = {r["coco_annotation"]: r for r in d.measure(kit, drawn, review, labels)}
    assert set(rows) == {1, 2}  # the excluded person has no reference
    assert rows[1]["mentor"] == "tight" and 1 <= rows[1]["outward_px"] <= 3
    assert rows[2]["mentor"] == "not tight" and 9 <= rows[2]["outward_px"] <= 11 and rows[2]["inward_px"] == 0
    assert set(rows[1]["by_tolerance"]) == {str(f) for f in d.DIAGONAL_FRACTIONS}


def row(mentor, outward, inward=0.0):
    return {"mentor": mentor, "outward_px": outward, "inward_px": inward, "diagonal_px": 800.0}


def test_the_tolerance_is_the_farthest_tight_outline_and_everything_farther_fails():
    rows = [row("tight", 3.0), row("tight", 1.0, 3.6), row("not tight", 4.4), row("not tight", 3.0),
            row(None, 2.0), row(None, 9.0)]
    c = d.calibrate(rows)
    assert c["tolerance_diagonal_fraction"] == pytest.approx(3.6 / 800) and c["tolerance_px_at_640x480"] == pytest.approx(3.6)
    assert c["tight_passing"] == 2 and c["rejected_failing"] == 1 and c["rejected"] == 2
    assert c["closest_rejected_diagonal_fraction"] == 3.0 / 800
    assert c["others_failing"] == 1 and c["others"] == 2
    assert d.calibrate([row("not tight", 5.0)]) == {}
