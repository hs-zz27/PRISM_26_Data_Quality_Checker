"""person_coverage decisions, with a fake segmenter (no Torch, no weights)."""
from io import BytesIO
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PIL import Image

from backend.app.checks.registry import RuleParameterError, validate_check_parameters
from backend.app.models.finding import FindingOutcome
from backend.app.models.rule import Rulebook
from backend.app.services import person_coverage as pc
from backend.app.services.evaluation import RuleEvaluationResult

H, W = 400, 300
PARAMS = validate_check_parameters("person_coverage", {})


def person_shape():
    """A standing 'person': head disc on a body rectangle, rows 50..349, so 300 px tall."""
    mask = np.zeros((H, W), np.uint8)
    cv2.circle(mask, (150, 80), 30, 1, -1)       # head: rows 50..110
    cv2.rectangle(mask, (110, 105), (190, 349), 1, -1)
    return mask.astype(bool)


def segmentation(*masks, carried=()):
    people = [SimpleNamespace(mask=m, box=pc._extent(m), score=0.99) for m in masks]
    return SimpleNamespace(people=people, carried=list(carried))


PERSON = person_shape()
SEG = segmentation(PERSON)
X0, Y0, X1, Y1 = pc._extent(PERSON)


def test_parameters_default_and_validation():
    assert (PARAMS.min_tolerance_px, PARAMS.tolerance_fraction) == (8, 0.04)
    assert validate_check_parameters("person_coverage", {"min_tolerance_px": 5, "tolerance_fraction": 0.1}).min_tolerance_px == 5
    for bad in ({"min_tolerance_px": -1}, {"tolerance_fraction": 2}, {"tolerance": 3}):
        with pytest.raises(RuleParameterError):
            validate_check_parameters("person_coverage", bad)


def test_correct_box_passes_and_small_change_is_within_tolerance():
    assert pc.judge_box((X0, Y0, X1, Y1), SEG, PARAMS)[0] is FindingOutcome.PASS
    assert pc.judge_box((X0 - 3, Y0 + 3, X1 + 3, Y1 - 3), SEG, PARAMS)[0] is FindingOutcome.PASS


@pytest.mark.parametrize("box, kind, side", [
    ((X0, Y0 + 45, X1, Y1), "missing", "top"),       # head left out
    ((X0, Y0, X1, Y1 - 30), "missing", "bottom"),    # feet left out
    ((X0 - 30, Y0, X1, Y1), "extra", "left"),        # background on the left
])
def test_box_errors_fail_and_say_where(box, kind, side):
    outcome, result = pc.judge_box(box, SEG, PARAMS)
    assert outcome is FindingOutcome.FAIL
    assert [(r["kind"], r["side"]) for r in result["regions"]] == [(kind, side)]
    assert result["tolerance_px"] == 12.0  # max(8 px, 4% of a 300 px person)


def test_mask_errors_fail_and_say_where():
    rows = np.arange(H)[:, None]
    head_cut = PERSON & (rows >= Y0 + 45)
    feet_cut = PERSON & (rows <= Y1 - 30)
    grown = cv2.dilate(PERSON.astype(np.uint8), np.ones((41, 41), np.uint8)) > 0
    hand = PERSON.copy()
    hand[200:260, 110:135] = False  # a missed part along the left side
    expected = {"head": (head_cut, ("missing", "top")), "feet": (feet_cut, ("missing", "bottom")),
                "hand": (hand, ("missing", "left"))}
    for name, (mask, where) in expected.items():
        outcome, result = pc.judge_region(mask, SEG, PARAMS, "mask")
        assert outcome is FindingOutcome.FAIL, name
        assert where in [(r["kind"], r["side"]) for r in result["regions"]], name
    outcome, result = pc.judge_region(grown, SEG, PARAMS, "mask")
    assert outcome is FindingOutcome.FAIL and {r["kind"] for r in result["regions"]} == {"extra"}


def test_correct_and_slightly_eroded_masks_pass():
    eroded = cv2.erode(PERSON.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    assert pc.judge_region(PERSON, SEG, PARAMS, "mask")[0] is FindingOutcome.PASS
    assert pc.judge_region(eroded, SEG, PARAMS, "polygon")[0] is FindingOutcome.PASS


def test_carried_object_may_be_included_but_never_replaces_the_person():
    bag = np.zeros((H, W), bool)
    bag[200:280, 185:240] = True  # touching the right side of the body
    with_bag = PERSON | bag
    assert pc.judge_region(with_bag, segmentation(PERSON), PARAMS, "mask")[0] is FindingOutcome.FAIL
    assert pc.judge_region(with_bag, segmentation(PERSON, carried=[bag]), PARAMS, "mask")[0] is FindingOutcome.PASS
    rows = np.arange(H)[:, None]
    head_cut_with_bag = (PERSON & (rows >= Y0 + 45)) | bag
    assert pc.judge_region(head_cut_with_bag, segmentation(PERSON, carried=[bag]), PARAMS, "mask")[0] is FindingOutcome.FAIL


def test_no_person_or_no_match_needs_review_never_passes():
    assert pc.judge_box((X0, Y0, X1, Y1), segmentation(), PARAMS)[0] is FindingOutcome.UNSURE
    elsewhere = np.zeros((H, W), bool)
    elsewhere[0:40, 0:40] = True
    outcome, result = pc.judge_region(elsewhere, SEG, PARAMS, "polygon")
    assert outcome is FindingOutcome.UNSURE and result["reason"] == "no_matching_person"


def test_penn_fudan_box_and_mask_are_both_judged_in_one_based_coordinates():
    instance = PERSON.astype(np.uint8)  # object 1
    annotation = {"object_id": 1, "label": "PASpersonWalking", "bbox": [X0 + 1, Y0 + 1 + 45, X1 + 1, Y1 + 1]}
    outcome, reason, result = pc.object_coverage(annotation, "penn_fudan", (W, H), instance, SEG, PARAMS)
    assert outcome is FindingOutcome.FAIL and "box: head/top of the person is outside the annotation" in reason
    box, mask = result["geometries"]
    assert (box["geometry"], mask["geometry"]) == ("box", "mask")
    assert box["person_box"] == [X0 + 1, Y0 + 1, X1 + 1, Y1 + 1] and box["coordinate_system"] == "one_based_inclusive"
    assert mask["regions"] == []  # the mask itself is complete


def rulebook(parameters=None):
    return Rulebook.model_validate({
        "rulebook_id": "rulebook_test", "version": 1, "guideline_text": "Cover the whole person.",
        "confirmation_state": "confirmed",
        "rules": [{"rule_id": "RULE-1", "description": "Cover the whole person.", "check_name": "person_coverage",
                   "parameters": parameters or {}, "origin": "reviewer", "source_sentence": "Cover the whole person.",
                   "confirmation_state": "confirmed", "context_sentences": [], "extraction_notes": []}]})


def run(manifest, rows, segmenter_factory, monkeypatch=None):
    placeholder = RuleEvaluationResult(findings=[], internal_check_failures=[])
    return pc.augment_person_coverage(placeholder, rulebook(), {"objects": rows}, manifest, SimpleNamespace(),
                                      audit_id="audit_test", sample_id="sample_1", sample_revision=1,
                                      segmenter_factory=segmenter_factory)


def test_missing_source_or_model_is_unavailable_never_pass(monkeypatch):
    rows = [{"annotation": {"object_id": 1, "bbox": [X0, Y0, X1 + 1, Y1 + 1], "bbox_origin": "supplied"}}]
    result = run(None, rows, lambda: SimpleNamespace(segment=lambda image: SEG))
    assert [(f.outcome, f.reason) for f in result.findings] == [(FindingOutcome.UNAVAILABLE, "source_data_unavailable")]

    def broken():
        raise RuntimeError("no checkpoint")
    monkeypatch.setattr("backend.app.storage.workflow_repository.read_manifest_asset_file", fake_reader)
    manifest = SimpleNamespace(annotation_format="coco", raw_mask=SimpleNamespace(availability=SimpleNamespace(value="not_supplied")))
    result = run(manifest, rows, broken)
    assert [(f.outcome, f.reason) for f in result.findings] == [(FindingOutcome.UNAVAILABLE, "person_segmenter_unavailable")]


def fake_reader(manifest, kind, settings, **_):
    buffer = BytesIO()
    Image.new("RGB", (W, H), "gray").save(buffer, format="PNG")
    return None, buffer.getvalue()


def test_json_polygon_findings_one_per_object(monkeypatch):
    monkeypatch.setattr("backend.app.storage.workflow_repository.read_manifest_asset_file", fake_reader)
    contour = cv2.findContours(PERSON.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0][0].reshape(-1, 2)
    good = [[float(x), float(y)] for x, y in contour]
    head_cut = [[x, max(y, Y0 + 45.0)] for x, y in good]
    rows = [{"annotation": {"object_id": 1, "source_key": "/annotations/0", "polygons": [good],
                            "bbox": [X0, Y0, X1 + 1, Y1 + 1], "bbox_origin": "supplied"}},
            {"annotation": {"object_id": 2, "source_key": "/annotations/1", "polygons": [head_cut],
                            "bbox": [X0, Y0 + 45, X1 + 1, Y1 + 1], "bbox_origin": "supplied"}}]
    manifest = SimpleNamespace(annotation_format="coco", raw_mask=SimpleNamespace(availability=SimpleNamespace(value="not_supplied")))
    result = run(manifest, rows, lambda: SimpleNamespace(segment=lambda image: SEG, identity={"model": "fake"}))
    outcomes = {f.object_id: f.outcome for f in result.findings}
    assert outcomes == {1: FindingOutcome.PASS, 2: FindingOutcome.FAIL}
    failed = next(f for f in result.findings if f.object_id == 2)
    assert "head/top of the person is outside the annotation" in failed.reason
    assert failed.evidence.measurements["segmenter"] == {"model": "fake"}
