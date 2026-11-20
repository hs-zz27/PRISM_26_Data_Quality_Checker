# Boundary fit: first results

Measured on 21 November 2026 with `evaluate.py` on `main` at `bc288fa` plus this branch. Laptop CPU, no model.

These are a first check of the idea, not an accuracy figure: only two outlines are known to be tight.

## Against the mentor's verdict

The mentor judged every person outline on the example sheet (reply of 18 November 2026): the fire performer
and the bride are tight; the other 19, including the thin context outlines, are not tight enough
(`sir_labels.json`).

| Mentor's verdict | People | Today's person check: pass | fail | unsure |
|---|---|---|---|---|
| tight | 2 | 2 | 0 | 0 |
| not tight | 19 | 12 | 5 | 2 |

The person check passes 12 of the 19 outlines he rejected. Boundary fit puts the two tight outlines above all
19 others:

| Mentor | Photo | Annotation | On the boundary | Stretches off it | Provisional result |
|---|---|---|---|---|---|
| tight | 000000476514 | /shapes/1 (bride) | 100.0% | 0 | pass |
| tight | 000000258388 | /shapes/2 (fire performer) | 93.5% | 2 | pass |
| not tight | 000000476514 | /shapes/0 | 87.5% | 2 | fail |
| not tight | 000000580197 | /shapes/0 | 87.3% | 2 | fail |
| not tight | 000000153229 | /shapes/7, /shapes/8 | 87.2% | 2 | fail |
| not tight | 000000153229 | /shapes/9 | 84.8% | 5 | fail |
| not tight | 000000153229 | /shapes/1, /shapes/2 | 84.2% | 3 | fail |
| not tight | 000000580197 | /shapes/2 (white-haired man) | 83.1% | 3 | fail |
| not tight | 000000153229 | /shapes/4 | 82.9% | 4 | fail |
| not tight | 000000153229 | /shapes/5, /shapes/6 | 82.8% | 3 | fail |
| not tight | 000000174371 | /shapes/0 | 79.7% | 5 | fail |
| not tight | 000000153229 | /shapes/3 | 79.3% | 1 | fail |
| not tight | 000000258388 | /shapes/0 | 71.0% | 6 | fail |
| not tight | 000000174371 | /shapes/3, /shapes/4 | 69.7% | 4 | fail |
| not tight | 000000174371 | /shapes/6 | 69.5% | 2 | fail |
| not tight | 000000258388 | /shapes/1 | 68.8% | 2 | fail |
| not tight | 000000174371 | /shapes/1 | 66.7% | 4 | fail |
| not tight | 000000153229 | /shapes/0 | 64.8% | 6 | fail |
| not tight | 000000174371 | /shapes/2 | 62.3% | 10 | fail |
| not tight | 000000174371 | /shapes/5 | 55.7% | 6 | fail |
| not tight | 000000580197 | /shapes/1 | 42.5% | 5 | fail |

The provisional pass mark of 90% sits in the gap between 93.5% and 87.5%. With two tight examples that gap
proves little; it was chosen after seeing these labels.

The stretches off the boundary land where an outline cuts a corner or stands off the body: around loose
heads, along coarse legs and across arms. The white-haired man is reported for his loose head outline. His
outline's overlap with the head in front of him is **not** among the stretches, because hair is textured and
the outline crosses it on edges; that error needs the overlap check, not this one.

## On the 250-photo LabelMe person pack

473 one-part human-drawn outlines at least 100 px tall (COCO 2017 outlines, which the mentor's verdict
suggests are mostly not tight).

- On the boundary, 10th / 25th / 50th / 75th / 90th percentile: 60.1% / 71.9% / 84.5% / 91.2% / 96.3%.
- At the provisional mark: 125 pass, 338 fail, 10 unsure (busy background).
- The same outline moved outward by only 4 px falls by a median of 23.6 points, and falls in 97% of cases.

Seeded errors, the outline before and after the error was added:

| Seeded error | Outlines | On the boundary before | After | Fell by 10 points or more |
|---|---|---|---|---|
| `loose_3` (3% of the height outward) | 16 | 88.5% | 58.0% | 88% |
| `loose_6` | 16 | 86.4% | 43.6% | 94% |
| `loose_12` | 16 | 83.4% | 47.1% | 75% |
| `shrunk_3` (3% inward) | 16 | 81.3% | 61.4% | 62% |
| `shrunk_6` | 15 | 82.8% | 55.6% | 87% |
| `head_missing` | 16 | 85.4% | 80.1% | 19% |
| `arm_missing` | 16 | 84.0% | 80.3% | 12% |
| `leg_missing` | 16 | 79.7% | 79.8% | 12% |

It reacts strongly to loose and shrunk outlines and hardly at all to a missing body part, because the rest of
that outline still follows the body. The person check found all 48 missing parts in the pack, so the two
measures are complementary. A very loose outline (`loose_12`) can run into the edges of other objects, which is
why it falls less often than `loose_6`.

## Edge finders that were tried

Same measure, different ways of finding edges in the photo (`evaluate.py --variants`, whole pack):

| Edge finder | Weaker tight outline | Best not tight | Not tight at or above it | Pack median | Moved 4 px: median fall | `loose_3` fell 10+ | `shrunk_3` fell 10+ |
|---|---|---|---|---|---|---|---|
| brightness thresholds, one blur | 93.1% | 93.2% | 1 of 19 | 79.8% | 27.1 | 81% | 62% |
| brightness thresholds, two blurs | 93.7% | 93.5% | 0 of 19 | 80.8% | 26.7 | 88% | 56% |
| contrast boosted | 94.2% | 95.5% | 2 of 19 | 86.7% | 20.8 | 69% | 69% |
| **contrast-spread thresholds, two blurs (built)** | 93.5% | 87.5% | 0 of 19 | 84.5% | 23.6 | 88% | 62% |
| contrast boosted, two blurs | 94.7% | 96.1% | 1 of 19 | 87.7% | 20.6 | 75% | 62% |
| boosted, two blurs, colour | 94.7% | 96.3% | 2 of 19 | 89.7% | 20.9 | 75% | 69% |

The built version was chosen because it keeps the widest gap on the mentor's labels while staying among the
most sensitive to loose outlines, and because it is the only one that finds all of a dim, blurred boundary
(brightness 50 against 70, blurred by 3 px, as in `backend/tests/test_boundary_fit.py`): it finds 100% of it,
the contrast-boosted versions 47% and the brightness-threshold versions none. Boosting contrast or adding
colour edges also finds edges almost everywhere in real photos, which makes loose outlines look tight. Every
version answers unsure on the busy-background test.

## What is not shown

- **Where the pass mark belongs.** Two tight examples cannot set it. A set of reference outlines drawn to the
  mentor's standard, and checked by him, is needed.
- **Outlines that follow the wrong edge.** An outline that runs along an edge that is not the person's
  boundary, for example along a shirt pattern, can still score well. The person check covers part of this.
- **Overlap between two people.** See the white-haired man above.
- **Results inside an audit.** The measure is not wired into audits yet (README.md).
- **Other photos.** Only COCO 2017 photos were used.
