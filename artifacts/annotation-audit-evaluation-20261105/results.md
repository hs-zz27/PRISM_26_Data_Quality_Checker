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
| v6 wording with neutral color terms, altered target-only | Fail / applies / no; 494.4 s | It described the magenta target as a non-person silhouette to the right of the couple, incorrectly marking background rather than the actual person. | This explanation refers to the shifted target and its visible displacement. It is the clearest altered-case result. |
| Same neutral wording, baseline target-only (5 November) | Pass / applies / yes; 465.7 s | It said the polygon outlines "the person (man and woman)". | The outcome matched the expected result and there was no false alarm, but the explanation merged the two people. The right outcome came with wrong reasoning. Both neutral runs still say "magenta", which the model reads from the image. |

The earlier four-request attempt at a 600-second timeout timed out while Ollama was still processing the images; it returned no assessments. The focused runs that produced assessments used the 1,200-second diagnostic timeout. That timeout is not a production setting.

## Segmentation proof (offline, not part of the application)

Torchvision Mask R-CNN (`maskrcnn_resnet50_fpn_v2`, COCO weights, CPU) predicted person masks from the image alone. Code then matched each supplied polygon to a predicted person and measured overlap and boundary distance. The segmenter never saw the annotations. No tolerance was applied: these numbers are evidence, not verdicts. See `segmentation/report.json`, the two overlays and `segmentation/run.py`.

| Supplied polygon | IoU with matched person | Share of polygon outside the person | Mean / 95th-percentile boundary distance |
|---|---|---|---|
| Man, baseline | 0.895 | 3.6% | 2.1 px / 7.6 px |
| Man, shifted 76.8 px | 0.001 | 99.8% | 50.4 px / 78.0 px |
| Woman (unchanged in both) | 0.895 | 4.3% | 1.6 px / 5.0 px |

Inference took 1.1 to 1.3 s per image, against 425 to 867 s for the visual-language model. The overlays confirm that predicted person 0 is the man and person 1 is the woman, so every polygon was matched to the right person. The woman's polygon overlaps her predicted outline as closely as the man's correct polygon does, which gives no support to the visual model's repeated claim that it includes the man's arm.

Limits: one image and one large error. Correct polygons already differ from the predicted mask by about 2 px on average and about 8 px at the 95th percentile, so a small error could be lost in that noise. The accept/reject threshold would have to come from a reviewer-confirmed rule parameter. Mask R-CNN knows only COCO classes, and it does not judge semantic rules such as posters or hidden body parts. It is not integrated into the audit pipeline.

## Conclusion

The visual-language model sometimes returns the expected overall outcome, but its explanation can refer to the wrong person or outline. Removing the other outlines did not fix this. With target-only overlays and color-neutral wording, the altered case failed with a relevant explanation and the matching baseline passed with no false alarm, so the outcomes now discriminate on this pair. The baseline explanation still merged both people, so the outcome cannot be trusted without its reasoning. A single pair does not prove causation or reliability. Keep findings as reviewer-facing proposals; these results do not justify automatic decisions.

An independent segmentation check separated the correct and shifted polygons by a wide margin, in about a second and without any identity confusion. That supports testing a segmentation-based geometry signal on the real data, alongside the visual model for rules that need language. It does not yet show that such a check works on small errors or other classes.

## Included source reports

- `reports/v7-visual-evaluation.json`
- `reports/v6-timeout-at-600s.json`
- `reports/v6-baseline.json`
- `reports/v6-altered-with-context-overlays.json`
- `reports/v6-altered-target-only-overlay.json`
- `reports/v6-neutral-color-terms-pending.json` (incomplete snapshot from the run before its completion)
- `reports/v6-neutral-color-terms-altered-target-only.json` (completed result)
- `reports/v6-neutral-color-terms-baseline-target-only.json` (matching baseline, 5 November)
- `segmentation/report.json`, `segmentation/baseline-overlay.png`, `segmentation/altered-overlay.png`, `segmentation/run.py`
