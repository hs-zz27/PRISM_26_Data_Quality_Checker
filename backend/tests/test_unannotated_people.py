"""unannotated_people: people the person model finds that no annotation accounts for (fake segmenter)."""
from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from backend.app.checks.registry import RuleParameterError, validate_check_parameters
from backend.app.models.finding import FindingOutcome
from backend.app.models.rule import Rulebook
from backend.app.services import person_coverage as pc
from backend.app.services.evaluation import RuleEvaluationResult

H, W = 400, 600


def person(x, top=50, height=300, width=80):
    mask = np.zeros((H, W), bool)
    mask[top:top + height, x:x + width] = True
    return mask


def segmentation(*people, scores=None):
    scores = scores or [0.99] * len(people)
    return SimpleNamespace(people=[SimpleNamespace(mask=m, box=pc._extent(m), score=s) for m, s in zip(people, scores)],
                           carried=[])


def box_row(object_id, mask, annotation_format="coco"):
    x0, y0, x1, y1 = pc._extent(mask)
    bbox = [x0, y0, x1 + 1, y1 + 1] if annotation_format == "coco" else [x0 + 1, y0 + 1, x1 + 1, y1 + 1]
    return {"annotation": {"object_id": object_id, "label": "person", "bbox": bbox, "bbox_origin": "supplied"}}


def judge(rows, seg, annotation_format="coco", **parameters):
    params = validate_check_parameters("unannotated_people", parameters)
    return pc.unannotated_people(rows, annotation_format, (W, H), None, seg, params)


A, B = person(50), person(300)


def test_parameters_default_and_validation():
    params = validate_check_parameters("unannotated_people", {})
    assert (params.min_person_height_px, params.min_score) == (40, 0.9)
    for bad in ({"min_score": 0.4}, {"min_score": 1.5}, {"min_person_height_px": -1}, {"min_person_height_px": 4.5},
                {"tolerance_fraction": 0.1}):
        with pytest.raises(RuleParameterError):
            validate_check_parameters("unannotated_people", bad)


def test_everyone_annotated_passes():
    outcome, reason, result = judge([box_row(1, A), box_row(2, B)], segmentation(A, B))
    assert outcome is FindingOutcome.PASS and reason is None
    assert result["people_found"] == 2 and result["people_annotated"] == 2 and result["unannotated"] == []


def test_a_person_without_an_annotation_fails_and_says_where():
    outcome, reason, result = judge([box_row(1, A)], segmentation(A, B))
    assert outcome is FindingOutcome.FAIL
    assert reason == "1 person has no annotation: at [300, 50, 380, 350] (300 px tall, 99% sure)"
    assert result["unannotated"] == [{"box": [300, 50, 380, 350], "height_px": 300, "score": 0.99,
                                      "inside_annotations": 0.0}]
    assert result["coordinate_system"] == "zero_based_continuous"


def test_penn_fudan_reports_one_based_inclusive_boxes():
    outcome, reason, result = judge([box_row(1, A, "penn_fudan")], segmentation(A, B), "penn_fudan")
    assert outcome is FindingOutcome.FAIL and result["unannotated"][0]["box"] == [301, 51, 380, 350]
    assert result["coordinate_system"] == "one_based_inclusive"


def test_an_image_without_annotations_reports_every_clear_person():
    outcome, reason, _ = judge([], segmentation(A, B))
    assert outcome is FindingOutcome.FAIL and reason.startswith("2 people have no annotation:")
    assert judge([], segmentation())[0] is FindingOutcome.PASS   # nobody found, nobody missing


def test_small_uncertain_and_covered_people_are_counted_not_reported():
    small = person(450, top=10, height=30, width=12)        # under 40 px tall
    unsure = person(200, width=60)                          # found with a low score
    upper_half = person(50, height=150)                     # a second detection of the annotated person A
    outcome, _, result = judge([box_row(1, A)], segmentation(A, small, unsure, upper_half,
                                                              scores=[0.99, 0.99, 0.6, 0.95]))
    assert outcome is FindingOutcome.PASS
    assert result["not_counted"] == {"inside_an_annotation": 1, "too_small": 1, "low_score": 1}
    # The thresholds are the rule's settings: a reviewer can ask for smaller or less certain people too.
    outcome, reason, _ = judge([box_row(1, A)], segmentation(A, small, unsure, upper_half, scores=[0.99, 0.99, 0.6, 0.95]),
                               min_person_height_px=20, min_score=0.5)
    assert outcome is FindingOutcome.FAIL and reason.startswith("2 people have no annotation:")


def test_a_polygon_claims_its_person_by_mask_overlap():
    outline = {"object_id": 1, "label": "person", "bbox_origin": "derived_from_polygon",
               "polygons": [[[300.0, 50.0], [379.0, 50.0], [379.0, 349.0], [300.0, 349.0]]], "bbox": [300, 50, 380, 350]}
    outcome, _, result = judge([{"annotation": outline}], segmentation(A, B))
    assert outcome is FindingOutcome.FAIL and result["unannotated"][0]["box"] == [50, 50, 130, 350]


# ── as an audit step ─────────────────────────────────────────────────────────
def rulebook(*checks):
    rules = [{"rule_id": f"RULE-{i}", "description": "Every visible person must have an annotation.", "check_name": check,
              "parameters": {}, "origin": "reviewer", "source_sentence": None, "confirmation_state": "confirmed",
              "context_sentences": [], "extraction_notes": []} for i, check in enumerate(checks, start=1)]
    return Rulebook.model_validate({"rulebook_id": "rulebook_test", "version": 1, "confirmation_state": "confirmed",
                                    "guideline_text": "Every visible person must have an annotation.", "rules": rules})


def fake_reader(manifest, kind, settings, **_):
    buffer = BytesIO()
    Image.new("RGB", (W, H), "gray").save(buffer, format="PNG")
    return None, buffer.getvalue()


def run(book, rows, segmenter_factory):
    placeholder = RuleEvaluationResult(findings=[], internal_check_failures=[])
    manifest = SimpleNamespace(annotation_format="coco", raw_mask=SimpleNamespace(availability=SimpleNamespace(value="not_supplied")))
    return pc.augment_person_coverage(placeholder, book, {"objects": rows}, manifest, SimpleNamespace(),
                                      audit_id="audit_test", sample_id="sample_1", sample_revision=1,
                                      segmenter_factory=segmenter_factory)


def test_audit_step_reports_one_image_level_finding(monkeypatch):
    monkeypatch.setattr("backend.app.storage.workflow_repository.read_manifest_asset_file", fake_reader)
    result = run(rulebook("unannotated_people"), [box_row(1, A)], lambda: SimpleNamespace(segment=lambda image: segmentation(A, B)))
    [finding] = result.findings
    assert finding.check_name == "unannotated_people" and finding.object_id is None
    assert finding.outcome is FindingOutcome.FAIL and finding.reason.startswith("1 person has no annotation")
    assert finding.evidence.measurements["result"]["unannotated"][0]["box"] == [300, 50, 380, 350]


def test_both_person_checks_share_one_model_run(monkeypatch):
    monkeypatch.setattr("backend.app.storage.workflow_repository.read_manifest_asset_file", fake_reader)
    calls = []

    def segment(image):
        calls.append(image.size)
        return segmentation(A, B)
    result = run(rulebook("person_coverage", "unannotated_people"), [box_row(1, A), box_row(2, B)],
                 lambda: SimpleNamespace(segment=segment))
    assert calls == [(W, H)]
    assert {(f.check_name, f.outcome) for f in result.findings} == {
        ("person_coverage", FindingOutcome.PASS), ("unannotated_people", FindingOutcome.PASS)}


def test_a_missing_model_is_unavailable_never_a_pass(monkeypatch):
    monkeypatch.setattr("backend.app.storage.workflow_repository.read_manifest_asset_file", fake_reader)

    def broken():
        raise RuntimeError("weights missing")
    [finding] = run(rulebook("unannotated_people"), [], broken).findings
    assert finding.outcome is FindingOutcome.UNAVAILABLE and finding.reason == "person_segmenter_unavailable"
