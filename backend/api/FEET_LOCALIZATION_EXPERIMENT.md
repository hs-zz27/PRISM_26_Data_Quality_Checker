# Independent visible-feet localization experiment

**Status: parked.** This research path is not part of the prototype's active audit/review workflow.
Do not add it to production routing or use it as a prerequisite for the demo. The priority is checking
supplied annotations against guidelines, visual rechecking after comments, and human corrections.

The owner's first real run, `feet_C01_20261103T233128`, returned a supposed foot region at x=600–660
for an image only 480 pixels wide. Validation correctly returned `invalid_localization` for both boxes.
The result establishes neither successful localization nor annotation accuracy. Preserve the report;
no further localization fix or model run is included in the return-to-core change below.

## Purpose and scope

C01 exposed a visual miss: v6 passed a box whose bottom edge excluded the shoes. Text feedback helped
flag that box but also made the correct baseline fail. Separating feedback from visual inspection
removed that direct influence, but the independent inspection still missed the shortened box.

This experiment replaces the direct box-compliance question with two distinct operations:

1. Make one bounded local-model request containing the original unmarked image, its dimensions, and
   a neutral description identifying the intended person. It contains no annotation, box, polygon,
   reviewer complaint, guideline verdict, answer key, crop or overlay.
2. Save the model's estimated lower-leg/foot extent ranges. Compare that same saved evidence with
   both candidate boxes in ordinary Python code. No second model call decides containment.

The reusable modules live in `models/feet_localization.py`, `services/feet_localization.py`, and
`inference/feet_localization.py`. The opt-in command is
`evaluation/annotation_error_pack/tools/verify_feet_localization.py`.

This is **not enabled in the production audit or rule-extraction router**. It is a selected profile for
the requirement that a fully visible person's box includes visible lower legs and footwear. The caller
must select the correct confirmed rule and target identity; this tool does not interpret arbitrary
guideline semantics or infer rule applicability from a rule ID. It does not check box tightness, head or
hand inclusion, polygons, masks, hidden anatomy, or annotation labels.

## Evidence and containment

`visible-feet-localization-v1` has its own recorded execution settings and request hash. Existing bounded
HTTP, local-model identity checks, completion checks and time limits are reused as transport only; v6
prompts and overlays are not used. There is one attempt and no automatic retry.

The model must identify two distinct lower-leg/foot regions. Each includes the knee-to-foot visible
extent and the entire shoe, heel and toe. It reports a low/high estimate for each extremal x/y edge in
original-image pixel-edge coordinates. Incomplete regions, duplicate identities, inverted ranges,
non-finite values, wrong response identity and out-of-image coordinates cannot produce a verdict.
Ambiguous identity or localization remains uncertain. Model-reported non-person/partial visibility
produces a provisional not-applicable result for this fully-visible-person profile, requiring review.
Other anatomy/visibility cases are outside this prototype; it must not guess hidden feet.

For every region, the backend compares each edge range with the candidate box:

- A wholly outside range proposes a failure.
- All ranges wholly inside or touching the box propose a pass.
- A range crossing a boundary stays uncertain unless another edge is already wholly outside.

No arbitrary pixel tolerance is added. These outcomes are conditional on model-estimated extents. The
ranges are **not calibrated confidence intervals**. A model can misidentify a person, omit anatomy,
misplace coordinates, or underestimate its own uncertainty. A valid JSON response and deterministic
comparison do not establish visual accuracy. Every result carries `review_required=true`.

COCO/LabelMe boxes use normalized-reader zero-based continuous xyxy coordinates. The comparison utility
also explicitly converts Penn-Fudan one-based inclusive boxes to continuous pixel edges as
`[xmin-1, ymin-1, xmax, ymax]`; the current ZIP experiment command supports COCO and LabelMe only.
Derived boxes and invalid/out-of-image supplied boxes are unavailable for this check.

## Verification and review

Run the new tests yourself:

```sh
.venv/bin/python -B -m pytest backend/tests/test_feet_localization.py -q -rs -p no:cacheprovider
```

Synthetic tests cover all four edges, small cuts versus uncertainty, inclusive-pixel conversion,
source-image binding, supplied-box provenance, one-call reuse, malformed responses, timeouts, model
identity changes, and context/image bounds. They do not establish real-model accuracy.

On 3 November, the owner authorized running this test file after 30 failures. The cause was PNG metadata
access loading the image before Pillow's `verify()`. Validation now verifies immediately after opening,
then reopens for frame/orientation checks and full decoding. Added PNG/JPEG tests cover EXIF orientation,
unchanged transmitted bytes, and truncated-image rejection. The assistant reran the file: **37 passed**.
No real-model inference was run.

For C01, the prepared neutral description is “The person wearing a red vest and blue jeans in front of
the stone wall”. This was selected from the source image, not from its error annotation. The supplied
baseline box ends at y=599.99; the altered box ends at y=486.914. Neither value enters localization.

The command creates a new output directory and refuses to overwrite one. It saves:

- The exact original image bytes, request text (without base64), source hashes and selected rule.
- `localization.json`, including execution identity and bounded diagnostics for invalid responses.
- `report.json`, with both comparisons and code hashes.
- Input previews and localization previews for both candidate boxes.

Blue is the supplied box; orange/cyan are the widest/tightest estimated rectangles. These are extent
estimates, not segmentation silhouettes. Previews are generated **after** localization and never sent
to the model. Inspect whether both regions actually cover the intended person's complete visible lower
legs and footwear before accepting even a baseline-pass/altered-fail pair. An unclear/not-applicable or
invalid result is unresolved evidence, not a successful detection.

`--prepare-only` makes no model call. `--reuse-localization` accepts a saved result only when image,
description, image size and exact request hash match; it performs no inference. Earlier failed reports
are preserved. No annotations, audit records, feedback policies or reviewer decisions are changed.

Only after review of the correct and altered examples, including small-error and ambiguity cases,
should this profile be considered for explicit production rule binding and frontend integration.
