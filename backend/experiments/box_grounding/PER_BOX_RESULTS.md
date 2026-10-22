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
