# Start and browse audits

`POST /api/v1/audits` and `GET /api/v1/audits` are implemented. Audit creation
connects the imported catalogue to the existing batch engine. It queues work;
it does not load images, run checks or contact Ollama in the HTTP request.

Every audit response includes `name` (for example `Audit 014`, or `Audit 014 R01`
for a recheck) and `rulebook_name` (`Rules 003`, shown with `rulebook_version` as
`Rules 003 v3`). Rename an audit with `PUT /api/v1/audits/{audit_id}/title`.
Requests still use `audit_id`. See [DISPLAY_NAMES.md](DISPLAY_NAMES.md).

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

## Audit-wide finding list

`GET /api/v1/audits/{audit_id}/findings` (`listAuditFindings`) lists the machine
findings of one audit across all of its samples, so a reviewer can scan every
`fail`, `unsure` or `unavailable` result without opening samples one by one. It
returns `FindingPage`: `items`, `limit`, `offset`, `total` and nullable `next_offset`.

```http
GET /api/v1/audits/audit_demo/findings?outcome=fail&limit=20&offset=0
```

| Query | Meaning |
| --- | --- |
| `limit` | 1 to 100, default 20. |
| `offset` | 0 up to 9223372036854775807, default 0. |
| `sample_id` | Optional. Only findings of that sample; blank values are rejected with 422. |
| `outcome` | Optional: `pass`, `fail`, `unsure` or `unavailable`. |

- Omit both filters to include every outcome and every review resolution. The two
  filters combine with AND. Neither filters by the reviewer's decision.
- Order is ascending `finding_id` (text order, not creation order), so unchanged data
  pages in the same order with no duplicates or gaps.
- `total` counts every matching finding before paging. `next_offset` is
  `offset + items.length` only when more matching findings remain; otherwise it is
  null, including for an empty page, an offset past the end and the final page.
  Follow it until it is null.
- An unknown `audit_id` returns 404. An existing audit with no findings, or a
  `sample_id` that matches nothing (including a sample that is not in this audit),
  returns 200 with an empty page, so an empty list never means the audit is missing.
- Each item is the stored finding: its pinned `sample_revision`, rulebook version,
  description, parameters, origin, source sentence, evidence and machine `outcome`,
  exactly as saved. `review` is the latest human decision, or `pending` when nobody
  has reviewed the finding. Machine evidence does not change when a reviewer decides.
- The audit check, the total, the page and the latest reviews come from one database
  snapshot. Reading starts no worker, runs no inference, reads no asset files and
  changes nothing. Evidence links are the ones stored with each finding; this route
  creates none.
- Errors use the normal envelope: 404 (audit not found), 422 (invalid query), 503
  (storage unavailable) and 500. There is no 409 for this read.
- Accepting a finding confirms the tool's conclusion for that finding, not the sample.
  Use `GET /audits/{audit_id}/samples/{sample_id}` for one sample's measurements,
  assets, failures and sample decision.

## Detection and matching evidence

When person detection is enabled on the server, each sample processed by a **new** audit gets a
`detection_evidence` entry in its stored measurements. Read it from
`GET /audits/{audit_id}/samples/{sample_id}` under `measurements.detection_evidence`; it is returned
exactly as stored. Reading never loads a model or recomputes anything, and there is no separate
detection endpoint. Audits accepted before this feature have no `detection_evidence` key. Treat a
missing key as "not recorded", never as "no people found".

The server settings are frozen when an audit is accepted. They are not part of any request or
response: clients send no file paths or model settings. A recheck reuses the parent audit's frozen
settings.

### Reading `status`

| `status` | Meaning |
| --- | --- |
| `disabled` | Detection was switched off when the audit was accepted. Only `configuration` is present. |
| `image_unavailable` | The sample's image could not be read, so detection did not run. |
| `compared` | Detection and comparison completed. |
| `compared_incomplete` | Compared, but some records were invalid; see `matching.invalid_records`. |
| `comparison_unavailable` | Detection ran, but the annotations could not be used, so nothing was compared. Detections are kept. |
| `detector_unavailable` | The checkpoint was missing, unusable or did not match the frozen SHA-256; `reason` says which. |
| `inference_failed` | The detector ran but failed. |

An empty `detector_output.detections` list on a `compared` result means the model ran and found
nobody. That is different from an unavailable status. For `comparison_unavailable`,
`detector_unavailable` and `inference_failed` the sample is also marked incomplete:
`summary.incomplete` is true and `measurements.errors` has an entry with `stage` `detection` and a
`reason` beginning `detection_`. Unrelated passing checks therefore do not read as a clean result.

### What the fields mean

- `detector_identity`: `architecture`, `checkpoint_sha256` (of the file actually used) and
  `expected_checkpoint_sha256` (frozen when the audit was accepted).
- `detector_output.detections`: boxes in original-image pixels, zero-based, with `x_min`, `y_min`,
  `x_max`, `y_max` as pixel edges, and a `score`. This is not the one-based inclusive convention
  annotations use; matching converts annotations before comparing. The detector returns every
  candidate it produces, and `min_confidence` is applied only during matching.
- `matching`: `matches`, `ambiguous_groups` (detections and annotations that overlap enough to be
  confused; the backend never picks one), `unmatched_detections`, `unmatched_annotations`,
  `confidence_filtered_detections` (below `min_confidence`, not compared) and `invalid_records`.
  Detection references are positions in `detector_output.detections`. Annotation references are
  positions in the original annotation list, and duplicate `object_id` values stay separate records.
- `person_labels` and `excluded_annotations`: only annotations whose label is in `person_labels` are
  compared. Others are listed as excluded, which is not an error.
- `min_confidence`, `min_iou`, `preprocessing`, `matching_policy` and the coordinate convention
  fields record how to interpret the result.

### Why unmatched is not an error

An unmatched detection may be a missing annotation, a duplicate detection or a false positive. An
unmatched annotation may be a detector miss or an annotation problem. Detector output is fallible
evidence. The backend creates no finding from it and does not change a sample's review. Final sample
review stays with the reviewer.

Illustrative, hand-written and abbreviated; not program output:

```json
{
  "evidence_schema_version": 1,
  "status": "compared",
  "reason": null,
  "image_width": 559, "image_height": 536,
  "min_confidence": 0.5, "min_iou": 0.5,
  "person_labels": ["PASperson"],
  "detector_identity": {
    "architecture": "fasterrcnn_mobilenet_v3_large_320_fpn",
    "checkpoint_source": "local_checkpoint",
    "checkpoint_sha256": "<64 hex characters>",
    "expected_checkpoint_sha256": "<same 64 hex characters>"
  },
  "detector_output": {
    "detections": [{"x_min": 162.7, "y_min": 177.3, "x_max": 303.5, "y_max": 432.2, "score": 0.99}]
  },
  "matching": {
    "matches": [{"detection": {"index": 0, "score": 0.99},
                 "annotation": {"index": 0, "object_id": 1, "label": "PASperson"}, "iou": 0.9}],
    "ambiguous_groups": [], "unmatched_detections": [], "unmatched_annotations": [],
    "confidence_filtered_detections": [], "invalid_records": [], "comparison_complete": true
  },
  "annotations_available": true, "annotation_count": 1, "excluded_annotations": []
}
```

```json
{
  "evidence_schema_version": 1,
  "status": "disabled",
  "configuration": {"enabled": false}
}
```

## Verification status

Coverage is authored for request validation, safe HTTP errors, catalogue-head
selection, durable pins, atomic rollback, public pagination, contracts and worker
dispatch. Tests and PostgreSQL scenarios have not been run during implementation.
Generated OpenAPI and examples are source-level contracts, not runtime test proof.
The audit-wide finding list adds PostgreSQL repository, HTTP and contract tests that
are authored and have not been run.
