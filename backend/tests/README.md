# Backend tests

Run the backend suite from the project root:

```bash
.venv/bin/python -B -m pytest backend/tests -q --tb=short -p no:cacheprovider
```

Tests cover inclusive box geometry, partial mask overlap, independent sample
checks, annotation metadata, finite JSON values, rule validation, local
Ollama configuration precedence (process environment, `.env`, TOML), request
handling, extraction, rulebook transitions, finding/sample-review requests and API templates.
Audit read tests cover HTTP query validation and safe errors, live route/schema
boundaries, PostgreSQL queue pagination/filtering, current reviews, failures,
null measurements, snapshot-pinned asset links, partial/legacy asset
availability and consistent snapshots during a concurrent review change. Asset
HTTP tests cover the four public asset kinds, required revision validation, safe
JSON errors and the live binary/error OpenAPI response shape. Job API tests cover
all six persisted states, public/private payload boundaries, failed-job HTTP 200
semantics, audit-aware cancellation, terminal conflicts and the GET-vs-POST
OpenAPI response boundary. Queue/detail reads are also guarded against
managed-file access.
PostgreSQL integration coverage follows generated audit links through the real
capture, manifest, file verification, preview and HTTP layers. It covers newer
revisions, removed source files, encoded identifiers, exact 16-bit mask bytes,
unchanged review state, tampered managed files and consistent source-byte limits.
They are authored but left unrun during implementation at the user's request.
Ollama requests are mocked; these tests do not download or run a model.

Asset preview coverage checks revision selection, verified managed bytes,
thumbnail dimensions and transparency, palette and 16-bit instance IDs, stable
colors across all 16-bit IDs, malformed/unsupported PNGs and invalid chunk
checksums, bounded managed-file reads, and preservation of source files. These
tests are also left unrun during implementation.

Performance regression coverage compares cropped component geometry with full
image measurements, retains invalid-box evidence in pairwise comparisons, and
checks multi-batch finding persistence, replay, conflicts and expiry rollback.
The separate `backend.benchmarks.benchmark_checks` timing harness measures CPU
workloads without executing pytest or touching PostgreSQL.

The contract suite compares generated files with their source and checks that
audit/export fixtures use the confirmed rulebook version. Run tests before
committing changes.

PostgreSQL integration tests require `PRISM_TEST_DATABASE_URL`. Each test uses
an isolated schema and removes it afterward. See the database test instructions
in [the backend guide](../README.md#database-tests). The review tests exercise
version history, concurrent decisions, migration upgrades and rollback when
either insert or the transaction commit fails.
Finding-review coverage also checks that audit replays preserve decisions and
that reviewing a finding leaves machine evidence, sample state and audit progress
unchanged.
Sample-review coverage checks explicit sample pins versus review revisions,
attributed reopening, no-findings samples, processing-state eligibility, preserved
finding decisions, concurrent saves, pagination scope, audit replays and migration
upgrades. New sample-review tests are authored but have not been executed yet;
run the suite, including opt-in PostgreSQL integration tests, before committing.
Job cancellation integration coverage uses isolated PostgreSQL schemas to verify
queued and running audit transitions, terminal conflicts, and preservation of
completed measurements, findings and reviewer decisions. These tests are also
authored but have not been executed.

## Saved-rulebook HTTP regressions

`test_rulebook_error_api.py` covers safe mappings, original registrations,
request IDs and unknown/internal validation failures. `test_rulebook_read_api.py`
covers all reads, latest versus explicit versions, empty histories, nonblank
identifiers, pagination, safe errors and thin delegation without side effects.
`test_rulebook_review_api.py` covers service delegation, strict bodies, state and
origin policy, unsupported checks, content validation and no-write failures.
`test_rulebook_contract.py` checks all 18 live operations, eight unique rulebook
operations, action-specific conflicts, actual model schemas, full schema
constraints/nullability/required fields, parameter defaults and artifact freshness.
Only schema titles and nullable explicit-null default metadata may differ between
FastAPI and generated schemas; validation constraints are retained.

`test_rulebook_http_repository.py` reuses the isolated-schema fixtures from
`test_repository.py`; it exercises v1→v6 through real HTTP/services/PostgreSQL,
immutable snapshots, correct attribution, empty histories, missing explicit
versions, pagination and failure paths without writes. Existing repository tests
remain responsible for locking, concurrency and transactional rollback.
These tests are authored, not run; do not treat their presence as passing evidence.
The checkpoint prohibits pytest/PostgreSQL execution until separately authorized.
