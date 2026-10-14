"""Generate the proposed API contract and frontend request/response fixtures.

Run from the project root with python -m backend.api.build_contract.
This module defines templates only; it does not register or run HTTP routes.
"""

from copy import deepcopy
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from typing import Any

import numpy as np
from PIL import Image

from backend.app.core.config import load_ollama_settings
from backend.app.models.rule import ConfirmationState, Rule, Rulebook, RuleOrigin
from backend.app.services.audit import audit_sample


API_DIRECTORY = Path(__file__).resolve().parent
BASE_PATH = "/api/v1"
TIMESTAMP = "2026-10-14T10:00:00Z"
SOURCE = "Allowed labels are person."
JsonObject = dict[str, Any]


def ref(name: str) -> JsonObject:
    return {"$ref": f"#/components/schemas/{name}"}


def nullable(schema: JsonObject) -> JsonObject:
    return {"anyOf": [schema, {"type": "null"}]}


def array(schema: JsonObject) -> JsonObject:
    return {"type": "array", "items": schema}


def obj(properties: JsonObject, required: list[str] | None = None) -> JsonObject:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


def choice(*values: str) -> JsonObject:
    return {"type": "string", "enum": list(values)}


TEXT = {"type": "string", "minLength": 1}
INTEGER = {"type": "integer"}
COUNT = {"type": "integer", "minimum": 0}
VERSION = {"type": "integer", "minimum": 1}
BOOLEAN = {"type": "boolean"}
TIME = {"type": "string", "format": "date-time"}
FRACTION = {"type": "number", "minimum": 0, "maximum": 1}
JOB_STATUS = choice("queued", "running", "cancelling", "completed", "failed", "cancelled")
SAMPLE_REVIEW_STATUS = choice("pending_review", "needs_correction", "accepted", "rejected")
TOOL_STATUS = choice("pending", "flagged", "unsure", "no_issue", "unavailable")


def replace_model_refs(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: replace_model_refs(item) for key, item in value.items()}
    if isinstance(value, list):
        return [replace_model_refs(item) for item in value]
    if isinstance(value, str) and value.startswith("#/$defs/"):
        return value.replace("#/$defs/", "#/components/schemas/", 1)
    return value


def build_schemas() -> JsonObject:
    """Reuse actual rule schemas; define proposed transport records separately."""
    book_schema = Rulebook.model_json_schema()
    definitions = book_schema.pop("$defs")
    schemas = {name: replace_model_refs(schema) for name, schema in definitions.items()}
    schemas["Rulebook"] = replace_model_refs(book_schema)
    schemas["ImageSize"] = {
        **array(VERSION), "minItems": 2, "maxItems": 2,
        "description": "[width, height], in pixels.",
    }
    schemas["BoundingBox"] = {
        **array(INTEGER), "minItems": 4, "maxItems": 4,
        "description": "[xmin, ymin, xmax, ymax], one-based and inclusive. Source data may be invalid; display integrity results.",
    }
    schemas["AnnotationObject"] = obj({"object_id": INTEGER, "label": TEXT, "bbox": ref("BoundingBox")})
    schemas["CorrectionObject"] = obj({"object_id": VERSION, "label": TEXT, "bbox": ref("BoundingBox")})
    schemas["ErrorDetail"] = obj({"field": nullable(TEXT), "message": TEXT})
    schemas["ApiError"] = obj({
        "code": TEXT, "message": TEXT, "details": array(ref("ErrorDetail")), "request_id": TEXT,
    })
    schemas["ErrorResponse"] = obj({"error": ref("ApiError")})
    schemas["Progress"] = obj({
        "phase": TEXT, "processed": COUNT, "total": nullable(COUNT), "fraction": nullable(FRACTION),
    })
    schemas["ResultReference"] = obj({
        "kind": choice("rulebook", "dataset", "audit", "export", "feedback_evaluation"),
        "id": TEXT, "version": nullable(VERSION), "href": TEXT,
    })
    schemas["Job"] = obj({
        "job_id": TEXT,
        "kind": choice("rule_extraction", "dataset_import", "audit", "recheck", "export", "feedback_evaluation"),
        "status": JOB_STATUS, "progress": ref("Progress"),
        "result": nullable(ref("ResultReference")), "error": nullable(ref("ApiError")),
        "created_at": TIME, "updated_at": TIME,
    })
    schemas["RulebookPage"] = page_schema(ref("Rulebook"))
    schemas["RulebookCreate"] = obj({"guideline_text": {"type": "string"}, "rules": array(ref("Rule"))})
    schemas["RuleExtractionRequest"] = obj({"guideline_text": TEXT})
    schemas["RulebookReplace"] = obj({
        "expected_version": VERSION, "guideline_text": {"type": "string"}, "rules": array(ref("Rule")),
    })
    schemas["RulebookAction"] = obj({"expected_version": VERSION, "reviewer_id": TEXT})
    schemas["Dataset"] = obj({
        "dataset_id": TEXT, "name": TEXT, "format": choice("penn_fudan"),
        "status": choice("importing", "ready", "invalid", "failed"),
        "sample_count": nullable(COUNT), "object_count": nullable(COUNT),
        "import_job_id": TEXT, "created_at": TIME, "issues": array(ref("ErrorDetail")),
    })
    schemas["DatasetPage"] = page_schema(ref("Dataset"))
    schemas["AssetReference"] = obj({"url": TEXT, "content_type": TEXT, "revision": VERSION})
    schemas["SampleAssets"] = obj({
        name: nullable(ref("AssetReference")) for name in ("image", "mask", "mask_preview", "thumbnail")
    })
    schemas["Sample"] = obj({
        "sample_id": TEXT, "dataset_id": TEXT, "revision": VERSION,
        "image_size": nullable(ref("ImageSize")),
        "integrity_status": choice("valid", "invalid", "unavailable"),
        "objects": array(ref("AnnotationObject")), "assets": ref("SampleAssets"),
    })
    schemas["SamplePage"] = page_schema(ref("Sample"))
    schemas["DatasetUpload"] = obj({
        "archive": {"type": "string", "format": "binary"}, "name": TEXT, "format": choice("penn_fudan"),
    })
    schemas["MaskUploadRequest"] = obj({"mask": {"type": "string", "format": "binary"}})
    schemas["MaskUpload"] = obj({
        "mask_upload_id": TEXT, "dataset_id": TEXT, "sample_id": TEXT,
        "image_size": ref("ImageSize"), "mask_ids": array(COUNT), "expires_at": TIME,
    })
    schemas["CorrectionRequest"] = obj({
        "expected_revision": VERSION, "reviewer_id": TEXT, "reason": TEXT,
        "objects": array(ref("CorrectionObject")), "mask_upload_id": nullable(TEXT),
        "source_audit_id": nullable(TEXT),
    })
    schemas["AnnotationRevision"] = obj({
        "dataset_id": TEXT, "sample_id": TEXT, "revision": VERSION,
        "parent_revision": nullable(VERSION), "objects": array(ref("AnnotationObject")),
        "mask_changed": BOOLEAN, "reviewer_id": nullable(TEXT), "reason": nullable(TEXT),
        "source_audit_id": nullable(TEXT), "created_at": TIME,
    })
    schemas["RevisionPage"] = page_schema(ref("AnnotationRevision"))
    schemas["AuditRequest"] = obj({
        "dataset_id": TEXT, "rulebook_id": TEXT, "rulebook_version": VERSION,
        "sample_ids": nullable(array(TEXT)), "feedback_policy_id": nullable(TEXT),
    })
    schemas["Audit"] = obj({
        "audit_id": TEXT, "parent_audit_id": nullable(TEXT), "dataset_id": TEXT,
        "rulebook_id": TEXT, "rulebook_version": VERSION, "feedback_policy_id": nullable(TEXT),
        "status": JOB_STATUS, "job_id": TEXT, "sample_count": COUNT, "processed_count": COUNT,
        "reviewed_count": COUNT, "created_at": TIME,
    })
    schemas["AuditPage"] = page_schema(ref("Audit"))
    schemas["AuditStarted"] = obj({"audit": ref("Audit"), "job": ref("Job")})
    schemas["DatasetImportStarted"] = obj({"dataset": ref("Dataset"), "job": ref("Job")})
    schemas["CheckEnvelope"] = obj({
        "status": choice("completed", "unavailable"), "reason": nullable(TEXT),
        "detail": nullable(TEXT), "result": nullable({"type": "object"}),
    })
    schemas["ProcessingError"] = obj({"stage": TEXT, "reason": TEXT, "detail": TEXT})
    schemas["ObjectMeasurements"] = obj({
        "annotation": ref("AnnotationObject"),
        "checks": {"type": "object", "additionalProperties": ref("CheckEnvelope")},
    })
    schemas["AuditMeasurements"] = obj({
        "image_id": TEXT, "image_size": nullable(ref("ImageSize")),
        "annotation_text": nullable({"type": "string"}), "allowed_labels": array(TEXT),
        "status": choice("completed", "incomplete"), "review_status": {"const": "pending_review"},
        "errors": array(ref("ProcessingError")),
        "checks": {"type": "object", "additionalProperties": ref("CheckEnvelope")},
        "objects": array(ref("ObjectMeasurements")),
    })
    schemas["FindingReview"] = obj({
        "revision": COUNT, "resolution": choice("pending", "accepted", "rejected", "unsure"),
        "reviewer_id": nullable(TEXT), "comment": nullable({"type": "string"}),
        "updated_at": nullable(TIME),
    })
    schemas["Evidence"] = obj({
        "image_url": nullable(TEXT), "mask_preview_url": nullable(TEXT), "crop_url": nullable(TEXT),
        "measurements": {"type": "object", "additionalProperties": ref("JsonValue")},
    })
    schemas["Finding"] = obj({
        "finding_id": TEXT, "audit_id": TEXT, "sample_id": TEXT, "sample_revision": VERSION,
        "object_id": nullable(INTEGER), "rulebook_id": TEXT, "rulebook_version": VERSION,
        "rule_id": TEXT, "description": TEXT, "check_name": nullable(TEXT),
        "outcome": choice("pass", "fail", "unsure", "unavailable"),
        "reason": nullable(TEXT), "parameters": {"type": "object", "additionalProperties": ref("JsonValue")},
        "origin": ref("RuleOrigin"), "source_sentence": nullable(TEXT),
        "evidence": ref("Evidence"), "review": ref("FindingReview"),
    })
    schemas["FindingPage"] = page_schema(ref("Finding"))
    schemas["SampleAuditSummary"] = obj({
        "sample_id": TEXT, "sample_revision": VERSION, "tool_status": TOOL_STATUS,
        "review_status": SAMPLE_REVIEW_STATUS,
        "finding_counts": obj({name: COUNT for name in ("pass", "fail", "unsure", "unavailable")}),
        "thumbnail_url": nullable(TEXT),
    })
    schemas["AuditSamplePage"] = page_schema(ref("SampleAuditSummary"))
    schemas["SampleReview"] = obj({
        "revision": COUNT, "decision": SAMPLE_REVIEW_STATUS, "reviewer_id": nullable(TEXT),
        "comment": nullable({"type": "string"}), "updated_at": nullable(TIME),
    })
    schemas["SampleAudit"] = obj({
        "audit_id": TEXT, "dataset_id": TEXT, "summary": ref("SampleAuditSummary"),
        "assets": ref("SampleAssets"), "measurements": ref("AuditMeasurements"),
        "findings": array(ref("Finding")), "review": ref("SampleReview"),
    })
    schemas["FindingReviewRequest"] = obj({
        "expected_revision": COUNT, "resolution": choice("accepted", "rejected", "unsure"),
        "reviewer_id": TEXT, "comment": {"type": "string"},
    })
    schemas["SampleReviewRequest"] = obj({
        "expected_revision": COUNT, "decision": SAMPLE_REVIEW_STATUS,
        "reviewer_id": TEXT, "comment": {"type": "string"},
    })
    schemas["RecheckRequest"] = obj({"sample_ids": {**array(TEXT), "minItems": 1}, "reviewer_id": TEXT})
    schemas["ExportRequest"] = obj({
        "audit_id": TEXT, "format": choice("penn_fudan"),
        "selection": choice("accepted_only", "all_reviewed"), "include_images": BOOLEAN,
    })
    schemas["Export"] = obj({
        "export_id": TEXT, "audit_id": TEXT, "job_id": TEXT, "status": JOB_STATUS,
        "format": choice("penn_fudan"), "selection": choice("accepted_only", "all_reviewed"),
        "include_images": BOOLEAN, "included_count": nullable(COUNT), "excluded_count": nullable(COUNT),
        "download_url": nullable(TEXT), "manifest_url": nullable(TEXT), "created_at": TIME,
    })
    schemas["ExportStarted"] = obj({"export": ref("Export"), "job": ref("Job")})
    schemas["ExportManifestEntry"] = obj({
        "sample_id": TEXT, "sample_revision": VERSION, "included": BOOLEAN,
        "review_decision": SAMPLE_REVIEW_STATUS, "reason": nullable(TEXT),
    })
    schemas["ExportManifest"] = obj({
        "export_id": TEXT, "audit_id": TEXT, "rulebook_id": TEXT, "rulebook_version": VERSION,
        "entries": array(ref("ExportManifestEntry")),
    })
    schemas["FeedbackExample"] = obj({
        "example_id": TEXT, "source_audit_id": TEXT, "dataset_id": TEXT, "sample_id": TEXT,
        "original_revision": VERSION, "corrected_revision": VERSION, "rule_id": TEXT,
        "reviewer_id": TEXT, "approved": BOOLEAN, "description": TEXT,
    })
    schemas["FeedbackExamplePage"] = page_schema(ref("FeedbackExample"))
    schemas["FeedbackPolicyCreate"] = obj({
        "name": TEXT, "strategy": {"const": "reviewer_examples"},
        "example_ids": {**array(TEXT), "minItems": 1, "uniqueItems": True},
        "target_check_names": {**array(TEXT), "minItems": 1},
    })
    schemas["FeedbackPolicy"] = obj({
        "policy_id": TEXT, "name": TEXT, "strategy": {"const": "reviewer_examples"},
        "status": choice("draft", "active", "retired"), "example_ids": array(TEXT),
        "target_check_names": array(TEXT), "evaluation_id": nullable(TEXT),
    })
    schemas["FeedbackPolicyPage"] = page_schema(ref("FeedbackPolicy"))
    schemas["GroundTruthCreate"] = obj({
        "dataset_id": TEXT, "source_audit_ids": {**array(TEXT), "minItems": 1}, "reviewer_id": TEXT,
    })
    schemas["GroundTruth"] = obj({
        "ground_truth_id": TEXT, "dataset_id": TEXT, "version": VERSION,
        "source_audit_ids": array(TEXT), "verified_sample_count": COUNT, "created_at": TIME,
    })
    schemas["FeedbackEvaluationRequest"] = obj({
        "policy_id": TEXT, "ground_truth_id": TEXT, "ground_truth_version": VERSION,
    })
    schemas["EvaluationMetrics"] = obj({
        "precision": nullable(FRACTION), "recall": nullable(FRACTION), "f1": nullable(FRACTION),
        "true_positives": COUNT, "false_positives": COUNT, "false_negatives": COUNT,
        "sample_count": COUNT, "elapsed_seconds": {"type": "number", "minimum": 0},
    })
    schemas["FeedbackEvaluation"] = obj({
        "evaluation_id": TEXT, "policy_id": TEXT, "job_id": TEXT, "status": JOB_STATUS,
        "evaluation_dataset_id": TEXT, "ground_truth_id": TEXT, "ground_truth_version": VERSION,
        "baseline": nullable(ref("EvaluationMetrics")), "candidate": nullable(ref("EvaluationMetrics")),
        "error": nullable(ref("ApiError")),
    })
    schemas["FeedbackEvaluationStarted"] = obj({"evaluation": ref("FeedbackEvaluation"), "job": ref("Job")})
    schemas["FeedbackEvaluationPage"] = page_schema(ref("FeedbackEvaluation"))
    schemas["ActivatePolicyRequest"] = obj({"evaluation_id": TEXT, "reviewer_id": TEXT})
    schemas["Health"] = obj({"status": {"const": "ok"}, "api_version": {"const": "v1"}})
    schemas["Capabilities"] = obj({
        "dataset_formats": array(TEXT), "guideline_input_formats": array(TEXT),
        "coordinate_convention": {"const": "one_based_inclusive"},
        "checks": array(obj({"name": TEXT, "implementation": choice("implemented", "planned")})),
        "execution_device": {"const": "cpu"},
    })
    schemas["SystemStatus"] = obj({
        "ollama": obj({"reachable": BOOLEAN, "model": TEXT, "model_available": nullable(BOOLEAN)}),
        "detector": obj({"weights_cached": BOOLEAN, "integration_ready": BOOLEAN}),
        "execution_device": {"const": "cpu"},
    })
    return schemas


def page_schema(item: JsonObject) -> JsonObject:
    return obj({"items": array(item), "limit": VERSION, "offset": COUNT, "total": COUNT, "next_offset": nullable(COUNT)})


def page(items: list[Any]) -> JsonObject:
    return {"items": items, "limit": 20, "offset": 0, "total": len(items), "next_offset": None}


def job(kind: str, resource_kind: str, resource_id: str, *, completed: bool = False) -> JsonObject:
    collection = {
        "rulebook": "rulebooks", "dataset": "datasets", "audit": "audits",
        "export": "exports", "feedback_evaluation": "feedback/evaluations",
    }[resource_kind]
    return {
        "job_id": f"job_{kind}_demo", "kind": kind,
        "status": "completed" if completed else "queued",
        "progress": {"phase": "completed" if completed else "queued", "processed": 1 if completed else 0, "total": 1, "fraction": 1.0 if completed else 0.0},
        "result": {"kind": resource_kind, "id": resource_id, "version": 1 if resource_kind == "rulebook" else None,
                   "href": f"{BASE_PATH}/{collection}/{resource_id}"} if completed else None,
        "error": None, "created_at": TIMESTAMP, "updated_at": TIMESTAMP,
    }


def build_examples() -> JsonObject:
    """Produce synthetic examples, including measurements from the actual service."""
    draft_rule = Rule(rule_id="LBL-1", description="Use an allowed label.", check_name="allowed_labels",
                      parameters={"allowed_labels": ["person"]}, origin=RuleOrigin.EXTRACTED, source_sentence=SOURCE)
    draft = Rulebook(rulebook_id="rulebook_demo", guideline_text=SOURCE, rules=[draft_rule]).model_dump(mode="json")
    confirmed_rule = Rule(**{**draft_rule.model_dump(), "confirmation_state": ConfirmationState.CONFIRMED})
    confirmed = Rulebook(rulebook_id="rulebook_demo", version=2, guideline_text=SOURCE, rules=[confirmed_rule],
                         confirmation_state=ConfirmationState.CONFIRMED).model_dump(mode="json")
    with TemporaryDirectory() as directory:
        root = Path(directory)
        for folder in ("PNGImages", "Annotation", "PedMasks"):
            (root / folder).mkdir()
        Image.new("RGB", (4, 3)).save(root / "PNGImages/sample_001.png")
        mask = np.array([[1, 1, 0, 0], [1, 1, 0, 0], [0, 0, 0, 2]], dtype=np.uint8)
        Image.fromarray(mask).save(root / "PedMasks/sample_001_mask.png")
        (root / "Annotation/sample_001.txt").write_text(
            'Bounding box for object 1 "person" (Xmin, Ymin) - (Xmax, Ymax) : (1, 1) - (2, 2)\n'
            'Bounding box for object 2 "vehicle" (Xmin, Ymin) - (Xmax, Ymax) : (4, 3) - (4, 3)\n'
        )
        measurements = json.loads(json.dumps(audit_sample(root, "sample_001", ["person"])))
        path = root / "Annotation/sample_001.txt"
        path.write_text(path.read_text().replace('"vehicle"', '"person"'))
        correct_measurements = json.loads(json.dumps(audit_sample(root, "sample_001", ["person"])))
    annotations = [row["annotation"] for row in measurements["objects"]]
    assets = {kind: {"url": f"{BASE_PATH}/datasets/dataset_demo/samples/sample_001/assets/{kind}?revision=1",
                      "content_type": "image/png", "revision": 1}
              for kind in ("image", "mask", "mask_preview", "thumbnail")}
    sample = {"sample_id": "sample_001", "dataset_id": "dataset_demo", "revision": 1,
              "image_size": [4, 3], "integrity_status": "valid", "objects": annotations, "assets": assets}
    dataset = {"dataset_id": "dataset_demo", "name": "Demo dataset", "format": "penn_fudan", "status": "ready",
               "sample_count": 1, "object_count": 2, "import_job_id": "job_dataset_import_demo", "created_at": TIMESTAMP, "issues": []}
    audit = {"audit_id": "audit_demo", "parent_audit_id": None, "dataset_id": "dataset_demo",
             "rulebook_id": "rulebook_demo", "rulebook_version": 2, "feedback_policy_id": None,
             "status": "completed", "job_id": "job_audit_demo", "sample_count": 1, "processed_count": 1,
             "reviewed_count": 0, "created_at": TIMESTAMP}
    finding = {
        "finding_id": "finding_demo", "audit_id": "audit_demo", "sample_id": "sample_001", "sample_revision": 1,
        "object_id": 2, "rulebook_id": "rulebook_demo", "rulebook_version": 2, "rule_id": "LBL-1",
        "description": "Use an allowed label.", "check_name": "allowed_labels", "outcome": "fail",
        "reason": "label_not_allowed", "parameters": {"allowed_labels": ["person"]}, "origin": "extracted", "source_sentence": SOURCE,
        "evidence": {"image_url": assets["image"]["url"], "mask_preview_url": assets["mask_preview"]["url"], "crop_url": None,
                     "measurements": {"actual_label": "vehicle", "allowed_labels": ["person"]}},
        "review": {"revision": 0, "resolution": "pending", "reviewer_id": None, "comment": None, "updated_at": None},
    }
    summary = {"sample_id": "sample_001", "sample_revision": 1, "tool_status": "flagged", "review_status": "pending_review",
               "finding_counts": {"pass": 1, "fail": 1, "unsure": 0, "unavailable": 0}, "thumbnail_url": assets["thumbnail"]["url"]}
    passing = deepcopy(finding)
    passing.update(finding_id="finding_pass_demo", object_id=1, outcome="pass", reason=None)
    passing["evidence"]["measurements"]["actual_label"] = "person"
    sample_review = {"revision": 0, "decision": "pending_review", "reviewer_id": None, "comment": None, "updated_at": None}
    detail = {"audit_id": "audit_demo", "dataset_id": "dataset_demo", "summary": summary, "assets": assets,
              "measurements": measurements, "findings": [passing, finding], "review": sample_review}
    no_issue = deepcopy(detail)
    no_issue["summary"].update(tool_status="no_issue", finding_counts={"pass": 2, "fail": 0, "unsure": 0, "unavailable": 0})
    no_issue["measurements"] = correct_measurements
    no_issue["findings"] = [deepcopy(passing), {**deepcopy(passing), "finding_id": "finding_pass_second_demo", "object_id": 2}]
    uncertain = deepcopy(no_issue)
    uncertain_finding = deepcopy(passing)
    uncertain_finding.update(finding_id="finding_uncertain_demo", rule_id="POSTURE-1", object_id=1,
                             description="The activity label should match visible posture.", check_name="person_posture",
                             outcome="unsure", reason="ambiguous_posture", origin="reviewer", source_sentence=None, parameters={})
    uncertain_finding["evidence"]["measurements"] = {"model_result": "uncertain"}
    uncertain["findings"].append(uncertain_finding)
    uncertain["summary"].update(tool_status="unsure", finding_counts={"pass": 2, "fail": 0, "unsure": 1, "unavailable": 0})
    unavailable = deepcopy(no_issue)
    blocked_finding = deepcopy(uncertain_finding)
    blocked_finding.update(finding_id="finding_unavailable_demo", rule_id="CUSTOM-1", object_id=None,
                           description="Custom reviewer requirement without a supported check.", check_name=None,
                           outcome="unavailable", reason="unsupported_check")
    blocked_finding["evidence"]["measurements"] = {}
    unavailable["findings"].append(blocked_finding)
    unavailable["summary"].update(tool_status="unavailable", finding_counts={"pass": 2, "fail": 0, "unsure": 0, "unavailable": 1})
    scenario_rulebooks = {"flagged": confirmed, "no_issue": confirmed}
    for name, scenario, rule_id in (("unsure", uncertain, "POSTURE-1"), ("unavailable", unavailable, "CUSTOM-1")):
        extra_finding = scenario["findings"][-1]
        extra_rule = Rule(
            rule_id=rule_id, description=extra_finding["description"], check_name=extra_finding["check_name"],
            parameters={}, origin=RuleOrigin.REVIEWER, confirmation_state=ConfirmationState.CONFIRMED,
        )
        scenario_book = Rulebook(
            rulebook_id=f"rulebook_{name}_demo", guideline_text=SOURCE,
            rules=[confirmed_rule, extra_rule], confirmation_state=ConfirmationState.CONFIRMED,
        ).model_dump(mode="json")
        for item in scenario["findings"]:
            item["rulebook_id"] = scenario_book["rulebook_id"]
            item["rulebook_version"] = scenario_book["version"]
        scenario_rulebooks[name] = scenario_book
    corrected = deepcopy(annotations)
    corrected[1]["label"] = "person"
    revision = {"dataset_id": "dataset_demo", "sample_id": "sample_001", "revision": 2, "parent_revision": 1,
                "objects": corrected, "mask_changed": False, "reviewer_id": "reviewer_demo", "reason": "Corrected the second object's label.",
                "source_audit_id": "audit_demo", "created_at": TIMESTAMP}
    export = {"export_id": "export_demo", "audit_id": "audit_recheck_demo", "job_id": "job_export_demo", "status": "completed",
              "format": "penn_fudan", "selection": "accepted_only", "include_images": False, "included_count": 1, "excluded_count": 0,
              "download_url": f"{BASE_PATH}/exports/export_demo/download", "manifest_url": f"{BASE_PATH}/exports/export_demo/manifest", "created_at": TIMESTAMP}
    feedback = {"example_id": "feedback_demo", "source_audit_id": "audit_posture_demo", "dataset_id": "dataset_feedback", "sample_id": "sample_feedback_001",
                "original_revision": 1, "corrected_revision": 2, "rule_id": "LBL-2", "reviewer_id": "reviewer_demo",
                "approved": True, "description": "Reviewer corrected a separate posture example from walking to standing."}
    policy = {"policy_id": "policy_demo", "name": "Reviewed examples", "strategy": "reviewer_examples", "status": "draft",
              "example_ids": ["feedback_demo"], "target_check_names": ["person_posture"], "evaluation_id": None}
    evaluation = {"evaluation_id": "evaluation_demo", "policy_id": "policy_demo", "job_id": "job_feedback_evaluation_demo",
                  "status": "queued", "evaluation_dataset_id": "dataset_holdout", "ground_truth_id": "truth_demo", "ground_truth_version": 1,
                  "baseline": None, "candidate": None, "error": None}
    return {
        "draft": draft, "confirmed": confirmed, "sample": sample, "dataset": dataset,
        "audit": audit, "finding": finding, "passing": passing, "summary": summary,
        "detail": detail, "sample_review": sample_review, "corrected": corrected,
        "revision": revision, "export": export, "feedback": feedback,
        "policy": policy, "evaluation": evaluation,
        "review_scenarios": {"flagged": detail, "no_issue": no_issue, "unsure": uncertain, "unavailable": unavailable},
        "review_scenario_rulebooks": scenario_rulebooks,
    }


def parameter(name: str, location: str, schema: JsonObject, example: Any, *, required: bool = False,
              description: str = "") -> JsonObject:
    return {"name": name, "in": location, "required": required or location == "path", "schema": schema,
            "example": example, "description": description}


def pagination_parameters() -> list[JsonObject]:
    return [parameter("limit", "query", {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}, 20),
            parameter("offset", "query", {"type": "integer", "minimum": 0, "default": 0}, 0)]


def build_contract() -> JsonObject:
    examples = build_examples()
    schemas = build_schemas()
    contract: JsonObject = {
        "openapi": "3.1.0",
        "info": {"title": "PRISM Data Quality Checker — proposed API", "version": "0.1.0",
                 "description": "Frontend contract templates only. No HTTP endpoints are currently implemented. Examples are synthetic, not model accuracy results. Schemas marked as existing models reuse Python records; other resources are planned. Local CPU prototype; plain text guidelines first."},
        "servers": [{"url": "http://127.0.0.1:8000/api/v1", "description": "Proposed local development address; no server is provided yet."}],
        "tags": [{"name": name} for name in ("System", "Rulebooks", "Datasets", "Jobs", "Audits", "Reviews", "Corrections", "Exports", "Feedback")],
        "paths": {}, "components": {"schemas": schemas},
    }
    operations: list[JsonObject] = []

    def add(method: str, path: str, operation_id: str, tag: str, summary: str, response_schema: str,
            response_example: Any, *, status: int = 200, body_schema: str | None = None,
            body_example: Any = None, parameters: list[JsonObject] | None = None,
            description: str = "", backing: str = "planned", multipart: bool = False,
            binary: bool = False) -> None:
        params = list(parameters or [])
        values = {"rulebook_id": "rulebook_demo", "dataset_id": "dataset_demo", "sample_id": "sample_001",
                  "audit_id": "audit_demo", "job_id": "job_audit_demo", "finding_id": "finding_demo",
                  "export_id": "export_demo", "policy_id": "policy_demo", "evaluation_id": "evaluation_demo",
                  "ground_truth_id": "truth_demo"}
        for name in re.findall(r"\{([^}]+)\}", path):
            if not any(item["name"] == name and item["in"] == "path" for item in params):
                params.append(parameter(name, "path", TEXT, values[name]))
        media = "application/zip" if binary else "application/json"
        success_content = {"schema": {"type": "string", "format": "binary"} if binary else ref(response_schema)}
        if not binary:
            success_content["example"] = deepcopy(response_example)
        responses = {str(status): {"description": "Job accepted; poll the returned job ID." if status == 202 else summary,
                                  "content": {media: success_content}}}
        for error_status, code, message in (
            (400, "INVALID_REQUEST", "The request could not be processed."),
            (404, "NOT_FOUND", "The requested resource does not exist."),
            (409, "VERSION_CONFLICT", "The resource changed; refresh before saving."),
            (422, "VALIDATION_ERROR", "One or more request fields are invalid."),
            (503, "DEPENDENCY_UNAVAILABLE", "A required local model or service is unavailable."),
        ):
            responses[str(error_status)] = {"description": message, "content": {"application/json": {
                "schema": ref("ErrorResponse"), "example": {"error": {"code": code, "message": message,
                "details": [], "request_id": "request_demo"}}}}}
        operation = {"operationId": operation_id, "tags": [tag], "summary": summary,
                     "description": description or summary, "x-implementation-status": "planned",
                     "x-backing-service-status": backing, "responses": responses}
        if params:
            operation["parameters"] = params
        if body_schema is not None:
            request_media = "multipart/form-data" if multipart else "application/json"
            operation["requestBody"] = {"required": True, "content": {request_media: {
                "schema": ref(body_schema), "example": deepcopy(body_example)}}}
        contract["paths"].setdefault(path, {})[method.lower()] = operation
        operations.append({"operation_id": operation_id, "method": method.upper(), "path": BASE_PATH + path,
                           "tag": tag, "summary": summary, "implementation": "planned",
                           "backing_service": backing, "request": {
                               "path_parameters": {p["name"]: p["example"] for p in params if p["in"] == "path"},
                               "query": {p["name"]: p["example"] for p in params if p["in"] == "query"},
                               "content_type": ("multipart/form-data" if multipart else "application/json") if body_schema else None,
                               "body": deepcopy(body_example)},
                           "response": {"status": status, "content_type": media, "body": None if binary else deepcopy(response_example)}})

    add("GET", "/health", "getHealth", "System", "Check HTTP server liveness", "Health", {"status": "ok", "api_version": "v1"},
        description="Liveness only. Ollama being offline must not make this endpoint fail.")
    checks = ["allowed_labels", "mask_dimensions", "mask_ids", "box_validity", "box_iou", "box_mask_alignment", "mask_outside_box", "mask_box_fill", "mask_components"]
    add("GET", "/capabilities", "getCapabilities", "System", "List supported and planned checks", "Capabilities",
        {"dataset_formats": ["penn_fudan"], "guideline_input_formats": ["plain_text"], "coordinate_convention": "one_based_inclusive",
         "checks": [{"name": name, "implementation": "implemented"} for name in checks] +
                   [{"name": name, "implementation": "planned"} for name in
                    ("person_detection", "detection_annotation_matches", "detection_mask_coverage", "mask_person_count", "person_posture")],
         "execution_device": "cpu"},
        backing="Existing measurement functions; registry and HTTP route are planned.")
    add("GET", "/system/status", "getSystemStatus", "System", "Inspect local model readiness", "SystemStatus",
        {"ollama": {"reachable": False, "model": load_ollama_settings().model, "model_available": None},
         "detector": {"weights_cached": True, "integration_ready": False}, "execution_device": "cpu"},
        description="Read-only readiness. Does not start services, download models or change configuration.")
    add("POST", "/rulebooks", "createRulebook", "Rulebooks", "Create a manually assembled draft", "Rulebook", examples["draft"], status=201,
        body_schema="RulebookCreate", body_example={"guideline_text": SOURCE, "rules": examples["draft"]["rules"]},
        backing="Rule and Rulebook validation exists; persistence and route are planned.",
        description="Server assigns rulebook_id and version 1. The book starts as draft. Reviewer-authored rules use origin reviewer and may have no citation.")
    add("POST", "/rulebooks/extractions", "extractRulebook", "Rulebooks", "Propose draft rules from plain text", "Job", job("rule_extraction", "rulebook", "rulebook_demo"), status=202,
        body_schema="RuleExtractionRequest", body_example={"guideline_text": SOURCE}, backing="propose_rules exists; jobs, persistence and route are planned.",
        description="Poll GET /jobs/{job_id}. On completion, result.href points to the saved draft rulebook. Failure is stored in job.error, including MODEL_UNAVAILABLE or INVALID_RULE_PROPOSALS.")
    add("GET", "/rulebooks", "listRulebooks", "Rulebooks", "List latest rulebook versions", "RulebookPage", page([examples["draft"]]), parameters=pagination_parameters())
    add("GET", "/rulebooks/{rulebook_id}", "getRulebook", "Rulebooks", "Read a rulebook version", "Rulebook", examples["confirmed"],
        parameters=[parameter("version", "query", VERSION, 2, description="Omit for latest; audits must reference a specific version.")])
    add("GET", "/rulebooks/{rulebook_id}/versions", "listRulebookVersions", "Rulebooks", "Read rulebook version history", "RulebookPage", page([examples["draft"]]), parameters=pagination_parameters())
    add("PUT", "/rulebooks/{rulebook_id}", "replaceDraftRulebook", "Rulebooks", "Save a reviewed draft", "Rulebook", {**examples["confirmed"], "confirmation_state": "draft"},
        body_schema="RulebookReplace", body_example={"expected_version": 1, "guideline_text": SOURCE, "rules": examples["confirmed"]["rules"]},
        description="Only latest drafts are editable. Save increments version and preserves history. All retained extracted citations must match the supplied text. A book stays draft until the confirm action. The response reflects submitted rules; example save confirms each rule but not the book.")
    add("POST", "/rulebooks/{rulebook_id}/confirm", "confirmRulebook", "Rulebooks", "Confirm a reviewed rulebook", "Rulebook", {**examples["confirmed"], "version": 2},
        body_schema="RulebookAction", body_example={"expected_version": 2, "reviewer_id": "reviewer_demo"},
        description="All included rules must be confirmed. Rejected rules must be removed from the active draft; prior versions preserve them. Freeze the confirmed version for audits. 409 RULEBOOK_NOT_READY when rules are unconfirmed.")
    add("POST", "/rulebooks/{rulebook_id}/revisions", "reviseRulebook", "Rulebooks", "Start a new draft from a confirmed version", "Rulebook", {**examples["draft"], "version": 3}, status=201,
        body_schema="RulebookAction", body_example={"expected_version": 2, "reviewer_id": "reviewer_demo"},
        description="Keep the old confirmed version immutable. Create the next draft and reset included rule confirmation to draft.")
    add("POST", "/datasets", "importDataset", "Datasets", "Upload a dataset archive for local import", "DatasetImportStarted",
        {"dataset": {**examples["dataset"], "status": "importing", "sample_count": None, "object_count": None}, "job": job("dataset_import", "dataset", "dataset_demo")},
        status=202, body_schema="DatasetUpload", body_example={"archive": "<binary dataset ZIP>", "name": "Demo dataset", "format": "penn_fudan"}, multipart=True,
        description="Send multipart FormData, not JSON. Penn-Fudan ZIP contains PNGImages/, Annotation/, PedMasks/ under an optional common root. Do not set multipart Content-Type manually; the browser supplies its boundary. Files remain on the local backend. Other dataset adapters are future work.")
    add("GET", "/datasets", "listDatasets", "Datasets", "List imported datasets", "DatasetPage", page([examples["dataset"]]), parameters=pagination_parameters())
    add("GET", "/datasets/{dataset_id}", "getDataset", "Datasets", "Read dataset import and integrity summary", "Dataset", examples["dataset"])
    add("GET", "/datasets/{dataset_id}/samples", "listDatasetSamples", "Datasets", "List every dataset sample", "SamplePage", page([examples["sample"]]), parameters=pagination_parameters())
    add("GET", "/datasets/{dataset_id}/samples/{sample_id}", "getDatasetSample", "Datasets", "Read annotations and pinned asset URLs", "Sample", examples["sample"],
        parameters=[parameter("revision", "query", VERSION, 1, description="Omit for latest. Returned asset URLs always pin a revision.")])
    asset = parameter("asset_kind", "path", choice("image", "mask", "mask_preview", "thumbnail"), "image")
    add("GET", "/datasets/{dataset_id}/samples/{sample_id}/assets/{asset_kind}", "getSampleAsset", "Datasets", "Download an image or mask asset", "", None, binary=True,
        parameters=[asset, parameter("revision", "query", VERSION, 1, required=True)],
        description="Returns PNG bytes. mask preserves 2D integer instance IDs; mask_preview is a colored display image and must never be used as raw IDs. Missing or unreadable assets return a JSON error.")
    asset_response = contract["paths"]["/datasets/{dataset_id}/samples/{sample_id}/assets/{asset_kind}"]["get"]["responses"]["200"]
    asset_response["content"]["image/png"] = asset_response["content"].pop("application/zip")
    operations[-1]["response"]["content_type"] = "image/png"
    add("POST", "/datasets/{dataset_id}/samples/{sample_id}/mask-uploads", "uploadCorrectionMask", "Corrections", "Stage a replacement instance-ID mask", "MaskUpload",
        {"mask_upload_id": "mask_upload_demo", "dataset_id": "dataset_demo", "sample_id": "sample_001", "image_size": [4, 3], "mask_ids": [0, 1, 2], "expires_at": "2026-10-14T11:00:00Z"}, status=201,
        body_schema="MaskUploadRequest", body_example={"mask": "<binary 2D instance-ID PNG>"}, multipart=True,
        description="Stage the mask without changing annotations. Submit mask_upload_id with the correction to apply boxes, labels and mask atomically. PNG must preserve integer IDs; RGB visualization PNGs are rejected. Staged uploads expire and must be uploaded again after expiry.")
    add("POST", "/datasets/{dataset_id}/samples/{sample_id}/revisions", "correctAnnotations", "Corrections", "Save corrected annotations and optional mask", "AnnotationRevision", examples["revision"], status=201,
        body_schema="CorrectionRequest", body_example={"expected_revision": 1, "reviewer_id": "reviewer_demo", "reason": examples["revision"]["reason"],
            "objects": examples["corrected"], "mask_upload_id": None, "source_audit_id": "audit_demo"},
        description="objects is the complete replacement list: add/remove entries to add/remove objects. Preserve originals and history. Validate boxes, labels and resulting ID correspondence before committing the revision. Changes make older sample reviews stale; request a recheck before deciding on the corrected revision. Reject stale expected_revision with 409.")
    add("GET", "/datasets/{dataset_id}/samples/{sample_id}/revisions", "listAnnotationRevisions", "Corrections", "Read correction history", "RevisionPage", page([examples["revision"]]), parameters=pagination_parameters())
    audit_request = {"dataset_id": "dataset_demo", "rulebook_id": "rulebook_demo", "rulebook_version": 2, "sample_ids": None, "feedback_policy_id": None}
    add("POST", "/audits", "startAudit", "Audits", "Start an audit against confirmed rules", "AuditStarted",
        {"audit": {**examples["audit"], "status": "queued", "processed_count": 0}, "job": job("audit", "audit", "audit_demo")}, status=202,
        body_schema="AuditRequest", body_example=audit_request, backing="audit_sample measurements exist; confirmed-rule execution, batch processing and route are planned.",
        description="sample_ids null means all samples. Require ready dataset and a confirmed rulebook version; return 409 RULEBOOK_NOT_CONFIRMED otherwise. Snapshot sample revisions and rules. Unsupported rules yield unavailable findings. A non-null feedback policy must be active. Batch size is selected from measured CPU resources, not supplied as a universal fixed size.")
    add("GET", "/audits", "listAudits", "Audits", "List audit runs", "AuditPage", page([examples["audit"]]), parameters=pagination_parameters())
    add("GET", "/audits/{audit_id}", "getAudit", "Audits", "Read audit processing and review counts", "Audit", examples["audit"],
        description="Processing completed does not mean data was accepted. Reviewed count refers only to final decisions on the pinned revisions.")
    add("GET", "/audits/{audit_id}/samples", "listAuditSamples", "Audits", "Read the complete review queue", "AuditSamplePage", page([examples["summary"]]),
        parameters=pagination_parameters() + [parameter("tool_status", "query", TOOL_STATUS, "flagged"), parameter("review_status", "query", SAMPLE_REVIEW_STATUS, "pending_review")],
        description="Omit filters to include all samples, including no_issue cases. Filters are optional UI conveniences; there is no automatic acceptance.")
    add("GET", "/audits/{audit_id}/samples/{sample_id}", "getAuditSample", "Audits", "Read sample findings, evidence and measurements", "SampleAudit", examples["detail"],
        backing="measurements embeds the actual audit_sample report shape; API findings and reviews are planned.",
        description="Measurements are immutable evidence from the pinned sample revision. Their review_status remains pending_review; the separate review record stores actual reviewer decisions. Colored masks are previews. Findings include pass outcomes as well as suspected violations.")
    sample_media = contract["paths"]["/audits/{audit_id}/samples/{sample_id}"]["get"]["responses"]["200"]["content"]["application/json"]
    sample_media.pop("example")
    sample_media["examples"] = {
        name: {"summary": f"Synthetic {name} review state", "value": value}
        for name, value in examples["review_scenarios"].items()
    }
    add("GET", "/audits/{audit_id}/findings", "listAuditFindings", "Audits", "List findings with rule sources and review state", "FindingPage", page([examples["finding"]]),
        parameters=pagination_parameters() + [parameter("sample_id", "query", TEXT, "sample_001"), parameter("outcome", "query", choice("pass", "fail", "unsure", "unavailable"), "fail")])
    add("POST", "/audits/{audit_id}/rechecks", "recheckSamples", "Audits", "Audit corrected sample revisions again", "AuditStarted",
        {"audit": {**examples["audit"], "audit_id": "audit_recheck_demo", "parent_audit_id": "audit_demo", "status": "queued", "processed_count": 0, "job_id": "job_recheck_demo"},
         "job": job("recheck", "audit", "audit_recheck_demo")}, status=202,
        body_schema="RecheckRequest", body_example={"sample_ids": ["sample_001"], "reviewer_id": "reviewer_demo"},
        description="Create a new audit linked to the previous run. Pin latest revisions at request time and keep the original rulebook version and feedback policy. Do not overwrite old evidence or inherit final reviewer decisions.")
    add("GET", "/jobs/{job_id}", "getJob", "Jobs", "Poll progress and completion", "Job", job("audit", "audit", "audit_demo", completed=True),
        description="Poll until completed, failed or cancelled. Follow result.href after completion. total/fraction null means indeterminate progress. Job failures are data in a 200 response, not a successful audit outcome.")
    cancelling = job("audit", "audit", "audit_demo")
    cancelling["status"] = "cancelling"
    add("POST", "/jobs/{job_id}/cancel", "cancelJob", "Jobs", "Request cancellation of pending work", "Job", cancelling, status=202,
        description="Cooperative cancellation stops at a safe processing boundary and preserves completed results. Terminal jobs return 409 JOB_ALREADY_FINISHED.")
    finding_review = {"revision": 1, "resolution": "accepted", "reviewer_id": "reviewer_demo", "comment": "Confirmed the label mismatch.", "updated_at": TIMESTAMP}
    add("PUT", "/findings/{finding_id}/review", "reviewFinding", "Reviews", "Accept or reject a tool finding", "FindingReview", finding_review,
        body_schema="FindingReviewRequest", body_example={"expected_revision": 0, "resolution": "accepted", "reviewer_id": "reviewer_demo", "comment": finding_review["comment"]},
        description="accepted means the reviewer confirms the finding, not that the sample is accepted. rejected dismisses the finding; unsure records unresolved review. Preserve revision history and check optimistic concurrency.")
    reviewed = {"revision": 1, "decision": "needs_correction", "reviewer_id": "reviewer_demo", "comment": "Correct the second label before final review.", "updated_at": TIMESTAMP}
    add("PUT", "/audits/{audit_id}/samples/{sample_id}/review", "reviewSample", "Reviews", "Record the reviewer's data decision", "SampleReview", reviewed,
        body_schema="SampleReviewRequest", body_example={"expected_revision": 0, "decision": "needs_correction", "reviewer_id": "reviewer_demo", "comment": reviewed["comment"]},
        description="Separate from finding resolution. Reviewer controls accepted/rejected decisions, including no_issue samples. needs_correction is not final acceptance. Only decide on the pinned, current sample revision; otherwise return 409 STALE_SAMPLE_REVISION. Save traceable review history.")
    add("GET", "/audits/{audit_id}/samples/{sample_id}/reviews", "listSampleReviews", "Reviews", "Read sample decision history", "SampleReviewPage", page([reviewed]), parameters=pagination_parameters())
    schemas["SampleReviewPage"] = page_schema(ref("SampleReview"))
    add("POST", "/exports", "startExport", "Exports", "Prepare reviewed output and manifest", "ExportStarted",
        {"export": {**examples["export"], "status": "queued", "included_count": None, "excluded_count": None, "download_url": None, "manifest_url": None}, "job": job("export", "export", "export_demo")},
        status=202, body_schema="ExportRequest", body_example={"audit_id": "audit_recheck_demo", "format": "penn_fudan", "selection": "accepted_only", "include_images": False},
        description="accepted_only includes explicit accepted decisions. all_reviewed includes accepted and rejected with their decisions in the manifest. pending_review and needs_correction are excluded. Export pinned reviewed revisions, not silently substituted latest data; stale decisions return 409 STALE_SAMPLE_REVISION. Preserve source coordinates and original files.")
    add("GET", "/exports/{export_id}", "getExport", "Exports", "Read export status and download URLs", "Export", examples["export"])
    add("GET", "/exports/{export_id}/download", "downloadExport", "Exports", "Download corrected dataset archive", "", None, binary=True,
        description="ZIP bytes with annotations, masks and review manifest; images included only when requested. Return 409 EXPORT_NOT_READY until preparation completes.")
    add("GET", "/exports/{export_id}/manifest", "getExportManifest", "Exports", "Read exported and excluded sample decisions", "ExportManifest",
        {"export_id": "export_demo", "audit_id": "audit_recheck_demo", "rulebook_id": "rulebook_demo", "rulebook_version": 2,
         "entries": [{"sample_id": "sample_001", "sample_revision": 2, "included": True, "review_decision": "accepted", "reason": None}]})
    add("GET", "/feedback/examples", "listFeedbackExamples", "Feedback", "List approved reviewer correction examples", "FeedbackExamplePage", page([examples["feedback"]]), parameters=pagination_parameters(),
        description="Examples derive from approved reviews and corrections; storing them is not yet an improvement mechanism. Rejected suggestions are not silently used as correct examples.")
    add("POST", "/feedback/policies", "createFeedbackPolicy", "Feedback", "Define a candidate reviewer-example policy", "FeedbackPolicy", examples["policy"], status=201,
        body_schema="FeedbackPolicyCreate", body_example={"name": "Reviewed examples", "strategy": "reviewer_examples", "example_ids": ["feedback_demo"], "target_check_names": ["person_posture"]},
        description="Planned improvement strategy: retrieve approved correction examples as context for supported model checks. Does not train or download a model. Policy starts as draft; applicability must be checked by the backend.")
    add("GET", "/feedback/policies", "listFeedbackPolicies", "Feedback", "List available feedback policies", "FeedbackPolicyPage", page([examples["policy"]]), parameters=pagination_parameters())
    add("GET", "/feedback/policies/{policy_id}", "getFeedbackPolicy", "Feedback", "Read a feedback policy", "FeedbackPolicy", examples["policy"])
    truth = {"ground_truth_id": "truth_demo", "dataset_id": "dataset_holdout", "version": 1,
             "source_audit_ids": ["audit_holdout_reviewed"], "verified_sample_count": 12, "created_at": TIMESTAMP}
    add("POST", "/feedback/ground-truth", "createGroundTruth", "Feedback", "Freeze reviewer-verified evaluation evidence", "GroundTruth", truth, status=201,
        body_schema="GroundTruthCreate", body_example={"dataset_id": "dataset_holdout", "source_audit_ids": ["audit_holdout_reviewed"], "reviewer_id": "reviewer_demo"},
        description="Snapshot reviewer-verified original/corrected annotations, accepted/rejected findings and checked no-issue cases, including missed errors identified through corrections. Require complete relevant review evidence; return 409 GROUND_TRUTH_NOT_READY otherwise. Evaluator semantics and storage remain planned.")
    add("GET", "/feedback/ground-truth/{ground_truth_id}", "getGroundTruth", "Feedback", "Read a frozen evaluation snapshot", "GroundTruth", truth)
    add("POST", "/feedback/evaluations", "evaluateFeedbackPolicy", "Feedback", "Compare candidate feedback with a baseline", "FeedbackEvaluationStarted",
        {"evaluation": examples["evaluation"], "job": job("feedback_evaluation", "feedback_evaluation", "evaluation_demo")}, status=202,
        body_schema="FeedbackEvaluationRequest", body_example={"policy_id": "policy_demo", "ground_truth_id": "truth_demo", "ground_truth_version": 1},
        description="Evaluate identical held-out samples with and without the candidate policy against frozen reviewer-verified ground truth. Reject overlap with policy example samples (409 EVALUATION_DATA_OVERLAP). A ground-truth revision and comparison evaluator still need implementation. Never treat runtime speed alone as accuracy improvement.")
    metrics = {"precision": 0.8, "recall": 0.8, "f1": 0.8, "true_positives": 8, "false_positives": 2, "false_negatives": 2, "sample_count": 12, "elapsed_seconds": 20.0}
    add("GET", "/feedback/evaluations/{evaluation_id}", "getFeedbackEvaluation", "Feedback", "Read held-out feedback evaluation results", "FeedbackEvaluation",
        {**examples["evaluation"], "status": "completed", "baseline": metrics,
         "candidate": {**metrics, "precision": 0.9, "recall": 0.9, "f1": 0.9, "true_positives": 9, "false_positives": 1, "false_negatives": 1, "elapsed_seconds": 22.0}},
        description="Synthetic numbers illustrate the shape only. Metrics remain null until measured. Undefined precision/recall denominators produce null, not invented perfect scores.")
    add("GET", "/feedback/evaluations", "listFeedbackEvaluations", "Feedback", "List feedback evaluation jobs", "FeedbackEvaluationPage", page([examples["evaluation"]]), parameters=pagination_parameters())
    add("POST", "/feedback/policies/{policy_id}/activate", "activateFeedbackPolicy", "Feedback", "Approve an evaluated policy for future audits", "FeedbackPolicy",
        {**examples["policy"], "status": "active", "evaluation_id": "evaluation_demo"},
        body_schema="ActivatePolicyRequest", body_example={"evaluation_id": "evaluation_demo", "reviewer_id": "reviewer_demo"},
        description="Require a completed matching evaluation and explicit reviewer activation. Does not alter historical audits or override final reviewer control. Return 409 POLICY_NOT_EVALUATED if the evaluation is missing or unfinished.")

    contract["x-template-operations"] = operations
    contract["x-review-scenarios"] = examples["review_scenarios"]
    contract["x-review-scenario-rulebooks"] = examples["review_scenario_rulebooks"]
    return contract


def main() -> None:
    contract = build_contract()
    fixtures = contract.pop("x-template-operations")
    scenarios = contract.pop("x-review-scenarios")
    scenario_rulebooks = contract.pop("x-review-scenario-rulebooks")
    for filename, content in (("openapi.json", contract), ("examples.json", {
        "notice": "Proposed contract and synthetic frontend fixtures. No HTTP server is implemented.",
        "base_url": "http://127.0.0.1:8000", "operations": fixtures,
        "review_scenarios": scenarios, "review_scenario_rulebooks": scenario_rulebooks,
    })):
        (API_DIRECTORY / filename).write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n")
    lines = ["# Proposed endpoint index", "", "Generated from `build_contract.py`. All HTTP routes are planned; none are live yet.", "",
             "Paths include the `/api/v1` prefix. See `openapi.json` for schemas and `examples.json` for frontend fixtures.", ""]
    for tag in contract["tags"]:
        lines.extend([f"## {tag['name']}", ""])
        for operation in fixtures:
            if operation["tag"] == tag["name"]:
                lines.append(f"- `{operation['method']} {operation['path']}` — {operation['summary']} (`{operation['operation_id']}`).")
        lines.append("")
    (API_DIRECTORY / "ENDPOINTS.md").write_text("\n".join(lines))
    print(f"Generated {len(fixtures)} endpoint templates and {len(contract['components']['schemas'])} schemas.")


if __name__ == "__main__":
    main()
