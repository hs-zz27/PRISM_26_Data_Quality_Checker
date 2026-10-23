# Assignment: integrate the validated pedestrian-box context method

Implement a clean, opt-in production check for this exact rule:

> Each annotation box must enclose a visible pedestrian; a box entirely on background is a violation.

A partly visible but identifiable pedestrian inside the box counts. A person
entirely outside the box does not. The rule does not require a whole body,
walking motion, box tightness, a visibility percentage, or annotation completeness.

## Repository and commits to read

Repository: `hs-zz27/PRISM_26_Data_Quality_Checker`.
Reference branch: `experiment/per-box-grounding`.

Read these commits in order:

1. `9b4878d` — earlier crop-v2 geometry correction and scoring work.
2. `7920862` — Bind experiment response identities to their requests.
3. `b75cd6a` — Compare tight crops with marked surrounding context.
4. `0f35d57` — Record the fresh and targeted crop validation results.

The last commit contains the evidence supporting this assignment. This handoff
is added after it. Confirm those references exist after fetching; stop and report
a missing reference rather than implementing from an outdated experiment.

Work from current `origin/main` on a new integration branch. The experiment
branch is a reference snapshot, not an instruction to replace current production
files. One way to keep both checkouts available is:

```powershell
git fetch origin
git switch -c integrate/pedestrian-box-context origin/main
git worktree add --detach ..\cdif-context-reference origin/experiment/per-box-grounding
```

Use a different worktree directory if that path already exists. Never reset,
delete, or overwrite someone else's checkout. Inspect the current main interfaces
before editing: newer production changes take precedence over review copies.

## Teammate environment — native Windows, 16 GB RAM

Write all teammate-facing commands for **PowerShell**, run from the appropriate
checkout root. Use native Python 3.12 and `pathlib`; paths may contain spaces.
Do not assume WSL, Bash, macOS tools, Homebrew, `caffeinate`, `/Users/...`,
`.venv/bin/python`, `source`, Unix signals or `fork`. Windows line continuation
uses a backtick, not a Bash backslash; prefer one-line commands.

If no suitable virtual environment already exists, create one after Python 3.12
is available:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe --version
```

Do not replace an existing environment. Using the interpreter directly avoids
requiring activation or changes to PowerShell's execution policy. Use the existing
repository dependency pins; do not upgrade packages or install experiment-only
requirements as if they were the full backend requirements. Request permission
before dependency installation/changes as specified below.

For a checkout containing the experiment packet, once its pinned test dependencies
are available, the existing offline experiment tests use:

```powershell
.\.venv\Scripts\python.exe -B -m pytest backend/experiments/box_grounding/tests -q
```

Run that command in the reference checkout with its own environment, or explicitly
point to a suitable interpreter; the two worktrees do not share `.venv` folders.
Keep new production regression tests in the integration checkout and provide their
exact PowerShell command separately. Do not require the owner's ignored files.

Coding and synthetic/mocked tests do **not** need Ollama, a vision model, the
Penn-Fudan dataset, the CNN checkpoint or a GPU. PostgreSQL integration tests do
need a separate local test database; use the project's Docker setup only when
that test stage is authorized. Ollama and the Torchvision detector are separate
systems; the detector weights cannot substitute for the vision-language model.

If real inference is later authorized, use Windows Ollama with a separately
verified local vision model. `ollama list` and `ollama ps` show installed/loaded
models; `ollama serve` is needed only if the Windows Ollama app has not already
started the server. Do not start a second server on the same port. A matching tag
does not prove a matching digest: the earlier Windows run had a different build.
Report any digest/version difference and do not silently substitute a model or
claim direct equivalence to the Mac results. Do not download a model automatically.

Start with automatic thread selection (`None`/omitted in production pins); the
experiment's `--threads auto` belongs to its main runner, not to `context_probe`,
whose integer-only `--threads` option should be omitted for automatic selection.
Pin the chosen setting within each paired run. Keep requests sequential on the
16 GB laptop and record actual latency/memory; Mac timings are not a Windows ETA.
Retain CPU-only model options for this feature. No GPU/CUDA dependency is needed.

New process entry points must be importable module-level callables with picklable
arguments and no import-time model/database startup. Preserve the existing spawn
supervisor; exercise it on native Windows. Use process cleanup/deadlines that work
on Windows rather than adding POSIX-only signals, shell commands or process groups.

Recorded experiment hashes may depend on exact file bytes. Do not change global
Git line-ending settings or rewrite a recorded hash to hide a CRLF mismatch.
Report any mismatch and distinguish local checkout conversion from changed source.

## Evidence and what it means

On the owner's Mac M4 Air, 16 GB RAM, Ollama 0.33.3, CPU only:

- Model: `qwen3-vl:4b-instruct`.
- Exact digest: `sha256:ee4b975b58c17ce268cd19d40db35d5edc64603035d2ffc1fee1968eb0947f7b`.
- Temperature 0, context 16384, output ceiling 2048, 10 threads, timeout 600s,
  keep-alive 3600s; known response identities bound using JSON Schema constants.
- The ten-thread setting is a measured Mac setting, not a Windows default.

Fresh validation: 24 images, 49 boxes, human decisions frozen before prediction.
Tight-v2 was correct on 48/49 boxes: TP 10, FP 1, FN 0, TN 38.
Context-v1 was correct on 49/49: TP 10, FP 0, FN 0, TN 39.
Both classified all 24 whole images correctly. The tight-crop false flag was
validation-23 box 4; another box in that image was a genuine violation, hiding
the error in the whole-image score. Summed request duration was 68.17 minutes
for tight crops and 69.81 minutes for context crops.

Targeted validation: 12 fresh images, one box each; eight human pass, four fail.
Tight-v2 was correct on 10/12: TP 4, FP 2, FN 0, TN 6, F1 0.8.
Context-v1 was correct on 12/12: TP 4, FP 0, FN 0, TN 8, F1 1.0.
Context corrected targeted-02 (45x118 pixels) and targeted-08 (111x138,
synthetic partial box). All four failing boxes near pedestrians remained fail.
Summed duration was 1004.74s versus 1007.01s, respectively.

There were no unsure/unavailable outcomes in these two validation runs. Source,
code, request/image hashes, identity checks, and sealed labels were verified
offline. No production code has been changed by these experiments.

Limitations: these are small controlled cohorts; they do not establish general
accuracy or a guaranteed improvement. The targeted partial boxes were synthetic
upper halves of annotations, not natural occlusion. The smallest positive in
that cohort was 106 pixels high. Truly tiny pedestrians remain unvalidated.
Source hashes are disjoint from recorded prior exposure, but similar scenes and
undocumented exposure are possible. Padding, a marker and wording changed
together: we did not isolate which component caused each improvement.

This is evidence-view/prompt work, not proof that the feedback policy learned
to improve accuracy. The earlier example-based feedback candidate regressed;
do not activate it or describe this work as its successful replacement.

## Read first — reference checkout

Under `backend/experiments/box_grounding/`:

- `PER_BOX_RESULTS.md`, `HANDOFF_NOTES.md`, `CONTEXT_PROBE.md`.
- `context_views.py`, `context_instruction_v1.txt`, `context_probe.py`.
- `crops.py`, `prompts.py`, `settings.py`, `scoring.py`, `client.py`.
- `tests/test_context_probe.py`, `tests/test_prompts.py`, `tests/test_client.py`.
- `reference_results/context_validation_v1_mac.json`.
- `reference_results/targeted_context_v1_mac.json`.
- `reference_results/context_probe_v1_mac.json` and `paired_fresh_v1.json` for history.

The reference JSON files contain recorded predictions, human labels, settings
and provenance; they do not contain the image corpus. Source assets, owner-local
review sheets and prepared scopes are ignored. Your implementation and offline
tests must not depend on those private paths or on this chat.

## Read first — current production checkout

- `SRS.md`, `backend/README.md`, `backend/api/README.md`.
- `backend/app/checks/registry.py`.
- `backend/app/models/rule.py`, `finding.py`, `audit.py`, `feedback.py`.
- `backend/app/core/config.py`, `backend/app/core/feedback.py`.
- `backend/app/inference/annotation_guideline.py`.
- `backend/app/services/feedback_evidence.py`, `feedback_prompt.py`, `feedback.py`.
- `backend/app/services/evaluation.py`, `sample_worker.py`, `batch_audit.py`.
- `backend/app/storage/feedback_repository.py`, `feedback_jobs.py`, `workflow_repository.py`.
- `backend/app/services/feedback_jobs.py`.
- `backend/app/routers/audits.py` and the existing audit request/response models.
- `backend/tests/test_feedback_inference.py`, `test_feedback_models.py`,
  `test_feedback_worker.py`, `test_workflow.py`, `test_batch_supervision.py`.
- `backend/api/build_contract.py` and `backend/api/schemas.py` when public schemas change.

Do not assume `annotation_guideline` already supports per-box calls. At the
reference baseline, its registry parameters are empty, `RulePin` accepts only
confirmed parameter-free annotation guidelines, `ExecutionPin` identifies the
original-image prompt/preprocessing, and feedback storage expects sample-level
findings. These are integration boundaries to respect, not validators to weaken.

## Phase 1 — explicit scope and immutable execution contract

Use an explicit confirmed check identity, recommended name
`pedestrian_box_enclosure`, with sample-level scope. Keep generic
`annotation_guideline` behavior unchanged. Do not select the method by substring
matching guideline text, assume every visual guideline means enclosure, or
introduce a global crop switch affecting unrelated rules.

Design a small typed, immutable execution pin for the new method. It must retain
model/digest, prompt, preprocessing and response-schema versions, finite request
limits and thread selection. Persist it when the audit is accepted, so queued,
retried and rechecked work uses the accepted method rather than current defaults.
Version new behavior honestly; do not label transformed images
`original-png-no-transform-v1` or mutate old stored pins/fingerprints.

Trace the acceptance, durable request decode, spawn serialization, evaluation,
finding persistence, read API and recheck paths before coding. Document the exact
files and data flow. Keep the check unavailable with a safe explanation when its
explicit runtime configuration is absent. No silent model selection/download.

This first integration is a baseline enclosure check without feedback examples.
Keep existing feedback policies and their activation rules unchanged. Reject an
attempt to use an incompatible policy for this new method clearly; never ignore
a supplied policy or imply that a full-image evaluation validates crop policies.
Do not broaden `RulePin` or frozen-set semantics just to bypass those constraints.
If current APIs prevent expressing this safely, report the exact conflict before
changing the feature scope or mixing the two execution protocols.

## Phase 2 — bounded rendering and local inference

Port the necessary pure rendering logic into an appropriate production module.
Production must not import experiment modules or read reference results/labels.
Reuse the existing verified immutable-asset reader and suitable transport/model
verification code. Keep functions small, typed and named for their responsibility.
Avoid duplicate clients, deep abstraction hierarchies and unrelated refactoring.

Preserve the tested method:

- One-based inclusive annotation `(xmin,ymin,xmax,ymax)` becomes PIL half-open
  `(xmin-1,ymin-1,xmax,ymax)`. Retain original list index and object ID separately;
  duplicate object IDs must not collapse results.
- Context padding on each side is half that target dimension rounded up, minimum
  32 pixels, clipped only at source boundaries. Validate the target itself;
  never repair, clip, drop or reorder an invalid annotation.
- No resizing. Preserve target pixels exactly. Add a neutral 2px outer margin
  and a red 2px frame entirely outside the target. Retain source and local windows.
- Use the frozen instruction and system prompt as the behavioral reference.
  If adapting the envelope to production, identify any request differences and
  require a later owner-run smoke check; do not claim byte-identical validation.
- Do not send original-image coordinates as if they described the cropped
  attachment. Keep source geometry in provenance, not ambiguous model instructions.
- Sequential requests, one per target. No GPU assumption or concurrent requests.
- Bound known case/rule/box/object identities in a fresh response schema and
  still validate them after return. Never constrain outcomes to an expected label,
  repair an ID, accept unverified output or substitute cached benchmark answers.
- Retain strict duplicate-key, schema, size, context, local-model digest and
  pre/post identity protections. A transport/model failure remains unavailable.

Use existing finite supervision. Bound the maximum number of boxes/calls and
respect the parent sample deadline and cancellation/ownership checks. Do not
multiply a per-call timeout into unbounded sample work or silently truncate the
annotation list. Reuse existing safeguards; no new queue or worker framework.

## Phase 3 — durable proposals and usable review

Retain one sample-level finding for this sample-level rule, with complete
per-box proposal evidence nested beneath it. Preserve existing finding/review
contracts where possible. Per-box evidence must include annotation index,
object ID, exact original box, source image/annotation hashes, transform/prompt
versions, source/local windows, view hash, outcome, reason, timing and verification.
Retain pinned rulebook ID/version, sample revision and execution settings.

Aggregate deterministically: any verified box fail means sample fail; otherwise
any unavailable means unavailable; otherwise any unsure means unsure; only all
verified passes mean pass. Zero objects, invalid geometry and unrepresentable
evidence must never produce a fabricated all-pass result. Record a safe reason
and make the no-object behavior explicit. Keep every intended annotation
accounted for, including failures; do not hide incomplete processing.

All findings remain pending human review. Existing deterministic measurements
and reviewer decisions must not be rewritten by model proposals. Preserve source
evidence so reviewers can inspect the full image and original box; regenerated
views must use pinned versions and verified bytes, not mutable dataset paths.

Integrate with current finding/sample reads, the findings-list endpoint, job
progress and linked rechecks. Rechecks use the correct immutable revised assets
and explicitly pinned protocol. Do not add a redundant endpoint merely because
there is a new check. If a public request/response or check catalogue changes,
update its documentation and the model-derived contract source consistently.
Do not expose private paths, storage keys, claim tokens or internal exceptions.

## Phase 4 — regression coverage, documentation and handoff

Use synthetic images and mocked Ollama responses for focused offline coverage:

- Target pixel preservation at image edges, one-pixel targets and clipped padding.
- Original geometry, local marker coordinates, duplicate IDs and annotation order.
- Partial pedestrians allowed by semantics; no invented tightness threshold.
- No held-out labels, construction hints or source-coordinate ambiguity in payloads.
- Identity mismatch, malformed output, changed digest, timeout and size/context limits.
- Aggregation combinations, zero boxes, invalid boxes and partial/unavailable work.
- Opt-in selection, absent configuration and unchanged generic guideline behavior.
- Serialization/rehydration of pinned requests and compatibility with old records.
- Spawned audit computation, cancellation, timeout/ownership loss, stored evidence,
  public review responses and revision-aware rechecks.
- Explicit rejection of incompatible feedback-policy requests.

You may run focused synthetic tests locally. PostgreSQL tests must use isolated
test schemas and an explicit test database; never operate on the owner's live
validation database. Ask the owner before real inference, weights/downloads,
dependency changes, migrations or API contract generation. Do not repeat expensive
full-model validation automatically. Provide exact owner-run commands when needed.

Document opt-in configuration, supported rule semantics, provenance, expected
one-call-per-box latency, timeout/box bounds and what remains unverified. Runtime
limits must be reconciled with real multi-box latency, not raised globally to
make a smoke test pass. Never claim production readiness from synthetic tests.

Completion means: an explicitly confirmed enclosure rule can run through the
normal accepted snapshot audit, supervised computation, durable finding and
human review flow; all intended boxes are accounted for; new requests/rechecks
use the intended pinned protocol; unrelated rules and existing feedback remain
compatible; focused checks pass; docs/contracts reflect actual behavior; and
the remaining real-model smoke gate is reported honestly if not yet run.

Prepare a PR with normal descriptive commits, a concise before/after description,
all modified files, exact tests/checks run, results and unresolved gates. Do not
merge your own PR or push directly to main. Obtain the owner's approval for the
specific repository, branch and prepared commits before pushing them. Do not add AI authorship attribution.
Do not edit the historical reference JSON to improve its scores.
