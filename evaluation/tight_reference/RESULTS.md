# Tight reference outlines: first results

Drawn by machine with `draw.py` on 21 November 2026, every outline checked by eye at full size
(`review.json`), and scored with `compare.py --review review.json`. None has been approved by the mentor yet.

## What was drawn

24 photos, 69 people. 65 outlines were redrawn; 4 kept COCO's outline because the drawing strayed from the
person. After the check, **47 people are kept as references** and 22 are left out (see README.md for the
reasons, and `review.json` for each person).

The first drawing lost the lower legs of several people on carpet or grass: shrinking COCO's outline to find
the "certainly the person" core erased thin limbs. The core now also keeps each limb's middle line where COCO's
outline and the person model agree, which brought the legs back without making any other outline worse.

## How closely the references follow the boundary

| Kind of photo | People | On the boundary: COCO (median) | Redrawn (median) | Points per outline: COCO -> redrawn (median) |
|---|---|---|---|---|
| one person | 8 | 89.2% | 98.0% | 82 -> 113 |
| two people close together | 7 | 83.8% | 98.1% | 61 -> 106 |
| partly hidden | 9 | 86.5% | 97.8% | 50 -> 89 |
| crowd | 5 | 87.8% | 99.5% | 54 -> 80 |
| the mentor's example sheet | 18 | 81.2% | 93.6% | 38 -> 85 |

## The pass mark: two sources disagree

- The mark that 95% of the references meet is **86%** on the boundary (44 of 46 pass it). At that mark, 20 of
  the same 46 people's COCO outlines (43%) would also pass.
- But three outlines the mentor rejected score **87.2% to 87.5%** (evaluation/boundary_fit/RESULTS.md), so 86%
  would pass them.
- The provisional mark of **90%** respects his verdicts and passes 89% of the references (41 of 46 judged; 1
  was unsure because of a busy background).

The references under 90% were checked again at full size: a girl sitting on a pavement and four people in
crowded or dim photos (three girls pressed together, a man in a dark suit against a dark curtain). Their
outlines are tight. They score low because hair, touching neighbours and dark-on-dark boundaries make weak
edges in the photo.

**So in crowded or dim scenes, edges in the photo cannot tell a tight outline from a slightly loose one, and no
single pass mark separates them there.** In open scenes with a clear background the measure works well. A
sharper comparison needs a better picture of where the person's boundary is than raw edges.

## What is not shown

- **That the mentor agrees these are tight.** He should review some, starting with the 18 from his own sheet.
- **That a person drawing by hand reaches the same scores.** Machine outlines are tighter than most hand
  drawing; a hand-drawn reference set may suggest a lower mark.
- **People who are small, blurred, behind a fence or in the dark.** They are left out of the references.
