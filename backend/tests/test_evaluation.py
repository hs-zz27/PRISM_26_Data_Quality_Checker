"""Confirmed-rule execution keeps machine outcomes and human review separate."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.app.models.finding import FindingOutcome, FindingResolution
from backend.app.models.rule import Rule, Rulebook
from backend.app.services.evaluation import (
    RuleEvaluationError,
    RulebookNotConfirmedError,
    evaluate_rules,
)


SOURCE = "Every object uses one of the allowed labels."


def confirmed_book(*rules: Rule) -> Rulebook:
    return Rulebook(
        rulebook_id="book_1",
        version=4,
        guideline_text=SOURCE,
        rules=list(rules),
        confirmation_state="confirmed",
    )


def label_rule(**changes) -> Rule:
    values = {
        "rule_id": "labels",
        "description": "Use only the configured labels.",
        "check_name": "allowed_labels",
        "parameters": {"allowed_labels": ["person"]},
        "origin": "extracted",
        "source_sentence": SOURCE,
        "confirmation_state": "confirmed",
    }
    values.update(changes)
    return Rule(**values)


def report(*, labels: list[str], mask_reason: str | None = None):
    rows = []
    for index, label in enumerate(labels):
        object_id = 7 if index < 2 else index + 1
        rows.append({
            "annotation": {"object_id": object_id, "label": label, "bbox": (1, 1, 2, 2)},
            "checks": {
                "label": {
                    "status": "completed", "reason": None, "detail": None,
                    "result": {
                        "object_id": object_id, "label": label,
                        "matches": label == "person", "reason": None,
                    },
                },
                "mask_components": {
                    "status": "unavailable" if mask_reason else "completed",
                    "reason": mask_reason,
                    "detail": None,
                "result": None if mask_reason else {
                        "measurement_available": True, "reason": None,
                        "component_count": 1, "components": [],
                    },
                },
            },
        })
    return {
        "image_id": "sample",
        "image_size": (2, 2),
        "annotation_text": SOURCE,
        "allowed_labels": ["person"],
        "status": "completed",
        "review_status": "pending_review",
        "errors": [],
        "checks": {
            "annotation_metadata": {
                "status": "completed", "reason": None, "detail": None,
                "result": {"measurement_available": True, "actual_object_count": len(labels)},
            },
            "labels": {
                "status": "completed", "reason": None, "detail": None,
                "result": {"matches": all(label == "person" for label in labels), "allowed_labels": ["person"]},
            },
        },
        "objects": rows,
    }


def test_supported_rules_emit_pass_and_fail_for_every_source_row():
    findings = evaluate_rules(
        confirmed_book(label_rule()), report(labels=["person", "vehicle"]),
        audit_id="audit_1", sample_id="sample", sample_revision=2,
    )
    assert [finding.outcome for finding in findings] == [FindingOutcome.PASS, FindingOutcome.FAIL]
    assert [finding.object_id for finding in findings] == [7, 7]
    assert findings[0].finding_id != findings[1].finding_id
    assert all(finding.review.resolution is FindingResolution.PENDING for finding in findings)
    assert all(finding.rulebook_id == "book_1" and finding.rulebook_version == 4 for finding in findings)
    assert all(finding.sample_revision == 2 and finding.source_sentence == SOURCE for finding in findings)


def test_unavailable_evidence_is_not_converted_to_failure():
    rule = Rule(
        rule_id="components", description="Use one connected component.",
        check_name="mask_components", parameters={"max_component_count": 1},
        origin="reviewer", confirmation_state="confirmed",
    )
    findings = evaluate_rules(
        confirmed_book(rule), report(labels=["person"], mask_reason="missing_mask"),
        audit_id="audit_1", sample_id="sample", sample_revision=1,
    )
    assert findings[0].outcome is FindingOutcome.UNAVAILABLE
    assert findings[0].reason == "missing_mask"


def test_confirmed_component_limit_can_fail_without_inventing_a_merge_claim():
    rule = Rule(
        rule_id="components", description="Use no more than one mask component.",
        check_name="mask_components", parameters={"max_component_count": 1},
        origin="reviewer", confirmation_state="confirmed",
    )
    measurements = report(labels=["person"])
    measurements["objects"][0]["checks"]["mask_components"]["result"]["component_count"] = 2
    findings = evaluate_rules(
        confirmed_book(rule), measurements,
        audit_id="audit_1", sample_id="sample", sample_revision=1,
    )
    assert findings[0].outcome is FindingOutcome.FAIL
    assert findings[0].reason is None


def test_unsupported_rule_is_visible_as_unavailable():
    rule = Rule(
        rule_id="future", description="A future requirement.", check_name="future_check",
        origin="reviewer", confirmation_state="confirmed",
    )
    findings = evaluate_rules(
        confirmed_book(rule), report(labels=["person"]),
        audit_id="audit_1", sample_id="sample", sample_revision=1,
    )
    assert findings[0].outcome is FindingOutcome.UNAVAILABLE
    assert findings[0].reason == "unsupported_check"
    assert findings[0].evidence.measurements == {}


def test_rule_parameters_are_strictly_validated_before_findings_are_created():
    rule = label_rule(parameters={"allowed_labels": ["person"], "invented": True})
    with pytest.raises(RuleEvaluationError):
        evaluate_rules(
            confirmed_book(rule), report(labels=["person"]),
            audit_id="audit_1", sample_id="sample", sample_revision=1,
        )


def test_negative_numeric_thresholds_are_rejected():
    rule = Rule(
        rule_id="alignment", description="Keep alignment within the configured bound.",
        check_name="box_mask_alignment", parameters={"max_edge_difference": -1},
        origin="reviewer", confirmation_state="confirmed",
    )
    with pytest.raises(RuleEvaluationError):
        evaluate_rules(
            confirmed_book(rule), report(labels=["person"]),
            audit_id="audit_1", sample_id="sample", sample_revision=1,
        )


@pytest.mark.parametrize("state", ["draft", "rejected"])
def test_nonconfirmed_rulebooks_are_rejected(state):
    rule = label_rule(confirmation_state=state)
    book = Rulebook(
        rulebook_id="book_1", version=1, guideline_text=SOURCE,
        rules=[rule], confirmation_state=state,
    )
    with pytest.raises(RulebookNotConfirmedError):
        evaluate_rules(book, report(labels=["person"]), audit_id="a", sample_id="s", sample_revision=1)


def test_mutating_inputs_after_evaluation_does_not_change_pinned_finding():
    book = confirmed_book(label_rule())
    measurements = report(labels=["person"])
    original_parameters = deepcopy(book.rules[0].parameters)
    finding = evaluate_rules(
        book, measurements, audit_id="audit_1", sample_id="sample", sample_revision=1,
    )[0]
    book.rules[0].parameters["allowed_labels"].append("vehicle")
    measurements["objects"][0]["annotation"]["label"] = "vehicle"
    assert finding.parameters == original_parameters
    assert finding.outcome is FindingOutcome.PASS


def test_nonfinite_measurement_evidence_is_rejected():
    measurements = report(labels=["person"])
    measurements["objects"][0]["checks"]["label"]["result"]["score"] = float("nan")
    with pytest.raises(RuleEvaluationError, match="finite"):
        evaluate_rules(
            confirmed_book(label_rule()), measurements,
            audit_id="audit_1", sample_id="sample", sample_revision=1,
        )


def test_ambiguous_completed_measurement_becomes_unsure():
    measurements = report(labels=["person"])
    measurements["objects"][0]["checks"]["mask_components"]["result"].pop("component_count")
    rule = Rule(
        rule_id="components", description="Use one connected component.",
        check_name="mask_components", parameters={"max_component_count": 1},
        origin="reviewer", confirmation_state="confirmed",
    )
    findings = evaluate_rules(
        confirmed_book(rule), measurements,
        audit_id="audit_1", sample_id="sample", sample_revision=1,
    )
    assert findings[0].outcome is FindingOutcome.UNSURE
    assert findings[0].reason == "ambiguous_evidence"


def test_missing_annotation_evidence_never_passes_allowed_labels():
    measurements = report(labels=[])
    measurements["annotation_text"] = None
    measurements["errors"] = [{
        "stage": "load", "reason": "annotation_load_failed", "detail": "missing annotation",
    }]
    measurements["objects"] = []
    findings = evaluate_rules(
        confirmed_book(label_rule()), measurements,
        audit_id="audit_1", sample_id="sample", sample_revision=1,
    )
    assert findings[0].outcome is FindingOutcome.UNAVAILABLE
    assert findings[0].reason == "annotation_load_failed"


def test_malformed_annotation_evidence_is_unavailable_for_allowed_labels():
    measurements = report(labels=[])
    measurements["annotation_text"] = "Bounding box for object malformed"
    measurements["errors"] = [{
        "stage": "parse", "reason": "annotation_parse_failed", "detail": "malformed box",
    }]
    measurements["objects"] = []
    findings = evaluate_rules(
        confirmed_book(label_rule()), measurements,
        audit_id="audit_1", sample_id="sample", sample_revision=1,
    )
    assert findings[0].outcome is FindingOutcome.UNAVAILABLE
    assert findings[0].reason == "annotation_parse_failed"


def test_parsed_zero_object_annotation_can_pass_allowed_labels():
    measurements = report(labels=[])
    measurements["annotation_text"] = "Objects with ground truth: 0 {}"
    measurements["checks"]["annotation_metadata"]["result"]["actual_object_count"] = 0
    measurements["objects"] = []
    findings = evaluate_rules(
        confirmed_book(label_rule()), measurements,
        audit_id="audit_1", sample_id="sample", sample_revision=1,
    )
    assert findings[0].outcome is FindingOutcome.PASS


def test_unrelated_asset_errors_do_not_block_usable_annotation_labels():
    measurements = report(labels=["person"])
    measurements["status"] = "incomplete"
    measurements["errors"] = [{"stage": "load", "reason": "mask_load_failed", "detail": "missing mask"}]
    findings = evaluate_rules(
        confirmed_book(label_rule()), measurements,
        audit_id="audit_1", sample_id="sample", sample_revision=1,
    )
    assert findings[0].outcome is FindingOutcome.PASS


def test_pair_rules_pin_pair_evidence_and_keep_duplicate_object_ids_distinct():
    rule = Rule(
        rule_id="overlap", description="Boxes must not overlap too much.",
        check_name="box_overlaps", parameters={"max_iou": 0.5},
        origin="reviewer", confirmation_state="confirmed",
    )
    measurements = report(labels=["person", "person"])
    measurements["checks"]["box_overlaps"] = {
        "status": "completed", "reason": None, "detail": None,
        "result": {
            "pairs": [
                {"measurement_available": True, "reason": None, "iou": 0.2, "object_ids": [7, 7]},
                {"measurement_available": True, "reason": None, "iou": 0.8, "object_ids": [7, 8]},
            ],
        },
    }
    findings = evaluate_rules(
        confirmed_book(rule), measurements,
        audit_id="audit_1", sample_id="sample", sample_revision=1,
    )
    assert [finding.outcome for finding in findings] == [FindingOutcome.PASS, FindingOutcome.FAIL]
    assert findings[0].finding_id != findings[1].finding_id
    assert findings[0].evidence.measurements["result"]["object_ids"] == [7, 7]
    assert findings[1].evidence.measurements["pair_index"] == 1
    assert all(finding.review.resolution is FindingResolution.PENDING for finding in findings)


def test_pair_rule_with_unavailable_measurement_is_unavailable_not_a_failure():
    rule = Rule(
        rule_id="overlap", description="Boxes must not overlap too much.",
        check_name="box_overlaps", parameters={"max_iou": 0.5},
        origin="reviewer", confirmation_state="confirmed",
    )
    measurements = report(labels=["person"])
    measurements["checks"]["box_overlaps"] = {
        "status": "completed", "reason": None, "detail": None,
        "result": {"pairs": [{"measurement_available": False, "reason": "invalid_box", "iou": None}]},
    }
    findings = evaluate_rules(
        confirmed_book(rule), measurements,
        audit_id="audit_1", sample_id="sample", sample_revision=1,
    )
    assert findings[0].outcome is FindingOutcome.UNAVAILABLE
    assert findings[0].reason == "invalid_box"


def test_each_allowed_labels_rule_uses_its_own_parameter_set():
    person_rule = label_rule(rule_id="person_labels")
    vehicle_rule = label_rule(
        rule_id="vehicle_labels", parameters={"allowed_labels": ["vehicle"]},
    )
    findings = evaluate_rules(
        confirmed_book(person_rule, vehicle_rule), report(labels=["person"]),
        audit_id="audit_1", sample_id="sample", sample_revision=1,
    )
    assert [finding.outcome for finding in findings] == [FindingOutcome.PASS, FindingOutcome.FAIL]
    assert findings[0].reason is None
    assert findings[1].reason == "label_not_allowed"
    assert findings[1].evidence.measurements["allowed_labels"] == ["vehicle"]


def test_generic_checks_use_boolean_fields_from_their_actual_contracts():
    rule = Rule(
        rule_id="valid-box", description="Boxes must be valid.",
        check_name="box_validity", parameters={}, origin="reviewer",
        confirmation_state="confirmed",
    )
    measurements = report(labels=["person"])
    measurements["objects"][0]["checks"]["box_validity"] = {
        "status": "completed", "reason": None, "detail": None,
        "result": {"valid": True, "bbox": [1, 1, 2, 2], "reason": None, "detail": None},
    }
    findings = evaluate_rules(
        confirmed_book(rule), measurements,
        audit_id="audit_1", sample_id="sample", sample_revision=1,
    )
    assert findings[0].outcome is FindingOutcome.PASS
