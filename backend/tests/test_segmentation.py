"""Numeric mask geometry and consistent standalone/combined measurements."""

import numpy as np
import pytest

from backend.app.checks.segmentation import (
    get_mask_bbox, measure_box_mask_alignment, measure_instance_mask,
    measure_mask_box_fill, measure_mask_components, measure_mask_outside_box,
)


@pytest.fixture
def mask():
    return np.array([[0, 1, 1, 2], [0, 1, 0, 0], [0, 1, 1, 0]], dtype=np.uint8)


def test_partial_box_preserves_signed_edges_and_distinct_fraction_denominators(mask):
    obj = {"object_id": 1, "label": "person", "bbox": (1, 2, 2, 3)}
    results = measure_instance_mask(obj, mask)
    alignment = results["box_mask_alignment"]
    assert alignment["mask_bbox"] == (2, 1, 3, 3)
    assert alignment["edge_differences"] == {"left": -1, "top": 1, "right": -1, "bottom": 0}
    assert alignment["max_edge_difference"] == 1
    outside = results["mask_outside_box"]
    assert outside["total_mask_pixels"] == 5 and outside["outside_pixels"] == 3
    assert outside["outside_fraction"] == pytest.approx(3 / 5)
    fill = results["mask_box_fill"]
    assert fill["box_area"] == 4 and fill["inside_mask_pixels"] == 2
    assert fill["fill_fraction"] == pytest.approx(1 / 2)
    assert results["mask_components"]["total_mask_pixels"] == 5


def test_single_pixel_box_includes_its_boundary(mask):
    obj = {"object_id": 2, "label": "person", "bbox": (4, 1, 4, 1)}
    assert get_mask_bbox(mask, 2) == obj["bbox"]
    assert measure_mask_outside_box(obj, mask)["outside_fraction"] == 0
    assert measure_mask_box_fill(obj, mask)["fill_fraction"] == 1


@pytest.mark.parametrize("object_id,box", [
    (1, (1, 2, 2, 3)), (1, (0, 2, 2, 3)), (9, (1, 1, 4, 3)),
])
def test_combined_results_match_public_standalone_functions(mask, object_id, box):
    obj = {"object_id": object_id, "label": "person", "bbox": box}
    results = measure_instance_mask(obj, mask)
    assert results == {
        "box_mask_alignment": measure_box_mask_alignment(obj, mask),
        "mask_outside_box": measure_mask_outside_box(obj, mask),
        "mask_box_fill": measure_mask_box_fill(obj, mask),
        "mask_components": measure_mask_components(mask, object_id),
    }


def test_missing_instance_has_unavailable_measurements_not_zero_fractions(mask):
    obj = {"object_id": 9, "label": "person", "bbox": (1, 1, 4, 3)}
    results = measure_instance_mask(obj, mask)
    assert get_mask_bbox(mask, 9) is None
    assert all(not result["measurement_available"] and result["reason"] == "missing_mask" for result in results.values())
    assert results["mask_outside_box"]["outside_fraction"] is None
    assert results["mask_box_fill"]["fill_fraction"] is None


def test_components_do_not_require_a_usable_annotation_box(mask):
    obj = {"object_id": 1, "label": "person", "bbox": (0, 0, 0, 0)}
    results = measure_instance_mask(obj, mask, include_box_measurements=False)
    assert set(results) == {"mask_components"}
    assert results["mask_components"]["component_count"] == 1


@pytest.mark.parametrize("measure", [measure_box_mask_alignment, measure_mask_outside_box, measure_mask_box_fill, measure_instance_mask])
def test_public_entry_points_validate_mask_and_object_id(measure):
    with pytest.raises(ValueError, match="2D"):
        measure({"object_id": 1, "label": "person", "bbox": (1, 1, 2, 2)}, np.zeros((2, 2, 3), dtype=np.uint8))
    with pytest.raises(ValueError, match="positive integer"):
        measure({"object_id": 0, "label": "person", "bbox": (1, 1, 2, 2)}, np.zeros((2, 2), dtype=np.uint8))
