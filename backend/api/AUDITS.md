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

## Person coverage (`person_coverage`)

A measured rule for "the annotation must cover the whole person, including head and feet, without large extra
background". It needs no vision chat model: a local Mask R-CNN (`maskrcnn_resnet50_fpn_v2`, COCO weights) finds
each person's outline in the **photo only**, and plain code compares every supplied box, Penn-Fudan mask or
polygon with it.

- Parameters: `min_tolerance_px` (default 8) and `tolerance_fraction` (default 0.04). Tolerance
  T = max(`min_tolerance_px`, `tolerance_fraction` x the detected person's height).
- Box: **fail** when the person extends past a side by more than T (`missing`, for example the head) or the box
  extends past the person by more than T (`extra` background).
- Mask or polygon: **fail** when a missing or extra region thicker than about T and at least 2 x T^2 pixels
  remains. A detected backpack, umbrella, handbag or suitcase touching the person may be included.
- One finding per annotated object. `reason` and `evidence.measurements.result.problems` say where, for example
  `box: head/top of the person is outside the annotation (45 px)`; `result.geometries[].regions[].box` gives the
  region in the annotation's own coordinates.
- No matching person: **unsure** (`no_matching_person`, `no_person_detected`). Model file missing or failing:
  **unavailable** (`person_segmenter_unavailable`). Neither can produce a pass.
- Runs inside the sample worker only when the rulebook contains the rule (about 1-3 s per image on CPU).
- Setup: put `maskrcnn_resnet50_fpn_v2_coco-73cbd019.pth` in `backend/data/models/` (mounted read-only at
  `/models`), or set `PERSON_SEGMENTER_CHECKPOINT`. Nothing is downloaded at audit time.
- Offline benchmark (synthetic errors on human outlines; Penn-Fudan for tuning, COCO val2017 held out):
  93.1% accuracy and 98.5% of errors caught on COCO. See `.cache/validation/rcnn-benchmark-20261106/REPORT.md`.

## Every person annotated (`unannotated_people`)

A measured rule for "every clearly visible person must have an annotation". The visual chat model cannot judge
this at all: it is asked about one annotation at a time, so a person with no annotation is never in front of it.
This check uses the same Mask R-CNN run as `person_coverage` (one run per image when a rulebook has both).

- Each annotation claims the detected person it matches best (its mask or polygon by mask overlap, otherwise its
  box; IoU at least 0.3). A detected person that no annotation claims is **missing an annotation** when it lies
  mostly outside every annotation's outline (or box, when there is no outline), is at least
  `min_person_height_px` tall (default 40) and was found with at least `min_score` certainty (default 0.9).
- One finding per image (`object_id` null). **fail** names each person and where they are, for example
  `1 person has no annotation: at [110, 84, 213, 337] (253 px tall, 100% sure)`; `result.unannotated[].box` is in
  the annotation's own coordinates. People not counted (inside another annotation, too small, low score) are
  counted in `result.not_counted`, not reported.
- Model file missing or failing: **unavailable** (`person_segmenter_unavailable`), never a pass.
- Measured on 282 COCO val2017 photos with every COCO person annotation supplied: 10 photos (4%) were flagged,
  either for a person COCO left out or for a person the model invented. With one annotation removed at a time,
  91% of people at least 100 px tall and 53% of people 40-100 px tall were reported. See
  `.cache/validation/missing-person-20261109/`.

## Verification status

Coverage is authored for request validation, safe HTTP errors, catalogue-head
selection, durable pins, atomic rollback, public pagination, contracts and worker
dispatch. Tests and PostgreSQL scenarios have not been run during implementation.
Generated OpenAPI and examples are source-level contracts, not runtime test proof.
The audit-wide finding list adds PostgreSQL repository, HTTP and contract tests that
are authored and have not been run.
