"""Exact inclusive-coordinate geometry, including boundary intersections."""

import pytest

from backend.app.checks.geometry import check_box_validity, measure_box_iou, measure_pairwise_box_iou


@pytest.mark.parametrize("first,second,intersection,union", [
    ((1, 1, 2, 2), (2, 2, 3, 3), 1, 7),
    ((1, 1, 1, 1), (1, 1, 1, 1), 1, 1),
    ((1, 1, 1, 1), (2, 1, 2, 1), 0, 2),
    ((1, 1, 4, 4), (2, 2, 3, 3), 4, 16),
])
def test_inclusive_iou(first, second, intersection, union):
    result = measure_box_iou(first, second, (4, 4))
    assert result["measurement_available"]
    assert result["intersection_area"] == intersection
    assert result["union_area"] == union
    assert result["iou"] == pytest.approx(intersection / union)


@pytest.mark.parametrize("box,reason", [
    ((0, 1, 2, 2), "box_outside_image"),
    ((1, 1, 5, 4), "box_outside_image"),
    ((3, 1, 2, 2), "invalid_box"),
    ((1, 1, 2), "invalid_box"),
    ((True, 1, 2, 2), "invalid_box"),
])
def test_invalid_geometry_has_no_numeric_iou(box, reason):
    assert check_box_validity(box, (4, 4))["reason"] == reason
    result = measure_box_iou(box, (1, 1, 2, 2), (4, 4))
    assert not result["measurement_available"] and result["iou"] is None
    assert result["invalid_boxes"] == {"box1": reason}


def test_pairwise_results_include_each_unordered_pair_once():
    objects = [{"object_id": number, "label": "person", "bbox": (number, 1, number, 1)} for number in (1, 2, 3)]
    results = measure_pairwise_box_iou(objects, (3, 1))
    assert [result["object_ids"] for result in results] == [(1, 2), (1, 3), (2, 3)]
    assert all(result["iou"] == 0 for result in results)
