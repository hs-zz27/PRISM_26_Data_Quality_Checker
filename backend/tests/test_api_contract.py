"""Keep frontend API fixtures valid, complete and consistent with source models."""

import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator, FormatChecker
import pytest

from backend.app.models.rule import Rulebook
from backend.app.models.finding import Finding, FindingReview
from backend.app.models.review import (
    FindingReviewPage, FindingReviewRequest, RulebookAction, RulebookReplace, RulebookReviewPage,
    SampleReview, SampleReviewPage, SampleReviewRequest,
)
from backend.api.build_contract import render_artifacts


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


def test_routes_distinguish_implemented_handlers_from_planned_operations():
    assert SPEC["openapi"] == "3.1.0"
    identifiers = [operation["operationId"] for _, _, operation in OPERATIONS]
    assert len(identifiers) == len(set(identifiers)) == 49
    implemented = {
        "listAuditSamples", "getAuditSample", "reviewFinding", "listFindingReviews",
        "reviewSample", "listSampleReviews",
    }
    for _, _, operation in OPERATIONS:
        expected = "implemented" if operation["operationId"] in implemented else "planned"
        assert operation["x-implementation-status"] == expected


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


def test_finding_review_template_matches_service_request_and_revision():
    fixture = next(item for item in EXAMPLES["operations"] if item["operation_id"] == "reviewFinding")
    request = FindingReviewRequest.model_validate_json(json.dumps(fixture["request"]["body"]))
    response = FindingReview.model_validate_json(json.dumps(fixture["response"]["body"]))

    assert response.revision == request.expected_review_version + 1
    assert response.resolution == request.resolution
    assert response.reviewer_id == request.reviewer_id
    assert response.comment == request.comment
    assert response.updated_at is not None


def test_finding_review_history_template_uses_attributed_records_and_pagination():
    operations = {item["operation_id"]: item for item in EXAMPLES["operations"]}
    write = operations["reviewFinding"]
    history = operations["listFindingReviews"]
    response = json.loads(json.dumps(write["response"]["body"]))
    page = FindingReviewPage.model_validate_json(json.dumps(history["response"]["body"]))

    assert page.items[0].finding_id == history["request"]["path_parameters"]["finding_id"]
    assert page.items[0].model_dump(mode="json") == {
        **response,
        "finding_id": history["request"]["path_parameters"]["finding_id"],
    }
    assert history["request"]["query"] == {"limit": 20, "offset": 0}
    parameters = SPEC["paths"]["/findings/{finding_id}/reviews"]["get"]["parameters"]
    assert {item["name"] for item in parameters} == {"finding_id", "limit", "offset"}
    assert all(item["required"] for item in parameters if item["in"] == "path")


def test_review_routes_document_live_error_statuses():
    review_operations = {
        "reviewFinding", "listFindingReviews", "reviewSample", "listSampleReviews",
    }
    expected = {"200", "404", "422", "500", "503"}
    for _, _, operation in OPERATIONS:
        if operation["operationId"] in review_operations:
            conflicts = set() if operation["operationId"] == "listFindingReviews" else {"409"}
            assert set(operation["responses"]) == expected | conflicts


def test_review_conflict_examples_match_the_errors_each_operation_can_return():
    expected_codes = {
        "reviewFinding": {"VERSION_CONFLICT"},
        "reviewSample": {"VERSION_CONFLICT", "STALE_SAMPLE_REVISION", "INVALID_SAMPLE_REVIEW_STATE"},
        "listSampleReviews": {"STALE_SAMPLE_REVISION"},
    }
    for _, _, operation in OPERATIONS:
        operation_id = operation["operationId"]
        if operation_id not in expected_codes:
            continue
        media = operation["responses"]["409"]["content"]["application/json"]
        codes = {example["error"]["code"] for example in media_examples(media)}
        assert codes == expected_codes[operation_id]


def test_finding_examples_validate_with_the_python_record():
    validated = 0
    collection_fields = {
        "#/components/schemas/FindingPage": "items",
        "#/components/schemas/SampleAudit": "findings",
    }
    for _, _, operation in OPERATIONS:
        for response in operation["responses"].values():
            media = response.get("content", {}).get("application/json", {})
            field = collection_fields.get(media.get("schema", {}).get("$ref"))
            if field is not None:
                for example in media_examples(media):
                    for finding in example[field]:
                        Finding.model_validate_json(json.dumps(finding))
                        validated += 1
    assert validated > 0
    for sample in EXAMPLES["review_scenarios"].values():
        for finding in sample["findings"]:
            Finding.model_validate_json(json.dumps(finding))


def test_sample_review_templates_use_distinct_sample_and_review_revisions():
    operations = {item["operation_id"]: item for item in EXAMPLES["operations"]}
    fixture = operations["reviewSample"]
    request = SampleReviewRequest.model_validate_json(json.dumps(fixture["request"]["body"]))
    response = SampleReview.model_validate_json(json.dumps(fixture["response"]["body"]))
    assert response.revision == request.expected_review_version + 1
    assert response.sample_revision == request.expected_sample_revision
    assert response.audit_id == fixture["request"]["path_parameters"]["audit_id"]
    assert response.sample_id == fixture["request"]["path_parameters"]["sample_id"]
    assert response.decision == request.decision
    assert response.reviewer_id == request.reviewer_id and response.comment == request.comment
    history = operations["listSampleReviews"]
    page = SampleReviewPage.model_validate_json(json.dumps(history["response"]["body"]))
    assert page.items[0].model_dump() == response.model_dump()
    assert history["request"]["query"]["sample_revision"] == response.sample_revision
    parameters = SPEC["paths"]["/audits/{audit_id}/samples/{sample_id}/reviews"]["get"]["parameters"]
    assert next(item for item in parameters if item["name"] == "sample_revision")["required"]
    for state in EXAMPLES["review_scenarios"].values():
        current = SampleReview.model_validate_json(json.dumps(state["review"]))
        assert current.audit_id == state["audit_id"]
        assert current.sample_id == state["summary"]["sample_id"]
        assert current.sample_revision == state["summary"]["sample_revision"]
        assert current.revision == 0 and current.reviewer_id is None


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
    assert response["rulebook_version"] == 3
    assert response["entries"][0]["sample_revision"] == 2
    assert response["entries"][0]["review_decision"] == "accepted"


def test_generated_artifacts_match_the_current_builder():
    for filename, expected in render_artifacts().items():
        assert (API_ROOT / filename).read_text(encoding="utf-8") == expected, (
            f"Regenerate {filename} with python -m backend.api.build_contract"
        )


def test_audits_and_exports_use_the_version_returned_by_confirmation():
    operations = {fixture["operation_id"]: fixture for fixture in EXAMPLES["operations"]}
    reviewed = operations["replaceDraftRulebook"]["response"]["body"]
    confirmed = operations["confirmRulebook"]["response"]["body"]
    assert reviewed["version"] == 2 and reviewed["confirmation_state"] == "draft"
    assert confirmed["version"] == 3 and confirmed["confirmation_state"] == "confirmed"
    assert operations["confirmRulebook"]["request"]["body"]["expected_version"] == reviewed["version"]
    assert operations["startAudit"]["request"]["body"]["rulebook_version"] == confirmed["version"]
    for identifier in ("startAudit", "recheckSamples"):
        assert operations[identifier]["response"]["body"]["audit"]["rulebook_version"] == confirmed["version"]
    assert operations["getExportManifest"]["response"]["body"]["rulebook_version"] == confirmed["version"]
    history = operations["listRulebookVersions"]["response"]["body"]["items"]
    assert [book["version"] for book in history] == [1, 2, 3]
    assert history[1] == reviewed and history[2] == confirmed
    for finding in EXAMPLES["review_scenarios"]["flagged"]["findings"]:
        assert finding["rulebook_version"] == confirmed["version"]
