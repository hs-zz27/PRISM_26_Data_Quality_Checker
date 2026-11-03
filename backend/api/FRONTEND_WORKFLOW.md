# Frontend workflow and mock guide

> Current feedback source proposal: ten feedback operations plus real bounded
> `annotation_guideline` inference are supplied. Proposed inventory: 46 implemented,
> 4 planned, 50 total. This handoff did not run tests, migrations, workers, inference
> or API contract generation. Regenerate all five artifacts together before using
> them for this feature; earlier generation/test statements below refer to the
> captured pre-feedback work. See [FEEDBACK.md](FEEDBACK.md).

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
- RULE_APPLICABILITY.md: rule applicability, per-annotation visual findings (`scope` evidence) and the
  optional `object_keys` on rechecks; all additions are optional for existing screens.
- README.md: setup, errors, coordinates and asset details.

The live-only spec includes `/api/v1` in paths. The combined spec includes it in
the server URL instead. Add that prefix once. Keep the backend origin configurable;
use a development proxy for `/api/v1` because cross-origin access is not enabled
by default. Resolve resource/asset links against that origin or proxy.
The current server is a local unauthenticated prototype. `reviewer_id` attributes
decisions; it is not authentication or a permission mechanism.

## 1. Start screen and service readiness

**Implemented:** `GET /health`, `GET /capabilities`, `GET /system/status` (see [SYSTEM.md](SYSTEM.md)).

Design separate HTTP liveness and model-readiness indicators. A reachable API
does not imply that Ollama or detector weights are available. These reads
report readiness rather than start services or download models. Capabilities
lists executable checks and, separately, detection evidence features.
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

**Implemented in the applied dataset checkpoint source:** POST `/datasets`
accepts exactly multipart `archive`, nonblank `name`, and `format=penn_fudan`.
Its acknowledged 202 contains a persisted importing dataset plus queued job with
unknown total/fraction. Do not set the browser multipart boundary yourself or send
the synthetic binary marker. Start the explicit import worker separately.

Poll `/jobs/{job_id}`; cancellation reuses POST `/jobs/{job_id}/cancel`.
Browse `/datasets`, `/datasets/{dataset_id}`, and its `/samples` collection/detail.
Existing empty/partial catalogues remain browsable during import and after failure
or cancellation. Lists select published current heads; omit detail `revision` to
select that explicit head, or supply a positive exact revision. All returned asset
links pin the selected revision. Never select current via manifest maximum.

Ready is not clean data. Display `issues`, null object counts, unavailable dimensions,
and missing links without hiding samples. Empty objects only mean zero objects when
annotation evidence establishes that fact. Corrupt readable bytes can have raw links
while derived previews fail safely. Do not automatically accept/reject samples.
Use `examples.dataset_import_scenarios` for all six job states, invalid archives,
partial cancellation, dirty samples and explicit-zero versus malformed annotations.
See DATASETS.md for worker setup, resource policies, safe errors and generation gates.
Audit creation, annotation/mask correction revisions and linked rechecks are implemented
in source after the current handoff is applied; see CORRECTIONS.md.
Reviewed dataset exports are implemented in source and their contracts are
regenerated. Tests have not been rerun after integration corrections; migration
application and real worker/database/download verification remain pending.

## 4. Start an audit and display processing

**Implemented:** `POST /audits` returns HTTP 202 `AuditStarted` containing `audit` and `job`.
Submit a ready dataset ID and an explicit confirmed rulebook ID/version.
Omitted/null `sample_ids` selects all samples; a nonempty unique list selects those
samples in request order. Omit/null `feedback_policy_id` for baseline context, or explicitly choose an active
compatible immutable policy. The server pins its execution configuration at acceptance. Start the explicit
audit worker documented in AUDITS.md. `GET /audits` lists persisted runs, newest first.
Do not add a CPU batch-size field or assume the standalone detector is integrated.

**Implemented:** poll `GET /jobs/{job_id}` and read `GET /audits/{audit_id}` using
IDs from the creation response. Startup creates no demo records.
Audit processing completion does not mean that a reviewer accepted its samples.

The live audit read returns `AuditRun` directly. Start and linked recheck responses
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
Completed evidence and decisions remain available. Dataset import, extraction
and export jobs use their own managed cancellation dispatch through the same
endpoint. Linked rechecks use kind `audit` and the same audit-aware cancellation
and recovery path. Feedback comparisons use their own managed explicit worker and existing public job polling/cancellation.

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

**Implemented:** `GET /audits/{audit_id}/findings` lists the findings of an audit across all of
its samples (see [AUDITS.md](AUDITS.md)). Filter with `sample_id` and `outcome`, page with
`limit` and `offset` until `next_offset` is null, and read the latest human decision in each
item's `review`. It lists tool findings only; open `GET /audits/{audit_id}/samples/{sample_id}`
for assets, measurements and the sample decision.

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

**Implemented in source:** stage a raw instance-ID PNG through
`POST /datasets/{dataset_id}/samples/{sample_id}/mask-uploads` using multipart,
including its required `expected_revision`. Its response's `base_revision` binds
the staged mask to the source revision being edited.
Then POST `/revisions` with its `mask_upload_id`, or null when keeping the mask,
the last-read `expected_revision`, attribution/reason and the full object list.
Omitted objects are removed; this is not a patch. The correction publishes a real immutable annotation/mask revision atomically
with provenance, head movement and any staged-mask consumption. Expired staged uploads must be repeated.
Use revision-history GET to display earlier versions. Keep original audit
evidence visible. Old decisions remain valid for their exact immutable old pins;
correction must not rewrite them or transfer acceptance to the new revision.

## 8. Recheck and export

**Implemented in source:** POST `/audits/{audit_id}/rechecks` with selected sample IDs, explicit
`sample_revisions` and reviewer attribution. The map keys must exactly match
`sample_ids`, with each value the expected positive current revision. Handle a
stale-revision 409 by refreshing; malformed selections use 422. Do not silently substitute revisions. Poll the returned
job and review the new audit's pinned revisions.
The child pins the exact parent rulebook, compatible feedback policy snapshot
and model execution configuration, including baseline/null context. Selected heads remain locked through enqueue. Its job kind is
`audit`; run the existing explicit audit worker. It creates fresh evidence;
old final sample decisions are not inherited.

**Implemented in source:**
`POST /exports` returns `export` plus `job`. After preparation, read
`GET /exports/{export_id}`, then follow download/manifest links or their endpoints.
`accepted_only` includes explicit sample acceptance. `all_reviewed` includes
accepted and rejected decisions; pending/needs-correction samples remain excluded.
The export uses immutable reviewed pins, not silently substituted latest files, and
does not require annotation corrections or rechecks. Exports freeze the current
human decision for each exact audited revision at submission. A later catalogue head
change does not invalidate that historical pin, and later review changes do not
alter an existing export's frozen selection. ZIP download is binary; the manifest
is JSON. Handle 409 `EXPORT_NOT_READY` and `AUDIT_NOT_READY` without presenting a
successful download. Contracts are regenerated; the corrections have not been
retested. Migration application and real worker/database/download verification
remain separate gates.

## 9. Evaluated feedback

**Implemented in supplied source:** browse approved `/feedback/examples` and select
1–4 compatible example IDs plus reviewer attribution when creating a policy.
Create truth with explicit sample-revision/rule references, binary expected
outcomes and human reasons; absence of a finding is never automatic compliance.
Submit `/feedback/evaluations` with exact policy/truth IDs, version 1 and reviewer
attribution. Poll its existing job; cancel through the existing job endpoint.
Inspect actual per-case predictions, durations, confusion counts, coverage and
nullable ratios, then explicitly activate a matching complete evaluation.
See [FEEDBACK.md](FEEDBACK.md) for concrete transports, all ten operations and
commands. Only explicit policy selection affects future audits; linked rechecks
preserve parent pins and create fresh pending reviews. No automatic global policy
or synthetic improvement claim is introduced. New generated artifacts require
local regeneration and runtime/model validation remains outstanding.

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

## Reviewed export flow

POST `/api/v1/exports` with audit_id, format="penn_fudan", selection and boolean
include_images; 202 means durable enqueue, not a ready ZIP. Poll its existing job
and export metadata, or cancel through the existing job API. List export history
with GET `/api/v1/exports?limit=20&offset=0`. Download/manifest are 409 until completed.
Counts and artifact URLs are null until completion. Failed/cancelled metadata
remains readable. Empty completed selections are valid manifest/README-only ZIPs.
accepted_only selects current explicit acceptance; all_reviewed also includes
rejected entries diagnostically without changing their decision. Pending/reopened
and needs_correction are excluded. Export revisions remain historical audit pins.
A lost POST response requires reconciliation through list/detail/job reads before
resubmission; there is no automatic retry or idempotency key. See EXPORTS.md for
safe errors, selection snapshot cutoff, omitted images and storage limitations.

## Correction/recheck contract gate

The correction/recheck source is applied and all five API artifacts are
regenerated (46 implemented and 4 planned at that point; all 50 operations are implemented now).
Use the updated implemented-only contract for client generation. Migration 012 and regression
execution after the latest integration fixes remain separate runtime gates.
