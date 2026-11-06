# Pedestrian-box enclosure check (opt-in)

Rule semantics: **each annotation box must enclose a visible pedestrian; a box entirely on background is a
violation.** A partly visible but identifiable pedestrian inside the box counts. A person entirely outside the box
does not. The check does not require a whole body, walking motion, box tightness, a visibility percentage or
annotation completeness. It is a model proposal for human review, not proof of accuracy.

## How to use it

1. Confirm a rule whose check is `pedestrian_box_enclosure` (explicit name; guideline text is never matched).
2. Enable the settings below and start a snapshot audit. The pin is frozen when the audit is accepted, so queued,
   retried and rechecked work uses that pin, not later defaults. A disabled parent remains disabled on recheck.
3. Without the settings, or with `ENCLOSURE_ENABLED` unset, the rule stays unavailable (`model_execution_unavailable`).
4. An activated feedback policy may be selected for this exact rulebook version and dataset. Its reviewed
   enclosure execution pin must match the enabled audit configuration. Other policies are rejected with
   `POLICY_INCOMPATIBLE`; a policy is never silently ignored or substituted.

## Feedback with marked box context

The feedback page now accepts attributed binary reviews of **single-box** enclosure samples.
An agreement retains the model answer; an override uses the opposite binary answer and its human reason.
The original proposal, review revision, execution pin, source hashes and box provenance remain frozen.
A review of a multi-box sample does not identify the correct answer for each box, so those samples are
not offered as training examples. This restriction does not apply to evaluation targets or future audits:
both still judge every annotation box and use the existing sample aggregation.

Feedback retains its 600-second maximum per model request; longer enclosure request pins are not
eligible for this workflow. Select up to four examples from one exact rule and dataset. Create a candidate, then freeze separate
human-adjudicated samples from that same scope. Image hashes and sample identities prevent overlap,
including renamed or revised copies. Evaluation uses the reviewed enclosure pin for both arms. The
baseline calls the existing request builder unchanged. The candidate appends all selected single-box
marked views and their human answers; target pixels, framing, identity schema, options and aggregation
stay the same. No original source coordinates or held-out answers enter the model request. Context that
cannot fit is rejected rather than truncated. The comparison worker retains its one-hour job deadline,
ownership fencing and cancellation supervision; keep live prototype evaluations small.

Each comparison stores both arms' complete per-box results and policy provenance. Metrics remain
sample-by-rule metrics, not per-box accuracy: any verified failing target box fails that sample.
Activation remains an explicit reviewer choice after a completed comparison. It does not promise an
accuracy improvement, and an equal or regressed candidate should not be described as an improvement.

New audits using the policy freeze its examples and execution. Their findings retain every box result
plus the policy ID and snapshot hash. Rechecks preserve that frozen configuration and start with pending
human reviews. Use a rulebook with enclosure and optional deterministic rules; a combined generic
`annotation_guideline` and enclosure policy runtime is intentionally unsupported.

The generic guideline feedback path and old saved snapshots retain their original serialization.
No database migration or backfill is needed. Request context and timing differ when examples are added;
this new candidate path requires owner-run tests and fresh real-model evaluation. Earlier context-method
validation establishes the baseline method, not the accuracy of this feedback extension.

| Setting | Meaning |
| --- | --- |
| `ENCLOSURE_ENABLED` | `true` to opt in. Default off. |
| `ENCLOSURE_MODEL`, `ENCLOSURE_MODEL_DIGEST` | Required. No default model, no download. The digest is checked before and after every call. |
| `ENCLOSURE_SAMPLE_BUDGET_SECONDS` | Required. Must be at most `AUDIT_SAMPLE_MAX_SECONDS` minus 30. |
| `ENCLOSURE_HOST` | Loopback Ollama URL. Default `http://127.0.0.1:11434`. |
| `ENCLOSURE_NUM_CTX` / `NUM_PREDICT` | 16384 / 2048. |
| `ENCLOSURE_NUM_THREAD` | Unset keeps automatic selection. |
| `ENCLOSURE_REQUEST_TIMEOUT_SECONDS` | 600; never above the sample budget. |
| `ENCLOSURE_MAX_BOXES` | 12 (at most 32). More boxes make the sample unavailable; the list is never truncated. |
| `ENCLOSURE_MAX_REQUEST_BYTES`, `MAX_RESPONSE_BYTES`, `KEEP_ALIVE_SECONDS` | Finite bounds. |

## What it stores

One sample-level finding per enclosure rule, pending human review. `evidence.measurements.enclosure` holds the pin,
the source image and annotation hashes, and one record per box (annotation index, object ID, original box, view hash
and size, source and target windows, request hash, outcome, reason, timing, verification). Aggregation: any verified
failing box fails the sample; otherwise any unavailable box; otherwise any unsure box; only all verified passes pass.
Zero boxes, invalid geometry, too many boxes or unreadable evidence are explicit unavailable results, never a pass.
Original annotations, deterministic measurements and reviews are never changed. Views are re-renderable from the
pinned assets and recorded hashes; no mutable dataset path is used.

## Latency and bounds

One sequential request per box. On a 16 GB Windows laptop one request took about 130 s; on the Mac validation about
85 s. The default 900 s sample deadline therefore covers roughly 6 boxes (Windows) to 10 (Mac). This is an estimate, not a guaranteed capacity: collection, model verification and storage also take time.
The single enclosure budget starts before evidence preparation and is shared by all rules and boxes.
It is capped by the persisted sample deadline minus the 30-second finalization margin.
Boxes not started within it are recorded `sample_budget_exhausted`. A timed-out in-flight call is
unavailable (`request_timeout`); an already verified failing box still makes the finding fail. Raise
`AUDIT_SAMPLE_MAX_SECONDS` deliberately for deployments that enable the check; do not raise it globally to make a
smoke test pass.

## Supervision and transport

Production uses the existing spawned sample process. Its parent continues checking ownership,
cancellation and the hard sample deadline while model requests run, terminating the child when
necessary. The synchronous test seam checks ownership between boxes but cannot forcibly interrupt
a blocked call. HTTP operations use the remaining call allowance, and expired responses are rejected;
HTTPX timeouts alone are not a hard wall-clock interrupt for every transport phase. The parent process
remains the hard-stop authority. No new retry or background-worker mechanism is introduced.

The enclosure-specific client is retained for this prototype: its bound box response differs from
the generic guideline response. Shared immutable evidence loading and the existing supervisor are
reused. Refactoring both model clients would broaden this change; both must retain digest, context,
response-size and identity verification.

## Validation status

On 2026-10-23 the owner reported the corrected test suite passing, including the
new PostgreSQL regressions. No exact final test count was supplied. API contracts
were regenerated with permission. Native Windows validation is outside this pass.

The owner then ran a production smoke audit through the spawned worker and PostgreSQL
using qwen3-vl:4b-instruct, digest
`sha256:ee4b975b58c17ce268cd19d40db35d5edc64603035d2ffc1fee1968eb0947f7b`.
The saved job completed: case-03 passed (one verified passing box); case-02 failed
(one verified failing box and five verified passing boxes). All seven intended boxes
have stored verified results, and both findings remain pending human review.
Summed per-box call time was 74.09 seconds and 445.37 seconds respectively.

This verifies the production adapter and durable result path on two previously reviewed
samples. It is not a new held-out accuracy evaluation or evidence of feedback-learning
improvement. The runner read stored results through the repository; public HTTP reads
were covered by the automated regression suite, not this live-model run.
No migration is needed. Tiny pedestrians remain insufficiently validated.
