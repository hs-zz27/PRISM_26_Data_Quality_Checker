# Plain-text extraction jobs

This checkpoint adds source implementation for `POST /api/v1/rulebooks/extractions`
(`extractRulebook`). Apply the reviewed source changes to the real checkout before
advertising availability. Runtime verification and regeneration of the five API
artifacts remain operator tasks; this source-only handoff has not run them.

## Submission and review

Send exactly `{"guideline_text": "Allowed labels are person."}`. The required
field is a strict, nonblank string. Whitespace, newlines and Unicode are preserved;
null, numbers, booleans and all extra fields are rejected. There is no truncation
or arbitrary guideline-character/token equivalence. Client-selected IDs, models,
hosts, retry settings, reviewer metadata and rules are not accepted.

The response is HTTP 202 with the existing public queued Job only after the job
and its private input ledger commit together. No model call, placeholder rulebook,
background task, readiness probe or worker startup occurs in the request. 202 does
not promise that a worker/model is online. No authentication is added; this is a
local prototype and must not be exposed publicly without separate access controls.

Poll `GET /api/v1/jobs/{job_id}`. Queued, running, cancelling, completed, failed and
cancelled states are all HTTP 200. A failed poll does not prove processing failed.
Follow `result.href` only for completion. It pins the initial output:

```json
{
  "kind": "rulebook",
  "id": "rulebook_example",
  "version": 1,
  "href": "/api/v1/rulebooks/rulebook_example?version=1"
}
```

A successful extraction publishes exactly one version-1 draft. Every rule has
extracted origin, draft state and an exact source citation. A valid empty proposal
list produces an empty draft, not an error. Crashes, EOF and malformed responses
are failures, never silently empty output. Initial review history is empty. Use
existing edit/confirm/reject/revision APIs; later versions do not change the pin.
Extraction/citations are proposals, not evidence of semantic correctness.

The extraction prompt distinguishes writing a visual checking rule from evaluating
an image: no image is required to extract a visual requirement. Nonblank source
lines receive request-local IDs; blank lines remain in the saved guideline text.
A one-line paragraph can support more than one check mapping. The model returns
only requirement/context references, a catalog check ID and parameters. It does
not regenerate descriptions, citations or rule IDs. The backend copies the
referenced requirement into `source_sentence`, includes its selected context lines
in `description`, assigns unique rule IDs and validates exact check parameters.
Unknown, repeated or malformed references and duplicate mappings are rejected.
Unsupported requirements use check ID 0 with empty parameters and are retained.

The generation schema has one compact rule shape with typed parameter values.
The executable registry independently checks each selected check's required and
forbidden parameters. Scope/context selection, requirement coverage and check
mapping remain model proposals requiring reviewer inspection; copying source text
does not establish correct interpretation. The full-model CPU run must be verified
after deployment. Mocked tests do not establish its quality or latency.

### Owner-run observations and remaining work (31 October 2026)

With `qwen3-vl:4b-instruct` on CPU, the owner reported a four-line example
completing in 58.9 seconds with the correct visual label-matching rule, attached
class definitions and a separate allowed-label vocabulary rule. A subsequent
local run of `evaluation/annotation_error_pack/guidelines.txt` completed in
64.8 seconds and returned 12 proposals. These are individual observations, not
a latency benchmark or an end-to-end API/audit validation.

The full draft is not yet semantically ready for confirmation:

- Scope and class definitions were emitted as standalone visual checks.
- Most checks did not retain applicable scope, definitions and exceptions as
  context. The audit prompt sends one target rule, not the entire rulebook.
- The exact 3-pixel tolerance was assigned to `annotation_guideline`, although
  that check is not a numeric measurement fallback. Retain this requirement as
  unsupported unless an appropriate measurement check exists; its reviewer
  approval condition must also remain visible.
- The uncertainty instruction was emitted as a separate rule rather than
  being attached to the affected checks.

Preserving every source line does not prove that every executable rule has the
right meaning. Follow-up must verify context propagation through the actual
audit payload, unsupported-rule handling and reviewer visibility. Automated
tests for this change were not run by the assistant; the reported local model
runs do not establish that the test suite passes.

Cancel with the existing `POST /api/v1/jobs/{job_id}/cancel` route, no body.
Queued jobs cancel immediately; running jobs enter cancelling, then the supervisor
stops the local child and acknowledges at a checked boundary. Repeated cancelling
requests are allowed. Terminal states keep 409 `JOB_ALREADY_FINISHED`. Cancellation
committed first wins under the job-row lock; completion committed first stays final.
Cancelled jobs have no result and publish no new draft. Audit dispatch is unchanged.

## Operator setup: separate processes

Use the real project's existing environment. Do not install models or start
services from imports, application startup, schema generation or request handlers.
PostgreSQL, Ollama and the installed local model are operator-managed prerequisites.
Apply migrations explicitly (009 at the supplied baseline), before serving requests:

```bash
.venv/bin/python -B -m backend.app.storage.database
```

Run the HTTP server separately using the existing launch command:

```bash
.venv/bin/python -B -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Run an explicit worker in another terminal/process:

```bash
.venv/bin/python -B -m backend.app.workers.rule_extraction --once
.venv/bin/python -B -m backend.app.workers.rule_extraction
```

Supported options are `--once` and argparse's `-h`/`--help`. `--once` recovers one
bounded stale batch, handles at most one queued extraction and exits 0 when none
exists. Loop mode polls an empty queue once per second. SIGINT/SIGTERM requests
shutdown; active local children are terminated/joined, with kill escalation after
a two-second grace. Storage/configuration/unresolved-outcome failures stop the
worker with exit code 1 and a constant, secret-free operator message.

The supervisor's nonblocking Pipe reader requires Linux/POSIX file descriptors.
It accepts one finite JSON frame, not pickled output, and rejects oversized,
truncated, duplicate-key and nonfinite envelopes. A 64-MiB serialized result guard
bounds parent memory; it is NOT an input-text or model-token-capacity limit. Large
frames are drained while the child is alive, before joining. Child entry points
and test fakes are top-level importable functions, compatible with spawn.

## Saved execution policy and model configuration

The server snapshots validated effective Ollama settings and execution policy at
acceptance. Existing Ollama configuration selects model, local host and generation
settings. Workers use the saved snapshot, not subsequently edited configuration.
Loopback, CPU, disabled proxy/redirect and local metadata verification policies
remain those of the existing client. There is no download/startup or readiness claim.

Default private policy: 900-second processing deadline, 30-second lease,
0.2-second poll interval, 5-second renewal cadence. The validated server-only
`ExtractionPolicy` permits finite execution limits up to 86400 seconds and requires
poll <= renew < half the lease. It is not exposed through HTTP/worker CLI. The
processing start and deadline are persisted once and never reset. Changing defaults
affects new acceptances only; it does not change queued/running saved policies.

The 900-second job budget accommodates the shipped 600-second Ollama HTTP timeout
plus setup and publication overhead on CPU. It is still a hard whole-job limit,
not a guarantee that inference will complete. The HTTP timeout and model output
limits remain independent. Rebuild the API and extraction worker after this
change; an already failed job is not resumed. A newly submitted extraction gets
the new policy, while previously accepted jobs retain their saved limits.

The parent owns claims and all database writes; the child gets only text,
serialized settings and reserved output ID. No connection, repository or live
HTTP client is passed to spawn. No heartbeat thread can renew around a hung call.
Renewal is forbidden at/after the persisted deadline. A separate monotonic ceiling
prevents local wall-clock changes extending supervision. PostgreSQL's fresh
`clock_timestamp()` is authoritative for ownership and publication. Socket timeouts
are not treated as the whole-operation deadline. Existing database connect,
statement and lock timeouts bound storage waits separately; scheduling/storage
waits can delay local termination, so this is not a real-time process-kill guarantee.

A lease may extend beyond the processing deadline only to let a still-live owner
record timeout. An expired owner cannot record any error/cancellation; dedicated
recovery performs that transition. Stale running attempts fail as interrupted or
timeout, never requeue. Stale cancelling attempts become cancelled. Queued jobs and
inputs survive restart. Generic job execution/claim/recovery paths cannot act on
the managed extraction kind. Extraction-specific queries never act on audits.

Stopping the local child does not prove that the separately managed Ollama server
immediately stopped its own generation. No late result can be published after
cancellation, ownership loss or processing expiry.

## Safe asynchronous errors

After 202, errors belong in `job.error`, not a retrospective 422/503 submission
response. Only fixed codes/messages are persisted; raw model output, exception
messages, guidelines, settings and credentials are not job errors or routine logs.

| Failure | Code |
| --- | --- |
| Missing model | `MODEL_UNAVAILABLE` |
| Unreachable local server | `OLLAMA_UNAVAILABLE` |
| Client request timeout | `MODEL_REQUEST_TIMEOUT` |
| Invalid proposals, incomplete generation, invalid citation/state/mapping | `INVALID_RULE_PROPOSALS` |
| API/protocol JSON, HTTP request failure, unverifiable model metadata | `MODEL_PROTOCOL_ERROR` |
| Remote-model rejection or invalid saved model configuration | `MODEL_CONFIGURATION_ERROR` |
| Whole-processing deadline | `EXTRACTION_TIMEOUT` |
| Stale/interrupted single attempt or operator shutdown | `EXTRACTION_INTERRUPTED` |
| Reserved output identifier already occupied | `EXTRACTION_OUTPUT_CONFLICT` |
| Other execution defects, missing/malformed/oversized child payload | `EXTRACTION_FAILED` |

Immediate request validation is safe 422 `VALIDATION_ERROR`; configuration/storage
acceptance failure is safe 503 `DEPENDENCY_UNAVAILABLE`. No global arbitrary
ValueError/Pydantic-to-422 mapping is introduced. Unknown programming/response
validation defects retain existing 500 semantics.

## Transaction and reconciliation boundary

Lock ordering is jobs row, then private request row, then the existing per-book
advisory lock. Every operation compares the authoritative request/resource,
worker/token, status, attempt=1 and live lease. Publication additionally revalidates
ID/version/text, citations, unique rules, finite parameters, registry mappings,
extracted origin and draft states. Fresh time is sampled after the advisory lock
and again after insertion. A late insertion rolls back rather than publishing
under an expired authorization. All publication checks occur on one connection;
no network/model operation occurs within that transaction.

The reserved ID must have no existing versions. A collision safely fails the job,
leaving the existing book/history unchanged; no overwrite, version-2 fallback or
mid-job replacement ID is allowed. Draft insertion and completed Job/result commit
together. Do not substitute the synchronous extract-and-save API plus generic
complete-job: their separate transactions cannot provide this boundary.

A lost final commit acknowledgement is unknown, not proof of rollback. The
supervisor reads back the public job and original pinned validated draft; a
completed pair is returned unchanged. Nonterminal/unavailable/inconsistent
readback stops as unresolved. It does not fail the completed job or replay the
model. A known rollback likewise never leaves a successful job or orphan draft.
If failure persistence is unavailable, the worker stops; stale recovery resolves
the durable attempt later without inference.

Every successful new POST allocates a new job and reserved output ID. No automatic
request/model retries or idempotency keys are provided. A submission 503 caused by
an unknown commit may still have accepted a job; blind client retries can create
a second independent job. Surface that uncertainty to the operator instead of
claiming rollback or exactly-once inference.

## Frontend fixtures and generation

`examples.extraction_job_scenarios.states` contains all six delivered extraction
states; `.failures` contains every safe error code above. Future producers remain
under `examples.future_job_scenarios`: import, recheck, export, feedback evaluation.
Fixtures are independent synthetic snapshots, not runtime or accuracy evidence.

The source operation inventory is 49 total, 20 implemented and 29 planned after
integration. Preserve the existing `extractRulebook` path/ID and route-context
duplicate-registration tests. Schemas come from the real strict request model.
Combined paths omit `/api/v1` with it in server.url; live-only paths include the
prefix with an origin-only server.url. Do not double-prefix result links.

Regenerate in the complete checkout, never hand-edit these five artifacts:

```bash
.venv/bin/python -B -m backend.api.build_contract
```

- `backend/api/openapi.json`
- `backend/api/implemented-openapi.json`
- `backend/api/examples.json`
- `backend/api/ENDPOINTS.md`
- `backend/api/PLANNED_ENDPOINTS.md`

Only source/syntax checks were possible in this handoff. Dependencies/imports,
artifact freshness and runtime/PG/HTTP/model correctness must be verified in the
real project. Authored tests are not evidence of passing tests or production readiness.
