"""The region records the review screen reads (backend/api/AUDITS.md, "Finding regions").

The frontend reader is frontend/src/lib/findingRegions.ts. A field may be added; these may not be renamed or dropped.
"""
import dataclasses
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from backend.app.checks.registry import validate_check_parameters
from backend.app.models.finding import FindingOutcome
from backend.app.services import boundary_distance as bd
from backend.app.services import boundary_fit as bf
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


# Planned tightness measures ("Planned: outline tightness measures" in AUDITS.md). No audit emits them yet, but
# their record fields are fixed now so the review screen can be built against them.

def check_record(region, kinds, size_field):
    assert set(region) == {"kind", "side", "box", size_field}
    assert region["kind"] in kinds and region["side"] in SIDES and isinstance(region[size_field], int)
    assert len(region["box"]) == 4 and all(isinstance(v, int) for v in region["box"])
    assert region["box"][0] < region["box"][2] and region["box"][1] < region["box"][3]


def test_distance_from_a_reference_reports_extra_and_missing_regions_in_the_shared_format():
    reference = person()
    annotation = reference.copy()
    annotation[50:80] = False                                        # the head left out
    cv2.circle(annotation.view(np.uint8), (200, 200), 20, 1, -1)     # a neighbour's shoulder taken in
    result = bd.boundary_distance(annotation, reference, 4)
    for region in result.regions:
        check_record(region, {"extra", "missing"}, "area_px")
    assert [(r["kind"], r["side"]) for r in result.regions] == [("missing", "top"), ("extra", "right")]
    assert [r["area_px"] for r in result.regions] == sorted((r["area_px"] for r in result.regions), reverse=True)


def test_distance_from_a_reference_keeps_its_per_person_field_names():
    names = {f.name for f in dataclasses.fields(bd.BoundaryDistance)}
    assert {"outward_px", "inward_px", "outward_max_px", "inward_max_px", "outward_share", "inward_share",
            "mask_iou", "boundary_iou", "tolerance_px", "regions"} <= names
    assert isinstance(bd.BoundaryDistance.too_small, property)


def test_edge_fit_reports_mismatch_stretches_in_the_shared_format():
    shape = person()
    photo = np.dstack([np.where(shape, 200, 40).astype(np.uint8)] * 3)
    loose = cv2.dilate(shape.astype(np.uint8), np.ones((21, 21), np.uint8))
    contours, _ = cv2.findContours(loose, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    fit = bf.boundary_fit(bf.edge_distance(photo), [contours[0].reshape(-1, 2).astype(float).tolist()])
    assert fit.regions
    for region in fit.regions:
        check_record(region, {"mismatch"}, "length_px")
    names = {f.name for f in dataclasses.fields(bf.BoundaryFit)}
    assert {"share_on_edge", "busy_share", "tolerance_px", "regions"} <= names
