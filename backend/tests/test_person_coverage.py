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


# ── measured box suggestions ─────────────────────────────────────────────────
@pytest.mark.parametrize("bbox, fixed, words", [
    # Penn-Fudan, 1-based inclusive: head left out, so only the top edge moves up to the detected head.
    ([X0 + 1, Y0 + 46, X1 + 1, Y1 + 1], [X0 + 1, Y0 + 1, X1 + 1, Y1 + 1], "Top edge moved up 45 px to include the head/top."),
    # Extra background on the left: the left edge moves right; the other sides stay as drawn.
    ([X0 - 29, Y0 + 1, X1 + 1, Y1 + 1], [X0 + 1, Y0 + 1, X1 + 1, Y1 + 1], "Left edge moved right 30 px to remove extra background."),
])
def test_measured_box_fix_moves_only_the_failing_sides(bbox, fixed, words):
    annotation = {"object_id": 1, "label": "PASpersonWalking", "bbox": bbox}
    outcome, _, result = pc.object_coverage(annotation, "penn_fudan", (W, H), None, SEG, PARAMS)
    assert outcome is FindingOutcome.FAIL
    assert pc.measured_box_fix(annotation, result) == (fixed, words)


def test_measured_box_fix_keeps_continuous_coordinates_of_sides_that_passed():
    annotation = {"object_id": 1, "bbox": [X0 + 0.25, Y0, X1 + 0.75, Y1 + 1 - 30], "bbox_origin": "supplied"}
    _, _, result = pc.object_coverage(annotation, "coco", (W, H), None, SEG, PARAMS)
    box, words = pc.measured_box_fix(annotation, result)
    assert box == [X0 + 0.25, Y0, X1 + 0.75, Y1 + 1]  # a continuous bottom edge ends after the last person row
    assert words == "Bottom edge moved down 30 px to include the feet/bottom."
    good = {"object_id": 1, "bbox": [X0, Y0, X1 + 1, Y1 + 1], "bbox_origin": "supplied"}
    assert pc.measured_box_fix(good, pc.object_coverage(good, "coco", (W, H), None, SEG, PARAMS)[2]) is None


def feedback(object_keys=None):
    from backend.app.models.review_feedback import ImageReviewFeedback
    return ImageReviewFeedback(reviewer_id="reviewer", source_audit_id="audit_parent", comment="The head is cut off.",
                               propose_correction=True, object_keys=object_keys)


def penn_fudan_manifest():
    return SimpleNamespace(annotation_format="penn_fudan", dataset_id="dataset_1", sample_id="sample_1",
                           sample_revision=1, image_key="FudanPed00001.png", original_image=SimpleNamespace(sha256="a" * 64),
                           annotation_text=SimpleNamespace(sha256="b" * 64),
                           raw_mask=SimpleNamespace(availability=SimpleNamespace(value="not_supplied")))


@pytest.mark.parametrize("object_keys, measured", [(None, True), (["penn_fudan:1"], True), (["penn_fudan:2"], False)])
def test_suggested_correction_uses_the_measured_box_for_reviewed_people(monkeypatch, object_keys, measured):
    from backend.app.services.review_correction import attach_review_context
    monkeypatch.setattr("backend.app.storage.workflow_repository.read_manifest_asset_file", fake_reader)
    monkeypatch.setattr("backend.app.services.review_correction.read_manifest_asset_file", fake_reader)
    head_cut = {"object_id": 1, "label": "PASpersonWalking", "bbox": [X0 + 1, Y0 + 46, X1 + 1, Y1 + 1]}
    complete = {"object_id": 2, "label": "PASpersonWalking", "bbox": [X0 + 1, Y0 + 1, X1 + 1, Y1 + 1]}
    rows = [{"annotation": head_cut}, {"annotation": complete}]
    evaluation = run(penn_fudan_manifest(), rows, lambda: SimpleNamespace(segment=lambda image: SEG))
    result = attach_review_context({"objects": rows}, feedback(object_keys), penn_fudan_manifest(), rulebook(),
                                   SimpleNamespace(), None, None, evaluation=evaluation)
    proposal = result["correction_proposal"]
    if not measured:  # nothing measured for the reviewed person: the chat-model draft runs as before
        assert "method" not in proposal and proposal["status"] == "unavailable"
        return
    assert proposal["method"] == "person_coverage" and proposal["status"] == "proposed"
    assert proposal["objects"] == [{**head_cut, "bbox": [X0 + 1, Y0 + 1, X1 + 1, Y1 + 1]}, complete]
    assert proposal["reason"] == ("Measured by the person check (Mask R-CNN). Top edge moved up 45 px to include "
                                  "the head/top. Other sides are unchanged.")
    assert rows[0]["annotation"]["bbox"] == [X0 + 1, Y0 + 46, X1 + 1, Y1 + 1]  # the draft never edits the source


# ── measured outline suggestions (COCO) ──────────────────────────────────────
def coco_head_cut_case():
    """A COCO document with two people: the first outline leaves out the head, the second is right.

    The rows are what the app's own COCO reader makes of it, so the proposal can be written back to the source.
    """
    import json
    from backend.app.datasets.coco import parse_coco
    from backend.app.datasets.json_sample import display_objects
    contour = cv2.findContours(PERSON.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0][0].reshape(-1, 2)
    good = [[float(x), float(y)] for x, y in contour]
    head_cut = [[x, max(y, Y0 + 45.0)] for x, y in good]
    annotations = [{"id": i + 1, "image_id": 1, "category_id": 1, "iscrowd": 0, "area": 1.0,
                    "segmentation": [[v for point in points for v in point]],
                    "bbox": [X0, Y0, X1 + 1 - X0, Y1 + 1 - Y0]}  # the boxes are right
                   for i, points in enumerate((head_cut, good))]
    document = {"images": [{"id": 1, "file_name": "p.png", "width": W, "height": H}], "annotations": annotations,
                "categories": [{"id": 1, "name": "person"}]}
    rows = [{"annotation": o.model_dump(mode="json")} for o in display_objects(parse_coco(document).images[0])]

    def reader(manifest, kind, settings, **_):
        return (None, json.dumps(document).encode()) if kind == "annotation_text" else fake_reader(manifest, kind, settings)
    manifest = SimpleNamespace(**{**vars(penn_fudan_manifest()), "annotation_format": "coco", "image_key": "/images/0"})
    return rows, reader, manifest


def test_outline_problem_gets_a_measured_outline_fix(monkeypatch):
    from backend.app.services.review_correction import attach_review_context
    rows, reader, manifest = coco_head_cut_case()
    monkeypatch.setattr("backend.app.storage.workflow_repository.read_manifest_asset_file", fake_reader)
    monkeypatch.setattr("backend.app.services.review_correction.read_manifest_asset_file", reader)
    evaluation = run(manifest, rows, lambda: SimpleNamespace(segment=lambda image: SEG))
    assert {f.object_id: f.outcome for f in evaluation.findings} == {1: FindingOutcome.FAIL, 2: FindingOutcome.PASS}
    proposal = attach_review_context({"objects": rows}, feedback(), manifest, rulebook(), SimpleNamespace(), None, None,
                                     evaluation=evaluation,
                                     segmenter_factory=lambda: SimpleNamespace(segment=lambda image: SEG))["correction_proposal"]
    assert proposal["method"] == "person_coverage" and proposal["status"] == "proposed"
    assert proposal["reason"].startswith("Measured by the person check (Mask R-CNN). Outline: added the person's head/top")
    fixed, untouched = proposal["objects"]
    assert untouched == rows[1]["annotation"] and fixed["bbox"] == rows[0]["annotation"]["bbox"]
    assert fixed["polygons"] != rows[0]["annotation"]["polygons"]
    assert all(3 <= len(part) <= pc.MAX_OUTLINE_POINTS for part in fixed["polygons"])
    assert pc.object_coverage(fixed, "coco", (W, H), None, SEG, PARAMS)[0] is FindingOutcome.PASS


@pytest.mark.parametrize("object_keys, outline_message", [(None, True), (["/annotations/0"], True),
                                                          (["/annotations/1"], False)])
def test_outline_problem_without_a_fix_says_to_use_correct(monkeypatch, object_keys, outline_message):
    from backend.app.services.review_correction import OUTLINE_NO_SUGGESTION, attach_review_context, object_key
    rows, reader, manifest = coco_head_cut_case()
    assert [object_key(row["annotation"]) for row in rows] == ["/annotations/0", "/annotations/1"]
    monkeypatch.setattr("backend.app.storage.workflow_repository.read_manifest_asset_file", fake_reader)
    monkeypatch.setattr("backend.app.services.review_correction.read_manifest_asset_file", reader)
    evaluation = run(manifest, rows, lambda: SimpleNamespace(segment=lambda image: SEG))

    def unavailable_model():
        raise RuntimeError("person model unavailable")
    proposal = attach_review_context({"objects": rows}, feedback(object_keys), manifest, rulebook(), SimpleNamespace(),
                                     None, None, evaluation=evaluation,
                                     segmenter_factory=unavailable_model)["correction_proposal"]
    assert proposal["status"] == "unavailable" and proposal["objects"] is None and "method" not in proposal
    assert (proposal["reason"] == OUTLINE_NO_SUGGESTION) is outline_message


def test_measured_outline_fix_is_none_when_nothing_is_flagged_or_for_masks():
    rows, _, _ = coco_head_cut_case()
    assert pc.measured_outline_fix(rows[1]["annotation"], "coco", (W, H), SEG, PARAMS) is None   # already right
    assert pc.measured_outline_fix({"object_id": 1, "bbox": [1, 1, 5, 5]}, "penn_fudan", (W, H), SEG, PARAMS) is None


@pytest.mark.parametrize("geometries, check, object_keys, message", [
    ([{"geometry": "mask", "regions": [{}]}], "person_coverage", None, "MASK_NO_SUGGESTION"),
    ([{"geometry": "polygon", "regions": [{}]}], "person_coverage", None, "OUTLINE_NO_SUGGESTION"),
    ([{"geometry": "box", "regions": [{}]}], "person_coverage", None, None),
    ([], "unannotated_people", None, "MISSING_NO_SUGGESTION"),
    ([], "unannotated_people", ["penn_fudan:1"], None),    # a person with no annotation is not one object
])
def test_plain_message_names_what_has_no_automatic_suggestion(geometries, check, object_keys, message):
    from backend.app.services import review_correction as rc
    annotation = {"object_id": 1, "label": "person", "bbox": [1, 1, 5, 5]} if check == "person_coverage" else None
    finding = SimpleNamespace(check_name=check, outcome=FindingOutcome.FAIL, evidence=SimpleNamespace(
        measurements={"annotation": annotation, "result": {"geometries": geometries}}))
    expected = getattr(rc, message) if message else None
    assert rc._no_suggestion_reason(feedback(object_keys), SimpleNamespace(findings=[finding])) == expected
