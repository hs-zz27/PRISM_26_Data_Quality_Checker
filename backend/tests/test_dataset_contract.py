"""Authored Phase 4 contract regressions. Not executed during source handoff."""
from copy import deepcopy
import json
from pathlib import Path
from urllib.parse import quote

from fastapi.routing import APIRoute, iter_route_contexts
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError
import pytest

from backend.api.build_contract import build_contract, render_artifacts
from backend.api.common import replace_model_refs
from backend.api.dataset_contract import (
    DATASET_OPERATIONS, align_dataset_contract, schema_closure, structural,
)
from backend.api.dataset_examples import build_dataset_import_scenarios
from backend.api.schemas import build_schemas
from backend.app.core.dataset_http import DatasetUpload
from backend.app.core.dataset_import import ERROR_MESSAGES
from backend.app.main import create_app
from backend.app.models.dataset import Dataset, DatasetPage, DatasetImportStarted, Sample, SamplePage
from backend.app.models.job import Job, JobStatus

API = Path(__file__).resolve().parents[1] / "api"
FILES = {"openapi.json", "implemented-openapi.json", "examples.json", "ENDPOINTS.md", "PLANNED_ENDPOINTS.md"}


def _validate(document, schema, value):
    Draft202012Validator({"components": document["components"], "allOf": [schema]},
                        format_checker=FormatChecker()).validate(value)


def _transport(operation):
    # Description/example normalization only; every validation key is retained.
    return structural({key: operation[key] for key in ("parameters", "requestBody", "responses")
                       if key in operation})


def test_every_dataset_shape_matches_live_and_both_generated_documents():
    live = create_app().openapi()
    combined = json.loads((API / "openapi.json").read_text())
    implemented = json.loads((API / "implemented-openapi.json").read_text())
    for identifier, (path, method) in DATASET_OPERATIONS.items():
        actual = live["paths"]["/api/v1" + path][method]
        for document, prefix in ((combined, ""), (implemented, "/api/v1")):
            generated = document["paths"][prefix + path][method]
            assert generated["operationId"] == actual["operationId"] == identifier
            assert generated["x-implementation-status"] == "implemented"
            assert _transport(generated) == _transport(actual)
            gc = schema_closure(_transport(generated), document["components"]["schemas"])
            lc = schema_closure(_transport(actual), live["components"]["schemas"])
            assert structural(gc, field_map=True) == structural(lc, field_map=True)


def test_dataset_models_are_not_overwritten_by_handwritten_schemas():
    generated = build_schemas()
    for model in (Dataset, DatasetPage, Sample, SamplePage, DatasetImportStarted, DatasetUpload):
        expected = model.model_json_schema()
        expected.pop("$defs", None)
        assert generated[model.__name__] == replace_model_refs(expected)
    assert generated["ImportedAnnotationObject"]["additionalProperties"] is False
    assert "minimum" not in generated["ImportedAnnotationObject"]["properties"]["object_id"]
    assert generated["CorrectionBoundingBox"]["items"]["minimum"] == 1
    assert generated["CorrectionObject"]["properties"]["bbox"] == {"$ref": "#/components/schemas/CorrectionBoundingBox"}
    assert "minimum" not in generated["BoundingBox"]["items"]


def test_constraints_defaults_nullability_and_exact_error_inventory():
    contract = build_contract()
    operations = contract["paths"]
    for identifier, (path, method) in DATASET_OPERATIONS.items():
        operation = operations[path][method]
        expected = {"202", "413", "415", "422", "500", "503"} if method == "post" else {"200", "422", "500", "503"}
        if path != "/datasets":
            expected.add("404")
        assert set(operation["responses"]) == expected
        params = {(p["in"], p["name"]): p for p in operation.get("parameters", [])}
        for (location, name), value in params.items():
            if location == "path":
                assert value["required"] is True
                assert value["schema"]["minLength"] == 1
                assert value["schema"]["maxLength"] == 255
            if name == "limit":
                assert value["schema"]["default"] == 20
                assert value["schema"]["minimum"] == 1 and value["schema"]["maximum"] == 100
            if name == "offset":
                assert value["schema"]["default"] == 0
                assert value["schema"]["maximum"] == 9223372036854775807
            if name == "revision":
                assert value["required"] is False and value["schema"]["default"] is None
                assert {"type": "null"} in value["schema"]["anyOf"]
                numeric = next(x for x in value["schema"]["anyOf"] if x.get("type") == "integer")
                assert numeric["minimum"] == 1 and numeric["maximum"] == 2147483647
    upload = operations["/datasets"]["post"]["requestBody"]
    assert upload["required"] is True
    assert set(upload["content"]) == {"multipart/form-data"}
    schema = upload["content"]["multipart/form-data"]["schema"]
    assert set(schema["required"]) == {"archive", "name", "format"}
    assert schema["additionalProperties"] is False
    assert schema["properties"]["archive"]["format"] == "binary"
    assert schema["properties"]["format"]["const"] == "penn_fudan"


def test_every_scenario_validates_and_matches_import_lifecycle_policy():
    scenarios = build_dataset_import_scenarios()
    assert set(scenarios["states"]) == {state.value for state in JobStatus}
    DatasetImportStarted.model_validate_json(json.dumps(scenarios["started"]))
    expected = {"queued": "importing", "running": "importing", "cancelling": "importing",
                "completed": "ready", "failed": "failed", "cancelled": "cancelled"}
    for state, pair in scenarios["states"].items():
        dataset = Dataset.model_validate_json(json.dumps(pair["dataset"]))
        job = Job.model_validate_json(json.dumps(pair["job"]))
        assert dataset.status.value == expected[state]
        assert dataset.import_job_id == job.job_id and job.status.value == state
        assert job.kind.value == "dataset_import"
        assert (job.result is not None) == (state == "completed")
        assert (job.error is not None) == (state == "failed")
        assert dataset.object_count is None
        if state == "queued":
            assert dataset.sample_count is None
            assert job.progress.total is None and job.progress.fraction is None
        if state == "completed":
            assert job.result.kind == "dataset" and job.result.version is None
            assert job.result.href == "/api/v1/datasets/" + quote(dataset.dataset_id, safe="")
            assert dataset.sample_count == len(scenarios["samples"])
    assert set(scenarios["invalid"]) | set(scenarios["failures"]) == set(ERROR_MESSAGES)
    for group, expected_status in (("invalid", "invalid"), ("failures", "failed")):
        for code, pair in scenarios[group].items():
            dataset = Dataset.model_validate_json(json.dumps(pair["dataset"]))
            job = Job.model_validate_json(json.dumps(pair["job"]))
            assert dataset.status.value == expected_status and job.status.value == "failed"
            assert job.error.code == code and job.error.message == ERROR_MESSAGES[code]
    pair = scenarios["queued_cancelled"]
    assert pair["dataset"]["status"] == pair["job"]["status"] == "cancelled"
    assert pair["dataset"]["sample_count"] is None
    assert pair["job"]["progress"]["total"] is None


def test_dirty_source_examples_preserve_objects_and_pinned_raw_availability():
    scenarios = build_dataset_import_scenarios()
    for value in scenarios["samples"].values():
        Sample.model_validate_json(json.dumps(value))
        for kind, asset in value["assets"].items():
            if asset is not None:
                assert asset["revision"] == value["revision"]
                assert asset["url"].endswith(f'/assets/{kind}?revision={value["revision"]}')
    samples = scenarios["samples"]
    assert all(asset is None for asset in samples["annotation_only"]["assets"].values())
    assert samples["annotation_only"]["objects"] and samples["annotation_only"]["image_size"] is None
    assert samples["missing_mask"]["assets"]["mask"] is None
    assert samples["corrupt_image"]["assets"]["image"] is not None
    assert samples["corrupt_image"]["issues"] and samples["corrupt_image"]["image_size"] is None
    assert [obj["object_id"] for obj in samples["invalid_objects"]["objects"]] == [0, 0]
    assert samples["invalid_objects"]["objects"][0]["bbox"] == [-3, 4, 1, -2]
    assert samples["invalid_objects"]["objects"][0]["label"] == "person"
    assert samples["zero_object"]["objects"] == samples["malformed_annotation"]["objects"] == []
    evidence = scenarios["annotation_evidence"]
    assert "Objects with ground truth : 0 {}" in evidence["zero_object"]["text"]
    assert evidence["zero_object"]["annotation_object_count"] == 0
    assert evidence["malformed_annotation"]["annotation_object_count"] is None
    assert samples["zero_object"]["issues"] == [] and samples["malformed_annotation"]["issues"]


@pytest.mark.parametrize("field,value", [("revision", True), ("revision", 0), ("revision", 1.5),
                                         ("image_size", [True, 3]), ("unexpected", 1)])
def test_source_transports_still_reject_coercion_or_unknown_fields(field, value):
    sample = deepcopy(build_dataset_import_scenarios()["samples"]["zero_object"])
    sample[field] = value
    with pytest.raises(ValidationError):
        Sample.model_validate_json(json.dumps(sample))


def test_schema_normalization_preserves_property_names_defaults_and_constraints():
    value = {"description": "annotation", "properties": {
        "description": {"type": "integer", "minimum": 1},
        "title": {"type": "object", "default": {"description": "literal", "title": "literal"}},
    }, "required": ["description", "title"], "additionalProperties": False}
    normalized = structural(value)
    assert "description" not in normalized
    assert normalized["properties"] == value["properties"]
    assert normalized["required"] == value["required"]
    assert normalized["additionalProperties"] is False


def test_alignment_rejects_schema_collision_before_changing_document():
    reference = build_contract()
    reference["components"]["schemas"]["Sample"]["properties"]["revision"]["minimum"] = 2
    before = deepcopy(reference)
    with pytest.raises(ValueError, match="component collision"):
        align_dataset_contract(reference, create_app().openapi())
    assert reference == before


def test_generated_examples_and_inventory_are_complete_unique_and_fresh():
    rendered = render_artifacts()
    assert set(rendered) == FILES
    for name, content in rendered.items():
        assert (API / name).read_text(encoding="utf-8") == content
    document = json.loads(rendered["openapi.json"])
    examples = json.loads(rendered["examples.json"])
    operations = [op for methods in document["paths"].values() for op in methods.values()]
    ids = [op["operationId"] for op in operations]
    assert len(ids) == len(set(ids)) == 49
    assert sum(op["x-implementation-status"] == "implemented" for op in operations) == 25
    assert sum(op["x-implementation-status"] == "planned" for op in operations) == 24
    routes = [context.original_route.operation_id for context in iter_route_contexts(create_app().routes)
              if isinstance(context.original_route, APIRoute) and context.original_route.include_in_schema]
    assert len(routes) == len(set(routes)) == 25
    for identifier in DATASET_OPERATIONS:
        assert routes.count(identifier) == 1
        fixture = next(item for item in examples["operations"] if item["operation_id"] == identifier)
        assert fixture["implementation"] == "implemented"
        path, method = DATASET_OPERATIONS[identifier]
        schema = document["paths"][path][method]["responses"][str(fixture["response"]["status"])]["content"]["application/json"]["schema"]
        _validate(document, schema, fixture["response"]["body"])
    assert set(examples["future_job_scenarios"]) == {"recheck", "export", "feedback_evaluation"}
    assert examples["dataset_import_scenarios"] == build_dataset_import_scenarios()
    assert "dataset_import" not in examples["future_job_scenarios"]
    scenarios = examples["dataset_import_scenarios"]
    for pair in list(scenarios["states"].values()) + list(scenarios["invalid"].values()) + list(scenarios["failures"].values()):
        for key, model in (("dataset", "Dataset"), ("job", "Job")):
            _validate(document, {"$ref": "#/components/schemas/" + model}, pair[key])
    for sample in scenarios["samples"].values():
        _validate(document, {"$ref": "#/components/schemas/Sample"}, sample)
    public = json.dumps(scenarios)
    for forbidden in ("archive_key", "archive_sha256", "storage_key", "claim_token", "lease_until",
                      "source_root", "DATABASE_URL", "POSTGRES_PASSWORD"):
        assert forbidden not in public


@pytest.mark.parametrize("defect", ["identity", "statuses", "response_media", "request_media", "reference"])
def test_alignment_fails_closed_without_partial_updates(defect):
    reference = build_contract()
    live = create_app().openapi()
    operation = live["paths"]["/api/v1/datasets"]["post"]
    if defect == "identity":
        operation["operationId"] = "anotherOperation"
    elif defect == "statuses":
        del operation["responses"]["413"]
    elif defect == "response_media":
        operation["responses"]["202"]["content"]["text/plain"] = {"schema": {"type": "string"}}
    elif defect == "request_media":
        operation["requestBody"]["content"] = {"application/json": {"schema": {"type": "object"}}}
    else:
        operation["responses"]["202"]["content"]["application/json"]["schema"] = {
            "$ref": "#/components/schemas/MissingDatasetComponent"}
    before = deepcopy(reference)
    with pytest.raises(ValueError):
        align_dataset_contract(reference, live)
    assert reference == before


def test_reference_walk_keeps_annotation_named_properties_but_ignores_literal_instances():
    schema = {"type": "object", "properties": {
        "example": {"$ref": "#/components/schemas/Value"},
        "$ref": {"type": "string"},
    }, "default": {"$ref": "not-a-schema-reference"}}
    closure = schema_closure(schema, {"Value": {"type": "integer", "minimum": 1}})
    assert closure == {"Value": {"type": "integer", "minimum": 1}}


def test_import_request_fixture_matches_acknowledged_name_and_format():
    contract = build_contract()
    fixture = next(item for item in contract["x-template-operations"] if item["operation_id"] == "importDataset")
    pair = fixture["response"]["body"]
    assert fixture["request"]["body"]["name"] == pair["dataset"]["name"]
    assert fixture["request"]["body"]["format"] == pair["dataset"]["format"]
    DatasetImportStarted.model_validate_json(json.dumps(pair))


def test_source_transport_permissiveness_is_not_a_claim_of_parser_support():
    value = deepcopy(build_dataset_import_scenarios()["samples"]["invalid_objects"])
    value["objects"][0].update(object_id=-1, label="")
    checked = Sample.model_validate_json(json.dumps(value))
    assert checked.objects[0].object_id == -1 and checked.objects[0].label == ""
    # Current Penn-Fudan regex does not parse signed IDs or empty quoted labels.
    # This boundary check must not masquerade as an executed import example.
    schema = build_schemas()["CorrectionObject"]
    assert schema["properties"]["object_id"]["minimum"] == 1
