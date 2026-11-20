# Boundary fit

How closely a person's outline follows the person's visible boundary in the photo. It is the measurement
proposed for "tight" after the mentor's reply of 18 November 2026: the outline should follow the person's
boundary closely, small deviations are acceptable, and every rule must be configurable rather than fixed.

The code is `backend/app/services/boundary_fit.py`, with tests in `backend/tests/test_boundary_fit.py`.
**It is not part of any audit yet**: wiring it in touches the check registry and the person check, which belong
to Muskan's work area. See "Wiring it into audits" below.

The first measured results are in [RESULTS.md](RESULTS.md).

## Why a new measurement

The person check compares each outline with the person model's outline of that person. That outline is too
coarse to tell a tight polygon from a loose one: on the example sheet, the person check passes most of the
outlines the mentor rejected (see RESULTS.md).

A tight outline runs along the place where the person meets the background or another object, and that place
is an edge in the photo. So this measures the outline against the photo itself:

- **On the boundary:** the share of the outline that lies on an edge in the photo, within a small tolerance
  (2 px, or 0.8% of the person's height for a tall person).
- **Stretches off it:** each run of the outline that leaves the edges for at least 12 px (or 5% of the
  person's height) is reported as a region of kind `mismatch` ("wrong shape"), with its side and box, in the
  shared region format of `backend/api/AUDITS.md`. The review screen's error close-up already draws this kind.
- **Busy background:** the same outline, moved outward by 3% of the person's height, is measured too. If it
  still lies on edges 80% of the time, the background is too busy to tell, and the result is **unsure**,
  never a pass.
- **The photo's border** is not judged: an outline along it has no edge to follow.
- All the parts drawn for one person are judged together.

Soft boundaries (dark clothes on a dark background, hair, blur) give weak edges. Edges are therefore looked for
at two blur levels, and what counts as an edge is set from each photo's own spread of contrast. A test checks
that a dim, blurred boundary is still found.

No model, no GPU and no service is needed. Finding the edges takes about 20 ms per photo on a laptop CPU, and
each outline a fraction of that. The photo and the annotation file are never changed.

## The pass mark is provisional

`PROVISIONAL_MIN_SHARE` is 0.90: halfway between the weaker of the mentor's two tight outlines (93.5%) and the
best outline he rejected (87.5%). Two tight examples are not enough to set it. It must be set again from
reference outlines agreed with the mentor, and it is meant to become a setting a guideline chooses, not a
constant.

It does not replace the person check. Cutting off an arm or a head moves only a small part of the outline
off the boundary, so this measure barely changes; the person check finds all of those. The two are meant to
run together.

## What is here

| File | What it is |
|---|---|
| `evaluate.py` | Scores the measure against the mentor's labels and the LabelMe person pack. |
| `sir_labels.json` | The mentor's verdict on all 21 people on the example sheet, keyed by COCO annotation id. |
| `RESULTS.md` | The results and what they do and do not show. |

## How to run

From the repository root. It needs the local COCO 2017 person file and photos; the pack tables need the
LabelMe person pack built (`evaluation/labelme_person_pack/README.md`).

```sh
.venv/bin/python -B -m pytest backend/tests/test_boundary_fit.py -q -p no:cacheprovider
.venv/bin/python -B evaluation/boundary_fit/evaluate.py                 # about 20 seconds
.venv/bin/python -B evaluation/boundary_fit/evaluate.py --variants      # also the edge finders that were tried, about 1 minute
.venv/bin/python -B evaluation/boundary_fit/evaluate.py --person-check  # also today's person check; needs its model file
```

## Wiring it into audits

Left to the owner of the person check and the check registry. What it needs:

1. Compute `edge_distance()` once per photo and `boundary_fit()` once per person, with all parts that share a
   `group_id` passed together.
2. Report its `mismatch` regions in the finding's evidence. The cleanest place is inside the person check's
   `polygon` geometry, next to its `missing` and `extra` regions: the frontend reader `findingRegions()` then
   shows them with no change. A separate check would need its own branch in that reader.
3. Add `length_px` to the region table in the "Finding regions" section of `backend/api/AUDITS.md`, and the
   pass mark as a setting the guideline chooses (for example tight, normal or loose), not a fixed number.
4. Keep "busy background" and "nothing to judge" as **unsure**.
