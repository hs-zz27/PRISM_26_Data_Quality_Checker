"""Synthetic format fixtures; no downloads, database, model calls or real images."""

from copy import deepcopy
import json

import pytest

from backend.app.datasets.annotation_reader import AnnotationReadError
from backend.app.datasets.coco import parse_coco, read_coco
from backend.app.datasets.labelme import parse_labelme, read_labelme


def coco():
    return {
        "images": [{"id": 7, "file_name": "nested/example.jpg", "width": 100, "height": 80},
                   {"id": 8, "file_name": "empty.jpg", "width": 100, "height": 80}],
        "categories": [{"id": 5, "name": "vehicle"}],
        "annotations": [{"id": 9, "image_id": 7, "category_id": 5,
                         "bbox": [1.25, 2.5, 10.5, 20.25], "iscrowd": 0,
                         "segmentation": [[1.25, 2.5, 11.75, 2.5, 11.75, 22.75],
                                          [4, 5, 6, 5, 6, 7]], "area": 30}],
    }


def labelme():
    return {
        "version": "synthetic", "imagePath": "example.jpg", "imageData": None,
        "imageWidth": 100, "imageHeight": 80, "flags": {"review": True},
        "shapes": [
            {"label": "vehicle", "shape_type": "polygon", "group_id": 0,
             "points": [[1.25, 2], [8, 2], [8, 10.5]], "flags": {"occluded": True}},
            {"label": "vehicle", "shape_type": "polygon", "group_id": 0,
             "points": [[20, 20], [21, 20], [21, 21]]},
            {"label": "sign", "shape_type": "rectangle", "group_id": None,
             "points": [[50.5, 60], [30, 20.25]]},
        ],
    }


def codes(result):
    return {entry.code for entry in result.all_issues}


def test_coco_preserves_parts_labels_ids_empty_images_and_source():
    data = coco()
    original = deepcopy(data)
    result = parse_coco(data)
    first, empty = result.images
    obj = first.annotations[0]
    assert obj.source_id == 9 and obj.category_id == 5 and obj.label == "vehicle"
    assert first.source_id == 7 and first.file_name == "nested/example.jpg"
    assert (first.width, first.height) == (100, 80)
    assert obj.bbox == (1.25, 2.5, 11.75, 22.75) and obj.bbox_origin == "supplied"
    assert len(obj.polygons) == 2 and obj.polygons[0][0] == (1.25, 2.5)
    assert obj.mask is None and not empty.annotations and not result.all_issues
    assert data == original == result.source
    data["annotations"][0]["segmentation"][0][0] = 999
    assert obj.source["segmentation"][0][0] == 1.25


def test_labelme_rectangles_and_grouped_polygons_are_not_merged():
    result = parse_labelme(labelme())
    first, second, rectangle = result.images[0].annotations
    assert first.bbox == (1.25, 2, 8, 10.5)
    assert first.bbox_origin == "derived_from_polygon"
    assert first.group_id == second.group_id == 0
    assert first.key != second.key and first.source_id is None
    assert first.source["flags"] == {"occluded": True}
    assert rectangle.bbox == (30, 20.25, 50.5, 60)
    assert rectangle.bbox_origin == "supplied"
    assert rectangle.source["points"] == [[50.5, 60], [30, 20.25]]
    assert not result.all_issues


def test_duplicate_ids_remain_separate_and_ambiguous_images_are_unassigned():
    data = coco()
    data["annotations"].append(deepcopy(data["annotations"][0]))
    result = parse_coco(data)
    objects = result.images[0].annotations
    assert len(objects) == 2 and objects[0].key != objects[1].key
    assert all("duplicate_annotation_id" in {i.code for i in o.issues} for o in objects)
    data["images"].append(deepcopy(data["images"][0]))
    result = parse_coco(data)
    assert len(result.unassigned_annotations) == 2
    assert all(not image.annotations for image in result.images)
    assert {"duplicate_image_id", "unresolved_image"} <= codes(result)


@pytest.mark.parametrize("category_id", [True, None, "5", 999, [], {}])
def test_invalid_category_references_are_preserved_not_coerced(category_id):
    data = coco()
    data["annotations"][0]["category_id"] = category_id
    result = parse_coco(data)
    assert result.images[0].annotations[0].label is None
    assert "unresolved_category" in codes(result)


def test_ambiguous_categories_do_not_choose_a_label():
    data = coco()
    data["categories"].append({"id": 5, "name": "different"})
    result = parse_coco(data)
    assert {"duplicate_category_id", "unresolved_category"} <= codes(result)
    assert result.images[0].annotations[0].label is None


@pytest.mark.parametrize("reference", [None, True, "7", 999, [], {}])
def test_orphans_are_retained(reference):
    data = coco()
    data["annotations"][0]["image_id"] = reference
    result = parse_coco(data)
    assert len(result.unassigned_annotations) == 1
    assert result.unassigned_annotations[0].source["image_id"] == reference
    assert "unresolved_image" in codes(result)


@pytest.mark.parametrize("segmentation", [
    {"size": [80, 100], "counts": [10, 5, 7985]},
    {"size": [80, 100], "counts": "encoded-source"},
])
def test_rle_is_retained_with_explicit_unsupported_diagnostic(segmentation):
    data = coco()
    data["annotations"][0]["segmentation"] = segmentation
    result = parse_coco(data)
    obj = result.images[0].annotations[0]
    assert obj.mask.encoding == "coco_rle" and obj.mask.value == segmentation
    assert not obj.mask.decoded and not obj.polygons
    assert result.all_issues[0].severity == "unsupported"


def test_invalid_boxes_and_parts_are_never_silently_repaired():
    data = coco()
    data["annotations"][0]["bbox"] = [-2, 5, -3, 0]
    data["annotations"][0]["segmentation"] = [[0, 0, 1], [True, 0, 1, 0, 1, 1], [2, 2, 2, 2]]
    result = parse_coco(data)
    obj = result.images[0].annotations[0]
    assert obj.bbox == (-2, 5, -5, 5)
    assert obj.polygons == [[(2, 2), (2, 2)]]
    assert {"degenerate_box", "invalid_polygon", "degenerate_polygon"} <= codes(result)
    assert obj.source == data["annotations"][0]


def test_missing_box_is_derived_but_invalid_supplied_box_is_not_replaced():
    data = coco()
    del data["annotations"][0]["bbox"]
    obj = parse_coco(data).images[0].annotations[0]
    assert obj.bbox_origin == "derived_from_polygon"
    data["annotations"][0]["bbox"] = [True, 2, 3, 4]
    result = parse_coco(data)
    assert result.images[0].annotations[0].bbox is None
    assert "invalid_box" in codes(result)


def test_box_conversion_overflow_is_diagnosed():
    data = coco()
    data["annotations"][0]["bbox"] = [1e308, 0, 1e308, 10]
    result = parse_coco(data)
    assert result.images[0].annotations[0].bbox is None
    assert "invalid_box" in codes(result)


@pytest.mark.parametrize("shape_type", ["circle", "line", "point", "mask", None])
def test_unsupported_labelme_shapes_and_masks_are_retained(shape_type):
    data = labelme()
    data["shapes"][0].update(shape_type=shape_type, mask="encoded-png")
    result = parse_labelme(data)
    obj = result.images[0].annotations[0]
    assert obj.mask.encoding == "labelme_png" and not obj.mask.decoded
    assert obj.source == data["shapes"][0]
    assert {"unsupported_shape", "unsupported_mask"} <= codes(result)


def test_labelme_invalid_records_and_group_ids_are_diagnosed():
    data = labelme()
    data["shapes"].extend([None, {"label": "", "shape_type": "rectangle",
                                  "points": [[1, 2]], "group_id": False}])
    result = parse_labelme(data)
    assert len(result.images[0].annotations) == 5
    assert {"invalid_shape", "invalid_label", "invalid_rectangle", "invalid_group_id"} <= codes(result)


def test_coco_malformed_records_are_not_lost():
    data = coco()
    data["images"].append(None)
    data["annotations"].append("broken")
    data["categories"].append(None)
    result = parse_coco(data)
    assert len(result.images) == 3
    assert result.unassigned_annotations[0].source == "broken"
    assert {"invalid_image", "invalid_annotation", "invalid_category"} <= codes(result)


@pytest.mark.parametrize("parser,data", [(parse_coco, coco()), (parse_labelme, labelme())])
def test_empty_annotations_are_valid(parser, data):
    data["annotations" if parser is parse_coco else "shapes"] = []
    result = parser(data)
    assert not result.all_issues and all(not image.annotations for image in result.images)


def test_missing_mask_does_not_invalidate_box_only_sample():
    data = coco()
    del data["annotations"][0]["segmentation"]
    result = parse_coco(data)
    assert not result.all_issues and result.images[0].annotations[0].mask is None


@pytest.mark.parametrize("reader,data", [(read_coco, coco()), (read_labelme, labelme())])
def test_file_read_preserves_exact_bytes_without_opening_images(tmp_path, reader, data):
    path = tmp_path / "source.json"
    raw = b"\xef\xbb\xbf" + json.dumps(data, indent=2).encode()
    path.write_bytes(raw)
    result = reader(path)
    assert result.source_bytes == raw and result.source_path == str(path)
    assert path.read_bytes() == raw
    assert not (tmp_path / "example.jpg").exists()
    with pytest.raises(AnnotationReadError, match="max_bytes"):
        reader(path, max_bytes=len(raw) - 1)


@pytest.mark.parametrize("raw", [b"{}", b"[]", b"{", b'\xff',
    b'{"shapes":[], "shapes":[]}', b'{"shapes":[],"x":NaN}',
    b'{"shapes":[],"x":1e999}'])
def test_unreadable_or_ambiguous_json_is_rejected(tmp_path, raw):
    path = tmp_path / "bad.json"
    path.write_bytes(raw)
    with pytest.raises(AnnotationReadError):
        read_labelme(path)


def test_labelme_embedded_image_is_explicitly_deferred():
    data = labelme()
    data["imageData"] = "encoded-image"
    result = parse_labelme(data)
    assert "unsupported_embedded_image" in codes(result)
    assert result.source["imageData"] == "encoded-image"


@pytest.mark.parametrize("parser,data", [(parse_coco, coco()), (parse_labelme, labelme())])
def test_nonfinite_python_input_is_rejected(parser, data):
    data["extra"] = float("nan")
    with pytest.raises(AnnotationReadError):
        parser(data)
