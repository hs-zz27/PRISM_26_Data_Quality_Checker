"""Tests for the annotation evaluation pack builder.

Run from the repository root:
    python -B -m pytest evaluation/annotation_error_pack/tests -q -p no:cacheprovider

The real-importer test runs only where the importer's POSIX file APIs exist (Linux/WSL/Docker).
"""

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_pack", HERE / "build_pack.py")
bp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bp)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("pack")
    before = bp.snapshot(bp.PROTECTED_ROOTS)
    bp.build(out)
    return out, before


def members(path):
    with zipfile.ZipFile(path) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


# ---------------------------------------------------------------- end-to-end build

def test_full_verification_passes_and_sources_are_untouched(built):
    out, before = built
    report = bp.verify(out, source_before=before)
    assert report["passed"], report["problems"]
    assert len(report["checks"]) >= 90


def test_two_builds_are_byte_identical(built, tmp_path):
    out, _ = built
    bp.build(tmp_path)
    first = sorted(p.relative_to(out) for p in out.rglob("*") if p.is_file())
    second = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file())
    assert first == second and len(first) == 13
    assert all((out / p).read_bytes() == (tmp_path / p).read_bytes() for p in first)


def test_check_uses_custom_output_and_detects_changed_files(built, tmp_path, capsys):
    out = tmp_path / "custom-output"
    shutil.copytree(built[0], out)
    assert bp.main(["--out", str(out), "--check"]) == 0
    assert str(out) in capsys.readouterr().out

    (out / "answer_key.json").write_text("{}\n", encoding="utf-8")
    assert bp.main(["--out", str(out), "--check"]) == 1
    report = capsys.readouterr().out
    assert str(out) in report and "answer_key.json" in report


def test_archive_contents_exactly_match_the_task(built):
    out, _ = built
    coco = members(out / "archives" / "coco_baseline_5.zip")
    labelme = members(out / "archives" / "labelme_errors_3.zip")
    assert sorted(coco) == ["annotations/instances.json"] + sorted(f"images/{c['file']}" for c in bp.COCO_CASES)
    assert sorted(labelme) == sorted([c["file"] for c in bp.LABELME_CASES] + [c["image"] for c in bp.LABELME_CASES])
    document = json.loads(coco["annotations/instances.json"])
    assert len(document["images"]) == 5
    assert len(document["categories"]) == 80                           # source definitions preserved
    assert all(name.endswith((".json", ".jpg")) for name in {**coco, **labelme})


def test_every_altered_case_changes_one_field_of_one_object(built):
    out, _ = built
    base = json.loads(members(out / "archives" / "coco_baseline_5.zip")["annotations/instances.json"])
    alt = json.loads(members(out / "archives" / "coco_errors_5.zip")["annotations/instances.json"])
    changed = {}
    for path in bp.json_diff(base, alt):
        parts = path.split("/")
        changed.setdefault(int(parts[2]), set()).add(parts[3])
    targets = {[a["id"] for a in base["annotations"]].index(c["annotation_id"]): c for c in bp.COCO_CASES}
    assert set(changed) == set(targets)
    for index, fields in changed.items():
        assert len(fields) == 1
    lm_base = members(out / "archives" / "labelme_baseline_3.zip")
    lm_alt = members(out / "archives" / "labelme_errors_3.zip")
    for case in bp.LABELME_CASES:
        diffs = bp.json_diff(json.loads(lm_base[case["file"]]), json.loads(lm_alt[case["file"]]))
        if case["status"] == "blocked":
            assert diffs == [] and lm_base[case["file"]] == lm_alt[case["file"]]
        else:
            assert {"/".join(p.split("/")[:4]) for p in diffs} == {
                f"/shapes/{case['shape']}/{'label' if case['edit']['op'] == 'relabel' else 'points'}"}


def test_exact_transformations(built):
    out, _ = built
    alt = json.loads(members(out / "archives" / "coco_errors_5.zip")["annotations/instances.json"])
    by_id = {a["id"]: a for a in alt["annotations"]}
    assert by_id[424921]["bbox"] == [143.5, 34.61, 248.25, 452.304]          # C01 height x 0.8
    assert by_id[1234574]["bbox"] == [115.16, 197.812, 83.23, 182.728]       # C02 y + 0.2h, h x 0.8
    assert by_id[495199]["bbox"] == [339.4, 87.84, 155.77, 356.52]           # C03 top edge up 59.42
    assert by_id[494051]["category_id"] == 3                                  # C04 car id from source
    source = json.loads(bp.text_bytes(bp.COCO_JSON))
    original = next(a for a in source["annotations"] if a["id"] == 430467)["segmentation"][0]
    moved = by_id[430467]["segmentation"][0]
    assert len(moved) == len(original)
    assert all(Decimal(repr(m)) - Decimal(repr(o)) == 64 for m, o in zip(moved[0::2], original[0::2]))
    assert moved[1::2] == original[1::2]
    lm = json.loads(members(out / "archives" / "labelme_errors_3.zip")["2011_000006.json"])
    src = json.loads(bp.text_bytes(bp.LABELME_ROOT / "2011_000006.json"))
    assert [p[0] for p in lm["shapes"][2]["points"]] == [p[0] for p in src["shapes"][2]["points"]]
    assert all(Decimal(repr(a[1])) - Decimal(repr(b[1])) == Decimal("37.5")
               for a, b in zip(lm["shapes"][2]["points"], src["shapes"][2]["points"]))


def test_image_bytes_identical_within_pairs_and_to_source(built):
    out, _ = built
    for fmt in ("coco", "labelme"):
        base = members(out / "archives" / bp.ARCHIVES[(fmt, "baseline")])
        alt = members(out / "archives" / bp.ARCHIVES[(fmt, "altered")])
        for name in (n for n in base if n.endswith(".jpg")):
            assert base[name] == alt[name]
            assert hashlib.sha256(base[name]).hexdigest() == bp.IMAGE_SHA256[Path(name).name]


def test_packaged_archives_parse_with_the_project_readers(built):
    out, _ = built
    coco = bp.inspect_archive("coco", members(out / "archives" / "coco_errors_5.zip"))
    labelme = bp.inspect_archive("labelme", members(out / "archives" / "labelme_errors_3.zip"))
    assert all(a == "available" for r in coco.values() for _, a, _, _ in r["images"])
    assert len(next(iter(coco.values()))["images"]) == 5
    assert len(labelme) == 3 and all(r["images"][0][1] == "available" for r in labelme.values())


def test_answer_key_structure(built):
    out, _ = built
    key = json.loads((out / "answer_key.json").read_text(encoding="utf-8"))
    entries = {e["case_id"]: e for e in key["entries"]}
    assert len(entries) == 16
    for case_id, entry in entries.items():
        assert entries[entry["paired_case_id"]]["paired_case_id"] == case_id
        assert entry["whole_image_reviewed"] is False
        if entry["variant"] == "baseline":
            assert entry["edited_field"] is None
    assert all(e['status'] == 'ready' for e in entries.values())
    assert entries['L01-altered']['image_file'] == '258421.jpg'
    assert entries['L01-altered']['source_json_pointer'] == '/shapes/0'
    assert entries['L01-altered']['transformation']['pixel_displacement'] == 76.8
    assert entries['L01-altered']['visual_verification'] == bp.REVIEW['L01']['visual_verification']
    assert entries["C04-altered"]["current_target_label"] == "car"
    assert entries["C03-altered"]["guideline_assessment"]["9"]["status"] == "not_satisfied"
    archive_names = [n for z in (out / "archives").glob("*.zip") for n in members(z)]
    assert "answer_key.json" not in [Path(n).name for n in archive_names]


# ---------------------------------------------------------------- refusal paths

def test_refuses_to_write_inside_or_above_source_datasets(tmp_path):
    with pytest.raises(bp.PackError, match="source dataset"):
        bp.check_output_dir(bp.REPO / "datasets" / "coco_people_30" / "out")
    with pytest.raises(bp.PackError, match="source dataset"):
        bp.check_output_dir(bp.REPO)
    assert bp.check_output_dir(tmp_path) == tmp_path.resolve()


def test_missing_source_file_stops_with_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(bp, "COCO_JSON", tmp_path / "missing.json")
    with pytest.raises(bp.PackError, match="Missing source file"):
        bp.load_sources()


def test_changed_source_bytes_stop_the_build(monkeypatch):
    monkeypatch.setitem(bp.IMAGE_SHA256, "000000199055.jpg", "0" * 64)
    with pytest.raises(bp.PackError, match="000000199055.jpg differs"):
        bp.load_sources()


def test_unexpected_target_values_stop_the_build(monkeypatch):
    source, images, _ = bp.load_sources()
    cases = copy.deepcopy(bp.COCO_CASES)
    cases[0]["expect"]["bbox"] = [1, 2, 3, 4]
    monkeypatch.setattr(bp, "COCO_CASES", cases)
    with pytest.raises(bp.PackError, match="C01: bbox"):
        bp.build_coco(source, images)


def test_wrong_annotation_identifier_stops_the_build(monkeypatch):
    source, images, _ = bp.load_sources()
    cases = copy.deepcopy(bp.COCO_CASES)
    cases[1]["annotation_id"] = 999999999
    monkeypatch.setattr(bp, "COCO_CASES", cases)
    with pytest.raises(bp.PackError, match="C02"):
        bp.build_coco(source, images)


def test_l01_has_a_distinct_pinned_source_and_one_exact_horizontal_edit(built):
    out, _ = built
    base_members = members(out / 'archives' / bp.ARCHIVES[('labelme', 'baseline')])
    alt_members = members(out / 'archives' / bp.ARCHIVES[('labelme', 'altered')])
    case = next(c for c in bp.LABELME_CASES if c['case'] == 'L01')
    assert case['image'] not in {'2011_000003.jpg', '2011_000006.jpg', '2011_000025.jpg'}
    assert '2011_000025.json' not in base_members and '2011_000025.jpg' not in base_members
    assert base_members[case['file']] == bp.text_bytes(bp.L01_ROOT / case['file'])
    assert base_members[case['image']] == alt_members[case['image']] == (bp.L01_ROOT / case['image']).read_bytes()
    base, alt = (json.loads(m[case['file']]) for m in (base_members, alt_members))
    before, after = (d['shapes'][case['shape']]['points'] for d in (base, alt))
    assert len(before) == len(after) == 27
    assert all(Decimal(str(a[0])) - Decimal(str(b[0])) == Decimal('76.8') for b, a in zip(before, after))
    assert [p[1] for p in before] == [p[1] for p in after]
    assert all(0 <= x <= 768 and 0 <= y <= 1152 for x, y in after)
    alt['shapes'][case['shape']]['points'] = copy.deepcopy(before)
    assert alt == base


def test_changed_l01_manifest_hash_is_rejected(monkeypatch, tmp_path):
    manifest = json.loads(bp.L01_MANIFEST.read_text())
    manifest['files'][0]['sha256'] = '0' * 64
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(bp, 'L01_MANIFEST', path)
    with pytest.raises(bp.PackError, match='258421.jpg differs'):
        bp.load_sources()


def test_l01_shape_identity_change_is_rejected():
    _, _, labelme = bp.load_sources()
    raw, image = labelme['258421.json']
    document = json.loads(raw)
    document['shapes'][0]['label'] = 'car'
    labelme = dict(labelme, **{'258421.json': (bp.labelme_dumps(document), image)})
    with pytest.raises(bp.PackError, match='expected person polygon'):
        bp.build_labelme(labelme)


def test_l01_caption_uses_width_and_l02_still_uses_height(monkeypatch, tmp_path):
    coco_source, images, labelme = bp.load_sources()
    _, cr, cb, ca = bp.build_coco(coco_source, images)
    _, lr = bp.build_labelme(labelme)
    captions = {}
    def capture(path, title, lines, *args, **kwargs):
        captions[path.stem] = '\n'.join(lines)
    monkeypatch.setattr(bp, 'render_comparison', capture)
    bp.render_all(tmp_path, cr, lr, cb, ca, images)
    assert '76.8 px (10% of image width)' in captions['L01']
    assert '37.5 px (10% of image height)' in captions['L02']


def test_committed_answer_key_matches_builder(built):
    assert (built[0] / 'answer_key.json').read_bytes() == (HERE / 'answer_key.json').read_bytes()


def test_shift_that_would_leave_the_image_is_refused():
    case = {"case": "X", "edit": {"op": "shift_polygon", "part": 0, "fraction": "0.1", "direction": "left"}}
    annotation = {"segmentation": [[10, 10, 50, 10, 50, 50]], "bbox": [10, 10, 40, 40]}
    with pytest.raises(bp.PackError, match="outside the image"):
        bp.coco_edit(case, annotation, {"person": 1, "car": 3}, (200, 100))


def test_extension_rules_for_every_edge_and_minimum():
    ann = lambda: {"bbox": [50, 40, 100, 60]}           # noqa: E731
    for edge, expected in {"top": [50, 28, 100, 72], "bottom": [50, 40, 100, 72],
                           "left": [30, 40, 120, 60], "right": [50, 40, 120, 60]}.items():
        annotation = ann()
        case = {"case": "X", "edit": {"op": "extend_edge", "edge": edge, "fraction": "0.2", "minimum_px": "10"}}
        bp.coco_edit(case, annotation, {"person": 1, "car": 3}, (400, 300))
        assert annotation["bbox"] == expected
    small = {"case": "X", "edit": {"op": "extend_edge", "edge": "top", "fraction": "0.2", "minimum_px": "10"}}
    with pytest.raises(bp.PackError, match="minimum"):
        bp.coco_edit(small, {"bbox": [50, 40, 100, 20]}, {"person": 1, "car": 3}, (400, 300))
    outside = {"case": "X", "edit": {"op": "extend_edge", "edge": "top", "fraction": "0.2", "minimum_px": "10"}}
    with pytest.raises(bp.PackError, match="leave the image"):
        bp.coco_edit(outside, {"bbox": [50, 5, 100, 60]}, {"person": 1, "car": 3}, (400, 300))


def test_relabel_uses_the_source_category_ids_not_assumed_ones():
    categories = bp.coco_category_ids({"categories": [{"id": 7, "name": "person"}, {"id": 42, "name": "car"}]})
    annotation = {"category_id": 7}
    case = {"case": "X", "edit": {"op": "relabel", "from": "person", "to": "car"}}
    assert bp.coco_edit(case, annotation, categories, (10, 10))[:3] == ("category_id", 7, 42)
    with pytest.raises(bp.PackError, match="exactly one 'car'"):
        bp.coco_category_ids({"categories": [{"id": 1, "name": "person"}, {"id": 3, "name": "car"},
                                             {"id": 9, "name": "car"}]})


def test_decimal_arithmetic_has_no_float_noise():
    assert bp.num(bp.dec(565.38) * Decimal("0.8")) == 452.304
    assert bp.num(bp.dec(640) * Decimal("0.1")) == 64 and isinstance(bp.num(Decimal("64.0")), int)


def test_importer_candidate_rules():
    names = {"annotations/instances.json", "images/a.jpg"}
    assert bp.importer_candidates("a.jpg", "annotations/instances.json", names, "coco") == {"images/a.jpg"}
    ambiguous = names | {"a.jpg"}
    assert len(bp.importer_candidates("a.jpg", "annotations/instances.json", ambiguous, "coco")) == 2
    assert bp.importer_candidates("x.jpg", "x.json", {"x.json", "x.jpg"}, "labelme") == {"x.jpg"}
    assert bp.importer_candidates("x.jpg", "sub/x.json", {"sub/x.json", "x.jpg"}, "labelme") == set()


# ---------------------------------------------------------------- the real importer (POSIX only)

@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="the managed importer needs POSIX file APIs")
@pytest.mark.parametrize("name,fmt,count", [("coco_baseline_5.zip", "coco", 5), ("coco_errors_5.zip", "coco", 5),
                                            ("labelme_baseline_3.zip", "labelme", 3),
                                            ("labelme_errors_3.zip", "labelme", 3)])
def test_the_real_importer_accepts_each_archive(built, tmp_path, name, fmt, count):
    import sys
    sys.path.insert(0, str(bp.REPO))
    from backend.app.datasets.json_zip import unpack_archive
    from backend.app.models.dataset_import import ImportPolicy, ImportRequest
    path = built[0] / "archives" / name
    data = path.read_bytes()
    request = ImportRequest(job_id="job_eval_pack", dataset_id="dataset_eval_pack", format=fmt,
                            archive_key=name, archive_sha256=hashlib.sha256(data).hexdigest(),
                            archive_size_bytes=len(data), policy=ImportPolicy())
    samples, unsupported = unpack_archive(path, tmp_path / "unpacked", request)
    assert len(samples) == count and not unsupported
    assert all(sample.image is not None for sample in samples)
