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
in the backend guide. The review tests exercise
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
`test_rulebook_contract.py` checks all 20 live operations, ten unique rulebook
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

## Manual rulebook creation regressions

`test_rulebook_creation.py` covers strict creation inputs, exact text/citations,
finite JSON, mutable-instance revalidation, independent drafts, server-owned
metadata, repository return identity, collision propagation and no retries.
`test_rulebook_create_api.py` covers 201 draft responses, dependency overrides,
safe 422/409/503/500 boundaries, malformed bodies, internal/response validation,
nonfinite values and unique synchronous collection-route registration.
`test_rulebook_create_repository.py` reuses the isolated-schema fixtures for
durable POST/GET/list/version reads, empty creation history, edit/confirm
v1-to-v2-to-v3, rollback, deterministic ID collisions, empty confirmation and
rejection/revision interactions.

## Plain-text extraction regressions

New authored tests: `test_extraction_repository.py`, `test_extraction_jobs.py`,
`extraction_fakes.py` and `test_extraction_api.py`. They reuse the isolated
PostgreSQL fixtures in `test_repository.py` and the real-lock-wait harness in
`test_workflow_repository.py`; no second test-database framework is introduced.
Coverage includes private restart-stable inputs, strict/mutated models, enqueue
and publication rollback, uncertain commit readback, reserved-ID conflicts,
managed-kind isolation, two-worker claiming, fresh authorization after row/advisory
lock waits, cancellation ordering, stale single-attempt recovery and additive
upgrade preserving existing audit/rulebook/review data.

Offline spawn tests use importable top-level fakes and explicit child-start
markers, not assumed timing stages. They cover large/partial Pipe frames,
crash/EOF/malformed/nonfinite output, safe client reasons, deadline, lease loss,
cancellation, shutdown, storage failure, reaping and unresolved commit behavior.
HTTP tests cover strict bodies, safe submission failures, all six polling states,
parent-owned completion, version-pinned retrieval and later edit/confirm behavior.
Existing audit/manual tests remain; inventory counts/fixtures are updated without
weakening route traversal, duplicate detection or schema/response constraints.

These tests were authored and syntax-compiled only, NOT collected or executed.
All runtime/import/schema/fixture/freshness checks remain for the full checkout.
See EXTRACTIONS.md for the operator workflow and limitations.
