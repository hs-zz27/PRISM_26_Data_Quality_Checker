# Backend

This folder contains the dataset reader, box and mask measurements, sample audit
service, rule records, PostgreSQL rulebook storage and local Ollama client. The
remaining modules contain responsibility notes for later implementation.

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

## Proposed frontend API

See [api/README.md](api/README.md) for the draft endpoint contract, request/response
schemas and frontend fixtures. [api/ENDPOINTS.md](api/ENDPOINTS.md) lists every
planned operation. These are templates; no HTTP routes are running yet.

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

This implements append-only version persistence, not reviewer authorization.
The planned same-version confirmation action needs a dedicated transactional
operation with review history; it must not be implemented by overwriting a
snapshot through `save_rulebook()`. Extraction, saving, jobs and HTTP endpoints
are still separate steps.

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
