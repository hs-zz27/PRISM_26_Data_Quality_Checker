"""Never-supplied vs malformed vs derived vs retained vs missing. Synthetic records only."""

from copy import deepcopy

import pytest

from backend.app.datasets.annotation_summary import summarize_annotation
from backend.app.datasets.coco import parse_coco
from backend.app.datasets.labelme import parse_labelme
from backend.app.models.annotation_document import AnnotationRecord, MaskReference


def coco(annotation):
    return parse_coco({
        "images": [{"id": 1, "file_name": "a.png", "width": 100, "height": 80}],
        "categories": [{"id": 1, "name": "thing"}],
        "annotations": [{"id": 1, "image_id": 1, "category_id": 1, **annotation}],
    }).images[0].annotations[0]


def labelme(shape):
    data = {"imagePath": "a.png", "imageWidth": 100, "imageHeight": 80, "shapes": [
        {"label": "thing", "group_id": None, **shape}]}
    return parse_labelme(data).images[0].annotations[0]


def states(obj, **kwargs):
    summary = summarize_annotation(obj, **kwargs)
    return summary.box.state, summary.polygon.state, summary.mask.state


def test_box_only_sample_has_no_polygon_or_mask_and_that_is_not_an_error():
    obj = coco({"bbox": [1, 2, 3, 4]})
    assert states(obj) == ("supplied", "not_supplied", "not_supplied")
    assert not obj.issues


def test_polygon_only_box_is_marked_derived_and_original_polygon_is_kept():
    obj = coco({"segmentation": [[1, 1, 9, 1, 9, 9]]})
    summary = summarize_annotation(obj)
    assert (summary.box.state, summary.polygon.state) == ("derived", "supplied")
    assert obj.bbox_origin == "derived_from_polygon" and obj.polygons == [[(1, 1), (9, 1), (9, 9)]]
    assert "derived" in " ".join(summary.types_present)
    assert obj.source["segmentation"] == [[1, 1, 9, 1, 9, 9]]


def test_labelme_polygon_and_rectangle_states():
    polygon = labelme({"shape_type": "polygon", "points": [[1, 1], [9, 1], [9, 9]]})
    rectangle = labelme({"shape_type": "rectangle", "points": [[9, 9], [1, 1]]})
    assert states(polygon) == ("derived", "supplied", "not_supplied")
    assert states(rectangle) == ("supplied", "not_supplied", "not_supplied")


def test_malformed_supplied_box_is_not_confused_with_never_supplied_or_derived():
    obj = coco({"bbox": [1, 2, 3], "segmentation": [[1, 1, 9, 1, 9, 9]]})
    assert states(obj) == ("malformed", "supplied", "not_supplied")
    assert obj.bbox is None  # the valid polygon never replaces a malformed supplied box


def test_malformed_rectangle_and_polygon_are_malformed_not_absent():
    assert states(labelme({"shape_type": "rectangle", "points": [[1, 1]]}))[0] == "malformed"
    assert states(labelme({"shape_type": "polygon", "points": [[1, 1], [2]]}))[1] == "malformed"
    assert states(coco({"bbox": [0, 0, 1, 1], "segmentation": [[1, 2, 3]]}))[1] == "malformed"


def test_empty_segmentation_list_is_not_supplied_with_a_note():
    summary = summarize_annotation(coco({"bbox": [0, 0, 5, 5], "segmentation": []}))
    assert summary.polygon.state == "not_supplied" and "empty" in summary.polygon.detail


def test_partially_parsed_polygon_parts_are_reported():
    summary = summarize_annotation(coco({"bbox": [0, 0, 9, 9],
                                         "segmentation": [[1, 1, 9, 1, 9, 9], [1, 2, 3]]}))
    assert summary.polygon.state == "supplied" and "1 further part" in summary.polygon.detail
    assert summary.polygon_parts == 1


@pytest.mark.parametrize("data", [
    {"counts": [10, 5], "size": [80, 100]},
])
def test_rle_is_retained_not_decoded_and_not_called_supplied(data):
    obj = coco({"bbox": [0, 0, 5, 5], "segmentation": data})
    assert summarize_annotation(obj).mask.state == "retained_not_decoded"
    assert obj.mask.decoded is False


def test_unsupported_shapes_report_no_geometry_instead_of_inventing_some():
    for shape in ({"shape_type": "circle", "points": [[1, 1], [3, 1]]}, {"points": [[1, 1], [2, 2], [3, 1]]}):
        obj = labelme(shape)
        assert states(obj) == ("not_supplied",) * 3
        assert any(issue.code == "unsupported_shape" for issue in obj.issues)


def test_file_referenced_mask_distinguishes_missing_from_never_supplied():
    never = AnnotationRecord("/x/0", {})
    referenced = AnnotationRecord("/x/1", {}, mask=MaskReference("file", "masks/a.png"))
    assert summarize_annotation(never).mask.state == "not_supplied"
    assert summarize_annotation(referenced, mask_file_exists=lambda ref: False).mask.state == "referenced_missing"
    assert summarize_annotation(referenced, mask_file_exists=lambda ref: True).mask.state == "supplied"
    unchecked = summarize_annotation(referenced).mask
    assert unchecked.state == "supplied" and "not checked" in unchecked.detail


def test_summary_does_not_modify_the_record():
    obj = coco({"segmentation": [[1, 1, 9, 1, 9, 9]]})
    before = deepcopy((obj.bbox, obj.bbox_origin, obj.polygons, obj.source, obj.issues))
    summarize_annotation(obj)
    assert (obj.bbox, obj.bbox_origin, obj.polygons, obj.source, obj.issues) == before
