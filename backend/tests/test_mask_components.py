import numpy as np
import pytest

from backend.app.checks.segmentation import measure_mask_components


def test_separate_pieces_have_instance_only_areas_and_inclusive_boxes():
    mask = np.zeros((5, 6), dtype=np.uint8)
    mask[:2, :2] = 1
    mask[4, 4:6] = 1
    mask[2, 2:4] = 2
    result = measure_mask_components(mask, 1)
    assert result["measurement_available"]
    assert result["total_mask_pixels"] == 6 and result["component_count"] == 2
    assert [piece["area"] for piece in result["components"]] == [4, 2]
    assert [piece["bbox"] for piece in result["components"]] == [(1, 1, 2, 2), (5, 5, 6, 5)]
    assert [piece["area_fraction"] for piece in result["components"]] == pytest.approx([2 / 3, 1 / 3])


def test_diagonal_connectivity_is_explicit():
    mask = np.array([[1, 0], [0, 1]], dtype=np.uint8)
    assert measure_mask_components(mask, 1)["component_count"] == 1
    assert measure_mask_components(mask, 1, connectivity=4)["component_count"] == 2


def test_single_pixel_and_full_foreground():
    single = measure_mask_components(np.array([[1]], dtype=np.uint8), 1)
    assert single["components"][0]["bbox"] == (1, 1, 1, 1)
    assert single["components"][0]["area_fraction"] == 1
    full = measure_mask_components(np.ones((3, 4), dtype=np.uint8), 1)
    assert full["component_count"] == 1 and full["components"][0]["area"] == 12


@pytest.mark.parametrize("mask", [np.zeros((2, 3), dtype=np.uint8), np.zeros((0, 0), dtype=np.uint8)])
def test_missing_mask_is_unavailable(mask):
    result = measure_mask_components(mask, 1)
    assert not result["measurement_available"] and result["reason"] == "missing_mask"
    assert result["total_mask_pixels"] == 0 and result["components"] == []


@pytest.mark.parametrize("connectivity", [0, 6, 8.0, True])
def test_invalid_connectivity(connectivity):
    with pytest.raises(ValueError):
        measure_mask_components(np.ones((2, 2), dtype=np.uint8), 1, connectivity)


@pytest.mark.parametrize("mask, object_id", [
    (np.zeros((2, 2, 3), dtype=np.uint8), 1),
    (np.zeros((2, 2), dtype=float), 1),
    (np.zeros((2, 2), dtype=np.uint8), 0),
    (np.zeros((2, 2), dtype=np.uint8), True),
])
def test_invalid_mask_or_object_id(mask, object_id):
    with pytest.raises(ValueError):
        measure_mask_components(mask, object_id)
