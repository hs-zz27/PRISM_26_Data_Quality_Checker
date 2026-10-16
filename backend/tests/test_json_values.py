"""Finite numeric values at both Python and JSON model boundaries."""

import json

from pydantic import ValidationError
import pytest

from backend.app.models.finding import Evidence, Finding
from backend.app.models.rule import Rule, RuleProposal


RECORDS = [
    (RuleProposal, "parameters", {"rule_id": "r1", "description": "A rule.", "source_sentence": "A rule."}),
    (Rule, "parameters", {"rule_id": "r1", "description": "A rule.", "origin": "reviewer"}),
    (Evidence, "measurements", {}),
    (Finding, "parameters", {
        "finding_id": "f1", "audit_id": "a1", "sample_id": "s1", "sample_revision": 1,
        "rulebook_id": "b1", "rulebook_version": 1, "rule_id": "r1",
        "description": "A rule.", "outcome": "unavailable", "origin": "reviewer",
    }),
]


@pytest.mark.parametrize("model,field,base", RECORDS, ids=[row[0].__name__ for row in RECORDS])
@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("nested", [False, True])
def test_nonfinite_numbers_are_rejected_from_python_and_json(model, field, base, number, nested):
    value = {"outer": [{"number": number}]} if nested else number
    data = {**base, field: {"value": value}}
    with pytest.raises(ValidationError, match="must be finite"):
        model.model_validate(data)
    # Python's encoder can emit these extensions; input must still reject them.
    with pytest.raises(ValidationError, match="must be finite"):
        model.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("model,field,base", RECORDS, ids=[row[0].__name__ for row in RECORDS])
def test_valid_nested_json_preserves_values_and_round_trips(model, field, base):
    values = {"nested": [None, True, 0, -4, 0.125, {"label": "person"}]}
    record = model.model_validate({**base, field: values})
    assert getattr(record, field) == values
    assert model.model_validate_json(record.model_dump_json()) == record
    json.dumps(record.model_dump(mode="json"), allow_nan=False)
