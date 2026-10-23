# Marked context diagnostic

## Why this experiment

This is the next inexpensive diagnostic, not an established best method. Fresh
crop v2 evaluation caught all nine labeled violations but falsely rejected 11
pedestrian boxes. Eight errors were in boxes at most 100 pixels tall; two other
errors were in large, visibly pedestrian-containing crops. Scale alone cannot
explain all errors.

Options, in the order worth investigating for this prototype:

- **Marked surrounding context:** retains the existing model and tests whether
  visual context helps recognition. It risks counting a nearby person outside
  the actual box. Background controls are essential. It is not expected to
  solve the per-box request cost.
- **Uncertainty handling:** can make unsupported confident failures less common,
  but abstaining is reduced coverage, not a correct classification. Record
  unsure/unavailable explicitly and retain reviewer control.
- **Larger/different model:** a valid subsequent comparison, requiring the same
  evidence and independent validation. Capacity alone is not proof of accuracy;
  CPU memory and latency must be measured.
- **Detector-assisted verification:** potentially useful given the existing CPU
  detector, but detector misses/overlaps are also uncertain evidence. Do not
  silently replace a guideline judgment or a human label with a detector score.
- **Upscaling/visual-token changes:** enlarging an existing crop cannot recover
  absent source detail. Qwen documents image resizing and visual-token budgets
  in its [official README](https://github.com/QwenLM/Qwen3-VL/blob/main/README.md).
  Those Hugging Face processor controls are not assumed to be Ollama settings.
  Changing runtime preprocessing would be a separate intervention.
- **Fine-tuning:** defer until there is a substantially larger reviewed corpus
  and a clear baseline; this small diagnostic set is inadequate training evidence.

## Frozen intervention

Control: unchanged `box-crop-v2` request, generated afresh. Candidate:
`box-context-v1`, one padded image with a red frame around the original target.
The system prompt, rule, model digest, output schema, identity binding and
decoding settings are shared. Both arms make one sequential request per box.
The candidate changes pixels, marker and instruction together; results cannot
isolate padding alone or uncertainty wording alone.

Padding on each side is half the target width/height, rounded upward, with a
minimum of 32 source pixels. Only padding is clipped at source edges. There is
no resize and no target clipping. A two-pixel neutral outer margin permits a
two-pixel red frame **outside** the target even at source edges. All original
target pixels are preserved. The model receives the marked image and object
identity, not source-image coordinates. Geometry, image hashes and exact request
hashes remain in local records. People entirely outside the frame cannot make
the target pass.

## Selected diagnostic cases

Eight targets in `context_probe_plan.json` are pinned to the existing scope hash:

- fresh-14 box 4 and fresh-23 box 2: small pedestrian errors.
- fresh-10 box 2 and fresh-20 box 2: large pedestrian errors.
- fresh-01 box 5: a small pedestrian the current method recognized.
- fresh-01 box 1, fresh-03 box 2 and fresh-10 box 3: background controls.

These categories are for the analyst only. The runner reads the label-free plan
and scope, never the reviewed labels, historical outcomes or this document.
Original box numbers and object IDs remain intact even though only selected
boxes are run. An image-level decision cannot be inferred from this subset.

Selection used known errors. This is development data for this new variant;
the previous held-out v2 report remains valid for the frozen previous method.
Do not reuse this probe as an unbiased final accuracy estimate.

## Decision rule before seeing results

Compare against frozen **per-box** human labels and the fresh paired tight-crop
responses. No label edits, retries or prompt changes within a run. A useful
signal requires fewer wrong box judgments without losing any of the three
background detections or the previously correct small pedestrian. Incomplete
runs, unverifiable source/model state or unsure/unavailable outputs cannot be
reported as a successful recognition improvement; describe them explicitly.
Report all targets, model reasons and request durations, including any regression.
One small diagnostic does not justify production activation. If promising,
freeze the variant and prepare a separate untouched evaluation set.

## Commands (owner executes tests and model requests)

From the repository root, first run the existing offline experiment suite:

```bash
.venv/bin/python -B -m pytest backend/experiments/box_grounding/tests -q
```

Then, with Ollama running, on the owner's Mac:

```bash
caffeinate -i .venv/bin/python -B -m backend.experiments.box_grounding.context_probe \
  --scope .cache/box-grounding/fresh-review-v1/scope.json \
  --run-name context-probe-v1 \
  --model-digest sha256:ee4b975b58c17ce268cd19d40db35d5edc64603035d2ffc1fee1968eb0947f7b \
  --threads 10 \
  --timeout-seconds 600 \
  --keep-alive-seconds 3600
```

Eight boxes, two arms, **16 requests**. Estimate 25–40 minutes from prior runs;
context image processing may change the duration. Ten threads is this owner's
Mac configuration, not a cross-machine recommendation. Docker is unnecessary.
`caffeinate` is macOS-specific; keep the lid open and power connected.

Add `--dry-run` for input/request preparation with no network or output files.
An existing run directory is rejected. Ctrl+C preserves appended results and
writes an interrupted summary. Three consecutive infrastructure failures abort
the run. No automatic retries or response repairs occur. Existing strict client
checks verify the local model digest and returned identities on every call.

Outputs: `.cache/box-grounding/runs/context-probe-v1/` contains the pinned plan,
settings, code hashes, paired image files, request hashes, response text, timing,
results and summary. The probe has its own record schema; **do not use the
whole-image `run_experiment score` command on it**. Score these selected boxes
against the existing sealed labels after the run. No full-case accuracy claim
can be calculated from this selected-box subset.

## Preparation verification

Source AST parsing and a real-scope dry run succeeded. Eight marked previews
were saved locally in `.cache/box-grounding/context-probe-preparation-v1`, along
with preparation code/plan/request fingerprints. The tiny target and a
background target next to a person were visually inspected. Offline regression
tests were added but are unrun by the assistant. No model requests, production
changes, database operations, commits or pushes were performed for this probe.

## Full-scope validation after the probe

The runner also accepts `context-validation-plan-v1` (up to 256 boxes), requiring
exact coverage of every box in every nonempty source case. The original diagnostic
plan retains its eight-box limit. Validation uses the same paired request builders
and alternating arm order, with explicit full-scope metadata. Inference still
reads no human labels. The owner must run the offline suite before execution.

For context-validation-v1, 24 images / 49 boxes were reviewed before predictions:
39 passing and 10 failing boxes, yielding 14 passing and 10 failing images.
The label file and scope are sealed in the local input directory. Preparation
captured all 98 request fingerprints using the frozen implementation in diagnostic
batches before adapting orchestration; every request hash matched afterward.
Only context_probe.py changed from the pre-review implementation. Prompts,
rendering, model and decoding settings are unchanged. The verification record
is `.cache/box-grounding/context-validation-v1/orchestration-verification.json`.

```bash
caffeinate -i .venv/bin/python -B -m backend.experiments.box_grounding.context_probe \
  --scope .cache/box-grounding/context-validation-v1/scope.json \
  --plan .cache/box-grounding/context-validation-v1/validation-plan.json \
  --run-name paired-context-validation-v1 \
  --model-digest sha256:ee4b975b58c17ce268cd19d40db35d5edc64603035d2ffc1fee1968eb0947f7b \
  --threads 10 \
  --timeout-seconds 600 \
  --keep-alive-seconds 3600
```

This runs 98 requests; allow approximately 2–3 hours, with possible variation.
No inference has been performed during preparation. Scope-wide per-box results
allow full-image aggregation after scoring against the sealed labels. Retain
uncertainty and failure counts; never count an unprocessed box as a pass.
