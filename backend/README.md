# Backend

This folder contains the dataset reader, box and mask measurements, local Ollama
client, rulebook extraction and review, batch audits, and finding/sample review backed
by PostgreSQL. The remaining modules contain responsibility notes for later
implementation.

See the project tracker for completed work, teammate handoffs,
verification status and the recommended next features.

## Dataset import checkpoint

Feature 4 adds durable Penn-Fudan ZIP acceptance, an explicit single-attempt
worker, and four catalogue reads. Phases 1–4 are applied and the five API artifacts
are regenerated. The user reports the pre-correction baseline passed. Reported
correction/recheck failures exposed stale contracts, missed route-inventory updates
and serialization before strict model revalidation. Those issues are corrected and
all five contracts are regenerated; tests have not been rerun after these fixes.
Migration application and real import/export execution remain separate gates.
See api/DATASETS.md for setup, statuses, nullable counts, revision
authority, partial evidence, limits and known local-prototype boundaries.
Ready does not mean clean annotations. Audit creation and listing now connect the
catalogue to the existing batch engine. Annotation/mask corrections, revision history and linked rechecks are implemented
in source after this handoff is applied; see api/CORRECTIONS.md. Reviewed dataset exports are implemented in source and preserve immutable
audited revisions and frozen human sample decisions; they do not require corrections
or rechecks. Their five contract artifacts are regenerated. The corrected export
workflow still requires a developer-run test pass and runtime verification.

## Create and process audits

Submit a ready dataset and an explicit confirmed rulebook version to
`POST /api/v1/audits`. It returns HTTP 202 with the queued audit and job. Omit
`sample_ids` or send null for all samples; a unique nonempty list selects a subset.
`GET /api/v1/audits` lists public lifecycle summaries, newest first.

Run the explicit worker in a separate terminal:

```bash
.venv/bin/python -B -m backend.app.workers.audit
```

Use `--once` to process at most one eligible audit. The worker uses the existing
supervised batch engine and only claims audit jobs. It shares the configured
PostgreSQL database and managed asset storage with the API/import worker.
Ordinary audit submission retains its existing schema requirements; linked rechecks
and correction publication require additive migration 012 after 001–011.
Updated audit/generic worker guards also require 012 before any queue scanning;
coordinate source/schema deployment before restarting services.

See [api/AUDITS.md](api/AUDITS.md) for pins, errors, cancellation and frontend
integration. Tests are authored but have not been run during this implementation.

## Structure

```text
backend/
  app/
    main.py                  Application entry point
    core/                    Configuration and local paths
    models/                  Structured records for data, rules and review
    datasets/                Dataset and annotation readers
    checks/                  Measurements and rule evaluation
    inference/               Local Ollama and CPU detector integration
    services/                Workflow coordination
    storage/                 Persistence of rules, evidence and corrections
    routers/                 Implemented and future HTTP API endpoints
  tests/                     Backend tests added as features are implemented
```

FastAPI routers expose audit creation/listing/reads, revision-pinned asset delivery,
job status/cancellation, finding/sample review routes and saved-rulebook reads/reviewer actions.
Dataset export creation, listing, detail, manifest and download routes are implemented.
Read repositories compose stored evidence; services coordinate workflow changes. A
separate controller layer is not needed.

`models/` means Python data structures, such as an annotation or a finding. AI model integration belongs in `inference/`; downloaded weights remain outside the source code, currently in the project's `.cache/torch` folder.

## Dataset reading and sample audits

`app/datasets/penn_fudan.py` reads the existing root-level `PennFudanPed/` folder:

1. Load `FudanPed00001.png`, its annotation text and its mask from the existing root-level `PennFudanPed/` folder.
2. Parse person IDs, labels and box coordinates.
3. Check that the image and mask dimensions agree and that instance IDs match the annotations.

Checks use one-based, inclusive coordinates and preserve source box values.
`parse_annotation_metadata()` reads declared image dimensions, object count and
coordinate origin separately from the bounding boxes. Missing, malformed or
duplicate headers are reported per field. Metadata comparisons include the source
image's channel count before any RGB conversion. Box checks require a declared
origin of `(1, 1)`; other or missing origins make those checks unavailable.

An annotation with no parsed boxes must explicitly declare
`Objects with ground truth : 0 {}`. Empty files, unrecognized text and unusable
object-count declarations are parsing failures, not evidence of zero objects.
Label evaluation also requires the declared zero count, including when given
an older measurement report. Unrelated image or coordinate metadata failures
do not prevent label evaluation for a valid zero-object annotation.

`audit_sample()` loads each asset independently. A missing or corrupt mask leaves
label and box checks available; a missing annotation leaves the image/mask size
check available. Errors identify the failed input with `image_load_failed`,
`annotation_load_failed` or `mask_load_failed`. Failed loading, box parsing or mask
validation makes the report `incomplete`. Metadata issues stay in the
`annotation_metadata` result, alongside any comparisons that could be made.

`measure_instance_mask()` shares one instance-pixel extraction across bounds,
outside pixels, fill fraction and connected components. Standalone measurement
functions still validate their own inputs. Measurements consistently expose
`measurement_available` and `reason`; alignment no longer uses
`comparison_available`. A completed check means it ran, not that the data passed.
Annotation and measurement result shapes are explicitly typed Python records.

The mask stores IDs `0`, `1` and `2` for this example, so it looks nearly black in a normal image viewer. Use display colours for visualization while retaining the original mask IDs.

## Environment and files

Use the existing Python 3.12 environment. From the project root:

```bash
source .venv/bin/activate
```

From inside `backend/`:

```bash
source ../.venv/bin/activate
```

Dependencies are recorded in the root-level `requirements.txt`. The dataset remains at `../PennFudanPed/` when your terminal is inside this folder. Configuration should resolve paths from a known project location rather than depending on the terminal's current directory.

Follow `../SRS.md`: plain text guidelines first, CPU inference, reviewer-controlled final decisions, actual annotation corrections and evaluated use of feedback.

## Ollama configuration

`ollama.toml` contains the shared defaults for the model, local server address,
request timeout, context length and maximum output tokens. For your own machine,
set optional overrides in the root `.env` (see `.env.example`):

```dotenv
OLLAMA_MODEL=qwen3-vl:4b-instruct
OLLAMA_BASE_URL=http://127.0.0.1:11434
```

Leave those keys out to use the TOML defaults. The model must already be installed
and its name must match `ollama list`. Precedence is process environment, then
root `.env`, then TOML. Blank or invalid overrides are rejected. These values
are read directly; you do not need to export or source `.env`.

If port `11434` is occupied, start Ollama on a free port, for example:

```bash
OLLAMA_HOST=127.0.0.1:11435 ollama serve
```

Then set `OLLAMA_BASE_URL=http://127.0.0.1:11435` in `.env`. `OLLAMA_HOST` configures
the Ollama server; `OLLAMA_BASE_URL` tells this app where to contact it. Changing
the app URL alone does not move the server. Stop an existing Ollama server before
starting another if you intend to move it rather than run a separate instance.

`app/core/config.py` loads and validates these settings independently of the
terminal's working directory. `propose_rules()` reloads the files and environment
on each call, so subsequent requests use saved changes. Invalid settings fail
before an API request. CPU execution remains enforced by this local client.
Cloud AI is permitted by the user, but cloud provider support is not implemented.

`ASSET_STORAGE_ROOT` optionally selects the private absolute root for immutable
source-asset snapshots. It defaults to `backend/data/assets`, uses the same
process-environment-over-`.env` precedence, and is not created while loading
configuration. The local capture service stores only relative keys, content types,
sizes and SHA-256 identities in manifests; public API models do not expose local
paths.

`capture_sample()` in `app/services/asset_snapshot.py` accepts a trusted local
Penn-Fudan dataset directory and independently copies `PNGImages/<sample>.png`,
`PedMasks/<sample>_mask.png` and `Annotation/<sample>.txt` as exact bytes. Missing
or unreadable source files are recorded per asset, while readable malformed bytes
are retained for later audit validation. A manifest is published only after all
available copies finish. Filesystem storage and PostgreSQL publication are separate
operations; an uncertain database commit is not reported as filesystem/database
atomicity.

Captures of the same dataset/sample/revision serialize publication and cleanup
with a local filesystem lock, so a failed capture cannot delete another capture's
published files. Lock files under the private root's `.locks` directory are
retained to keep waiting processes on the same lock. This local storage backend
requires POSIX file locking, as provided by macOS and Linux; it is not a
distributed object-storage implementation. An uncertain manifest commit retains
its files so a later replay can reconcile the result safely.

`SamplePreviewService` in `app/services/asset_preview.py` renders display-only
PNGs from verified bytes for an explicit dataset, sample and revision:

```python
from backend.app.services.asset_preview import SamplePreviewService

previews = SamplePreviewService(manifest_repository)
thumbnail = previews.render("dataset_1", "sample_1", 2, "thumbnail")
mask_preview = previews.render("dataset_1", "sample_1", 2, "mask_preview")
# Each result provides content (PNG bytes), content_type, width and height.
```

Thumbnails preserve aspect ratio and transparency, fit within 256×256 pixels,
and never upscale. Mask previews retain the raw mask dimensions, make ID 0
transparent, and assign distinct, deterministic colors for all 16-bit IDs.
Palette indices and 16-bit IDs remain distinct inputs; raw mask bytes are never
converted or overwritten. Colors are display aids, not an encoding from which
to recover object IDs. Previews do not rotate source coordinates or retain
source metadata.

Rendering is in memory with a 64 MiB encoded-source limit and a
16-million-pixel limit. The repository enforces the byte budget before reading,
checks the opened file's size, and bounds the read to detect later growth.
Mask coloring uses a fixed lookup table instead of sorting all pixels. PNG
chunk checksums are verified before decoding. Corrupt PNGs,
animated PNGs and unsupported mask formats raise `PreviewUnavailableError`.
Missing manifests/assets, integrity failures and byte-limit failures retain
their repository errors (`AssetReadLimitError` for the source-byte limit).
There is no fallback to live dataset files. The implemented HTTP asset route
serves exact revision-pinned raw bytes or in-memory derived previews; it does
not expose private storage paths or mutate source files. Links are documented
below with the audit reads. Caching remains unimplemented.

For a one-off model selection, use `propose_rules(text, model="installed-model:tag")`.
To supply an alternate configuration file, pass
`settings=load_ollama_settings(path)`. To select a different environment file,
use `load_ollama_settings(path, env_path=environment_path)`. Import the loader from
`backend.app.core.config` when running from the project root. Per-call model,
host and timeout overrides take precedence over the supplied settings and are
validated without modifying them.

`propose_rules()` returns `RuleProposal` records containing the model's proposed
content. The client checks the response schema; `extract_rulebook()` assigns
origin and draft state and builds the `Rulebook`, which checks unique rule IDs
and whether citations occur in the original guidelines.

## Audit review API

The audit queue/detail reads, revision-pinned asset delivery, job status and
audit-aware cancellation, and finding/sample review routes are implemented with
FastAPI:

- `GET /api/v1/audits/{audit_id}/samples`
- `GET /api/v1/audits/{audit_id}/samples/{sample_id}`
- `GET /api/v1/datasets/{dataset_id}/samples/{sample_id}/assets/{asset_kind}?revision={revision}`
- `GET /api/v1/jobs/{job_id}`
- `POST /api/v1/jobs/{job_id}/cancel`
- `PUT /api/v1/findings/{finding_id}/review`
- `GET /api/v1/findings/{finding_id}/reviews`
- `PUT /api/v1/audits/{audit_id}/samples/{sample_id}/review`
- `GET /api/v1/audits/{audit_id}/samples/{sample_id}/reviews`

From the project root, with dependencies installed and PostgreSQL configured:

```bash
docker compose up -d --wait postgres
.venv/bin/python -B -m backend.app.storage.database
.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Existing environments need `fastapi==0.143.5` and `uvicorn==0.54.0`, now pinned
in root `requirements.txt`. The current schema includes migrations 007 and 008 for
sample manifests and snapshot audit associations; they do not backfill existing
audits. App imports
and startup do not run migrations, start workers or contact Ollama. `/docs` and
`/openapi.json` describe only the implemented routes. This remains a local
prototype without authentication.

Use an audit ID returned by `POST /api/v1/audits` or the Python batch service:

```bash
curl 'http://127.0.0.1:8000/api/v1/audits/AUDIT_ID/samples?limit=20&offset=0'
curl 'http://127.0.0.1:8000/api/v1/audits/AUDIT_ID/samples/SAMPLE_ID'
curl --output sample.png 'http://127.0.0.1:8000/api/v1/datasets/DATASET_ID/samples/SAMPLE_ID/assets/thumbnail?revision=2'
curl 'http://127.0.0.1:8000/api/v1/jobs/JOB_ID'
curl -X POST 'http://127.0.0.1:8000/api/v1/jobs/JOB_ID/cancel'
```

The queue includes every sample in original audit order. `limit` is 1–100
(default 20), `offset` is nonnegative (default 0). Optional `tool_status` and
`review_status` filters apply before pagination; `total` counts matching samples.
An existing audit with no matching results returns an empty page; a missing audit
or sample returns 404. Invalid query fields return 422. Errors use the documented
`error` envelope with a request ID; storage/configuration failures return 503 and
unexpected errors return 500 without exception internals.

Each summary separates `processing_status`, `incomplete`, `tool_status` and
`review_status`. Queued/running samples are `pending`; for other states, a failed
finding takes precedence (`flagged`), then `unsure`, then `unavailable`. Failed,
cancelled, incomplete or zero-findings samples cannot be `no_issue`. `no_issue`
requires completed processing and at least one finding, all passing. Counts always
describe original machine outcomes, even when reviewers disagree with them.

Detail reads combine original measurements, all findings with their latest
reviews, technical failures and the latest sample decision in one database
snapshot. Measurements are null when none were persisted; otherwise their stored
document retains its original `pending_review` marker. Loading-error details use
safe messages, including for older reports that stored filesystem exceptions;
the original stored evidence is not rewritten. New loader diagnostics remain in
server logs. Render the separate `review` for the current human decision.
The queue also uses one snapshot
for its count and page. Reads never change progress, evidence or review history.

Job polling returns the public `Job` record with HTTP 200 for every persisted
state: `queued`, `running`, `cancelling`, `completed`, `failed` and `cancelled`.
`progress.total` and `progress.fraction` may be null for indeterminate work;
`result` and `error` are nullable. Failed jobs remain HTTP 200 with their
structured `error`. `result.href` is returned from storage unchanged; the
current audit result destination `/api/v1/audits/{audit_id}` is callable and
returns the persisted audit lifecycle summary.

Cancellation accepts no body and returns HTTP 202 with the same public Job
model. Queued audit cancellation immediately becomes `cancelled` through the
existing transaction that updates the audit and queued samples. Running audits
become `cancelling` until a worker reaches a safe boundary; repeated requests
preserve that state. Completed, failed and already-cancelled jobs return 409
`JOB_ALREADY_FINISHED`; missing jobs return 404. Invalid IDs use 422, while
storage and unexpected failures use safe 503/500 envelopes. Worker identities,
claim tokens, leases, attempts and internal resource fields are never returned,
and cancellation does not rewrite completed measurements, findings or reviewer
decisions.

Snapshot-backed audit samples use the exact `audit_samples` manifest association
and pinned `sample_revision` to populate relative `assets.image`, `assets.mask`,
`assets.mask_preview`, `assets.thumbnail` and `summary.thumbnail_url` links when
the corresponding captured source is available. Image and thumbnail depend on
`original_image`; mask and mask preview depend on `raw_mask`. Recorded missing or
unreadable assets, and legacy samples without a manifest association, retain null
links. Queue/detail reads fetch only manifest availability metadata in their
database snapshot: they never open current dataset or managed asset files.

Links advertise captured availability rather than guaranteeing successful
rendering. The asset route requires an explicit positive revision, verifies the
exact manifest bytes and integrity before serving raw assets, and may reject
corrupt or unsupported sources when generating previews. Raw responses preserve
the exact captured bytes, including malformed files retained for inspection;
integrity verification checks stored size and SHA-256 rather than PNG validity.
The public source roles always use `image/png` and `X-Content-Type-Options:
nosniff`, independent of stored content-type metadata. Valid raw masks preserve
their integer IDs; colored mask previews are display-only PNGs. All four asset
kinds enforce the 64 MiB source-byte limit and return `422 ASSET_TOO_LARGE`
when exceeded; preview decoding failures return `422 ASSET_NOT_RENDERABLE`.

See api/README.md for the full contract and frontend fixtures.
api/ENDPOINTS.md distinguishes these implemented routes from
remaining planned operations, including evaluated feedback and other unfinished
workflows. Correction/recheck source is described in api/CORRECTIONS.md; its
generated inventory includes these operations. Reviewed dataset exports are applied; see
[api/EXPORTS.md](api/EXPORTS.md) for selection semantics and remaining verification gates.

## Finding records

`app/models/finding.py` defines a finding's identity, sample revision, rulebook
version, rule content, outcome and evidence. Outcomes are `pass`, `fail`,
`unsure` and `unavailable`. Reviewer resolution is separate and defaults to
`pending`, including for passing findings. Accepting a finding confirms the
tool's conclusion; the sample's final review decision is separate.

Non-pending finding reviews require a positive revision, reviewer ID and
timezone-aware timestamp. Extracted findings require a nonblank source sentence;
matching it to the guideline remains the evaluator's responsibility.

`Evidence` contains JSON measurements and optional asset links. Findings may be
sample-wide (`object_id=None`) or refer to an object, including an invalid source
ID that needs reporting. The API builder reuses these Python schemas. Rule
evaluation, finding persistence and finding-review actions are implemented as
services and FastAPI routes. Sample decisions and their review history are exposed
through the corresponding implemented FastAPI routes.

Rule proposals, rules, finding parameters and evidence share a finite JSON-value
validator. Nested `NaN` and infinity values are rejected before storage; ordinary
JSON values are preserved. Repository saves also revalidate mutable rule objects.

## Finding review

`review_finding()` in `app/services/review.py` records `accepted`, `rejected` or
`unsure` decisions on existing findings, including passing and unavailable
outcomes. Accepting confirms the tool's conclusion; it does not accept the sample.
The request contains the review revision last read, the decision, reviewer ID and
a comment (which may be empty). The backend assigns the next revision and a UTC
timestamp. `reviewer_id` provides attribution; authentication is separate work.

Apply the new `005_finding_reviews.sql` migration before using finding reads or
reviews on an existing database:

```bash
.venv/bin/python -m backend.app.storage.database
```

Then review a saved finding from Python:

```python
from backend.app.models.review import FindingReviewRequest
from backend.app.services.review import review_finding
from backend.app.storage import FindingRepository

repository = FindingRepository()
finding = repository.get_finding("finding_1")  # Use an ID from your audit.
review = review_finding(
    finding.finding_id,
    FindingReviewRequest(
        expected_review_version=finding.review.revision,
        resolution="rejected",
        reviewer_id="reviewer_1",
        comment="The original label is correct; the guideline was misinterpreted.",
    ),
    repository=repository,
)
history = repository.list_finding_reviews(finding.finding_id, limit=20)
```

Pending findings start at revision 0 with empty review history. Each saved decision
appends one record; later decisions retain earlier reviewers' comments and
timestamps. `get_finding()` and `list_findings()` return the latest review alongside
the original finding. History is ordered by revision and supports `limit`/`offset`
pagination.

The save checks `expected_review_version` while holding the finding's row lock. Competing
decisions cannot both advance the same revision. An outdated request raises
`FindingReviewConflictError`; reload the finding and inspect the newer decision
before resubmitting. Missing findings raise `FindingNotFoundError`. Review writes
are not automatically retried; if a connection failure leaves the commit outcome
unknown, reload the finding and its history before deciding whether to resubmit.

Review history is stored separately from the original machine snapshot. Machine
outcomes, measurements, pinned rule/sample data and audit progress stay unchanged,
including during audit result replays. Raw measurement `review_status` remains
`pending_review`; current finding decisions come from `Finding.review`. These
decisions do not change sample acceptance or correct annotations. The implemented
`PUT /findings/{finding_id}/review` route reuses the service's request model, and
`GET /findings/{finding_id}/reviews` exposes its attributed, ascending review history
with limit/offset pagination.

## Sample review

`review_sample()` records whether an audited sample is `accepted`, `rejected` or
`needs_correction`. A sample starts at `pending_review`, review revision 0, with
empty history. Reviewers can explicitly reopen it to `pending_review`; that action
also appends an attributed decision with the next review revision. Reviewer ID and
comment are required (the comment may be empty); the service assigns a UTC timestamp.
This decides whether the data can be used. Finding review separately decides
whether the tool's conclusion was correct. Neither decision changes the other.

Apply `006_sample_reviews.sql` with the migration runner before using sample
review on an existing database:

```bash
.venv/bin/python -m backend.app.storage.database
```

From Python, use IDs and the sample revision stored in your audit request:

```python
from backend.app.models.review import SampleReviewRequest
from backend.app.services.review import review_sample
from backend.app.storage import AuditRepository, SampleReviewRepository

audit_id, sample_id = "audit_1", "sample_1"  # Use your stored audit/sample IDs.
audit_request = AuditRepository().get_request(audit_id)
sample_revision = audit_request.sample_revisions.get(sample_id, 1)
repository = SampleReviewRepository()
current = repository.get_sample_review(audit_id, sample_id, sample_revision)
saved = review_sample(
    audit_id, sample_id,
    SampleReviewRequest(
        expected_sample_revision=current.sample_revision,
        expected_review_version=current.revision,
        decision="accepted",
        reviewer_id="reviewer_1",
        comment="Checked the available annotations and audit evidence.",
    ),
    repository=repository,
)
history = repository.list_sample_reviews(audit_id, sample_id, sample_revision, limit=20)
```

Every response and history entry includes `audit_id`, `sample_id` and
`sample_revision`. `expected_sample_revision` identifies that stored audit pin;
`expected_review_version` identifies the last-read **review** revision. These are
independent counters. History is scoped to the exact audit/sample pin and sorted
by ascending review revision, with `limit`/`offset` pagination. A later audit or
sample revision starts with its own pending decision and empty history.

Only samples whose durable processing status is `completed` or `failed` may be
reviewed. Completed samples with incomplete measurements, passing findings, or no
findings are included. Failed samples can be decided using available failure
evidence and manual inspection; acceptance does not erase or resolve the failure.
The parent audit may still be running or may have failed/cancelled after this
sample completed. Queued, running and cancelled samples reject every decision,
including reopening, with `SampleReviewStateError`. No machine outcome or confidence
automatically accepts/rejects a sample or restricts the reviewer's decision.

Saves lock the exact audit sample row and append one history record.
`SampleReviewConflictError` means another decision advanced the review revision;
reload before resubmitting. `SampleRevisionConflictError` means the supplied sample
revision is not stored for that audit/sample. `AuditSampleNotFoundError` means the
audit/sample pair does not exist. Failed inserts/commits preserve prior decisions.
Writes are not automatically retried; after an unknown commit outcome, inspect
the current decision and history before resubmitting. Source measurements,
findings and their resolutions, processing states, failures, and job/audit progress
remain unchanged, including during audit-result replays. Raw measurement
`review_status` stays `pending_review`; use `get_sample_review()` for the current
human decision.

Legacy audits still load `dataset_root`, and their sample revision is not a
guarantee of current dataset files. Snapshot-backed audits use
`BatchAuditRequest(source_mode="snapshot")`: HTTP submission supplies exact
catalogue manifests directly. Legacy Python integrations may instead inject an
authoritative revision resolver that captures or reuses the exact manifest for every
sample before creating the audit, and materializes only those managed bytes for
collection. A missing or tampered snapshot never falls back to `dataset_root`.
The parent resolves the manifest from PostgreSQL; the isolated child verifies and
materializes its bytes before collecting measurements and evaluating rules.
Snapshot preparation therefore shares the existing timeout, cancellation and
lease supervision, without opening PostgreSQL in the child. The parent removes
the temporary sample directory after stopping the child. A cleanup failure is
logged without replacing an existing processing, cancellation or ownership-loss
error; cleanup failure after otherwise successful processing remains a storage
error. Managed-file integrity
and temporary-storage failures fail the job rather than being recorded as bad
source samples. Missing or unreadable assets recorded at capture time remain
source-data evidence for the collector.

Dataset import now owns annotation-head selection. Audit HTTP submission reads
those heads directly; legacy Python resolver integrations still provide their
own authoritative revision and trusted source root for each sample.
`needs_correction` records a decision, not an annotation edit. Correction storage,
validation and immutable publication now create new revisions without changing old
audits or exports. Corrected data needs a new linked audit and explicit review;
correction never transfers acceptance.
Reviewed-count aggregation remains separate work. Exports now freeze the current
human decisions and exact audited revisions without requiring correction support.

## Confirmed-rule evaluation

`app/services/evaluation.py` is the boundary between measurements and machine
findings. `evaluate_rules()` accepts one explicitly supplied, confirmed
`Rulebook`, one `AuditMeasurements` report and explicit audit/sample identity.
It revalidates mutable rulebook objects, validates parameters through the
explicit registry in `app/checks/registry.py`, and returns findings for passing
as well as failing objects. Each finding pins the rulebook ID/version and rule
snapshot, sample revision, source sentence and finite evidence. Findings are
created with `review.resolution="pending"`; no machine outcome changes human
review state.

The registry currently supports the existing measurement vocabulary:
`allowed_labels`, annotation/mask ID and dimension checks, metadata, box
validity/overlap, box-mask alignment, outside fraction, box fill and connected
component count. Thresholds and label lists must be explicit confirmed-rule or
request parameters. There are no built-in labels or thresholds. A missing or
unusable measurement is `unavailable`, an ambiguous result is `unsure`, and an
unknown check remains visible as an `unavailable` finding. Object occurrence
indices are part of deterministic finding IDs, so duplicate source object IDs
are not collapsed.

## Persistent batch audits and jobs

`app/services/batch_audit.py` provides `BatchAuditService`. `start()` validates
and pins one confirmed rulebook version, stores the audit request, creates the
queued job and creates one queued sample row in one PostgreSQL transaction.
`run_job()`/`run_audit()` claim a lease and process one sample at a time. Workers
that already hold a `JobClaim` from `JobRepository.claim_next_job()` dispatch an
audit with `run_claimed_job(claim)` instead. It verifies persisted ownership,
prepares the audit and resumes unfinished work without claiming the job again.
Only audit claims can be passed to this method.

Each sample transaction stores its measurements, pending findings, technical
failure (when needed), terminal sample state and job progress together.

`BatchAuditRequest.allowed_labels` is optional. When omitted or `None`,
collection skips shared label checks while retaining annotation labels and
geometry measurements. Each `allowed_labels` rule uses its own confirmed label
list during evaluation, even when the request supplies a shared list.

To start a snapshot-backed audit from Python, provide a resolver that selects the
authoritative revision and trusted local source root for every sample, plus a
manifest repository configured with the private asset root:

```python
from backend.app.models.audit import BatchAuditRequest
from backend.app.services.batch_audit import BatchAuditService, ResolvedSampleRevision
from backend.app.storage import AuditRepository, SampleAssetManifestRepository

class Resolver:
    def resolve_samples(self, *, dataset_id, sample_ids, requested_revisions):
        # Resolve from the dataset/revision authority; do not choose max(manifest).
        return {
            sample_id: ResolvedSampleRevision(
                sample_revision=requested_revisions[sample_id],
                source_root="/trusted/PennFudanPed",
            )
            for sample_id in sample_ids
        }

manifests = SampleAssetManifestRepository()
audits = AuditRepository()
service = BatchAuditService(
    rulebook_repository=rulebooks,
    audit_repository=audits,
    sample_revision_resolver=Resolver(),
    manifest_repository=manifests,
)
request = BatchAuditRequest(
    dataset_id="dataset_1",
    dataset_root="legacy-path-retained-for-compatibility",
    rulebook_id="book_1",
    rulebook_version=1,
    sample_ids=["sample_1"],
    sample_revisions={"sample_1": 1},
    source_mode="snapshot",
)
service.start(request, audit_id="audit_snapshot_1", job_id="job_snapshot_1")
service.run_audit("audit_snapshot_1", worker_id="worker_1")
```

`rulebooks` in this example is an existing `RulebookRepository` instance. This
resolver example is for external Python integrations. Imported datasets use the
catalogue-backed HTTP submission service without a source root. Existing requests
without `source_mode` remain legacy.

`app/storage/workflow_repository.py` implements atomic worker claims with
PostgreSQL row locks, claim tokens and leases. Cancellation is cooperative:
queued work is cancelled immediately, running work becomes `cancelling`, and
the worker stops before starting the next sample. Expired leases can be
recovered by `AuditRepository.recover_stale_jobs()`; a replacement claim has a
new token, so a stale worker cannot finalize it later. The implementation is
bounded and sequential initially, while durable `queued`, `running`,
`cancelling`, `completed`, `failed` and `cancelled` job states are exposed by
the implemented job polling and cancellation routes. Cancellation remains
cooperative: queued audit work is terminal immediately, while running work
waits for a safe worker boundary.

Lease checks use the current time after acquiring the required job and sample
row locks. A claim that expires while waiting cannot renew or persist results.
New leases start after the claim's lock waits, giving the worker the configured
lease duration. Accepted transactions retain their locks through commit.
Audit acquisition retries retain a unique acquisition ID; sharing a worker name
does not grant access to another execution's live claim. An expired acquisition
cannot revive its old token.

Each running sample also has a persisted hard deadline. The parent worker
checkpoints ownership and cancellation before collection, after collection,
before evaluation and before terminal persistence. Collection runs in a
macOS-spawned child process that receives only sample inputs and a pinned
rulebook; the parent owns PostgreSQL access and terminates the child when
cancellation, ownership loss or the deadline wins. The child clears inherited
PostgreSQL credential environment variables before computation; this changes
only the child process and is process-termination isolation, not a security
sandbox. Both collection and confirmed-rule evaluation run in that supervised
child; `AUDIT_SAMPLE_MAX_SECONDS` configures the default limit
(900 seconds, bounded to 1--86400); a deadline is recorded as the explicit
`processing_timeout` sample failure rather than being confused with bad source
data.
An independent local watchdog terminates computation when the hard deadline or
last confirmed lease expires, including while the parent waits for PostgreSQL
or result transport. Renewals only extend this local bound after acknowledgement.
Database transactions also use a 5-second lock timeout and a 30-second statement
timeout; these bound server waits separately from the sample's execution limit.

Transient collector failures use a durable attempt counter with at most three
total collector executions; transient database operations have a separate
bounded transaction retry and do not consume collector attempts. Terminal
sample writes carry a stable operation identity, so an acknowledgement lost
after commit is reconciled as the same result rather than applied twice; an
attempt increment is likewise idempotent. The transaction retry budget is
bounded per operation in the current worker and is not a durable cross-restart
retry budget. Lost commit acknowledgements are reconciled even when the driver
has no SQLSTATE. Known server rejections retain their rollback classification;
an unknown commit is replayed only by operations that explicitly support it. Invalid
confirmed-rule parameters, corrupt shared
measurement state, ownership loss, cancellation and programming errors are not
silently retried. A retry that exhausts its attempts becomes the explicit
`retry_exhausted` sample failure. Rule execution has a narrower per-check
boundary: an attributable supported-check implementation failure emits an
`unavailable` finding with reason `check_execution_failed` and marks the sample
and audit incomplete; it does not turn into a pass or fail. The collector's
measurement document keeps its original `status`; the durable incomplete flag
also captures evaluator incompleteness without rewriting source-processing
truth. Ordinary missing or
unusable evidence remains `unavailable`, while invalid rule configuration and
fatal workflow/storage errors stop the audit.
Collection checks receive private copies of annotation/metadata/image inputs
and share one mask snapshot backed by immutable bytes. Ordinary local computation
errors can be isolated without altering later checks' inputs. Resource exhaustion,
I/O failures and unclassified runtime failures escape that boundary and fail the
worker; they are not presented as isolated unavailable checks.

Apply migration `004_bounded_sample_processing.sql` through the normal initialization command
before using these repositories. Dataset roots are carried as opaque strings;
this target deliberately does not add path validation or dataset-import
policy.

## Performance priorities and measurements

The first optimization pass targets repeated CPU work and database round trips
while retaining complete evidence, human review state and worker safeguards:

1. Mask measurements use OpenCV pixel extraction, reuse the instance bounds
   within a call, and run connected components only over actual foreground
   bounds. Pixels outside annotation boxes and disconnected pieces remain
   included; component boxes are translated back to source coordinates.
2. Finding persistence uses batches of up to 128 pipelined inserts and one
   readback per batch. For 1,000 findings this reduces evidence-readback queries
   from 1,000 to 8. Transaction-held row locks, checks at batch boundaries and
   the final pre-completion check preserve ownership/deadline handling. A
   conflict or expired claim rolls back measurements and every inserted batch.
   Existing machine evidence and reviewer records are never overwritten.
3. Pairwise geometry validates each annotation box once per call, reducing box
   validations from `n * (n - 1)` to `n`. All pairs, including invalid-box
   evidence, are still returned; pair generation itself remains quadratic.

On 19 October 2026, local synthetic timings (2048×1024 images, OpenCV using 10
threads, median of seven runs with three iterations each) measured:

- Two-object sample collection: 16.41 ms before, 9.46 ms after (1.73× faster).
- Ten-object sample collection: 50.60 ms before, 16.16 ms after (3.13× faster).
- Combined measurements for a compact mask instance: 4.15 ms to 0.80 ms (5.16×).
- Combined measurements for a full-image instance: 10.68 ms to 7.03 ms (1.52×).
- Box comparisons across 10–100 objects: approximately 2.8–3.6× faster.

These measure synthetic CPU workloads, not real-dataset or complete audit-job
throughput. Database batching has not been runtime-tested or benchmarked yet.
Run the timing harness separately from the test suite:

```bash
.venv/bin/python -B -m backend.benchmarks.benchmark_checks
```

For before/after timing, pass `--baseline-dir /path/to/earlier/checks`, containing
the earlier `geometry.py` and `segmentation.py`. The collector comparison keeps
loading and check orchestration identical while swapping those two kernels.
No database or model is started, and temporary sample files are removed.

Next profiling priorities are PostgreSQL connection setup and per-sample process
startup for small samples, review-queue queries for large audits, and model reload
cost for repeated Ollama extraction. Pooling, worker reuse, parallelism and model
residency need representative measurements and explicit resource/lifecycle
handling before changing their current behavior.

## Draft rulebook extraction

`app/services/rule_extraction.py` connects the local Ollama client to the
rulebook model. From Python, running from the project root:

```python
from backend.app.services.rule_extraction import extract_rulebook

book = extract_rulebook("Allowed labels are person and vehicle.")
```

The service preserves the guideline text, assigns a unique rulebook ID and
returns version 1 in draft state. Empty proposals and unsupported requirements
remain available for reviewer inspection. It uses `ollama.toml` by default;
pass `settings=load_ollama_settings(path)` for an alternate configuration.
Model/configuration errors propagate instead of becoming empty successful
results. Persistence, job handling, reviewer confirmation and HTTP routing are
not performed by this function.

To extract and save a draft in one operation, use `extract_and_save_rulebook()`
after initializing PostgreSQL as described below:

```python
from backend.app.services.rule_extraction import extract_and_save_rulebook
from backend.app.storage.repository import RulebookRepository

book = extract_and_save_rulebook(
    "Allowed labels are person and vehicle.",
    repository=RulebookRepository(),
)
```

The repository is an explicit dependency, so callers control its database
settings. The service completes extraction and validation before opening a
database transaction, and returns the saved version-1 draft after the write
commits. Pass `settings=...` to select alternate Ollama settings. Extraction,
validation and storage errors propagate to the caller. Empty drafts and
unsupported requirements are saved for reviewer inspection. Each call creates a
new rulebook; failed calls are not automatically retried. Reviewer confirmation,
jobs and HTTP routing remain separate steps.

## Local PostgreSQL setup

PostgreSQL runs in Docker; Python and Ollama continue running on the host. Install
Docker with the Compose plugin and start its engine before running these commands
from the project root:

```bash
cp .env.example .env
```

Set `POSTGRES_PASSWORD` in `.env` to your own nonempty password. Single-quote
passwords containing `$` or `#`, for example `POSTGRES_PASSWORD='your$password#here'`,
so Docker Compose and the backend read the same literal value. Keep credentials
in that ignored file; commit only the empty example. Then install the Python
dependencies, start PostgreSQL and initialize the application schema:

```bash
.venv/bin/python -m pip install -r requirements.txt
docker compose up -d --wait postgres
.venv/bin/python -m backend.app.storage.database
```

Compose uses the official `postgres:18-bookworm` image and exposes the database
only at `127.0.0.1:55432`. Set `POSTGRES_PORT` in `.env` to choose a different
host port. The `postgres_data` named volume preserves records across container
restarts and recreation. For PostgreSQL 18, it mounts at `/var/lib/postgresql`,
following the official image's volume layout.
The first start needs internet access to download the image; an installed image
and local database can run offline afterwards.

The backend reads the root `.env` file independently of the terminal's working
directory. Existing environment variables take precedence over that file.
`POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER` and
`POSTGRES_PASSWORD` define the connection. An optional `DATABASE_URL` overrides
them for the backend; it does not configure Docker's database container. Connection
settings are validated before opening a database connection.
`POSTGRES_CONNECT_TIMEOUT` optionally sets the connection timeout in seconds
(1–60, default 5). Environment-file variable interpolation is disabled in the
backend; supply literal values.

To stop or resume the database without deleting records:

```bash
docker compose stop postgres
docker compose up -d --wait postgres
```

`docker compose down` also retains the named volume. Do not remove that volume
when you intend to keep the records. The image's user, password and database
environment variables initialize an empty volume; editing `.env` after
initialization does not change existing database credentials. Any earlier SQLite
file is left untouched; its records are not automatically migrated.

## Rulebook storage

`RulebookRepository` stores rulebook versions in PostgreSQL. Initialize the schema
with the command above before using it. Importing or constructing the repository
does not create a database or modify the schema.

```python
from backend.app.storage.repository import RulebookRepository

repository = RulebookRepository()
saved = repository.save_rulebook(book)
latest = repository.get_rulebook(saved.rulebook_id)
original = repository.get_rulebook(saved.rulebook_id, version=1)
page = repository.list_rulebooks(limit=20, offset=0)
```

The first save requires version 1 and defaults to `expected_version=0`, meaning
the ID must not already exist. For a new version, pass the latest version you
read as `expected_version` and supply a rulebook with that version plus 1.
Old snapshots are never overwritten. The version check and insert share one
transaction, so competing edits cannot both silently succeed.

`get_rulebook()` returns the latest saved version, including drafts, unless an
explicit version is requested. Missing IDs/versions raise `RulebookNotFoundError`.
Stale saves raise `RulebookVersionConflictError`; invalid or unreadable stored
data raises `RulebookStorageError`. Invalid input records raise validation errors.
Each read returns independently validated objects. `list_rulebooks()` returns a
`RulebookPage` with items, limit, offset, total and next_offset, ordered by ID.

For review actions, use the service functions below. The repository validates
snapshots and handles persistence; the review service owns state transitions and
rule-origin policy. Calling the low-level `save_rulebook()` directly does not
record a reviewer action.

## Rulebook review

`app/services/review.py` supports editing, confirming, rejecting and reopening
saved rulebooks. Existing databases need migration `002_rulebook_reviews.sql`;
rerun the schema command before using the review workflow:

```bash
.venv/bin/python -m backend.app.storage.database
```

The migration runner applies only migrations that have not already been applied.
It preserves existing rulebook snapshots.

Load a saved draft, inspect its proposals and submit a `RulebookReplace` request.
This request contains the complete guideline text and rule list, so omitted rules
are removed from the new version. Retained rule IDs keep their original origin;
new IDs must use `RuleOrigin.REVIEWER`. You can change descriptions, check names,
parameters and citations. Extracted rules still need citations, and every supplied
citation must occur exactly in the submitted guideline text. Rule IDs must remain
unique. Unsupported checks can remain visible for review.

The request carries each rule's reviewer decision: `draft`, `confirmed` or
`rejected`. Saving these decisions leaves the whole book in draft state. For
example, after a reviewer has checked the first rule in a nonempty saved draft:

```python
from backend.app.models.review import RulebookAction, RulebookReplace
from backend.app.models.rule import ConfirmationState, Rule
from backend.app.services.review import confirm_rulebook, save_rulebook_review
from backend.app.storage.repository import RulebookRepository

repository = RulebookRepository()
draft = repository.get_rulebook("saved-rulebook-id")  # Use the ID returned at creation.
reviewed_rules = list(draft.rules)
reviewed_rules[0] = Rule(**{
    **reviewed_rules[0].model_dump(),
    "confirmation_state": ConfirmationState.CONFIRMED,
})
reviewed = save_rulebook_review(
    draft.rulebook_id,
    RulebookReplace(
        expected_version=draft.version,
        reviewer_id="reviewer_demo",
        guideline_text=draft.guideline_text,
        rules=reviewed_rules,
    ),
    repository=repository,
)
```

This records one explicit rule decision and preserves the other rules. Review
the remaining rules in the same way. To reject a rule, submit its state as
`REJECTED`; remove it from a subsequent replacement list before confirming the
book. Earlier snapshots retain its rejection. Add a reviewer-authored rule by
including a new `Rule` with a new ID and `origin=RuleOrigin.REVIEWER`; its citation
may be absent.

Once the reviewer has confirmed every retained rule, confirm the nonempty book:

```python
confirmed = confirm_rulebook(
    reviewed.rulebook_id,
    RulebookAction(expected_version=reviewed.version, reviewer_id="reviewer_demo"),
    repository=repository,
)
```

`confirm_rulebook()` checks the recorded decisions; it never approves individual
rules automatically. Empty books and books containing draft or rejected rules
cannot be confirmed. `reject_rulebook()` takes the same arguments and rejects a
draft as a whole while preserving its individual rule decisions.

Only drafts can be edited, confirmed or rejected. To change a confirmed or
rejected book, call `revise_rulebook()` with its latest version and a
`RulebookAction`. This creates a new draft and resets every included rule to draft
so the reviewer can reconsider it. A draft is already editable and cannot be
reopened through this action.

Every successful edit, confirmation, rejection or revision appends version
`expected_version + 1`. The snapshot and its review record are committed together;
if either write or the commit fails, neither is saved. Review records contain the
rulebook ID, new version, reviewer ID, action and backend-assigned timestamp.
`reviewer_id` provides attribution for the local prototype; authentication and
authorization are not implemented.

Two callers editing the same version cannot overwrite one another. A stale
`expected_version` raises `RulebookVersionConflictError`, including when another
reviewer saves after the service loads the draft. Reload the latest version and
review the differences before resubmitting. No automatic retry merges decisions.
Invalid transitions raise `RulebookReviewError` with reason
`invalid_rulebook_state`; premature confirmation uses `rulebook_not_ready` and
origin changes use `invalid_rule_origin`. Invalid fields or citations raise
Pydantic `ValidationError`. Missing records and storage failures use the repository
errors described above. Failed actions create no new snapshot or review record.

Read the saved history with:

```python
versions = repository.list_rulebook_versions(reviewed.rulebook_id, limit=20, offset=0)
reviews = repository.list_rulebook_reviews(reviewed.rulebook_id, limit=20, offset=0)
```

Both lists are ordered by ascending version and expose `items`, `limit`, `offset`,
`total` and `next_offset`; limits are 1–100. A missing rulebook raises
`RulebookNotFoundError`. An existing book can have an empty review history:
extraction and earlier low-level saves do not gain invented reviewer records.
Saved-rulebook HTTP reads and reviewer actions now reuse these Python services
and repository operations. Rulebook extraction-job HTTP submission is implemented in the checkpoint source, with an explicit extraction worker. See the extraction guide.
See the rulebook HTTP guide. Audit jobs are documented above.

## Database tests

The normal test command runs unit tests and skips PostgreSQL integration tests
when `PRISM_TEST_DATABASE_URL` is unset:

```bash
.venv/bin/python -m pytest backend/tests -q
```

Set `PRISM_TEST_DATABASE_URL` to a PostgreSQL connection URL or libpq connection
string to include the integration tests. Each test creates a unique schema and
removes only that schema afterward, preserving application tables. The test user
needs permission to create schemas. A configured but unavailable database fails
the tests instead of being silently skipped.

## Rulebook HTTP read and review

Nine rulebook operations are implemented: create a manual draft, list latest
snapshots, read latest/explicit versions, list versions, list attributed reviews,
replace a draft, confirm, reject and reopen a final book as a draft. See
the API rulebook guide for exact paths,
strict requests, states and error codes. Manual creation uses strict editor
inputs, preserves exact guideline text, and saves a version-1 draft without
inventing review history. Extraction-job,
audit-start, authentication or feedback-evaluation routes are added by this work.

Mutations use request-time repository injection and the existing transactional
review services. They append a snapshot and real attribution atomically; reads
never write, run inference or inspect assets. reviewer_id is attribution, not
verified identity. Conflicts require refreshing and explicit reviewer resubmission.
An unsupported check can be confirmed for human review without becoming an
implemented evaluator. Replacement can remove rules; book confirmation never
implicitly confirms rules. Earlier snapshots remain unchanged.

The four GETs exclude mutation-only 409 declarations. Each mutation documents
only its supported conflicts. Invalid replacement IDs/citations use a narrow
client-error boundary; stored corruption and unrelated validation errors do not.
No exception internals are returned to clients.

Generated OpenAPI, fixtures and endpoint inventories must come from
`.venv/bin/python -B -m backend.api.build_contract`, not manual artifact edits.
Earlier passing suite results do not validate the current export integration.
The earlier failing-suite note is historical; the user now reports the baseline passed. A correction/recheck full-suite result
remains pending. PostgreSQL tests require `PRISM_TEST_DATABASE_URL`.

## Durable guideline extraction jobs

The extraction guide documents migration 009, separate HTTP/worker processes, `--once`/loop mode, saved model settings, finite execution policy, one-attempt stale recovery, atomic draft/job publication and the external Ollama cancellation limitation. The original synchronous extraction APIs remain compatible. Its source is integrated and the shared API artifacts are regenerated; runtime validation remains a separate developer-controlled step.

## Reviewed dataset exports

`POST /api/v1/exports` freezes the terminal audit's exact sample revisions and human
sample decisions, then durably queues a managed export job. `accepted_only` selects
explicit acceptance; `all_reviewed` also includes rejected samples diagnostically.
Pending and needs-correction decisions remain excluded. The archive copies verified
captured bytes and includes an inclusion/exclusion manifest; later reviews or
catalogue heads do not change an existing export.

Read/list export metadata, poll or cancel its existing job, and download the ZIP
or JSON manifest after completion. Migration 011 and an explicit export worker
are required. Neither API startup nor submission runs that worker.

```bash
.venv/bin/python -B -m backend.app.workers.export
```

See [api/EXPORTS.md](api/EXPORTS.md) for setup, limits and error responses.
Correction/recheck source and all five regenerated contracts are integrated.
The combined API has 36 implemented and 14 planned operations, 50 total.
Tests after the latest fixes, migration application and real worker/download
verification remain operator-controlled gates.

## Correction/recheck source handoff

See [api/CORRECTIONS.md](api/CORRECTIONS.md). All four source phases are applied and
all five API artifacts are regenerated, with 36 implemented and 14 planned
operations. Migration 012, regression execution and runtime verification remain
separate gates; contract generation does not establish passing tests.
