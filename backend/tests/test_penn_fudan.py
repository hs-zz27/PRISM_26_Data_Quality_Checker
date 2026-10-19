"""Source parsing and independently readable annotation metadata."""

import pytest

from backend.app.datasets.penn_fudan import parse_annotation_metadata, parse_annotations


HEADERS = (
    'Image size (X x Y x C) : 4 x 3 x 3\n'
    'Objects with ground truth : 1 { "person" }\n'
    '# Top left pixel co-ordinates : (1, 1)\n'
)
BOX = 'Bounding box for object 1 "person" (Xmin, Ymin) - (Xmax, Ymax) : (0, 2) - (3, 4)\n'


def test_parser_preserves_source_coordinates_even_when_outside_image():
    assert parse_annotations(HEADERS + BOX) == [
        {"object_id": 1, "label": "person", "bbox": (0, 2, 3, 4)},
    ]
    assert parse_annotation_metadata(HEADERS + BOX) == {
        "image_size": (4, 3, 3), "object_count": 1,
        "coordinate_origin": (1, 1), "errors": {},
    }


def test_headers_support_crlf_and_surrounding_whitespace():
    text = "\r\n".join(f"  {line}  " for line in HEADERS.splitlines())
    assert parse_annotation_metadata(text) == parse_annotation_metadata(HEADERS)


def test_missing_metadata_is_explicit_and_does_not_invent_an_origin():
    metadata = parse_annotation_metadata(BOX)
    assert metadata["image_size"] is None and metadata["coordinate_origin"] is None
    assert metadata["errors"] == {
        "image_size": "missing_metadata", "object_count": "missing_metadata",
        "coordinate_origin": "missing_metadata",
    }


@pytest.mark.parametrize("field,line", [
    ("image_size", "Image size (X x Y x C) : 4 x three x 3"),
    ("object_count", 'Objects with ground truth : many { "person" }'),
    ("coordinate_origin", "# Top left pixel co-ordinates : (one, one)"),
])
def test_malformed_metadata_does_not_discard_boxes(field, line):
    metadata = parse_annotation_metadata(line + "\n" + BOX)
    assert metadata["errors"][field] == "malformed_metadata"
    assert parse_annotations(line + "\n" + BOX)[0]["object_id"] == 1


def test_duplicate_origins_are_unavailable_instead_of_choosing_one():
    metadata = parse_annotation_metadata(HEADERS + "# Top left pixel co-ordinates : (0, 0)\n")
    assert metadata["coordinate_origin"] is None
    assert metadata["errors"] == {"coordinate_origin": "duplicate_metadata"}


def test_malformed_box_reports_its_source_line():
    with pytest.raises(ValueError, match="line 4"):
        parse_annotations(HEADERS + "Bounding box for object broken")


@pytest.mark.parametrize("text", [
    "", " \n", "not an annotation", HEADERS,
    "Objects with ground truth : -1 {}",
    "Objects with ground truth : broken {}",
    "Objects with ground truth : 0 {}\nObjects with ground truth : 0 {}",
])
def test_unusable_annotation_is_not_a_valid_empty_annotation(text):
    with pytest.raises(ValueError, match="zero-object declaration"):
        parse_annotations(text)


def test_explicit_zero_object_annotation_does_not_require_unrelated_headers():
    assert parse_annotations("Objects with ground truth : 0 {}") == []


def test_zero_object_header_does_not_hide_a_malformed_box():
    with pytest.raises(ValueError, match="Malformed bounding box"):
        parse_annotations("Objects with ground truth : 0 {}\nBounding box for object broken")
