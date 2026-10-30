"""Synthetic image/annotation pairs for read-only structural inspection."""

from copy import deepcopy
from hashlib import sha256
import json
import os

from PIL import Image
import pytest

from backend.app.datasets.annotation_validation import inspect_document
from backend.app.datasets.coco import parse_coco
from backend.app.datasets.labelme import parse_labelme, read_labelme
from backend.app.datasets.inspect_annotations import main
from backend.app.models.annotation_validation import ValidationLimits


def annotation(points=None):
    return {
        "imagePath": "image.png", "imageWidth": 20, "imageHeight": 15,
        "imageData": None,
        "shapes": [{"label": "object", "shape_type": "polygon", "group_id": None,
                    "points": points if points is not None else [[1, 1], [10, 1], [10, 10], [1, 10]]}],
    }


def save_image(root, name="image.png", size=(20, 15), **kwargs):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", size, "white") as image:
        image.save(path, **kwargs)
    return path


def codes(report):
    return {issue.code for issue in report.all_issues}


def test_complete_inspection_keeps_source_and_parsed_document_unchanged(tmp_path):
    path = save_image(tmp_path)
    raw = path.read_bytes()
    document = parse_labelme(annotation())
    before = deepcopy(document)
    report = inspect_document(document, tmp_path)
    assert report.all_issues == []
    image = report.images[0]
    assert image.availability == "available"
    assert (image.actual_width, image.actual_height) == (20, 15)
    assert image.image_format == "PNG" and image.sha256 == sha256(raw).hexdigest()
    assert document == before and path.read_bytes() == raw
    assert inspect_document(document, tmp_path) == report


def test_coco_uses_image_root_not_json_directory(tmp_path):
    save_image(tmp_path, "pictures/image.jpg")
    document = parse_coco({
        "images": [{"id": 1, "file_name": "pictures/image.jpg", "width": 20, "height": 15}],
        "categories": [{"id": 1, "name": "object"}],
        "annotations": [{"id": 1, "image_id": 1, "category_id": 1, "bbox": [0, 0, 20, 15]}],
    })
    document.source_path = str(tmp_path / "annotations" / "coco.json")
    report = inspect_document(document, tmp_path)
    assert not report.all_issues and report.images[0].image_format == "JPEG"
    assert document.images[0].annotations[0].mask is None


def test_labelme_resolves_relative_to_json_with_parent_reference_inside_root(tmp_path):
    save_image(tmp_path, "images/image.png")
    directory = tmp_path / "annotations"
    directory.mkdir()
    path = directory / "label.json"
    data = annotation()
    data["imagePath"] = "../images/image.png"
    path.write_text(json.dumps(data))
    document = read_labelme(path)
    assert not inspect_document(document, tmp_path).all_issues


def test_reference_base_override_is_explicit(tmp_path):
    save_image(tmp_path, "images/image.png")
    document = parse_labelme(annotation())
    assert "missing_image" in codes(inspect_document(document, tmp_path))
    assert not inspect_document(document, tmp_path, reference_base=tmp_path / "images").all_issues


@pytest.mark.parametrize("reference", ["../outside.png", "/tmp/outside.png", "C:\\image.png",
    "https://example.com/image.png", "folder\\image.png", "bad\x00.png"])
def test_unsafe_references_do_not_read_files(tmp_path, reference):
    data = annotation()
    data["imagePath"] = reference
    report = inspect_document(parse_labelme(data), tmp_path)
    assert "unsafe_image_reference" in codes(report)
    assert report.images[0].availability == "unavailable"


def test_symlink_escape_is_rejected(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    path = save_image(tmp_path)
    try:
        (root / "image.png").symlink_to(path)
    except (OSError, NotImplementedError):
        pytest.skip("Creating symlinks needs extra privileges on this platform.")
    report = inspect_document(parse_labelme(annotation()), root)
    assert "unsafe_image_reference" in codes(report)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are POSIX-only")
def test_fifo_does_not_block(tmp_path):
    os.mkfifo(tmp_path / "image.png")
    report = inspect_document(parse_labelme(annotation()), tmp_path)
    assert "invalid_image_file" in codes(report)


def test_missing_and_corrupt_files_are_reported(tmp_path):
    document = parse_labelme(annotation())
    assert "missing_image" in codes(inspect_document(document, tmp_path))
    (tmp_path / "image.png").write_bytes(b"not an image")
    report = inspect_document(document, tmp_path)
    assert "invalid_image" in codes(report)
    assert report.images[0].actual_width is None


def test_image_limit_checks(tmp_path):
    save_image(tmp_path)
    document = parse_labelme(annotation())
    assert "image_byte_limit" in codes(inspect_document(document, tmp_path,
                                       limits=ValidationLimits(max_image_bytes=5)))
    assert "image_pixel_limit" in codes(inspect_document(document, tmp_path,
                                        limits=ValidationLimits(max_image_pixels=100)))


def test_dimension_mismatch_checks_actual_extent_without_scaling(tmp_path):
    save_image(tmp_path)
    data = annotation([[1, 1], [25, 1], [25, 10]])
    data["imageWidth"] = 100
    document = parse_labelme(data)
    before = deepcopy(document)
    report = inspect_document(document, tmp_path)
    assert {"image_dimension_mismatch", "polygon_outside_image"} <= codes(report)
    assert document == before and report.images[0].actual_width == 20


def test_box_bounds_are_not_a_quality_tolerance(tmp_path):
    save_image(tmp_path)
    data = annotation()
    data["shapes"] = [{"label": "object", "shape_type": "rectangle", "points": [[-0.1, 0], [20, 15]]}]
    report = inspect_document(parse_labelme(data), tmp_path)
    assert "box_outside_image" in codes(report)
    data["shapes"][0]["points"][0][0] = 0
    assert not inspect_document(parse_labelme(data), tmp_path).all_issues


@pytest.mark.parametrize("points,expected", [
    ([[1, 1], [10, 10], [1, 10], [10, 1]], {"self_intersecting_polygon", "zero_polygon_area"}),
    ([[1, 1], [2, 2], [3, 3]], {"zero_polygon_area"}),
    ([[1, 1], [10, 1], [10, 10], [10, 1], [1, 10]], {"repeated_polygon_vertex", "self_intersecting_polygon"}),
])
def test_polygon_topology_problems_are_preserved(tmp_path, points, expected):
    save_image(tmp_path)
    document = parse_labelme(annotation(points))
    original = deepcopy(document)
    assert expected <= codes(inspect_document(document, tmp_path))
    assert document == original


@pytest.mark.parametrize("points", [
    [[1, 1], [10, 1], [10, 10], [1, 10], [1, 1]],
    [[1, 1], [1, 10], [10, 10], [10, 1]],
    [[1, 1], [10, 1], [5, 5], [10, 10], [1, 10]],
    [[0, 0], [20, 0], [20, 15], [0, 15]],
])
def test_closed_clockwise_concave_and_boundary_polygons(tmp_path, points):
    save_image(tmp_path)
    assert not inspect_document(parse_labelme(annotation(points)), tmp_path).all_issues


def test_topology_budget_reports_skip_instead_of_pass(tmp_path):
    save_image(tmp_path)
    report = inspect_document(parse_labelme(annotation()), tmp_path,
                              limits=ValidationLimits(max_polygon_vertices=3))
    assert "polygon_topology_limit" in codes(report)
    assert report.geometry_issues[0].severity == "unsupported"


def test_polygon_diagnostic_uses_original_part_index(tmp_path):
    save_image(tmp_path)
    document = parse_coco({
        "images": [{"id": 1, "file_name": "image.png", "width": 20, "height": 15}],
        "categories": [{"id": 1, "name": "object"}],
        "annotations": [{"id": 1, "image_id": 1, "category_id": 1,
                         "segmentation": [[1, 2, 3], [0, 0, 1, 1, 2, 2]]}],
    })
    report = inspect_document(document, tmp_path)
    assert "invalid_polygon" in codes(report)
    assert next(i.location for i in report.geometry_issues if i.code == "zero_polygon_area") == "/annotations/0/segmentation/1"


def test_orphan_topology_still_checked_without_inventing_an_image(tmp_path):
    document = parse_coco({"images": [], "categories": [{"id": 1, "name": "object"}],
        "annotations": [{"id": 1, "image_id": 999, "category_id": 1,
                         "segmentation": [[0, 0, 1, 1, 2, 2]]}]})
    report = inspect_document(document, tmp_path)
    assert report.unassigned_annotation_count == 1
    assert {"unresolved_image", "zero_polygon_area"} <= codes(report)
    assert not report.images


def test_embedded_image_is_not_silently_replaced_with_external_pixels(tmp_path):
    save_image(tmp_path)
    data = annotation()
    data["imageData"] = "different-embedded-content"
    report = inspect_document(parse_labelme(data), tmp_path)
    assert "embedded_image_unavailable" in codes(report)
    assert report.images[0].availability == "unavailable"


def test_exif_orientation_requires_explicit_mapping(tmp_path):
    exif = Image.Exif()
    exif[274] = 6
    save_image(tmp_path, "image.jpg", exif=exif)
    data = annotation()
    data.update(imagePath="image.jpg", imageWidth=15, imageHeight=20)
    report = inspect_document(parse_labelme(data), tmp_path)
    assert "unsupported_image_orientation" in codes(report)
    assert "image_dimension_mismatch" not in codes(report)
    assert report.images[0].availability == "available"


def test_multiframe_png_is_not_assumed_to_be_a_single_image(tmp_path):
    with Image.new("RGB", (20, 15), "white") as first, Image.new("RGB", (20, 15), "black") as second:
        first.save(tmp_path / "image.png", save_all=True, append_images=[second], duration=100)
    report = inspect_document(parse_labelme(annotation()), tmp_path)
    assert "unsupported_multiframe_image" in codes(report)


def test_cli_reports_diagnostics_and_unreadable_input_distinctly(tmp_path, capsys):
    path = tmp_path / "label.json"
    path.write_text(json.dumps(annotation()))
    args = ["labelme", str(path), "--image-root", str(tmp_path)]
    assert main(args) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["inspection_only"] and payload["diagnostic_count"] == 1
    save_image(tmp_path)
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["diagnostic_count"] == 0
    path.write_text("broken json")
    assert main(args) == 2
    assert "error" in json.loads(capsys.readouterr().err)


@pytest.mark.parametrize("value", [0, -1, True, 2.5])
def test_invalid_limits_are_configuration_errors(value):
    with pytest.raises(ValueError):
        ValidationLimits(max_image_pixels=value)
