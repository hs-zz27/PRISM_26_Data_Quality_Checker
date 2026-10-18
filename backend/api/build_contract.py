"""Assemble and write proposed endpoints and frontend contract artifacts.

Run from the project root with python -m backend.api.build_contract.
These templates do not register or run HTTP routes.
"""

from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any

from .common import (
    BASE_PATH, SAMPLE_REVIEW_STATUS, TEXT, TOOL_STATUS, VERSION,
    JsonObject, choice, ref,
)
from .fixtures import EXAMPLE_MODEL, SOURCE, TIMESTAMP, build_examples, job, page
from .schemas import build_schemas


API_DIRECTORY = Path(__file__).resolve().parent


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
    checks = ["allowed_labels", "annotation_metadata", "mask_dimensions", "mask_ids", "box_validity", "box_iou", "box_mask_alignment", "mask_outside_box", "mask_box_fill", "mask_components"]
    add("GET", "/capabilities", "getCapabilities", "System", "List supported and planned checks", "Capabilities",
        {"dataset_formats": ["penn_fudan"], "guideline_input_formats": ["plain_text"], "coordinate_convention": "one_based_inclusive",
         "checks": [{"name": name, "implementation": "implemented"} for name in checks] +
                   [{"name": name, "implementation": "planned"} for name in
                    ("person_detection", "detection_annotation_matches", "detection_mask_coverage", "mask_person_count", "person_posture")],
         "execution_device": "cpu"},
        backing="Existing measurement functions; registry and HTTP route are planned.")
    add("GET", "/system/status", "getSystemStatus", "System", "Inspect local model readiness", "SystemStatus",
        {"ollama": {"reachable": False, "model": EXAMPLE_MODEL, "model_available": None},
         "detector": {"weights_cached": True, "integration_ready": False}, "execution_device": "cpu"},
        description="Read-only readiness. Does not start services, download models or change configuration.")
    add("POST", "/rulebooks", "createRulebook", "Rulebooks", "Create a manually assembled draft", "Rulebook", examples["manual_draft"], status=201,
        body_schema="RulebookCreate", body_example={"guideline_text": SOURCE, "rules": examples["manual_draft"]["rules"]},
        backing="Rule/Rulebook validation and PostgreSQL storage exist; manual creation service and route are planned.",
        description="Server assigns rulebook_id and version 1. The book starts as draft. Reviewer-authored rules use origin reviewer and may have no citation.")
    add("POST", "/rulebooks/extractions", "extractRulebook", "Rulebooks", "Propose draft rules from plain text", "Job", job("rule_extraction", "rulebook", "rulebook_demo"), status=202,
        body_schema="RuleExtractionRequest", body_example={"guideline_text": SOURCE}, backing="extract_and_save_rulebook coordinates extraction and PostgreSQL storage; jobs and route are planned.",
        description="Poll GET /jobs/{job_id}. On completion, result.href points to the saved draft rulebook. Failure is stored in job.error, including MODEL_UNAVAILABLE or INVALID_RULE_PROPOSALS.")
    add("GET", "/rulebooks", "listRulebooks", "Rulebooks", "List latest rulebook versions", "RulebookPage", page([examples["confirmed"]]), parameters=pagination_parameters(),
        backing="RulebookRepository.list_rulebooks exists; route is planned.")
    add("GET", "/rulebooks/{rulebook_id}", "getRulebook", "Rulebooks", "Read a rulebook version", "Rulebook", examples["confirmed"],
        parameters=[parameter("version", "query", VERSION, examples["confirmed"]["version"], description="Omit for latest; audits must reference a specific version.")],
        backing="RulebookRepository.get_rulebook exists; route is planned.")
    add("GET", "/rulebooks/{rulebook_id}/versions", "listRulebookVersions", "Rulebooks", "Read rulebook version history", "RulebookPage", page([examples["draft"], examples["reviewed"], examples["confirmed"]]), parameters=pagination_parameters(),
        backing="RulebookRepository.list_rulebook_versions exists; route is planned.",
        description="Snapshots are ordered by ascending version. Unknown rulebooks return 404; an offset past the last version returns an empty page.")
    add("GET", "/rulebooks/{rulebook_id}/reviews", "listRulebookReviews", "Rulebooks", "Read rulebook review attribution", "RulebookReviewPage",
        page([
            {"rulebook_id": "rulebook_demo", "version": 2, "reviewer_id": "reviewer_demo", "action": "edit", "created_at": TIMESTAMP},
            {"rulebook_id": "rulebook_demo", "version": 3, "reviewer_id": "reviewer_demo", "action": "confirm", "created_at": TIMESTAMP},
        ]),
        parameters=pagination_parameters(), backing="RulebookRepository.list_rulebook_reviews exists; route is planned.",
        description="Attributed review actions ordered by ascending version. Each action and snapshot commit together. Extraction and older low-level saves have no invented review records. reviewer_id is attribution, not authentication.")
    add("PUT", "/rulebooks/{rulebook_id}", "replaceDraftRulebook", "Rulebooks", "Save a reviewed draft", "Rulebook", examples["reviewed"],
        body_schema="RulebookReplace", body_example={"expected_version": 1, "reviewer_id": "reviewer_demo", "guideline_text": SOURCE, "rules": examples["confirmed"]["rules"]},
        backing="save_rulebook_review service and transactional PostgreSQL review history exist; route is planned.",
        description="Only latest drafts are editable. Submit the complete rule list and explicit rule decisions; omission removes a rule. Retained IDs preserve origin; added IDs must use reviewer origin. All citations must match the supplied text. Save increments version, attributes the reviewer and preserves history. The book stays draft; example save confirms individual rules only.")
    add("POST", "/rulebooks/{rulebook_id}/confirm", "confirmRulebook", "Rulebooks", "Confirm a reviewed rulebook", "Rulebook", examples["confirmed"],
        body_schema="RulebookAction", body_example={"expected_version": 2, "reviewer_id": "reviewer_demo"},
        backing="confirm_rulebook service and transactional PostgreSQL review history exist; route is planned.",
        description="Only a latest draft can be confirmed. Require a nonempty book with all included rules confirmed. Rejected rules must be removed; prior versions preserve them. Append expected_version + 1 as a frozen confirmed snapshot for audits. 409 RULEBOOK_NOT_READY when rules are unconfirmed or empty.")
    add("POST", "/rulebooks/{rulebook_id}/reject", "rejectRulebook", "Rulebooks", "Reject a draft rulebook", "Rulebook", {**examples["draft"], "version": 2, "confirmation_state": "rejected"},
        body_schema="RulebookAction", body_example={"expected_version": 1, "reviewer_id": "reviewer_demo"},
        backing="reject_rulebook service and transactional PostgreSQL review history exist; route is planned.",
        description="Reject the latest draft as a whole. Append a new version without changing individual rule decisions. Start a revision before further edits. This example is an alternative branch from the original version-1 draft, not a step after confirmation.")
    add("POST", "/rulebooks/{rulebook_id}/revisions", "reviseRulebook", "Rulebooks", "Start a new draft from a final version", "Rulebook", {**examples["draft"], "version": 4}, status=201,
        body_schema="RulebookAction", body_example={"expected_version": 3, "reviewer_id": "reviewer_demo"},
        backing="revise_rulebook service and transactional PostgreSQL review history exist; route is planned.",
        description="Require the latest confirmed or rejected book. Keep its snapshot immutable; append expected_version + 1 as a draft and reset every included rule to draft. Draft books are already editable. Invalid state transitions return 409 INVALID_RULEBOOK_STATE; stale expected_version returns 409 VERSION_CONFLICT.")
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
    audit_request = {"dataset_id": "dataset_demo", "rulebook_id": "rulebook_demo", "rulebook_version": examples["confirmed"]["version"], "sample_ids": None, "feedback_policy_id": None}
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
        backing="AuditRepository measurements, FindingRepository current reviews and SampleReviewRepository current decisions exist; asset/detail composition and HTTP adapter planned.",
        description="Measurements retain the stored audit sample pin. Their review_status remains pending_review; the separate review record stores actual reviewer decisions and the audited sample revision. Colored masks are previews. Findings include pass outcomes as well as suspected violations. Current storage does not certify that on-disk assets still match the pin; immutable revision loading remains planned.")
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
        body_schema="FindingReviewRequest", body_example={"expected_review_version": 0, "resolution": "accepted", "reviewer_id": "reviewer_demo", "comment": finding_review["comment"]},
        backing="review_finding service and PostgreSQL finding review history implemented; HTTP adapter planned",
        description="accepted confirms the tool's conclusion; rejected dismisses it; unsure records unresolved review. This does not decide sample acceptance or change machine evidence. Save at expected_review_version + 1 and retain every earlier decision. A stale expected_review_version returns 409 VERSION_CONFLICT; reload before resubmitting. Pending findings start at revision 0.")
    reviewed = {"audit_id": "audit_demo", "sample_id": "sample_001", "sample_revision": 1,
                "revision": 1, "decision": "needs_correction", "reviewer_id": "reviewer_demo", "comment": "Correct the second label before final review.", "updated_at": TIMESTAMP}
    add("PUT", "/audits/{audit_id}/samples/{sample_id}/review", "reviewSample", "Reviews", "Record the reviewer's data decision", "SampleReview", reviewed,
        body_schema="SampleReviewRequest", body_example={"expected_sample_revision": 1, "expected_review_version": 0, "decision": "needs_correction", "reviewer_id": "reviewer_demo", "comment": reviewed["comment"]},
        backing="review_sample service and PostgreSQL sample review history implemented; HTTP adapter planned",
        description="Separate from finding resolution. Reviewer controls accepted/rejected/needs_correction decisions, including no_issue or no-findings samples; pending_review explicitly reopens review with attribution. Completed samples (including incomplete measurements) and failed samples are eligible regardless of parent audit status; queued/running/cancelled samples return 409 INVALID_SAMPLE_REVIEW_STATE. expected_sample_revision must identify a stored pin for this audit/sample, otherwise 409 STALE_SAMPLE_REVISION (404 if the audit/sample is missing). It does not check current dataset files: authoritative annotation revisions and immutable asset loading remain planned. expected_review_version is the last-read review revision, starting at 0; each decision advances it by one. A stale review revision returns 409 VERSION_CONFLICT. Machine evidence, failures, progress and finding resolutions stay unchanged.")
    add("GET", "/audits/{audit_id}/samples/{sample_id}/reviews", "listSampleReviews", "Reviews", "Read sample decision history", "SampleReviewPage", page([reviewed]),
        parameters=pagination_parameters() + [parameter("sample_revision", "query", VERSION, 1, required=True,
            description="Exact stored audit sample revision; history never merges other pins or audits.")],
        backing="SampleReviewRepository.list_sample_reviews implemented; HTTP adapter planned",
        description="Attributed decisions in ascending review revision order, scoped to the audit, sample and required sample_revision. A never-reviewed pin has an empty history and current pending_review revision 0. Explicit reopening is retained in history. An unknown audit/sample returns 404; an unmatched sample revision returns 409 STALE_SAMPLE_REVISION.")
    add("POST", "/exports", "startExport", "Exports", "Prepare reviewed output and manifest", "ExportStarted",
        {"export": {**examples["export"], "status": "queued", "included_count": None, "excluded_count": None, "download_url": None, "manifest_url": None}, "job": job("export", "export", "export_demo")},
        status=202, body_schema="ExportRequest", body_example={"audit_id": "audit_recheck_demo", "format": "penn_fudan", "selection": "accepted_only", "include_images": False},
        description="accepted_only includes explicit accepted decisions. all_reviewed includes accepted and rejected with their decisions in the manifest. pending_review and needs_correction are excluded. Export pinned reviewed revisions, not silently substituted latest data; stale decisions return 409 STALE_SAMPLE_REVISION. Preserve source coordinates and original files.")
    add("GET", "/exports/{export_id}", "getExport", "Exports", "Read export status and download URLs", "Export", examples["export"])
    add("GET", "/exports/{export_id}/download", "downloadExport", "Exports", "Download corrected dataset archive", "", None, binary=True,
        description="ZIP bytes with annotations, masks and review manifest; images included only when requested. Return 409 EXPORT_NOT_READY until preparation completes.")
    add("GET", "/exports/{export_id}/manifest", "getExportManifest", "Exports", "Read exported and excluded sample decisions", "ExportManifest",
        {"export_id": "export_demo", "audit_id": "audit_recheck_demo", "rulebook_id": "rulebook_demo", "rulebook_version": examples["confirmed"]["version"],
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


def render_artifacts() -> dict[str, str]:
    """Render all generated files without writing them, for reproducible updates."""
    contract = build_contract()
    fixtures = contract.pop("x-template-operations")
    scenarios = contract.pop("x-review-scenarios")
    scenario_rulebooks = contract.pop("x-review-scenario-rulebooks")
    documents = {"openapi.json": contract, "examples.json": {
        "notice": "Proposed contract and synthetic frontend fixtures. No HTTP server is implemented.",
        "base_url": "http://127.0.0.1:8000", "operations": fixtures,
        "review_scenarios": scenarios, "review_scenario_rulebooks": scenario_rulebooks,
    }}
    artifacts = {
        filename: json.dumps(content, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        for filename, content in documents.items()
    }
    lines = ["# Proposed endpoint index", "", "Generated from `build_contract.py`. All HTTP routes are planned; none are live yet.", "",
             "Paths include the `/api/v1` prefix. See `openapi.json` for schemas and `examples.json` for frontend fixtures.", ""]
    for tag in contract["tags"]:
        lines.extend([f"## {tag['name']}", ""])
        for operation in fixtures:
            if operation["tag"] == tag["name"]:
                lines.append(f"- `{operation['method']} {operation['path']}` — {operation['summary']} (`{operation['operation_id']}`).")
        lines.append("")
    artifacts["ENDPOINTS.md"] = "\n".join(lines)
    return artifacts


def main() -> None:
    for filename, content in render_artifacts().items():
        (API_DIRECTORY / filename).write_text(content, encoding="utf-8")
    print("Generated OpenAPI schemas, frontend fixtures and the endpoint index.")


if __name__ == "__main__":
    main()
