# Proposed endpoint index

Generated from `build_contract.py`. All HTTP routes are planned; none are live yet.

Paths include the `/api/v1` prefix. See `openapi.json` for schemas and `examples.json` for frontend fixtures.

## System

- `GET /api/v1/health` — Check HTTP server liveness (`getHealth`).
- `GET /api/v1/capabilities` — List supported and planned checks (`getCapabilities`).
- `GET /api/v1/system/status` — Inspect local model readiness (`getSystemStatus`).

## Rulebooks

- `POST /api/v1/rulebooks` — Create a manually assembled draft (`createRulebook`).
- `POST /api/v1/rulebooks/extractions` — Propose draft rules from plain text (`extractRulebook`).
- `GET /api/v1/rulebooks` — List latest rulebook versions (`listRulebooks`).
- `GET /api/v1/rulebooks/{rulebook_id}` — Read a rulebook version (`getRulebook`).
- `GET /api/v1/rulebooks/{rulebook_id}/versions` — Read rulebook version history (`listRulebookVersions`).
- `PUT /api/v1/rulebooks/{rulebook_id}` — Save a reviewed draft (`replaceDraftRulebook`).
- `POST /api/v1/rulebooks/{rulebook_id}/confirm` — Confirm a reviewed rulebook (`confirmRulebook`).
- `POST /api/v1/rulebooks/{rulebook_id}/revisions` — Start a new draft from a confirmed version (`reviseRulebook`).

## Datasets

- `POST /api/v1/datasets` — Upload a dataset archive for local import (`importDataset`).
- `GET /api/v1/datasets` — List imported datasets (`listDatasets`).
- `GET /api/v1/datasets/{dataset_id}` — Read dataset import and integrity summary (`getDataset`).
- `GET /api/v1/datasets/{dataset_id}/samples` — List every dataset sample (`listDatasetSamples`).
- `GET /api/v1/datasets/{dataset_id}/samples/{sample_id}` — Read annotations and pinned asset URLs (`getDatasetSample`).
- `GET /api/v1/datasets/{dataset_id}/samples/{sample_id}/assets/{asset_kind}` — Download an image or mask asset (`getSampleAsset`).

## Jobs

- `GET /api/v1/jobs/{job_id}` — Poll progress and completion (`getJob`).
- `POST /api/v1/jobs/{job_id}/cancel` — Request cancellation of pending work (`cancelJob`).

## Audits

- `POST /api/v1/audits` — Start an audit against confirmed rules (`startAudit`).
- `GET /api/v1/audits` — List audit runs (`listAudits`).
- `GET /api/v1/audits/{audit_id}` — Read audit processing and review counts (`getAudit`).
- `GET /api/v1/audits/{audit_id}/samples` — Read the complete review queue (`listAuditSamples`).
- `GET /api/v1/audits/{audit_id}/samples/{sample_id}` — Read sample findings, evidence and measurements (`getAuditSample`).
- `GET /api/v1/audits/{audit_id}/findings` — List findings with rule sources and review state (`listAuditFindings`).
- `POST /api/v1/audits/{audit_id}/rechecks` — Audit corrected sample revisions again (`recheckSamples`).

## Reviews

- `PUT /api/v1/findings/{finding_id}/review` — Accept or reject a tool finding (`reviewFinding`).
- `PUT /api/v1/audits/{audit_id}/samples/{sample_id}/review` — Record the reviewer's data decision (`reviewSample`).
- `GET /api/v1/audits/{audit_id}/samples/{sample_id}/reviews` — Read sample decision history (`listSampleReviews`).

## Corrections

- `POST /api/v1/datasets/{dataset_id}/samples/{sample_id}/mask-uploads` — Stage a replacement instance-ID mask (`uploadCorrectionMask`).
- `POST /api/v1/datasets/{dataset_id}/samples/{sample_id}/revisions` — Save corrected annotations and optional mask (`correctAnnotations`).
- `GET /api/v1/datasets/{dataset_id}/samples/{sample_id}/revisions` — Read correction history (`listAnnotationRevisions`).

## Exports

- `POST /api/v1/exports` — Prepare reviewed output and manifest (`startExport`).
- `GET /api/v1/exports/{export_id}` — Read export status and download URLs (`getExport`).
- `GET /api/v1/exports/{export_id}/download` — Download corrected dataset archive (`downloadExport`).
- `GET /api/v1/exports/{export_id}/manifest` — Read exported and excluded sample decisions (`getExportManifest`).

## Feedback

- `GET /api/v1/feedback/examples` — List approved reviewer correction examples (`listFeedbackExamples`).
- `POST /api/v1/feedback/policies` — Define a candidate reviewer-example policy (`createFeedbackPolicy`).
- `GET /api/v1/feedback/policies` — List available feedback policies (`listFeedbackPolicies`).
- `GET /api/v1/feedback/policies/{policy_id}` — Read a feedback policy (`getFeedbackPolicy`).
- `POST /api/v1/feedback/ground-truth` — Freeze reviewer-verified evaluation evidence (`createGroundTruth`).
- `GET /api/v1/feedback/ground-truth/{ground_truth_id}` — Read a frozen evaluation snapshot (`getGroundTruth`).
- `POST /api/v1/feedback/evaluations` — Compare candidate feedback with a baseline (`evaluateFeedbackPolicy`).
- `GET /api/v1/feedback/evaluations/{evaluation_id}` — Read held-out feedback evaluation results (`getFeedbackEvaluation`).
- `GET /api/v1/feedback/evaluations` — List feedback evaluation jobs (`listFeedbackEvaluations`).
- `POST /api/v1/feedback/policies/{policy_id}/activate` — Approve an evaluated policy for future audits (`activateFeedbackPolicy`).
