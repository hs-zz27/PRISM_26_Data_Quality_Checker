"""Implement coordinate, dimension and box geometry measurements."""

from itertools import combinations

import numpy as np


def check_box_validity(box, image_size):
    """Check one-based, inclusive coordinates against (width, height).

    A one-pixel box is valid. Invalid annotation boxes return a reason and
    detail; invalid image dimensions raise ValueError as a caller error.
    """
    width, height = image_size
    if any(
        not isinstance(value, (int, np.integer))
        or isinstance(value, bool)
        or value <= 0
        for value in (width, height)
    ):
        raise ValueError("Image width and height must be positive integers.")

    result = {"valid": False, "bbox": None, "reason": None, "detail": None}
    try:
        coordinates = tuple(box)
    except TypeError:
        result["reason"] = "invalid_box"
        result["detail"] = "Expected four integer coordinates."
        return result

    result["bbox"] = coordinates
    if len(coordinates) != 4 or any(
        not isinstance(value, (int, np.integer)) or isinstance(value, bool)
        for value in coordinates
    ):
        result["reason"] = "invalid_box"
        result["detail"] = "Expected four integer coordinates."
        return result

    xmin, ymin, xmax, ymax = (int(value) for value in coordinates)
    result["bbox"] = (xmin, ymin, xmax, ymax)
    if xmin > xmax or ymin > ymax:
        result["reason"] = "invalid_box"
        result["detail"] = "Box minimum coordinates exceed maximum coordinates."
        return result

    if xmin < 1 or ymin < 1 or xmax > width or ymax > height:
        result["reason"] = "box_outside_image"
        result["detail"] = "Box coordinates must lie within the image bounds."
        return result

    result["valid"] = True
    return result


def measure_box_iou(box1, box2, image_size):
    """Measure rectangle overlap using one-based, inclusive coordinates.

    IoU is intersection area divided by union area, from 0 to 1.
    Invalid boxes produce an unavailable measurement. Mask pixel IDs
    are not used, and no duplicate-box thresholds are applied.
    """
    check1 = check_box_validity(box1, image_size)
    check2 = check_box_validity(box2, image_size)
    invalid_boxes = {}
    if not check1["valid"]:
        invalid_boxes["box1"] = check1["reason"]
    if not check2["valid"]:
        invalid_boxes["box2"] = check2["reason"]

    result = {
        "box1": check1["bbox"],
        "box2": check2["bbox"],
        "measurement_available": False,
        "reason": None,
        "invalid_boxes": invalid_boxes,
        "intersection_area": None,
        "union_area": None,
        "iou": None,
    }
    if invalid_boxes:
        result["reason"] = "invalid_box"
        return result

    xmin1, ymin1, xmax1, ymax1 = check1["bbox"]
    xmin2, ymin2, xmax2, ymax2 = check2["bbox"]

    # Shared rectangle: later starting edges and earlier ending edges.
    intersection_width = max(0, min(xmax1, xmax2) - max(xmin1, xmin2) + 1)
    intersection_height = max(0, min(ymax1, ymax2) - max(ymin1, ymin2) + 1)
    intersection_area = intersection_width * intersection_height

    area1 = (xmax1 - xmin1 + 1) * (ymax1 - ymin1 + 1)
    area2 = (xmax2 - xmin2 + 1) * (ymax2 - ymin2 + 1)
    union_area = area1 + area2 - intersection_area

    result["measurement_available"] = True
    result["intersection_area"] = intersection_area
    result["union_area"] = union_area
    result["iou"] = intersection_area / union_area
    return result


def measure_pairwise_box_iou(objects, image_size):
    """Compare each unique pair of annotations, without self-comparisons.

    Each result includes the two object IDs. Fewer than two annotations
    produce an empty list. High IoU alone does not establish a duplicate.
    """
    results = []
    for obj1, obj2 in combinations(objects, 2):
        measurement = measure_box_iou(obj1["bbox"], obj2["bbox"], image_size)
        measurement["object_ids"] = (obj1["object_id"], obj2["object_id"])
        results.append(measurement)
    return results
