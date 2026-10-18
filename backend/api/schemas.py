"""Resource schemas, reusing implemented Python models where available."""

from backend.app.models.finding import Finding
from backend.app.models.review import (
    FindingReviewRequest,
    RulebookAction, RulebookReplace, RulebookReviewPage, RulebookReviewRecord,
    SampleReview, SampleReviewRequest, SampleReviewRecord, SampleReviewPage,
)
from backend.app.models.rule import Rulebook

from .common import (
    BOOLEAN, COUNT, FRACTION, INTEGER, JOB_STATUS, SAMPLE_REVIEW_STATUS,
    TEXT, TIME, TOOL_STATUS, VERSION, JsonObject, array, choice, nullable,
    obj, page_schema, ref, replace_model_refs,
)


def build_schemas() -> JsonObject:
    """Reuse implemented records; keep proposed transport records explicit."""
    schemas = {}
    for model in (
        Rulebook, Finding, FindingReviewRequest,
        RulebookReplace, RulebookAction, RulebookReviewRecord, RulebookReviewPage,
        SampleReview, SampleReviewRequest, SampleReviewRecord, SampleReviewPage,
    ):
        schema = model.model_json_schema()
        schemas.update({name: replace_model_refs(definition)
                        for name, definition in schema.pop("$defs", {}).items()})
        schemas[model.__name__] = replace_model_refs(schema)
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
    schemas["FindingPage"] = page_schema(ref("Finding"))
    schemas["SampleAuditSummary"] = obj({
        "sample_id": TEXT, "sample_revision": VERSION, "tool_status": TOOL_STATUS,
        "review_status": SAMPLE_REVIEW_STATUS,
        "finding_counts": obj({name: COUNT for name in ("pass", "fail", "unsure", "unavailable")}),
        "thumbnail_url": nullable(TEXT),
    })
    schemas["AuditSamplePage"] = page_schema(ref("SampleAuditSummary"))
    schemas["SampleAudit"] = obj({
        "audit_id": TEXT, "dataset_id": TEXT, "summary": ref("SampleAuditSummary"),
        "assets": ref("SampleAssets"), "measurements": ref("AuditMeasurements"),
        "findings": array(ref("Finding")), "review": ref("SampleReview"),
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
