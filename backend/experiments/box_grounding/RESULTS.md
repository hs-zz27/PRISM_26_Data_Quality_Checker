# Earlier results and the question to investigate

## Model and rule

The owner used local `qwen3-vl:4b-instruct`, CPU-only, with 16,384 context tokens,
up to 2,048 generated tokens, temperature 0, a 600-second request timeout and ten
CPU threads on an M4 MacBook Air. Preserve the recorded digest from
`reference_results/reviewed_comparison.json`. Ten threads is not a Windows
recommendation. The CNN detector is a separate model and is not required for
this assignment.

The evaluated rule is:

> Each annotation box must enclose a visible pedestrian; a box entirely on
> background is a violation.

It does not require a box around every visible person. It also does not invent
a tightness, overlap or minimum-size threshold. This distinction needed
clarification during human review; the original and final answers are retained.

## Earlier two-case smoke run — 21 October 2026

Baseline and one-example candidate both had F1 1.0 on two controlled cases.
Candidate model time was 932.73 seconds versus 447.06 seconds for baseline.
These were operator-delegated fixture references, not independently reviewed
accuracy evidence. They demonstrated the workflow, not an improvement.

## Human-reviewed comparison — 22 October 2026

Twelve overlay images were reviewed without model predictions. One guided
practice case was excluded. Three separate source images were reserved for
training and eight for the comparison. The candidate used only approved
training case 06. Labels and the selection plan were frozen before predictions.

- Baseline: 2 true positives, 0 false positives, 1 false negative and 5 true
  negatives. It matched 7/8 human labels, recall 2/3 and F1 0.80.
- One-example candidate: 1 true positive, 0 false positives, 2 false negatives
  and 5 true negatives. It matched 6/8 labels, recall 1/3 and F1 0.50.
- Both had full coverage and zero unsure/unavailable responses. Full coverage
  does not imply correct answers.
- Model-call totals were 856.96 seconds for baseline and 1,122.67 seconds for
  candidate, about 31% longer. These are not complete job wall-clock timings.
- Both incorrectly passed case 02. Case 12 changed from correct fail to
  incorrect pass. Both detected case 11. The candidate was not activated.

See the exact per-case reasons in `reference_results/reviewed_comparison.json`.
Some explanations describe pedestrians anywhere in the image rather than
checking each annotation region. That suggests a grounding problem; it is a
hypothesis to test, not an established diagnosis.

## Combined detector/Ollama integration

Real sequential audit jobs of 1, 2 and 4 samples completed in 82.65, 84.63 and
194.89 seconds. Detector evidence and verified model responses were persisted
and appeared through HTTP reads. One verified response was unavailable and
remained unavailable. These were seven executions of four distinct images.
Repeated images benefited strongly from caching, so the totals do not establish
a batch-size speedup. They do not measure concurrent capacity or this teammate's
machine. The observations are included only as operational context.

## What the teammate should establish

Test whether explicit cropped regions reduce missed background-only boxes,
while preserving compliant decisions and uncertainty. Compare with a freshly
run raw-image baseline on the same machine/model/settings. Keep historical Mac
results as reference, not as a substitute for that paired control.

An honest negative result, including slower or worse predictions, is useful.
Do not select only cases 02/12 for a final score, change labels to fit outputs,
hide unavailable results, or repeatedly tune on these eight cases and describe
them as unseen. Generalization remains unproven until a new blinded test set is
reviewed and frozen.
