# Dataset import and catalogue HTTP handoff

## Delivery status and application prerequisites

Feature 4 Phases 1–4 are applied in this workspace as of 21 October 2026.
The five contract artifacts have been generated with the installed dependencies.
The developer's latest suite reported 39 failures, 2,021 passes and two errors;
tests have not been rerun after integration corrections. Migration application
and real import/export execution remain separate verification gates. The source baseline is PRISM commit
`071f3fcf9c16debb95f1308c546bfb3e115204ff` (merged CPU detection).
Never overwrite newer local work with a snapshot copy.

Phase 4 source patches promote the five existing declarations, derive public
schemas from Pydantic, align operations with live FastAPI and add independent
synthetic lifecycle examples. No sixth route or detector/evaluator change is added.
The generated inventory is 50 operations: 32 implemented and 18 planned.
Audit creation/listing and reviewed exports are implemented; see AUDITS.md and
EXPORTS.md. Corrections, rechecks and evaluated feedback remain planned.
The catalogue revision resolver deliberately has no fabricated live source_root;
audit submission now resolves its exact manifest-only pins directly.

The live OpenAPI adapter preserves exact integer bounds and nullable defaults
from the pinned FastAPI/Pydantic schema generation. This avoids rounding the
PostgreSQL BIGINT maximum or omitting `default: null` during framework encoding.
Contract generation does not establish runtime correctness. Regenerate all five
artifacts together after changes to API models or routes.

Requirements for another checkout:

- Complete Python 3.12 checkout and its existing virtual environment with root
  requirements, including Phase 3's explicit `python-multipart==0.0.32` declaration.
  Dependency installation is an operator prerequisite, not performed by this handoff.
- Reviewed Phase 1 model/catalogue migration 010 and repositories; Phase 2 durable
  acceptance/worker and workflow managed-kind/cancellation patch; Phase 3 router,
  parser/ingress, narrow error mappings and application registration patches.
- Corrected Phase 2 acknowledgement regression: finite spawned-producer cleanup,
  not the original non-daemon thread. Use the corrected source bundle/patch.
- Exact source hashes before mechanical application; an existing/newer target or
  unmatched patch anchor must stop application for review. Preserve all unrelated edits.

## Exactly five dataset operations

Paths below already include `/api/v1`.

| Method | Path | Operation ID | Success |
| --- | --- | --- | --- |
| POST | `/api/v1/datasets` | `importDataset` | 202 `DatasetImportStarted` |
| GET | `/api/v1/datasets` | `listDatasets` | 200 `DatasetPage` |
| GET | `/api/v1/datasets/{dataset_id}` | `getDataset` | 200 `Dataset` |
| GET | `/api/v1/datasets/{dataset_id}/samples` | `listDatasetSamples` | 200 `SamplePage` |
| GET | `/api/v1/datasets/{dataset_id}/samples/{sample_id}` | `getDatasetSample` | 200 `Sample` |

POST accepts exactly one file part `archive`, one nonblank scalar `name` (up to
255 characters), and one scalar `format=penn_fudan`. All are required. Unknown,
duplicate, malformed or wrongly typed parts/query fields are rejected. Clients do
not choose IDs, revision, policy, roots, status, claims or storage identities.
Use browser FormData and let the browser add the boundary. The fixture's binary
marker is not file content. Content-Type/extension alone never prove valid ZIP data.

Acceptance streams, measures, closes and records private staging identity, then
commits dataset/import job/private request together. Return 202 only after commit
acknowledgement. It is not import completion or proof a worker is running.
A known rollback only cleans the submission's unaccepted files; an unknown commit
retains possibly referenced files, returns a safe unresolved dependency failure,
and requires operator readback, not automatic submission replay.

Reads use stored metadata and exact manifest identity, never image decoding,
source capture, live directory fallback, progress mutation or implicit import.
List limit is 1–100 (default 20); offset is nonnegative (default 0, bounded to signed
64-bit). IDs are nonblank, non-NUL and up to 255 characters. Ordering is stable
with a unique tie-breaker; total and items use the same database snapshot selection.
No filters are promised for the dataset lists.

Omit detail revision to select the catalogue's explicit current head. A supplied
revision must be positive and at most 2147483647. Never select MAX(manifest revision).
Historical reads stay pinned; missing/corrupt pinned manifests fail closed instead
of recapturing mutable files. Existing unrelated legacy manifests/audits are not
adopted or backfilled into the catalogue. Each accepted upload creates a new ID.

Assets reuse the existing revision-required route and quoting conventions.
Readable malformed source bytes may have raw links; thumbnails/mask previews can
subsequently fail with the existing safe render error. No link is a guarantee of
decodability. Missing/unreadable source roles have null links. Raw masks retain
palette/16-bit IDs and exact bytes; previews are display-only. The existing asset
route independently enforces its 64 MiB read and 16-million-pixel preview limits.

## Dirty source, counts and partial evidence

Ready means a coherent imported catalogue and captured assets suitable for audit;
it does not mean clean annotations, successful checks, reviewer acceptance or model
confidence. Missing masks, orphan annotation/mask roles, duplicate/negative object
IDs, invalid boxes, malformed readable text and corrupt readable PNGs remain sample
evidence. The importer does not run the full audit or impose labels/quality thresholds.

Sample issues are safe allowlisted ErrorDetail records, not exception text or paths.
Objects preserve source order, duplicate IDs and invalid coordinate values. The
transport can retain negative IDs/empty labels, but that does not expand the current
Penn-Fudan parser regex: unparseable source remains exact captured bytes plus issues,
not invented parsed objects. The invalid_objects fixture uses parser-recognizable
duplicate zero IDs and invalid boxes; wider transport permissiveness has a separate
model-boundary regression. Empty
objects do not establish zero objects: explicit `Objects with ground truth : 0 {}`
evidence is distinct from an unusable annotation. The sample transport does not
expose raw private annotation storage; the separate synthetic annotation_evidence
fixture is explanatory reference data, not an added response property.

sample_count is null before deterministic discovery is committed. It then describes
the discovered selection, not necessarily the number of published rows during a
partial import. A sample page total counts published heads and can therefore be
smaller. object_count remains null until a complete aggregate can be established;
any unknown annotation count keeps the completed dataset aggregate unknown.
Do not display null as zero or silently sum only good annotations.

Completed sample revisions remain immutable and browsable after later failure or
cancellation. An existing dataset with no published samples has a valid empty
page, including while importing/invalid/failed/cancelled. A missing dataset is 404.
Partial evidence and progress never imply ready. Published files are never removed
as generic cancellation cleanup.

## Job lifecycle and cancellation

Polling and cancellation reuse `/api/v1/jobs/{job_id}` and
`POST /api/v1/jobs/{job_id}/cancel`; no duplicate dataset-job endpoint exists.
Polling persisted failed jobs returns HTTP 200 with structured job.error.
Cancellation returns 202 with Job; terminal cancellation requests retain the
existing 409 JOB_ALREADY_FINISHED behavior, not a new dataset-route conflict.

| Job | Dataset | Meaning |
| --- | --- | --- |
| queued | importing | Durable request; unknown total/fraction before discovery |
| running | importing | One bounded attempt; only completed publication boundaries advance progress |
| cancelling | importing | Cancellation requested; worker stops at a safe boundary |
| completed | ready | Catalogue finalization and job result committed together |
| failed | invalid | Inspected unusable archive/layout/identity structure |
| failed | failed | Storage/resource/deadline/interruption/internal processing failure |
| cancelled | cancelled | Explicit cancellation won; published partial evidence remains |

Queued cancellation couples dataset/job terminal state atomically. Running
cancellation first marks cancelling; the worker/recovery finalizes cancellation.
Job row is locked before request/dataset and sample publication rows; database
time is sampled after blocking locks. A cancelled, foreign, expired or replaced
claim cannot publish manifests/heads/progress or complete a job. Generic job
execution/recovery must not bypass the managed dataset_import repository.
Ready plus completed result share one SQL transaction, but filesystem capture and
SQL are not one atomic filesystem/database transaction.

Completed result has kind dataset, dataset ID, version null, and relative href
`/api/v1/datasets/{dataset_id}`. No fabricated audit/review result is returned.
Queued work survives HTTP/worker restart. Interrupted/expired running work fails
(or cancels if cancellation won), never automatically requeues/resumes. Submit a
new archive deliberately after reconciliation; do not blindly retry unknown commits.

Safe asynchronous failure allowlist: INVALID_ARCHIVE, INVALID_LAYOUT,
IMPORT_RESOURCE_LIMIT, IMPORT_STORAGE_FAILED, IMPORT_TIMEOUT, IMPORT_INTERRUPTED,
IMPORT_FAILED. These populate dataset/job state after acceptance, not fabricated
synchronous HTTP validation errors. No arbitrary exception or private identity is
included in a public message.

## HTTP errors

All JSON errors reuse ErrorResponse, ApiError, ErrorDetail and generated request IDs.

- 404 NOT_FOUND: missing dataset/sample/exact revision. Collection POST and dataset
  list do not invent a 404; child sample lists require an existing dataset.
- 422 VALIDATION_ERROR: invalid multipart/scalar/ID/query/revision/pagination fields.
- 413 UPLOAD_TOO_LARGE: measured ingress or archive byte policy exceeded.
- 415 UNSUPPORTED_MEDIA_TYPE: unsupported multipart/upload representation.
- 503 DEPENDENCY_UNAVAILABLE: narrow staging/configuration/storage/unknown acceptance
  outcome. No accepted job is falsely returned and no automatic replay is attempted.
- 500 INTERNAL_ERROR: unexpected defects, internal validation or response failures.
  Do not globally turn arbitrary ValueError/Pydantic errors into client 422.

## Layout and resource policies

Supported roles are PNGImages/id.png, Annotation/id.txt and PedMasks/id_mask.png,
at root or under one common wrapper. Directory entries may be absent. Discovery
uses the union of role identities, not only images. No recursive root guessing or
multiple competing wrappers. The adapter rejects path traversal/absolute/drive/UNC
names, backslash/NUL aliases, case/Unicode collisions, duplicate logical entries,
links/special files, encryption and unsupported compression. It checks actual
streamed sizes/CRC, not metadata alone, and never uses unchecked extractall.
Narrow metadata/documentation ignoring does not exempt entry identity validation.
Unsupported harmless role extras are reported, not interpreted as extra samples.

Server policies are resource defaults, not dataset quality requirements:

| ImportPolicy field | Development default |
| --- | --- |
| max_archive_bytes | 512 MiB |
| max_entries | 10,000 |
| max_file_bytes | 64 MiB |
| max_expanded_bytes | 2 GiB |
| max_annotation_bytes | 2 MiB |
| max_image_pixels | 16,000,000 |
| max_samples | 3,000 |
| max_objects_per_sample | 10,000 |
| max_result_bytes | 16 MiB |
| max_seconds | 900 seconds |
| lease_seconds / renew_seconds / poll_seconds | 30 / 5 / 0.2 seconds |

Root .env is loaded literally, then process environment takes precedence.
Use DATASET_IMPORT_ plus each uppercased policy field for overrides. Private
DATASET_IMPORT_ROOT and ASSET_STORAGE_ROOT must be absolute and disjoint; defaults
are backend/data/imports and backend/data/assets resolved from the project.
Settings imports/reads create no directories, connect, migrate or spawn workers.
Do not expose actual private roots, archive keys, hashes or claim/connection data.

Phase 3 multipart policies use DATASET_HTTP_OVERHEAD_BYTES (65536), SCALAR_BYTES
(1024), PART_HEADER_BYTES (8192) and SPOOL_BYTES (1048576), with the DATASET_HTTP_
prefix on each. Request body limit is archive cap plus overhead; the scoped ASGI
guard counts bytes before parsing, with separate file/text/header checks. A
Content-Length hint alone is not authority. Starlette spooling uses temporary
storage before durable acceptance: provide a private TMPDIR, disk/process quotas
and server/proxy request/header caps plus finite read/idle/total-upload timeouts.
The guard cannot undo already allocated ASGI chunks or bound global concurrency.

## Operator commands — not executed by this handoff

Use the complete checkout and configured private environment. Commands here are
instructions for the developer, not permission for a local AI to run tests,
migrations, services, installation or inference automatically.

After reviewing/applying source and configuring PostgreSQL, the operator may apply
the additive migration and start the explicit worker and HTTP server separately:

```bash
.venv/bin/python -B -m backend.app.storage.database
.venv/bin/python -B -m backend.app.workers.dataset_import --once
# Polling worker alternative (not in app startup):
.venv/bin/python -B -m backend.app.workers.dataset_import
# HTTP server, in a separate terminal:
.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Contract generation requires complete imported modules and declared dependencies,
not a running worker/Ollama/database connection. The existing generator creates
tiny synthetic measurement fixtures; no model inference or real dataset is needed.
After prerequisites are available, regenerate rather than editing generated text:

```bash
.venv/bin/python -B -m backend.api.build_contract
```

All three outputs must be freshly produced from the same reviewed source:
openapi.json, examples.json and ENDPOINTS.md. If imports/schema alignment or inventory checks fail, stop;
do not weaken constraints, alter assertions or publish old artifacts as fresh.
All documents are rendered before the existing writer starts, but the three file
writes are not a single atomic multi-file commit; a write failure requires a full
regeneration and freshness verification before client handoff.

Only when the developer separately authorizes test execution:

```bash
.venv/bin/python -m pytest backend/tests/test_dataset_contract.py backend/tests/test_api_contract.py backend/tests/test_rulebook_contract.py backend/tests/test_planned_api_contract.py -q
```

Also run the authored Phase 1–3 model, repository, migration, ZIP/import,
managed-kind/cancellation and multipart/HTTP regression groups, plus unchanged
existing routes. PostgreSQL groups use the existing isolated opt-in fixture and
must never point cleanup at application tables. The developer ran the suite before
these corrections; no rerun has occurred. Source/schema verification and regenerated
artifacts do not establish runtime correctness. Migration application and real
worker/HTTP verification remain separate operator-controlled steps.

## Frontend fixtures and structural checks

examples.dataset_import_scenarios contains started, states (all six Job states),
invalid, failures, queued_cancelled, samples, partial_sample and illustrative
annotation_evidence. They are independent synthetic snapshots validated against
public models during generation, not a fake stateful server or measured results.
The six sample examples are zero_object, annotation_only, missing_mask,
corrupt_image, malformed_annotation and invalid_objects. A completed dirty dataset
can legitimately have object_count null. Preview availability is not renderability.

future_job_scenarios now contains only recheck and feedback_evaluation. Delivered
exports have separate export_scenarios.
Delivered extraction scenarios and unrelated mocks remain unchanged.
Dataset/Sample schemas come from real Pydantic records; ImportedAnnotationObject
retains permissive source values while future CorrectionObject/CorrectionBoundingBox
keep positive correction constraints. No shared component is silently overwritten
with a conflicting live definition. Five operations copy actual live shapes, not
handwritten approximations of defaults/nullability/bounds/statuses/media.

Authored tests compare combined/implemented/live requests, responses and reachable
schemas; they retain validation constraints and normalize descriptions/examples
only. Duplicate included-router registration is checked with iter_route_contexts,
not flat app.routes assumptions. All five artifacts must match render_artifacts
exactly. These are authored regressions; the corrected integration has no fresh
passing suite result.

## Remaining limits

This is an unauthenticated trusted-local POSIX-storage prototype, not production
certification. No auth project, multi-tenant admission control, global resource
scheduler, distributed storage, automatic import retries/resume or garbage collector
is added. Killed children or unknown SQL commit outcomes may leave filesystem-only
staging/snapshot leftovers. Reconcile ownership before narrowly cleaning any file;
never delete registered evidence or another request's files. Parser/process/DB
compatibility and all end-to-end race behavior require developer-run verification.
Three static review iterations do not prove zero technical/logical gaps.
