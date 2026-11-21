"""Which outlines the mentor is asked to judge on the verdict sheet (verdict_sheet.py)."""
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))  # verdict_sheet imports sheet_tools from its own folder, as when run as a script
SPEC = importlib.util.spec_from_file_location("tight_reference_verdict_sheet", HERE / "verdict_sheet.py")
v = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(v)


def test_only_kept_references_outside_the_mentors_own_sheet_are_asked_about_in_kit_order(tmp_path):
    (tmp_path / "kit_manifest.json").write_text(json.dumps({"photos": [
        {"file": "a.json", "category": "single", "coco_annotations": [1]},
        {"file": "b.json", "category": "mentor_sheet", "coco_annotations": [2]},
        {"file": "c.json", "category": "crowd", "coco_annotations": [5, 3, 4]},
        {"file": "d.json", "category": "single", "coco_annotations": [6]},
    ]}))
    review = {"1": {"verdict": "reference"}, "2": {"verdict": "reference"}, "3": {"verdict": "excluded"},
              "4": {"verdict": "reference"}, "5": {"verdict": "reference"}, "6": {"verdict": "excluded"}}
    chosen = [(entry["file"], ids) for entry, ids in v.people_to_judge(tmp_path, review)]
    assert chosen == [("a.json", [1]), ("c.json", [5, 4])]


def test_the_yardstick_is_the_two_outlines_the_mentor_marked_tight():
    labels = json.loads((HERE.parent / "boundary_fit/sir_labels.json").read_text())
    tight = {cid for photo in labels["photos"].values() for cid in photo["tight"]}
    assert set(v.YARDSTICK) == tight
    assert {stem for stem, _ in v.YARDSTICK.values()} == {s for s, p in labels["photos"].items() if p["tight"]}
