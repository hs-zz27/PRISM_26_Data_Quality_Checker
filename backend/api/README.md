# Frontend API guide

> Current feedback source proposal: ten feedback operations plus real bounded
> `annotation_guideline` inference are supplied. Proposed inventory: 46 implemented,
> 4 planned, 50 total. This handoff did not run tests, migrations, workers, inference
> or API contract generation. Regenerate all five artifacts together before using
> them for this feature; earlier generation/test statements below refer to the
> captured pre-feedback work. See [FEEDBACK.md](FEEDBACK.md).

This folder gives the frontend developer endpoint templates, request bodies,
response schemas and synthetic examples for the implemented and planned workflow.
**46 HTTP operations are implemented in source after this handoff is applied:** dataset import and four catalogue reads, extraction submission, manual rulebook creation/read/review, audit creation/listing/metadata
and sample reads, revision-pinned assets, job polling/cancellation, and
finding/sample decisions with history, five reviewed-export operations and four correction/recheck operations.
All five API artifacts now include these four operations.
All other operations remain planned. Check `x-implementation-status` in OpenAPI
and `implementation` in each fixture. Server setup
includes the launch command. The running server's `/docs` and `/openapi.json`
include only real routes; this folder also documents the remaining planned workflow.

The development base address is `http://127.0.0.1:8000/api/v1`.
Keep the base URL configurable in the frontend.
For a frontend served on another port, use its development server's proxy for
`/api/v1`; cross-origin access is not enabled by default.

Use implemented-openapi.json to generate a client
for the running backend. Its server URL is `http://127.0.0.1:8000` and paths
already include `/api/v1`. The combined `openapi.json` uses a server URL ending
in `/api/v1` and paths without that prefix. Apply the prefix exactly once.
`examples.json` uses the origin in `base_url` and includes the prefix in paths.
Resolve asset URLs and `result.href` against the backend origin or its proxy,
not an SPA route.

## Dataset import verification status

Phases 1–4 are applied, including the corrected Phase 2 producer cleanup. All
five API artifacts are regenerated, including the export integration. The developer
reports the latest pre-feature suite passed (count and independent evidence not supplied).
The correction/recheck changes remain unexecuted. Migration application and real import/export
execution remain separate verification gates. See DATASETS.md for setup, worker
commands and the remaining verification steps. Regenerate the artifacts together
when API models or routes change.

## Files

- ENDPOINTS.md: every method, path and operation ID, grouped by
  implemented versus planned.
- RULEBOOKS.md: manual creation, editing, confirmation and history.
- DATASETS.md: durable ZIP acceptance, explicit worker, dirty-source browsing and generation prerequisites.
- AUDITS.md: catalogue-backed audit creation, listing and explicit worker commands.
- EXPORTS.md: frozen reviewed selections, managed export jobs, manifests and downloads.
- dataset_contract.py / dataset_examples.py: live-aligned dataset shapes and model-validated lifecycle snapshots.
- PLANNED_ENDPOINTS.md: all 4 remaining future operations, with
  parameters, request/response examples, proposed errors and referenced schemas.
- FRONTEND_WORKFLOW.md: screen flows, mock setup, jobs,
  correction/recheck pins and export/feedback interactions.
- implemented-openapi.json: callable endpoints only,
  with actual FastAPI transport schemas and frontend examples; use for client generation.
- openapi.json: combined implementation/planning reference; the
  4 remaining planned operations are not available on the running server.
- examples.json: frontend fixtures with path parameters, query
  values, request body, response status and response body. Also includes flagged,
  no-issue, uncertain and unavailable review scenarios, plus a legacy audit with
  null asset links.
- build_contract.py: endpoint definitions and artifact generation.
- schemas.py: transport schemas, reusing implemented dataset/sample, rulebook,
  finding and review models; correction-only constraints stay separate.
- fixtures.py: synthetic workflows and measurement examples from
  `audit_sample()` on a tiny dataset. System status uses a fixed example model name
  so generation does not depend on local Ollama settings.
- common.py: shared schema vocabulary and small constructors.

Run from the project root:

```bash
.venv/bin/python -B -m backend.api.build_contract
```

Before committing, run the contract tests, including generated-file freshness:

```bash
.venv/bin/python -m pytest backend/tests/test_api_contract.py backend/tests/test_rulebook_contract.py -q
```

Edit generator inputs rather than generated artifacts. The implemented-only
spec imports the app without serving requests, connecting to PostgreSQL,
applying migrations, or running inference/workers. This is contract version
`0.1.0`; coordinate field changes with the frontend.
The HTTP adapter uses FastAPI. Format reference:
https://spec.openapis.org/oas/v3.1.0.html.

## Available operations

Paths below are relative to `/api/v1`. GET requests have no body.

Saved rulebooks (see the detailed guide):

- `GET /rulebooks` → 200 `RulebookPage`.
- `GET /rulebooks/{rulebook_id}?version=N` → 200 `Rulebook`; omit version for latest.
- `GET /rulebooks/{rulebook_id}/versions` → 200 `RulebookPage`.
- `GET /rulebooks/{rulebook_id}/reviews` → 200 `RulebookReviewPage`.
- `PUT /rulebooks/{rulebook_id}` → 200 `Rulebook`; `RulebookReplace` body.
- `POST /rulebooks/{rulebook_id}/confirm` → 200 `Rulebook`; `RulebookAction` body.
- `POST /rulebooks/{rulebook_id}/reject` → 200 `Rulebook`; `RulebookAction` body.
- `POST /rulebooks/{rulebook_id}/revisions` → 201 `Rulebook`; `RulebookAction` body.

Datasets (see DATASETS.md):

- `POST /datasets` → 202 `DatasetImportStarted`; multipart archive/name/format.
- `GET /datasets` → 200 `DatasetPage`.
- `GET /datasets/{dataset_id}` → 200 `Dataset`.
- `GET /datasets/{dataset_id}/samples` → 200 `SamplePage`.
- `GET /datasets/{dataset_id}/samples/{sample_id}?revision=N` → 200 `Sample`; omit revision for catalogue current head.

Audits, assets and jobs:

- `POST /audits` → 202 `AuditStarted`; strict `AuditRequest` body.
- `GET /audits` → 200 `AuditPage`; newest audits first, with limit/offset pagination.
- `GET /audits/{audit_id}` → 200 `AuditRun`.
- `GET /audits/{audit_id}/samples` → 200 `AuditSamplePage`.
- `GET /audits/{audit_id}/samples/{sample_id}` → 200 `SampleAudit`.
- `GET /datasets/{dataset_id}/samples/{sample_id}/assets/{asset_kind}?revision=N`
  → 200 PNG bytes; the positive revision is required.
- `GET /jobs/{job_id}` → 200 `Job`, including failed jobs.
- `POST /jobs/{job_id}/cancel` → 202 `Job`; no body.

Finding and sample decisions:

- `PUT /findings/{finding_id}/review` → 200 `FindingReview`;
  `FindingReviewRequest` body.
- `GET /findings/{finding_id}/reviews` → 200 `FindingReviewPage`.
- `PUT /audits/{audit_id}/samples/{sample_id}/review` → 200 `SampleReview`;
  `SampleReviewRequest` body.
- `GET /audits/{audit_id}/samples/{sample_id}/reviews?sample_revision=N`
  → 200 `SampleReviewPage`; the positive audited pin is required.

Reviewed exports (see EXPORTS.md):

- `POST /exports` → 202 `ExportStarted`; strict `ExportRequest` body.
- `GET /exports` → 200 `ExportPage`.
- `GET /exports/{export_id}` → 200 `Export`.
- `GET /exports/{export_id}/manifest` → 200 `ExportManifest` after completion.
- `GET /exports/{export_id}/download` → 200 ZIP bytes after completion.

Lists support `limit` (1–100, default 20) and nonnegative `offset` (default 0).
Audit sample lists also accept `tool_status` and `review_status`; omit a filter
to disable it rather than sending the string `null`.

Manual rulebook creation, dataset import/catalogue browsing and audit creation/listing
are implemented. Submit a ready dataset and confirmed rulebook version through
`POST /audits`, then run the explicit audit worker. See AUDITS.md. App startup does not create demo resources;
fixture IDs do not imply that matching PostgreSQL records exist.

`GET /audits/{audit_id}` uses `AuditRun`: IDs, pinned rulebook version,
`source_mode`, lifecycle status, sample/processed/failed/incomplete counts and
timestamps. It does not return `reviewed_count`, `parent_audit_id` or
`feedback_policy_id`. Start/list and linked recheck contracts reuse this same
`AuditRun` shape; optional parent/policy context is on the `AuditStarted` wrapper.
The planning name `Audit` aliases `AuditRun`. Processing completion does not mean
human acceptance.

## Planned end-to-end workflow

This is the target workflow. Planned steps are mock/UI design references, not
actions the running backend currently supports.

1. **Planned:** inspect health, capabilities and system status. Liveness is separate from
   model readiness; these endpoints do not start services or download models.
2. **Implemented:** submit plain text through `POST /rulebooks/extractions`, poll the job, then
   follow its version-pinned `result.href` to the saved draft. **Implemented:**
   manual rulebook creation through `POST /rulebooks`. Other guideline input
   formats are deferred.
3. **Implemented for saved rulebooks:** show source sentences, descriptions and parameters. Save reviewed rules with
   `PUT /rulebooks/{rulebook_id}`. The example saves version 2 with confirmed
   individual rules while the book stays draft. Confirm the book separately,
   creating version 3. Confirmed and rejected versions are frozen; later edits
   start a new draft version with every rule reset to draft.
   The main audit, finding and export fixtures all reference confirmed version 3.
   Rejection is an alternative branch from the original draft; the review-state
   scenarios are independent examples rather than successive edits to one sample.
4. **Implemented in the dataset checkpoints:** upload a dataset ZIP through `POST /datasets`, poll import, then inspect sample
   integrity. The first adapter is `penn_fudan`, not a universal specification.
5. **Implemented:** `POST /audits` starts an audit using a ready dataset and exact
   confirmed rulebook version. Omitted/null `sample_ids` selects all catalogue
   samples; a nonempty unique list selects a subset in request order. Heads are
   resolved in one snapshot and exact manifests are durably pinned. The explicit
   audit worker processes the queue sequentially using the existing supervisor.
   Send no private paths or execution settings. Explicit active compatible `feedback_policy_id` is supported in source;
   omission/null means baseline and execution settings are durably pinned. `GET /audits` lists all audit states.
6. **Implemented for existing audits/jobs:** poll the job, list the complete review queue and load sample detail. Omit queue
   filters to include apparently correct cases. Findings include pass outcomes.
   Snapshot-backed samples advertise captured asset links when their manifest
   roles are available; legacy samples without manifest associations retain null
   links. Follow an asset link only with its explicit revision query parameter.
7. **Implemented:** resolve findings and record sample decisions separately.
   **Implemented in source:** correct boxes, labels and masks through a new immutable revision; see CORRECTIONS.md.
8. **Implemented in source:** recheck corrected revisions through an audit-kind job. This creates a child audit, retaining old evidence
   and requiring a new reviewer decision. Export the reviewed child audit.
9. **Implemented:** prepare an export from a terminal snapshot-backed audit, poll
   its job, then download the ZIP and JSON manifest. Corrections/rechecks are not prerequisites.
10. **Planned:** inspect approved correction examples, create an example-retrieval policy,
    freeze reviewer-verified held-out ground truth, evaluate baseline/candidate,
    then explicitly activate the evaluated policy for later audits.

Every operation has `x-implementation-status` and a backing-service note. The
audit queue/detail reads, revision-pinned asset delivery, job status/cancellation
and finding/sample review routes plus rulebook reads/reviewer actions are implemented. Python
measurements, rule records, Ollama integration, rulebook, finding and sample review
services, confirmed-rule evaluation, durable PostgreSQL jobs/audits/findings and
PostgreSQL version/review storage also exist. Dataset import/catalogue checkpoint
source is described in DATASETS.md. Correction/recheck services are implemented in source after this handoff is applied.
Other still-planned HTTP routes remain unavailable. Reviewed dataset export routes are implemented in source;
they preserve immutable
audited revisions and frozen human sample decisions without requiring corrections
or rechecks. The five contract artifacts are regenerated; tests have not been rerun
after integration corrections. Migration application and real worker/database/download
verification remain pending. Evaluated feedback services are supplied in source; detector status follows the
merged source rather than older placeholder notes.

## Requests, responses and jobs

Single-resource endpoints return the resource directly. Paginated lists return:

```json
{"items": [], "limit": 20, "offset": 0, "total": 0, "next_offset": null}
```

Limit is 1–100, default 20. Offset starts at 0. Null next offset means no more
pages. During processing, counts refer to currently available records and can
change. Query parameters and example filtered results agree.

Long-running operations return 202 and a job, sometimes alongside the new
resource. The implemented `GET /jobs/{job_id}` returns the public `Job` model
with HTTP 200 for `queued`, `running`, `cancelling`, `completed`, `failed` and
`cancelled`. Stop polling at the final three states. Unknown progress has null
`total`/`fraction`; `result` and `error` are nullable. Failed jobs remain HTTP
200 with a non-null structured error. `result.href` is preserved from storage; the audit destination
`/api/v1/audits/{audit_id}` is callable and returns the persisted audit
lifecycle summary. Worker identity, claim tokens, leases, attempts and resource columns
are not part of the public schema.

`POST /jobs/{job_id}/cancel` takes no body and returns HTTP 202 with the same
public `Job` model. Queued audit cancellation immediately returns `cancelled`
after the repository transaction updates the audit and queued samples. Running
audits return `cancelling` until a worker reaches a safe boundary; repeated
requests preserve that state. Completed, failed or already-cancelled jobs return
409 `JOB_ALREADY_FINISHED`. Missing jobs return 404, invalid identifiers return
422, and storage/unexpected failures return the shared safe 503/500 envelopes.
Cancellation preserves completed measurements, findings and reviewer decisions.

Errors share this envelope:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "One or more request fields are invalid.",
    "details": [{"field": "body.expected_version", "message": "Input should be greater than or equal to 1"}],
    "request_id": "request_demo"
  }
}
```

Use codes for handling and messages/details for display. Validation locations
are dotted, such as `body.expected_version` or `query.limit`. Model extraction
codes such as `MODEL_UNAVAILABLE`, `MODEL_REQUEST_TIMEOUT` and `INVALID_RULE_PROPOSALS`
are persisted asynchronously in accepted extraction jobs; see EXTRACTIONS.md. Validation uses 422, missing records
404, stale revisions and invalid workflow transitions 409. Domain-specific
conflict codes are described on their operations; generic error examples show
the shared envelope rather than every possible domain failure.

Use `expected_version` for rulebook writes, `expected_revision` for annotations,
and `expected_review_version` for finding/sample reviews. Refresh after a 409
before deciding whether to reapply changes.
Review revisions start at 0; annotation and rulebook versions start at 1.
Sample-review writes additionally require `expected_sample_revision` for the
stored audit sample pin; `expected_review_version` means the review counter.

Dataset ZIP and mask PNG uploads use multipart FormData. Fixture strings like
`<binary dataset ZIP>` represent file parts, not literal JSON bodies. Let the
browser set multipart Content-Type and its boundary. Asset and archive downloads
return bytes, not JSON wrappers; unavailable files return the JSON error envelope.

## Review state

Rule confirmation is `draft`, `confirmed`, `rejected`; origin is `extracted` or
`reviewer`. Keep rejected rule history, but remove rejected rules from a book
before confirming its active version. Unsupported requirements remain visible.

The rulebook review contract follows the implemented Python service:

- `PUT /rulebooks/{rulebook_id}` submits the complete guideline text and rule list
  with `expected_version` and `reviewer_id`. Omitted rules are removed. Retained
  IDs keep their origin; new IDs use `reviewer`. Supply each rule's decision
  explicitly. Citations must match the supplied text, and extracted rules require
  a citation. The new book remains draft even when all its rules are confirmed.
- `POST /rulebooks/{rulebook_id}/confirm` requires a latest, nonempty draft with
  every retained rule confirmed. It does not confirm rules automatically.
- `POST /rulebooks/{rulebook_id}/reject` rejects the latest draft as a whole and
  preserves its individual rule decisions.
- `POST /rulebooks/{rulebook_id}/revisions` starts a draft from the latest
  confirmed or rejected book and resets every included rule to draft. A draft
  is already editable. Final versions cannot be edited directly.
- `GET /rulebooks/{rulebook_id}/versions` and
  `GET /rulebooks/{rulebook_id}/reviews` return paginated snapshots and attributed
  actions in ascending version order. Extraction and older low-level saves have
  no review records. An existing book can therefore have an empty review list.

Each successful review action creates version `expected_version + 1` and commits
the snapshot and reviewer record atomically. The backend assigns the record's
timestamp. Failures preserve both histories. Existing databases must apply
`002_rulebook_reviews.sql` through the migration runner described in
the backend setup.

The implemented HTTP mapping is 409 `VERSION_CONFLICT` for stale versions,
409 `INVALID_RULEBOOK_STATE` for invalid transitions and 409 `RULEBOOK_NOT_READY`
for premature confirmation. Invalid fields or citations use 422
`VALIDATION_ERROR`; missing books or requested versions use 404. The Python
service also rejects origin changes with reason `invalid_rule_origin`. Reload
after a conflict before resubmitting reviewer decisions. Services currently raise
exceptions; the implemented HTTP adapter maps known reasons to safe responses.
Storage failures return safe 503 and unknown/internal failures return safe 500.

Finding outcomes are `pass`, `fail`, `unsure`, `unavailable`. Resolutions are
`pending`, `accepted`, `rejected`, `unsure`. **Accepting a finding confirms the
tool's conclusion; it does not accept the sample.** Rejecting dismisses the
finding. Current sample decisions use `pending_review`, `needs_correction`,
`accepted`, `rejected`. No tool confidence automatically changes those decisions.
`reviewer_id` records attribution for the local prototype, not an implemented
authentication system.

The finding-review Python service, PostgreSQL history storage and FastAPI routes are
implemented. `PUT /findings/{finding_id}/review` uses `FindingReviewRequest`:
`expected_review_version`, `resolution`, `reviewer_id` and `comment` are required;
a comment may be empty. Server-owned revision and timestamp cannot be supplied in
the request. `GET /findings/{finding_id}/reviews` returns `FindingReviewPage` with
attributed `FindingReviewRecord` items in ascending revision order and supports
`limit` (1–100, default 20) and `offset` (default 0). A missing finding returns
404 and an empty history is a valid 200 page for an existing finding.
Pending findings start at review revision 0. A successful save advances it by one
and preserves every earlier decision. The HTTP mapping is 409
`VERSION_CONFLICT` for `FindingReviewConflictError`, 404
`NOT_FOUND` for `FindingNotFoundError`, 422 `VALIDATION_ERROR` for invalid request
fields, 503 `DEPENDENCY_UNAVAILABLE` for storage/configuration failures and 500
`INTERNAL_ERROR` for unexpected failures. Reload after a conflict before
resubmitting. Finding repository reads include the latest review.
See finding review setup for the migration and usage.

The sample-review Python service, PostgreSQL history storage and FastAPI routes are
implemented. `PUT /audits/{audit_id}/samples/{sample_id}/review` uses
`SampleReviewRequest`: `expected_sample_revision`, `expected_review_version`,
`decision`, `reviewer_id` and `comment` are required. Responses reuse `SampleReview`
and include `audit_id`, `sample_id` and `sample_revision`. The first two IDs come
from the route; the sample revision must identify the stored audit pin. Review
revision starts at 0; every saved decision increments it, including an explicit
`pending_review` reopening with reviewer attribution. Empty comments are allowed.
`GET .../reviews` returns `SampleReviewPage`/`SampleReviewRecord` history sorted by
ascending review revision, requires the `sample_revision` query parameter and never
mixes other audits or sample pins. These required pin fields extend the earlier
draft contract; frontend callers and fixtures must include them.

Only `completed` and `failed` audit sample rows are reviewable. This includes
incomplete measurement reports, no-issue/no-findings cases and failures without
measurements; reviewers can use the available evidence and manual inspection.
Eligibility depends on the individual sample, regardless of parent audit status.
Queued/running/cancelled samples reject every decision, including reopening.
Accepting failed or incomplete data preserves its technical failure/evidence.
No machine outcome or finding-review state automatically decides sample use.

HTTP mappings are 409 `VERSION_CONFLICT` for `SampleReviewConflictError`, 409
`STALE_SAMPLE_REVISION` for `SampleRevisionConflictError`, 409
`INVALID_SAMPLE_REVIEW_STATE` for `SampleReviewStateError`, 404 `NOT_FOUND` for
`AuditSampleNotFoundError`, 422 `VALIDATION_ERROR` for invalid request fields, 503
`DEPENDENCY_UNAVAILABLE` for storage/configuration failures and 500 `INTERNAL_ERROR`
for unexpected failures. The sample revision conflict only means the supplied pin is not stored for that audit/sample. It does
**not** verify current annotation files. Snapshot-backed Python audits now read
captured immutable assets, while legacy audits still read live files.
`needs_correction` saves a decision and does not
edit annotations. Correction/recheck workflows create fresh evidence
and require a new decision on corrected revisions. Current decisions/history can
be read through `SampleReviewRepository` or the implemented queue/detail/review-history
GET routes. Reviewed-count aggregation remains planned.
See sample review setup.

The implemented queue supports `limit` (1–100, default 20), `offset` (default 0),
and optional `tool_status`/`review_status` filters. Results retain original audit
order; totals count filtered matches. Summary fields `processing_status` and
`incomplete` expose technical state independently of human decisions. Machine
status is pending while queued/running; otherwise fail takes precedence over
unsure, then unavailable. Only completed, complete, nonempty, all-pass results are
`no_issue`. No findings means unavailable, not automatic success.

Detail adds `failures` and permits `measurements: null` for samples without stored
measurements. Non-null measurements preserve the stored collector object, including older
minimal envelopes, except loading-error details use safe messages even for old
reports. The stored evidence is not rewritten. The `AuditMeasurements` schema documents the
full collector shape; it is not imposed on older saved documents. All findings
and current reviews are returned, scoped to the exact audited revision. Reads
use one database snapshot. Snapshot-backed samples populate asset fields from
the exact manifest association: image and thumbnail depend on `original_image`,
while mask and `mask_preview` depend on `raw_mask`. Missing/unreadable roles and
legacy samples without a manifest association remain null. Links advertise
captured availability only; the asset route verifies integrity and previews may
reject corrupt or unsupported sources. The four machine-outcome scenarios show
snapshot-backed available links; the additional `legacy` scenario demonstrates
null links with the same stored evidence and pending review.

The audit detail's raw measurements preserve their original `pending_review`
field. Render the separate current review record for actual reviewer decisions.
Each measurement check has status, reason, detail, result. `completed` means the
measurement ran, not that data passed. Unavailable results can be null or contain
low-level evidence. Null fractions mean unavailable, never zero.

Low-level measurements consistently use `measurement_available`; box/mask
alignment's earlier `comparison_available` field has been replaced. Sample checks
include `annotation_metadata`, which compares declared image dimensions, object
count and coordinate origin. Missing or unreadable assets block only dependent
checks. Missing or unsupported coordinate origins block box comparisons while
ID, label, dimension and component checks can still run.

The no-issue fixture also stays pending review. Uncertain and unsupported-rule
fixtures illustrate future rule/model execution, which the current measurement
service cannot produce on its own. Evaluation numbers are synthetic interface
examples, not measured accuracy or performance.

## Images, corrections and export

Boxes are `[xmin, ymin, xmax, ymax]`, one-based and inclusive. Image size is
`[width, height]`. Invalid source coordinates remain visible with integrity
results; corrected geometry must pass server validation. For zero-based canvas:

```text
left = xmin - 1
top = ymin - 1
width = xmax - xmin + 1
height = ymax - ymin + 1
```

Convert edited geometry back to the source convention when submitting it. Raw
mask PNGs retain integer instance IDs, with 0 as background. Colored mask previews
are display assets and must never be submitted as raw masks. One pixel holds
one ID; box overlap does not imply shared mask ownership. URLs pin revisions.

The Python preview service and the implemented asset route generate or return
PNG bytes for an explicit revision. `GET /datasets/{dataset_id}/samples/{sample_id}/assets/{asset_kind}`
accepts only `image`, `mask`, `mask_preview` and `thumbnail`, and requires a
positive `revision`; it never chooses the latest revision. Raw image/mask bytes
are verified against the immutable manifest, while previews are rendered in
memory and can return `ASSET_NOT_RENDERABLE` for corrupt or unsupported sources.
All four kinds return `ASSET_TOO_LARGE` when the captured source exceeds 64 MiB.
Raw responses preserve malformed captured files too: size/SHA-256 verification
does not certify PNG validity. Their content type is selected by the public
source role (`image/png`), never by stored metadata, and responses include
`X-Content-Type-Options: nosniff`.
Asset links never expose private storage paths. Missing/unavailable assets use the
JSON error envelope, and legacy audit samples without manifest associations keep
null links.

**Correction workflow (implemented in source):** stage the PNG first, then submit its upload ID with the
complete replacement object list. Add/remove objects by adding/removing entries.
Commit objects and mask atomically after checking IDs, labels and geometry.
Staged masks expire; expired uploads must be repeated. Old audit evidence and
final decisions do not automatically carry forward to corrected data.

**Reviewed dataset exports** use pinned reviewed revisions and preserve source
coordinates. The export routes are implemented in source. The example uses `accepted_only`;
`all_reviewed` explicitly includes accepted and rejected data with decisions in
the manifest. Pending and needs-correction data remain excluded. Exports freeze
the current human decision for each exact audited revision at submission. A later
catalogue head change does not invalidate that historical pin, and later review
changes do not alter an existing export's frozen selection. Exports do not require
corrections or rechecks. Images are only included when requested. No actual demo
PNG or ZIP files are supplied here. The five contract artifacts are regenerated.
Tests have not been rerun after corrections; migration application and real
worker/database/download verification remain pending.

## Feedback boundary

The supplied strategy freezes explicitly selected, accepted binary model findings
as examples for one exact sample-scoped `annotation_guideline` rule and dataset.
It never changes deterministic numeric checks, trains/downloads a model or silently
activates a policy. Ground truth requires explicit pass/fail rule-level human
adjudications, including checked no-issue cases and missed errors, against exact
immutable revisions. Corrections alone and missing findings do not assign truth.
Fresh baseline/candidate inference uses the same held-out evidence and execution
pin; candidate sees only training examples, not held-out labels/comments.
See [FEEDBACK.md](FEEDBACK.md) for all ten routes, payloads, limits, metric
denominators, leakage checks, explicit activation and owner-run commands.
Storage, mocked results and source delivery do not prove actual improvement.

## Frontend fixtures

Load `examples.json` into your mock/test layer:

```javascript
const template = examples.operations.find(
  operation => operation.implementation === "implemented"
    && operation.operation_id === "getAuditSample"
);
const responseBody = structuredClone(template.response.body);
```

Use `examples.review_scenarios.flagged`, `.no_issue`, `.unsure`, `.unavailable`
to develop review states, and `.legacy` to handle unavailable asset links.
Their matching confirmed rulebooks are under
`examples.review_scenario_rulebooks` with the same keys; the uncertain and
unavailable cases include their additional reviewer-authored rules.
`examples.job_scenarios` and `examples.audit_scenarios` cover all six lifecycle
states with model-validated snapshots. `examples.dataset_import_scenarios` adds delivered import states and dirty-source examples. `examples.feedback_job_scenarios` adds the six implemented feedback job states
after regeneration; `examples.future_job_scenarios` is empty for current producers.
`examples.recheck_job_scenarios` provides delivered recheck states with kind `audit`. Delivered export examples are in `examples.export_scenarios`.
Delivered extraction mocks are in
`examples.extraction_job_scenarios.states` and `.failures`. Completed extraction links pin rulebook version 1.
`examples.check_catalog` comes from the
current registry and includes supported check names, meanings, scopes and parameter
schemas. These are bundled reference data, not a live `/capabilities` endpoint.
Do not invent default thresholds for vague rules.

Fixtures are independent synthetic snapshots, not a mutable mock server. Example
IDs are not stored resources, and no binary PNG/ZIP files are embedded. Generate
live clients from `implemented-openapi.json`; mock planned operations separately.

## Implemented rulebook HTTP workflow

Ten rulebook operations are implemented: queue plain-text extraction, create a manual draft, list latest
snapshots, read latest/explicit versions, list versions, list attributed reviews,
replace a draft, confirm, reject and reopen a final book as a draft. See
the rulebook guide for exact paths,
strict requests, states and error codes. Manual creation accepts strict editor
inputs, preserves exact guideline text and saves a version-1 draft without
inventing review history. Extraction and audit jobs use separate explicit workers;
feedback-evaluation routes are supplied in source; model and worker validation remain separate gates. Authentication is not implemented by this local prototype.

Mutations use request-time repository injection. Review actions append a snapshot
and real attribution atomically; initial creation adds no review record. Reads
never write, run inference or inspect assets. reviewer_id is attribution, not
verified identity. Conflicts require refreshing and explicit reviewer resubmission.
An unsupported check can be confirmed for human review without becoming an
implemented evaluator. Replacement can remove rules; book confirmation never
implicitly confirms rules. Earlier snapshots remain unchanged.

The four GETs exclude mutation-only 409 declarations. Each mutation documents
only its supported conflicts. Invalid replacement IDs/citations use a narrow
client-error boundary; stored corruption and unrelated validation errors do not.
No exception internals are returned to clients.

Creation validates duplicate IDs and exact citations, forbids server-owned
metadata, preserves unsupported/null checks with finite parameters, and never
returns a success response after storage failure. Generated-ID collisions use
the existing safe 409 mapping without overwrite or version advancement. Separate
successful creation requests create separate books; no automatic retry or
idempotency store is added.

Generated OpenAPI, fixtures and endpoint inventories must come from
`.venv/bin/python -B -m backend.api.build_contract`, not manual artifact edits.
Earlier passing HTTP/contract results do not validate the current integration.
The user reports the pre-correction baseline passed. Reported correction/recheck
failures are addressed in source and regenerated artifacts, but tests have not
been rerun after those fixes. Migration and worker verification remain separate.
PostgreSQL integration tests require `PRISM_TEST_DATABASE_URL`.

## Durable plain-text extraction

See EXTRACTIONS.md for the complete submission, worker, polling, cancellation, review and error workflow. Extraction requires migration 009, explicit worker startup and operator-controlled runtime verification. The shared artifacts are regenerated. 202 does not imply model/worker readiness. No private ledger/claim fields are added to Job.

## Reviewed export integration

See [EXPORTS.md](EXPORTS.md). Five export operations are implemented in the applied
source, using existing job polling/cancellation. Four planned operations are
promoted in place and the collection GET is new. The integrated inventory is
46 implemented, 4 planned, 50 total after this handoff is applied.
All five artifacts are regenerated for the correction/recheck source as well.
A fresh test pass, the required migrations and real
worker/download verification remain pending; source availability and schema
generation are not runtime validation.

## Correction/recheck application gate

See [CORRECTIONS.md](CORRECTIONS.md). Source and all five regenerated API artifacts
describe 46 implemented and 4 planned operations, 50 total. Migration 012 and
regression/runtime verification after the latest fixes remain separate gates.
