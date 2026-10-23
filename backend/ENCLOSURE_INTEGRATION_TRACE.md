# Pedestrian-box enclosure check: integration trace and open decisions

Status: the check is wired end to end behind an opt-in pin. Original offline results are reported below; the owner subsequently reported the correction suite passing. See
[ENCLOSURE_CHECK.md](ENCLOSURE_CHECK.md) for use. Remaining gates are listed in section 4. The correction pass retains the scoped client and policy rejection; validation gates remain explicit.

Basis. Reference method: patches `9b4878d` to `5e45ca0`. Production: current `origin/main` as exported in
`main_current.zip`; every interface below was re-checked against it before editing.

## 1. How a model check runs today (annotation_guideline)

1. Acceptance: `workflow_repository._start_audit_locked` calls `services/feedback.prepare_runtime`.
   It looks for confirmed `annotation_guideline` rules. With `FEEDBACK_MODEL_DIGEST` set (read from
   the root `.env` via dotenv) it freezes `FeedbackRuntime(execution=ExecutionPin)` into the request;
   otherwise the request is unchanged. A `feedback_policy_id` with no such rule raises `POLICY_INCOMPATIBLE`.
2. Durable request: `models/audit.BatchAuditRequest` stores `feedback_runtime` as JSON.
3. Spawn: `batch_audit` passes the runtime to `sample_worker.run_sample_child`, which materializes the
   immutable snapshot, runs the collector and `evaluator`, then `feedback.augment_evaluation`.
4. Evaluation: `evaluation._evaluate_sample_rule` returns a fixed `unavailable` /
   `model_execution_unavailable` finding for `annotation_guideline`; augmentation then replaces it with
   `GuidelineClient.predict(manifest, target, examples, asset_settings)`.
5. Evidence reader: `feedback_evidence.evidence(manifest, limits, asset_settings)` returns verified image
   bytes and parsed annotations from the immutable snapshot.
6. Finding: `evaluation._make_finding` stores the proposal under `evidence.measurements`; findings stay pending.
7. Reads and rechecks: sample detail, the findings list and rechecks all read stored findings; a recheck clones the
   stored request, so a frozen pin travels with it.

## 2. Planned flow for `pedestrian_box_enclosure`

Mirror steps 1 to 7 with a separate pin; nothing in the feedback path changes.

| Step | File | Change |
| --- | --- | --- |
| Pin and response models | `models/enclosure.py` | **Done.** Frozen pin, runtime, response schema, aggregation. |
| Rendering | `services/enclosure_views.py` | **Done.** Byte-identical to the reference on 227 views. |
| Prompts and request | `services/enclosure_prompt.py` | **Done.** System prompt equals the production constant. |
| Explicit configuration | `core/enclosure.py` | New. `ENCLOSURE_*` settings; absent means unavailable. |
| Client | `inference/enclosure_client.py` | New, sequential, same safeguards (see C4). |
| Per-sample step | `services/enclosure.py` | New `augment_enclosure`, called after `augment_evaluation`. |
| Request field | `models/audit.py` | Add `enclosure_runtime: EnclosureRuntime \| None = None` (old records decode). |
| Check identity | `checks/registry.py` | Add `pedestrian_box_enclosure`, sample scope, no parameters. |
| Placeholder | `services/evaluation.py` | Same fixed `model_execution_unavailable` finding as the guideline check. |
| Acceptance | `workflow_repository`, `services/enclosure.py` | Freeze the pin; decide policy handling (C2). |
| Worker and batch | `sample_worker.py`, `batch_audit.py` | Pass the runtime to the child. |
| Docs and contract | `api/*`, `README` | After owner approval (C5). |

Each finding is one sample-level finding. Its evidence holds the pin, source hashes and one record per box:
annotation index, object ID, original box, view hash, source and target windows, outcome, reason, timing and
verification. All boxes are present, including unavailable ones. No migration appears to be needed (request and
evidence are JSON); confirm against current `main`.

## 3. Decisions taken (defaults, unconfirmed by the owner)

- C1 time budget: a pinned `sample_budget_seconds`, validated against `AUDIT_SAMPLE_MAX_SECONDS` minus 30; unsent boxes are
  recorded `sample_budget_exhausted`; more than `max_boxes` makes the sample unavailable. The global deadline is unchanged.
- C2 policy: a feedback policy on a book using the check is rejected with `POLICY_INCOMPATIBLE`.
- C3 configuration: explicit `ENCLOSURE_*` settings, no default model or digest, absent means unavailable.
- C4 client: a second small client (`inference/enclosure_client.py`) with the same safeguards; `annotation_guideline.py` is untouched.
- C5 contract: the registry entry changes `GET /capabilities`; regeneration was completed in the correction pass with owner approval (see ENCLOSURE_CHECK.md).

## 4. Original author validation report (before corrections)

New: `models/enclosure.py`, `core/enclosure.py`, `inference/enclosure_client.py`, `services/enclosure.py`,
`services/enclosure_views.py`, `services/enclosure_prompt.py`, tests `test_enclosure_core.py` and
`test_enclosure_integration.py` (99 tests, passing on Linux). Edited by script: `models/audit.py`, `checks/registry.py`,
`services/evaluation.py`, `services/sample_worker.py`, `services/batch_audit.py`, `storage/workflow_repository.py`.
Checks run: the renderer reproduces the reference PNG bytes on 227 synthetic views; 60 existing test files near the changed
code gave 1432 passed, 264 skipped (PostgreSQL), and 6 failures that are exactly the generated-artifact freshness tests,
which pass after regeneration in a throwaway copy.
Not run: a real model, PostgreSQL, spawn on Windows, revision-aware rechecks, public read responses, contract generation in the
repository. Not production-ready; a real-model smoke check is required.

## 5. Completion corrections

- Rechecks explicitly preserve the original enclosure runtime, including `None`; new audits alone load current settings.
- All enclosure rules share one budget, including evidence preparation, bounded by the remaining persisted sample deadline.
- Metadata verification and chat consume one decreasing call allowance; expired proposals are discarded.
- Existing parent supervision remains responsible for cancelling or killing blocked processing. The synchronous seam checks ownership between boxes.
- `.env.example` and API guidance now describe the opt-in behavior. Contracts were regenerated with owner approval.
- Added PostgreSQL regressions for acceptance, corrected revisions, stored evidence and the actual sample/findings HTTP responses. The owner subsequently reported the suite passing.

## 6. Exact request differences from the experiment

The system prompt and context instruction text are retained. Context rendering retains the
coordinate conversion, padding, neutral margin and external red frame. The production request
is **not byte-identical** to a benchmark request:

- Case identity is derived from immutable rulebook version, rule, sample revision and source hashes,
  rather than the experiment case identifier. Those identities remain bound in the response schema.
- `task.variant` is the production view identifier `box-context-png-v1`, rather than `box-context-v1`.
- The `guideline` is the accepted production rule document, including its production check name
  and metadata; this differs from the experiment's frozen guideline document.
- The response schema is generated from the production BoxResponse model, with production limits
  and titles. Identities are constrained, but verdicts and reasons remain model-generated.
- Model, digest, context, threads, keep-alive and byte/time limits come from the accepted execution
  pin. They match a particular experiment only when explicitly configured to the same values.
- Neither path sends original source coordinates in the crop prompt. Production stores geometry
  and request/view hashes as audit provenance instead.

Therefore experimental accuracy cannot be asserted for this adapter without a production smoke run.
Historical test counts above describe the author's earlier run, not the modified branch.

## 7. Owner validation on 2026-10-23

The owner reported the corrected suite passing, then completed production audit
`audit_92b9dbaf1a3f47bf87f78f1497bee561`. Stored results were inspected:
case-03 had one verified pass; case-02 had one verified fail and five verified passes.
Both aggregate outcomes matched the prior human labels; both reviews remain pending.
All seven intended box records were retained. See ENCLOSURE_CHECK.md for the exact
model digest and timings. This closes the small Mac production smoke gate, not general
accuracy validation. Historical pending gates in section 4 describe the original PR.
