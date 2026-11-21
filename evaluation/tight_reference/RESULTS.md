# Tight reference outlines: first results

Drawn by machine with `draw.py` on 21 November 2026 in two ways, Segment Anything (the default) and GrabCut,
every outline checked by eye at full size, and scored with `compare.py --review review.json`. None has been
approved by the mentor yet.

## What was drawn

24 photos, 69 people. Segment Anything redrew 67; GrabCut redrew 65. Where a drawing strayed too far from the
person, COCO's outline was kept.

Checked side by side, **Segment Anything is as good as or tighter than GrabCut for every person kept as a
reference**. GrabCut leaked into the background in about ten places (ground below the feet, a strip of wall, a
dune, a skateboard); Segment Anything did not. GrabCut's first version also lost thin limbs on carpet and
grass until its "certainly the person" core was given each limb's middle line.

After the check, **46 people are kept as references** (`review.json`): 18 from the mentor's own example
sheet, 9 partly hidden, 7 single, 7 close pairs and 5 in crowds. 23 are left out with their reasons
(README.md).

## How closely the references follow edges in the photo

The "on the boundary" share from `backend/app/services/boundary_fit.py`, for the 46 references:

| Kind of photo | People | COCO (median) | Segment Anything (median) | GrabCut (median) |
|---|---|---|---|---|
| one person | 7 | 89.3% | 95.9% | 98.0% |
| two people close together | 7 | 83.8% | 95.0% | 98.1% |
| partly hidden | 9 | 86.5% | 95.1% | 97.8% |
| crowd | 5 | 87.8% | 98.4% | 99.5% |
| the mentor's example sheet | 18 | 81.2% | 91.0% | 93.6% |

GrabCut medians are from its own review, with 47 people kept.

## Why this cannot set the pass mark

- **The measure prefers the method that snaps to colour edges, not the tighter outline.** GrabCut cuts along
  colour edges, so it scores higher; Segment Anything traces a smooth line that can sit a pixel or two off
  the strongest edge, so it scores lower although it looked tighter by eye.
- **The marks the references suggest disagree with the mentor.** With Segment Anything references, the mark
  that 95% of them meet is 79%, which 34 of the same 46 people's COCO outlines (74%) would also pass. With
  GrabCut references it was 86%. Both are below the 87.2–87.5% scored by three outlines the mentor rejected.
- **The provisional 90% mark rejects tight outlines.** It passes only 35 of the 46 Segment Anything
  references. The lowest are three girls pressed together in a crowd (67.5%, 75.1%, 79.5%) and a girl on a
  pavement (79.2%): hair and touching neighbours give weak edges.

So the share of an outline lying on photo edges works in open scenes but cannot be the tightness judge in
crowded or dim photos. The better comparison, which the research supports, is to measure each annotation
against a high-quality reference mask of the same person, such as these Segment Anything outlines, with a
boundary measure and a tolerance band.

## What is not shown

- **That the mentor agrees these are tight.** He should review some, starting with the 18 from his own sheet.
- **That a person drawing by hand reaches the same result.** Machine outlines can be tighter than hand drawing.
- **People who are small, blurred, behind a fence or in the dark.** They are left out of the references.
