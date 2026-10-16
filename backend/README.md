# Backend

This folder contains the dataset reader, box and mask measurements, sample audit
service, local Ollama client, and rulebook extraction, review and PostgreSQL
storage. The remaining modules contain responsibility notes for later
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

## First task

Start in `app/datasets/penn_fudan.py`:

1. Load `FudanPed00001.png`, its annotation text and its mask from the existing root-level `PennFudanPed/` folder.
2. Parse person IDs, labels and box coordinates.
3. Check that the image and mask dimensions agree and that instance IDs match the annotations.

The annotation declares a coordinate origin of `(1, 1)`. Choose and document an internal coordinate convention before converting boxes; preserve the original convention when exporting.

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

Edit `ollama.toml` in this folder to change the local model. Set `model` to the
exact name shown by `ollama list`; the model must already be installed. The same
file controls the local server address, request timeout in seconds, context
length and maximum output tokens.

`app/core/config.py` loads and validates these settings independently of the
terminal's working directory. `propose_rules()` reads the file on each call,
so subsequent requests use saved changes. Invalid settings fail before an API
request. CPU execution remains enforced by the client.

For a one-off model selection, use `propose_rules(text, model="installed-model:tag")`.
To supply an alternate configuration file, pass
`settings=load_ollama_settings(path)`. Import the loader from
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
evaluation, finding persistence and finding-review actions remain to be connected.

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
These are Python services and repository operations; HTTP routes and jobs remain
planned.

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
