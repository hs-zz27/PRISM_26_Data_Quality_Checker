# Evaluated reviewer feedback

## Availability and evidence

This source proposal adds ten feedback operations and one deliberately bounded,
sample-scoped model check, `annotation_guideline`. It is not a general learning,
training, detector, posture-classification or automatic rule-rewriting system.
New model findings, including pass findings, are always pending human review.
Finding acceptance is not sample acceptance. Corrections are not implicit truth.

The captured baseline is `1c3c33deb9235692129b7d72cbc4c6256ff0002e`.
The proposed inventory is **46 implemented / 4 planned / 50 total operations**,
subject to actual route/contract verification after application. Three system
reads and the separate audit-wide findings operation remain planned. The five
API artifacts must be regenerated together; this source handoff did not run the
generator, tests, database migrations, workers, inference or HTTP scenarios.
Source availability, integrated verification and demonstrated model improvement
are separate statuses. No production-readiness or actual improvement is claimed.

## What the model judges

A confirmed rule describes an explicit, image/annotation semantic requirement.
The check has sample scope and empty parameters. Still images do not prove
motion, hidden facts, external facts or unstated numerical thresholds. Precise
numeric rules keep their existing deterministic mappings. Unsupported checks
remain unavailable; this check is not an automatic fallback for unmapped rules.
A reviewer confirms plain-language requirements, not a programming task.

The request contains the exact confirmed rule, verified original PNG bytes and
UTF-8 annotation text with parsed objects. No resizing, re-encoding, normalization
or source mutation occurs. The private file reader bounds and verifies the same
single-asset bytes against their immutable manifest. A missing mask alone does
not prevent image/annotation judgment. Invalid/incomplete required evidence is
unavailable, never an invented pass. Ordinary measurements remain unchanged;
model proposals/provenance are separate finite finding evidence.

## Frontend workflow

1. Complete a snapshot-backed baseline audit with the configured local model.
   Inspect each proposed finding against its pinned image and annotation. Accept
   a binary pass/fail finding only when the human agrees. Rejection, unsure,
   unavailable and free-text comments are not inverted into training labels.
2. `GET /api/v1/feedback/examples?limit=20&offset=0` projects current accepted,
   verified binary model findings from terminal snapshot audits. This is a
   repeatable-read paginated metadata query, not an asset scan or approval write.
   An ID is `example_<canonical-base64url-UTF8-finding-id>.<review-revision>`.
   Reopening/review changes yield a different version or remove eligibility.
   IDs resolve directly to finding IDs; they are not row-offset identifiers.
3. `POST /api/v1/feedback/policies` with `name`, `reviewer_id`, and 1–4 distinct
   `example_ids`. The server verifies source files outside SQL, then locks the
   original findings in stable order and rechecks current review versions before
   freezing their payloads. One exact rulebook/version/rule and dataset scope is
   required. Incompatible examples are rejected, not randomly combined.
4. `POST /api/v1/feedback/ground-truth` with `reviewer_id` and 1–100 explicit
   `cases`: `audit_id`, `sample_id`, `sample_revision`, `rule_id`, `expected`
   (`pass`/`fail`) and a human `reason`. Include checked no-issue cases and missed
   errors, not only emitted findings. Absence of a finding is not a pass. The
   adjudicator must actually inspect the exact pinned evidence. Ambiguous cases
   cannot be assigned binary truth. All cases have one exact rule/dataset scope.
   Each creation yields a new immutable ID/version 1; subsequent adjudication
   creates a new snapshot ID rather than mutating an old truth resource.
5. `POST /api/v1/feedback/evaluations` with `policy_id`, `policy_version=1`,
   `ground_truth_id`, `ground_truth_version=1`, `reviewer_id`. The server checks
   disjoint logical samples/revisions, sample-name aliases and identical image
   hashes. It verifies complete context bounds before enqueue, then atomically
   saves frozen input, evaluation and queued job. The coherent immutable inputs
   are the cutoff; a later review cannot change them. No inference runs here.
6. Poll `/api/v1/jobs/{job_id}` and `/api/v1/feedback/evaluations/{evaluation_id}`.
   Browse `/api/v1/feedback/evaluations` and `/api/v1/feedback/policies`; read
   policy/truth details by ID. Collection limit is 1–100 (default 20); offset is
   nonnegative BIGINT. Ordering is newest time, then stable C-collation ID.
   Past-end pages return 200 with no items. Reads never recover/claim work.
7. Cancel with the existing `POST /api/v1/jobs/{job_id}/cancel`. Queued work is
   immediately cancelled; running work becomes cancelling and stops at bounded
   checkpoints. Completed/failed/cancelled jobs use existing terminal conflicts.
   Partial/cancelled/failed results are not completed evaluation evidence.
8. Inspect per-case actual baseline/candidate proposals, reasons, durations,
   coverage, confusion counts and nullable ratios. An actual new baseline run
   receives no policy examples. Candidate receives only frozen training examples,
   never held-out labels/reasons/reviews/correction lineage or future revisions.
9. Explicitly `POST /api/v1/feedback/policies/{policy_id}/activate` with
   `evaluation_id`, `expected_policy_version=1`, `reviewer_id`. The evaluation
   must be complete, match the exact immutable policy/truth/execution input, and
   contain verified inference without unavailable cases. No universal quality
   threshold or confidence auto-activation is invented. A reviewer may knowingly
   activate a measured regressing/inconclusive policy; show those results clearly.
   Repeating the same policy/evaluation/reviewer is idempotent; competing different
   activations conflict. Activation creates an immutable attributed decision.
10. Explicitly select `feedback_policy_id` when starting a future audit. Omitted/
    null means baseline context, never a moving global active policy. Draft,
    unknown and incompatible policies fail safely. The audit pins the activating
    evaluation's execution settings and exact policy. Linked rechecks preserve
    that pin and parent rulebook while creating fresh pending reviews. They never
    select a newer policy or copy human decisions. Existing CPU settings remain.

## Honest metric conventions

Positive = human-adjudicated rule violation (`fail`), negative = compliant (`pass`).
Each sample revision/rule is one case, not one case per annotated object.

- TP: expected fail, proposed fail. FP: expected pass, proposed fail.
- FN: expected fail, proposed pass/unsure/unavailable. TN: expected pass, proposed pass.
- Negative abstentions: expected pass, proposed unsure/unavailable; neither TN nor FP.
- Coverage = (all intended cases − unsure − unavailable) / all intended cases.
- Precision = TP/(TP+FP); recall = TP/(TP+FN); F1 = 2TP/(2TP+FP+FN).
- Zero denominators yield null, not perfect scores. Counts retain every intended
  case. Durations are measured monotonic milliseconds and reported separately.
- Aggregate and per-rule interpretation coincide because a resource has exactly
  one pinned rule scope. No unrelated rules or objects inflate sample counts.
- `metrics=null` until a full comparison is published. Missing predictions cannot
  complete. Unavailable inference makes assessment inconclusive. Higher F1 with
  nondecreasing coverage is labeled improved; lower F1/coverage is regressed;
  exact equality is equal; undefined ratios/remaining tradeoffs are inconclusive.
  This descriptive label is not an automatic activation threshold.

All delivered examples and mocked gains are synthetic plumbing fixtures. Equal,
worse, undefined, incomplete or unexecuted runs are not evidence of improvement.
Near duplicates, image crops/augmentations or semantic aliases are not reliably
identified by exact hashes; reviewers must curate genuinely independent holdouts.

## Local configuration and reproducibility limits

Keep the existing `backend/ollama.toml`, `OLLAMA_MODEL` and validated loopback-only
`OLLAMA_BASE_URL` settings. Install/select a suitable local vision model separately.
Set `FEEDBACK_MODEL_DIGEST=sha256:<64-lowercase-hex-from-local-api-tags>` only after
an owner-authorized model identity check. No model downloads/training are added.
Raw Ollama 64-hex digests are normalized to that prefixed form. Missing digest
leaves new model checks unavailable without a model dependency; deterministic-only
and legacy audits retain their old behavior.

A worker checks `/api/tags` and local `/api/show` vision capabilities before and
after each chat. Proxies and redirects are disabled, GPU is disabled, temperature
is zero; responses use strict structured prediction JSON. Digest/capability drift,
identity mismatch, invalid JSON, overflow, offline transport or incomplete output
cannot produce passing results. Hosts, private paths and credentials stay private.
The execution record states `local-server-reported-pre-post-v1`: this trusts the
local server's statements, not cryptographic attestation. It cannot prove absence
of a transient tag change between observations, model correctness, complete vision
tokenization fidelity or deterministic generations across runtimes. Validate the
specific installed model's image ordering/context behavior before relying on it.

Default finite bounds: four example images plus target; 8 MiB/image, 16 million
pixels/image, 128 KiB annotation, 64 MiB serialized request, 64 KiB response.
Model identity/capability metadata has a separate 1 MiB response bound because
Ollama includes licenses and tensor descriptions; prediction responses retain
their stricter 64 KiB bound.
Execution pins record these bounds and context/output/time settings. Output tokens
are at most 2048; request timeout at most 600 seconds. No silent annotation/rule
truncation occurs. Conservative text/image reservations are checked before job
acceptance; worker prompt-count checks reject unverified/overflow context. These
reservations are not a proof of every model's actual multimodal tokenization.
Oversized required context is rejected with `CONTEXT_NOT_REPRESENTABLE`.

Comparison jobs use one sequential attempt, 30-second server leases, 5-second
heartbeats, a 25-second independent local watchdog and a fixed 3600-second hard
job deadline. Per-case supervision is at most min(1200, 2×request-timeout+60)
seconds including both arms. Child processes receive serializable pins and asset
settings, no SQL connection/HTTP client; parent owns all SQL. IPC result size is
32 KiB. Cancellation and lease/deadline loss stop generation. Locks use job →
evaluation → policy/activation; server time is checked after lock waits and SQL
publication fences recheck ownership. Stale work fails or cancels; no automatic
retry, transparent replay, resume, training or global execution framework is added.
The operator must explicitly submit a new comparison after a known failed attempt.

## Commands for later authorized local execution

Run from the actual repository root using its existing Python 3.12 environment.
These commands were **not run by this handoff**. Confirm migration 013 is the next
available number before applying; never overwrite/renumber applied migrations.

```bash
.venv/bin/python -B -m backend.app.storage.database
.venv/bin/python -B -m backend.api.build_contract
.venv/bin/python -B -m pytest backend/tests/test_feedback_models.py backend/tests/test_feedback_inference.py backend/tests/test_feedback_metrics.py backend/tests/test_feedback_api_contract.py -q --tb=short -p no:cacheprovider
```
Local agent: after the owner's execution authorization, run these exact commands
from the repository root. The generator writes all five artifacts together; keep
its real outputs. Report each exit status and failure; do not claim these ran here.

Set an isolated `PRISM_TEST_DATABASE_URL` before database tests; fixtures create
and drop their own schema. Never point destructive test fixtures at shared data.

```bash
.venv/bin/python -B -m pytest backend/tests/test_feedback_storage.py backend/tests/test_feedback_repository.py backend/tests/test_feedback_worker.py -q --tb=short -p no:cacheprovider
.venv/bin/python -B -m pytest backend/tests -q --tb=short -p no:cacheprovider
.venv/bin/python -B -m backend.app.workers.feedback --once
.venv/bin/python -B -m backend.app.workers.audit --once
```
Local agent: run only against the owner's chosen local environment and isolated
test database after authorization. Worker commands process real queued work;
tests use synthetic assets and local protocol stubs, not installed-model accuracy.
Normal queue loops omit `--once`. Starting the API does not start any worker.

If a response/commit acknowledgement is lost, do not automatically repeat POST.
Reconcile evaluation/policy/truth lists/detail/jobs; ground truth is resolved by its
returned ID, and unresolved lost-create truth acknowledgements require operator
inspection because no truth collection endpoint exists in this ten-route contract.
Known rollback and unknown commit are distinct. No idempotency system is added.
Errors are safe `NOT_FOUND`, `FEEDBACK_NOT_READY`, `FEEDBACK_CONFLICT`,
`EVALUATION_OVERLAP`, `POLICY_INCOMPATIBLE`, `CONTEXT_NOT_REPRESENTABLE`,
`VALIDATION_ERROR`, `DEPENDENCY_UNAVAILABLE`, or `INTERNAL_ERROR` envelopes.

## Owner-run installed-model smoke gate

After migrations, contract regeneration, mocked tests and PostgreSQL regressions:
1. Verify the configured local vision model/digest and its image attachment order,
   structured JSON capability and context limits without downloading anything.
2. Import a small owner-approved dataset; confirm one explicit visual guideline.
3. Run an explicit baseline audit, inspect its real image/annotation inputs and
   pending proposals; accept genuinely correct binary proposals as examples.
4. Explicitly adjudicate independent held-out compliant, violating and ambiguous
   candidates. Exclude unresolved ambiguity from binary truth rather than invent
   labels. Include missed violations/no-issue cases in the declared comparison.
5. Freeze a candidate and truth, enqueue a comparison, run the feedback worker and
   inspect saved per-case real predictions/durations and reconstructed counts.
6. Exercise queued/running cancellation, offline model, digest replacement,
   damaged immutable evidence and context overflow. None may appear as success.
7. Only then consider explicit activation. Submit a policy-bearing audit and a
   linked recheck; confirm pins and fresh pending reviews. Record actual outcomes,
   including equal/regressing/inconclusive results. Never report a mock gain as
   an observed installed-model improvement.

Authentication is still a local-prototype limitation: reviewer_id is attribution,
not verified identity. This feature does not implement multi-tenant authorization,
cryptographic model attestation, training, broad reliability or detector integration.
