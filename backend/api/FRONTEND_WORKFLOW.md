# Frontend workflow and mock guide

Build screens against the contracts below, using live calls only for operations
marked **implemented**. Planned operations describe the intended interface; their
routes and services are unavailable until backend implementation is delivered.
Keep those calls behind your mock transport so the UI can progress independently.

- ENDPOINTS.md: status of every operation.
- PLANNED_ENDPOINTS.md: future requests, responses and examples.
- implemented-openapi.json: generate the live client here.
- openapi.json: complete implemented/planned schema reference.
- examples.json: synthetic requests, resources and review scenarios.
- RULEBOOKS.md: current manual creation and versioned review behavior.
- README.md: setup, errors, coordinates and asset details.

The live-only spec includes `/api/v1` in paths. The combined spec includes it in
the server URL instead. Add that prefix once. Keep the backend origin configurable;
use a development proxy for `/api/v1` because cross-origin access is not enabled
by default. Resolve resource/asset links against that origin or proxy.
The current server is a local unauthenticated prototype. `reviewer_id` attributes
decisions; it is not authentication or a permission mechanism.

## 1. Start screen and service readiness

**Planned:** `GET /health`, `GET /capabilities`, `GET /system/status`.

Design separate HTTP liveness and model-readiness indicators. A reachable API
does not imply that Ollama or detector weights are available. These planned reads
will report readiness rather than start services or download models. Capabilities
may include implemented checks even while the capabilities HTTP route is planned.
For mocks, use `examples.check_catalog` for check meanings and parameter schemas;
do not infer runtime model readiness from synthetic fixtures.

## 2. Create and review a rulebook

**Implemented manual path:** `POST /rulebooks` creates a draft, then use the
saved-rulebook reads and review actions documented in RULEBOOKS.md.

**Implemented extraction submission path:** submit plain text through `POST /rulebooks/extractions`.
Its committed 202 response is a `Job`, not the rulebook; a separately started worker is required. Poll `GET /jobs/{job_id}`;
on completion, follow a non-null `result.href` to the saved draft.
Model failures belong in the job's structured error after work was accepted.

Display the original guideline, exact source citations, proposed meaning and
parameters for review. Never invent a threshold for a vague requirement.
Internal check names may identify controls, but show their human-readable meaning.
Unsupported or null checks remain visible and may require clarification.

**Implemented review path:** list books with `GET /rulebooks`, read a book or
specific `?version=N`, and retain the returned version. Save the complete draft
with `PUT /rulebooks/{rulebook_id}`. Confirm, reject or start a new draft through
the corresponding POST action. Every action uses the last-read `expected_version`.
Individual rule confirmation does not confirm the book. A final version is frozen;
later edits begin through `/revisions`. Show version and review-attribution history.

## 3. Import and select dataset samples

**Planned:** upload through `POST /datasets`; the 202 response contains both
`dataset` and `job`. Poll the job, then load `GET /datasets/{dataset_id}`.
Use `GET /datasets` for selection, `/datasets/{dataset_id}/samples` for browsing,
and `/datasets/{dataset_id}/samples/{sample_id}` for a sample/revision view.

Send a ZIP file part plus the contract's text fields as multipart `FormData`.
Do not send JSON, base64 or the fixture's `<binary dataset ZIP>` placeholder.
Let the browser set multipart `Content-Type` and its boundary. The proposed first
adapter is Penn-Fudan; do not advertise arbitrary archive formats.

Distinguish importing, ready, invalid, failed and cancelled datasets. Show sample integrity
issues without pretending that malformed source annotations are corrected.
The planned sample read allows latest-revision discovery when revision is omitted;
asset downloads always require an explicit revision pin.

## 4. Start an audit and display processing

**Planned:** `POST /audits` returns `AuditStarted` containing `audit` and `job`.
Submit a ready dataset ID and an explicit confirmed rulebook ID/version.
The proposed `sample_ids: null` selects all samples; a list selects those samples.
Keep `feedback_policy_id: null` until an applicable active policy is available.
Do not add a CPU batch-size field or assume the standalone detector is integrated.

**Implemented for existing jobs/audits:** poll `GET /jobs/{job_id}` and read
`GET /audits/{audit_id}`. Startup creates no demo records: obtain existing IDs
from the backend developer or use mocks while creation/import routes are planned.
Audit processing completion does not mean that a reviewer accepted its samples.

The live audit read returns `AuditRun` directly. Planned start/recheck responses
wrap that lifecycle shape as `audit` beside `job`; optional parent/policy context
belongs to the wrapper. Do not require `reviewed_count`, `parent_audit_id` or
`feedback_policy_id` on the persisted `AuditRun` response.

## 5. Jobs, cancellation and asynchronous errors

Polling an existing job returns HTTP 200 in all six states:
`queued`, `running`, `cancelling`, `completed`, `failed`, `cancelled`.
Stop automatic polling at the last three states. Unknown totals/fractions are
null, not zero. Result and error are nullable; never assume a result exists.
Render a failed job's structured error even though the polling request succeeded.

An HTTP error rejects a request or indicates that polling itself failed. A
`job.error` reports failure of previously accepted work. Keep these UI states
separate; a temporary failed poll does not establish that the job failed.

**Implemented:** `POST /jobs/{job_id}/cancel` has no request body, returns 202,
and uses the existing audit-aware dispatch. A queued audit can already be
cancelled in that response; a running audit enters cancelling. Continue polling
until terminal. Terminal cancellation conflicts use 409 `JOB_ALREADY_FINISHED`.
Completed evidence and decisions remain available. Do not assume cancellation
support for future import/export/evaluation job kinds merely because
their proposed responses use the same public Job schema.

## 6. Review queue, evidence and decisions

**Implemented:** `GET /audits/{audit_id}/samples` and its sample-detail route.
Omit filters to show every audited sample, including apparently correct cases.
Lists paginate with `limit`, `offset`, `total`, `next_offset`; null next offset ends
pagination. Optional `tool_status`/`review_status` filters change totals. Send no
query parameter to disable a filter, rather than the string `null`.

Keep processing state, incomplete evidence, machine outcome and human decision
separate. Outcomes are pass/fail/unsure/unavailable; no findings is not a pass.
Detail can have `measurements: null`, technical failures and null asset links.
The collector's stored `pending_review` field is historical; display the separate
current review record for the actual decision.

**Implemented:** finding review PUT/history GET and sample review PUT/history GET.
Accepting a finding agrees with the tool's conclusion; accepting a sample is a
separate decision. `needs_correction` records a decision without editing data.
Finding writes need `expected_review_version`. Sample writes also require
`expected_sample_revision`; sample history requires `sample_revision` in the query.
Review counters start at zero, independently of source revisions and book versions.
Only completed/failed sample rows are currently reviewable, including incomplete
evidence; queued/running/cancelled rows reject decisions.

**Planned:** `/audits/{audit_id}/findings` adds a cross-sample finding list.
Use existing detail responses for live per-sample findings meanwhile.

## 7. Assets and annotation correction

**Implemented:** `GET /datasets/{dataset_id}/samples/{sample_id}/assets/{asset_kind}`
for image, mask, mask_preview and thumbnail, with required positive `revision`.
Follow supplied pinned links; do not silently swap in current dataset files.
Null links mean unavailable/unassociated assets, including legacy audits.
Decode/render failure needs an explicit UI state even for a non-null link.
Raw masks preserve instance IDs; colored previews are display assets only.

Annotation boxes are one-based inclusive `[xmin, ymin, xmax, ymax]`; image size is
`[width, height]`. Canvas left/top subtract one, while width/height include `+1`.
Convert edited boxes back to the source convention before submission.

**Planned:** stage a raw instance-ID PNG through
`POST /datasets/{dataset_id}/samples/{sample_id}/mask-uploads` using multipart,
including its required `expected_revision`. Its response's `base_revision` binds
the staged mask to the source revision being edited.
Then POST `/revisions` with its `mask_upload_id`, or null when keeping the mask,
the last-read `expected_revision`, attribution/reason and the full object list.
Omitted objects are removed; this is not a patch. The proposed correction is an
atomic annotation/mask revision. Expired staged uploads must be repeated.
Use revision-history GET to display earlier versions. Keep original audit
evidence visible. Old decisions remain valid for their exact immutable old pins;
correction must not rewrite them or transfer acceptance to the new revision.

## 8. Recheck and export

**Planned:** POST `/audits/{audit_id}/rechecks` with selected sample IDs, explicit
`sample_revisions` and reviewer attribution. The map keys must exactly match
`sample_ids`, with each value the expected positive current revision. Handle a
stale-revision 409 by refreshing; malformed selections use 422. Do not silently substitute revisions. Poll the returned
job and review the new audit's pinned revisions.
The target contract keeps the prior book/policy while creating fresh evidence;
old final sample decisions are not inherited.

**Planned:** `POST /exports` returns `export` plus `job`. After preparation, read
`GET /exports/{export_id}`, then follow download/manifest links or their endpoints.
`accepted_only` includes explicit sample acceptance. `all_reviewed` includes
accepted and rejected decisions; pending/needs-correction samples remain excluded.
The proposed export uses reviewed pins, not silently substituted latest files.
ZIP download is binary; the manifest is JSON. Handle 409 `EXPORT_NOT_READY` and
`AUDIT_NOT_READY` without presenting a successful download. A newer dataset head
does not invalidate review decisions for the immutable exported pin.

## 9. Evaluated feedback

**All planned:** browse approved `/feedback/examples`, create/read/list candidate
`/feedback/policies`, and freeze reviewer-verified `/feedback/ground-truth`.
Submit `/feedback/evaluations` with the policy and explicit ground-truth version;
poll its job, read/list evaluations, compare measured baseline/candidate metrics,
then explicitly POST `/feedback/policies/{policy_id}/activate`.

Storing corrections alone is not evidence of improvement. The proposed policy
retrieves approved examples for supported model checks; it does not train a model
or change deterministic label rules. Held-out data must not overlap policy
examples. Metrics can be null before measurement or when undefined. Fixture
numbers are synthetic. Activation must not change historical audits or overrule
reviewers; show incomplete/missing evaluation as a blocked state.

## 10. Mock coverage and conflict behavior

Use fixture `implementation` flags when selecting live versus mock operations.
Fixtures are examples rather than stored demo records or a stateful mock server.
Model state transitions explicitly instead of replaying unrelated scenario IDs.
Cover loading/empty/populated/paginated views, all six job states, pending/no-issue/
flagged/uncertain/unavailable reviews, legacy null links, corrupt previews, invalid
forms, stale versions, storage failures and cancelled partial audits.

Errors use `error.code`, safe message/details and `request_id`. On a version/pin
409, preserve the user's unsaved input, reload current state and let the reviewer
decide whether to reapply it. Do not automatically retry mutations or overwrite
newer decisions. Draft creation is not idempotent; duplicate successful requests
create separate books. Treat planned contracts as reviewable designs and coordinate
changes as each feature becomes callable.

## Extraction execution and fixtures

See EXTRACTIONS.md for the explicit worker, one-attempt/deadline policy, safe errors and cancellation limits. Extraction cancellation is implemented through the existing job route. Completion pins version 1; later review does not change that pin. Use `examples.extraction_job_scenarios.states` and `.failures`; other producer mocks remain under `examples.future_job_scenarios`. Do not infer worker/model readiness from 202 or synthetic fixtures.
