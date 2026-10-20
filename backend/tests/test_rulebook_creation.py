"""Manual creation validation and persistence boundaries; no database or inference."""

import ast
from copy import deepcopy
import inspect
import json
from unittest.mock import Mock, call
from uuid import UUID

from pydantic import ValidationError
import pytest

from backend.app.models.rule import ConfirmationState, Rule, Rulebook, RuleOrigin
from backend.app.models.rulebook_creation import RulebookCreate, RulebookCreateRule
from backend.app.services import rulebook_creation as creation_module
from backend.app.services.rulebook_creation import create_rulebook
from backend.app.storage.repository import (
    RulebookRepository, RulebookStorageError, RulebookVersionConflictError,
)

SOURCE = " \tAt most 5% may lie outside.\r\n保留 café e\u0301 🙂\n "
CITATION = "At most 5% may lie outside."

def payload():
    return {
        "guideline_text": SOURCE,
        "rules": [{
            "rule_id": "MASK-1",
            "description": "  Preserve this reviewer-authored description.  ",
            "check_name": "mask_outside_box",
            "parameters": {
                "max_outside_fraction": 0.05,
                "nested": [None, True, 0, -4, 0.125, {"labels": ["人", "café"]}],
            },
            "source_sentence": CITATION,
        }],
    }

@pytest.fixture
def repository():
    store = Mock(spec_set=RulebookRepository)
    store.save_rulebook.side_effect = lambda draft, **kwargs: Rulebook.model_validate(draft.model_dump())
    return store

def assert_only_initial_save(repository, result):
    repository.save_rulebook.assert_called_once_with(result, expected_version=0)
    assert repository.method_calls == [call.save_rulebook(result, expected_version=0)]

def test_valid_creation_preserves_content_and_server_metadata(repository):
    values = payload()
    request = RulebookCreate.model_validate(values)
    before = request.model_dump()

    result = create_rulebook(request, repository=repository)

    assert result.rulebook_id.startswith("rulebook_")
    generated = UUID(hex=result.rulebook_id.removeprefix("rulebook_"))
    assert generated.version == 4
    assert result.rulebook_id == f"rulebook_{generated.hex}"
    assert result.version == 1
    assert result.confirmation_state is ConfirmationState.DRAFT
    assert result.guideline_text == SOURCE
    assert result.guideline_text.encode("utf-8") == SOURCE.encode("utf-8")
    rule = result.rules[0]
    assert rule.origin is RuleOrigin.REVIEWER
    assert rule.confirmation_state is ConfirmationState.DRAFT
    assert {name: getattr(rule, name) for name in values["rules"][0]} == values["rules"][0]
    assert request.model_dump() == before
    submitted = repository.save_rulebook.call_args.args[0]
    assert result is not submitted
    assert result.rules[0] is not submitted.rules[0]
    assert submitted.rules[0] is not request.rules[0]
    assert result.rules[0].parameters is not request.rules[0].parameters
    assert_only_initial_save(repository, result)

@pytest.mark.parametrize("values", [
    {"guideline_text": ""},
    {"guideline_text": "", "rules": []},
    {"guideline_text": " \r\n\t", "rules": []},
    {"guideline_text": SOURCE, "rules": []},
    {"guideline_text": "", "rules": [{"rule_id": "manual", "description": "Review manually."}]},
])
def test_empty_drafts_and_uncited_manual_rules_are_allowed(repository, values):
    result = create_rulebook(RulebookCreate.model_validate(values), repository=repository)
    assert result.guideline_text == values["guideline_text"]
    assert len(result.rules) == len(values.get("rules", []))
    assert result.confirmation_state is ConfirmationState.DRAFT
    assert all(rule.confirmation_state is ConfirmationState.DRAFT for rule in result.rules)
    assert_only_initial_save(repository, result)

def test_json_round_trip_preserves_finite_parameter_types():
    request = RulebookCreate.model_validate_json(json.dumps(payload(), ensure_ascii=False))
    nested = request.rules[0].parameters["nested"]
    assert [type(value) for value in nested] == [type(None), bool, int, int, float, dict]
    assert request.guideline_text == SOURCE
    assert RulebookCreate.model_validate_json(request.model_dump_json()) == request
    json.dumps(request.model_dump(mode="json"), allow_nan=False)

def test_input_defaults_are_independent():
    first = RulebookCreate(guideline_text="")
    second = RulebookCreate(guideline_text="")
    first_rule = RulebookCreateRule(rule_id="one", description="One.")
    second_rule = RulebookCreateRule(rule_id="two", description="Two.")
    assert first_rule.check_name is None and first_rule.source_sentence is None
    first.rules.append(first_rule)
    first_rule.parameters["nested"] = ["changed"]
    assert second.rules == []
    assert second_rule.parameters == {}

@pytest.mark.parametrize("values", [
    {}, {"guideline_text": None}, {"guideline_text": 1},
    {"guideline_text": True}, {"guideline_text": b"text"},
    {"guideline_text": "", "rules": None},
    {"guideline_text": "", "rules": {}},
    {"guideline_text": "", "rules": ()},
    {"guideline_text": "", "rules": [None]},
    {"guideline_text": "", "rules": [{}]},
])
def test_invalid_request_shapes_never_reach_storage(repository, values):
    with pytest.raises(ValidationError):
        request = RulebookCreate.model_validate(values)
        create_rulebook(request, repository=repository)
    assert repository.method_calls == []

@pytest.mark.parametrize("field,value", [
    ("rule_id", ""), ("rule_id", " \t\r\n"), ("rule_id", 1), ("rule_id", None),
    ("description", ""), ("description", "\n\t"), ("description", False),
    ("description", None), ("check_name", ""), ("check_name", " \n"),
    ("check_name", 1), ("source_sentence", ""), ("source_sentence", " \t"),
    ("source_sentence", 1), ("parameters", None), ("parameters", []),
    ("parameters", {1: "not a string key"}), ("parameters", {"not_json": {1, 2}}),
    ("parameters", {"not_json": object()}),
])
def test_invalid_rule_fields_never_reach_storage(repository, field, value):
    values = payload()
    values["rules"][0][field] = value
    with pytest.raises(ValidationError):
        request = RulebookCreate.model_validate(values)
        create_rulebook(request, repository=repository)
    assert repository.method_calls == []

@pytest.mark.parametrize("field", ["rule_id", "description"])
def test_required_rule_fields_cannot_be_omitted(repository, field):
    values = payload()
    del values["rules"][0][field]
    with pytest.raises(ValidationError):
        request = RulebookCreate.model_validate(values)
        create_rulebook(request, repository=repository)
    assert repository.method_calls == []

@pytest.mark.parametrize("level", ["book", "rule"])
@pytest.mark.parametrize("field,value", [
    ("unknown", "forbidden"), ("rulebook_id", "chosen_by_client"),
    ("version", 9), ("origin", "extracted"),
    ("confirmation_state", "confirmed"), ("review_history", []),
    ("reviews", []), ("created_at", "2026-10-20T00:00:00Z"),
    ("reviewer_id", "not_a_creation_review"),
])
def test_unknown_and_server_owned_fields_are_forbidden(repository, level, field, value):
    values = payload()
    target = values if level == "book" else values["rules"][0]
    target[field] = value
    with pytest.raises(ValidationError):
        request = RulebookCreate.model_validate(values)
        create_rulebook(request, repository=repository)
    assert repository.method_calls == []

def test_persisted_rule_is_not_an_input_rule(repository):
    persisted = Rule(rule_id="one", description="One.", origin=RuleOrigin.REVIEWER)
    for item in (persisted, persisted.model_dump()):
        with pytest.raises(ValidationError):
            request = RulebookCreate(guideline_text="", rules=[item])
            create_rulebook(request, repository=repository)
    assert repository.method_calls == []

@pytest.mark.parametrize("change", ["duplicate", "absent", "case", "line_endings", "unicode"])
def test_aggregate_content_errors_are_request_validation_errors(repository, change):
    values = payload()
    if change == "duplicate":
        values["rules"].append(deepcopy(values["rules"][0]))
    else:
        values["rules"][0]["source_sentence"] = {
            "absent": "Invented citation.",
            "case": CITATION.lower(),
            "line_endings": SOURCE.replace("\r\n", "\n"),
            "unicode": "café é",  # Source contains decomposed e + combining acute.
        }[change]
    with pytest.raises(ValidationError):
        request = RulebookCreate.model_validate(values)
        create_rulebook(request, repository=repository)
    assert repository.method_calls == []

@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("nested", [False, True])
def test_nonfinite_parameters_fail_in_python_and_json_before_storage(repository, number, nested):
    values = payload()
    values["rules"][0]["parameters"] = {
        "value": {"outer": [{"number": number}]} if nested else number,
    }
    for validate in (RulebookCreate.model_validate, lambda data: RulebookCreate.model_validate_json(json.dumps(data))):
        with pytest.raises(ValidationError, match="must be finite"):
            request = validate(values)
            create_rulebook(request, repository=repository)
    assert repository.method_calls == []

@pytest.mark.parametrize("change", [
    "guideline_type", "rules_null", "rules_tuple", "bad_item", "blank_id",
    "description_type", "parameters_null", "nested_nonfinite", "nested_not_json",
    "duplicate", "citation", "removed_guidelines", "nested_constructed",
])
def test_mutated_requests_are_revalidated_before_any_repository_call(repository, change):
    request = RulebookCreate.model_validate(payload())
    if change == "guideline_type":
        request.guideline_text = 123
    elif change == "rules_null":
        request.rules = None
    elif change == "rules_tuple":
        request.rules = tuple(request.rules)
    elif change == "bad_item":
        request.rules.append(None)
    elif change == "blank_id":
        request.rules[0].rule_id = " \t"
    elif change == "description_type":
        request.rules[0].description = 123
    elif change == "parameters_null":
        request.rules[0].parameters = None
    elif change == "nested_nonfinite":
        request.rules[0].parameters["nested"].append({"bad": float("nan")})
    elif change == "nested_not_json":
        request.rules[0].parameters["nested"].append(object())
    elif change == "duplicate":
        request.rules.append(request.rules[0])
    elif change == "citation":
        request.rules[0].source_sentence = "Not in the guidelines."
    elif change == "removed_guidelines":
        request.guideline_text = ""
    else:
        # Deliberately bypass validation to prove the service does not trust it.
        request.rules[0] = RulebookCreateRule.model_construct(rule_id="bad", description=123)
    with pytest.raises(ValidationError):
        create_rulebook(request, repository=repository)
    assert repository.method_calls == []

@pytest.mark.parametrize("level", ["book", "rule"])
def test_unvalidated_model_copy_cannot_smuggle_server_fields(repository, level):
    request = RulebookCreate.model_validate(payload())
    if level == "book":
        request = request.model_copy(update={"rulebook_id": "client_chosen"})
    else:
        request.rules[0] = request.rules[0].model_copy(update={"origin": "extracted"})
    with pytest.raises(ValidationError):
        create_rulebook(request, repository=repository)
    assert repository.method_calls == []

def test_outer_constructed_model_is_revalidated(repository):
    request = RulebookCreate.model_construct(guideline_text=123, rules=[])
    with pytest.raises(ValidationError):
        create_rulebook(request, repository=repository)
    assert repository.method_calls == []

@pytest.mark.parametrize("candidate", [None, {}, "text", object()])
def test_service_requires_the_typed_request(repository, candidate):
    with pytest.raises(TypeError, match="RulebookCreate instance"):
        create_rulebook(candidate, repository=repository)
    assert repository.method_calls == []

@pytest.mark.parametrize("check_name", [None, "future_unimplemented_check"])
def test_unsupported_and_null_checks_remain_for_review(repository, check_name):
    request = RulebookCreate(
        guideline_text="", rules=[RulebookCreateRule(
            rule_id="manual", description="Human review needed.", check_name=check_name,
        )],
    )
    result = create_rulebook(request, repository=repository)
    assert result.rules[0].check_name == check_name
    assert result.rules[0].parameters == {}  # No invented labels or thresholds.
    assert result.rules[0].source_sentence is None
    assert result.rules[0].confirmation_state is ConfirmationState.DRAFT
    assert_only_initial_save(repository, result)

def test_separate_creations_and_nested_contents_are_independent(repository):
    values = payload()
    request = RulebookCreate.model_validate(values)
    first = create_rulebook(request, repository=repository)
    second = create_rulebook(request, repository=repository)
    drafts = [entry.args[0] for entry in repository.save_rulebook.call_args_list]
    assert first.rulebook_id != second.rulebook_id
    assert first.rules[0] is not second.rules[0]
    assert drafts[0].rules[0].parameters is not drafts[1].rules[0].parameters
    values["rules"][0]["parameters"]["nested"][-1]["labels"].append("source-only")
    request.rules[0].parameters["nested"][-1]["labels"].append("request-only")
    first.rules[0].parameters["nested"][-1]["labels"].append("first-only")
    for draft in drafts:
        assert draft.rules[0].parameters["nested"][-1]["labels"] == ["人", "café"]
    assert second.rules[0].parameters["nested"][-1]["labels"] == ["人", "café"]
    assert repository.method_calls == [
        call.save_rulebook(drafts[0], expected_version=0),
        call.save_rulebook(drafts[1], expected_version=0),
    ]

def test_returns_the_exact_snapshot_supplied_after_repository_success(repository):
    returned = []

    def commit(draft, *, expected_version):
        assert expected_version == 0
        snapshot = Rulebook.model_validate(draft.model_dump())
        returned.append(snapshot)
        return snapshot

    repository.save_rulebook.side_effect = commit
    result = create_rulebook(RulebookCreate.model_validate(payload()), repository=repository)
    assert result is returned[0]
    assert result is not repository.save_rulebook.call_args.args[0]
    assert_only_initial_save(repository, result)

@pytest.mark.parametrize("kind", ["storage", "commit", "value", "validation"])
def test_repository_errors_propagate_unchanged_without_retry_or_success(repository, kind):
    if kind == "validation":
        with pytest.raises(ValidationError) as caught:
            Rulebook.model_validate({})
        error = caught.value
    elif kind == "value":
        error = ValueError("Repository defect, not submitted-content validation.")
    else:
        error = RulebookStorageError(f"Synthetic {kind} failure.")
    repository.save_rulebook.side_effect = error
    with pytest.raises(type(error)) as caught:
        create_rulebook(RulebookCreate.model_validate(payload()), repository=repository)
    assert caught.value is error
    assert repository.save_rulebook.call_count == 1
    assert len(repository.method_calls) == 1
    assert repository.method_calls[0][0] == "save_rulebook"
    assert repository.save_rulebook.call_args.kwargs == {"expected_version": 0}

def test_generated_id_conflict_propagates_without_advancing_or_retrying(repository, monkeypatch):
    fixed = UUID("12345678-1234-4234-8123-1234567890ab")
    generator = Mock(return_value=fixed)
    monkeypatch.setattr(creation_module, "uuid4", generator)
    identifier = f"rulebook_{fixed.hex}"
    error = RulebookVersionConflictError(identifier, 0, 3)
    repository.save_rulebook.side_effect = error
    with pytest.raises(RulebookVersionConflictError) as caught:
        create_rulebook(RulebookCreate.model_validate(payload()), repository=repository)
    assert caught.value is error
    generator.assert_called_once_with()
    draft = repository.save_rulebook.call_args.args[0]
    assert draft.rulebook_id == identifier and draft.version == 1
    assert repository.method_calls == [call.save_rulebook(draft, expected_version=0)]

def test_creation_service_does_not_import_execution_or_review_workflows():
    """Keep the new service independent of inference and workflow side effects."""
    tree = ast.parse(inspect.getsource(creation_module))
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert all(isinstance(node, ast.ImportFrom) for node in imports)
    assert {node.module for node in imports} == {
        "__future__", "typing", "uuid", "models.rule", "models.rulebook_creation", "storage.repository",
    }
    guarded = next(node for node in tree.body if isinstance(node, ast.If))
    assert isinstance(guarded.test, ast.Name) and guarded.test.id == "TYPE_CHECKING"
    assert any(isinstance(node, ast.ImportFrom) and node.module == "storage.repository" for node in guarded.body)
    # On every service success above, method_calls also excludes review/history,
    # reads and schema initialization: the only repository call is the initial save.

def test_large_json_exponent_cannot_overflow_to_a_nonfinite_parameter(repository):
    raw = '{"guideline_text":"","rules":[{"rule_id":"r","description":"R.","parameters":{"nested":[1e999]}}]}'
    with pytest.raises(ValidationError, match="must be finite"):
        request = RulebookCreate.model_validate_json(raw)
        create_rulebook(request, repository=repository)
    assert repository.method_calls == []

def test_metadata_like_parameter_keys_and_nonblank_text_are_not_overrestricted(repository):
    values = {"guideline_text": " exact citation ", "rules": [{
        "rule_id": " Rule-1 ", "description": " Description with spaces. ",
        "check_name": " future_check ", "source_sentence": " exact citation ",
        "parameters": {"origin": "parameter data", "version": 7, "confirmation_state": None},
    }]}
    result = create_rulebook(RulebookCreate.model_validate(values), repository=repository)
    assert result.rules[0].rule_id == " Rule-1 "
    assert result.rules[0].description == " Description with spaces. "
    assert result.rules[0].check_name == " future_check "
    assert result.rules[0].source_sentence == " exact citation "
    assert result.rules[0].parameters == values["rules"][0]["parameters"]
    assert result.rules[0].origin is RuleOrigin.REVIEWER
    assert_only_initial_save(repository, result)

def test_multiple_rules_keep_editor_order_and_exact_distinct_ids(repository):
    shared = {"nested": [{"labels": ["人", "person"]}]}
    identifiers = ["Rule", "rule", " Rule "]
    request = RulebookCreate(guideline_text=SOURCE, rules=[
        RulebookCreateRule(
            rule_id=identifier, description=f"Review {identifier}.",
            parameters=shared, source_sentence=CITATION,
        ) for identifier in identifiers
    ])
    result = create_rulebook(request, repository=repository)
    assert [rule.rule_id for rule in result.rules] == identifiers
    assert [rule.description for rule in result.rules] == [f"Review {identifier}." for identifier in identifiers]
    assert all(rule.source_sentence == CITATION and rule.check_name is None for rule in result.rules)
    draft = repository.save_rulebook.call_args.args[0]
    result.rules[0].parameters["nested"][0]["labels"].append("result-only")
    request.rules[1].parameters["nested"][0]["labels"].append("request-only")
    assert shared == {"nested": [{"labels": ["人", "person"]}]}
    assert all(rule.parameters == shared for rule in draft.rules)
    assert all(rule.parameters == shared for rule in result.rules[1:])
    assert repository.method_calls == [call.save_rulebook(draft, expected_version=0)]

def test_failed_save_does_not_mutate_or_poison_a_later_explicit_creation(repository, monkeypatch):
    request = RulebookCreate.model_validate(payload())
    original = request.model_dump()
    identifiers = [UUID("12345678-1234-4234-8123-1234567890ab"),
                   UUID("12345678-1234-4234-8123-1234567890ac")]
    generator = Mock(side_effect=identifiers)
    monkeypatch.setattr(creation_module, "uuid4", generator)
    repository.save_rulebook.side_effect = RulebookStorageError("Synthetic failed commit.")
    with pytest.raises(RulebookStorageError):
        create_rulebook(request, repository=repository)
    assert request.model_dump() == original and repository.save_rulebook.call_count == 1
    # This is a new explicit invocation, never an implicit retry by the service.
    repository.save_rulebook.side_effect = lambda draft, **kwargs: Rulebook.model_validate(draft.model_dump())
    result = create_rulebook(request, repository=repository)
    drafts = [entry.args[0] for entry in repository.save_rulebook.call_args_list]
    assert [draft.rulebook_id for draft in drafts] == [f"rulebook_{identifier.hex}" for identifier in identifiers]
    assert result.rulebook_id == drafts[1].rulebook_id
    assert result.guideline_text == SOURCE and request.model_dump() == original
    assert generator.call_count == 2
    assert repository.method_calls == [call.save_rulebook(draft, expected_version=0) for draft in drafts]
