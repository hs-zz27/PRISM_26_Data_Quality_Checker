# First paired run: raw-image control vs per-box crops

Run `paired-1`, 22 October 2026, under [EXPERIMENT_PLAN.md](EXPERIMENT_PLAN.md) version 1 with no
deviation. The run directory is git-ignored, so the numbers are recorded here. All eight
development cases, 30 model calls, scored with `score` against `reviewed_labels.json`.

## Conditions

Windows laptop, Intel64 Family 6 Model 140 (8 logical CPUs), CPU only, automatic threads,
Python 3.12, Ollama 0.40.8, `qwen3-vl:4b-instruct`, digest `sha256:84f7ed8042fa...5bc318`.
**The digest differs from the historical Mac run** (`sha256:ee4b975b58c1...`), so this model is not
the same build. Context 16384, up to 2048 generated tokens, temperature 0, timeout 1800 s for
both arms, interleaved order, one request at a time. A smoke run on case-03 came first
(`smoke-case-03` was interrupted by the operator before any model call; `smoke-case-03-b` completed).

## Result

| Arm | TP | FP | FN | TN | Abstain on pass | Unsure | Unavailable | Coverage | Precision | Recall | F1 | Calls | Seconds |
| --- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| `raw-image-v1` (fresh control) | 1 | 0 | 2 | 3 | 2 | 0 | 2 | 0.75 | 1.00 | 0.33 | 0.50 | 8 | 1100 |
| `box-crop-v1` (candidate) | 3 | 5 | 0 | 0 | 0 | 0 | 0 | 1.00 | 0.38 | 1.00 | 0.55 | 22 | 2843 |

Production assessment rule: **inconclusive** (the control holds unavailable results). Plan verdict
(section 8): **not promising**. The candidate recovered both violations the control missed
(cases 02 and 12) but added five false positives, so it fails the "adds no false positive" condition.
Historical Mac results (context only): baseline F1 0.80, one-example candidate 0.50, numbered overview 0.50.

| Case | Expected | Control | Candidate |
| --- | --- | --- | --- |
| case-02 | fail | pass (153 s) | fail (740 s), all 6 boxes fail |
| case-03 | pass | pass (175 s) | fail (179 s) |
| case-04 | pass | pass (153 s) | fail (396 s), all 3 boxes fail |
| case-05 | pass | unavailable (`identity_mismatch`) | fail (276 s); box 1 unavailable (`identity_mismatch`), box 2 fail |
| case-09 | pass | pass (117 s) | fail (120 s) |
| case-10 | pass | unavailable (model answered unavailable) | fail (123 s) |
| case-11 | fail | fail (125 s) | fail (355 s), all 3 boxes fail |
| case-12 | fail | pass (132 s) | fail (654 s), all 5 boxes fail |

Per-box totals for the candidate: 22 boxes, **21 fail, 1 unavailable, 0 pass, 0 unsure**.
Model-call time: control about 137 s per call, candidate about 129 s per call; the candidate used
2.75 times as many calls and 2.6 times the time.

## Observed evidence

- The candidate answered fail for every box it judged. It never answered pass or unsure. Its recall of
  1.00 therefore says nothing about discrimination.
- The human labels say every box in cases 03, 04, 05, 09 and 10 contains a visible pedestrian (8 boxes).
  The candidate marked 7 of them fail and 1 unavailable; none correct.
- Many fail reasons describe a person: "a person's shoulder and arm" (case-03), "torso and arm... head and
  legs are cut off" (case-09), "back and shoulder" (case-10), "head and shoulder" (case-12 boxes 4 and 5).
  The model saw part of a person and still answered fail, although the rule asks only for a visible pedestrian.
- Reasons on the injected `(1,1)-(80,55)` boxes in cases 02, 11 and 12 describe background ("foliage and
  tree trunks", "dark, indistinct background"), which is correct, but real pedestrians received the same answer.
- The crop images were correct in the smoke run (92 by 293 pixels, one walking pedestrian, matching bbox 48 40 139 332).
- In the smoke run Ollama reported 1684 prompt tokens for the 92 by 293 crop and 1865 for the full image.
- The control missed cases 02 and 12 (the historical baseline missed only 02) and returned two unavailable
  results. Two of the 30 calls ended in `identity_mismatch`, both in case-05.
- The smoke run's raw call took 29.5 s while the same call took 175 s cold in the paired run.

## Hypotheses (not established)

1. Small crops are upscaled by the vision pipeline, which would explain the "blurry" wording and token counts
   close to a full image. Untested.
2. Without surrounding context the model requires a whole, sharp pedestrian, a stricter reading than the rule,
   and the per-box wording did not stop it. A wording or padding variant could test this.
3. The one-box question biases a small model toward fail. It never used unsure although the instruction offers it.
4. The 29.5 s smoke timing was a warm prompt cache from the interrupted attempt. Timing in the paired run is
   interleaved and not a controlled benchmark.
5. The control's two unavailable results and extra misses versus history may come from the different model
   build, Ollama version, or the reconstructed guideline JSON. Not isolated.

## Limits of this result

Eight exposed development images, one reviewer, three violations that all use the same corner box, a
changed model build, one run. No significance or generalization claim. Nothing was tuned after seeing results.

## Owner verification of saved records — 22 October 2026

The supplied run.json, results.jsonl, summary.json and score.json agree on all
16 sample/arm records, 30 chat calls and the metrics above. Their code hashes
match the teammate branch at a6e3dde. All 22 recorded crop byte hashes match
crops independently reconstructed from the original source images and declared
coordinates. This supports correct crop generation; it does not prove how the
inference server subsequently represented those pixels internally.

For the Windows run, raw-arm model-call time is 1,099.70 s, including 883.23 s
of server-reported prompt evaluation (80.3%) and 198.27 s of generation. Crop-arm
time is 2,843.22 s, including 2,161.96 s of prompt evaluation (76.0%) and 672.08 s
of generation. Crop-arm model loading totals only 0.44 s. Repeated model loading
is therefore not the dominant bottleneck in this run. The 22 crop calls repeat
model input processing compared with eight raw calls. Both arms request CPU-only
execution. These measurements describe this run, not the Mac's performance.

Recorded prompt token counts range from 1,684 to 1,724 for crops and 1,850 to
2,207 for full images. These totals include text and schema as well as image
processing; they are not isolated image-token counts and cannot establish
upscaling. Accuracy explanations repeatedly introduce unsupported whole-body
or motion requirements. Correct crop hashes, those explanations and a different
installed model build do not isolate one root cause. Image processing, wording
and model capability remain distinct hypotheses.

A useful next diagnostic would freeze a few development inputs and change one
factor at a time: exact crop with a neutral person-presence question, the same
pixels with explicit partial-visibility wording, then a separately declared
context/padding variant. Preserve original runs and all outcomes. Version and
measure each variant; do not run the full eight-case comparison merely to debug
request wiring. Any improvement claim still needs a fresh blinded test set.

## Controlled source-geometry diagnostic — 22 October 2026

The owner subsequently authorized a small local model diagnostic. The case-03
92-by-293 crop was reconstructed and its byte hash matched the Windows record.
Visual inspection shows a pedestrian from head to shoes. The original request
nevertheless reproduced the Windows failure on the Mac: it described only a
blurry shoulder and arm and returned fail.

The request attaches a crop but also supplies original-image coordinates
`[48, 40, 139, 332]` and original image size `[375, 349]`. Those coordinates do
not describe the attached 92-by-293 image. The convention is explicitly marked
as source pixels, so this is not an arithmetic crop error. It is unnecessary
geometry that the model must distinguish from its actual attached view.

Removing only `box.bbox`, `box.bbox_convention` and `box.source_image_size`
changed the outcome to pass. The experiment alternated original, removal,
original, removal: outcomes were fail, pass, fail, pass. Image bytes, model
digest, execution options, system prompt, guideline, per-box instruction and
response schema remained identical. Both repeated variants used identical
request bytes. All four structured outputs passed schema and identity checks.
The removal response correctly described a person in a dark suit. A separate
plain description of the unchanged crop also correctly recognized that person.
The plain negative control correctly described trees and foliage with no person.
That negative control checks basic recognition; it does not yet validate the
geometry-removal candidate on background crops. Six requests, their responses,
settings, image hashes and timing/caching fields are preserved without dataset
image bytes in `reference_results/crop_geometry_diagnostic.json`.

This isolates the source-geometry field group as a reproducible cause of this
case's erroneous judgment under the tested configuration. It does not isolate
which individual field matters, establish the model's internal reasoning, or
prove that all Windows failures have the same cause. Interpreting source
coordinates against the already-cropped image is a plausible explanation, not
an observed second crop operation. No resizing or larger model was needed to
correct this particular answer.

The local runtime was Ollama 0.33.3 with model digest
`sha256:ee4b975b58c17ce268cd19d40db35d5edc64603035d2ffc1fee1968eb0947f7b`,
CPU-only, ten threads, temperature zero, 16,384 context and 2,048 output limit.
The original Windows run used a different model digest and runtime. These are
exposed development diagnostics, not a fresh accuracy benchmark or authorization
to activate a candidate.

Local server logs also identify a performance mechanism: the runner starts with
`--image-min-tokens 1024`; this small crop used 1,044 image tokens and 640 text
tokens. Reducing crop dimensions therefore did not produce a proportionately
cheap request. The original first call took 70.22 seconds, with 56.79 seconds
of prompt evaluation. Later geometry comparisons reused 1,601 cached prompt
tokens; their 8–12 second times must not be advertised as a geometry-removal
speedup. The Windows record separately establishes repeated input processing
across 22 crop requests as its main measured cost; its exact visual-token
floor was not captured.

The narrow next implementation is a versioned crop prompt that keeps source
coordinates in local provenance rather than sending them with the crop. Verify
both positive and negative cases on the same Windows build before any broader
claim. Keep original experiment versions and results intact. Reducing model
calls and measuring a smaller supported visual-token budget are separate speed
experiments, each requiring accuracy checks.

## Completed Mac crop-v2 comparison — 22 October 2026

The owner ran `paired-crop-v2-mac`: all 16 planned sample/arm units completed,
with 8 raw requests and 22 crop requests. The original records report no missing
units and no source changes. Code fingerprints, saved crop hashes, confusion
counts and duration sums were independently checked. The records, including
unavailable responses, are preserved without image bytes in
`reference_results/paired_crop_v2_mac.json`.

The fresh raw control had TP 2, FP 1, FN 1, TN 3 and one negative abstention;
precision, recall and F1 were each 0.667. Crop v2 had TP 3, FP 1, FN 0, TN 3
and one negative abstention; precision was 0.75, recall 1.0 and F1 0.857.
Coverage was 7/8 for each arm. The assessment remains **inconclusive** because
both arms have an unavailable sample. This is a promising development result,
not an adoption decision or an unseen-set accuracy claim.

V2 produced 16 box passes, four box failures and two unavailable box responses.
It correctly failed the three injected background boxes in cases 02, 11 and 12,
while passing the other boxes in those cases. Case 04's 32-by-70-pixel third box
was the remaining false positive: its reason described only background, whereas
the frozen review labels mark that sample compliant. Case 05 was unavailable
in both arms. Its raw response and both crop responses incorrectly echoed the
case ID; the crop responses otherwise proposed pass. Their final outcomes remain
unavailable, with exact-identity checks preserved. No review label or model ID
was changed to improve the score.

The repeated case-03 diagnostic and these results support correcting source
geometry in crop requests. They do not establish that it was the sole cause of
all earlier errors. In particular, the Windows v1 run used a different digest
and runtime; its five false positives cannot be treated as a paired v1-versus-v2
measurement against this Mac run.

Wall time was 59.24 minutes. Raw-arm call time was 15.86 minutes; crop-arm call
time was 43.36 minutes. Crop requests still took about 2.73 times the total raw
call time. Crop server metrics sum to 1,724.73 seconds prompt evaluation,
871.06 seconds generation and only 0.036 seconds loading. Removing source
geometry corrected the reproduced judgment but did not remove the cost of
22 sequential model calls. Later calls slowed substantially; this run contains
no power, thermal or system-load measurements that identify why.

Next investigation priorities are preventing identity-copy failures while
retaining request/response binding, and separately examining the small remaining
pedestrian crop. New variants must preserve these results and use explicit
versions. Fresh independently reviewed images remain necessary for any broader
accuracy claim. No additional model run, activation or production change was
performed while reviewing these records.
