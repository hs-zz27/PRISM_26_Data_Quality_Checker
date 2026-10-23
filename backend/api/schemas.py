"""Resource schemas, reusing implemented Python models where available."""

from backend.app.models.correction import (CorrectionObject, MaskUploadRequest, MaskUpload, CorrectionRequest, AnnotationRevision, RevisionPage, RecheckRequest)

from backend.app.models.feedback import (CaseView,FeedbackExamplePage,FeedbackPolicyCreate,FeedbackPolicy,FeedbackPolicyPage,
    GroundTruthCreate,GroundTruth,FeedbackEvaluationCreate,FeedbackEvaluation,FeedbackEvaluationStarted,
    FeedbackEvaluationPage,FeedbackPolicyActivate)

from backend.app.models.dataset import Dataset, DatasetPage, Sample, SamplePage, DatasetImportStarted
from backend.app.core.dataset_http import DatasetUpload
from backend.app.models.finding import Finding
from backend.app.models.export import (
    ExportRequest, Export, ExportStarted, ExportPage, ExportManifestEntry, ExportManifest,
)
from backend.app.models.audit import AuditRun
from backend.app.models.audit_submission import AuditPage, AuditRequest, AuditStarted
from backend.app.models.audit_read import AuditSamplePage, FindingPage, SampleAudit, SampleAuditSummary
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
    obj, ref, replace_model_refs,
)

def build_schemas() -> JsonObject:
    """Reuse implemented records; keep proposed transport records explicit."""
    schemas = {}
    for model in (
        RuleExtractionRequest, Rulebook, RulebookPage, RulebookCreate, RulebookCreateRule, Finding, FindingReviewRequest, FindingReviewRecord, FindingReviewPage,
        RulebookReplace, RulebookAction, RulebookReviewRecord, RulebookReviewPage,
        SampleReview, SampleReviewRequest, SampleReviewRecord, SampleReviewPage,
        AuditSamplePage, FindingPage, SampleAudit, SampleAuditSummary,
        ErrorDetail, ApiError, Progress, ResultReference, Job, AuditRun, AuditPage, AuditRequest, AuditStarted,
        Dataset, DatasetPage, Sample, SamplePage, DatasetImportStarted, DatasetUpload,
        ExportRequest, Export, ExportStarted, ExportPage, ExportManifestEntry, ExportManifest,
        CorrectionObject, MaskUploadRequest, MaskUpload, CorrectionRequest, AnnotationRevision, RevisionPage, RecheckRequest,
        CaseView,FeedbackExamplePage,FeedbackPolicyCreate,FeedbackPolicy,FeedbackPolicyPage,GroundTruthCreate,GroundTruth,
        FeedbackEvaluationCreate,FeedbackEvaluation,FeedbackEvaluationStarted,FeedbackEvaluationPage,FeedbackPolicyActivate,
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
    schemas["ErrorResponse"] = obj({"error": ref("ApiError")})
    # Planned consumers reuse the implemented audit transport. Relationship
    # context belongs to AuditStarted, not the stored public model.
    schemas["Audit"] = ref("AuditRun")
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
    # FindingPage comes from the live Pydantic model exported in the loop above.
    # Health, Capabilities and SystemStatus come from the live Pydantic models; see system_contract.py.
    return schemas
