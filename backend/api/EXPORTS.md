# Reviewed dataset exports

## Delivery and validation status

This feature preserves human-reviewed immutable source revisions. It does not
implement annotation corrections, rechecks, feedback improvement or detection.
All four phases are applied in this checkout, and the five shared API artifacts
are regenerated from the corrected source. The current inventory is 32 implemented
and 18 planned operations, 50 total. The developer's latest full-suite run reported
39 failures, 2,021 passes and two errors. Integration corrections are applied but
tests have not been rerun; no passing result is claimed for this corrected source.
Migration 011 application and real worker/PostgreSQL/download verification remain
operator-controlled gates. Static/schema verification is not runtime evidence.

## Applied source and remaining verification

The original handoff captured baseline
`8179e286fa3e54e669886fa69181bc8d570c8939`. Its models/settings/repository,
managed-job guards and cancellation dispatch, archive builder/supervisor/worker,
HTTP delivery, examples and contract alignment are integrated. The bounded
existing-test edits are applied, and all five contract artifacts are regenerated.
Do not reapply baseline or earlier-draft patches over these corrections.

Migration 011 is the additive reviewed-export schema in this checkout. Another
checkout with a conflicting migration number requires coordination; never
overwrite or renumber an applied migration. A fresh test pass and explicit
migration/worker/download validation remain necessary before claiming readiness.

Operator commands below are documentation, not instructions to run automatically.
Migration/worker/runtime execution requires the user's
separate authorization and an appropriately configured local checkout.

```bash
python -B -m backend.app.storage.database
python -B -m backend.app.workers.export --once
python -B -m backend.app.workers.export
```

Offline contract regeneration is complete for this revision. After later source
changes, regenerate from the complete pinned environment with explicit permission:

```bash
python -B -m backend.api.build_contract
```

This produces `openapi.json`, `implemented-openapi.json`, `examples.json`,
`ENDPOINTS.md` and `PLANNED_ENDPOINTS.md` together. Do not hand-edit generated
schemas, relax constraints, remove the BIGINT/null-default OpenAPI adapter, or
invent hashes for ungenerated artifacts.

## API and frontend flow

| Operation ID | Method/path | Success | Other declared statuses |
| --- | --- | --- | --- |
| startExport | POST /api/v1/exports | 202 ExportStarted | 404, 409, 422, 500, 503 |
| listExports | GET /api/v1/exports | 200 ExportPage | 422, 500, 503 |
| getExport | GET /api/v1/exports/{export_id} | 200 Export | 404, 422, 500, 503 |
| getExportManifest | GET /api/v1/exports/{export_id}/manifest | 200 ExportManifest | 404, 409, 422, 500, 503 |
| downloadExport | GET /api/v1/exports/{export_id}/download | 200 application/zip | 404, 409, 422, 500, 503 |

Creation requires exactly the public fields `audit_id`, `format="penn_fudan"`,
`selection="accepted_only"|"all_reviewed"`, and boolean `include_images`.
Unknown fields and booleans in place of integers are not accepted. Clients
cannot supply output paths, revisions, ZIP member names or resource policies.

POST returns only after the job, export row and private frozen input commit.
Poll the returned existing `/api/v1/jobs/{job_id}` and export metadata. Use the
existing `/api/v1/jobs/{job_id}/cancel` endpoint for cancellation. Failed and
cancelled resources remain readable; their states are not HTTP polling errors.
Counts, `download_url` and `manifest_url` remain null until completed.

Listing is newest created time first with descending export ID as a stable tie
break. Defaults are limit 20, offset 0; limit 1–100 and offset 0–9223372036854775807.
Unknown/duplicate query fields are invalid. Empty and past-the-end pages are 200.
Metadata/list reads do not inspect files, start workers or recover jobs.
Both metadata and listing validate frozen input, attempt allocation and coupled
job/resource state within their read-only snapshot. Completed progress/counts
must agree. Listing validates each bounded page entry rather than publishing a
weaker unchecked projection; no asset-file reads are introduced.

## Selection semantics and snapshot cutoff

- Only terminal source audits (`completed`, `failed`, `cancelled`) are eligible.
  Active audits return 409 `AUDIT_NOT_READY`.
- Legacy mutable-root audits return 409 `AUDIT_NOT_EXPORTABLE`; the server does
  not recapture current files to make them appear reproducible.
- `accepted_only` includes explicit current sample acceptance.
- `all_reviewed` includes accepted **and rejected** samples. Rejected entries
  remain rejected. This is a diagnostic export, not an all-training-ready set.
- Pending (including explicitly reopened) and needs-correction samples are
  excluded. Finding acceptance, confidence and no-issue results do not substitute
  for a human sample decision. Explicit acceptance of failed samples is retained.

The entire ordered pin/manifest/current-review view is collected in one read-only
repeatable-read transaction, followed by a separate atomic enqueue transaction.
The visibility of that selection snapshot is the cutoff, not HTTP acknowledgement.
`selected_at` records when that snapshot was observed; it is not a timestamp-based
review filter and does not mean every review committed before that wall-clock
instant is visible. Review revision and audited sample revision are separate.
No saved review is pending review at revision 0 without attribution.

Every audit sample gets one entry, including exclusions. Exact audit manifest
associations and the audit's recorded rulebook ID/version are frozen. New heads,
reopened reviews, new findings or edited rulebooks do not change existing exports.
Request a new export for a new selection. Current rule confirmation is not
re-required and rules are not reevaluated.

Required captured roles are annotation text and raw mask, plus original image
only when requested. Selection-eligible samples with recorded missing/unreadable
required roles have explicit stable exclusion reasons. A broken manifest
association is a storage failure, not a silent exclusion. If an available file
later disappears, grows, changes digest or exceeds bounds, the entire attempt
fails; membership never changes to hide source damage.

## Archive and byte preservation

Included files are `Annotation/{sample_id}.txt`, `PedMasks/{sample_id}_mask.png`
and optionally `PNGImages/{sample_id}.png`, with UTF-8 `manifest.json` and
`README.txt` at the root. Empty eligible selections are valid manifest/README-only
archives. No data files are fabricated and empty progress is coherent with Job.

Source bytes are copied without parsing or reencoding: malformed captured text,
one-based inclusive coordinates, palette/high-valued mask IDs and original image
bytes are preserved. Layout does not certify source-content validity.
Selected IDs that cannot safely and unambiguously form portable archive names
fail the attempt rather than being silently renamed. ZIP ordering and metadata
are fixed within the implementation; binary identity across different zlib or
Python versions is not promised.

## Private roots and resource policy

`EXPORT_ROOT` defaults to `backend/data/exports`, independent of the caller's cwd.
It must be disjoint from immutable assets and dataset import storage. Configuration
loading does not create directories. The worker creates an owner-only export root
and unique attempt directory; an existing nonprivate root is rejected rather than
silently chmodded. HTTP responses and manifests never expose private keys/paths.

Filesystem helpers explicitly require POSIX directory-relative `O_NOFOLLOW`,
`O_DIRECTORY` and `O_NONBLOCK` behavior. This handoff does not provide a native
Windows filesystem adapter. Use an appropriate Linux/POSIX backend environment;
deployment permissions and root stability remain operator responsibilities.

| Setting | Default | Purpose |
| --- | ---: | --- |
| EXPORT_MAX_FILE_BYTES | 67108864 | Actual bytes per source role |
| EXPORT_MAX_INPUT_BYTES | 2147483648 | Actual uncompressed archive input, including manifest/README |
| EXPORT_MAX_ARCHIVE_BYTES | 2147483648 | Output high-water length, checked before writes |
| EXPORT_MAX_SAMPLES | 3000 | Frozen audit entries, not just inclusions |
| EXPORT_MAX_METADATA_BYTES | 16777216 | Frozen input and published manifest bound |
| EXPORT_MAX_SECONDS | 900 | Original hard attempt deadline |
| EXPORT_LEASE_SECONDS | 30 | Owned lease, capped at the original deadline |
| EXPORT_POLL_SECONDS | 1 | Idle worker queue polling |

These are server resource limits, not dataset-quality or accuracy thresholds.
Accepted policy is frozen across restarts. Storage roots must remain stable or
be migrated by a deliberate operator process; there is no automatic storage mover.

## Worker, cancellation and recovery

Only an explicit sequential export worker claims export jobs. Parent owns all
SQL; a spawned bounded child generates files with no live DB connection. Bounded
atomic status files avoid huge ZIP IPC payloads and blocked queue drains.
The parent permission watchdog operates even during blocked SQL. Child checks
cancellation/time during chunk copying/compression and has a parent-death/deadline
watchdog. The process is isolated for termination, not a hostile-code sandbox.

Lock order is job → export → private input. Every owned mutation checks stored
identity, claim token, attempt, lifecycle and server time after acquiring locks.
Generic creation/claim/mutation/recovery cannot execute managed exports.
Public cancellation dispatch uses the already locked transaction:
queued → cancelled, running → cancelling, repeated cancelling is idempotent,
and terminal cancellation requests return existing `JOB_ALREADY_FINISHED`.
A committed cancelling state wins over later publication.

One processing attempt only. Stale running work becomes failed; stale cancelling
work becomes cancelled. No replay, automatic retry, resume, range download,
public expiring links, cloud storage or global garbage collection is added.
On local dependency loss the worker stops rather than speculatively mutating SQL;
fresh stale recovery terminalizes expired attempts. No unfinished files are served.
Read boundaries reject malformed active claims and inactive retained ownership.
Recovery alone may clear an abandoned missing/expired lease after locked identity,
frozen input and original attempt timing are validated; it grants no authority to
resume generation. Missing/corrupt provenance is not fabricated to enable recovery.
Cleanup first revokes child permission and still attempts reaping if watcher join
fails. Secondary cleanup/logging failures do not replace an unknown-commit or
ownership-loss exception. Keep worker diagnostic logs private to operators.

## Publication, unresolved outcomes and cleanup

ZIP and manifest are closed and fsynced, then verified before a single SQL
transaction publishes artifact keys, size/digest, completed export counts and job
result (`kind="export"`, metadata href, null version). Filesystem and SQL are not
one atomic system. An unknown commit outcome stops processing and retains possible
outputs. A lost POST response is not permission to retry automatically: reconcile
list/detail/job reads and operator diagnostics before considering resubmission.

The implementation deliberately performs no automatic attempt cleanup, including
known failures. Private attempt-owned partial files, input/status files and
unregistered orphans can remain. Operators must reconcile durable registrations
and active claims before deleting only an identified inactive unpublished attempt;
never delete a whole configured root or source/import assets. No GC tool is supplied.

## Download integrity and public errors

Only completed registered artifacts can be served. Verification is bounded and
uses the same ordinary-file descriptor later streamed; the path is not reopened
for delivery. Response lifetime closes it on completion, disconnect or cancellation.
Download headers use a constant-safe attachment filename, `application/zip`,
`nosniff`, no-store and the verified byte length. A disk/network failure after
headers aborts delivery; it cannot become a fresh JSON error response.

Errors before headers are safe envelopes: 404 `NOT_FOUND`; 409 `AUDIT_NOT_READY`,
`AUDIT_NOT_EXPORTABLE` or `EXPORT_NOT_READY`; 422 `VALIDATION_ERROR`; 503
`DEPENDENCY_UNAVAILABLE`; unexpected programming failures remain 500
`INTERNAL_ERROR`. Internal paths, storage keys, SQL, credentials and exception text
are not public messages. Worker error codes are an explicit finite allowlist.

The prototype's existing reviewer attribution is not an authentication boundary.
This feature does not add authorization or permission to redistribute source data;
operators must respect dataset licenses and existing local deployment limits.
