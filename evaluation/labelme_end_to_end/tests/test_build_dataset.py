"""The seeded LabelMe test datasets: reproducible, faithful to their sources, and importable.

No application service or model runs here. Tests that need the local source photos (datasets/, kept out of Git)
are skipped with a reason when those are absent; the error-injection tests need nothing.
"""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import zipfile

import pytest

SPEC = importlib.util.spec_from_file_location("labelme_flow_build", Path(__file__).resolve().parents[1] / "build_dataset.py")
b = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(b)

needs_sources = pytest.mark.skipif(
    not b.COCO_JSON.is_file() or not (b.NATIVE_DIR / "2011_000003.json").is_file(),
    reason="local source datasets are not downloaded (see datasets/README.md)")
SIZE = (640, 480)


def person(left=100.0, top=50.0, width=80.0, height=300.0, group_id=None, label="person"):
    """A simple outline: narrow head on top, wider body, two feet."""
    right, bottom, middle = left + width, top + height, left + width / 2
    points = [[middle - 10, top], [middle + 10, top], [middle + 12, top + 40], [right, top + 60],
              [right, bottom - 40], [middle + 25, bottom], [middle + 5, bottom], [middle, bottom - 60],
              [middle - 5, bottom], [middle - 25, bottom], [left, bottom - 40], [left, top + 60], [middle - 12, top + 40]]
    return {"label": label, "points": points, "group_id": group_id, "description": "", "shape_type": "polygon", "flags": {}}


def document(*shapes):
    return {"version": "5.4.1", "flags": {}, "shapes": list(shapes), "imagePath": "photo.jpg", "imageData": None,
            "imageHeight": SIZE[1], "imageWidth": SIZE[0]}


def members(path):
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


# ── error injection (no local data needed) ─────────────────────────────────
def test_head_cut_and_feet_cut_remove_only_that_end_of_the_outline():
    points = person()["points"]
    left, top, right, bottom = b.bounds(points)
    cut, facts = b.head_cut(points, SIZE)
    assert facts["cut_line_y"] == top + b.CUT_FRACTION * (bottom - top)
    assert b.bounds(cut) == (left, facts["cut_line_y"], right, bottom)
    assert all(point in points for point in cut if point[1] > facts["cut_line_y"])
    cut, facts = b.feet_cut(points, SIZE)
    assert b.bounds(cut) == (left, top, right, facts["cut_line_y"])
    assert all(point in points for point in cut if point[1] < facts["cut_line_y"])


def test_extra_background_and_shift_stay_inside_the_image_and_keep_the_vertex_order():
    points = person()["points"]
    left, top, right, bottom = b.bounds(points)
    wide, facts = b.extra_background(points, SIZE)
    assert len(wide) == len(points) and [p[1] for p in wide] == [p[1] for p in points]
    assert facts["side"] in ("left", "right") and b.bounds(wide)[3] == bottom
    assert (b.bounds(wide)[2] - b.bounds(wide)[0]) > (right - left) * 1.25
    assert 0 <= b.bounds(wide)[0] and b.bounds(wide)[2] <= SIZE[0] - 1
    moved, facts = b.shifted(points, SIZE)
    assert [round(new[0] - old[0], 2) for new, old in zip(moved, points)] == [facts["offset_x"]] * len(points)
    assert abs(facts["offset_x"]) == round(b.SHIFT_FRACTION * (right - left), 2)
    # No room on either side: the error is skipped instead of leaving the image.
    assert b.shifted([[1, 10], [638, 10], [638, 200], [1, 200]], SIZE) is None
    assert b.extra_background([[1, 10], [638, 10], [638, 200], [1, 200]], SIZE) is None


@pytest.mark.parametrize("kind", b.ERROR_TYPES)
def test_each_error_changes_one_annotation_and_adds_no_structural_problem(kind):
    source = document(person(), person(left=350.0))
    before = copy.deepcopy(source)
    altered, entry = b.inject(source, kind, 1)
    assert source == before  # the input document is never edited in place
    assert entry["error"] == kind and entry["source_shape_key"] == "/shapes/1"
    assert b.structural_codes(altered) == b.structural_codes(source) == []
    assert altered["shapes"][0] == source["shapes"][0]
    assert {k: v for k, v in altered.items() if k != "shapes"} == {k: v for k, v in source.items() if k != "shapes"}
    if kind == "annotation_removed":
        assert len(altered["shapes"]) == 1 and entry["shape_key"] is None
    elif kind == "wrong_label":
        assert altered["shapes"][1] == {**source["shapes"][1], "label": b.WRONG_LABEL}
    else:
        assert altered["shapes"][1]["points"] != source["shapes"][1]["points"]
        assert {k: v for k, v in altered["shapes"][1].items() if k != "points"} == \
            {k: v for k, v in source["shapes"][1].items() if k != "points"}


def test_only_tall_single_part_outlines_take_an_error_and_a_removed_person_stands_apart():
    doc = document(person(), person(left=300.0, height=60.0), person(left=450.0, group_id=1), person(left=110.0))
    assert b.candidates(doc, "head_cut") == [0, 3]          # 1 is too short, 2 is one part of a grouped person
    assert b.candidates(doc, "annotation_removed") == []    # 0 and 3 overlap each other
    assert b.candidates(document(person(), person(left=350.0)), "annotation_removed") == [0, 1]
    assert b.candidates(document(person()), "annotation_removed") == []  # never empties a file


def test_plan_is_reproducible_and_leaves_controls():
    documents = {f"photo{i:02}": document(person(), person(left=350.0)) for i in range(12)}
    first = b.plan_errors(documents, 7)
    assert first == b.plan_errors(copy.deepcopy(documents), 7)
    assert first != b.plan_errors(documents, 8)
    altered, cases = first
    assert [case["file"] for case in cases] == sorted(f"{stem}.json" for stem in documents)
    unchanged = [case for case in cases if case["error"] is None]
    assert len(unchanged) == 3 and all(altered[Path(case["file"]).stem] == documents[Path(case["file"]).stem] for case in unchanged)
    assert {case["error"] for case in cases if case["error"]} == set(b.ERROR_TYPES)
    assert all(case["people"] == 2 and case["shapes"] == 2 for case in cases)


# ── the real build ──────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("labelme-flow")
    sources = [b.COCO_JSON, *sorted((b.COCO_DIR / "images").glob("*.jpg")), *sorted(b.NATIVE_DIR.glob("2011_*"))]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    key = b.build(root)
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    return root, key


@needs_sources
def test_build_is_byte_for_byte_reproducible(built, tmp_path):
    root, key = built
    again = b.build(tmp_path)
    assert again == key
    for name in ("labelme_people_clean.zip", "labelme_people_errors.zip", "labelme_native_examples.zip"):
        assert (root / "archives" / name).read_bytes() == (tmp_path / "archives" / name).read_bytes()
    assert (root / "verification.json").read_bytes() == (tmp_path / "verification.json").read_bytes()


@needs_sources
def test_clean_files_carry_the_annotators_exact_numbers_as_polygons_only(built):
    root, key = built
    clean = members(root / "archives" / "labelme_people_clean.zip")
    source = json.loads(b.COCO_JSON.read_text(encoding="utf-8"))
    person_ids = {c["id"] for c in source["categories"] if c["name"] == "person"}
    images = {image["id"]: image for image in source["images"]}
    expected = {}
    for annotation in sorted(source["annotations"], key=lambda item: item["id"]):
        if annotation["category_id"] in person_ids:
            stem = Path(images[annotation["image_id"]]["file_name"]).stem
            expected.setdefault(stem, []).extend(annotation["segmentation"])
    cases = {case["file"]: case for case in key["cases"]}
    assert {case["people"] for case in cases.values()} == set(range(1, 10))  # the local photos have 1 to 9 people
    assert all(1 <= case["people"] <= b.MAX_PEOPLE for case in cases.values())
    for name, data in clean.items():
        stem = Path(name).stem
        if name.endswith(".jpg"):
            assert data == (b.COCO_DIR / "images" / name).read_bytes()
            continue
        doc = json.loads(data)
        assert doc["imageData"] is None and doc["imagePath"] == f"{stem}.jpg"
        # An object has a polygon or a box, never both: these files have polygons only.
        assert {shape["shape_type"] for shape in doc["shapes"]} == {"polygon"}
        flat = [[value for point in shape["points"] for value in point] for shape in doc["shapes"]]
        assert flat == expected[stem]
        assert [[type(v) for v in part] for part in flat] == [[type(v) for v in part] for part in expected[stem]]
        parsed = b.parse_labelme(doc).images[0]
        assert all(obj.bbox_origin == "derived_from_polygon" for obj in parsed.annotations)
        assert len(doc["shapes"]) == cases[name]["shapes"]


@needs_sources
def test_error_files_differ_from_clean_only_where_the_answer_key_says(built):
    root, key = built
    clean = members(root / "archives" / "labelme_people_clean.zip")
    errors = members(root / "archives" / "labelme_people_errors.zip")
    assert clean.keys() == errors.keys()
    injected = [case for case in key["cases"] if case["error"]]
    assert len(injected) == key["datasets"]["labelme_people_errors"]["errors_injected"] >= 12
    assert {case["error"] for case in injected} == set(b.ERROR_TYPES)
    for case in key["cases"]:
        before, after = json.loads(clean[case["file"]]), json.loads(errors[case["file"]])
        assert clean[case["image"]] == errors[case["image"]]
        if case["error"] is None:
            assert clean[case["file"]] == errors[case["file"]]
            continue
        index = int(case["source_shape_key"].rsplit("/", 1)[1])
        assert before["shapes"][index]["group_id"] is None
        box = b.bounds(before["shapes"][index]["points"])
        assert box[3] - box[1] >= b.MIN_HEIGHT and [round(v, 2) for v in box] == case["source_box"]
        if case["error"] == "annotation_removed":
            assert after["shapes"] == before["shapes"][:index] + before["shapes"][index + 1:]
            continue
        assert case["shape_key"] == case["source_shape_key"]
        others = lambda doc: [shape for position, shape in enumerate(doc["shapes"]) if position != index]
        assert others(after) == others(before)
        assert after["shapes"][index] != before["shapes"][index]
        assert b.structural_codes(after) == b.structural_codes(before)


@needs_sources
def test_native_examples_are_the_upstream_bytes_and_nothing_private_is_packed(built):
    root, _ = built
    native = members(root / "archives" / "labelme_native_examples.zip")
    assert native == {f"{stem}{suffix}": (b.NATIVE_DIR / f"{stem}{suffix}").read_bytes()
                      for stem in b.NATIVE_STEMS for suffix in (".json", ".jpg")}
    for name in ("labelme_people_clean.zip", "labelme_people_errors.zip", "labelme_native_examples.zip"):
        assert not any(Path(member).name in ("answer_key.json", "verification.json", "guidelines.txt", "results.json")
                       for member in members(root / "archives" / name))


@needs_sources
@pytest.mark.parametrize("destination", (b.COCO_DIR, b.COCO_DIR / "images", b.NATIVE_DIR))
def test_output_cannot_go_into_a_source_folder(destination):
    with pytest.raises(SystemExit, match="source dataset"):
        b.build(destination)


@needs_sources
@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="the managed importer needs POSIX file APIs")
@pytest.mark.parametrize("name,count", (("labelme_people_clean", 29), ("labelme_people_errors", 29),
                                        ("labelme_native_examples", 3)))
def test_real_importer_takes_every_file_with_its_exact_points(built, tmp_path, name, count):
    from backend.app.datasets.json_zip import prepare_sample, unpack_archive
    from backend.app.models.dataset_import import ImportPolicy, ImportRequest
    root, _ = built
    path = root / "archives" / f"{name}.zip"
    raw = path.read_bytes()
    request = ImportRequest(job_id="job_labelme_flow", dataset_id="dataset_labelme_flow", format="labelme",
                            archive_key=path.name, archive_sha256=hashlib.sha256(raw).hexdigest(),
                            archive_size_bytes=len(raw), policy=ImportPolicy())
    samples, unsupported = unpack_archive(path, tmp_path / "unpacked", request)
    assert len(samples) == count and not unsupported
    files = members(path)
    for ordinal, item in enumerate(samples):
        publication = prepare_sample(request.dataset_id, item, ordinal, source_root=tmp_path / "unpacked",
                                     asset_root=tmp_path / "assets", policy=request.policy)
        shapes = json.loads(files[item.annotation.name])["shapes"]
        assert [obj.source_key for obj in publication.sample.objects] == [f"/shapes/{i}" for i in range(len(shapes))]
        for obj, shape in zip(publication.sample.objects, shapes):
            assert obj.label == shape["label"]
            if shape["shape_type"] == "polygon":
                assert [[list(point) for point in part] for part in obj.polygons] == [shape["points"]]
                assert obj.bbox_origin == "derived_from_polygon"
