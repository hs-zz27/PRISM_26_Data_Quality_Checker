"""Resource schemas, reusing implemented Python models where available."""

from backend.app.models.dataset import Dataset, DatasetPage, Sample, SamplePage, DatasetImportStarted
from backend.app.core.dataset_http import DatasetUpload
from backend.app.models.finding import Finding
from backend.app.models.audit import AuditRun
from backend.app.models.audit_read import AuditSamplePage, SampleAudit, SampleAuditSummary
from backend.app.models.job import ApiError, ErrorDetail, Job, Progress, ResultReference
from backend.app.models.review import (
    FindingReviewPage, FindingReviewRecord, FindingReviewRequest,
    RulebookAction, RulebookReplace, RulebookReviewPage, RulebookReviewRecord,
    SampleReview, SampleReviewRequest, SampleReviewRecord, SampleReviewPage,
)
from backend.app.models.rule import Rulebook, RulebookPage
from backend.app.models.rulebook_creation import RulebookCreate, RulebookCreateRule
from backend.app.models.rule_extraction import RuleExtractionRequest

from .common import (
    BOOLEAN, COUNT, FRACTION, INTEGER, JOB_STATUS, SAMPLE_REVIEW_STATUS,
    TEXT, TIME, VERSION, JsonObject, array, choice, nullable,
    obj, page_schema, ref, replace_model_refs,
)

def build_schemas() -> JsonObject:
    """Reuse implemented records; keep proposed transport records explicit."""
    schemas = {}
    for model in (
        RuleExtractionRequest, Rulebook, RulebookPage, RulebookCreate, RulebookCreateRule, Finding, FindingReviewRequest, FindingReviewRecord, FindingReviewPage,
        RulebookReplace, RulebookAction, RulebookReviewRecord, RulebookReviewPage,
        SampleReview, SampleReviewRequest, SampleReviewRecord, SampleReviewPage,
        AuditSamplePage, SampleAudit, SampleAuditSummary,
        ErrorDetail, ApiError, Progress, ResultReference, Job, AuditRun,
        Dataset, DatasetPage, Sample, SamplePage, DatasetImportStarted, DatasetUpload,
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
    schemas["CorrectionBoundingBox"] = {
        **array(VERSION), "minItems": 4, "maxItems": 4,
        "description": "[xmin, ymin, xmax, ymax], one-based inclusive positive integers. The server also validates ordering and image bounds.",
    }
    schemas["CorrectionObject"]["properties"]["bbox"] = ref("CorrectionBoundingBox")
    schemas["ErrorResponse"] = obj({"error": ref("ApiError")})
    schemas["MaskUploadRequest"] = obj({
        "mask": {"type": "string", "format": "binary"}, "expected_revision": VERSION,
    })
    schemas["MaskUpload"] = obj({
        "mask_upload_id": TEXT, "dataset_id": TEXT, "sample_id": TEXT,
        "base_revision": VERSION, "image_size": ref("ImageSize"),
        "mask_ids": array(COUNT), "expires_at": TIME,
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
        "sample_ids": nullable({**array(TEXT), "minItems": 1, "uniqueItems": True}),
        "feedback_policy_id": nullable(TEXT),
    })
    # Future entry points reuse the implemented audit transport. Relationship
    # context belongs to the planned start wrapper, not the stored public model.
    schemas["Audit"] = ref("AuditRun")
    schemas["AuditPage"] = page_schema(ref("AuditRun"))
    schemas["AuditStarted"] = obj({
        "audit": ref("AuditRun"), "job": ref("Job"),
        "parent_audit_id": {**nullable(TEXT), "default": None},
        "feedback_policy_id": {**nullable(TEXT), "default": None},
    }, required=["audit", "job"])
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
    schemas["RecheckRequest"] = obj({
        "sample_ids": {**array(TEXT), "minItems": 1, "uniqueItems": True},
        "sample_revisions": {
            "type": "object", "minProperties": 1, "additionalProperties": VERSION,
            "description": "Expected current revisions; keys must exactly match sample_ids. Reject stale revisions rather than auditing a different selection.",
        },
        "reviewer_id": TEXT,
    })
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
