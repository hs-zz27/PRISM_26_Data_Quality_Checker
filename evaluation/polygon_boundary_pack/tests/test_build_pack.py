"""Dataset integrity checks; no application services or model inference."""

import copy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import zipfile

import pytest

SPEC = importlib.util.spec_from_file_location("boundary_pack", Path(__file__).resolve().parents[1] / "build_pack.py")
b = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(b)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("boundary-pack")
    paths = [b.REPO / s[k] for s in b.SOURCES for k in ("annotation", "image")]
    before = {p: p.read_bytes() for p in paths}
    key = b.build(root)
    assert all(p.read_bytes() == data for p, data in before.items())
    return root, key


def members(path):
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


def test_coverage_and_review_state(built):
    root, key = built
    cases = [c for c in key["cases"] if not c.get("control")]
    assert len(cases) == 42
    for fmt in ("coco", "labelme"):
        records = [c for c in cases if c["format"] == fmt]
        assert {(tuple(c["regions"]), c["edits"][0]["depth_px"]) for c in records} == {
            (regions, depth) for regions in b.COMBINATIONS for depth in b.DEPTHS}
    assert all(c["human_review_status"] == "pending" and c["confirmed_result"] is None for c in key["cases"])
    assert len(list((root / "comparisons").glob("*.png"))) == 42


def test_native_records_and_photo_bytes_preserved(built):
    root, _ = built
    for spec in b.SOURCES:
        source, photo = b.source_input(spec)
        baseline = members(root / "archives" / f"{spec['format']}_baseline_21.zip")
        changed = members(root / "archives" / f"{spec['format']}_errors_21.zip")
        assert baseline.keys() == changed.keys()
        assert len(baseline) == 42
        for name in baseline:
            if name.endswith(".jpg"):
                assert baseline[name] == changed[name] == photo
                continue
            assert json.loads(baseline[name]) == source
            altered = json.loads(changed[name])
            target = b.target(altered, spec)
            # Undo only the intended geometry/area fields, then demand exact
            # equality of all native metadata, IDs, labels and unrelated objects.
            original_target = b.target(source, spec)
            fields = ("segmentation", "area") if spec["format"] == "coco" else ("points",)
            for field in fields:
                target[field] = copy.deepcopy(original_target[field])
            assert altered == source


def test_every_perturbation_removes_only_local_area(built):
    _, key = built
    for case in key["cases"]:
        if case.get("control"):
            continue
        spec = next(s for s in b.SOURCES if s["format"] == case["format"])
        source, _ = b.source_input(spec)
        altered, edits, percent = b.altered_copy(source, spec, case["regions"], case["edits"][0]["depth_px"])
        assert 0 < percent < 3
        assert math.isclose(percent, case["removed_polygon_area_percent"], abs_tol=1e-9)
        assert all(e["removed_area_px2"] > 0 for e in edits)
        original_parts = b.parts_of(b.target(source, spec), spec["format"])
        altered_parts = b.parts_of(b.target(altered, spec), spec["format"])
        assert math.isclose(sum(e["removed_area_px2"] for e in edits),
                            sum(b.area(p) for p in original_parts) - sum(b.area(p) for p in altered_parts), abs_tol=1e-7)
        for edit in edits:
            if edit["operation"] == "inset_convex_vertex":
                assert math.isclose(math.dist(edit["before"], edit["after"]), edit["depth_px"], abs_tol=1e-9)
            else:
                assert math.isclose(edit["cut_line_y"] - min(p[1] for p in original_parts[0]), edit["depth_px"])
                assert all(p[1] >= edit["cut_line_y"] for p in altered_parts[0])


def test_no_added_polygon_coverage_at_subpixel_scale(built):
    """Independent raster check catches outward edits and combined-edit mistakes."""
    from PIL import Image, ImageChops, ImageDraw
    for spec in b.SOURCES:
        source, _ = b.source_input(spec)
        base = b.parts_of(b.target(source, spec), spec["format"])
        flat = [p for part in base for p in part]
        left, top = min(p[0] for p in flat), min(p[1] for p in flat)
        factor = 4
        size = (math.ceil((max(p[0] for p in flat) - left) * factor) + 4,
                math.ceil((max(p[1] for p in flat) - top) * factor) + 4)

        def mask(parts):
            image = Image.new("L", size)
            draw = ImageDraw.Draw(image)
            for polygon in parts:
                draw.polygon([((x - left) * factor + 1, (y - top) * factor + 1) for x, y in polygon], fill=255)
            return image

        original = mask(base)
        for depth in b.DEPTHS:
            for regions in b.COMBINATIONS:
                altered, _, _ = b.altered_copy(source, spec, regions, depth)
                changed = mask(b.parts_of(b.target(altered, spec), spec["format"]))
                # Rasterisers can differ by a boundary pixel even for a subset.
                # Every added pixel must be on the original polygon boundary.
                from PIL import ImageFilter
                allowance = original.filter(ImageFilter.MaxFilter(3))
                assert ImageChops.subtract(changed, allowance).getbbox() is None
                assert ImageChops.subtract(original, changed).getbbox() is not None


def test_mixed_uploads_have_control_and_all_four_pixel_combinations(built):
    root, _ = built
    for spec in b.SOURCES:
        archive = members(root / "archives" / f"{spec['format']}_mixed_8.zip")
        folders = {name.split("/")[0] for name in archive}
        assert folders == {spec["prefix"] + f"{i:03}" for i in (0, *range(8, 15))}
        source, _ = b.source_input(spec)
        control = next(data for name, data in archive.items() if name.startswith(spec["prefix"] + "000/") and name.endswith(".json"))
        assert json.loads(control) == source
        assert not any(Path(name).name in ("answer_key.json", "guidelines.txt", "verification.json") for name in archive)


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="Managed importer requires POSIX file APIs")
@pytest.mark.parametrize("fmt", ("coco", "labelme"))
@pytest.mark.parametrize("group,count", (("baseline_21", 21), ("errors_21", 21), ("mixed_8", 8)))
def test_real_importer_resolves_every_sample(built, tmp_path, fmt, group, count):
    from backend.app.datasets.annotation_validation import inspect_document
    from backend.app.datasets.json_zip import unpack_archive
    from backend.app.datasets.coco import read_coco
    from backend.app.datasets.labelme import read_labelme
    from backend.app.models.dataset_import import ImportPolicy, ImportRequest
    root, _ = built
    name = f"{fmt}_{group}.zip"
    path = root / "archives" / name
    raw = path.read_bytes()
    request = ImportRequest(job_id="job_boundary", dataset_id="dataset_boundary", format=fmt,
                            archive_key=name, archive_sha256=hashlib.sha256(raw).hexdigest(),
                            archive_size_bytes=len(raw), policy=ImportPolicy())
    samples, unsupported = unpack_archive(path, tmp_path / "unpacked", request)
    assert len(samples) == count and not unsupported
    assert len({s.sample_id for s in samples}) == count
    for sample in samples:
        assert sample.image is not None and sample.image.is_file()
        document = (read_coco if fmt == "coco" else read_labelme)(sample.annotation)
        report = inspect_document(document, sample.image.parent)
        assert not report.all_issues


@pytest.mark.parametrize("destination", (b.REPO, b.REPO / "datasets", b.REPO / "PennFudanPed", b.REPO / "evaluation" / "annotation_error_pack"))
def test_output_cannot_replace_sources_or_original_pack(destination):
    with pytest.raises(ValueError, match="must not overwrite"):
        b.build(destination)
