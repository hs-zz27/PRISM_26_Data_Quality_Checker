# LabelMe person pack: first results

Scored on 14 November 2026 with `score_pack.py` against `main` at `f7fb1ac`. Seed `20261114`, 250 photos.
The checks ran with their default settings: `person_coverage` tolerance is the larger of 8 px and 4% of the
person's height. The person model's output for each photo was saved on 9 November 2026 and holds people only,
not carried objects, so "extra background" can be reported slightly more often here than in the application.

These numbers are the starting point for the tightness work. They are not a pass or fail of anything.

## Unchanged outlines

| | Outlines | Reported | Unsure |
|---|---|---|---|
| All 749 human-drawn outlines | 749 | 157 (21%) | 88 (12%) |
| Outlines in one part, at least 100 px tall | 473 | 119 (25%) | |

A person was reported as having no annotation in 9 of the 250 photos.

About one unchanged outline in four is reported. That matches the 33 of 130 measured in
[labelme_end_to_end](../labelme_end_to_end/RESULTS.md) on 29 photos, now on a set nearly six times larger.
Nobody has reviewed these outlines one by one, so the count is an upper bound on wrong reports, not a measured
error rate.

## Seeded errors

| Error | Injected | Reported (fail) | Unsure | Passed | Right kind and place | Already failing before |
|---|---|---|---|---|---|---|
| `head_missing` | 16 | 16 | 0 | 0 | 16 | 5 |
| `arm_missing` | 16 | 16 | 0 | 0 | 16 | 1 |
| `leg_missing` | 16 | 16 | 0 | 0 | 16 | 6 |
| `loose_3` | 16 | 14 | 0 | 2 | 14 | 5 |
| `loose_6` | 16 | 16 | 0 | 0 | 16 | 1 |
| `loose_12` | 16 | 15 | 1 | 0 | 15 | 3 |
| `shrunk_3` | 16 | 15 | 0 | 1 | 14 | 5 |
| `shrunk_6` | 15 | 9 | 6 | 0 | 9 | 6 |
| `overlap_10` | 15 | 12 | 0 | 3 | - | 4 |
| `overlap_30` | 15 | 15 | 0 | 0 | - | 2 |
| `wrong_label` | 16 | not scored here | | | | |
| `annotation_removed` | 15 | 15 | 0 | 0 | 15 | 3 |

How each column is counted is in [README.md](README.md#how-the-score-is-counted).

## What the numbers say

- **A missing head, arm or leg is always reported, in the right place:** 48 of 48, with a `missing` region on
  the part that was cut. The report does not yet say which part it is; that is the body-part work.
- **Loose outlines are reported from the smallest level:** 14 of 16 at 3% of the person's height, all 16 at
  6%. The two that passed were moved by 3 px and 8 px, which is at or under the 8 px minimum tolerance.
- **A badly shrunk outline becomes "unsure" instead of "fail":** 6 of 15 at the 6% level. The outline has lost
  so much area that it no longer matches its person, so the check answers "no matching person". The reviewer
  still sees it, but without the missing regions.
- **Overlap between two people is reported only by accident:** 27 of 30, each time as `extra` background on
  the outline that grew. Nothing says that two people's polygons overlap, and 3 of the 15 small overlaps pass. A
  polygon-overlap check would have to report these directly.
- **A removed annotation is always reported** where the annotation used to be: 15 of 15.
- **The cost is on the unchanged outlines.** The checks catch nearly every seeded error, and report a quarter
  of the outlines nobody changed. Tightening the tolerance further raises that share; the open question for
  the mentor's answer is which of those reports are real.

## Crowds: one, some or all of the people annotated

62 photos with 5 to 13 people each, scored with `crowd.py score` (`unannotated_people`, default settings: a
person is reported when at least 40 px tall and found with at least 90% certainty).

| Dataset | Photos | People annotated | People left out | Photos reported | Left out and reported: 100 px or taller | 40 to 100 px | under 40 px | Reports matching nobody left out |
|---|---|---|---|---|---|---|---|---|
| `labelme_crowd_all` | 62 | 478 | 0 | 5 | - | - | - | 5 |
| `labelme_crowd_some` | 62 | 221 | 257 | 62 | 106 of 114 | 62 of 93 | 2 of 50 | 3 |
| `labelme_crowd_one` | 62 | 62 | 416 | 62 | 173 of 182 | 91 of 139 | 4 of 95 | 4 |

- Every photo with people left out is reported: 62 of 62 in both datasets.
- People at least 100 px tall who were left out are nearly always named (93% and 95%). People of 40 to 100 px
  are named about two times in three. People under 40 px are almost never named, by design: the check does not
  report people that small.
- With everyone annotated, 5 of the 62 photos are still reported. Each is either a person COCO's annotators
  left out or a person the model invented; they have not been looked at one by one.

## Overlap between two people's outlines

Measured with `overlap.py` on 16 November 2026. No check in the application does this yet; this is the
measurement proposed for one.

| Dataset | People | Pairs of people | Pairs sharing any pixel | Sharing 10% or more of the smaller outline | With a thick shared patch |
|---|---|---|---|---|---|
| `labelme_pack_clean` (human-drawn, unchanged) | 685 | 1,259 | 170 | 28 | 2 |
| `labelme_crowd_all` (human-drawn crowds) | 478 | 1,789 | 208 | 41 | 3 |
| Seeded `overlap_10` | | 15 | 15 | 15 | 12 |
| Seeded `overlap_30` | | 15 | 15 | 15 | 15 |

- **Human-drawn outlines of neighbours often share pixels.** 170 of 1,259 pairs do, and 28 pairs share 10% or
  more of the smaller outline. Most are a thin strip where two outlines run along the same boundary, or a small
  person standing behind a large one. A fixed percentage would report all of these.
- **A thick shared patch separates the two.** The shared area is kept only where it is thicker than the
  tightness tolerance (the larger of 8 px and 4% of the shorter person's height) and at least 2 x tolerance^2
  pixels. That leaves 2 of the 170 human-drawn pairs and 27 of the 30 seeded errors. The 3 missed are small
  10% overlaps that form only a narrow band.
- **The model says whose pixels they are.** In 29 of the 30 seeded errors the shared pixels belong to one of
  the two people in the person model's output, so the report can say which outline is wrong.
- This is one tolerance for both rules: a boundary that is a little off is a minor deviation, whether it runs
  into background or into the next person; a patch thicker than that is a noticeable error.
- It assumes the guideline says to outline only the visible part of each person. If a guideline asks for the
  whole person including hidden parts, shared area is expected and this rule does not apply.

## What is not proven

- **Whether the reports on unchanged outlines are right.** They are COCO's human annotations; some are loose.
  A reviewed sample is needed before any of them is called a false alarm.
- **The application itself.** `score_pack.py` calls the check functions directly. Import, the review screen,
  correction and export were not run on this pack; `labelme_end_to_end` covers those on 29 photos.
- **Small or irregular errors.** The smallest outline shift is 3% of the height, and each body-part error
  removes a whole forearm, lower leg or head. A few missing pixels at a boundary were not tried.
- **Carried objects.** The saved model output has no backpacks, umbrellas or bags, which the application allows
  inside an outline.
- **People drawn in several parts and people under 100 px tall** take no seeded outline error.
- **Other photos.** All 250 are COCO validation photos. No Samsung data has been supplied.
