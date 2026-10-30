"""Demo, overlay and synthetic fixtures. Every fixture here is SYNTHETIC (invented)."""

from hashlib import sha256
import json
import os
from pathlib import Path

from PIL import Image
import pytest

from backend.app.datasets import preview_annotations as demo
from backend.app.datasets.annotation_validation import ImageInputError, image_path_for, inspect_document
from backend.app.datasets.coco import parse_coco, read_coco
from backend.app.datasets.labelme import read_labelme
from backend.app.datasets.synthetic_samples import coco_document, write_synthetic_dataset


@pytest.fixture()
def synthetic(tmp_path):
    return write_synthetic_dataset(tmp_path / "syn")


def snapshot(root: Path):
    return {str(p.relative_to(root)): sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def run(tmp_path, *args):
    return demo.main([*args, "--max-images", "0"])


def codes_by_file(report):
    result = {}
    for doc in report["documents"]:
        for image in doc["images"]:
            codes = {i["code"] for i in image["issues"]}
            codes |= {i["code"] for obj in image["objects"] for i in obj["issues"]}
            result.setdefault(image["file_name"], set()).update(codes)
    return result


def build(synthetic, **kwargs):
    jobs = [("coco", synthetic["coco_json"], synthetic["coco_images"]),
            ("labelme", synthetic["labelme_dir"], synthetic["labelme_dir"])]
    documents, unreadable, roots = [], [], {}
    for fmt, path, root in jobs:
        found, bad = demo.load_inputs(fmt, path)
        roots.update({id(d): root for d in found})
        documents += found
        unreadable += bad
    resolver = lambda d: roots[id(d)]  # noqa: E731
    return documents, demo.build_report(documents, unreadable, image_root=resolver, max_images=0, **kwargs), resolver


def test_synthetic_generator_refuses_a_non_empty_directory_and_marks_everything(tmp_path, synthetic):
    with pytest.raises(FileExistsError):
        write_synthetic_dataset(synthetic["root"])
    documents, report, _ = build(synthetic)
    assert report["documents"] and all(doc["synthetic"] for doc in report["documents"])
    assert "SYNTHETIC" in (synthetic["root"] / "README_SYNTHETIC.txt").read_text()


def test_each_problem_case_is_reported_and_none_is_repaired(synthetic):
    documents, report, _ = build(synthetic)
    found = codes_by_file(report)
    assert found["box_only.png"] == set() and found["lm_clean.png"] == set()
    assert "duplicate_annotation_id" in found["multipart.png"]
    assert "unsupported_rle" in found["rle_retained.png"]
    assert {"self_intersecting_polygon", "box_outside_image"} <= found["bad_geometry.png"]
    assert "missing_image" in found["missing_file.png"] and "missing_image" in found["does_not_exist.png"]
    assert "image_dimension_mismatch" in found["dimension_mismatch.png"]
    assert "invalid_box" in found["malformed_box.png"]
    assert "duplicate_image_reference" in found["empty.png"]
    assert "unsupported_shape" in found["lm_unsupported_shapes.png"]
    assert "polygon_outside_image" in found["lm_dimension_mismatch.png"]
    # Problem objects were kept, not discarded or fixed.
    coco_doc = report["documents"][0]
    assert coco_doc["object_count"] == 10 and coco_doc["unassigned_count"] == 1
    bad = next(i for i in coco_doc["images"] if i["file_name"] == "bad_geometry.png")
    assert bad["objects"][1]["box"]["value"] == [140, 100, 180, 130]  # still outside the image


def test_unreadable_files_are_separate_from_readable_annotations_with_mistakes(synthetic):
    _, report, _ = build(synthetic)
    assert [os.path.basename(u["path"]) for u in report["unreadable"]] == ["lm_unreadable.json"]
    assert len(report["documents"]) == 5  # one COCO + four readable LabelMe files


def test_box_only_and_polygon_only_samples_are_not_failures(synthetic):
    _, report, _ = build(synthetic)
    images = {i["file_name"]: i for i in report["documents"][0]["images"]}
    only_box = images["box_only.png"]["objects"][0]
    only_polygon = images["polygon_only.png"]["objects"][0]
    assert only_box["polygon"]["state"] == only_box["mask"]["state"] == "not_supplied" and not only_box["issues"]
    assert only_polygon["box"]["state"] == "derived" and only_polygon["polygon"]["state"] == "supplied"
    assert not only_polygon["issues"]


def test_running_the_demo_never_modifies_source_files(tmp_path, synthetic):
    before = snapshot(synthetic["root"])
    out = tmp_path / "overlays"
    assert run(tmp_path, "synthetic", str(tmp_path / "again"), "--out", str(out)) == 1
    assert demo.main(["labelme", str(synthetic["labelme_dir"]), "--out", str(out), "--max-images", "0"]) == 1
    assert snapshot(synthetic["root"]) == before


def test_overlay_marks_supplied_solid_and_derived_dashed_at_unscaled_coordinates(tmp_path, synthetic):
    documents, report, resolver = build(synthetic)
    out = tmp_path / "o"
    written = demo.write_overlays(documents, report, image_root=resolver, reference_base=None,
                                  out_dir=out, scale=1)
    box_path = written[1]["/images/0"]       # supplied box (20, 20, 70, 60)
    tri_path = written[1]["/images/1"]       # polygon only; derived box x=90.5..140, y=20..80
    box_img, tri_img = Image.open(box_path).convert("RGB"), Image.open(tri_path).convert("RGB")
    color = demo.PALETTE[0]
    # Solid supplied rectangle: every pixel along the left edge is the object colour.
    assert all(box_img.getpixel((20, y)) == color for y in range(22, 58))
    # Derived rectangle: dashed, so the same kind of edge has colour AND gaps.
    edge = [tri_img.getpixel((x, 20)) == color or tri_img.getpixel((x, 21)) == color for x in range(94, 138)]
    assert any(edge) and not all(edge)
    # Alignment: the polygon's own vertex is drawn where the source says it is.
    assert tri_img.getpixel((140, 30)) == color
    # Legend strip sits below the image; the image area keeps its original size.
    assert box_img.height > 120 and box_img.width >= demo.MIN_LEGEND_WIDTH


def test_overlay_does_not_rescale_coordinates_on_dimension_mismatch(tmp_path, synthetic):
    documents, report, resolver = build(synthetic)
    written = demo.write_overlays(documents, report, image_root=resolver, reference_base=None,
                                  out_dir=tmp_path, scale=1)
    path = written[1]["/images/6"]   # declared 200x100, decoded 160x120
    image = Image.open(path).convert("RGB")
    assert all(image.getpixel((10, y)) == demo.PALETTE[0] for y in range(12, 38))  # box left edge, unscaled


def test_missing_images_and_duplicate_file_names_get_distinct_outcomes(tmp_path, synthetic):
    documents, report, resolver = build(synthetic)
    written = demo.write_overlays(documents, report, image_root=resolver, reference_base=None,
                                  out_dir=tmp_path, scale=1)
    assert written[1]["/images/5"].startswith("skipped")
    assert written[1]["/images/8"] != written[1]["/images/9"]      # both empty.png records kept
    assert Path(written[1]["/images/8"]).exists() and Path(written[1]["/images/9"]).exists()


def test_text_report_names_original_vs_derived_and_unsupported(synthetic):
    documents, report, _ = build(synthetic)
    text = demo.render_text(report)
    for needle in ("SYNTHETIC FIXTURE", "[DERIVED, not in source]", "UNSUPPORTED (circle)",
                   "UNSUPPORTED (missing shape_type)", "retained_not_decoded (coco_rle)",
                   "UNREADABLE INPUT", "not annotation-quality verdicts"):
        assert needle in text


def test_json_output_is_strict_json(tmp_path, capsys, synthetic):
    code = demo.main(["labelme", str(synthetic["labelme_dir"]), "--json", "--no-overlay", "--max-images", "0"])
    data = json.loads(capsys.readouterr().out)
    assert code == 1 and data["inspection_only"] is True and data["unreadable"]


def test_exit_codes_distinguish_clean_diagnostics_and_unusable_input(tmp_path, synthetic, capsys):
    clean = synthetic["labelme_dir"] / "lm_clean.json"
    assert demo.main(["labelme", str(clean), "--out", str(tmp_path / "x"), "--max-images", "0"]) == 0
    assert demo.main(["labelme", str(synthetic["labelme_dir"] / "lm_unreadable.json"), "--no-overlay"]) == 2
    assert demo.main(["coco", str(tmp_path / "missing.json"), "--image-root", str(tmp_path), "--no-overlay"]) == 2
    assert demo.main(["coco", str(synthetic["coco_json"]), "--image-root", str(tmp_path / "nope")]) == 2
    assert demo.main(["labelme", str(clean), "--max-images", "-1"]) == 2


def test_detail_limit_is_stated_and_totals_cover_every_image(synthetic):
    documents, bad = demo.load_inputs("coco", synthetic["coco_json"])
    report = demo.build_report(documents, bad, image_root=synthetic["coco_images"], max_images=2)
    text = demo.render_text(report)
    assert "first 2 of 10 images" in text and report["totals"]["images"] == 10
    assert len(report["documents"][0]["images"]) == 2


def test_duplicate_image_reference_keeps_both_records_and_annotations():
    data = coco_document()
    result = parse_coco(data)
    flagged = [i for i in result.images if any(x.code == "duplicate_image_reference" for x in i.issues)]
    assert [i.source_id for i in flagged] == [9, 10]
    assert len(result.images) == 10 and data == result.source


def test_image_path_helper_matches_inspection_rules(tmp_path, synthetic):
    document = read_labelme(synthetic["labelme_dir"] / "lm_clean.json")
    path = image_path_for(document, "lm_clean.png", synthetic["labelme_dir"])
    assert path == (synthetic["labelme_dir"] / "lm_clean.png").resolve()
    for bad in ("../x.png", "C:\\x.png", "http://x/y.png", "/etc/passwd"):
        with pytest.raises(ImageInputError):
            image_path_for(document, bad, synthetic["labelme_dir"])


def test_inspection_works_where_unix_only_open_flags_do_not_exist(monkeypatch, synthetic):
    """Windows has no os.O_NOFOLLOW / os.O_NONBLOCK; the image open must not crash."""
    for name in ("O_NOFOLLOW", "O_NONBLOCK"):
        monkeypatch.delattr(os, name, raising=False)
    document = read_coco(synthetic["coco_json"])
    report = inspect_document(document, synthetic["coco_images"])
    assert report.images[0].availability == "available"
    assert next(i for i in report.images if i.file_name == "missing_file.png").availability == "unavailable"


def test_exclude_skips_companion_files_instead_of_calling_them_unreadable(synthetic):
    folder = synthetic["labelme_dir"]
    (folder / "lm_clean.validation.json").write_text('{"report": true}', encoding="utf-8")
    _, unreadable = demo.load_inputs("labelme", folder)
    assert "lm_clean.validation.json" in [os.path.basename(u["path"]) for u in unreadable]
    _, unreadable = demo.load_inputs("labelme", folder, exclude=("*.validation.json",))
    assert [os.path.basename(u["path"]) for u in unreadable] == ["lm_unreadable.json"]


def test_labelme_image_level_issues_do_not_print_a_blank_location(synthetic):
    _, report, _ = build(synthetic)
    text = demo.render_text(report)
    assert "image_dimension_mismatch (document):" in text and "image_dimension_mismatch :" not in text
