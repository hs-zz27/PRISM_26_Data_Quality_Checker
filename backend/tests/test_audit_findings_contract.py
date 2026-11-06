"""listAuditFindings: one promoted operation whose contract matches the live route and models."""

import json
from pathlib import Path

import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from jsonschema import Draft202012Validator, FormatChecker

from backend.api.build_contract import build_implemented_contract
from backend.api.common import replace_model_refs
from backend.api.schemas import build_schemas
from backend.app.main import create_app
from backend.app.models.audit_read import FindingPage
from backend.tests.test_rulebook_contract import assert_same_schema

API = Path(__file__).resolve().parents[1] / "api"
PATH = "/audits/{audit_id}/findings"
LIVE_PATH = "/api/v1" + PATH
SPEC = json.loads((API / "openapi.json").read_text(encoding="utf-8"))
IMPLEMENTED = build_implemented_contract(SPEC)
EXAMPLES = json.loads((API / "examples.json").read_text(encoding="utf-8"))
EXAMPLE_NAMES = {"first_page_with_more", "last_page", "filtered_by_sample_and_outcome", "empty"}


def operation(document, path):
    return document["paths"][path]["get"]


def operations(document):
    return [op for methods in document["paths"].values() for op in methods.values()]


# --- registration ------------------------------------------------------------------------------------

def test_the_route_and_operation_id_are_registered_exactly_once():
    contexts = [
        context for context in iter_route_contexts(create_app().routes)
        if isinstance(context.original_route, APIRoute) and context.original_route.include_in_schema
    ]
    assert [(c.path, tuple(sorted(c.methods))) for c in contexts].count((LIVE_PATH, ("GET",))) == 1
    identifiers = [context.original_route.operation_id for context in contexts]
    assert identifiers.count("listAuditFindings") == 1 and len(identifiers) == len(set(identifiers)) == 53


def test_the_operation_has_no_request_body_and_documents_exactly_the_real_statuses():
    for document, path in ((SPEC, PATH), (IMPLEMENTED, LIVE_PATH)):
        found = operation(document, path)
        assert found["operationId"] == "listAuditFindings"
        assert found["x-implementation-status"] == "implemented"
        assert "requestBody" not in found
        assert set(found["responses"]) == {"200", "404", "422", "500", "503"}  # and no 409
        assert found["responses"]["200"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/FindingPage",
        }
        for status in ("404", "422", "500", "503"):
            content = found["responses"][status]["content"]["application/json"]
            assert content["schema"] == {"$ref": "#/components/schemas/ErrorResponse"}


# --- parameters ---------------------------------------------------------------------------------------------

def test_query_parameter_constraints_agree_between_the_generated_and_live_documents():
    generated = {(p["in"], p["name"]): p for p in operation(SPEC, PATH)["parameters"]}
    live = {(p["in"], p["name"]): p for p in operation(IMPLEMENTED, LIVE_PATH)["parameters"]}
    assert set(generated) == set(live) == {
        ("path", "audit_id"), ("query", "limit"), ("query", "offset"),
        ("query", "sample_id"), ("query", "outcome"), ("query", "include_rechecks"),
    }
    for key, parameter in live.items():
        assert generated[key]["required"] == parameter["required"] == (key[0] == "path")
        assert_same_schema(
            generated[key]["schema"], parameter["schema"],
            SPEC["components"]["schemas"], IMPLEMENTED["components"]["schemas"],
        )
        assert generated[key]["example"] == parameter["example"]


def test_the_live_constraints_match_the_documented_semantics():
    live = {p["name"]: p["schema"] for p in operation(IMPLEMENTED, LIVE_PATH)["parameters"]}
    assert (live["limit"]["minimum"], live["limit"]["maximum"], live["limit"]["default"]) == (1, 100, 20)
    assert (live["offset"]["minimum"], live["offset"]["maximum"], live["offset"]["default"]) == (
        0, 9_223_372_036_854_775_807, 0,
    )
    text = next(branch for branch in live["sample_id"]["anyOf"] if branch.get("type") == "string")
    assert text["minLength"] == 1 and text["pattern"] == r"^[^\x00]*[^\s\x00][^\x00]*$"
    assert live["audit_id"]["pattern"] == text["pattern"]
    assert {"type": "null"} in live["sample_id"]["anyOf"] and {"type": "null"} in live["outcome"]["anyOf"]
    outcome = next(branch for branch in live["outcome"]["anyOf"] if "$ref" in branch)
    assert outcome == {"$ref": "#/components/schemas/FindingOutcome"}
    assert IMPLEMENTED["components"]["schemas"]["FindingOutcome"]["enum"] == ["pass", "fail", "unsure", "unavailable"]


@pytest.mark.parametrize("identifier,valid", [
    ("audit_1", True), (" sample_1 ", True), ("sample\n1", True), ("人物_1", True),
    ("sample%00_1", True), ("", False), (" \t\n", False), ("\x00", False),
    ("\x00sample_1", False), ("sample\x00_1", False), ("sample_1\x00", False),
    ("sample_1\x00\n", False),
])
def test_identifier_contracts_reject_nul_without_rejecting_ordinary_text(identifier, valid):
    for document, path in ((SPEC, PATH), (IMPLEMENTED, LIVE_PATH)):
        parameters = {item["name"]: item["schema"] for item in operation(document, path)["parameters"]}
        for name in ("audit_id", "sample_id"):
            assert Draft202012Validator(parameters[name]).is_valid(identifier) is valid


# --- schema fidelity -----------------------------------------------------------------------------------------

def test_the_page_schema_is_derived_from_the_real_model_not_handwritten():
    expected = replace_model_refs(FindingPage.model_json_schema())
    expected.pop("$defs", None)
    assert build_schemas()["FindingPage"] == expected
    assert SPEC["components"]["schemas"]["FindingPage"] == expected
    assert_same_schema(
        SPEC["components"]["schemas"]["FindingPage"], IMPLEMENTED["components"]["schemas"]["FindingPage"],
        SPEC["components"]["schemas"], IMPLEMENTED["components"]["schemas"],
    )


def test_the_page_schema_requires_every_field_and_nullable_next_offset():
    schema = SPEC["components"]["schemas"]["FindingPage"]
    assert set(schema["required"]) == {"items", "limit", "offset", "total", "next_offset"}
    assert schema["additionalProperties"] is False
    assert schema["properties"]["items"]["items"] == {"$ref": "#/components/schemas/Finding"}
    assert (schema["properties"]["limit"]["minimum"], schema["properties"]["limit"]["maximum"]) == (1, 100)
    assert schema["properties"]["offset"]["minimum"] == schema["properties"]["total"]["minimum"] == 0
    assert {"type": "null"} in schema["properties"]["next_offset"]["anyOf"]


# --- examples ---------------------------------------------------------------------------------------------------

def test_examples_validate_against_the_generated_schema_and_the_model():
    for document, path in ((SPEC, PATH), (IMPLEMENTED, LIVE_PATH)):
        media = operation(document, path)["responses"]["200"]["content"]["application/json"]
        assert "example" not in media and set(media["examples"]) == EXAMPLE_NAMES
        for entry in media["examples"].values():
            Draft202012Validator(
                {"components": document["components"], "allOf": [media["schema"]]}, format_checker=FormatChecker(),
            ).validate(entry["value"])
            FindingPage.model_validate_json(json.dumps(entry["value"]))
    values = {
        name: entry["value"] for name, entry in
        operation(SPEC, PATH)["responses"]["200"]["content"]["application/json"]["examples"].items()
    }
    assert (values["first_page_with_more"]["total"], values["first_page_with_more"]["next_offset"]) == (5, 2)
    assert values["last_page"]["next_offset"] is None and values["empty"]["items"] == []
    assert {item["review"]["resolution"] for item in values["first_page_with_more"]["items"]} == {"pending", "accepted"}


def test_the_frontend_fixture_describes_the_implemented_operation():
    entries = [entry for entry in EXAMPLES["operations"] if entry["operation_id"] == "listAuditFindings"]
    assert len(entries) == 1
    entry = entries[0]
    assert (entry["implementation"], entry["method"], entry["path"]) == ("implemented", "GET", LIVE_PATH)
    assert set(entry["request"]["query"]) == {"limit", "offset", "sample_id", "outcome", "include_rechecks"}
    assert entry["request"]["content_type"] is None and entry["request"]["body"] is None
    assert entry["response"]["status"] == 200
    FindingPage.model_validate_json(json.dumps(entry["response"]["body"]))


def test_the_description_states_the_read_semantics_a_frontend_depends_on():
    description = operation(SPEC, PATH)["description"]
    for phrase in (
        "ascending finding_id", "AND", "total counts", "next_offset", "HTTP 404", "empty page",
        "one database snapshot", "never starts a worker", "latest human review",
    ):
        assert phrase in description
    assert operation(IMPLEMENTED, LIVE_PATH)["description"] == description


# --- inventory -----------------------------------------------------------------------------------------------------

def test_the_inventory_is_fifty_implemented_and_nothing_planned():
    for document in (SPEC, IMPLEMENTED):
        found = operations(document)
        assert len(found) == len({op["operationId"] for op in found}) == 53
        assert {op["x-implementation-status"] for op in found} == {"implemented"}
    assert "listAuditFindings" in {op["operation_id"] for op in EXAMPLES["operations"]}
    assert {entry["implementation"] for entry in EXAMPLES["operations"]} == {"implemented"}


def test_existing_finding_review_and_sample_detail_contracts_stay_compatible():
    for identifier in ("listFindingReviews", "reviewFinding", "getAuditSample", "listAuditSamples", "getAudit"):
        found = [op for op in operations(IMPLEMENTED) if op["operationId"] == identifier]
        assert len(found) == 1 and found[0]["x-implementation-status"] == "implemented"
    detail = SPEC["components"]["schemas"]["SampleAudit"]["properties"]["findings"]["items"]
    assert detail == SPEC["components"]["schemas"]["FindingPage"]["properties"]["items"]["items"]
    assert_same_schema(
        SPEC["components"]["schemas"]["Finding"], IMPLEMENTED["components"]["schemas"]["Finding"],
        SPEC["components"]["schemas"], IMPLEMENTED["components"]["schemas"],
    )


# --- freshness ------------------------------------------------------------------------------------------------------------
