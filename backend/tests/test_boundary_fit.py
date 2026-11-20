"""How closely an outline follows the person's visible boundary in the photo (services/boundary_fit.py)."""
import cv2
import numpy as np
import pytest

from backend.app.services import boundary_fit as bf

H, W = 400, 500
SIDES = {"top", "bottom", "left", "right", "middle"}


def photo(shape_mask, inside=200, outside=40, blur=0.0, texture=False):
    """A plain photo with one bright person shape on a dark background."""
    image = np.where(shape_mask, inside, outside).astype(np.uint8)
    if texture:
        rng = np.random.default_rng(1)
        image = np.where(shape_mask, image, rng.integers(0, 255, image.shape)).astype(np.uint8)
    if blur:
        image = cv2.GaussianBlur(image, (0, 0), blur)
    return np.dstack([image] * 3)


def person_mask():
    """A rounded figure: a head on a body, about 300 px tall."""
    mask = np.zeros((H, W), np.uint8)
    cv2.circle(mask, (250, 90), 40, 1, -1)
    cv2.ellipse(mask, (250, 240), (70, 120), 0, 0, 360, 1, -1)
    return mask.astype(bool)


def outline(mask, grow=0):
    m = mask.astype(np.uint8)
    if grow:
        m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1,) * 2))
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    return max(contours, key=cv2.contourArea).reshape(-1, 2).astype(float).tolist()


@pytest.fixture(scope="module")
def figure():
    mask = person_mask()
    return mask, bf.edge_distance(photo(mask))


def test_an_outline_drawn_on_the_boundary_passes_with_no_stretch_reported(figure):
    mask, distance = figure
    fit = bf.boundary_fit(distance, [outline(mask)])
    assert fit.share_on_edge >= 0.98 and fit.regions == [] and bf.provisional_outcome(fit) == "pass"
    assert fit.border_px == 0 and fit.judged_px > 600


def test_the_same_outline_drawn_loosely_fails_and_says_where(figure):
    mask, distance = figure
    fit = bf.boundary_fit(distance, [outline(mask, grow=10)])
    assert fit.share_on_edge < 0.2 and bf.provisional_outcome(fit) == "fail"
    assert fit.regions and all(r["kind"] == "mismatch" and r["side"] in SIDES for r in fit.regions)
    assert sum(r["length_px"] for r in fit.regions) > 0.8 * fit.judged_px


def test_a_soft_low_contrast_boundary_is_still_found():
    mask = person_mask()
    distance = bf.edge_distance(photo(mask, inside=70, outside=50, blur=3.0))
    assert bf.boundary_fit(distance, [outline(mask)]).share_on_edge >= 0.9


def test_a_coarse_outline_cutting_across_a_curve_is_reported_there(figure):
    mask, distance = figure
    tight = np.array(outline(mask))
    coarse = tight[::40]  # a few corners joined by straight lines that cut across the curved body
    fit = bf.boundary_fit(distance, [coarse.tolist()])
    assert fit.share_on_edge < 0.9 and fit.regions
    for region in fit.regions:
        x0, y0, x1, y1 = region["box"]
        assert x0 < x1 and y0 < y1 and mask[max(0, y0 - 15):y1 + 15, max(0, x0 - 15):x1 + 15].any()


def test_a_region_record_has_the_fields_of_the_shared_region_format(figure):
    mask, distance = figure
    (region, *_) = bf.boundary_fit(distance, [outline(mask, grow=10)]).regions
    assert set(region) == {"kind", "side", "box", "length_px"} and region["kind"] == "mismatch"
    assert len(region["box"]) == 4 and all(isinstance(v, int) for v in region["box"])
    assert isinstance(region["length_px"], int)


def test_the_photo_border_is_not_judged():
    mask = np.zeros((H, W), bool)
    mask[150:, 200:300] = True  # a person cut off by the bottom of the photo
    fit = bf.boundary_fit(bf.edge_distance(photo(mask)), [[[200, 150], [299, 150], [299, H - 1], [200, H - 1]]])
    assert fit.border_px >= 90 and fit.share_on_edge >= 0.95
    assert all(region["box"][3] < H - bf.BORDER_PX for region in fit.regions)


def test_a_busy_background_makes_the_result_unsure_never_a_pass():
    mask = person_mask()
    fit = bf.boundary_fit(bf.edge_distance(photo(mask, texture=True)), [outline(mask, grow=6)])
    assert fit.busy and bf.provisional_outcome(fit) == "unsure"


def test_a_person_drawn_in_two_parts_is_judged_as_one_person():
    """Someone in front hides the middle of the person, so the annotator drew the visible parts separately."""
    upper, lower = np.zeros((H, W), bool), np.zeros((H, W), bool)
    upper[40:160, 200:300] = True
    lower[260:380, 210:290] = True
    distance = bf.edge_distance(photo(upper | lower))
    parts = [outline(upper), outline(lower)]
    together = bf.boundary_fit(distance, parts)
    assert together.share_on_edge >= 0.98 and together.regions == []
    assert together.tolerance_px == pytest.approx(round(bf.TOLERANCE_FRACTION * (379 - 40), 2))  # the whole person's height
    assert together.judged_px == sum(bf.boundary_fit(distance, [part]).judged_px for part in parts)


def test_nothing_to_judge_is_unsure():
    fit = bf.boundary_fit(np.zeros((10, 10), np.float32), [[[1, 1], [2, 2]]])
    assert fit.share_on_edge is None and bf.provisional_outcome(fit) == "unsure"
