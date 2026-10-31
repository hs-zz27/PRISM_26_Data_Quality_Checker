"""Tests for tools/check_l01_candidate.py. Every image and annotation here is SYNTHETIC (invented
in the test), written only to exercise the checker. None of it is a real dataset or a real person."""

import hashlib
import importlib.util
import json
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("check_l01_candidate", HERE.parent / "tools" / "check_l01_candidate.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
REPO = HERE.parents[2]


def make(tmp_path, *, size=(400, 300), points=None, label="person", shape_type="polygon", image_data=None,
         declared=None, name="synthetic.jpg", exif=None, image_path=None):
    picture = Image.new("RGB", size, (180, 190, 200))
    target = tmp_path / name
    if exif is not None:
        data = Image.Exif()
        data[274] = exif
        picture.save(target, format="JPEG", exif=data)
    else:
        picture.save(target, format="JPEG")
    points = points if points is not None else [[40, 60], [120, 60], [140, 200], [50, 210]]
    document = {"version": "synthetic", "flags": {}, "shapes": [
        {"label": label, "points": points, "group_id": None, "shape_type": shape_type, "flags": {}}],
        "imagePath": image_path or name, "imageData": image_data,
        "imageHeight": (declared or size)[1], "imageWidth": (declared or size)[0]}
    annotation = tmp_path / (Path(name).stem + ".json")
    annotation.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return target, annotation


def results(report):
    return {c["check"]: c for c in report["checks"]}


def failed(report):
    return [c["check"] for c in report["checks"] if not c["ok"]]


def test_eligible_candidate_passes_and_shift_is_exactly_ten_percent_of_width(tmp_path):
    image, annotation = make(tmp_path)
    report = checker.check(image, annotation)
    assert report["passed"], failed(report)
    shape = report["info"]["person_shapes"][0]
    assert shape["direction"] == "right" and report["info"]["shift_pixels"] == 40
    before, after = shape["original_points"], shape["shifted_points"]
    assert [p[0] + 40 for p in before] == [p[0] for p in after]          # every x moved by exactly 0.1 * 400
    assert [p[1] for p in before] == [p[1] for p in after]                # every y unchanged
    assert len(before) == len(after)                                       # vertex count and order unchanged
    assert all(0 <= p[0] <= 400 for p in after)                            # still inside the image


def test_fractional_coordinates_use_exact_decimal_arithmetic(tmp_path):
    image, annotation = make(tmp_path, size=(333, 200), points=[[10.5, 20.25], [60.1, 20.25], [60.1, 90.75]])
    shape = checker.check(image, annotation)["info"]["person_shapes"][0]
    assert shape["shifted_points"][0][0] == float(Decimal("10.5") + Decimal("33.3"))      # 43.8, no float noise
    assert shape["shifted_points"][2] == [float(Decimal("60.1") + Decimal("33.3")), 90.75]


def test_polygon_near_the_left_edge_shifts_right_and_near_the_right_edge_shifts_left(tmp_path):
    image, annotation = make(tmp_path, points=[[5, 50], [90, 50], [90, 200]])
    assert checker.check(image, annotation)["info"]["person_shapes"][0]["direction"] == "right"
    image2, annotation2 = make(tmp_path, name="right.jpg", points=[[300, 50], [395, 50], [395, 200]])
    assert checker.check(image2, annotation2)["info"]["person_shapes"][0]["direction"] == "left"


@pytest.mark.parametrize("kwargs, expected", [
    ({"image_data": "AAAA"}, "annotation does not embed the image"),
    ({"label": "car"}, "at least one shape labelled"),
    ({"shape_type": "circle", "points": [[50, 50], [90, 50]]}, "a polygon the reader supports"),
    ({"points": [[5, 50], [395, 50], [395, 200]]}, "a polygon the reader supports"),            # too wide to shift either way
    ({"points": [[-5, 50], [90, 50], [90, 200]]}, "a polygon the reader supports"),             # already outside the image
    ({"declared": (500, 300)}, "declared image size equals decoded size"),
    ({"exif": 6}, "image has no EXIF rotation"),
    ({"image_path": "other_name.jpg"}, "imagePath names this image file"),
])
def test_each_requirement_failure_is_reported_by_name(tmp_path, kwargs, expected):
    image, annotation = make(tmp_path, **kwargs)
    report = checker.check(image, annotation)
    assert not report["passed"]
    assert any(name.startswith(expected) for name in failed(report)), failed(report)


def test_png_is_accepted_and_other_formats_are_not(tmp_path):
    image, annotation = make(tmp_path, name="synthetic.png")          # saved as JPEG bytes under a .png name: format is read from content
    assert checker.check(image, annotation)["info"]["image_format"] == "JPEG"
    bmp = tmp_path / "synthetic.bmp"
    Image.new("RGB", (400, 300)).save(bmp, format="BMP")
    (tmp_path / "synthetic.json").write_text(annotation.read_text().replace("synthetic.png", "synthetic.bmp"))
    assert "image is an external PNG or JPEG file" in failed(checker.check(bmp, tmp_path / "synthetic.json"))


def test_the_three_existing_labelme_images_are_rejected():
    source = REPO / "datasets" / "labelme_people_examples"
    if not (source / "2011_000006.jpg").is_file():
        pytest.skip("source dataset not present")
    report = checker.check(source / "2011_000006.jpg", source / "2011_000006.json")
    assert "different from the three existing LabelMe images" in failed(report)


def test_a_renamed_copy_of_an_existing_image_is_still_rejected(tmp_path):
    source = REPO / "datasets" / "labelme_people_examples"
    if not (source / "2011_000006.jpg").is_file():
        pytest.skip("source dataset not present")
    copy = tmp_path / "different_name.jpg"
    copy.write_bytes((source / "2011_000006.jpg").read_bytes())
    document = json.loads((source / "2011_000006.json").read_text())
    document["imagePath"] = "different_name.jpg"
    (tmp_path / "different_name.json").write_text(json.dumps(document))
    assert "different from the three existing LabelMe images" in failed(
        checker.check(copy, tmp_path / "different_name.json"))


def test_inputs_are_never_modified_and_overlays_never_overwrite(tmp_path):
    image, annotation = make(tmp_path)
    before = (hashlib.sha256(image.read_bytes()).hexdigest(), hashlib.sha256(annotation.read_bytes()).hexdigest())
    out = tmp_path / "out"
    checker.check(image, annotation, out_dir=out)
    first = out / "l01_candidate_check.png"
    first_bytes = first.read_bytes()
    checker.check(image, annotation, out_dir=out)
    assert first.read_bytes() == first_bytes and (out / "l01_candidate_check-2.png").exists()
    assert before == (hashlib.sha256(image.read_bytes()).hexdigest(), hashlib.sha256(annotation.read_bytes()).hexdigest())


def test_command_line_exit_codes(tmp_path, capsys):
    image, annotation = make(tmp_path)
    assert checker.main(["--image", str(image), "--annotation", str(annotation)]) == 0
    assert "Still needs a human" in capsys.readouterr().out
    bad_image, bad_annotation = make(tmp_path, name="bad.jpg", label="car")
    assert checker.main(["--image", str(bad_image), "--annotation", str(bad_annotation)]) == 1
    assert checker.main(["--image", str(tmp_path / "missing.jpg"), "--annotation", str(annotation)]) == 2
