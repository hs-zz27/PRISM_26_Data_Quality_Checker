# Tight reference outlines

The mentor will finalise how tight an outline must be only "after testing against our actual annotations".
COCO's outlines cannot be that reference: he judged nearly all of them not tight enough. This folder makes
a small set of outlines drawn to his standard, and uses them to set the boundary-fit pass mark
(`evaluation/boundary_fit`).

## Steps

1. **Build the kit:** `.venv/bin/python -B evaluation/tight_reference/build_kit.py`. It picks 24 photos with
   69 people and writes `tight_reference_kit.zip`:

   | Kind of photo | Photos | People |
   |---|---|---|
   | one person | 8 | 8 |
   | two people close together | 5 | 10 |
   | a person partly hidden by someone in front | 4 | 11 |
   | a crowd of five to ten | 2 | 19 |
   | the photos of the example sheet the mentor reviewed | 5 | 21 |

2. **Redraw:** whoever prepares the datasets opens the kit in LabelMe and tightens every outline, following
   `DRAWING_GUIDE.md` (plain instructions, included in the zip). Expect two to three hours.
3. **Check with the mentor:** show him a few redrawn photos, especially the five from his example sheet next to
   their old outlines, and agree they are tight enough.
4. **Score:** `.venv/bin/python -B evaluation/tight_reference/compare.py --returned <folder or zip>`. It
   matches every redrawn person to the original, reports how much closer the outlines now follow the
   boundary, and suggests a pass mark that 95% of the redrawn outlines meet. That number replaces
   `PROVISIONAL_MIN_SHARE` in `backend/app/services/boundary_fit.py`, or becomes the "tight" level of a
   guideline setting.

The kit starts from COCO's outlines, which is faster than drawing from nothing. The risk is that the person
redrawing leaves some outlines as they were; the guide asks for every outline to be checked, and the compare
step shows how many points each outline gained.

The photos are COCO 2017 photos and stay out of Git; the scripts rebuild the same kit every time.

```sh
.venv/bin/python -B -m pytest evaluation/tight_reference/tests -q -p no:cacheprovider
```
