"""Frontend planning coherence; these do not make future operations callable."""

import json
from pathlib import Path

from backend.app.models.audit import AuditRun
from backend.app.models.job import Job, JobStatus

API = Path(__file__).resolve().parents[1] / "api"
SPEC = json.loads((API / "openapi.json").read_text())
EXAMPLES = json.loads((API / "examples.json").read_text())

def test_planned_reference_covers_every_future_operation_and_no_live_operation():
    guide = (API / "PLANNED_ENDPOINTS.md").read_text()
    planned = []
    for methods in SPEC["paths"].values():
        for operation in methods.values():
            heading = "\n## " + operation["operationId"] + "\n"
            if operation["x-implementation-status"] == "planned":
                planned.append(operation)
                assert guide.count(heading) == 1
                assert "500" in operation["responses"]
            else:
                assert heading not in guide
    assert len(planned) == 4

def test_future_audit_payloads_reuse_the_live_audit_transport():
    schemas = SPEC["components"]["schemas"]
    assert schemas["AuditStarted"]["properties"]["audit"] == {"$ref": "#/components/schemas/AuditRun"}
    assert schemas["AuditPage"]["properties"]["items"]["items"] == {"$ref": "#/components/schemas/AuditRun"}
    operations = {entry["operation_id"]: entry for entry in EXAMPLES["operations"]}
    for name in ("startAudit", "recheckSamples"):
        body = operations[name]["response"]["body"]
        audit = AuditRun.model_validate_json(json.dumps(body["audit"]))
        job = Job.model_validate_json(json.dumps(body["job"]))
        assert audit.job_id == job.job_id
        assert audit.status == job.status
    recheck = operations["recheckSamples"]
    request = recheck["request"]["body"]
    assert set(request["sample_revisions"]) == set(request["sample_ids"])
    assert recheck["response"]["body"]["parent_audit_id"] == recheck["request"]["path_parameters"]["audit_id"]

def test_future_mask_upload_and_corrected_boxes_have_revision_and_geometry_constraints():
    schemas = SPEC["components"]["schemas"]
    assert "expected_revision" in schemas["MaskUploadRequest"]["required"]
    assert "base_revision" in schemas["MaskUpload"]["required"]
    corrected = schemas["CorrectionBoundingBox"]
    assert all(item["minimum"] == 1 for item in corrected["prefixItems"])
    assert corrected["minItems"] == corrected["maxItems"] == 4
    assert "minimum" not in schemas["BoundingBox"]["items"]
    for path in ("/datasets", "/datasets/{dataset_id}/samples/{sample_id}/mask-uploads"):
        operation = SPEC["paths"][path]["post"]
        assert set(operation["requestBody"]["content"]) == {"multipart/form-data"}
        assert {"413", "415"} <= set(operation["responses"])

def test_future_conflicts_expose_the_domain_codes_needed_by_frontend_flows():
    expected = {
        ("/audits", "post"): "RULEBOOK_NOT_CONFIRMED",
        ("/audits/{audit_id}/rechecks", "post"): "STALE_SAMPLE_REVISION",
        ("/exports/{export_id}/download", "get"): "EXPORT_NOT_READY",
        ("/exports/{export_id}/manifest", "get"): "EXPORT_NOT_READY",
        ("/feedback/ground-truth", "post"): "FEEDBACK_NOT_READY",
        ("/feedback/evaluations", "post"): "EVALUATION_OVERLAP",
        ("/feedback/policies/{policy_id}/activate", "post"): "FEEDBACK_NOT_READY",
    }
    for (path, method), code in expected.items():
        media = SPEC["paths"][path][method]["responses"]["409"]["content"]["application/json"]
        assert code in media["examples"]
        assert media["examples"][code]["value"]["error"]["code"] == code

def test_future_job_mocks_cover_six_states_and_pin_extraction_results():
    scenarios = EXAMPLES["future_job_scenarios"]
    assert scenarios == {}
    scenarios = {"feedback_evaluation": EXAMPLES["feedback_job_scenarios"]}
    assert "dataset_import_scenarios" in EXAMPLES
    for kind, states in scenarios.items():
        assert set(states) == {status.value for status in JobStatus}
        for state, document in states.items():
            job = Job.model_validate_json(json.dumps(document))
            assert job.kind.value == kind and job.status.value == state
            assert (job.error is not None) == (state == "failed")
            assert (job.result is not None) == (state == "completed")
    extraction = EXAMPLES["extraction_job_scenarios"]
    states = extraction["states"]
    assert set(states) == {status.value for status in JobStatus}
    for state, document in states.items():
        saved = Job.model_validate_json(json.dumps(document))
        assert saved.kind.value == "rule_extraction" and saved.status.value == state
        assert (saved.result is not None) == (state == "completed")
    from backend.app.storage.extraction_repository import ERROR_MESSAGES
    assert set(extraction["failures"]) == set(ERROR_MESSAGES)
    for code, document in extraction["failures"].items():
        saved = Job.model_validate_json(json.dumps(document))
        assert saved.error.code == code and saved.status.value == "failed"
    result = states["completed"]["result"]
    assert result["href"].endswith(f"?version={result['version']}")
