"""Mechanics of tools/compare_visual_audit.py using its --mock stand-in. SYNTHETIC data only.

These tests say nothing about a real model; they show the script builds samples exactly like the
application, compares v4 and v5, keeps expected outcomes out of requests, and refuses to overwrite output.
"""
import importlib.util
import json
import os
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

pytestmark = pytest.mark.skipif(not hasattr(os, 'O_NOFOLLOW'), reason='the managed importer needs POSIX file APIs')
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("compare_visual_audit", HERE.parent / "tools" / "compare_visual_audit.py")
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)

from backend.app.models.rule import Rule, Rulebook  # noqa: E402

W, H = 200, 300
MAN = [[110, 50], [160, 50], [165, 250], [105, 250]]
WOMAN = [[20, 60], [70, 60], [75, 260], [15, 260]]


def archive(path, shift):
    def shape(points):
        return {"label": "person", "shape_type": "polygon", "group_id": None, "flags": {}, "points": points}
    document = {"version": "synthetic", "flags": {}, "imagePath": "258421.jpg", "imageData": None,
                "imageWidth": W, "imageHeight": H,
                "shapes": [shape([[x + shift, y] for x, y in MAN]), shape(WOMAN)]}
    image = BytesIO()
    Image.new("RGB", (W, H), (100, 130, 100)).save(image, format="JPEG")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("data/258421.json", json.dumps(document))
        z.writestr("data/258421.jpg", image.getvalue())


@pytest.fixture()
def setup(tmp_path):
    archive(tmp_path / "baseline.zip", 0)
    archive(tmp_path / "altered.zip", 20)
    texts = {"RULE-2": "The bounding box includes the visible head.", "RULE-7": "The polygon follows the visible outline."}
    rules = [Rule(rule_id=k, description=v, check_name="annotation_guideline", origin="reviewer",
                  confirmation_state="confirmed") for k, v in texts.items()]
    rules.append(Rule(rule_id="RULE-6", description="Boxes may extend at most 3 pixels.", check_name=None,
                      origin="reviewer", confirmation_state="confirmed"))
    book = Rulebook(rulebook_id="book", version=3, guideline_text="\n".join(r.description for r in rules),
                    rules=rules, confirmation_state="confirmed")
    (tmp_path / "rulebook.json").write_text(book.model_dump_json())
    return tmp_path


def run(setup, *extra, out="report.json"):
    return compare.main(["--baseline-archive", str(setup / "baseline.zip"), "--altered-archive", str(setup / "altered.zip"),
                         "--sample-stem", "258421", "--rulebook", str(setup / "rulebook.json"),
                         "--polygon-fit-rule", "RULE-7", "--shifted-key", "/shapes/0", "--mock",
                         "--out", str(setup / out), *extra])


def test_mock_run_writes_a_report_that_is_labelled_not_model_evidence(setup):
    assert run(setup) == 0
    report = json.loads((setup / "report.json").read_text())
    assert report["mock_only_not_model_evidence"] is True
    assert report["rules_run"] == ["RULE-2", "RULE-7"]
    assert report["rules_not_run"] == [{"rule_id": "RULE-6", "reason": "not a confirmed annotation_guideline rule"}]
    assert set(report["results"]) == {"v4", "v5"}


def test_v5_skips_the_box_rule_without_inference_and_names_the_annotation_while_v3_asks_the_model(setup):
    run(setup, '--versions', 'v3,v5')
    results = json.loads((setup / "report.json").read_text())["results"]
    v3_box = next(r for r in results["v3"]["altered"] if r["rule_id"] == "RULE-2")
    v5_box = next(r for r in results["v5"]["altered"] if r["rule_id"] == "RULE-2")
    assert v3_box["outcome"] == "fail" and v3_box["model_verified"] is True      # v3: every rule goes to the model
    assert v5_box["outcome"] == "unavailable" and v5_box["model_verified"] is False
    assert v5_box["scope"]["unavailable"]["code"] == "supplied_box_not_available"
    fit = next(r for r in results["v5"]["altered"] if r["rule_id"] == "RULE-7")
    assert {x["object_key"]: x["outcome"] for x in fit["scope"]["results"]} == {"/shapes/0": "fail", "/shapes/1": "pass"}


def test_expected_outcomes_are_applied_only_after_the_fact(setup):
    run(setup)
    evaluation = json.loads((setup / "report.json").read_text())["evaluation"]
    assert evaluation["v5"]["altered_flagged_annotations_polygon_fit"] == ["/shapes/0"]
    assert evaluation["v5"]["altered_unchanged_annotation_flagged"] == []
    assert evaluation["v5"]["model_calls_skipped_as_unavailable_without_inference"] == 2
    assert evaluation["v4"]["altered_flagged_annotations_polygon_fit"] == ["/shapes/0"]


def test_a_single_version_can_be_run_and_existing_output_is_never_overwritten(setup):
    assert run(setup, "--versions", "v5", "--rules", "RULE-7") == 0
    assert list(json.loads((setup / "report.json").read_text())["results"]) == ["v5"]
    before = (setup / "report.json").read_bytes()
    assert run(setup) == 2
    assert (setup / "report.json").read_bytes() == before
    assert run(setup, "--versions", "v2", out="other.json") == 2


def test_imported_comparison_images_survive_a_symlinked_temporary_root(setup):
    real = setup / 'real-work'
    real.mkdir()
    alias = setup / 'work-alias'
    alias.symlink_to(real, target_is_directory=True)
    manifest, assets = compare.build_sample(setup / 'baseline.zip', '258421', alias)
    assert manifest.original_image.availability.value == 'available'
    assert manifest.annotation_text.availability.value == 'available'
    from backend.app.storage.workflow_repository import read_manifest_asset_file
    _, image = read_manifest_asset_file(manifest, 'original_image', assets)
    assert Image.open(BytesIO(image)).size == (W, H)


def test_missing_source_image_stops_comparison_instead_of_reporting_model_results(setup):
    with zipfile.ZipFile(setup / 'baseline.zip') as source:
        annotation = source.read('data/258421.json')
    missing = setup / 'missing-image.zip'
    with zipfile.ZipFile(missing, 'w') as target:
        target.writestr('data/258421.json', annotation)
    work = setup / 'missing-work'
    work.mkdir()
    with pytest.raises(SystemExit, match='unavailable source assets: original_image'):
        compare.build_sample(missing, '258421', work)


def test_model_service_failures_are_not_counted_as_eligibility_skips_or_clean_annotations():
    from copy import deepcopy
    from types import SimpleNamespace

    failed = dict(rule_id='RULE-7', outcome='unavailable', model_verified=False, duration_ms=0,
        scope=dict(outcome_source='no_model_call', unavailable=None, results=[]), visual_assessment=None)
    skipped = dict(rule_id='RULE-2', outcome='unavailable', model_verified=False, duration_ms=0,
        scope=dict(outcome_source='no_model_call',
                   unavailable=dict(code='supplied_box_not_available', detail='No supplied box.'), results=[]),
        visual_assessment=None)
    report = {'results': {'v5': {'baseline': [skipped, failed], 'altered': deepcopy([skipped, failed])}}}
    result = compare.evaluate(report, SimpleNamespace(polygon_fit_rule='RULE-7', shifted_key='/shapes/0'))['v5']
    assert result['model_calls_skipped_as_unavailable_without_inference'] == 2
    assert result['unverified_execution_count'] == 2
    assert result['altered_flagged_annotations_polygon_fit'] is None
    assert result['altered_unchanged_annotation_flagged'] is None
    assert result['unavailable_rules'] == {'baseline': ['RULE-2', 'RULE-7'], 'altered': ['RULE-2', 'RULE-7']}
