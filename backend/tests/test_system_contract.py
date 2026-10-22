"""The generated contract promotes exactly the three system operations from the live models."""

import json
from pathlib import Path

import pytest

pytest.importorskip("fcntl", reason="the contract generator imports the full app, which needs POSIX file locking")

from jsonschema import Draft202012Validator, FormatChecker  # noqa: E402

from backend.api.build_contract import build_contract  # noqa: E402
from backend.api.system_contract import EXPECTED, system_examples  # noqa: E402
from backend.app.main import create_app  # noqa: E402
from backend.tests.test_rulebook_contract import assert_same_schema  # noqa: E402

API = Path(__file__).resolve().parents[1] / "api"
SYSTEM = {identifier: key for key, identifier in EXPECTED.items()}


def operations(document):
    return [op for methods in document["paths"].values() for op in methods.values()]


def test_the_three_system_operations_are_promoted_once_and_match_the_live_routes():
    generated, live = build_contract(), create_app().openapi()
    for identifier, (path, method) in SYSTEM.items():
        one, two = generated["paths"][path][method], live["paths"]["/api/v1" + path][method]
        assert one["operationId"] == two["operationId"] == identifier
        assert one["x-implementation-status"] == "implemented"
        assert set(one["responses"]) == set(two["responses"]) == {"200", "500"}
        assert "parameters" not in one and "requestBody" not in one


def test_the_inventory_is_50_implemented_and_nothing_planned():
    document = build_contract()
    ops = operations(document)
    assert len(ops) == len({op["operationId"] for op in ops}) == 50
    assert sum(op["x-implementation-status"] == "implemented" for op in ops) == 50
    planned = {op["operationId"] for op in ops if op["x-implementation-status"] == "planned"}
    assert planned == set()


def test_system_schemas_come_from_the_live_models_not_handwritten_copies():
    generated, live = build_contract(), create_app().openapi()
    for name in ("Health", "Capabilities", "SystemStatus", "OllamaStatus", "DetectorStatus"):
        assert_same_schema(
            generated["components"]["schemas"][name], live["components"]["schemas"][name],
            generated["components"]["schemas"], live["components"]["schemas"],
        )


def test_system_examples_validate_against_the_generated_schemas():
    document = build_contract()
    for identifier, (path, method) in SYSTEM.items():
        schema = document["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"]
        example = document["paths"][path][method]["responses"]["200"]["content"]["application/json"]["example"]
        Draft202012Validator(
            {"components": document["components"], "allOf": [schema]}, format_checker=FormatChecker(),
        ).validate(example)
        assert example == system_examples()[identifier]
        json.dumps(example, allow_nan=False)


def test_the_planned_reference_no_longer_lists_the_system_operations():
    guide = (API / "PLANNED_ENDPOINTS.md").read_text(encoding="utf-8")
    for identifier in SYSTEM:
        assert "\n## " + identifier + "\n" not in guide
