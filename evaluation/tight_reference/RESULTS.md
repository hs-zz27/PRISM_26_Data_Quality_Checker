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

After the check, **45 people are kept as references** (`review.json`): 17 from the mentor's own example
sheet, 9 partly hidden, 7 single, 7 close pairs and 5 in crowds. 24 are left out with their reasons
(README.md). One of them, a dark figure against a dark wall, was first kept and then taken out on a closer
look: the drawing misses both bent arms.

## How closely the references follow edges in the photo

The "on the boundary" share from `backend/app/services/boundary_fit.py`, for the 45 references:

| Kind of photo | People | COCO (median) | Segment Anything (median) | GrabCut (median) |
|---|---|---|---|---|
| one person | 7 | 89.3% | 95.9% | 98.0% |
| two people close together | 7 | 83.8% | 95.0% | 98.1% |
| partly hidden | 9 | 86.5% | 95.1% | 97.8% |
| crowd | 5 | 87.8% | 98.4% | 99.5% |
| the mentor's example sheet | 17 | 82.8% | 91.8% | 93.6% |

GrabCut medians are from its own review, with 47 people kept.

## Why this cannot set the pass mark

- **The measure prefers the method that snaps to colour edges, not the tighter outline.** GrabCut cuts along
  colour edges, so it scores higher; Segment Anything traces a smooth line that can sit a pixel or two off
  the strongest edge, so it scores lower although it looked tighter by eye.
- **The marks the references suggest disagree with the mentor.** With Segment Anything references, the mark
  that 95% of them meet is 79%, which 34 of the same 45 people's COCO outlines (76%) would also pass. With
  GrabCut references it was 86%. Both are below the 87.2–87.5% scored by three outlines the mentor rejected.
- **The provisional 90% mark rejects tight outlines.** It passes only 35 of the 45 Segment Anything
  references. The lowest are three girls pressed together in a crowd (67.5%, 75.1%, 79.5%) and a girl on a
  pavement (79.2%): hair and touching neighbours give weak edges.

So the share of an outline lying on photo edges works in open scenes but cannot be the tightness judge in
crowded or dim photos. The better comparison, which the research supports, is to measure each annotation
against a high-quality reference mask of the same person, such as these Segment Anything outlines, with a
boundary measure and a tolerance band.

## What is not shown

- **That the mentor agrees these are tight.** He should review some, starting with the 17 from his own sheet.
- **That a person drawing by hand reaches the same result.** Machine outlines can be tighter than hand drawing.
- **People who are small, blurred, behind a fence or in the dark.** They are left out of the references.

## Distance from the reference agrees with the mentor on all 17 people it can check

`distances.py` compares each of COCO's outlines with the person's reference, boundary to boundary, using
`backend/app/services/boundary_distance.py`. "Outward" is how far the outline reaches beyond the person;
"inward" is how far the person reaches beyond the outline. The figure is the distance that 95% of the
boundary stays within. 17 of the 21 outlines the mentor judged have a kept reference:

| Person | Mentor | Outward | Inward | Mask IoU | Boundary IoU at 1% of the diagonal |
|---|---|---|---|---|---|
| fire performer (1236789) | tight | 3.0 px | 0.0 px | 0.911 | 0.692 |
| bride (431398) | tight | 3.6 px | 1.0 px | 0.960 | 0.713 |
| 14 others he rejected | not tight | 4.4–22.0 px | 1.0–13.0 px | 0.785–0.922 | 0.301–0.601 |
| white-haired man (2198581) | not tight | 7.4 px | 1.0 px | 0.946 | 0.518 |

- **The mentor's two tight outlines lie closer to their references than all 15 he rejected.** This holds
  for the 80th, 90th, 95th and 98th percentile of the distance, and for Boundary IoU at 0.5%, 0.75%, 1% and
  2% of the diagonal. A random order would put both first about once in 136 tries.
- **Area overlap cannot do this.** The white-haired man's outline, which takes in the head in front of him,
  has a higher mask IoU (0.946) than the fire performer's (0.911). The distance shows where it goes wrong:
  7.4 px outward, but only 1.0 px inward.
- **The tolerance the tight outlines need is 0.47% of the photo's diagonal**, about 3.7 px on a 640x480
  photo. The closest rejected outline is at 0.56% (4.4 px), so the gap is under 1 px, and a reference that
  is a pixel off could close it. That is too thin to finalise from two examples: the fire performer alone
  would set 0.39%, which the bride fails.
- **At that tolerance 24 of the other 28 COCO outlines fail**, which matches the mentor's view that COCO's
  outlines are generally not tight. The 4 that pass (a girl on a wall, a man in a yellow coat, a tennis
  server, a batter) look as close as the bride's by eye; the mentor has not judged them.

- **It also says where.** The places farther from the reference than the tolerance come out as `extra` and
  `missing` regions in the review screen's format. On the white-haired man they mark the strip over the head
  in front of him, his shoulder and the loose top of his head; on a player, the grass between his legs; on
  the woman in uniform, a neighbour's hand and phone taken in (`extra`) and her right arm left out
  (`missing`). On the bride there are none.

So a boundary distance against a checked reference reproduces the mentor's verdicts where mask IoU does not,
and it names the side that is wrong. The edge-share measure also ranks his sheet correctly, but it rejected
tight references in crowded and dim photos because it needs strong edges in the photo; this measure needs a
good reference instead, so it can only be as right as the reference. The pass mark still needs more judged
outlines: the research report suggests about 40 more, plus a second careful redraw of the tight ones to
see how far two careful outlines differ.

## Segment Anything disagreeing with itself does not show which references to distrust

The research suggested treating a reference as uncertain when Segment Anything draws a different outline for
slightly different prompts. `agreement.py` drew all 69 people again with four other prompts (the box alone,
the box and points inside, a 10% wider box, points without a box) and measured how far each drawing moved from
the reference. The 24 drawings left out after the check by eye are the test:

| | Kept as references (45) | Left out (24) |
|---|---|---|
| Median spread (largest over the other prompts) | 0.28% of the diagonal | 1.02% |
| Over a threshold that 90% of the kept ones meet (3.86%) | 5 | 4 |

- **It is a weak signal.** A left-out person spreads more than a kept one only 63% of the time (50% would be
  chance). Using fewer prompts gives 61-66%, and picking the best of those after the fact would only fit
  noise.
- **Drawings that are wrong in the same way under every prompt spread least.** The dark figure whose drawing
  missed both arms moved 1.0 px; five of the seven people behind the chain-link fence moved under 1% of the
  diagonal.
- **Good references can spread a lot.** Four kept outlines moved 65-116 px, three under the points-only
  prompt and one under the box with inside points. In the two looked at, the other prompt dropped part of the
  person: a woman's uniform without her head and arm, and a player's upper body without the leg that shows
  below the player in front.
- **This tests the drawing procedure, not the raw model.** Every prompt's result is held within COCO's band
  and kept off other people, and the candidate closest to COCO's outline is chosen, which pulls the variants
  together. Without those limits the spread would be larger, for good and bad references alike; that is
  untested.

So a machine reference still needs a person to check it, as the 69 here were; disagreement between prompts
cannot replace that check. Speed on this laptop's CPU: 1.43 s to read a photo (median of 24) and 0.03 s per
person and prompt, so the four extra prompts add about 0.1 s per person.
