"""Exact rulebook operation inventory, schemas and generated/live contracts."""

import json
from pathlib import Path

from fastapi.routing import APIRoute, iter_route_contexts

from backend.api.build_contract import render_artifacts
from backend.api.common import replace_model_refs
from backend.api.schemas import build_schemas
from backend.app.main import create_app
from backend.app.models.review import RulebookAction, RulebookReplace, RulebookReviewPage, RulebookReviewRecord
from backend.app.models.rule import Rule, Rulebook, RulebookPage
from backend.app.models.rulebook_creation import RulebookCreate, RulebookCreateRule
from backend.app.models.rule_extraction import RuleExtractionRequest

API = Path(__file__).resolve().parents[1] / "api"
OPERATIONS = {
    "extractRulebook": ("/rulebooks/extractions", "post", "202", "Job", "RuleExtractionRequest", set()),
    "createRulebook": ("/rulebooks", "post", "201", "Rulebook", "RulebookCreate", {"VERSION_CONFLICT"}),
    "listRulebooks": ("/rulebooks", "get", "200", "RulebookPage", None, set()),
    "getRulebook": ("/rulebooks/{rulebook_id}", "get", "200", "Rulebook", None, set()),
    "listRulebookVersions": ("/rulebooks/{rulebook_id}/versions", "get", "200", "RulebookPage", None, set()),
    "listRulebookReviews": ("/rulebooks/{rulebook_id}/reviews", "get", "200", "RulebookReviewPage", None, set()),
    "replaceDraftRulebook": ("/rulebooks/{rulebook_id}", "put", "200", "Rulebook", "RulebookReplace",
                              {"VERSION_CONFLICT", "INVALID_RULEBOOK_STATE"}),
    "confirmRulebook": ("/rulebooks/{rulebook_id}/confirm", "post", "200", "Rulebook", "RulebookAction",
                         {"VERSION_CONFLICT", "INVALID_RULEBOOK_STATE", "RULEBOOK_NOT_READY"}),
    "rejectRulebook": ("/rulebooks/{rulebook_id}/reject", "post", "200", "Rulebook", "RulebookAction",
                        {"VERSION_CONFLICT", "INVALID_RULEBOOK_STATE"}),
    "reviseRulebook": ("/rulebooks/{rulebook_id}/revisions", "post", "201", "Rulebook", "RulebookAction",
                        {"VERSION_CONFLICT", "INVALID_RULEBOOK_STATE"}),
}

def _resolve(schema, components):
    if "$ref" not in schema:
        return schema
    name = schema["$ref"].rsplit("/", 1)[-1]
    assert name in components
    return {**components[name], **{key: value for key, value in schema.items() if key != "$ref"}}

def assert_same_schema(left, right, left_components, right_components, seen=None):
    """Compare all constraints; permit titles and optional null-default metadata only.

    Follow references instead of renaming schemas or stripping validation keys.
    FastAPI may omit an explicit `default: null`; that is allowed only on an
    actually nullable schema. Required sets are always compared unchanged.
    Non-null defaults, enums, patterns, minimum/maximum and additionalProperties
    are never removed. Recursive JSON-value references are checked once per pair.
    """
    seen = set() if seen is None else seen
    if isinstance(left, dict) and isinstance(right, dict):
        if "$ref" in left and "$ref" in right:
            pair = (left["$ref"], right["$ref"])
            if pair in seen:
                return
            seen = seen | {pair}
        left = _resolve(left, left_components)
        right = _resolve(right, right_components)
        left = {key: value for key, value in left.items() if key != "title"}
        right = {key: value for key, value in right.items() if key != "title"}
        if ("default" in left) != ("default" in right):
            with_default = left if "default" in left else right
            without_default = right if "default" in left else left
            assert with_default["default"] is None
            assert {"type": "null"} in with_default.get("anyOf", [])
            assert {"type": "null"} in without_default.get("anyOf", [])
            left.pop("default", None)
            right.pop("default", None)
        assert set(left) == set(right), (left, right)
        for key in left:
            assert_same_schema(left[key], right[key], left_components, right_components, seen)
    elif isinstance(left, list) and isinstance(right, list):
        assert len(left) == len(right)
        for one, two in zip(left, right):
            assert_same_schema(one, two, left_components, right_components, seen)
    else:
        assert left == right, (left, right)

def test_rulebook_models_are_generated_without_handwritten_page_overrides():
    generated = build_schemas()
    for model in [RuleExtractionRequest, Rule, Rulebook, RulebookPage, RulebookCreate, RulebookCreateRule, RulebookAction, RulebookReplace,
                  RulebookReviewRecord, RulebookReviewPage]:
        expected = model.model_json_schema()
        expected.pop("$defs", None)
        assert generated[model.__name__] == replace_model_refs(expected)
    page = generated["RulebookPage"]
    assert "next_offset" not in page["required"]
    assert page["properties"]["next_offset"]["default"] is None
    assert generated["RulebookAction"]["additionalProperties"] is False

def test_full_generated_implemented_inventory_matches_live_once():
    generated = json.loads((API / "openapi.json").read_text())
    live = create_app().openapi()
    implemented = {(path, method, op["operationId"]) for path, methods in generated["paths"].items()
                   for method, op in methods.items() if op["x-implementation-status"] == "implemented"}
    actual = {(path.removeprefix("/api/v1"), method, op["operationId"])
              for path, methods in live["paths"].items() for method, op in methods.items()}
    assert implemented == actual
    identifiers = [op["operationId"] for methods in live["paths"].values() for op in methods.values()]
    assert len(identifiers) == len(set(identifiers)) == 49
    # OpenAPI dictionaries hide duplicate registrations. Traverse included routers
    # as well as directly registered routes so repeated inclusions remain visible.
    routes = [
        context.original_route.operation_id
        for context in iter_route_contexts(create_app().routes)
        if isinstance(context.original_route, APIRoute)
        and context.original_route.include_in_schema
    ]
    assert len(routes) == len(set(routes)) == 49
    for identifier in OPERATIONS:
        assert routes.count(identifier) == 1
    assert generated["paths"]["/rulebooks"]["post"]["x-implementation-status"] == "implemented"
    assert generated["paths"]["/rulebooks/extractions"]["post"]["x-implementation-status"] == "implemented"

def test_rulebook_live_generated_operations_match_without_constraint_normalization():
    generated = json.loads((API / "openapi.json").read_text())
    live = create_app().openapi()
    gc, lc = generated["components"]["schemas"], live["components"]["schemas"]
    for identifier, (path, method, success, response_model, request_model, conflicts) in OPERATIONS.items():
        one, two = generated["paths"][path][method], live["paths"]["/api/v1" + path][method]
        assert one["operationId"] == two["operationId"] == identifier
        expected_statuses = {success, "404", "422", "500", "503"} | ({"409"} if conflicts else set())
        assert set(one["responses"]) == set(two["responses"]) == expected_statuses
        assert one["responses"][success]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/" + response_model,
        }
        for status in expected_statuses:
            assert_same_schema(one["responses"][status]["content"]["application/json"]["schema"],
                               two["responses"][status]["content"]["application/json"]["schema"], gc, lc)
        first = {(p["in"], p["name"]): p for p in one.get("parameters", [])}
        second = {(p["in"], p["name"]): p for p in two.get("parameters", [])}
        assert set(first) == set(second)
        for key in first:
            assert first[key]["required"] == second[key]["required"]
            assert_same_schema(first[key]["schema"], second[key]["schema"], gc, lc)
        assert ("requestBody" in one) == ("requestBody" in two) == (request_model is not None)
        if request_model:
            assert one["requestBody"]["required"] is two["requestBody"]["required"] is True
            assert_same_schema(one["requestBody"]["content"]["application/json"]["schema"],
                               two["requestBody"]["content"]["application/json"]["schema"], gc, lc)
        if conflicts:
            for op in [one, two]:
                examples = op["responses"]["409"]["content"]["application/json"]["examples"]
                assert set(examples) == conflicts
                assert {item["value"]["error"]["code"] for item in examples.values()} == conflicts

def test_rulebook_and_existing_generated_artifacts_are_fresh():
    for name, expected in render_artifacts().items():
        assert (API / name).read_text(encoding="utf-8") == expected
