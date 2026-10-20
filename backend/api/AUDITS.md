# Start and browse audits

`POST /api/v1/audits` and `GET /api/v1/audits` are implemented. Audit creation
connects the imported catalogue to the existing batch engine. It queues work;
it does not load images, run checks or contact Ollama in the HTTP request.

## Start an audit

Import a dataset, let the dataset worker finish, and choose an explicit confirmed
rulebook version. Submit:

```http
POST /api/v1/audits
Content-Type: application/json

{
  "dataset_id": "dataset_demo",
  "rulebook_id": "rulebook_demo",
  "rulebook_version": 3,
  "sample_ids": null,
  "feedback_policy_id": null
}
```

Use actual saved IDs. Omitting `sample_ids`, or sending null, selects every sample
in catalogue order. A nonempty list selects exactly those samples in request
order. Duplicates, unknown fields, empty selections, invalid IDs and non-integer
versions are rejected. `feedback_policy_id` must be omitted or null: feedback
execution remains planned, and non-null values return 422.

The backend checks ready status and selects current sample heads from one database
snapshot. It pins those exact immutable manifests and the requested rulebook
version before saving the audit, queued job and sample rows in one transaction.
Later catalogue-head changes and newer rulebook versions cannot change that run.
The original ZIP, deleted import scratch and mutable Penn-Fudan source directories
are not needed. Available asset bytes are verified later during processing.

Ready does not mean clean: samples with malformed annotations or missing asset
roles remain eligible for auditing. Unsupported checks produce unavailable
findings. Every finding still requires human review, including a passing result.

HTTP 202 returns `AuditStarted`: `audit` is the public `AuditRun`, and `job` is
the public queued `Job`. `parent_audit_id` and `feedback_policy_id` are null for
this entry point. Private source paths, manifests and worker ownership are never
returned. The selected subset determines `sample_count` and job `progress.total`.

Safe errors use the existing `ErrorResponse` envelope and a generated request ID:

- 404 `NOT_FOUND`: dataset, selected sample or exact rulebook version is missing.
- 409 `DATASET_NOT_READY`: import has not produced a ready catalogue.
- 409 `RULEBOOK_NOT_CONFIRMED`: the selected book is not confirmed for execution.
- 422 `VALIDATION_ERROR`: invalid request fields or supported-check parameters.
- 503 `DEPENDENCY_UNAVAILABLE`: storage/configuration failure or unresolved commit.
- 500 `INTERNAL_ERROR`: unexpected internal failure.

Submission has no idempotency key. Each successful request creates a separate
audit. If the response is lost or a commit outcome is unresolved, inspect the audit
list before resubmitting; do not automatically retry the POST.

## Run the worker

Use the existing PostgreSQL and asset-storage configuration, including migration
010 from dataset import. This feature adds no migration. Start the worker from
the project root in a separate terminal:

```bash
.venv/bin/python -B -m backend.app.workers.audit
```

To recover stale audit work and process at most one eligible audit:

```bash
.venv/bin/python -B -m backend.app.workers.audit --once
```

It claims only audit jobs, processes samples sequentially through the existing
spawn-isolated supervisor, and retains existing lease, timeout, cancellation and
recovery safeguards. Import/extraction workers remain separate. It does not
download models, run migrations or start at API startup.

SIGINT/SIGTERM request shutdown after the current audit returns. For prompt
cancellation of active computation, use `POST /api/v1/jobs/{job_id}/cancel`; that
uses the existing supervised cancellation path. An interrupted process's stale
work is handled by the existing recovery logic on a later worker run.

## Browse progress and review

`GET /api/v1/audits?limit=20&offset=0` returns `AuditPage` with `items`, `limit`,
`offset`, `total` and nullable `next_offset`. All persisted lifecycle states are
included, including legacy audits. Order is descending creation time, then
descending audit ID for ties. Limit is 1–100 and offset is a nonnegative BIGINT.
An empty collection or offset beyond the end returns HTTP 200 with no items.
Count and items use one database snapshot; separate offset pages may shift when
other clients submit audits. Listing performs no processing or recovery.

Poll `GET /jobs/{job_id}`, read `GET /audits/{audit_id}`, then use the existing
sample queue/detail and finding/sample review routes. Completion is processing
status, not reviewer approval. Include apparently correct samples in review.

## Verification status

Coverage is authored for request validation, safe HTTP errors, catalogue-head
selection, durable pins, atomic rollback, public pagination, contracts and worker
dispatch. Tests and PostgreSQL scenarios have not been run during implementation.
Generated OpenAPI and examples are source-level contracts, not runtime test proof.
