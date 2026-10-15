"""Keep frontend API fixtures valid, complete and consistent with source models."""

import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator, FormatChecker
import pytest

from backend.app.models.rule import Rulebook
from backend.app.models.review import RulebookAction, RulebookReplace, RulebookReviewPage


API_ROOT = Path(__file__).resolve().parents[1] / "api"
SPEC = json.loads((API_ROOT / "openapi.json").read_text())
EXAMPLES = json.loads((API_ROOT / "examples.json").read_text())
OPERATIONS = [
    (path, method, operation)
    for path, methods in SPEC["paths"].items()
    for method, operation in methods.items()
]


def validate(schema, value):
    document = {"components": SPEC["components"], "allOf": [schema]}
    Draft202012Validator(document, format_checker=FormatChecker()).validate(value)


def media_examples(media):
    if "example" in media:
        return [media["example"]]
    return [example["value"] for example in media.get("examples", {}).values()]


def test_all_routes_are_explicitly_planned_with_unique_operation_ids():
    assert SPEC["openapi"] == "3.1.0"
    identifiers = [operation["operationId"] for _, _, operation in OPERATIONS]
    assert len(identifiers) == len(set(identifiers)) == 48
    assert all(operation["x-implementation-status"] == "planned" for _, _, operation in OPERATIONS)


@pytest.mark.parametrize("path,method,operation", OPERATIONS, ids=[op["operationId"] for _, _, op in OPERATIONS])
def test_operation_schemas_examples_and_path_parameters(path, method, operation):
    declared = [p for p in operation.get("parameters", []) if p["in"] == "path"]
    assert {p["name"] for p in declared} == set(re.findall(r"\{([^}]+)\}", path))
    assert all(p["required"] for p in declared)
    for parameter in operation.get("parameters", []):
        validate(parameter["schema"], parameter["example"])
    for response in operation["responses"].values():
        for media in response.get("content", {}).values():
            assert not ("example" in media and "examples" in media)
            validate_schema = {"components": SPEC["components"], "allOf": [media["schema"]]}
            Draft202012Validator.check_schema(validate_schema)
            for value in media_examples(media):
                validate(media["schema"], value)
    for media in operation.get("requestBody", {}).get("content", {}).values():
        validate(media["schema"], media["example"])


def test_local_schema_references_are_resolvable():
    def visit(value):
        if isinstance(value, dict):
            if "$ref" in value:
                reference = value["$ref"]
                assert reference.startswith("#/components/schemas/")
                assert reference.rsplit("/", 1)[1] in SPEC["components"]["schemas"]
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(SPEC)


def test_frontend_fixtures_match_declared_operations_and_payloads():
    fixture_ids = {fixture["operation_id"] for fixture in EXAMPLES["operations"]}
    assert fixture_ids == {op["operationId"] for _, _, op in OPERATIONS}
    for fixture in EXAMPLES["operations"]:
        path = fixture["path"].removeprefix("/api/v1")
        operation = SPEC["paths"][path][fixture["method"].lower()]
        response = fixture["response"]
        media = operation["responses"][str(response["status"])]["content"][response["content_type"]]
        if response["content_type"] == "application/json":
            assert response["body"] in media_examples(media)
        else:
            assert response["body"] is None
        if operation.get("requestBody"):
            request_media = operation["requestBody"]["content"][fixture["request"]["content_type"]]
            assert fixture["request"]["body"] == request_media["example"]


def test_rulebook_responses_validate_with_existing_python_records():
    for _, _, operation in OPERATIONS:
        for response in operation["responses"].values():
            media = response.get("content", {}).get("application/json", {})
            if media.get("schema", {}).get("$ref") == "#/components/schemas/Rulebook":
                for example in media_examples(media):
                    Rulebook.model_validate_json(json.dumps(example))


def test_rulebook_review_templates_match_service_requests_and_version_semantics():
    for fixture in EXAMPLES["operations"]:
        identifier = fixture["operation_id"]
        if identifier == "replaceDraftRulebook":
            request = RulebookReplace.model_validate_json(json.dumps(fixture["request"]["body"]))
        elif identifier in {"confirmRulebook", "rejectRulebook", "reviseRulebook"}:
            request = RulebookAction.model_validate_json(json.dumps(fixture["request"]["body"]))
        elif identifier == "listRulebookReviews":
            RulebookReviewPage.model_validate_json(json.dumps(fixture["response"]["body"]))
            continue
        else:
            continue
        assert fixture["response"]["body"]["version"] == request.expected_version + 1


@pytest.mark.parametrize("state", ["flagged", "no_issue", "unsure", "unavailable"])
def test_review_scenarios_preserve_human_control_and_rule_references(state):
    sample = EXAMPLES["review_scenarios"][state]
    validate({"$ref": "#/components/schemas/SampleAudit"}, sample)
    assert sample["summary"]["tool_status"] == state
    assert sample["review"]["decision"] == "pending_review"
    assert sample["summary"]["review_status"] == "pending_review"
    counts = sample["summary"]["finding_counts"]
    assert sum(counts.values()) == len(sample["findings"])
    book = Rulebook.model_validate_json(json.dumps(EXAMPLES["review_scenario_rulebooks"][state]))
    rules = {rule.rule_id: rule for rule in book.rules}
    for finding in sample["findings"]:
        assert finding["review"]["resolution"] == "pending"
        assert finding["rulebook_id"] == book.rulebook_id
        assert finding["rulebook_version"] == book.version
        assert finding["rule_id"] in rules
        assert finding["source_sentence"] == rules[finding["rule_id"]].source_sentence


def test_example_measurements_keep_actual_service_semantics():
    report = EXAMPLES["review_scenarios"]["flagged"]["measurements"]
    assert report["status"] == "completed" and report["review_status"] == "pending_review"
    assert report["checks"]["mask_dimensions"]["result"]["matches"]
    assert report["checks"]["labels"]["result"]["invalid_object_ids"] == [2]
    for row in report["objects"]:
        assert row["checks"]["mask_components"]["result"]["component_count"] == 1
        assert row["checks"]["mask_outside_box"]["result"]["outside_fraction"] == 0
        assert row["checks"]["mask_box_fill"]["result"]["fill_fraction"] == 1


def test_completed_job_result_link_points_to_a_declared_resource():
    response = SPEC["paths"]["/jobs/{job_id}"]["get"]["responses"]["200"]["content"]["application/json"]["example"]
    assert response["status"] == "completed"
    href = response["result"]["href"].removeprefix("/api/v1")
    pattern = "/audits/{audit_id}"
    assert href == "/audits/" + response["result"]["id"]
    assert "get" in SPEC["paths"][pattern]


def test_corrected_export_pins_the_rechecked_revision():
    response = SPEC["paths"]["/exports/{export_id}/manifest"]["get"]["responses"]["200"]["content"]["application/json"]["example"]
    assert response["audit_id"] == "audit_recheck_demo"
    assert response["rulebook_version"] == 2
    assert response["entries"][0]["sample_revision"] == 2
    assert response["entries"][0]["review_decision"] == "accepted"
