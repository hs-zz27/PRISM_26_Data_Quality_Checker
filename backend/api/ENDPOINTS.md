# Endpoint index

Generated from `build_contract.py`. Paths include `/api/v1`.

Use [implemented-openapi.json for callable endpoint schemas and client generation. openapi.json also includes planned designs. examples.json contains synthetic fixtures.

## Implemented (36 operations)

### Rulebooks

- `POST /api/v1/rulebooks` — Create a manually assembled draft (`createRulebook`; implemented).
- `POST /api/v1/rulebooks/extractions` — Propose draft rules from plain text (`extractRulebook`; implemented).
- `GET /api/v1/rulebooks` — List latest rulebook versions (`listRulebooks`; implemented).
- `GET /api/v1/rulebooks/{rulebook_id}` — Read a rulebook version (`getRulebook`; implemented).
- `GET /api/v1/rulebooks/{rulebook_id}/versions` — Read rulebook version history (`listRulebookVersions`; implemented).
- `GET /api/v1/rulebooks/{rulebook_id}/reviews` — Read rulebook review attribution (`listRulebookReviews`; implemented).
- `PUT /api/v1/rulebooks/{rulebook_id}` — Save a reviewed draft (`replaceDraftRulebook`; implemented).
- `POST /api/v1/rulebooks/{rulebook_id}/confirm` — Confirm a reviewed rulebook (`confirmRulebook`; implemented).
- `POST /api/v1/rulebooks/{rulebook_id}/reject` — Reject a draft rulebook (`rejectRulebook`; implemented).
- `POST /api/v1/rulebooks/{rulebook_id}/revisions` — Start a new draft from a final version (`reviseRulebook`; implemented).

### Datasets

- `POST /api/v1/datasets` — Queue a durable dataset ZIP import (`importDataset`; implemented).
- `GET /api/v1/datasets` — List persisted datasets (`listDatasets`; implemented).
- `GET /api/v1/datasets/{dataset_id}` — Read persisted import status (`getDataset`; implemented).
- `GET /api/v1/datasets/{dataset_id}/samples` — Browse published sample heads (`listDatasetSamples`; implemented).
- `GET /api/v1/datasets/{dataset_id}/samples/{sample_id}` — Read exact catalogue sample evidence (`getDatasetSample`; implemented).
- `GET /api/v1/datasets/{dataset_id}/samples/{sample_id}/assets/{asset_kind}` — Download a revision-pinned image or mask asset (`getSampleAsset`; implemented).

### Jobs

- `GET /api/v1/jobs/{job_id}` — Poll persisted job progress and completion (`getJob`; implemented).
- `POST /api/v1/jobs/{job_id}/cancel` — Request audit-aware job cancellation (`cancelJob`; implemented).

### Audits

- `POST /api/v1/audits` — Start an audit against confirmed rules (`startAudit`; implemented).
- `GET /api/v1/audits` — List audit runs (`listAudits`; implemented).
- `GET /api/v1/audits/{audit_id}` — Read persisted audit progress (`getAudit`; implemented).
- `GET /api/v1/audits/{audit_id}/samples` — Read the complete review queue (`listAuditSamples`; implemented).
- `GET /api/v1/audits/{audit_id}/samples/{sample_id}` — Read sample findings, evidence and measurements (`getAuditSample`; implemented).
- `POST /api/v1/audits/{audit_id}/rechecks` — Audit corrected sample revisions again (`recheckSamples`; implemented).

### Reviews

- `GET /api/v1/findings/{finding_id}/reviews` — Read finding decision history (`listFindingReviews`; implemented).
- `PUT /api/v1/findings/{finding_id}/review` — Accept or reject a tool finding (`reviewFinding`; implemented).
- `PUT /api/v1/audits/{audit_id}/samples/{sample_id}/review` — Record the reviewer's data decision (`reviewSample`; implemented).
- `GET /api/v1/audits/{audit_id}/samples/{sample_id}/reviews` — Read sample decision history (`listSampleReviews`; implemented).

### Corrections

- `POST /api/v1/datasets/{dataset_id}/samples/{sample_id}/mask-uploads` — Stage a replacement instance-ID mask (`uploadCorrectionMask`; implemented).
- `POST /api/v1/datasets/{dataset_id}/samples/{sample_id}/revisions` — Save corrected annotations and optional mask (`correctAnnotations`; implemented).
- `GET /api/v1/datasets/{dataset_id}/samples/{sample_id}/revisions` — Read correction history (`listAnnotationRevisions`; implemented).

### Exports

- `POST /api/v1/exports` — Prepare reviewed output and manifest (`startExport`; implemented).
- `GET /api/v1/exports/{export_id}` — Read export status and download URLs (`getExport`; implemented).
- `GET /api/v1/exports/{export_id}/download` — Download reviewed dataset archive (`downloadExport`; implemented).
- `GET /api/v1/exports/{export_id}/manifest` — Read exported and excluded sample decisions (`getExportManifest`; implemented).
- `GET /api/v1/exports` — List reviewed exports (`listExports`; implemented).

## Planned — unavailable on the running server (14 operations)

### System

- `GET /api/v1/health` — Check HTTP server liveness (`getHealth`; planned).
- `GET /api/v1/capabilities` — List supported and planned checks (`getCapabilities`; planned).
- `GET /api/v1/system/status` — Inspect local model readiness (`getSystemStatus`; planned).

### Audits

- `GET /api/v1/audits/{audit_id}/findings` — List findings with rule sources and review state (`listAuditFindings`; planned).

### Feedback

- `GET /api/v1/feedback/examples` — List approved reviewer correction examples (`listFeedbackExamples`; planned).
- `POST /api/v1/feedback/policies` — Define a candidate reviewer-example policy (`createFeedbackPolicy`; planned).
- `GET /api/v1/feedback/policies` — List available feedback policies (`listFeedbackPolicies`; planned).
- `GET /api/v1/feedback/policies/{policy_id}` — Read a feedback policy (`getFeedbackPolicy`; planned).
- `POST /api/v1/feedback/ground-truth` — Freeze reviewer-verified evaluation evidence (`createGroundTruth`; planned).
- `GET /api/v1/feedback/ground-truth/{ground_truth_id}` — Read a frozen evaluation snapshot (`getGroundTruth`; planned).
- `POST /api/v1/feedback/evaluations` — Compare candidate feedback with a baseline (`evaluateFeedbackPolicy`; planned).
- `GET /api/v1/feedback/evaluations/{evaluation_id}` — Read held-out feedback evaluation results (`getFeedbackEvaluation`; planned).
- `GET /api/v1/feedback/evaluations` — List feedback evaluation jobs (`listFeedbackEvaluations`; planned).
- `POST /api/v1/feedback/policies/{policy_id}/activate` — Approve an evaluated policy for future audits (`activateFeedbackPolicy`; planned).
