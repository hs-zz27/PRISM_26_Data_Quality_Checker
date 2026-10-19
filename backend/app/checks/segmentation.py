"""Measure instance masks using one-based, inclusive annotation coordinates."""

from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import TypedDict

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image

from ..models.annotation import AnnotationObject, BoundingBox, ImageSize
from ..models.measurements import MeasurementResult
from .geometry import check_box_validity


class MaskDimensions(TypedDict):
    matches: bool
    image_size: ImageSize
    mask_size: ImageSize


class MaskIds(TypedDict):
    matches: bool
    annotation_ids: set[int]
    mask_ids: set[int]
    missing_masks: set[int]
    missing_annotations: set[int]


class BoxMaskAlignment(MeasurementResult):
    object_id: int
    annotation_bbox: tuple | None
    mask_bbox: BoundingBox | None
    edge_differences: dict[str, int] | None
    max_edge_difference: int | None


class MaskOutsideBox(MeasurementResult):
    object_id: int
    annotation_bbox: tuple | None
    total_mask_pixels: int | None
    outside_pixels: int | None
    outside_fraction: float | None


class MaskBoxFill(MeasurementResult):
    object_id: int
    annotation_bbox: tuple | None
    box_area: int | None
    inside_mask_pixels: int | None
    fill_fraction: float | None


class MaskComponent(TypedDict):
    component_id: int
    area: int
    area_fraction: float
    bbox: BoundingBox


class MaskComponents(MeasurementResult):
    object_id: int
    connectivity: int
    total_mask_pixels: int
    component_count: int
    components: list[MaskComponent]


class BoxMaskMeasurements(TypedDict):
    box_mask_alignment: BoxMaskAlignment
    mask_outside_box: MaskOutsideBox
    mask_box_fill: MaskBoxFill


def validate_instance_mask(mask: NDArray) -> None:
    """Require a 2D mask whose pixel values are integer object IDs."""
    if mask.ndim != 2:
        raise ValueError("Expected a 2D mask with one object ID per pixel.")
    if not np.issubdtype(mask.dtype, np.integer):
        raise ValueError("Mask pixel values must be integer object IDs.")


def _validate_object_id(object_id: int) -> None:
    if (
        not isinstance(object_id, (int, np.integer))
        or isinstance(object_id, bool)
        or object_id <= 0
    ):
        raise ValueError("Object ID must be a positive integer; 0 is background.")


def validate_connectivity(connectivity: int) -> None:
    if not isinstance(connectivity, (int, np.integer)) or connectivity not in (4, 8):
        raise ValueError("Connectivity must be the integer 4 or 8.")


@dataclass(frozen=True)
class _InstancePixels:
    """Temporary per-call data shared by measurements, never cached across edits."""

    binary: NDArray[np.uint8]
    rows: NDArray[np.int32]
    columns: NDArray[np.int32]

    @classmethod
    def from_mask(cls, mask: NDArray, object_id: int) -> "_InstancePixels":
        validate_instance_mask(mask)
        _validate_object_id(object_id)
        binary = (mask == object_id).astype(np.uint8)
        # OpenCV scans the byte mask directly and stores compact (x, y) pairs.
        coordinates = cv2.findNonZero(binary) if binary.size else None
        if coordinates is None:
            rows = columns = np.empty(0, dtype=np.int32)
        else:
            coordinates = coordinates.reshape(-1, 2)
            columns, rows = coordinates[:, 0], coordinates[:, 1]
        return cls(binary, rows, columns)

    @property
    def area(self) -> int:
        return int(self.rows.size)

    @cached_property
    def bbox(self) -> BoundingBox | None:
        if self.area == 0:
            return None
        return (
            int(self.columns.min()) + 1,
            int(self.rows.min()) + 1,
            int(self.columns.max()) + 1,
            int(self.rows.max()) + 1,
        )


def check_mask_dimensions(image: Image.Image, mask: NDArray) -> MaskDimensions:
    """Compare image and mask sizes as (width, height)."""
    validate_instance_mask(mask)
    height, width = mask.shape
    return {
        "matches": image.size == (width, height),
        "image_size": image.size,
        "mask_size": (width, height),
    }


def check_mask_ids(objects: Sequence[AnnotationObject], mask: NDArray) -> MaskIds:
    """Compare ID presence, excluding background 0; do not infer mask accuracy."""
    validate_instance_mask(mask)
    annotation_ids = {obj["object_id"] for obj in objects}
    mask_ids = set(np.unique(mask).tolist()) - {0}
    return {
        "matches": annotation_ids == mask_ids,
        "annotation_ids": annotation_ids,
        "mask_ids": mask_ids,
        "missing_masks": annotation_ids - mask_ids,
        "missing_annotations": mask_ids - annotation_ids,
    }


def get_mask_bbox(mask: NDArray, object_id: int) -> BoundingBox | None:
    """Return one-based inclusive instance bounds, or None when absent."""
    return _InstancePixels.from_mask(mask, object_id).bbox


def _measure_box_mask(obj: AnnotationObject, pixels: _InstancePixels) -> BoxMaskMeasurements:
    height, width = pixels.binary.shape
    box_check = check_box_validity(obj["bbox"], (width, height))
    box = box_check["bbox"]
    reason = box_check["reason"] if not box_check["valid"] else None
    if reason is None and pixels.area == 0:
        reason = "missing_mask"
    available = reason is None
    common = {
        "object_id": int(obj["object_id"]),
        "annotation_bbox": box,
        "measurement_available": available,
        "reason": reason,
    }
    alignment: BoxMaskAlignment = {
        **common, "mask_bbox": None, "edge_differences": None, "max_edge_difference": None,
    }
    outside: MaskOutsideBox = {
        **common, "total_mask_pixels": None, "outside_pixels": None, "outside_fraction": None,
    }
    fill: MaskBoxFill = {
        **common, "box_area": None, "inside_mask_pixels": None, "fill_fraction": None,
    }
    results: BoxMaskMeasurements = {
        "box_mask_alignment": alignment, "mask_outside_box": outside, "mask_box_fill": fill,
    }
    if not box_check["valid"]:
        return results

    xmin, ymin, xmax, ymax = box
    fill["box_area"] = (xmax - xmin + 1) * (ymax - ymin + 1)
    outside["total_mask_pixels"] = pixels.area
    outside["outside_pixels"] = 0
    if not available:
        return results

    mask_bbox = pixels.bbox
    alignment["mask_bbox"] = mask_bbox
    differences = dict(zip(
        ("left", "top", "right", "bottom"),
        (edge - mask_edge for edge, mask_edge in zip(box, mask_bbox)),
    ))
    alignment["edge_differences"] = differences
    alignment["max_edge_difference"] = max(abs(value) for value in differences.values())

    # Convert zero-based NumPy positions once; edge pixels count as inside.
    x, y = pixels.columns + 1, pixels.rows + 1
    outside_pixels = int(np.count_nonzero((x < xmin) | (x > xmax) | (y < ymin) | (y > ymax)))
    outside["outside_pixels"] = outside_pixels
    outside["outside_fraction"] = outside_pixels / pixels.area
    inside_pixels = pixels.area - outside_pixels
    fill["inside_mask_pixels"] = inside_pixels
    fill["fill_fraction"] = inside_pixels / fill["box_area"]
    return results


def measure_box_mask_alignment(obj: AnnotationObject, mask: NDArray) -> BoxMaskAlignment:
    """Measure signed annotation-minus-mask edge differences, without thresholds.

    Image/mask dimensions and coordinate origin must already agree.
    Invalid boxes and absent instances produce unavailable measurements.
    """
    return _measure_box_mask(obj, _InstancePixels.from_mask(mask, obj["object_id"]))["box_mask_alignment"]


def measure_mask_outside_box(obj: AnnotationObject, mask: NDArray) -> MaskOutsideBox:
    """Measure outside pixels / total instance pixels; box edges count as inside.

    Image/mask dimensions and coordinate origin must already agree.
    """
    return _measure_box_mask(obj, _InstancePixels.from_mask(mask, obj["object_id"]))["mask_outside_box"]


def measure_mask_box_fill(obj: AnnotationObject, mask: NDArray) -> MaskBoxFill:
    """Measure this instance's pixels inside the box / inclusive box area.

    Image/mask dimensions and coordinate origin must already agree.
    """
    return _measure_box_mask(obj, _InstancePixels.from_mask(mask, obj["object_id"]))["mask_box_fill"]


def _measure_components(pixels: _InstancePixels, object_id: int, connectivity: int) -> MaskComponents:
    result: MaskComponents = {
        "object_id": int(object_id),
        "connectivity": int(connectivity),
        "measurement_available": pixels.area > 0,
        "reason": None if pixels.area else "missing_mask",
        "total_mask_pixels": pixels.area,
        "component_count": 0,
        "components": [],
    }
    if pixels.area == 0:
        return result
    xmin, ymin, xmax, ymax = pixels.bbox
    left_offset, top_offset = xmin - 1, ymin - 1
    # Crop to actual foreground bounds, never the annotation box: pixels outside
    # that box and distant disconnected pieces must remain part of the evidence.
    binary = np.ascontiguousarray(pixels.binary[top_offset:ymax, left_offset:xmax])
    label_count, _, stats, _ = cv2.connectedComponentsWithStats(
        binary, connectivity=int(connectivity)
    )
    for component_id in range(1, label_count):
        left = int(stats[component_id, cv2.CC_STAT_LEFT]) + left_offset
        top = int(stats[component_id, cv2.CC_STAT_TOP]) + top_offset
        width = int(stats[component_id, cv2.CC_STAT_WIDTH])
        height = int(stats[component_id, cv2.CC_STAT_HEIGHT])
        area = int(stats[component_id, cv2.CC_STAT_AREA])
        result["components"].append({
            "component_id": component_id,
            "area": area,
            "area_fraction": area / pixels.area,
            "bbox": (left + 1, top + 1, left + width, top + height),
        })
    result["components"].sort(key=lambda component: component["area"], reverse=True)
    result["component_count"] = len(result["components"])
    return result


def measure_mask_components(mask: NDArray, object_id: int, connectivity: int = 8) -> MaskComponents:
    """Measure connected pieces, largest first, without deciding whether they are errors.

    Connectivity 8 includes diagonal neighbours; 4 uses horizontal and vertical
    neighbours only. Component IDs identify pieces, not annotated objects.
    """
    validate_connectivity(connectivity)
    return _measure_components(_InstancePixels.from_mask(mask, object_id), object_id, connectivity)


def measure_instance_mask(
    obj: AnnotationObject, mask: NDArray, connectivity: int = 8, *, include_box_measurements: bool = True,
) -> dict[str, MeasurementResult]:
    """Share one pixel extraction across the audit's instance measurements.

    The caller enables box measurements only when image dimensions and annotation
    coordinates are usable. Public standalone functions remain independently usable.
    """
    validate_connectivity(connectivity)
    pixels = _InstancePixels.from_mask(mask, obj["object_id"])
    results: dict[str, MeasurementResult] = {
        "mask_components": _measure_components(pixels, obj["object_id"], connectivity),
    }
    if include_box_measurements:
        results.update(_measure_box_mask(obj, pixels))
    return results
