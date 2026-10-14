"""Implement mask dimensions, IDs and box/mask consistency measurements."""

import cv2
import numpy as np

from .geometry import check_box_validity


def _validate_instance_mask(mask):
    """Require a 2D mask whose pixel values are integer object IDs."""
    if mask.ndim != 2:
        raise ValueError("Expected a 2D mask with one object ID per pixel.")
    if not np.issubdtype(mask.dtype, np.integer):
        raise ValueError("Mask pixel values must be integer object IDs.")


def _validate_object_id(object_id):
    """Require a positive integer ID, excluding background and booleans."""
    if (
        not isinstance(object_id, (int, np.integer))
        or isinstance(object_id, bool)
        or object_id <= 0
    ):
        raise ValueError("Object ID must be a positive integer; 0 is background.")


def check_mask_dimensions(image, mask):
    """Compare image and mask sizes, reporting both as (width, height)."""
    _validate_instance_mask(mask)

    image_width, image_height = image.size
    mask_height, mask_width = mask.shape

    return {
        "matches": (image_width, image_height) == (mask_width, mask_height),
        "image_size": (image_width, image_height),
        "mask_size": (mask_width, mask_height),
    }


def check_mask_ids(objects, mask):
    """Compare annotation IDs with mask IDs, excluding background value 0.

    This checks ID presence, not whether each mask outlines the right person.
    """
    _validate_instance_mask(mask)

    annotation_ids = {obj["object_id"] for obj in objects}
    mask_ids = set(np.unique(mask).tolist()) - {0}

    return {
        "matches": annotation_ids == mask_ids,
        "annotation_ids": annotation_ids,
        "mask_ids": mask_ids,
        "missing_masks": annotation_ids - mask_ids,
        "missing_annotations": mask_ids - annotation_ids,
    }


def get_mask_bbox(mask, object_id):
    """Return an instance's (xmin, ymin, xmax, ymax), or None if absent.

    Bounds are one-based and inclusive: (1, 1) is the top-left pixel,
    and xmax/ymax identify the last occupied column/row. This uses the
    annotation's declared origin while leaving source boxes unchanged.
    """
    _validate_instance_mask(mask)
    _validate_object_id(object_id)

    rows, columns = np.nonzero(mask == object_id)
    if rows.size == 0:
        return None

    return (
        int(columns.min()) + 1,
        int(rows.min()) + 1,
        int(columns.max()) + 1,
        int(rows.max()) + 1,
    )


def measure_box_mask_alignment(obj, mask):
    """Compare an annotation box with its instance's mask bounds.

    The annotation box must use one-based, inclusive coordinates, and the
    mask must belong to the same image. Each signed difference is annotation
    edge minus mask edge; max_edge_difference is the largest absolute value
    in pixels. Image/mask dimensions must already match; mask dimensions
    provide image bounds here. Invalid boxes make comparison unavailable.
    These are measurements, without rule thresholds or decisions.
    """
    _validate_instance_mask(mask)
    _validate_object_id(obj["object_id"])
    height, width = mask.shape
    box_check = check_box_validity(obj["bbox"], (width, height))
    annotation_bbox = box_check["bbox"]

    result = {
        "object_id": obj["object_id"],
        "annotation_bbox": annotation_bbox,
        "mask_bbox": None,
        "comparison_available": False,
        "reason": None,
        "edge_differences": None,
        "max_edge_difference": None,
    }
    if not box_check["valid"]:
        result["reason"] = box_check["reason"]
        return result

    mask_bbox = get_mask_bbox(mask, obj["object_id"])
    result["mask_bbox"] = mask_bbox
    if mask_bbox is None:
        result["reason"] = "missing_mask"
        return result

    xmin, ymin, xmax, ymax = annotation_bbox
    mask_xmin, mask_ymin, mask_xmax, mask_ymax = mask_bbox
    differences = {
        "left": xmin - mask_xmin,
        "top": ymin - mask_ymin,
        "right": xmax - mask_xmax,
        "bottom": ymax - mask_ymax,
    }

    result["edge_differences"] = differences
    result["max_edge_difference"] = max(abs(value) for value in differences.values())
    result["comparison_available"] = True
    return result


def measure_mask_outside_box(obj, mask):
    """Count instance pixels outside a one-based, inclusive annotation box.

    The mask must belong to the annotation's image. outside_fraction is
    outside pixels / total instance pixels, or None when the ID is absent.
    Image/mask dimensions must already match; mask dimensions provide image
    bounds here. Invalid boxes make measurement unavailable. Pixels on the
    box edges count as inside. No rule thresholds are applied.
    """
    _validate_instance_mask(mask)
    _validate_object_id(obj["object_id"])
    height, width = mask.shape
    box_check = check_box_validity(obj["bbox"], (width, height))
    result = {
        "object_id": obj["object_id"],
        "annotation_bbox": box_check["bbox"],
        "measurement_available": False,
        "reason": None,
        "total_mask_pixels": None,
        "outside_pixels": None,
        "outside_fraction": None,
    }
    if not box_check["valid"]:
        result["reason"] = box_check["reason"]
        return result

    xmin, ymin, xmax, ymax = box_check["bbox"]
    rows, columns = np.nonzero(mask == obj["object_id"])
    total_pixels = int(rows.size)
    result["total_mask_pixels"] = total_pixels
    result["outside_pixels"] = 0
    if total_pixels == 0:
        result["reason"] = "missing_mask"
        return result

    # Convert NumPy positions to the annotation's one-based coordinates.
    x = columns + 1
    y = rows + 1
    outside = (x < xmin) | (x > xmax) | (y < ymin) | (y > ymax)
    outside_pixels = int(np.count_nonzero(outside))

    result["outside_pixels"] = outside_pixels
    result["outside_fraction"] = outside_pixels / total_pixels
    result["measurement_available"] = True
    return result


def measure_mask_box_fill(obj, mask):
    """Measure the fraction of a box occupied by this object's mask pixels.

    Boxes use one-based, inclusive coordinates. Image/mask dimensions must
    already match; mask dimensions provide the image bounds here. Invalid
    boxes, boxes outside those bounds, and missing masks produce an
    unavailable measurement. No rule thresholds are applied.
    """
    _validate_instance_mask(mask)
    _validate_object_id(obj["object_id"])
    height, width = mask.shape
    box_check = check_box_validity(obj["bbox"], (width, height))
    result = {
        "object_id": obj["object_id"],
        "annotation_bbox": box_check["bbox"],
        "measurement_available": False,
        "reason": None,
        "box_area": None,
        "inside_mask_pixels": None,
        "fill_fraction": None,
    }

    if not box_check["valid"]:
        result["reason"] = box_check["reason"]
        return result

    xmin, ymin, xmax, ymax = box_check["bbox"]
    box_area = (xmax - xmin + 1) * (ymax - ymin + 1)
    result["box_area"] = box_area

    outside_measurement = measure_mask_outside_box(obj, mask)
    if not outside_measurement["measurement_available"]:
        result["reason"] = outside_measurement["reason"]
        return result

    inside_pixels = (
        outside_measurement["total_mask_pixels"]
        - outside_measurement["outside_pixels"]
    )
    result["measurement_available"] = True
    result["inside_mask_pixels"] = inside_pixels
    result["fill_fraction"] = inside_pixels / box_area
    return result


def measure_mask_components(mask, object_id, connectivity=8):
    """Measure connected pieces of one instance, largest first.

    Connectivity 8 includes diagonal neighbors; 4 uses only horizontal and
    vertical neighbors. Each component reports area, fraction of this
    instance's pixels, and a one-based, inclusive bounding box. Component
    IDs identify pieces within this measurement, not annotated people.
    Multiple pieces alone do not prove a mask error or multiple people.
    """
    _validate_instance_mask(mask)
    _validate_object_id(object_id)
    if not isinstance(connectivity, (int, np.integer)) or connectivity not in (4, 8):
        raise ValueError("Connectivity must be the integer 4 or 8.")

    instance_mask = (mask == object_id).astype(np.uint8)
    total_pixels = int(np.count_nonzero(instance_mask))
    result = {
        "object_id": object_id,
        "connectivity": int(connectivity),
        "measurement_available": total_pixels > 0,
        "reason": None,
        "total_mask_pixels": total_pixels,
        "component_count": 0,
        "components": [],
    }
    if total_pixels == 0:
        result["reason"] = "missing_mask"
        return result

    label_count, _, stats, _ = cv2.connectedComponentsWithStats(
        instance_mask, connectivity=int(connectivity)
    )
    components = []
    # Label 0 describes background; labels 1 onward describe mask pieces.
    for component_id in range(1, label_count):
        left = int(stats[component_id, cv2.CC_STAT_LEFT])
        top = int(stats[component_id, cv2.CC_STAT_TOP])
        width = int(stats[component_id, cv2.CC_STAT_WIDTH])
        height = int(stats[component_id, cv2.CC_STAT_HEIGHT])
        area = int(stats[component_id, cv2.CC_STAT_AREA])
        components.append({
            "component_id": component_id,
            "area": area,
            "area_fraction": area / total_pixels,
            "bbox": (left + 1, top + 1, left + width, top + height),
        })

    components.sort(key=lambda component: component["area"], reverse=True)
    result["component_count"] = len(components)
    result["components"] = components
    return result
