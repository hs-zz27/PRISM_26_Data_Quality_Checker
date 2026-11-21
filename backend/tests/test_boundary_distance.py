"""How far an outline lies from a reference outline of the same person (services/boundary_distance.py)."""
import cv2
import numpy as np
import pytest

from backend.app.services import boundary_distance as bd

H, W = 400, 500


def figure(grow=0):
    """A head on a body, about 300 px tall; ``grow`` widens it by that many pixels all round."""
    mask = np.zeros((H, W), np.uint8)
    cv2.circle(mask, (250, 90), 40 + grow, 1, -1)
    cv2.ellipse(mask, (250, 240), (70 + grow, 120 + grow), 0, 0, 360, 1, -1)
    return mask.astype(bool)


def test_the_same_outline_lies_nowhere_off_the_reference():
    result = bd.boundary_distance(figure(), figure(), 4)
    assert result.outward_px == result.inward_px == 0
    assert result.outward_share == result.inward_share == 0
    assert result.mask_iou == result.boundary_iou == result.agreement == 1
    assert not result.too_small and 290 < result.reference_height_px < 320
    assert result.regions == []


def test_a_loose_outline_is_measured_outward_by_how_far_it_reaches():
    result = bd.boundary_distance(figure(grow=6), figure(), 4)
    assert 5 <= result.outward_px <= 7 and result.outward_max_px <= 8
    assert result.outward_share > 0.95 and result.inward_px == 0 and result.inward_share == 0
    assert result.mask_iou > 0.85  # area overlap hides the loose band ...
    assert result.boundary_iou < 0.3  # ... the boundary comparison does not


def test_an_outline_that_cuts_in_is_measured_inward():
    result = bd.boundary_distance(figure(), figure(grow=6), 4)
    assert 5 <= result.inward_px <= 7 and result.inward_share > 0.95
    assert result.outward_px == 0 and result.outward_share == 0


def test_a_missing_arm_shows_only_on_the_part_it_belongs_to():
    with_arm = figure()
    cv2.rectangle(with_arm.view(np.uint8), (320, 200), (420, 215), 1, -1)
    result = bd.boundary_distance(figure(), with_arm, 4)
    assert result.inward_max_px > 90  # the arm's tip is far outside the annotation ...
    assert 0.05 < result.inward_share < 0.4  # ... but most of the boundary agrees
    assert result.outward_px == 0
    [arm] = result.regions
    assert arm["kind"] == "missing" and arm["side"] == "right"
    assert 320 <= arm["box"][0] and arm["box"][2] == 421 and 195 <= arm["box"][1] and arm["box"][3] <= 220
    assert 1200 < arm["area_px"] < 1600


def test_taking_in_a_neighbour_is_loose_on_that_side_only():
    with_neighbour = figure()
    cv2.circle(with_neighbour.view(np.uint8), (330, 90), 30, 1, -1)  # another head beside this one
    result = bd.boundary_distance(with_neighbour, figure(), 4)
    assert result.outward_max_px > 40 and 0.05 < result.outward_share < 0.4 and result.inward_px == 0
    [head] = result.regions
    assert head["kind"] == "extra" and head["side"] == "top" and head["box"][2] == 361


def test_specks_smaller_than_the_tolerance_are_not_reported_as_regions():
    with_speck = figure()
    with_speck[100:104, 330:334] = True  # 16 px, well outside the head and past a 4 px tolerance, but tiny
    result = bd.boundary_distance(with_speck, figure(), 4)
    assert result.outward_max_px > 4 and result.regions == []


def test_a_tolerance_wider_than_the_gap_accepts_the_outline():
    loose = bd.boundary_distance(figure(grow=3), figure(), 2)
    accepted = bd.boundary_distance(figure(grow=3), figure(), 5)
    assert loose.outward_share > 0.9 and accepted.outward_share == 0
    assert accepted.boundary_iou > loose.boundary_iou


def test_a_person_a_few_tolerances_tall_is_too_small_to_judge():
    small = np.zeros((H, W), bool)
    small[100:130, 100:110] = True
    assert bd.boundary_distance(small, small, 4).too_small
    assert not bd.boundary_distance(small, small, 3).too_small


def test_a_person_cut_off_by_the_photo_border_has_a_boundary_there():
    reference = np.zeros((H, W), bool)
    reference[200:, 100:200] = True  # runs off the bottom of the photo
    annotation = reference.copy()
    annotation[H - 5:, :] = False  # stops 5 px short of the border
    result = bd.boundary_distance(annotation, reference, 2)
    assert 4 <= result.inward_px <= 6


def test_polygons_of_one_person_are_joined_and_the_tolerance_follows_the_diagonal():
    mask = bd.polygons_mask([[[10, 10], [50, 10], [50, 50]], [[60, 60], [90, 60], [90, 90], [60, 90]], [[1, 1]]],
                            (100, 100))
    assert mask.shape == (100, 100) and mask[70, 70] and mask[20, 40] and not mask[5, 95]
    assert bd.tolerance_for((640, 480), 0.01) == pytest.approx(8.0)


@pytest.mark.parametrize("annotation,reference,tolerance", [
    (np.zeros((5, 5), bool), np.zeros((6, 5), bool), 1),
    (np.ones((5, 5), bool), np.ones((5, 5), bool), 0),
    (np.ones((5, 5), bool), np.zeros((5, 5), bool), 1),
])
def test_bad_input_is_refused(annotation, reference, tolerance):
    with pytest.raises(ValueError):
        bd.boundary_distance(annotation, reference, tolerance)
