"""The region records the review screen reads (backend/api/AUDITS.md, "Finding regions").

The frontend reader is frontend/src/lib/findingRegions.ts. A field may be added; these may not be renamed or dropped.
"""
from types import SimpleNamespace

import numpy as np
import pytest

from backend.app.checks.registry import validate_check_parameters
from backend.app.models.finding import FindingOutcome
from backend.app.services import person_coverage as pc

H, W = 400, 600
KINDS = {"missing", "extra", "mismatch"}
SIDES = {"top", "bottom", "left", "right", "middle"}
PARAMS = validate_check_parameters("person_coverage", {})


def person(top=50, height=300, left=100, width=80):
    mask = np.zeros((H, W), bool)
    mask[top:top + height, left:left + width] = True
    return mask


def segmentation(mask):
    return SimpleNamespace(people=[SimpleNamespace(mask=mask, box=pc._extent(mask), score=0.99)], carried=[])


def check_region(region, size_field):
    assert {"kind", "side", "box", size_field} <= set(region)
    assert region["kind"] in KINDS and region["side"] in SIDES
    assert len(region["box"]) == 4 and isinstance(region[size_field], int)


def test_a_box_region_names_its_kind_side_size_and_place():
    outcome, result = pc.judge_box((100, 120, 179, 349), segmentation(person()), PARAMS)  # head cut off
    assert outcome is FindingOutcome.FAIL and result["geometry"] == "box"
    [region] = result["regions"]
    check_region(region, "pixels")
    assert (region["kind"], region["side"], region["pixels"]) == ("missing", "top", 70) and "edge" in region


def test_an_outline_region_names_its_kind_side_area_and_place():
    whole = person()
    cut = whole.copy()
    cut[50:120] = False  # head missing from the outline
    outcome, result = pc.judge_region(cut, segmentation(whole), PARAMS, "polygon")
    assert outcome is FindingOutcome.FAIL and result["geometry"] == "polygon"
    [region] = result["regions"]
    check_region(region, "area_px")
    assert (region["kind"], region["side"]) == ("missing", "top")
    assert 5000 < region["area_px"] <= 70 * 80  # the patch's corners are rounded off


def test_extra_background_keeps_the_stored_word_extra():
    loose = person(top=10, height=380, left=60, width=160)
    outcome, result = pc.judge_region(loose, segmentation(person()), PARAMS, "polygon")
    assert outcome is FindingOutcome.FAIL
    assert {region["kind"] for region in result["regions"]} == {"extra"}


@pytest.mark.parametrize("annotation_format,system", [("coco", "zero_based_continuous"),
                                                      ("labelme", "zero_based_continuous"),
                                                      ("penn_fudan", "one_based_inclusive")])
def test_each_geometry_states_its_coordinate_system(annotation_format, system):
    _, result = pc.judge_box((100, 120, 179, 349), segmentation(person()), PARAMS)
    assert pc._to_annotation_coordinates(result, annotation_format)["coordinate_system"] == system


def test_a_person_with_no_annotation_is_reported_with_a_box_height_and_score():
    params = validate_check_parameters("unannotated_people", {})
    outcome, _, result = pc.unannotated_people([], "coco", (W, H), None, segmentation(person()), params)
    assert outcome is FindingOutcome.FAIL and result["coordinate_system"] == "zero_based_continuous"
    [missing] = result["unannotated"]
    assert {"box", "height_px", "score"} <= set(missing) and len(missing["box"]) == 4
