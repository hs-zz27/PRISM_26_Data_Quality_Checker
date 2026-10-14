import pytest
from pydantic import ValidationError

from backend.app.models.rule import ConfirmationState, Rule, Rulebook, RuleOrigin


SOURCE = "Allowed labels are person and vehicle."


def label_rule(**changes):
    values = {
        "rule_id": "LBL-1",
        "description": "Use an allowed label.",
        "check_name": "allowed_labels",
        "parameters": {"allowed_labels": ["person", "vehicle"]},
        "origin": "extracted",
        "source_sentence": SOURCE,
    }
    values.update(changes)
    return Rule(**values)


def test_draft_defaults_and_json_round_trip():
    rule = label_rule()
    book = Rulebook(rulebook_id="example", guideline_text=SOURCE, rules=[rule])
    assert rule.confirmation_state == book.confirmation_state == "draft"
    assert book.version == 1 and book.guideline_text == SOURCE
    assert Rulebook.model_validate_json(book.model_dump_json()) == book


def test_reviewer_rule_can_have_no_citation_or_implementation():
    rule = Rule(rule_id="CUSTOM-1", description="Review every image.", origin="reviewer")
    book = Rulebook(rulebook_id="manual", guideline_text="", rules=[rule])
    assert book.rules[0].source_sentence is None and book.rules[0].check_name is None


def test_unknown_check_is_retained():
    rule = label_rule(check_name="future_check")
    assert rule.check_name == "future_check"


def test_extracted_rule_requires_source():
    with pytest.raises(ValidationError, match="source sentence"):
        label_rule(source_sentence=None)


@pytest.mark.parametrize("source", ["invented source", SOURCE.lower()])
def test_rulebook_rejects_citation_absent_from_guidelines(source):
    with pytest.raises(ValidationError, match="absent"):
        Rulebook(rulebook_id="example", guideline_text=SOURCE, rules=[label_rule(source_sentence=source)])


def test_reviewer_citations_are_also_verified():
    with pytest.raises(ValidationError, match="absent"):
        Rulebook(rulebook_id="example", guideline_text=SOURCE, rules=[label_rule(origin="reviewer", source_sentence="invented")])


def test_duplicate_rule_ids_are_rejected():
    with pytest.raises(ValidationError, match="unique"):
        Rulebook(rulebook_id="example", guideline_text=SOURCE, rules=[label_rule(), label_rule()])


def test_confirmed_rulebook_contains_confirmed_rules():
    book = Rulebook(
        rulebook_id="example", version=2, guideline_text=SOURCE,
        rules=[label_rule(confirmation_state="confirmed")], confirmation_state="confirmed",
    )
    assert book.version == 2 and book.confirmation_state == "confirmed"


@pytest.mark.parametrize("state", ["draft", "rejected"])
def test_unconfirmed_rules_cannot_enter_confirmed_rulebook(state):
    with pytest.raises(ValidationError, match="only confirmed"):
        Rulebook(
            rulebook_id="example", guideline_text=SOURCE,
            rules=[label_rule(confirmation_state=state)], confirmation_state="confirmed",
        )


def test_empty_rulebook_cannot_be_confirmed():
    with pytest.raises(ValidationError, match="cannot be empty"):
        Rulebook(rulebook_id="example", guideline_text=SOURCE, confirmation_state="confirmed")


@pytest.mark.parametrize("version", [0, -1, True, "1"])
def test_invalid_version(version):
    with pytest.raises(ValidationError):
        Rulebook(rulebook_id="example", version=version, guideline_text=SOURCE)


@pytest.mark.parametrize("changes", [
    {"rule_id": " "}, {"description": ""}, {"check_name": " "},
    {"source_sentence": "\n"}, {"origin": "model"}, {"confirmation_state": "accepted"},
    {"parameters": {"allowed_labels": {"person"}}}, {"unexpected": "value"},
])
def test_invalid_rule_fields(changes):
    with pytest.raises(ValidationError):
        label_rule(**changes)


def test_default_parameter_and_rule_collections_are_independent():
    first = Rule(rule_id="1", description="First.", origin="reviewer")
    second = Rule(rule_id="2", description="Second.", origin="reviewer")
    first.parameters["limit"] = 3
    assert second.parameters == {}
    first_book = Rulebook(rulebook_id="first", guideline_text="")
    second_book = Rulebook(rulebook_id="second", guideline_text="")
    first_book.rules.append(first)
    assert second_book.rules == []


def test_string_inputs_and_defaults_produce_enum_members():
    rule = label_rule()
    book = Rulebook(rulebook_id="example", guideline_text=SOURCE, rules=[rule])
    assert rule.origin is RuleOrigin.EXTRACTED
    assert rule.confirmation_state is ConfirmationState.DRAFT
    assert book.confirmation_state is ConfirmationState.DRAFT


def test_enum_inputs_serialize_as_original_string_values():
    rule = label_rule(origin=RuleOrigin.EXTRACTED, confirmation_state=ConfirmationState.CONFIRMED)
    book = Rulebook(
        rulebook_id="example", guideline_text=SOURCE, rules=[rule],
        confirmation_state=ConfirmationState.CONFIRMED,
    )
    stored = book.model_dump(mode="json")
    assert stored["confirmation_state"] == "confirmed"
    assert stored["rules"][0]["origin"] == "extracted"
    loaded = Rulebook.model_validate_json(book.model_dump_json())
    assert loaded.confirmation_state is ConfirmationState.CONFIRMED
    assert loaded.rules[0].origin is RuleOrigin.EXTRACTED


@pytest.mark.parametrize("changes", [{"origin": 1}, {"confirmation_state": True}])
def test_non_string_enum_values_are_rejected(changes):
    with pytest.raises(ValidationError):
        label_rule(**changes)
