"""Demo, overlay and synthetic fixtures. Every fixture here is SYNTHETIC (invented)."""

from hashlib import sha256
from io import BytesIO
import json
import os
import time
from pathlib import Path

from PIL import Image
import pytest

from backend.app.datasets import overlay_render, preview_annotations as demo
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


def has_colour(image, xs, ys, colour):
    return any(image.getpixel((x, y)) == colour for x in xs for y in ys)


def test_overlay_marks_supplied_solid_and_derived_dashed_at_unscaled_coordinates(tmp_path, synthetic):
    documents, report, resolver = build(synthetic)
    written = demo.write_overlays(documents, report, image_root=resolver, reference_base=None,
                                  out_dir=tmp_path / "o", scale=1)
    box_img = Image.open(written[1]["/images/0"]["path"]).convert("RGB")    # supplied box (20, 20, 70, 60)
    tri_img = Image.open(written[1]["/images/1"]["path"]).convert("RGB")    # polygon only; derived box x 90.5..140, y 20..80
    colour = demo.PALETTE[0]
    # Solid supplied rectangle: along its whole left edge the object colour is present.
    assert all(has_colour(box_img, range(18, 23), [y], colour) for y in range(22, 58))
    # Derived rectangle: dashed, so the same kind of edge has colour AND gaps.
    top = [has_colour(tri_img, [x], range(18, 23), colour) for x in range(94, 138)]
    assert any(top) and not all(top)
    assert sum(top) / len(top) < 0.8
    # Alignment at scale 1: the polygon vertex (140, 30) is where the source says.
    assert has_colour(tri_img, range(137, 143), range(27, 33), colour)
    # The image panel keeps its size; the object list and legend sit outside it.
    assert box_img.width >= 160 + overlay_render.SIDEBAR_WIDTH and box_img.height > 120
    assert box_img.getpixel((165, 60)) == (255, 255, 255)

def test_overlay_does_not_rescale_coordinates_on_dimension_mismatch(tmp_path, synthetic):
    documents, report, resolver = build(synthetic)
    written = demo.write_overlays(documents, report, image_root=resolver, reference_base=None,
                                  out_dir=tmp_path, scale=1)
    result = written[1]["/images/6"]   # declared 200x100, decoded 160x120
    image = Image.open(result["path"]).convert("RGB")
    assert all(has_colour(image, range(8, 13), [y], demo.PALETTE[0]) for y in range(12, 38))  # box edge, unscaled
    assert result["canvas"][0] == image.width


def test_scale_comes_from_the_decoded_size_not_the_declared_size():
    scale, panel_w, panel_h = overlay_render.plan_scale(160, 120)      # decoded size
    assert (panel_w, panel_h) == (round(160 * scale), round(120 * scale))
    assert max(panel_w, panel_h) <= overlay_render.TARGET_LONG_SIDE + 1

def test_missing_images_and_duplicate_file_names_get_distinct_outcomes(tmp_path, synthetic):
    documents, report, resolver = build(synthetic)
    written = demo.write_overlays(documents, report, image_root=resolver, reference_base=None,
                                  out_dir=tmp_path, scale=1)
    assert written[1]["/images/5"]["status"] == "skipped" and "not available" in written[1]["/images/5"]["reason"]
    first, second = written[1]["/images/8"]["path"], written[1]["/images/9"]["path"]
    assert first != second and Path(first).exists() and Path(second).exists()   # both empty.png records kept

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


# ------------------------------------------------------------------ output safety

def one_image_dataset(tmp_path, annotations, name="a.png", size=(100, 80)):
    images = tmp_path / "images"
    images.mkdir(exist_ok=True)
    Image.new("RGB", size, (200, 200, 200)).save(images / name)
    document = {"images": [{"id": 1, "file_name": name, "width": size[0], "height": size[1]}],
                "categories": [{"id": 1, "name": "thing"}],
                "annotations": [dict(id=i + 1, image_id=1, category_id=1, iscrowd=0, **a)
                                for i, a in enumerate(annotations)]}
    path = tmp_path / "c.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path, images


def run_coco(path, images, out, *extra):
    return demo.main(["coco", str(path), "--image-root", str(images), "--out", str(out),
                      "--max-images", "0", *extra])


def test_existing_files_are_never_overwritten_and_the_new_overlay_gets_a_new_name(tmp_path):
    path, images = one_image_dataset(tmp_path, [{"bbox": [1, 1, 5, 5]}])
    out = tmp_path / "out"
    out.mkdir()
    (out / "01_001_a.overlay.png").write_bytes(b"KEEP ME")
    assert run_coco(path, images, out) == 0
    assert (out / "01_001_a.overlay.png").read_bytes() == b"KEEP ME"
    assert (out / "01_001_a.overlay-2.png").exists()
    assert run_coco(path, images, out) == 0           # a second run adds, never replaces
    assert (out / "01_001_a.overlay-3.png").exists()


def test_source_images_are_untouched_when_output_goes_into_the_image_folder(tmp_path):
    path, images = one_image_dataset(tmp_path, [{"bbox": [1, 1, 5, 5]}])
    (images / "01_001_a.overlay.png").write_bytes(b"A SOURCE IMAGE THAT HAS THE GENERATED NAME")
    before = snapshot(images)
    assert run_coco(path, images, images) == 0
    after = snapshot(images)
    assert all(after[name] == digest for name, digest in before.items())      # nothing replaced
    assert len(after) == len(before) + 1                                       # one new file added


def test_output_path_that_is_a_file_is_a_clean_error(tmp_path, capsys):
    path, images = one_image_dataset(tmp_path, [{"bbox": [1, 1, 5, 5]}])
    blocker = tmp_path / "out"
    blocker.write_text("not a folder")
    assert run_coco(path, images, blocker) == 2
    assert blocker.read_text() == "not a folder"


# ------------------------------------------------------------- invalid and extreme geometry

def test_negative_width_box_does_not_stop_the_preview(tmp_path, capsys):
    path, images = one_image_dataset(tmp_path, [{"bbox": [50, 10, -20, 30]}, {"bbox": [5, 5, 20, 20]}])
    out = tmp_path / "out"
    code = run_coco(path, images, out)
    text = capsys.readouterr().out
    assert code == 1 and "(preview #2)" in text and list(out.glob("*.overlay.png"))
    # The inverted box is drawn from its supplied corners and still carries its diagnostics.
    assert "box     : supplied" in text


def test_extreme_coordinates_are_reported_not_drawn_and_never_hang():
    objects = [
        overlay_render.OverlayObject(1, "huge", "1", box=(0, 0, 1e300, 5), box_origin="supplied"),
        overlay_render.OverlayObject(2, "poly", "2", polygons=[[(0, 0), (1e12, 0), (1e12, 1e12)]],
                                     box=(0, 0, 1e12, 1e12), box_origin="derived_from_polygon"),
        overlay_render.OverlayObject(3, "nan", "3", polygons=[[(0, 0), (float("nan"), 1), (5, 5)]]),
        overlay_render.OverlayObject(4, "ok", "4", box=(10, 10, 40, 40), box_origin="supplied"),
        overlay_render.OverlayObject(5, "big but allowed", "5", box=(-5e6, -5e6, 5e6, 5e6), box_origin="supplied"),
    ]
    png = BytesIO()
    Image.new("RGB", (100, 80)).save(png, format="PNG")
    started = time.monotonic()
    canvas, info = overlay_render.render_overlay(png.getvalue(), objects, scale=1)
    assert time.monotonic() - started < 5
    status = {o.number: o for o in info.outcomes}
    assert status[1].status == status[2].status == status[3].status == "not_drawn"
    assert any("preview limit" in n for n in status[1].notes + status[2].notes)
    assert any("non-finite" in n for n in status[3].notes)
    assert status[4].status == "drawn"
    assert status[5].status in ("outside_image", "drawn_clipped")      # clipped analytically, no long loop
    assert canvas.width * canvas.height < overlay_render.MAX_CANVAS_PIXELS


def test_geometry_that_cannot_be_drawn_is_listed_with_its_reason_in_the_text_report(tmp_path, capsys, synthetic):
    assert demo.main(["labelme", str(synthetic["labelme_dir"] / "lm_unsupported_shapes.json"),
                      "--out", str(tmp_path / "o"), "--max-images", "0"]) == 1
    text = capsys.readouterr().out
    assert "preview #1: not_drawn:" in text and "is not normalized by the readers" in text


def test_output_size_is_validated_before_a_canvas_is_allocated(tmp_path, monkeypatch, capsys):
    with pytest.raises(overlay_render.OverlayTooLarge):
        overlay_render.plan_scale(100_000, 100_000, requested=1)
    with pytest.raises(overlay_render.OverlayTooLarge):
        overlay_render.plan_scale(100, 80, requested=float("inf"))
    path, images = one_image_dataset(tmp_path, [{"bbox": [1, 1, 5, 5]}])
    created = []
    real_new = Image.new
    monkeypatch.setattr(overlay_render.Image, "new", lambda mode, size, *a, **k: created.append(size) or real_new(mode, size, *a, **k))
    assert run_coco(path, images, tmp_path / "out", "--scale", "100000") == 0     # skipped and reported, not crashed
    assert created == []                                                          # nothing was allocated
    assert "exceed the size limits" in capsys.readouterr().out
    assert not list((tmp_path / "out").glob("*.png"))


def test_one_failing_image_does_not_stop_the_others(tmp_path, monkeypatch, capsys):
    path, images = one_image_dataset(tmp_path, [{"bbox": [1, 1, 5, 5]}])
    calls = []
    real = overlay_render.render_overlay
    def flaky(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("boom")
    monkeypatch.setattr(demo, "render_overlay", flaky)
    assert run_coco(path, images, tmp_path / "out") == 0
    assert "preview failed for this image (RuntimeError: boom)" in capsys.readouterr().out


# ------------------------------------------------------------- identity, data and labels

def test_numbers_are_preview_numbers_and_source_identifiers_are_kept(synthetic):
    documents, report, _ = build(synthetic)
    record, image = documents[0].images[2], report["documents"][0]["images"][2]   # two objects with source id 3
    items = demo.overlay_objects(record, image)
    assert [i.number for i in items] == [1, 2] and [i.ident for i in items] == ["3", "3"]
    lm = demo.overlay_objects(documents[1].images[0], report["documents"][1]["images"][0])
    assert [i.ident for i in lm] == ["/shapes/0 group 1", "/shapes/1 group 1", "/shapes/2"]


def test_drawing_does_not_change_annotation_data(tmp_path, synthetic):
    from copy import deepcopy
    documents, report, resolver = build(synthetic)
    before = [deepcopy((o.bbox, o.bbox_origin, o.polygons, o.source, o.issues))
              for d in documents for r in d.images for o in r.annotations]
    demo.write_overlays(documents, report, image_root=resolver, reference_base=None, out_dir=tmp_path, scale=None)
    after = [(o.bbox, o.bbox_origin, o.polygons, o.source, o.issues)
             for d in documents for r in d.images for o in r.annotations]
    assert before == after


def test_badges_in_a_crowded_spot_do_not_overlap_each_other():
    occupied, panel = [], (900, 700)
    for _ in range(40):                                  # 40 objects whose boxes start at the same corner
        rect = overlay_render._place_badge((26, 22), (300, 300, 340, 360), occupied, panel)
        assert all(overlay_render._overlap(rect, other) == 0 for other in occupied)
        occupied.append(rect)
    assert all(0 <= r[0] and 0 <= r[1] and r[2] <= panel[0] and r[3] <= panel[1] for r in occupied)


def test_each_object_keeps_one_colour_in_lines_badge_and_list():
    colours = [overlay_render.color_for(n) for n in range(1, 40)]
    assert colours[0] == colours[len(overlay_render.PALETTE)]          # consistent wrap-around
    assert len(set(colours[:len(overlay_render.PALETTE)])) == len(overlay_render.PALETTE)


def test_legend_text_is_wrapped_to_the_available_width():
    font = overlay_render._font(13)
    long_text = "word " * 80 + "x" * 300
    lines = overlay_render.wrap_text(long_text, font, 300)
    assert len(lines) > 3 and all(font.getlength(line) <= 300 for line in lines)
