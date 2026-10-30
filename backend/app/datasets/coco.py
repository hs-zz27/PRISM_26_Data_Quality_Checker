"""Read COCO instance/detection JSON without changing source annotations.

Polygon segmentation and XYWH boxes are normalized. RLE is retained, not decoded.
This module neither reads referenced images nor publishes catalogue records.
"""

from collections import defaultdict
from pathlib import Path

from ..models.annotation_document import AnnotationDocument, AnnotationRecord, ImageRecord, MaskReference
from .annotation_reader import (
    DEFAULT_MAX_JSON_BYTES, check_box, dimensions, document_copy, issue,
    load_document, number, polygon, polygon_bounds,
)


def parse_coco(data: dict) -> AnnotationDocument:
    source = document_copy(data, ("images", "annotations", "categories"))
    result = AnnotationDocument("coco", source)
    images = defaultdict(list)
    categories = defaultdict(list)
    for index, raw in enumerate(source["categories"]):
        location = f"/categories/{index}"
        if not isinstance(raw, dict) or type(raw.get("id")) is not int:
            issue(result, "invalid_category", location, "Category requires an integer ID.")
            continue
        categories[raw["id"]].append(raw)
        if not isinstance(raw.get("name"), str) or not raw["name"].strip():
            issue(result, "invalid_category", location, "Category requires a nonempty name.")
        if len(categories[raw["id"]]) > 1:
            issue(result, "duplicate_category_id", location, "Category ID is ambiguous.")

    for index, raw in enumerate(source["images"]):
        image = ImageRecord(f"/images/{index}", raw)
        result.images.append(image)
        if not isinstance(raw, dict):
            issue(image, "invalid_image", image.key, "Expected an image object.")
            continue
        if type(raw.get("id")) is int:
            image.source_id = raw["id"]
            images[image.source_id].append(image)
        else:
            issue(image, "invalid_image_id", image.key, "Image requires an integer ID.")
        if isinstance(raw.get("file_name"), str) and raw["file_name"].strip():
            image.file_name = raw["file_name"]
        else:
            issue(image, "missing_image_reference", image.key, "Image requires file_name.")
        dimensions(image, raw.get("width"), raw.get("height"))
    for matches in images.values():
        if len(matches) > 1:
            for image in matches:
                issue(image, "duplicate_image_id", image.key, "Image ID is ambiguous.")
    # Two image records naming one file split its annotations between identities.
    # Both records are kept; the reviewer decides which identity is intended.
    references = defaultdict(list)
    for image in result.images:
        if image.file_name is not None:
            references[image.file_name].append(image)
    for matches in references.values():
        if len(matches) > 1:
            for image in matches:
                issue(image, "duplicate_image_reference", image.key + "/file_name",
                      "Another image record uses the same file_name; records remain separate.")

    ids = defaultdict(list)
    for index, raw in enumerate(source["annotations"]):
        obj = AnnotationRecord(f"/annotations/{index}", raw)
        if not isinstance(raw, dict):
            issue(obj, "invalid_annotation", obj.key, "Expected an annotation object.")
            result.unassigned_annotations.append(obj)
            continue
        if type(raw.get("id")) is int:
            obj.source_id = raw["id"]
            ids[obj.source_id].append(obj)
        else:
            issue(obj, "invalid_annotation_id", obj.key, "Annotation requires an integer ID.")
        matches = images.get(raw.get("image_id"), []) if type(raw.get("image_id")) is int else []
        if len(matches) == 1:
            matches[0].annotations.append(obj)
        else:
            result.unassigned_annotations.append(obj)
            issue(obj, "unresolved_image", obj.key, "image_id must identify exactly one image.")
        if type(raw.get("category_id")) is int:
            obj.category_id = raw["category_id"]
        category = categories.get(obj.category_id, [])
        if len(category) == 1 and isinstance(category[0].get("name"), str) and category[0]["name"].strip():
            obj.label = category[0]["name"]
        else:
            issue(obj, "unresolved_category", obj.key, "category_id must identify one named category.")

        if "bbox" in raw:
            bbox = raw["bbox"]
            if isinstance(bbox, list) and len(bbox) == 4 and all(number(v) for v in bbox):
                x, y, width, height = bbox
                converted = (x, y, x + width, y + height)
                if all(number(v) for v in converted):
                    obj.bbox, obj.bbox_origin = converted, "supplied"
                    check_box(obj)
                else:
                    issue(obj, "invalid_box", obj.key + "/bbox", "Box conversion exceeds finite coordinates.")
            else:
                issue(obj, "invalid_box", obj.key + "/bbox", "Expected four finite XYWH numbers.")

        if "segmentation" in raw:
            segmentation = raw["segmentation"]
            if isinstance(segmentation, dict):
                obj.mask = MaskReference("coco_rle", segmentation)
                issue(obj, "unsupported_rle", obj.key + "/segmentation",
                      "RLE retained; decoding and validation are not implemented.", unsupported=True)
            elif isinstance(segmentation, list):
                for part_index, part in enumerate(segmentation):
                    location = f"{obj.key}/segmentation/{part_index}"
                    if not isinstance(part, list) or len(part) % 2:
                        issue(obj, "invalid_polygon", location, "Expected an even-length coordinate list.")
                    else:
                        polygon(obj, [part[i:i + 2] for i in range(0, len(part), 2)], location)
            else:
                issue(obj, "invalid_segmentation", obj.key + "/segmentation", "Expected polygons or RLE.")
        if "bbox" not in raw:
            polygon_bounds(obj)
        if "keypoints" in raw:
            issue(obj, "unsupported_keypoints", obj.key + "/keypoints",
                  "Keypoints retained in source only.", unsupported=True)
        if obj.bbox is None and not obj.polygons and obj.mask is None:
            issue(obj, "missing_geometry", obj.key, "No supported box, polygon or retained mask.")
    for matches in ids.values():
        if len(matches) > 1:
            for obj in matches:
                issue(obj, "duplicate_annotation_id", obj.key, "Annotation ID is duplicated; records remain separate.")
    return result


def read_coco(path: str | Path, *, max_bytes: int = DEFAULT_MAX_JSON_BYTES) -> AnnotationDocument:
    return load_document(path, parse_coco, max_bytes=max_bytes)
