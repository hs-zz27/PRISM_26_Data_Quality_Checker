# Experiment plan: per-box crop grounding (plan version 1)

**Status.** This plan was written before any output of the new runner was
examined; no real-model run of it existed when it was written. After the first
output, this document changes only through a dated entry in the amendment log at
the end, never by silent edit. Any new variant, prompt or setting needs a new
plan version and a new variant identifier. Earlier results are kept.

## 1. Question

Does showing the model each annotation box's own pixels, one box at a time,
reduce missed background-only boxes without losing compliant decisions or
honest uncertainty, compared with a raw-image control run freshly on the same
machine, model and settings?

The rule is exactly: "Each annotation box must enclose a visible pedestrian; a
box entirely on background is a violation." People outside every annotation box
are out of scope. No tightness, IoU, area or visibility threshold is added. An
ambiguous crop stays unsure; failed execution stays unavailable. A detector miss
is not ground truth.

## 2. Hypotheses

- **H1 (candidate helps).** Per-box crops produce more supported failing boxes on
  the three reviewed violations than the control, with no more false positives,
  unsure or unavailable cases.
- **H0 (no effect or worse).** Crops change nothing, or lose compliant decisions,
  coverage or speed. A negative result is reported in full and is a useful
  outcome.

The earlier numbered overview and the one-example candidate both missed cases 02
and 12. That motivates this experiment; it does not show the cause. Some earlier
explanations describe pedestrians anywhere in the image, which suggests a
grounding problem. That is a hypothesis, not a finding.

## 3. Arms

| | Control `raw-image-v1` | Candidate `box-crop-v1` |
| --- | --- | --- |
| Requests per case | 1 | 1 per annotation box |
| Image sent | original PNG bytes, untouched (`original-png-no-transform-v1`) | that box's raw crop, cropped from the untouched image (`box-crop-png-v1`) |
| Text evidence | rule + full annotation text + all objects + image size and hashes | rule + that box's number, object ID, label, bbox, crop size, source image size |
| Output schema | production `Prediction` (`prediction-v1`) | `BoxPrediction` (`box-prediction-v1`), echoing case, rule, box number and object ID |
| System prompt | `system_prompt_v1.txt`, byte-identical | `system_prompt_v1.txt`, byte-identical |
| Extra wording | none | `per_box_instruction_v1.txt` (version `per-box-instruction-v1`), inside the user message |
| Examples | none | none |

**Honest description of the intervention.** The candidate changes three things
together: the pixels (crops), the request structure (one request per box) and
the wording (a per-box instruction). It is not a pure pixel-only comparison, and
a difference cannot be attributed to cropping alone. A crop-sheet intermediate
control is not part of version 1; if run later it is a separate named variant.

**Crop transform, declared.** `(xmin, ymin, xmax, ymax)` one-based inclusive
becomes the PIL crop `(xmin-1, ymin-1, xmax, ymax)`. No padding, no surrounding
context, no resizing, no clipping, no extra images. Crops never contain red
borders or labels. Each crop's annotation index, object ID, coordinates, source
image hash and crop hash are saved with its result. Other boxes are not described
in a box's request.

**Reconstructed control.** The historical rule JSON is not in the handoff, so the
control builds the guideline as a production `Rule` from the prepared scope. The
control therefore follows the historical request format but is not byte-identical
to the historical baseline. The fresh control, not the historical Mac numbers, is
the comparison.

## 4. Fixed settings (identical in both arms)

Local `qwen3-vl:4b-instruct` with the installed digest recorded and checked before
and after every call; CPU only (`num_gpu` 0); temperature 0; the same context,
output, request and response limits; one request at a time; fixed thread setting
for the whole run (start with automatic selection); a loopback Ollama address.
Hardware, OS, Python, package and Ollama versions are recorded. If the installed
digest differs from the historical one, the report says so before any claim of
equivalence. There are no hidden retries and no resume.

## 5. Scope

Eight development cases (02, 03, 04, 05, 09, 10, 11, 12), 22 annotation boxes.
A full paired run is 8 control requests plus 22 candidate requests = 30 chat
requests, each preceded and followed by a model verification (two small metadata requests each). Training cases
are not used; no training example enters any request in this comparison. Expected
labels are read only when scoring, after inference, from `reviewed_labels.json`.
Case 01 is excluded.

## 6. Aggregation and unrepresentable input

A sample's outcome from its per-box outcomes:

1. any supported (model-verified) failing box: **fail**;
2. otherwise any unavailable box: **unavailable**;
3. otherwise any unsure box: **unsure**;
4. only all supported passing boxes: **pass**.

Every box is judged; the loop never stops at the first failure. Zero annotated
boxes, more than 32 boxes, a non-one-based origin, invalid or out-of-image
geometry, and source or rendered size limits are **rejected before any request**
and the case stays in coverage as unavailable, in both arms. Context that does not
fit is rejected, never truncated. A request or parse failure, an unverified
model, a wrong returned identity or a truncated response is unavailable with a
reason code; it is never repaired into a pass.

## 7. Measurements

Violation-positive, with unsure and unavailable cases kept in the intended
denominator (definitions match the historical records; the metric code is checked
against all three of them): TP, FP, FN, TN, negative abstentions, precision,
recall, F1, overall correct labels, unsure and unavailable counts, coverage,
model-call count and model-call time. A missed violation, including an abstention,
is a false negative. False negatives and false positives are compared as well as
F1. Per-case and per-box outcomes and reasons are kept so errors can be inspected.
Timing is model-call time including verification requests, excluding rendering.

## 8. Interpretation rules, declared in advance

- The three reviewed violations make one case worth 0.33 recall. One case of
  difference is a descriptive observation, not evidence. No significance is
  claimed.
- The candidate is called **promising** only if it recovers at least one violation
  the control missed, adds no false positive, adds no unsure or unavailable case
  that lowers coverage, and reports its extra model calls and time. Anything else
  is reported as **not promising** or **inconclusive**. The production assessment
  rule is also printed.
- Cases 02 and 12 are not scored alone. All eight cases are reported.
- No prompt, setting, label or case selection is changed after seeing results to
  improve them. A changed variant is a new, separately reported variant.
- A repeated run is allowed for variance but is never a favorable retry: every run
  is reported with its own record.

## 9. Run protocol

1. Offline tests pass; `check-scope` verifies the prepared corpus; `run --dry-run`
   confirms every request fits.
2. One explicitly named development smoke case (`case-03`) for wiring. Preserved.
3. The paired eight-case run, interleaved order (the first arm alternates per case)
   so neither arm always benefits from a warm cache. Arm order and caching can still
   affect timing; this is recorded, not corrected.
4. `score` against the labels. Findings are separated into observed evidence and
   hypotheses.

## 10. Threats to validity

- **Homogeneous controlled violations.** In all four controlled cases (02, 06, 11,
  12) the replaced annotation is object 1 at `(1,1)-(80,55)`: the same corner box,
  the same size, the same box number. A good result may reflect that corner region,
  not general box grounding. The coordinates also appear in text, in both arms.
- **Exposed development data.** These eight images are development data now. They
  cannot show improvement on unseen images.
- **Small, mixed set.** Eight cases, three violations, one reviewer who clarified
  scope for two cases, original images mixed with controlled copies.
- **Three changes at once.** Crops, per-box requests and wording differ together.
- **Different request sizes.** The arms use different numbers of requests and tokens;
  speed differences are not a clean measure.
- **Small crops.** The smallest unresized crops are 32 by 70 and 80 by 55 pixels. A
  vision model may upscale or reject them; such failures appear as unavailable
  reasons, not as passes.
- **Identity echo.** The candidate asks the model to echo four identity fields. A
  mismatch makes the box unavailable, which can cost coverage for reasons unrelated
  to vision.
- **Hardware and model.** A different machine, thread setting, Ollama version or
  model digest is not interchangeable with the Mac run.

## 11. After the results

Whatever the outcome, promising or not: propose a new human-reviewed test set whose
source-image SHA-256 hashes do not overlap `prior_image_exclusions.json`, with
varied background boxes, labels frozen before predictions, and no coding or model
assistant supplying or tuning against labels. A fresh evaluation and owner approval
precede any production change or policy activation. Nothing here activates a policy.

## Amendment log

- 2026-10-22: run `paired-1` executed under this plan with no change to prompts, settings, cases or rules after
  seeing results. Installed model digest differed from the historical one (reported in the results). Outcome and
  analysis are in [PER_BOX_RESULTS.md](PER_BOX_RESULTS.md).

## Versioned follow-up: box-crop-v2 (22 October 2026)

The original box-crop-v1 run and its negative result remain part of the record.
After the source-geometry diagnostic documented in PER_BOX_RESULTS.md, the owner
authorized a separately selectable v2. It removes the source bbox, coordinate
convention and source image dimensions from model input; the variant identifier
also changes to box-crop-v2. The original instruction text, pixels, schema and
other request fields are retained. Geometry remains in local provenance.

Use the same eight exposed development cases and the same frozen labels, with a
fresh raw-image-v1 control on the same model digest and execution settings. Keep
one crop version per run, alternate arm order as before, and make all 22 crop
requests. Preserve every unavailable outcome and do not silently retry, relabel,
stop after finding a failing box, or substitute historical raw results. Existing
aggregation, completeness and assessment rules apply unchanged. Score and inspect
false positives as well as recall: an all-fail candidate is not useful. This is
still development investigation; confirmation on unseen, independently reviewed
images is required before claiming generalization. No production activation is
part of this plan. RUNNER.md provides the explicit v2 commands.
