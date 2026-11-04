# Evaluation results

## Guideline extraction

The local CPU run retained all 10 guideline requirements, produced no duplicate rules, matched the expected requirement-to-source mappings, and preserved source/context text. The occlusion requirement retained both box and polygon geometry. An unsupported numeric requirement was retained rather than silently dropped. It took 166.9 seconds and produced a **local draft only**; it was not written to the database or confirmed automatically.

See `extraction/extraction-validation.json` and `extraction/extracted-rulebook.json`.

## Visual audit

| Run | Result | What the explanation said | Interpretation |
|---|---|---|---|
| v7 prompt, baseline/altered and feedback cases | All four aggregate results were unsure | The model raised identity/boundary uncertainty; feedback did not consistently resolve it. | Conservative review behavior, but no useful discrimination in this example. |
| v6 wording, baseline | Pass / applies / yes; 509.2 s | It described the target polygon as outlining “the person (man and woman)”. | The baseline outcome matched the expected result, but the explanation merged the two people, so treat it cautiously. |
| v6 wording, altered with context outlines | Fail / applies / no; 427.0 s | It described a cyan polygon around the woman, although the target was the man's magenta `/shapes/0` polygon. | The fail outcome matched expectation, but its explanation identified the wrong annotation/person. This is not a reliable detection. |
| v6 wording, altered with target-only overlays | Fail / applies / no; 425.0 s | It repeated the same cyan/woman explanation. | Removing other annotation outlines did not correct the identity error. The saved overlay files contain magenta target pixels and zero exact cyan pixels. |
| v6 wording with neutral color terms, altered target-only | Fail / applies / no; 494.4 s | It described the magenta target as a non-person silhouette to the right of the couple, incorrectly marking background rather than the actual person. | This explanation refers to the shifted target and its visible displacement. It is the clearest altered-case result so far, but the matching baseline with this wording has not been run. |

The earlier four-request attempt at a 600-second timeout timed out while Ollama was still processing the images; it returned no assessments. The focused runs that produced assessments used the 1,200-second diagnostic timeout. That timeout is not a production setting.

## Conclusion

The model sometimes returns the expected overall outcome, but its explanation can refer to the wrong person or outline. Removing the other outlines did not fix this. With target-only overlays and color-neutral request wording, the altered case produced a relevant explanation. This suggests the earlier color wording may have influenced the response, but a single altered case does not prove causation or reliability. Run the matching baseline with the same neutral wording to check for a false alarm. Keep findings as reviewer-facing proposals; these results do not justify automatic decisions.

## Included source reports

- `reports/v7-visual-evaluation.json`
- `reports/v6-timeout-at-600s.json`
- `reports/v6-baseline.json`
- `reports/v6-altered-with-context-overlays.json`
- `reports/v6-altered-target-only-overlay.json`
- `reports/v6-neutral-color-terms-pending.json` (incomplete snapshot from the run before its completion)
- `reports/v6-neutral-color-terms-altered-target-only.json` (completed result)
