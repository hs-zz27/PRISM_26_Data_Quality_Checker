"""Assemble and write API endpoints and frontend contract artifacts.

Run from the project root with python -m backend.api.build_contract.
Schema generation imports the app without serving requests or connecting to storage.
"""

from backend.app.models.audit_read import FindingPage
from .feedback_contract import align_feedback_contract,feedback_job_scenarios
from .feedback_examples import feedback_examples

from .correction_contract import align_correction_contract, recheck_job_scenarios

from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any

from backend.app.checks.registry import CHECK_REGISTRY, extraction_check_catalog
from backend.app.main import create_app

from .common import (
    BASE_PATH, SAMPLE_REVIEW_STATUS, TEXT, TOOL_STATUS, VERSION,
    JsonObject, choice, ref,
)
from .fixtures import EXAMPLE_MODEL, SOURCE, TIMESTAMP, build_examples, job, page
from .schemas import build_schemas
from .dataset_contract import align_dataset_contract
from .export_contract import align_export_contract
from .system_contract import align_system_contract
from .planned_endpoints import render_planned_endpoints

API_DIRECTORY = Path(__file__).resolve().parent
RULEBOOK_ERROR_EXAMPLES = {
    404: {"NOT_FOUND": "The requested rulebook or version does not exist."},
    503: {"DEPENDENCY_UNAVAILABLE": "Rulebook storage is unavailable."},
}

def parameter(name: str, location: str, schema: JsonObject, example: Any, *, required: bool = False,
              description: str = "") -> JsonObject:
    return {"name": name, "in": location, "required": required or location == "path", "schema": schema,
            "example": example, "description": description}

def pagination_parameters() -> list[JsonObject]:
    return [parameter("limit", "query", {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}, 20),
            parameter("offset", "query", {"type": "integer", "minimum": 0, "default": 0}, 0)]

def error_example(code: str, message: str) -> JsonObject:
    return {"error": {"code": code, "message": message, "details": [], "request_id": "request_demo"}}

def build_contract() -> JsonObject:
    examples = build_examples()
    schemas = build_schemas()
    contract: JsonObject = {
        "openapi": "3.1.0",
        "info": {"title": "PRISM Data Quality Checker — API contract", "version": "0.1.0",
                 "description": "Combined implementation and planning reference. Audit creation/listing/metadata/queue/detail reads, revision-pinned asset delivery, job status/cancellation, finding/sample review, plain-text extraction jobs, manual rulebook creation and saved-rulebook read/review operations are implemented. Dataset import and catalogue reads are implemented in the applied checkpoint source. Liveness, capabilities and local readiness reads and the audit-wide finding list are implemented. No operations are currently planned; x-implementation-status remains on every operation. Use implemented-openapi.json for clients targeting the running backend. Examples are synthetic, not model accuracy results."},
        "servers": [{"url": "http://127.0.0.1:8000/api/v1", "description": "Local development server; only implemented operations are callable."}],
        "tags": [{"name": name} for name in ("System", "Rulebooks", "Datasets", "Jobs", "Audits", "Reviews", "Corrections", "Exports", "Feedback")],
        "paths": {}, "components": {"schemas": schemas},
    }
    operations: list[JsonObject] = []

    def add(method: str, path: str, operation_id: str, tag: str, summary: str, response_schema: str,
            response_example: Any, *, status: int = 200, body_schema: str | None = None,
            body_example: Any = None, parameters: list[JsonObject] | None = None,
            description: str = "", backing: str = "planned", multipart: bool = False,
            binary: bool = False, implementation: str = "planned",
            conflict_examples: dict[str, str] | None = None,
            error_examples: dict[int, dict[str, str]] | None = None,
            not_found: bool = True) -> None:
        params = list(parameters or [])
        values = {"rulebook_id": "rulebook_demo", "dataset_id": "dataset_demo", "sample_id": "sample_001",
                  "audit_id": "audit_demo", "job_id": "job_audit_demo", "finding_id": "finding_demo",
                  "export_id": "export_demo", "policy_id": "policy_demo", "evaluation_id": "evaluation_demo",
                  "ground_truth_id": "truth_demo"}
        for name in re.findall(r"\{([^}]+)\}", path):
            if not any(item["name"] == name and item["in"] == "path" for item in params):
                identifier = {**TEXT, "pattern": r"\S"}
                params.append(parameter(name, "path", identifier, values[name]))
        media = "application/zip" if binary else "application/json"
        success_content = {"schema": {"type": "string", "format": "binary"} if binary else ref(response_schema)}
        if not binary:
            success_content["example"] = deepcopy(response_example)
        success_description = (
            "Cancellation requested; inspect the returned job state." if operation_id == "cancelJob"
            else "Job accepted; poll the returned job ID." if status == 202 else summary
        )
        responses = {str(status): {"description": success_description,
                                  "content": {media: success_content}}}
        errors = {
            400: ("INVALID_REQUEST", "The request could not be processed."),
            404: ("NOT_FOUND", "The requested resource does not exist."),
            409: ("VERSION_CONFLICT", "The resource changed; refresh before saving."),
            422: ("VALIDATION_ERROR", "One or more request fields are invalid."),
            503: ("DEPENDENCY_UNAVAILABLE", "A required local model or service is unavailable."),
            500: ("INTERNAL_ERROR", "The request could not be completed."),
        }
        for extra_status, examples_by_code in (error_examples or {}).items():
            if extra_status not in errors:
                errors[extra_status] = next(iter(examples_by_code.items()))
        for error_status, (code, message) in errors.items():
            if implementation == "implemented":
                if error_status == 404:
                    message = (
                        "The requested rulebook or version does not exist." if path.startswith("/rulebooks")
                        else "The requested job does not exist." if path.startswith("/jobs")
                        else "The requested sample asset does not exist." if path.startswith("/datasets")
                        else "The requested audit, sample, or finding does not exist."
                    )
                elif error_status == 503:
                    message = "Rulebook storage is unavailable." if path.startswith("/rulebooks") else "Audit storage is unavailable."
            if (implementation == "implemented" and error_status == 400) or (error_status == 404 and not not_found):
                continue
            if error_status == 409 and not conflict_examples:
                continue
            content = {"schema": ref("ErrorResponse")}
            if error_status == 409 and conflict_examples:
                content["examples"] = {
                    name: {"summary": detail, "value": error_example(name, detail)}
                    for name, detail in conflict_examples.items()
                }
            elif error_examples and error_status in error_examples:
                content["examples"] = {
                    name: {"summary": detail, "value": error_example(name, detail)}
                    for name, detail in error_examples[error_status].items()
                }
            else:
                content["example"] = error_example(code, message)
            responses[str(error_status)] = {
                "description": (
                    "Conflict; see the error code."
                    if error_status == 409 and conflict_examples else message
                ),
                "content": {"application/json": content},
            }
        operation = {"operationId": operation_id, "tags": [tag], "summary": summary,
                     "description": description or summary, "x-implementation-status": implementation,
                     "x-backing-service-status": backing, "responses": responses}
        if params:
            operation["parameters"] = params
        if body_schema is not None:
            request_media = "multipart/form-data" if multipart else "application/json"
            operation["requestBody"] = {"required": True, "content": {request_media: {
                "schema": ref(body_schema), "example": deepcopy(body_example)}}}
        if method.lower() in contract["paths"].get(path, {}):
            raise ValueError("Duplicate contract method/path.")
        if any(entry["operation_id"] == operation_id for entry in operations):
            raise ValueError("Duplicate contract operation ID.")
        contract["paths"].setdefault(path, {})[method.lower()] = operation
        operations.append({"operation_id": operation_id, "method": method.upper(), "path": BASE_PATH + path,
                           "tag": tag, "summary": summary, "implementation": implementation,
                           "backing_service": backing, "request": {
                               "path_parameters": {p["name"]: p["example"] for p in params if p["in"] == "path"},
                               "query": {p["name"]: p["example"] for p in params if p["in"] == "query"},
                               "content_type": ("multipart/form-data" if multipart else "application/json") if body_schema else None,
                               "body": deepcopy(body_example)},
                           "response": {"status": status, "content_type": media, "body": None if binary else deepcopy(response_example)}})

    add("GET", "/health", "getHealth", "System", "Check HTTP server liveness", "Health", {"status": "ok", "api_version": "v1"},
        description="Liveness only. Ollama being offline must not make this endpoint fail.")
    checks = sorted(CHECK_REGISTRY)
    add("GET", "/capabilities", "getCapabilities", "System", "List supported and planned checks", "Capabilities",
        {"dataset_formats": ["penn_fudan"], "guideline_input_formats": ["plain_text"], "coordinate_convention": "one_based_inclusive",
         "checks": [{"name": name, "implementation": "implemented"} for name in checks] +
                   [{"name": name, "implementation": "planned"} for name in
                    ("person_detection", "detection_annotation_matches", "detection_mask_coverage", "mask_person_count", "person_posture")],
         "execution_device": "cpu"},
        backing="CHECK_REGISTRY and measurement/evaluation services exist; the HTTP route is planned.")
    add("GET", "/system/status", "getSystemStatus", "System", "Inspect local model readiness", "SystemStatus",
        {"ollama": {"reachable": False, "model": EXAMPLE_MODEL, "model_available": None},
         "detector": {"weights_cached": True, "integration_ready": False}, "execution_device": "cpu"},
        description="Read-only readiness. Does not start services, download models or change configuration.")
    add("POST", "/rulebooks", "createRulebook", "Rulebooks", "Create a manually assembled draft", "Rulebook", examples["manual_draft"], status=201,
        body_schema="RulebookCreate", body_example=examples["manual_create"],
        implementation="implemented", error_examples=RULEBOOK_ERROR_EXAMPLES,
        conflict_examples={"VERSION_CONFLICT": "The rulebook is out of date. Refresh before resubmitting."},
        backing="create_rulebook service, RulebookRepository.save_rulebook(expected_version=0) and FastAPI route implemented.",
        description="Manually create a version-1 draft with server-assigned ID, reviewer origin and draft states. Exact guideline text is preserved; non-null citations must match exactly. Rules may be empty or omitted, and guideline_text may be empty. Unknown fields and client attempts to supply server metadata are rejected with safe 422 VALIDATION_ERROR. Unsupported or null checks remain for human review without inferred thresholds. Initial review history is empty; creation does not confirm rules, call a model or start jobs/audits. Separate successful POST requests create separate books, without automatic retries or idempotency. A generated-ID collision returns 409 VERSION_CONFLICT without overwriting or appending a version. Storage/commit failures return safe 503, and unexpected internal validation/response defects remain 500.")
    add("POST", "/rulebooks/extractions", "extractRulebook", "Rulebooks", "Propose draft rules from plain text", "Job", job("rule_extraction", "rulebook", "rulebook_demo"), status=202,
        body_schema="RuleExtractionRequest", body_example={"guideline_text": SOURCE}, implementation="implemented",
        error_examples={404: RULEBOOK_ERROR_EXAMPLES[404], 503: {"DEPENDENCY_UNAVAILABLE": "Extraction could not be durably accepted."}},
        backing="ExtractionRepository atomically enqueues private inputs; explicitly started supervised extraction worker atomically publishes draft and completed job.",
        description="Durably accept exact nonblank plain text without inline inference or a placeholder book. HTTP 202 does not establish worker readiness. Start the extraction worker separately. Poll GET /jobs/{job_id}; every state, including failed, remains HTTP 200. One processing attempt only; stale attempts fail without replay. Completion publishes one version-1 extracted/draft rulebook, empty review history and a result.href pinned with ?version=1. Existing cancellation route is supported; cancelled jobs publish no output. Errors are safe job.error codes, not retrospective submission errors. See EXTRACTIONS.md for error mapping, finite deadlines and operator workflow.")
    add("GET", "/rulebooks", "listRulebooks", "Rulebooks", "List latest rulebook versions", "RulebookPage", page([examples["confirmed"]]), parameters=pagination_parameters(),
        implementation="implemented", error_examples=RULEBOOK_ERROR_EXAMPLES, backing="RulebookRepository.list_rulebooks and FastAPI route implemented.")
    add("GET", "/rulebooks/{rulebook_id}", "getRulebook", "Rulebooks", "Read a rulebook version", "Rulebook", examples["confirmed"],
        parameters=[parameter("version", "query", {"anyOf": [VERSION, {"type": "null"}], "default": None}, examples["confirmed"]["version"], description="Omit for latest; audits must reference a specific version.")],
        implementation="implemented", error_examples=RULEBOOK_ERROR_EXAMPLES, backing="RulebookRepository.get_rulebook and FastAPI route implemented.")
    add("GET", "/rulebooks/{rulebook_id}/versions", "listRulebookVersions", "Rulebooks", "Read rulebook version history", "RulebookPage", page([examples["draft"], examples["reviewed"], examples["confirmed"]]), parameters=pagination_parameters(),
        implementation="implemented", error_examples=RULEBOOK_ERROR_EXAMPLES, backing="RulebookRepository.list_rulebook_versions and FastAPI route implemented.",
        description="Snapshots are ordered by ascending version. Unknown rulebooks return 404; an offset past the last version returns an empty page.")
    add("GET", "/rulebooks/{rulebook_id}/reviews", "listRulebookReviews", "Rulebooks", "Read rulebook review attribution", "RulebookReviewPage",
        page([
            {"rulebook_id": "rulebook_demo", "version": 2, "reviewer_id": "reviewer_demo", "action": "edit", "created_at": TIMESTAMP},
            {"rulebook_id": "rulebook_demo", "version": 3, "reviewer_id": "reviewer_demo", "action": "confirm", "created_at": TIMESTAMP},
        ]),
        parameters=pagination_parameters(), implementation="implemented", error_examples=RULEBOOK_ERROR_EXAMPLES, backing="RulebookRepository.list_rulebook_reviews and FastAPI route implemented.",
        description="Attributed review actions ordered by ascending version. Each action and snapshot commit together. Manual creation, extraction and older low-level saves have no invented review records. reviewer_id is attribution, not authentication.")
    add("PUT", "/rulebooks/{rulebook_id}", "replaceDraftRulebook", "Rulebooks", "Save a reviewed draft", "Rulebook", examples["reviewed"],
        body_schema="RulebookReplace", body_example={"expected_version": 1, "reviewer_id": "reviewer_demo", "guideline_text": SOURCE, "rules": examples["confirmed"]["rules"]},
        implementation="implemented", error_examples=RULEBOOK_ERROR_EXAMPLES, conflict_examples={"VERSION_CONFLICT": "The rulebook is out of date. Refresh before resubmitting.", "INVALID_RULEBOOK_STATE": "This action is not allowed in the current rulebook state."},
        backing="save_rulebook_review service and transactional PostgreSQL review history and FastAPI route implemented.",
        description="Only latest drafts are editable. Submit the complete rule list and explicit rule decisions; omission removes a rule. Retained IDs preserve origin; added IDs must use reviewer origin. All citations must match the supplied text. Save increments version, attributes the reviewer and preserves history. The book stays draft; example save confirms individual rules only.")
    add("POST", "/rulebooks/{rulebook_id}/confirm", "confirmRulebook", "Rulebooks", "Confirm a reviewed rulebook", "Rulebook", examples["confirmed"],
        body_schema="RulebookAction", body_example={"expected_version": 2, "reviewer_id": "reviewer_demo"},
        implementation="implemented", error_examples=RULEBOOK_ERROR_EXAMPLES, conflict_examples={"VERSION_CONFLICT": "The rulebook is out of date. Refresh before resubmitting.", "INVALID_RULEBOOK_STATE": "This action is not allowed in the current rulebook state.", "RULEBOOK_NOT_READY": "Confirm every retained rule and remove rejected rules before confirming the book."},
        backing="confirm_rulebook service and transactional PostgreSQL review history and FastAPI route implemented.",
        description="Only a latest draft can be confirmed. Require a nonempty book with all included rules confirmed. Rejected rules must be removed; prior versions preserve them. Append expected_version + 1 as a frozen confirmed snapshot for audits. 409 RULEBOOK_NOT_READY when rules are unconfirmed or empty.")
    add("POST", "/rulebooks/{rulebook_id}/reject", "rejectRulebook", "Rulebooks", "Reject a draft rulebook", "Rulebook", {**examples["draft"], "version": 2, "confirmation_state": "rejected"},
        body_schema="RulebookAction", body_example={"expected_version": 1, "reviewer_id": "reviewer_demo"},
        implementation="implemented", error_examples=RULEBOOK_ERROR_EXAMPLES, conflict_examples={"VERSION_CONFLICT": "The rulebook is out of date. Refresh before resubmitting.", "INVALID_RULEBOOK_STATE": "This action is not allowed in the current rulebook state."},
        backing="reject_rulebook service and transactional PostgreSQL review history and FastAPI route implemented.",
        description="Reject the latest draft as a whole. Append a new version without changing individual rule decisions. Start a revision before further edits. This example is an alternative branch from the original version-1 draft, not a step after confirmation.")
    add("POST", "/rulebooks/{rulebook_id}/revisions", "reviseRulebook", "Rulebooks", "Start a new draft from a final version", "Rulebook", {**examples["draft"], "version": 4}, status=201,
        body_schema="RulebookAction", body_example={"expected_version": 3, "reviewer_id": "reviewer_demo"},
        implementation="implemented", error_examples=RULEBOOK_ERROR_EXAMPLES, conflict_examples={"VERSION_CONFLICT": "The rulebook is out of date. Refresh before resubmitting.", "INVALID_RULEBOOK_STATE": "This action is not allowed in the current rulebook state."},
        backing="revise_rulebook service and transactional PostgreSQL review history and FastAPI route implemented.",
        description="Require the latest confirmed or rejected book. Keep its snapshot immutable; append expected_version + 1 as a draft and reset every included rule to draft. Draft books are already editable. Invalid state transitions return 409 INVALID_RULEBOOK_STATE; stale expected_version returns 409 VERSION_CONFLICT.")
    dataset_errors = {
        404: {"NOT_FOUND": "The requested dataset, sample or revision does not exist."},
        503: {"DEPENDENCY_UNAVAILABLE": "Dataset storage or durable acceptance is unavailable."},
    }
    add("POST", "/datasets", "importDataset", "Datasets", "Queue a durable dataset ZIP import", "DatasetImportStarted",
        examples["dataset_import_scenarios"]["started"],
        status=202, body_schema="DatasetUpload", body_example={"archive": "<binary dataset ZIP>", "name": "Synthetic dirty-source dataset", "format": "penn_fudan"}, multipart=True,
        implementation="implemented", not_found=False,
        backing="Durable upload service and managed single-attempt import worker; worker is explicitly started.",
        error_examples={
            413: {"UPLOAD_TOO_LARGE": "The dataset upload exceeds the configured limit."},
            415: {"UNSUPPORTED_MEDIA_TYPE": "Upload a supported multipart dataset ZIP archive."},
            503: dataset_errors[503],
        },
        description="Send exactly archive, name and format=penn_fudan using multipart FormData, not JSON. Let the browser set its boundary. 202 acknowledges closed durable staging plus committed dataset/job/request, not import completion. Poll the shared job route. ZIP layout/CRC/identity failures after acceptance belong to dataset/job state, not an HTTP validation response. Unknown commit outcome is unresolved, never a fabricated 202 or an automatic replay.")
    add("GET", "/datasets", "listDatasets", "Datasets", "List persisted datasets", "DatasetPage", page([examples["dataset"]]),
        parameters=pagination_parameters(), implementation="implemented", not_found=False, error_examples=dataset_errors,
        backing="DatasetRepository metadata reads; no decoding, capture or workflow transitions.")
    add("GET", "/datasets/{dataset_id}", "getDataset", "Datasets", "Read persisted import status", "Dataset", examples["dataset"],
        implementation="implemented", error_examples=dataset_errors, backing="DatasetRepository metadata read.",
        description="Ready means coherent imported catalogue, not clean annotations or a review verdict. Invalid means unusable archive/layout/identity structure; failed means processing could not finish. Counts may be null. Existing published samples remain browsable after failure/cancellation without making the dataset ready.")
    add("GET", "/datasets/{dataset_id}/samples", "listDatasetSamples", "Datasets", "Browse published sample heads", "SamplePage", page([examples["sample"]]),
        parameters=pagination_parameters(), implementation="implemented", error_examples=dataset_errors,
        backing="DatasetRepository page-bounded snapshot reads, including damaged evidence.",
        description="A missing dataset is 404; an existing dataset with no published samples returns an empty page, in any dataset state. Stable ordering and total describe the same selection. No source files are decoded or recaptured.")
    add("GET", "/datasets/{dataset_id}/samples/{sample_id}", "getDatasetSample", "Datasets", "Read exact catalogue sample evidence", "Sample", examples["sample"],
        implementation="implemented", error_examples=dataset_errors, backing="Catalogue-owned current head or exact historical revision.",
        parameters=[parameter("revision", "query", VERSION, 1, description="Omit for the explicit catalogue current head, never MAX(manifest revision). Returned asset URLs always pin the selected revision.")],
        description="Missing dataset, sample or exact revision is 404. Missing/corrupt pinned manifest fails closed as a storage error; never recapture from mutable files or a deleted staging directory. Sample issues explain basic integrity, not rulebook or reviewer decisions. Source order, duplicate/negative IDs and invalid boxes remain source evidence.")
    asset = parameter("asset_kind", "path", choice("image", "mask", "mask_preview", "thumbnail"), "image",
                      description="One of image, mask, mask_preview or thumbnail.")
    add("GET", "/datasets/{dataset_id}/samples/{sample_id}/assets/{asset_kind}", "getSampleAsset", "Datasets", "Download a revision-pinned image or mask asset", "", None, binary=True,
        parameters=[asset, parameter("revision", "query", VERSION, 1, required=True,
                                     description="Required positive captured sample revision; the service never falls back to the latest revision.")],
        implementation="implemented",
        backing="SampleAssetService, SamplePreviewService, SampleAssetManifestRepository and FastAPI route implemented.",
        error_examples={
            404: {"NOT_FOUND": "The requested sample asset does not exist."},
            422: {
                "VALIDATION_ERROR": "One or more request fields are invalid.",
                "ASSET_NOT_RENDERABLE": "The requested sample asset cannot be rendered.",
                "ASSET_TOO_LARGE": "The requested sample asset exceeds the read limit.",
            },
            503: {"DEPENDENCY_UNAVAILABLE": "Audit storage is unavailable."},
            500: {"INTERNAL_ERROR": "The request could not be completed."},
        },
        description="Returns image/png for exactly the requested revision with X-Content-Type-Options: nosniff. image and mask return exact captured bytes after size and SHA-256 verification, including malformed source bytes retained for inspection; no raw mask decoding or conversion occurs. Stored content-type metadata cannot select a different response type. thumbnail and mask_preview are derived display PNGs; previews never replace raw mask bytes and may reject corrupt or unsupported sources with ASSET_NOT_RENDERABLE. All four kinds enforce a 64 MiB source-byte limit and return ASSET_TOO_LARGE when exceeded. Missing or unavailable assets return a JSON error, and integrity, storage-configuration and unexpected failures retain the documented JSON error envelope. No latest-revision lookup or private storage path is exposed.")
    asset_response = contract["paths"]["/datasets/{dataset_id}/samples/{sample_id}/assets/{asset_kind}"]["get"]["responses"]["200"]
    asset_response["content"]["image/png"] = asset_response["content"].pop("application/zip")
    operations[-1]["response"]["content_type"] = "image/png"
    add("POST", "/datasets/{dataset_id}/samples/{sample_id}/mask-uploads", "uploadCorrectionMask", "Corrections", "Stage a replacement instance-ID mask", "MaskUpload",
        {"mask_upload_id": "mask_upload_demo", "dataset_id": "dataset_demo", "sample_id": "sample_001", "base_revision": 1, "image_size": [4, 3], "mask_ids": [0, 1, 2], "expires_at": "2026-10-14T11:00:00Z"}, status=201,
        body_schema="MaskUploadRequest", body_example={"mask": "<binary 2D instance-ID PNG>", "expected_revision": 1}, multipart=True,
        conflict_examples={"STALE_SAMPLE_REVISION": "The sample changed. Refresh before staging the replacement mask."},
        error_examples={
            413: {"UPLOAD_TOO_LARGE": "The replacement mask exceeds the configured upload limit."},
            415: {"UNSUPPORTED_MEDIA_TYPE": "Upload an instance-ID PNG mask."},
            422: {"VALIDATION_ERROR": "The mask dimensions or instance-ID encoding are invalid."},
        },
        description="Stage the mask without changing annotations, bound to the dataset, sample and expected_revision; base_revision echoes that pin. Submit mask_upload_id with the correction to apply boxes, labels and mask atomically. PNG must preserve integer IDs; RGB visualization PNGs are rejected. Staged uploads expire and must be uploaded again after expiry. Numeric upload limits are deployment settings, not universal constants.")
    add("POST", "/datasets/{dataset_id}/samples/{sample_id}/revisions", "correctAnnotations", "Corrections", "Save corrected annotations and optional mask", "AnnotationRevision", examples["revision"], status=201,
        body_schema="CorrectionRequest", body_example={"expected_revision": 1, "reviewer_id": "reviewer_demo", "reason": examples["revision"]["reason"],
            "objects": examples["corrected"], "mask_upload_id": None, "source_audit_id": "audit_demo"},
        conflict_examples={
            "STALE_SAMPLE_REVISION": "The sample changed. Refresh before saving corrections.",
            "MASK_UPLOAD_UNAVAILABLE": "The staged mask is expired, consumed or does not belong to this sample revision.",
        },
        description="objects is the complete replacement list: add/remove entries to add/remove objects. Preserve originals and history. Validate boxes, labels and resulting ID correspondence before committing the revision. A staged mask must belong to this dataset, sample and expected_revision and may be consumed once. Earlier evidence and reviews remain valid for their original immutable pins; they do not approve the corrected revision. Request a recheck and fresh review for corrected data. Reject stale expected_revision with 409.")
    add("GET", "/datasets/{dataset_id}/samples/{sample_id}/revisions", "listAnnotationRevisions", "Corrections", "Read correction history", "RevisionPage", page([examples["revision"]]), parameters=pagination_parameters())
    audit_request = {"dataset_id": "dataset_demo", "rulebook_id": "rulebook_demo", "rulebook_version": examples["confirmed"]["version"], "sample_ids": None, "feedback_policy_id": None}
    add("POST", "/audits", "startAudit", "Audits", "Start an audit against confirmed rules", "AuditStarted",
        {"audit": {**examples["audit"], "status": "queued", "processed_count": 0}, "job": job("audit", "audit", "audit_demo"),
         "parent_audit_id": None, "feedback_policy_id": None}, status=202,
        body_schema="AuditRequest", body_example=audit_request, implementation="implemented",
        backing="AuditSubmissionService, catalogue-owned revision selection and BatchAuditService.start; an explicit audit worker processes the queue.",
        conflict_examples={
            "DATASET_NOT_READY": "Wait for a ready dataset before starting an audit.",
            "RULEBOOK_NOT_CONFIRMED": "Confirm the selected rulebook version and all its rules before starting an audit.",
        },
        error_examples={
            404: {"NOT_FOUND": "The requested dataset, sample or rulebook version does not exist."},
            422: {"VALIDATION_ERROR": "One or more request fields or selected rule parameters are invalid."},
        },
        description="Omit sample_ids or send null for every sample in catalogue order; a nonempty unique subset preserves request order. Require a ready imported dataset and a nonempty confirmed rulebook version containing only confirmed rules. Resolve all selected current heads in one database snapshot and durably pin those immutable manifests before returning 202. Dirty sample annotations remain auditable. Processing runs in the explicitly started audit worker, never in the HTTP request. Unsupported rules yield unavailable findings and all findings remain pending human review. feedback_policy_id optionally selects an active compatible frozen policy; null uses baseline context. No source paths, model execution settings or client-supplied sample revisions are accepted. Repeated submissions create separate audits; after a lost response inspect the audit list before resubmitting.")
    add("GET", "/audits", "listAudits", "Audits", "List audit runs", "AuditPage", page([examples["audit"]]),
        parameters=[pagination_parameters()[0], parameter("offset", "query", {
            "type": "integer", "minimum": 0, "maximum": 9223372036854775807, "default": 0,
        }, 0)], implementation="implemented", not_found=False,
        backing="AuditRepository.list_audits and FastAPI route implemented.",
        description="Read every persisted lifecycle state, newest created_at first and descending audit_id for ties. limit is 1–100 (default 20); offset is a nonnegative BIGINT (default 0). Count and page use one database snapshot. Empty and past-the-end pages return 200. Exposes only AuditRun summaries, never private paths, request documents, claims or asset keys. Listing does not execute or recover jobs. Offset pages may shift when new audits are added between requests.")
    add("GET", "/audits/{audit_id}", "getAudit", "Audits", "Read persisted audit progress", "AuditRun", examples["audit_run"],
        implementation="implemented",
        backing="AuditRepository.get_audit and FastAPI route implemented.",
        description="Read the persisted audit lifecycle summary without executing, claiming, recovering or modifying work, inspecting asset files or contacting Ollama. HTTP 200 is returned for every persisted lifecycle state. Processing completion does not imply reviewer acceptance.")
    add("GET", "/audits/{audit_id}/samples", "listAuditSamples", "Audits", "Read the complete review queue", "AuditSamplePage", page([examples["summary"]]),
        parameters=pagination_parameters() + [parameter("tool_status", "query", {"anyOf": [TOOL_STATUS, {"type": "null"}], "default": None}, "flagged"), parameter("review_status", "query", {"anyOf": [SAMPLE_REVIEW_STATUS, {"type": "null"}], "default": None}, "pending_review")],
        implementation="implemented", backing="AuditReadRepository.list_samples and FastAPI route implemented.",
        description="Omit filters to include every sample in original audit order. total and next_offset refer to the filtered queue. Processing state, incomplete flag, machine outcomes and human decisions are separate. Queued/running samples are pending; otherwise fail takes precedence over unsure, then unavailable. Failed/cancelled/incomplete or zero-findings samples cannot be no_issue. No automatic acceptance. Snapshot-backed samples with an available captured original image advertise a revision-pinned thumbnail_url; legacy samples without a manifest association keep it null. Missing audits return 404, empty matching pages return 200.")
    add("GET", "/audits/{audit_id}/samples/{sample_id}", "getAuditSample", "Audits", "Read sample findings, evidence and measurements", "SampleAudit", examples["detail"],
        implementation="implemented", backing="AuditReadRepository.get_sample and FastAPI route implemented.",
        description="One database snapshot includes the stored sample pin, exact captured manifest availability, original measurements, all findings with latest reviews, technical failures and current sample decision. Snapshot-backed samples advertise image/thumbnail links when original_image is available and mask/mask_preview links when raw_mask is available; recorded missing or unreadable roles and legacy samples without a manifest association remain null. Links advertise captured availability only: the asset route verifies exact bytes and derived previews may reject corrupt or unsupported sources. Measurements are null when not persisted and otherwise preserve the collector document (including its original pending_review field), except that loading-error details use safe messages even for legacy reports. Stored evidence is not rewritten. No current dataset files are read or latest revision inferred. Missing audit/sample returns 404.")
    sample_media = contract["paths"]["/audits/{audit_id}/samples/{sample_id}"]["get"]["responses"]["200"]["content"]["application/json"]
    sample_media.pop("example")
    sample_media["examples"] = {
        name: {"summary": f"Synthetic {name} review state", "value": value}
        for name, value in examples["review_scenarios"].items()
    }
    add("GET", "/audits/{audit_id}/findings", "listAuditFindings", "Audits", "List findings with rule sources and review state", "FindingPage", page([examples["finding"]]),
        parameters=[
            parameter("audit_id", "path", {"type": "string", "minLength": 1, "pattern": r"^[^\x00]*[^\s\x00][^\x00]*$"}, "audit_demo"),
            parameter("limit", "query", {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}, 20,
                      description="Page size, from 1 to 100."),
            parameter("offset", "query", {"type": "integer", "minimum": 0, "maximum": 9223372036854775807, "default": 0}, 0,
                      description="Zero-based position in the filtered list, up to PostgreSQL's largest integer."),
            parameter("sample_id", "query", {"anyOf": [{"type": "string", "minLength": 1, "pattern": r"^[^\x00]*[^\s\x00][^\x00]*$"}, {"type": "null"}], "default": None}, "sample_001",
                      description="Optional filter. A sample with no findings in this audit, including a sample that is not in it, returns an empty page rather than HTTP 404."),
            parameter("outcome", "query", {"anyOf": [ref("FindingOutcome"), {"type": "null"}], "default": None}, "fail",
                      description="Optional machine outcome filter. It combines with sample_id using AND and never filters by the reviewer's decision."),
        ],
        implementation="implemented", backing="AuditReadRepository.list_audit_findings and FastAPI route implemented.",
        description="List every machine finding of one audit across all of its samples, in ascending finding_id order (text order, not creation order). Each item is the stored finding, including its pinned sample_revision, rulebook version, rule parameters, origin, source sentence and evidence, with the latest human review in review; a finding nobody has reviewed shows resolution pending. Omit the filters to include all four outcomes (pass, fail, unsure, unavailable) and every review resolution. sample_id and outcome combine with AND. total counts all matching findings before paging, and next_offset is offset plus the number of items only when more matching findings remain, otherwise null (an empty page, an offset past the end and the final page all return null). An unknown audit returns HTTP 404; an existing audit with no matching findings returns HTTP 200 with an empty page. Audit existence, the total, the page and the latest reviews are read from one database snapshot. Reading never starts a worker, runs inference, reads asset files or changes a finding or its review. Findings are machine evidence: a reviewer's decision is separate, and an accepted or rejected finding does not decide the sample. Use getAuditSample for one sample's full detail.")
    findings_media = contract["paths"]["/audits/{audit_id}/findings"]["get"]["responses"]["200"]["content"]["application/json"]
    findings_media.pop("example")
    reviewed_finding = {**deepcopy(examples["passing"]), "review": {
        "revision": 1, "resolution": "accepted", "reviewer_id": "reviewer_demo",
        "comment": "Confirmed the label is allowed.", "updated_at": TIMESTAMP,
    }}
    findings_media["examples"] = {
        name: {"summary": summary, "value": FindingPage.model_validate_json(json.dumps(document)).model_dump(mode="json")}
        for name, summary, document in (
            ("first_page_with_more", "First page of five matching findings; follow next_offset",
             {"items": [examples["finding"], reviewed_finding], "limit": 2, "offset": 0, "total": 5, "next_offset": 2}),
            ("last_page", "Final page; next_offset is null",
             {"items": [examples["finding"]], "limit": 2, "offset": 4, "total": 5, "next_offset": None}),
            ("filtered_by_sample_and_outcome", "sample_id=sample_001&outcome=fail; total counts only matches",
             {"items": [examples["finding"]], "limit": 20, "offset": 0, "total": 1, "next_offset": None}),
            ("empty", "An existing audit, or a filter that matches nothing",
             {"items": [], "limit": 20, "offset": 0, "total": 0, "next_offset": None}),
        )
    }
    add("POST", "/audits/{audit_id}/rechecks", "recheckSamples", "Audits", "Audit corrected sample revisions again", "AuditStarted",
        {"audit": {**examples["audit"], "audit_id": "audit_recheck_demo", "status": "queued", "processed_count": 0, "job_id": "job_recheck_demo"},
         "job": {**job("audit", "audit", "audit_recheck_demo"), "job_id": "job_recheck_demo"}, "parent_audit_id": "audit_demo", "feedback_policy_id": None}, status=202,
        body_schema="RecheckRequest", body_example={"sample_ids": ["sample_001"], "sample_revisions": {"sample_001": 2}, "reviewer_id": "reviewer_demo"},
        conflict_examples={
            "STALE_SAMPLE_REVISION": "A selected sample changed. Refresh the revision selection before rechecking.",
            "AUDIT_NOT_READY": "The parent audit is not ready for rechecking.",
        },
        description="Create a new audit linked to the previous run. sample_revisions keys must exactly match sample_ids and identify their expected current revisions; reject stale pins rather than silently selecting newer data. Keep the original rulebook version and feedback policy. The nested audit uses the same AuditRun schema as getAudit; parent/policy context is in the start wrapper only. Do not overwrite old evidence or inherit final reviewer decisions.")
    add("GET", "/jobs/{job_id}", "getJob", "Jobs", "Poll persisted job progress and completion", "Job", job("audit", "audit", "audit_demo", completed=True),
        implementation="implemented",
        backing="JobRepository.get_job and FastAPI route implemented.",
        description="Read the persisted public Job resource without claiming, executing, recovering or modifying work. Return HTTP 200 for queued, running, cancelling, completed, failed and cancelled states. progress.total and progress.fraction may be null when work is indeterminate; result and error are nullable. Failed jobs remain HTTP 200 with their structured error in the response, not an HTTP failure. Preserve result.href exactly when present; the audit result destination /api/v1/audits/{audit_id} is callable. Missing jobs return 404; invalid identifiers return 422; storage and unexpected failures use the shared safe 503/500 envelopes. Worker identity, claim tokens, leases, attempts and resource columns are never public.")
    cancelled = job("audit", "audit", "audit_demo")
    cancelled["status"] = "cancelled"
    cancelled["progress"]["phase"] = "cancelled"
    cancelled["result"] = {
        "kind": "audit", "id": "audit_demo", "version": None,
        "href": "/api/v1/audits/audit_demo",
    }
    add("POST", "/jobs/{job_id}/cancel", "cancelJob", "Jobs", "Request audit-aware job cancellation", "Job", cancelled, status=202,
        implementation="implemented",
        conflict_examples={
            "JOB_ALREADY_FINISHED": "A terminal job cannot be cancelled.",
        },
        backing="AuditRepository.cancel_job dispatch boundary and FastAPI route implemented.",
        description="Accept no request body and return HTTP 202 with the public Job resource. For a queued audit, the existing transactional repository operation immediately marks the job cancelled, updates the audit and queued samples, and may preserve its stored result link. For a running audit, return cancelling while the worker reaches a safe boundary; repeated requests while cancelling preserve that state. Completed, failed and already-cancelled jobs return 409 JOB_ALREADY_FINISHED. Missing jobs return 404; invalid identifiers return 422; storage and unexpected failures use the shared safe 503/500 envelopes. Worker identity, claim tokens, leases, attempts and resource columns are never public. Cancellation does not rewrite completed measurements, findings or reviewer decisions.")
    finding_review = {"revision": 1, "resolution": "accepted", "reviewer_id": "reviewer_demo", "comment": "Confirmed the label mismatch.", "updated_at": TIMESTAMP}
    finding_review_record = {**finding_review, "finding_id": "finding_demo"}
    add("GET", "/findings/{finding_id}/reviews", "listFindingReviews", "Reviews", "Read finding decision history", "FindingReviewPage", page([finding_review_record]),
        parameters=pagination_parameters(), implementation="implemented",
        backing="FindingRepository.list_finding_reviews and FastAPI route implemented.",
        description="Attributed finding decisions in ascending review revision order, scoped to the requested finding. A missing finding returns 404; limit is 1–100 with offset starting at 0. History preserves every earlier decision and does not change machine evidence or sample acceptance.")
    add("PUT", "/findings/{finding_id}/review", "reviewFinding", "Reviews", "Accept or reject a tool finding", "FindingReview", finding_review,
        body_schema="FindingReviewRequest", body_example={"expected_review_version": 0, "resolution": "accepted", "reviewer_id": "reviewer_demo", "comment": finding_review["comment"]},
        implementation="implemented", conflict_examples={
            "VERSION_CONFLICT": "The finding review is out of date. Refresh before resubmitting.",
        },
        backing="review_finding service, PostgreSQL finding review history and FastAPI route implemented.",
        description="accepted confirms the tool's conclusion; rejected dismisses it; unsure records unresolved review. This does not decide sample acceptance or change machine evidence. Save at expected_review_version + 1 and retain every earlier decision. A stale expected_review_version returns 409 VERSION_CONFLICT; reload before resubmitting. Pending findings start at revision 0.")
    reviewed = {"audit_id": "audit_demo", "sample_id": "sample_001", "sample_revision": 1,
                "revision": 1, "decision": "needs_correction", "reviewer_id": "reviewer_demo", "comment": "Correct the second label before final review.", "updated_at": TIMESTAMP}
    add("PUT", "/audits/{audit_id}/samples/{sample_id}/review", "reviewSample", "Reviews", "Record the reviewer's data decision", "SampleReview", reviewed,
        body_schema="SampleReviewRequest", body_example={"expected_sample_revision": 1, "expected_review_version": 0, "decision": "needs_correction", "reviewer_id": "reviewer_demo", "comment": reviewed["comment"]},
        implementation="implemented", conflict_examples={
            "VERSION_CONFLICT": "The sample review is out of date. Refresh before resubmitting.",
            "STALE_SAMPLE_REVISION": "The requested sample revision is stale. Refresh before resubmitting.",
            "INVALID_SAMPLE_REVIEW_STATE": "The sample is not currently eligible for review.",
        },
        backing="review_sample service, PostgreSQL sample review history and FastAPI route implemented.",
        description="Separate from finding resolution. Reviewer controls accepted/rejected/needs_correction decisions, including no_issue or no-findings samples; pending_review explicitly reopens review with attribution. Completed samples (including incomplete measurements) and failed samples are eligible regardless of parent audit status; queued/running/cancelled samples return 409 INVALID_SAMPLE_REVIEW_STATE. expected_sample_revision must identify a stored pin for this audit/sample, otherwise 409 STALE_SAMPLE_REVISION (404 if the audit/sample is missing). It does not check current dataset files: snapshot-backed audits use immutable captured assets; legacy audits use live files. Application-owned immutable correction revisions and linked rechecks remain separate from review decisions. expected_review_version is the last-read review revision, starting at 0; each decision advances it by one. A stale review revision returns 409 VERSION_CONFLICT. Machine evidence, failures, progress and finding resolutions stay unchanged.")
    add("GET", "/audits/{audit_id}/samples/{sample_id}/reviews", "listSampleReviews", "Reviews", "Read sample decision history", "SampleReviewPage", page([reviewed]),
        parameters=pagination_parameters() + [parameter("sample_revision", "query", VERSION, 1, required=True,
            description="Exact stored audit sample revision; history never merges other pins or audits.")],
        implementation="implemented", conflict_examples={
            "STALE_SAMPLE_REVISION": "The requested sample revision is stale. Refresh before resubmitting.",
        },
        backing="SampleReviewRepository.list_sample_reviews and FastAPI route implemented.",
        description="Attributed decisions in ascending review revision order, scoped to the audit, sample and required sample_revision. A never-reviewed pin has an empty history and current pending_review revision 0. Explicit reopening is retained in history. An unknown audit/sample returns 404; an unmatched sample revision returns 409 STALE_SAMPLE_REVISION.")
    add("POST", "/exports", "startExport", "Exports", "Prepare reviewed output and manifest", "ExportStarted",
        {"export": {**examples["export"], "status": "queued", "included_count": None, "excluded_count": None, "download_url": None, "manifest_url": None}, "job": job("export", "export", "export_demo")},
        status=202, body_schema="ExportRequest", body_example={"audit_id": "audit_recheck_demo", "format": "penn_fudan", "selection": "accepted_only", "include_images": False},
        conflict_examples={"AUDIT_NOT_READY": "Wait until the selected audit can be exported."},
        description="accepted_only includes explicit accepted decisions. all_reviewed includes accepted and rejected with their decisions in the manifest. pending_review and needs_correction are excluded. Export exactly the immutable revisions reviewed in the selected audit; newer dataset heads do not invalidate those old-pin decisions or silently replace the export contents. Corrected data requires recheck and fresh review. Preserve source coordinates and original files.")
    add("GET", "/exports/{export_id}", "getExport", "Exports", "Read export status and download URLs", "Export", examples["export"])
    add("GET", "/exports/{export_id}/download", "downloadExport", "Exports", "Download reviewed dataset archive", "", None, binary=True,
        conflict_examples={"EXPORT_NOT_READY": "Wait for export preparation to complete before downloading."},
        description="ZIP bytes with annotations, masks and review manifest; images included only when requested. Return 409 EXPORT_NOT_READY until preparation completes.")
    add("GET", "/exports/{export_id}/manifest", "getExportManifest", "Exports", "Read exported and excluded sample decisions", "ExportManifest",
        {"export_id": "export_demo", "audit_id": "audit_recheck_demo", "rulebook_id": "rulebook_demo", "rulebook_version": examples["confirmed"]["version"],
         "entries": [{"sample_id": "sample_001", "sample_revision": 2, "included": True, "review_decision": "accepted", "reason": None}]},
        conflict_examples={"EXPORT_NOT_READY": "Wait for export preparation to complete before reading its manifest."})
    feedback=feedback_examples()
    for path,method,identifier,schema,request_schema,summary in (
        ('/feedback/examples','GET','listFeedbackExamples','FeedbackExamplePage',None,'List approved binary model examples'),
        ('/feedback/policies','POST','createFeedbackPolicy','FeedbackPolicy','FeedbackPolicyCreate','Freeze selected approved examples'),
        ('/feedback/policies','GET','listFeedbackPolicies','FeedbackPolicyPage',None,'List candidate policies'),
        ('/feedback/policies/{policy_id}','GET','getFeedbackPolicy','FeedbackPolicy',None,'Read a frozen policy'),
        ('/feedback/ground-truth','POST','createGroundTruth','GroundTruth','GroundTruthCreate','Freeze explicit rule-level adjudications'),
        ('/feedback/ground-truth/{ground_truth_id}','GET','getGroundTruth','GroundTruth',None,'Read immutable truth'),
        ('/feedback/evaluations','POST','evaluateFeedbackPolicy','FeedbackEvaluationStarted','FeedbackEvaluationCreate','Queue a held-out comparison'),
        ('/feedback/evaluations/{evaluation_id}','GET','getFeedbackEvaluation','FeedbackEvaluation',None,'Read comparison results'),
        ('/feedback/evaluations','GET','listFeedbackEvaluations','FeedbackEvaluationPage',None,'List comparisons'),
        ('/feedback/policies/{policy_id}/activate','POST','activateFeedbackPolicy','FeedbackPolicy','FeedbackPolicyActivate','Record explicit reviewer activation'),
    ):
        add(method,path,identifier,'Feedback',summary,schema,feedback['responses'][identifier],
            status=202 if identifier=='evaluateFeedbackPolicy' else 201 if identifier in {'createFeedbackPolicy','createGroundTruth'} else 200,
            body_schema=request_schema,body_example=feedback['requests'].get(identifier),
            parameters=pagination_parameters() if identifier.startswith('list') else None)

    align_dataset_contract(contract, create_app().openapi())
    align_export_contract(contract, create_app().openapi(), operations)
    align_correction_contract(contract, create_app().openapi(), operations)
    align_system_contract(contract, create_app().openapi(), operations)
    align_feedback_contract(contract, create_app().openapi(), operations)
    contract["x-feedback-job-scenarios"] = feedback_job_scenarios()
    contract["x-recheck-job-scenarios"] = recheck_job_scenarios()
    contract["x-template-operations"] = operations
    contract["x-review-scenarios"] = examples["review_scenarios"]
    contract["x-review-scenario-rulebooks"] = examples["review_scenario_rulebooks"]
    contract["x-job-scenarios"] = examples["job_scenarios"]
    contract["x-audit-scenarios"] = examples["audit_scenarios"]
    contract["x-extraction-job-scenarios"] = examples["extraction_job_scenarios"]
    contract["x-dataset-import-scenarios"] = examples["dataset_import_scenarios"]
    contract["x-future-job-scenarios"] = {kind: states for kind, states in examples["future_job_scenarios"].items()
                                            if kind not in {"export", "recheck", "feedback_evaluation"}}
    return contract

def build_implemented_contract(reference: JsonObject) -> JsonObject:
    """Use live FastAPI schemas for callable endpoints and retain frontend examples."""
    implemented = create_app().openapi()
    published = {
        (BASE_PATH + path, method): operation
        for path, methods in reference["paths"].items()
        for method, operation in methods.items()
        if operation["x-implementation-status"] == "implemented"
    }
    registered = {
        (path, method) for path, methods in implemented["paths"].items() for method in methods
    }
    if set(published) != registered:
        raise ValueError("Implemented contract operations must match the registered HTTP routes.")
    for (path, method), documented in published.items():
        operation = implemented["paths"][path][method]
        if operation["operationId"] != documented["operationId"]:
            raise ValueError(f"Operation ID disagrees with the registered route: {method} {path}")
        if set(operation["responses"]) != set(documented["responses"]):
            raise ValueError(f"Response statuses disagree with the registered route: {method} {path}")
        for key in ("summary", "description", "x-implementation-status", "x-backing-service-status"):
            operation[key] = deepcopy(documented[key])
        parameter_docs = {(item["in"], item["name"]): item for item in documented.get("parameters", [])}
        for parameter in operation.get("parameters", []):
            matching = parameter_docs.get((parameter["in"], parameter["name"]), {})
            for key in ("example", "description"):
                if key in matching:
                    parameter[key] = deepcopy(matching[key])
        for status, response in operation["responses"].items():
            for media_type, media in response.get("content", {}).items():
                matching = documented["responses"][status].get("content", {}).get(media_type, {})
                for key in ("example", "examples"):
                    media.pop(key, None)
                    if key in matching:
                        media[key] = deepcopy(matching[key])
        for media_type, media in operation.get("requestBody", {}).get("content", {}).items():
            matching = documented.get("requestBody", {}).get("content", {}).get(media_type, {})
            if "example" in matching:
                media["example"] = deepcopy(matching["example"])
    implemented["servers"] = [{"url": "http://127.0.0.1:8000", "description": "Paths already include /api/v1."}]
    implemented["info"]["description"] = (
        "Callable backend endpoints only, with schemas from the FastAPI application. "
        "Use this document for frontend client generation. Examples are independent synthetic snapshots."
    )
    return implemented

def render_artifacts() -> dict[str, str]:
    """Render all generated files without writing them, for reproducible updates."""
    contract = build_contract()
    fixtures = contract.pop("x-template-operations")
    scenarios = contract.pop("x-review-scenarios")
    scenario_rulebooks = contract.pop("x-review-scenario-rulebooks")
    job_scenarios = contract.pop("x-job-scenarios")
    audit_scenarios = contract.pop("x-audit-scenarios")
    future_job_scenarios = contract.pop("x-future-job-scenarios")
    extraction_job_scenarios = contract.pop("x-extraction-job-scenarios")
    dataset_import_scenarios = contract.pop("x-dataset-import-scenarios")
    export_scenarios = contract.pop("x-export-scenarios")
    recheck_job_scenarios = contract.pop("x-recheck-job-scenarios")
    feedback_job_states = contract.pop("x-feedback-job-scenarios")
    documents = {"openapi.json": contract, "implemented-openapi.json": build_implemented_contract(contract), "examples.json": {
        "notice": "Synthetic frontend fixtures, not stored resources, measured model accuracy or a stateful server. Feedback policies, adjudicated truth, comparisons and explicit activation are implemented in source. Only operations marked implemented are callable. Annotation/mask corrections, history and linked audit-kind rechecks, dataset import/catalogue, reviewed exports, audit creation/listing/metadata, queue/detail, the audit-wide finding list, assets, jobs, finding/sample reviews and extraction/manual rulebook creation/read/review are implemented in the applied checkpoint source. Use implemented-openapi.json for live client generation. Lifecycle and review scenarios are independent snapshots; binary assets are not embedded.",
        "base_url": "http://127.0.0.1:8000", "operations": fixtures,
        "review_scenarios": scenarios, "review_scenario_rulebooks": scenario_rulebooks,
        "job_scenarios": job_scenarios, "audit_scenarios": audit_scenarios,
        "dataset_import_scenarios": dataset_import_scenarios,
        "export_scenarios": export_scenarios,
        "recheck_job_scenarios": recheck_job_scenarios,
        "feedback_job_scenarios": feedback_job_states,
        "future_job_scenarios": future_job_scenarios,
        "extraction_job_scenarios": extraction_job_scenarios,
        "check_catalog": extraction_check_catalog(),
    }}
    artifacts = {
        filename: json.dumps(content, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        for filename, content in documents.items()
    }
    lines = [
        "# Endpoint index", "", "Generated from `build_contract.py`. Paths include `/api/v1`.", "",
        "Use implemented-openapi.json for callable endpoint schemas and client generation. "
        "openapi.json is the combined reference and marks each operation with x-implementation-status. "
        "examples.json contains synthetic fixtures.", "",
    ]
    for status, heading in (("implemented", "Implemented"), ("planned", "Planned — unavailable on the running server")):
        matching = [operation for operation in fixtures if operation["implementation"] == status]
        lines.extend([f"## {heading} ({len(matching)} operations)", ""])
        if not matching:
            lines.extend(["None.", ""])
        for tag in contract["tags"]:
            grouped = [operation for operation in matching if operation["tag"] == tag["name"]]
            if not grouped:
                continue
            lines.extend([f"### {tag['name']}", ""])
            for operation in grouped:
                lines.append(f"- `{operation['method']} {operation['path']}` — {operation['summary']} (`{operation['operation_id']}`; {status}).")
            lines.append("")
    artifacts["ENDPOINTS.md"] = "\n".join(lines)
    artifacts["PLANNED_ENDPOINTS.md"] = render_planned_endpoints(contract)
    return artifacts

def main() -> None:
    for filename, content in render_artifacts().items():
        (API_DIRECTORY / filename).write_text(content, encoding="utf-8")
    print("Generated combined and implemented-only OpenAPI, frontend fixtures and live/planned endpoint references.")

if __name__ == "__main__":
    main()
