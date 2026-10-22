# Assignment: investigate cropped-box judgments for the prototype

You are helping improve prediction quality in the private
`hs-zz27/PRISM_26_Data_Quality_Checker` repository. Work from your own checkout
and a dedicated branch, such as `experiment/per-box-grounding`. You are on
Windows with 16 GB RAM. Coding assistance may help implement and analyze this
experiment, but actual accuracy measurements must use the project's Qwen vision
model and independently reviewed human labels.

## Goal and ownership

Investigate whether showing individual annotation-box regions helps the model
recognize background-only boxes. The owner is separately testing numbered
full-image overviews. Your work must be independent of that experiment and must
not change production behavior. Produce clean, readable, typed code with small
functions and focused tests; reuse suitable existing models rather than
duplicating their contracts or introducing a new framework.

Allowed implementation scope is `backend/experiments/box_grounding/`. Put new
synthetic tests in its `tests/` folder. Keep machine-specific files, downloaded
data, prepared views, model weights, caches and generated experiment results
under ignored `.cache/box-grounding/`. Do not change `backend/app/`,
`backend/api/`, root dependency pins, audit workers, database schemas or API
artifacts. If a production change appears necessary, document the exact need
and ask the owner instead of applying it. No API regeneration, migrations,
policy activation, model fine-tuning, broad refactoring, automatic commits or
pushes are authorized by this assignment.

## Context you must read

1. This folder's README.md, RESULTS.md and DATA_PROVENANCE.md.
2. manifest.json, reviewed_labels.json and system_prompt_v1.txt.
3. reference_results/reviewed_comparison.json and the other two reference files.
4. prepare.py, views.py and their synthetic tests.
5. For existing contracts and safeguards, read:
   - backend/app/models/feedback.py
   - backend/app/inference/annotation_guideline.py
   - backend/app/services/feedback_prompt.py
   - backend/app/services/feedback_evidence.py
   - backend/app/services/feedback_metrics.py

All these paths are in GitHub. Do not depend on any owner-only scripts, local
database schema, private asset directories, absolute Mac paths or local chat
history. Do not claim a runner or portable inference loop already exists in
this handoff: implementing it is part of your task.

## Scientific baseline

The rule is exactly: “Each annotation box must enclose a visible pedestrian;
a box entirely on background is a violation.” People outside all annotation
boxes are outside this rule's scope. Do not invent a tightness, IoU, pixel-area
or visibility-fraction threshold. An ambiguous crop must remain unsure; failed
execution must remain unavailable. A detector miss is not human ground truth.

The original eight-case comparison had baseline F1 0.80 and candidate F1 0.50.
The candidate added one negative example, missed one additional violation and
took about 31% longer. It was not activated. Its failure motivates the new
experiment, but does not prove the cause. Current images are a small mixture of
original and controlled copied annotations, not representative deployment data.
They are now development cases and cannot establish unseen-data improvement.

Use the same installed local `qwen3-vl:4b-instruct` for both arms of a paired
comparison. Record its exact digest. The historical digest is in the reference
file; if the installed digest differs, report that before claiming equivalence.
Changing model or quantization is not part of this experiment. Use CPU-only
requests, temperature 0, and the same context/output/request limits in both
arms. Start with automatic thread selection on Windows; do not copy the Mac's
ten-thread setting as a universal recommendation. Keep threads fixed within a
run, and record hardware, OS, Python, package and Ollama versions.

## Phase 1 — Verify the corpus and freeze the plan

Use Python 3.12 and the minimal experiment requirements. Run the supplied
synthetic tests. Obtain the official dataset or ZIP explicitly, then execute
the prepare command from README.md. Do not modify the original dataset or
normalize away a hash mismatch. Report exact mismatched case/hash fields and
stop that preparation if the input differs. Never substitute other images.

Verify eleven prepared cases: three training and eight development. Guided
practice case 01 is absent. Confirm that `scope.json` has annotation/source
evidence but no expected labels or training-example answers. Review the
existing renderer's coordinate conventions: one-based inclusive source boxes
become half-open PIL crops `(xmin-1, ymin-1, xmax, ymax)`. Preserve duplicate
object IDs using distinct annotation-list indices. Do not clip invalid boxes,
omit difficult objects, renumber source object IDs or silently drop cases.

Write an experiment plan before examining new model outputs. Define exactly
which candidate you will test, settings, aggregation, scope and measurements.
The first candidate uses raw crops from the untouched image, with box indices
and exact source-coordinate/hash provenance. Cropped pixels must not include
the rendered overview's red borders or labels.

## Phase 2 — Implement the separate portable runner

Implement a CLI such as `run_experiment.py` under this folder. It must work from
the checkout on native Windows without PostgreSQL, Docker, Torch, the CNN
checkpoint or the owner's environment. All source data comes from prepared
scope; no hardcoded user paths or implicit root .env settings. Provide explicit
local Ollama address, model/digest, thread and finite execution-limit options.
Keep a straightforward sequential request loop, never concurrent inference.

Implement a raw-image/no-example control and a separately versioned per-box
crop candidate. Keep model/settings and rule fixed. Inference builders accept
image/annotation evidence and rule only, never a human adjudication object.
Read expected labels only when scoring after inference. Training examples are
not added during this first input-grounding comparison. If explicit per-box
wording differs from the old system prompt, save its exact text and version;
describe the intervention honestly instead of calling it a pure pixel-only
comparison. A crop-sheet intermediate control is optional and must be reported
as a separate variant, not substituted silently.

Reuse the reference renderer where suitable. For individual crop requests,
keep each crop's original annotation index, object ID, coordinates and source
hash. Supply enough neutral context to understand the rule, without announcing
whether a box was deliberately altered. Surrounding context, resizing, padding
or extra images must be declared and versioned; do not add untracked transforms.
Do not force a supported pass/fail if the crop cannot establish the requirement.

Sample aggregation is deterministic: any supported failing box means sample
fail. Otherwise an unavailable box makes the sample unavailable; otherwise any
unsure box makes it unsure; only all supported passing boxes make it pass.
Record every per-box outcome/reason and the aggregate. Explicit zero-object or
invalid-geometry handling must be documented and tested, never guessed or
silently omitted. Limits must reject unrepresentable inputs and retain the case
in reported coverage rather than truncating boxes/context.

Preserve the useful safeguards in the existing guideline client: verify the
local model/digest and vision capability before/after calls; bounded request
and streamed response bytes; finite timeouts; structured validated output;
reject duplicate JSON fields; require the expected case/rule/box identity;
require completed, non-truncated responses and observed context headroom.
Use existing prediction types where appropriate. Version any new per-box
schema explicitly. Never repair an incorrect returned identity or turn a
request/parse failure into an empty passing result. Do not save transformed
inputs under the production `original-png-no-transform-v1` identity.

Save complete run settings, variant/prompt/preprocessing versions, source and
rendered hashes, elapsed times, results and progress to a new ignored output
directory. Preserve original runs and partial failures. No hidden retries or
silent overwrite/resume. Print a start message immediately and progress before
and after each sample/box; provide a status command that reads saved progress
without running inference. Keep this an experiment, not a new job framework.

## Phase 3 — Exercise and compare honestly

Add focused tests for coordinate conversion, right/bottom edges, duplicate IDs,
source immutability, retention of every case and annotation in the results,
context/size rejection, malformed/incorrect identities, unavailable/unsure
propagation, and the aggregation truth table. Use synthetic images and mocked
HTTP responses. Tests must need no live model or database. Run them and report
the exact command and pass/skip/fail totals. Do not weaken existing tests.

After offline checks, run one explicitly identified development smoke case for
wiring, preserving that attempt. Then run the raw control and candidate over
all eight development cases, on the same machine with fixed settings. Do not
score just cases 02 and 12 or select a favorable retry. Record whether warm
caches or arm order may affect timing. If no model is installed, finish code
and offline checks, list the runtime gate as pending and provide exact commands
for the owner to execute. Do not fabricate or claim real-model measurements.

Report violation-positive TP/FP/FN/TN, precision, recall, F1, overall correct
labels, unsure/unavailable counts, coverage, model-call count and elapsed time.
Uncertain and unavailable cases stay in the intended denominator. A missed
violation remains a false negative. Compare false negatives and false positives
as well as F1; a speed/coverage tradeoff must be visible. Keep exact per-case
and per-box reasons so the owner can inspect errors. Report all tested variants,
including worse outcomes. Historical Mac measurements are reference context;
the fresh local control is the comparison for your candidate.

## Phase 4 — Review and hand off

Review the diff for scope, truth leakage, incorrect coordinates, hidden
truncation, changed model/settings, private paths and untracked transforms.
Provide a short implementation report: changed files and purpose, actual
verification commands/results, real-model measurements or remaining gates,
known limitations, and exact owner-run commands. Include a case-level error
analysis that distinguishes observed evidence from hypotheses.

Any result on these eight images is development evidence. If promising, propose
a new human-reviewed test set whose source-image hashes do not overlap
`prior_image_exclusions.json`. Freeze labels before predictions; coding/model
assistants must not supply those labels or tune against them. A fresh evaluation
and owner approval precede any production change or policy activation. A good
development result does not guarantee improvement or statistical significance.

## Completion criteria

- Portable source preparation and inference/status commands are documented.
- Synthetic checks actually pass; any skipped checks and limitations are named.
- A full paired eight-case report exists, or real-model execution is explicitly
  incomplete and the owner has reproducible commands to finish it.
- Results retain all cases/boxes, uncertainty, failures, timings and provenance.
- No production behavior, contracts, database or private data were changed.
- No improvement claim exceeds the evidence, and no policy was activated.
- Only authorized experiment code/tests/docs enter the review diff. Downloads,
  images, weights and local run outputs remain ignored. Preserve unrelated work.
- Obtain the owner's approval before any commit/push or publication. Use normal
  human commit messages and the configured human identity, without AI authorship
  credits or trailers.
