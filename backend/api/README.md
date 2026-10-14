# Proposed API contract

This folder gives the frontend developer endpoint templates, request bodies,
response schemas and synthetic examples for the complete planned workflow.
**No HTTP server or router is implemented yet.** These files describe the
interface to build; they do not make the routes callable.

The draft base address is `http://127.0.0.1:8000/api/v1`. Port 8000 is a proposed
development default. Keep the base URL configurable in the frontend.

## Files

- [ENDPOINTS.md](ENDPOINTS.md): every method, path and operation ID.
- [openapi.json](openapi.json): OpenAPI 3.1 schemas and examples for local Swagger
  UI, API clients and client-type generation.
- [examples.json](examples.json): frontend fixtures with path parameters, query
  values, request body, response status and response body. Also includes flagged,
  no-issue, uncertain and unavailable review scenarios.
- [build_contract.py](build_contract.py): source for generated artifacts. Reuses
  actual `Rule`/`Rulebook` schemas and obtains measurement examples from
  `audit_sample()` on a tiny synthetic dataset.

Run from the project root:

```bash
.venv/bin/python -m backend.api.build_contract
.venv/bin/python -m pytest backend/tests/test_api_contract.py -q
```

Edit the builder rather than generated artifacts. This is draft contract version
`0.1.0`; agree on field changes with the frontend before implementing routes.
No API framework is selected here. Format reference:
https://spec.openapis.org/oas/v3.1.0.html.

## Frontend workflow

1. Inspect health, capabilities and system status. Liveness is separate from
   model readiness; these endpoints do not start services or download models.
2. Submit plain text through `POST /rulebooks/extractions`, poll the job, then
   follow `result.href` to its saved draft. Manual rulebook creation is also
   specified. Other guideline input formats are deferred.
3. Show source sentences, descriptions and parameters. Save reviewed rules with
   `PUT /rulebooks/{rulebook_id}`. The example saves version 2 with confirmed
   individual rules while the book stays draft. Confirm the book separately.
   Confirmed versions are frozen; later edits start a new draft version.
4. Upload a dataset ZIP through `POST /datasets`, poll import, then inspect sample
   integrity. The first adapter is `penn_fudan`, not a universal specification.
5. Start an audit using a confirmed rulebook ID/version. Null `sample_ids` means
   all samples. The backend snapshots annotation revisions and selects batch
   size from measured CPU resources. An optional active feedback policy is pinned.
6. Poll the job, list the complete review queue and load sample detail. Omit queue
   filters to include apparently correct cases. Findings include pass outcomes.
7. Resolve findings and record sample decisions separately. Correct boxes, labels
   and masks through a new annotation revision while preserving originals.
8. Recheck corrected revisions. This creates a child audit, retaining old evidence
   and requiring a new reviewer decision. Export the reviewed child audit.
9. Prepare an export, poll its job, then download the ZIP and JSON manifest.
10. Inspect approved correction examples, create an example-retrieval policy,
    freeze reviewer-verified held-out ground truth, evaluate baseline/candidate,
    then explicitly activate the evaluated policy for later audits.

Every operation has `x-implementation-status: planned` and a backing-service
note. Current Python measurements, rule records and Ollama integration exist;
HTTP routes, storage, confirmed-rule execution, jobs, model checks, review,
correction/export and evaluated feedback services remain planned.

## Requests, responses and jobs

Single-resource endpoints return the resource directly. Paginated lists return:

```json
{"items": [], "limit": 20, "offset": 0, "total": 0, "next_offset": null}
```

Limit is 1–100, default 20. Offset starts at 0. Null next offset means no more
pages. During processing, counts refer to currently available records and can
change. Query parameters and example filtered results agree.

Long-running operations return 202 and a job, sometimes alongside the new
resource. Jobs use `queued`, `running`, `cancelling`, `completed`, `failed`,
`cancelled`. Stop polling at the final three states. Unknown progress has null
total/fraction. Completed jobs provide `result.href`. A failed job remains a
200 response from its GET endpoint, with a non-null error.

Errors share this envelope:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "One or more request fields are invalid.",
    "details": [{"field": "/rulebook_version", "message": "Expected a positive integer."}],
    "request_id": "request_demo"
  }
}
```

Use codes for handling and messages/details for display. The HTTP adapter will
map existing client reasons to uppercase codes such as `MODEL_UNAVAILABLE`,
`REQUEST_TIMEOUT`, `INVALID_RULE_PROPOSALS`. Validation uses 422, missing records
404, stale revisions and invalid workflow transitions 409. Domain-specific
conflict codes are described on their operations; generic error examples show
the shared envelope rather than every possible domain failure.

Use `expected_version` for rulebook writes and `expected_revision` for annotations
or reviews. Refresh after a 409 before deciding whether to reapply changes.
Review revisions start at 0; annotation and rulebook versions start at 1.

Dataset ZIP and mask PNG uploads use multipart FormData. Fixture strings like
`<binary dataset ZIP>` represent file parts, not literal JSON bodies. Let the
browser set multipart Content-Type and its boundary. Asset and archive downloads
return bytes, not JSON wrappers; unavailable files return the JSON error envelope.

## Review state

Rule confirmation is `draft`, `confirmed`, `rejected`; origin is `extracted` or
`reviewer`. Keep rejected rule history, but remove rejected rules from a book
before confirming its active version. Unsupported requirements remain visible.

Finding outcomes are `pass`, `fail`, `unsure`, `unavailable`. Resolutions are
`pending`, `accepted`, `rejected`, `unsure`. **Accepting a finding confirms the
tool's conclusion; it does not accept the sample.** Rejecting dismisses the
finding. Current sample decisions use `pending_review`, `needs_correction`,
`accepted`, `rejected`. No tool confidence automatically changes those decisions.
`reviewer_id` records attribution for the local prototype, not an implemented
authentication system.

The audit detail's raw measurements preserve their original `pending_review`
field. Render the separate current review record for actual reviewer decisions.
Each measurement check has status, reason, detail, result. `completed` means the
measurement ran, not that data passed. Unavailable results can be null or contain
low-level evidence. Null fractions mean unavailable, never zero.

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

For mask corrections, stage the PNG first, then submit its upload ID with the
complete replacement object list. Add/remove objects by adding/removing entries.
Commit objects and mask atomically after checking IDs, labels and geometry.
Staged masks expire; expired uploads must be repeated. Old audit evidence and
final decisions do not automatically carry forward to corrected data.

Exports use pinned reviewed revisions and preserve source coordinates. The
example uses `accepted_only`; `all_reviewed` explicitly includes accepted and
rejected data with decisions in the manifest. Pending and needs-correction data
remain excluded. Stale decisions do not authorize changed data. Images are only
included when requested. No actual demo PNG or ZIP files are supplied here.

## Feedback boundary

The proposed strategy retrieves approved examples as context for supported model
checks. It does not change exact label membership rules, train/download a model
or activate a policy automatically. Example feedback comes from a separate
posture-review dataset, not the label-only demo.

Ground truth freezes reviewer-verified findings, corrections, apparently correct
cases and missed errors identified by reviewers. An actual scoring/matching
implementation is still needed. Compare baseline and candidate on identical
held-out samples and reject overlap with policy example samples. Undefined metric
denominators return null. Storage alone does not fulfil evaluated improvement.

## Frontend fixtures

Load `examples.json` into your mock/test layer:

```javascript
const template = examples.operations.find(
  operation => operation.operation_id === "getAuditSample"
);
const responseBody = structuredClone(template.response.body);
```

Use `examples.review_scenarios.flagged`, `.no_issue`, `.unsure`, `.unavailable`
to develop review states. Their matching confirmed rulebooks are under
`examples.review_scenario_rulebooks` with the same keys; the uncertain and
unavailable cases include their additional reviewer-authored rules.
Fixtures are independent lifecycle snapshots, not a
mutable mock server. Import OpenAPI into locally running tooling for schema
inspection or code generation. Keep binary downloads mocked separately.
