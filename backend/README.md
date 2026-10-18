# Backend

This folder contains the dataset reader, box and mask measurements, local Ollama
client, rulebook extraction and review, batch audits, and finding/sample review backed
by PostgreSQL. The remaining modules contain responsibility notes for later
implementation.

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
    routers/                 Future API endpoints, if an HTTP API is used
  tests/                     Backend tests added as features are implemented
```

Routers will accept requests and call services if an HTTP API is introduced. A separate controller layer is not needed initially: services coordinate the workflow. No API framework has been installed or chosen by this scaffold.

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

## Proposed frontend API

See [api/README.md](api/README.md) for the draft endpoint contract, request/response
schemas and frontend fixtures. [api/ENDPOINTS.md](api/ENDPOINTS.md) lists every
planned operation. These are templates; no HTTP routes are running yet.

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
services. Sample decisions are implemented separately; HTTP routing remains planned.

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
decisions do not change sample acceptance or correct annotations. The planned
`PUT /findings/{finding_id}/review` contract now reuses the service's request model;
its HTTP handler is not implemented yet.

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

The pin is **not a guarantee of current dataset files**. `BatchAuditRequest`
currently supplies sample revision numbers (default 1), and workers load assets
from the dataset path. There is no authoritative annotation revision store or
immutable asset loader. This guard prevents reviewing the wrong stored audit pin;
it cannot detect edits to files on disk. `needs_correction` records a decision,
not an annotation edit. Correction storage, validation and immutable revision
loading still need implementation; corrected data must receive a new audit and
explicit review rather than inheriting acceptance. HTTP adapters, review queue
composition, reviewed-count aggregation and export remain separate work.

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

`app/storage/workflow_repository.py` implements atomic worker claims with
PostgreSQL row locks, claim tokens and leases. Cancellation is cooperative:
queued work is cancelled immediately, running work becomes `cancelling`, and
the worker stops before starting the next sample. Expired leases can be
recovered by `AuditRepository.recover_stale_jobs()`; a replacement claim has a
new token, so a stale worker cannot finalize it later. The implementation is
bounded and sequential initially, while durable `queued`, `running`,
`cancelling`, `completed`, `failed` and `cancelled` job states remain available
for the planned HTTP layer.

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
following the [official image's volume layout](https://hub.docker.com/_/postgres).
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
These are Python services and repository operations; HTTP routes and rulebook
extraction job wiring remain separate. Audit jobs are documented above.

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
