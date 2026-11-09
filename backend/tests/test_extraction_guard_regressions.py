"""Regression cases for PR #18: source grounding, not model quality."""
import json
import pytest
from backend.app.inference.rule_proposals import expand_response, extraction_catalog
from backend.app.inference.extraction_grounding import normalized_numbers

IDS = {c["check_name"]: c["check_id"] for c in extraction_catalog()}

def extract(text, check, parameters=None, roles=None, context=None):
    response = {
        "lines": [{"source_id": i, "role": role} for i, role in (roles or {1: "requirement"}).items()],
        "rules": [{"source_id": 1, "context_ids": context or [], "check_id": IDS[check], "parameters": parameters or {}}],
    }
    return expand_response(json.dumps(response), text.splitlines())

@pytest.mark.parametrize("marker", ["5. ", "5) ", "(5) ", "[5] "])
def test_numbering_is_not_threshold_evidence(marker):
    [rule] = extract(marker + "Each mask may have at most 2 connected components.",
                     "mask_components", {"max_component_count": 5})
    assert rule.check_name is None and rule.extraction_notes

def test_other_measurements_do_not_authorize_a_component_limit():
    [rule] = extract("Each mask may have at most 2 connected components, and at most 5% of a mask may lie outside its box.",
                     "mask_components", {"max_component_count": 5})
    assert rule.check_name is None

@pytest.mark.parametrize("word", ["three", "twenty-three", "one hundred and five", "3"])
def test_numeric_pixel_requirement_cannot_be_visual(word):
    text = f"Boxes may extend at most {word} pixels beyond the visible person; a reviewer must approve this tolerance."
    [rule] = extract(text, "annotation_guideline")
    assert rule.check_name is None and rule.source_sentence == text

def test_supported_written_count_remains_executable():
    [rule] = extract("Each mask may have at most two connected components.",
                     "mask_components", {"max_component_count": 2})
    assert rule.check_name == "mask_components" and not rule.extraction_notes

def test_wrong_comparison_direction_is_unsupported():
    [rule] = extract("Each mask must have at least 2 connected components.",
                     "mask_components", {"max_component_count": 2})
    assert rule.check_name is None

def test_percentage_is_grounded_as_fraction():
    [rule] = extract("At most five percent of each mask may lie outside its box.",
                     "mask_outside_box", {"max_outside_fraction": 0.05})
    assert rule.check_name == "mask_outside_box"

def test_quantity_with_wrong_units_is_not_substituted():
    [rule] = extract("At most 5 pixels of each mask may lie outside its box.",
                     "mask_outside_box", {"max_outside_fraction": 0.05})
    assert rule.check_name is None

def test_invented_numeric_value_is_unsupported():
    [rule] = extract("Masks should mostly stay inside their boxes.",
                     "mask_outside_box", {"max_outside_fraction": 0.1})
    assert rule.check_name is None and rule.extraction_notes

@pytest.mark.parametrize("text", [
    "Only use a label that matches the visible object, for example person or car.",
    "Each valid label must match the visible object, such as person or car.",
])
def test_examples_are_not_a_closed_vocabulary(text):
    [rule] = extract(text, "allowed_labels", {"allowed_labels": ["person", "car"]})
    assert rule.check_name is None and rule.extraction_notes

@pytest.mark.parametrize("text", [
    "Allowed labels are person and car.",
    'Allowed labels are "person" and "car".',
    "Annotation classes are person and car.",
    "Labels must be one of person or car.",
    "Only person and car are allowed labels.",
    "Use only person and car as labels.",
])
def test_explicit_vocabulary_is_supported(text):
    [rule] = extract(text, "allowed_labels", {"allowed_labels": ["person", "car"]})
    assert rule.check_name == "allowed_labels"

@pytest.mark.parametrize("text,labels", [
    ("Allowed labels are person and cart.", ["person", "car"]),
    ("Allowed labels are person, car and bus.", ["person", "car"]),
    ('Allowed labels are "Person" and "car".', ["person", "car"]),
])
def test_vocabulary_requires_exact_complete_names(text, labels):
    [rule] = extract(text, "allowed_labels", {"allowed_labels": labels})
    assert rule.check_name is None

def test_multi_requirement_paragraph_preserves_the_explicit_list():
    text = "Allowed labels are dog and cat, every label must name the visible animal, and boxes stay inside the image."
    [rule] = extract(text, "allowed_labels", {"allowed_labels": ["dog", "cat"]})
    assert rule.check_name == "allowed_labels"

def test_unclassified_requirement_is_not_hidden_by_a_context_reference():
    feet = "The polygon must include the visible feet."
    rules = extract("Each label must match the visible object.\n" + feet,
                    "annotation_guideline", roles={1: "requirement"}, context=[2])
    assert len(rules) == 2
    assert rules[1].source_sentence == feet
    assert rules[1].check_name is None and rules[1].extraction_notes

def test_measurement_context_cannot_bypass_visual_guard():
    text = "The box must follow the person's outline.\nThe permitted tolerance is three pixels."
    rules = extract(text, "annotation_guideline", roles={1: "requirement", 2: "context"})
    assert rules[0].check_name is None
    assert rules[0].context_sentences == [text.splitlines()[1]]

def test_number_normalization_preserves_source_input():
    original = "5. Each mask may have at most twenty-three connected components."
    assert normalized_numbers(original) == "Each mask may have at most 23 connected components."
    assert original.startswith("5.")

# The 10-rule COCO guideline of 8 Nov 2026: the 4b model mapped 6 of 10 rules wrongly.
@pytest.mark.parametrize("text", [
    "The only allowed annotation category is person.",
    "1. The only allowed annotation category is person.",
    "Allowed annotation category is person.",
    "The allowed classes are person.",
])
def test_a_singular_or_the_only_label_list_is_explicit(text):
    [rule] = extract(text, "allowed_labels", {"allowed_labels": ["person"]})
    assert rule.check_name == "allowed_labels"

@pytest.mark.parametrize("text,check,parameters", [
    ("Two annotation boxes must have an IoU of at most 0.6; a higher IoU means the same person was annotated twice.", "box_validity", {}),
    ("The declared width and height of each image must match the actual image.", "box_overlaps", {"max_iou": 0.6}),
    ("Every annotation ID must be a positive integer and unique.", "polygon_validity", {}),
    ("Every polygon must have at least three distinct vertices and no self-intersections.", "annotation_metadata", {}),
])
def test_a_neighbouring_rules_check_is_not_accepted(text, check, parameters):
    [rule] = extract(text, check, parameters)
    assert rule.check_name is None and "does not mention what that check measures" in rule.extraction_notes[0]

@pytest.mark.parametrize("text,check", [
    ("Every bounding box must be valid and lie entirely within the image bounds.", "box_validity"),
    ("The declared width and height of each image must match the actual image.", "annotation_metadata"),
    ("Every annotation ID must be a positive integer and unique.", "annotation_ids"),
    ("Every polygon must have at least three distinct vertices and no self-intersections.", "polygon_validity"),
])
def test_the_right_check_is_still_accepted(text, check):
    [rule] = extract(text, check)
    assert rule.check_name == check and not rule.extraction_notes

@pytest.mark.parametrize("text,check", [
    ("Each person's polygon and bounding box must cover the whole person, including the head and the feet, "
     "without large areas of extra background.", "person_coverage"),
    ("Every clearly visible person in the image must have an annotation; no visible person may be left without one.",
     "unannotated_people"),
])
def test_a_measurable_person_rule_proposed_as_visual_gets_the_measured_check(text, check):
    [rule] = extract(text, "annotation_guideline")
    assert rule.check_name == check and rule.parameters == {}
    assert "mapped to the measured check" in rule.extraction_notes[0]

@pytest.mark.parametrize("text", [
    "Only real people may be annotated; statues, posters, pictures, mannequins and reflections of people must not be annotated.",
    "Each annotation must mark exactly one person.",
    "The bounding box of a fully visible person must include the whole head, including hair and any headwear.",
    "Every annotation drawn around a visible person must be labelled person, never car.",
])
def test_other_person_rules_stay_visual(text):
    [rule] = extract(text, "annotation_guideline")
    assert rule.check_name == "annotation_guideline"
