# Human review of the evaluation answer key

**Status: REVIEWED by a human reviewer (Muskan), 2026-10-31.** The visual judgements in `answer_key.json` were originally made by an AI assistant; this sheet records a human review of them.
The initial wording of the rows below was drafted by an AI assistant from the comparison images. The reviewer checked each row against the images and edited it.
Muskan's signed review covers C01-C05, L02 and L03. L01 has since been implemented using a new source
pair and reviewed separately by Harkamal Singh on 2026-10-31. The 3-pixel tolerance in guideline 9 is still unapproved
by the project owner. The seven original answer-key cases are unchanged; L01's new expectations
for guideline 10 are human-confirmed, with the boundary concerns recorded below. No case in the signed review is marked Disagreed.

| Field | Value |
|---|---|
| Reviewer name | Muskan |
| Review date | 2026-10-31 |
| How the images were viewed (screen size / zoom) | |
| Guidelines version reviewed | `guidelines.txt` (team-authored development guidelines, not Samsung requirements) |

## How to review

1. Build the images: `python -B evaluation/annotation_error_pack/build_pack.py`, then open `evaluation/annotation_error_pack/comparisons/<case>.png` (generated, not committed).
2. For each case, judge the **baseline target**, then the **altered target**, then **the other annotations on the image** (the answer key reviews only the documented target, so say which others you looked at).
3. Mark each result **Confirmed** (you agree with the answer key), **Uncertain** (the visible boundary or the guideline cannot be settled), or **Disagreed** (explain with what you see; do not edit the answer key until the evidence is discussed).
4. Guideline 9's 3-pixel tolerance is a proposal that the project owner has not approved. Do not mark a box as exactly compliant from box-to-polygon agreement alone; use Uncertain where the true boundary cannot be established.
5. Known unrelated issues (for example the worn backpack in C04 and the existing polygon diagnostics in C03) must stay documented; do not treat them as errors of the case.

## C01 - COCO 000000199055.jpg

- Target: `/annotations/91` (COCO annotation 424921)
- Deliberate error: Bottom edge raised by 20% of the original box height (height x 0.8); x, y and width unchanged.
- Primary guideline: 6; applicable: [2, 5, 6, 7, 8, 9, 12]
- Answer key says: baseline `pass` (g2=satisfied, g5=satisfied, g6=satisfied, g7=satisfied, g8=not_applicable, g9=satisfied, g12=not_applicable); altered `fail` (g2=satisfied, g5=satisfied, g6=not_satisfied, g7=not_satisfied, g8=not_applicable, g9=satisfied, g12=not_applicable)
- Answer key related finding: Segmentation now extends 113.076 px below the box bottom edge (box/polygon disagreement); the polygon itself is unchanged.
- Answer key related finding: The 'area' field is unchanged; it describes the unchanged polygon.
- Answer key known unrelated issue: No reader or geometry diagnostics in this image.
- Answer key known unrelated issue: The skateboard annotation (id 638445) overlaps the person's feet; this is expected scene overlap, not an annotation error.

| Item | Result (Confirmed / Uncertain / Disagreed) | Explanation (what you see) |
|---|---|---|
| Baseline target, guidelines listed above | Confirmed for 2, 5, 6, 7; Uncertain for 9 | A man in a red vest and denim jacket standing with a foot on a skateboard. The top of his hair is at the top edge of the green box, the raised hand and the other elbow reach the side edges, and both white shoes are inside the bottom edge. Guideline 9 is not judged: the 3 px tolerance is unapproved and I could not measure the edges to the pixel. |
| Baseline: other annotations on the image (list those checked) | Confirmed | One other annotation is visible: the thin grey box and outline around the skateboard at the bottom. It sits around the board and its wheel. Checked 1 of 1. |
| Altered target, primary guideline | Confirmed | The red box now ends around the lower legs, so both shoes and the skateboard are outside it. The dashed white line shows the old bottom edge, and the unchanged outline now runs below the box. Breaks guideline 6 and also 7. |
| Documented unrelated issues still accurate | Confirmed | The skateboard annotation overlaps his feet, as expected for a person standing on a board. The report (coco_preview.txt) shows no flagged objects for this image. |

## C02 - COCO 000000001000.jpg

- Target: `/annotations/139` (COCO annotation 1234574)
- Deliberate error: Top edge lowered by 20% of the original box height; bottom edge, x and width unchanged.
- Primary guideline: 5; applicable: [2, 5, 6, 7, 8, 9, 12]
- Answer key says: baseline `pass` (g2=satisfied, g5=satisfied, g6=satisfied, g7=satisfied, g8=not_applicable, g9=satisfied, g12=not_applicable); altered `fail` (g2=satisfied, g5=not_satisfied, g6=satisfied, g7=not_satisfied, g8=not_applicable, g9=satisfied, g12=not_applicable)
- Answer key related finding: Segmentation now extends 45.682 px above the box top edge (box/polygon disagreement); the polygon itself is unchanged.
- Answer key related finding: The 'area' field is unchanged; it describes the unchanged polygon.
- Answer key known unrelated issue: Source /annotations/143 (person 1303041) and /annotations/144 (person 1304800) carry repeated_polygon_vertex and self_intersecting_polygon diagnostics; they are not the target.
- Answer key known unrelated issue: Crowded group scene: several person boxes overlap the target box.

| Item | Result (Confirmed / Uncertain / Disagreed) | Explanation (what you see) |
|---|---|---|
| Baseline target, guidelines listed above | Confirmed for 2, 5, 6, 7; Uncertain for 9 | A child in a white shirt and cap standing with hands on hips. The box top is at the top of the cap, the bottom is at the shoes, and both elbows reach the side edges. The outline is coarse and cuts a V between the legs. Guideline 9 not judged (tolerance unapproved). |
| Baseline: other annotations on the image (list those checked) | Uncertain | Crowded group. Checked the child with the yellow cap on the left and the child in the red cap on the right; both grey boxes sit around their own child. The other boxes overlap too much to judge. |
| Altered target, primary guideline | Confirmed | The red box top is now around shoulder level, so the whole head and cap are above it. The dashed white line shows the old top. Breaks guideline 5 and also 7. |
| Documented unrelated issues still accurate | Confirmed | The report shows annotations 143 and 144 flagged with repeated_polygon_vertex and self_intersecting_polygon. Several person boxes overlap the target box, as the sheet says. |

## C03 - COCO 000000153229.jpg

- Target: `/annotations/93` (COCO annotation 495199)
- Deliberate error: Top edge moved outward (upward) by 20% of the original box height into clear background.
- Primary guideline: 9; applicable: [2, 5, 6, 7, 8, 9, 12]
- Answer key says: baseline `pass` (g2=satisfied, g5=satisfied, g6=satisfied, g7=satisfied, g8=not_applicable, g9=satisfied, g12=not_applicable); altered `fail` (g2=satisfied, g5=satisfied, g6=satisfied, g7=satisfied, g8=not_applicable, g9=not_satisfied, g12=not_applicable)
- Answer key related finding: The box now extends 59.42 px above the top of the polygon (box/polygon disagreement); the polygon itself is unchanged.
- Answer key known unrelated issue: EXISTING on the target itself (not introduced by this case): source /annotations/93 has repeated_polygon_vertex and self_intersecting_polygon diagnostics near the left leg. They are present in both the baseline and the altered archive.
- Answer key known unrelated issue: Because of that self-intersection, guideline 10 (polygon follows the person) is uncertain for this target in both versions; guideline 10 is not tested by this case.

| Item | Result (Confirmed / Uncertain / Disagreed) | Explanation (what you see) |
|---|---|---|
| Baseline target, guidelines listed above | Confirmed for 2, 5, 6, 7, 8; Uncertain for 9 and 10 | A player in a green shirt and red cap, seen from behind, with one leg raised. The box top is at the top of the cap, the standing foot is at the bottom edge and the raised white shoe at the right edge. Arms and the raised leg are inside. The outline is coarse and has a narrow spike at the left leg, so I cannot judge guideline 10 or the exact edges. |
| Baseline: other annotations on the image (list those checked) | Uncertain | Looked at the two players jumping on the left and the player at the right edge; their grey outlines sit around them. The group at the lower left overlaps too much to judge. |
| Altered target, primary guideline | Confirmed | The red box top is now far above the cap, in the house wall and window behind him. The strip between is empty background, much more than 3 px. The dashed white line marks the old top. Breaks guideline 9, which is still unapproved. |
| Documented unrelated issues still accurate | Confirmed | The report shows annotation 93 flagged with repeated_polygon_vertex and self_intersecting_polygon, in both versions. Guideline 10 is therefore uncertain for this player and not tested by this case. |

## C04 - COCO 000000100624.jpg

- Target: `/annotations/215` (COCO annotation 494051)
- Deliberate error: category_id changed from the source person category to the source car category.
- Primary guideline: 4; applicable: [1, 2, 3, 4, 12]
- Answer key says: baseline `pass` (g1=satisfied, g2=satisfied, g3=not_applicable, g4=satisfied, g12=not_applicable); altered `fail` (g1=satisfied, g2=satisfied, g3=not_applicable, g4=not_satisfied, g12=not_applicable)
- Answer key related finding: Only the category changes; the geometry still outlines a person, so a label-versus-content check is the only way to detect it.
- Answer key related finding: The car category already exists in the source categories; no category was created.
- Answer key known unrelated issue: No reader or geometry diagnostics in this image.
- Answer key known unrelated issue: BASELINE CONFLICT, recorded not resolved: the target's box and polygon include the worn backpack on the left, which also has its own annotation (backpack 1170623). The guidelines do not say whether worn items count as part of the person, so guidelines 9 and 10 are uncertain for this target. They are not tested by this case.
- Answer key known unrelated issue: The woman is cut off by the bottom image edge (guideline 8 applies; guideline 6 does not).

| Item | Result (Confirmed / Uncertain / Disagreed) | Explanation (what you see) |
|---|---|---|
| Baseline target, guidelines listed above | Confirmed | The label says person and the outline contains a woman talking on a phone. She is cut off by the bottom edge of the photo and the box ends there. The label matches what is inside, and she is not a car. |
| Baseline: other annotations on the image (list those checked) | Uncertain | Checked the backpack box at the lower left, the small phone box at her ear, and the people in the blurry background; they sit around plausible objects. The small box at the top left is too small to identify. |
| Altered target, primary guideline | Confirmed | The label now says car, but the box and outline are unchanged and still contain the woman. Breaks guideline 4. Guideline 1 still applies because it is about what the image shows. |
| Documented unrelated issues still accurate | Uncertain | The box and outline include the dark backpack strap and bag at her left, and the backpack also has its own grey box, as the sheet says. The guidelines say nothing about worn items, so I cannot decide guidelines 9 and 10; this needs an owner decision. The report shows no flagged objects for this image. |

## C05 - COCO 000000085157.jpg

- Target: `/annotations/169` (COCO annotation 430467)
- Deliberate error: Every vertex of segmentation part 0 shifted right by 10% of the image width.
- Primary guideline: 10; applicable: [2, 8, 10, 11, 12]
- Answer key says: baseline `pass` (g2=satisfied, g8=satisfied, g10=satisfied, g11=satisfied, g12=not_applicable); altered `fail` (g2=satisfied, g8=satisfied, g10=not_satisfied, g11=uncertain, g12=not_applicable)
- Answer key related finding: The polygon now extends 64 px beyond the box right edge and leaves a 64 px gap inside the box left edge (box/polygon disagreement); the box is unchanged.
- Answer key related finding: The 'area' field is unchanged; a translation does not change polygon area.
- Answer key known unrelated issue: Source /annotations/167 (chair 387255) and /annotations/174 (bowl 709624) carry repeated_polygon_vertex and self_intersecting_polygon diagnostics; they are not the target.
- Answer key known unrelated issue: Crowded dining scene with many overlapping annotations.

| Item | Result (Confirmed / Uncertain / Disagreed) | Explanation (what you see) |
|---|---|---|
| Baseline target, guidelines listed above | Confirmed for 2, 8, 10 (coarse), 11 | A seated woman in a pink top. The cyan outline follows her hair, face, shoulder and raised hand; it is coarse and has a notch at the lower left. Her lower body is hidden by the table and the outline stops at the table without covering anything hidden. One polygon part, containing only her. |
| Baseline: other annotations on the image (list those checked) | Uncertain | Checked the woman in the blue pattern at the left, the woman with the ponytail in the foreground, and the man in the striped shirt; their grey outlines sit around them. Many other grey shapes overlap on the table and could not be judged. |
| Altered target, primary guideline | Confirmed for 10; Uncertain for 11 | The magenta outline has moved to the right and now lies over the back of the head and hair of the woman in the foreground, with her own face outside it. The red box is unchanged. Breaks guideline 10. It appears to outline part of a different person, but I cannot say whether that counts under guideline 11. |
| Documented unrelated issues still accurate | Confirmed | The report shows annotations 167 (chair) and 174 (bowl) flagged with repeated_polygon_vertex and self_intersecting_polygon. The scene is a crowded dining table with many overlapping annotations. |

## L01 - LabelMe 258421.jpg (human-confirmed, 2026-10-31)

Reviewer: **Harkamal Singh**. Verdict: **Confirmed**. Review source: **Human reviewed**.
This section records his explicit response and is separate from Muskan's earlier review.

- Source: Christian Mills' LabelMe tutorial dataset, pinned in `datasets/labelme_l01_example/download_manifest.json`.
- Target: `/shapes/0`, man on the right in a white shirt and tan trousers; 27 vertices, group_id null.
- Deliberate edit: every target x coordinate increases by 76.8 pixels, exactly 10% of the 768-pixel image width. Y coordinates and the other person's annotation remain unchanged.
- Primary guideline: 10, polygon fit. The reviewer confirmed baseline pass and altered fail, with minor baseline boundary inaccuracies accepted.
- Artifact reviewed: `.cache/visuals/L01-review.png`, a 2x crop. The subsequently generated `comparisons/L01.png` was inspected and matches this reviewed transformation; that inspection does not constitute an additional human review.
- Check the coarse boundary around the visible arm, overlapping people and shoes. No pixel-exact fit is claimed. Guideline 11 for background-only displacement remains uncertain and is not the primary result.

| Item | Result (Confirmed / Uncertain / Disagreed) | Reviewer explanation |
|---|---|---|
| Image rights and attribution | Outside this visual review | The response confirms visual annotation quality. Source and licence information are recorded separately in `datasets/labelme_l01_example/README.md`. |
| Baseline target under guideline 10 | Confirmed | Follows the visible body reasonably well; minor inaccuracies around overlapping arms and lower body, overall acceptable. |
| Altered target under guideline 10 | Confirmed | Clearly misplaced onto background; no longer represents the man's visible silhouette. |
| Other annotation: /shapes/1, woman on the left | Confirmed with boundary ambiguity | Generally follows her visible body; minor ambiguity at the overlap, no significant misalignment. |
| Exceptions or unrelated issues | Boundary concerns retained | No exact pixel accuracy, reader diagnostics, other cases or numeric tolerance was confirmed by this visual review. |

### Reviewer's response (verbatim)

> Name: Harkamal Singh
> Verdict: Confirmed
> Concerns:
> 1. Green outline (baseline, left): The polygon follows the man's visible body reasonably well, including his head, torso, arms, legs, and shoes. Minor inaccuracies are visible around the overlapping arms and lower body, but the overall fit is acceptable.
> 2. Red outline (altered, right): The polygon is clearly misplaced. The stated 76.8-pixel horizontal shift moves it away from the man's actual body and into the background. It no longer represents his visible silhouette and violates the polygon-fit guideline.
> 3. Woman's outline: The white polygon generally follows her visible body accurately. Minor boundary ambiguity exists where the two people overlap, but no significant misalignment is apparent.
> Final Assessment: Confirmed. The green baseline is acceptable, while the shifted red polygon is a clear annotation failure.
> Review source:Human reviewed

Recorded from the project owner's chat response on 2026-10-31. Owner-run automated validation was subsequently recorded on 2026-11-01: 98 builder checks passed, all 13 generated files reproduced, and 57 tests passed. See `verification.md`.

## L02 - LabelMe 2011_000006.jpg

- Target: `/shapes/2` (group_id None)
- Deliberate error: Every point of shape 2 shifted down by 10% of the image height.
- Primary guideline: 10; applicable: [2, 8, 10, 11, 12]
- Answer key says: baseline `pass` (g2=satisfied, g8=satisfied, g10=satisfied, g11=satisfied, g12=not_applicable); altered `fail` (g2=satisfied, g8=satisfied, g10=not_satisfied, g11=satisfied, g12=not_applicable)
- Answer key related finding: The polygon-derived box (LabelMe stores no box) moves down by the same 37.5 px.
- Answer key known unrelated issue: /shapes/0 and /shapes/1 (the other two women) carry self_intersecting_polygon diagnostics; /shapes/3 (chair) carries polygon_outside_image. None is the target.
- Answer key known unrelated issue: The image contains an __ignore__ region (/shapes/5) and grouped sofa parts (group_id 0).

| Item | Result (Confirmed / Uncertain / Disagreed) | Explanation (what you see) |
|---|---|---|
| Baseline target, guidelines listed above | Confirmed | /shapes/2 outlines the woman in the green top, on the right of the three women. The green outline follows her head, hair, green top, arm and jeans; it is coarse. She is partly hidden by the sofa arm and her neighbour, and the outline covers only what is visible. A single shape with group_id None. |
| Baseline: other annotations on the image (list those checked) | Uncertain | Looked at the grey outlines of the other two women and the small shapes at the top right; they sit around plausible objects. I did not check each sofa outline closely. |
| Altered target, primary guideline | Confirmed | The red outline has moved down: it now starts at her chest, so her head is outside it, and its lower end lies on the sofa. The dashed white line shows the original. Breaks guideline 10. |
| Documented unrelated issues still accurate | Confirmed | The report shows self_intersecting_polygon on /shapes/0 and /shapes/1 and polygon_outside_image on /shapes/3. I can see small shapes at the top right and several sofa outlines; the group IDs are not readable in the picture, but the report shows group 0 on the sofa shapes. |

## L03 - LabelMe 2011_000003.jpg

- Target: `/shapes/0` (group_id None)
- Deliberate error: Shape label changed from person to car.
- Primary guideline: 4; applicable: [1, 2, 3, 4, 12]
- Answer key says: baseline `pass` (g1=satisfied, g2=satisfied, g3=not_applicable, g4=satisfied, g12=not_applicable); altered `fail` (g1=satisfied, g2=satisfied, g3=not_applicable, g4=not_satisfied, g12=not_applicable)
- Answer key related finding: Only the label changes; the outline still fits a person.
- Answer key known unrelated issue: No reader or geometry diagnostics in 2011_000003.
- Answer key known unrelated issue: /shapes/2 is a 4-point person part grouped (group_id 0) with /shapes/1 near the bottle; it plausibly outlines that man's hand. Not the target; not verified.

| Item | Result (Confirmed / Uncertain / Disagreed) | Explanation (what you see) |
|---|---|---|
| Baseline target, guidelines listed above | Confirmed | The label says person and the outline contains a crouching man in a teal and blue jacket and black hat, fully visible from hat to sandals. The label matches; he is not a car. |
| Baseline: other annotations on the image (list those checked) | Uncertain | Looked at the outline of the man on the right and the bottle; both sit around their objects. I did not check the food basket at the bottom. |
| Altered target, primary guideline | Confirmed | The label now says car on the same man; the outline is unchanged. Breaks guideline 4. Guideline 1 still applies. |
| Documented unrelated issues still accurate | Uncertain | The report shows no flagged objects for this image. The sheet says /shapes/2 is a small 4-point part near the bottle that probably outlines a hand. I can see thin outlines around the bottle and the hand holding it, but I cannot tell which of them is /shapes/2. |

## Summary

| Case | Overall result | Needs owner decision? |
|---|---|---|
| C01 | Confirmed | Yes: guideline 9 tolerance |
| C02 | Confirmed | Yes: guideline 9 tolerance |
| C03 | Confirmed (guideline 10 uncertain, not tested by this case) | Yes: guideline 9 tolerance |
| C04 | Confirmed (guidelines 9 and 10 uncertain) | Yes: do worn items count as part of the person? |
| C05 | Confirmed (guideline 11 uncertain) | Optional: does a displaced outline over another person count under guideline 11? |
| L01 | Confirmed for guideline 10 by Harkamal Singh; boundary concerns retained | No further visual decision required for the primary result; owner-run automated validation passed |
| L02 | Confirmed | No |
| L03 | Confirmed (shape 2 not verified) | No |

Signature for the seven original cases (name and date): Muskan, 2026-10-31
