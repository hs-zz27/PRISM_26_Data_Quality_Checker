"""Read one LabelMe JSON file, preserving each shape and its grouping metadata."""

from pathlib import Path

from ..models.annotation_document import AnnotationDocument, AnnotationRecord, ImageRecord, MaskReference
from .annotation_reader import (
    DEFAULT_MAX_JSON_BYTES, check_box, dimensions, document_copy, issue,
    load_document, number, polygon, polygon_bounds,
)


def parse_labelme(data: dict) -> AnnotationDocument:
    source = document_copy(data, ("shapes",))
    result = AnnotationDocument("labelme", source)
    image = ImageRecord("", {key: value for key, value in source.items() if key != "shapes"})
    result.images.append(image)
    if isinstance(source.get("imagePath"), str) and source["imagePath"].strip():
        image.file_name = source["imagePath"]
    if source.get("imageData") is not None:
        issue(image, "unsupported_embedded_image", "/imageData",
              "Embedded image retained; decoding is not implemented.", unsupported=True)
    if image.file_name is None and source.get("imageData") is None:
        issue(image, "missing_image_reference", "/imagePath", "No imagePath or imageData supplied.")
    dimensions(image, source.get("imageWidth"), source.get("imageHeight"))
    for index, raw in enumerate(source["shapes"]):
        obj = AnnotationRecord(f"/shapes/{index}", raw)
        image.annotations.append(obj)
        if not isinstance(raw, dict):
            issue(obj, "invalid_shape", obj.key, "Expected a shape object.")
            continue
        if isinstance(raw.get("label"), str):
            obj.label = raw["label"]
        if not obj.label or not obj.label.strip():
            issue(obj, "invalid_label", obj.key + "/label", "Expected a nonempty label.")
        group = raw.get("group_id")
        if group is None or type(group) is int:
            obj.group_id = group
        else:
            issue(obj, "invalid_group_id", obj.key + "/group_id", "Expected integer group_id or null.")
        shape_type = raw.get("shape_type")
        if shape_type == "polygon":
            polygon(obj, raw.get("points"), obj.key + "/points")
            polygon_bounds(obj)
        elif shape_type == "rectangle":
            points = raw.get("points")
            if (isinstance(points, list) and len(points) == 2 and all(
                isinstance(p, list) and len(p) == 2 and all(number(v) for v in p) for p in points
            )):
                (x1, y1), (x2, y2) = points
                # LabelMe permits drawing rectangles in either direction. This
                # is a representation conversion, not a repair of source points.
                obj.bbox = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
                obj.bbox_origin = "supplied"
                check_box(obj)
            else:
                issue(obj, "invalid_rectangle", obj.key + "/points", "Expected two finite coordinate pairs.")
        else:
            issue(obj, "unsupported_shape", obj.key + "/shape_type",
                  "Only explicit polygon and rectangle shapes are normalized.", unsupported=True)
        if raw.get("mask") is not None:
            obj.mask = MaskReference("labelme_png", raw["mask"])
            issue(obj, "unsupported_mask", obj.key + "/mask",
                  "Embedded shape mask retained; decoding is not implemented.", unsupported=True)
    return result


def read_labelme(path: str | Path, *, max_bytes: int = DEFAULT_MAX_JSON_BYTES) -> AnnotationDocument:
    return load_document(path, parse_labelme, max_bytes=max_bytes)
