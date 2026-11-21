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

2. **Redraw:** either a person tightens every outline in LabelMe, following `DRAWING_GUIDE.md` (plain
   instructions, included in the zip; two to three hours), or `draw.py` draws them by machine into `drawn/`
   (about one minute). By default it cuts each person out with Segment Anything (ViT-B weights, Apache-2.0),
   prompted with COCO's box, points deep inside the person and points on neighbours;
   `--method grabcut` uses OpenCV's GrabCut instead and needs no download. Either way COCO's outline is kept
   wherever the result strays too far from the person. Machine outlines are tighter than a person would draw,
   so check them by eye; they are a reference only once the mentor has approved them.

   Segment Anything needs its weights file once (375 MB):

   ```sh
   mkdir -p .cache/models
   curl -L -o .cache/models/sam_vit_b_01ec64.pth https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth
   .venv/bin/python -m pip install segment-anything
   ```

   Every machine-drawn outline was checked by eye at full size, next to the GrabCut version; `review.json`
   records the verdict for each of the 69 people. 45 follow the visible boundary closely, with nothing missing
   and nothing added, and are kept as references. 24 are left out, each with its reason: 7 people seen
   through a chain-link fence, 7 small and dark people in a dugout, 2 people in a night scene, 3 people whose
   boundary cannot be seen (legs behind a net's white band, a small blurred person, a face squeezed between
   two others), 2 partly hidden people whose small pieces cannot all be checked, and 3 drawings that miss a
   foot, miss both arms of a dark figure, or add a piece of a chair.
3. **Check with the mentor:** `review_sheet.py` builds a PDF of his five example photos with the original and
   redrawn outlines side by side, whole photo and person by person (17 people). show him a few redrawn photos, especially the five from his example sheet next to
   their old outlines, and agree they are tight enough.
4. **Score:** `.venv/bin/python -B evaluation/tight_reference/compare.py --returned <folder or zip>`
   (for the machine version, `--returned evaluation/tight_reference/drawn --review
   evaluation/tight_reference/review.json`, which scores only the 45 kept references). It
   matches every redrawn person to the original, reports how much closer the outlines now follow the
   boundary, and suggests a pass mark that 95% of the redrawn outlines meet. That number replaces
   `PROVISIONAL_MIN_SHARE` in `backend/app/services/boundary_fit.py`, or becomes the "tight" level of a
   guideline setting.

5. **Distance from the reference:** `.venv/bin/python -B evaluation/tight_reference/distances.py` measures how
   far each of COCO's outlines lies from its reference, outward and inward, with
   `backend/app/services/boundary_distance.py`, and checks the result against the mentor's verdicts
   (`evaluation/boundary_fit/sir_labels.json`). It prints the tolerance his tight outlines need and writes
   `distance_results.json`.

6. **Prompt agreement:** `.venv/bin/python -B evaluation/tight_reference/agreement.py` (about 3 minutes; add
   `--saved` to reprint the last run) draws every person again with draw.py's other Segment Anything prompts and
   measures how far each drawing moves. It writes `agreement_results.json`. The result, in RESULTS.md: the spread
   does not reliably pick out the drawings that failed the check by eye.

7. **More verdicts from the mentor:** `.venv/bin/python -B evaluation/tight_reference/verdict_sheet.py` builds
   `review_sheet/coco_outline_verdicts.pdf` (14 pages): the 28 COCO outlines whose reference was kept, outside his
   own example sheet, each with "tight enough / not tight enough" boxes, his two tight outlines as the yardstick,
   and an answer table. Only COCO's outline is shown, so the machine drawing does not steer his answer.
   `review_sheet/coco_outline_verdicts_key.json` maps each number to its COCO id; add his answers to
   `evaluation/boundary_fit/sir_labels.json` and rerun `distances.py`. That gives 45 judged outlines instead of
   17, enough to test the tolerance instead of only fitting it.

The kit starts from COCO's outlines, which is faster than drawing from nothing. The risk is that the person
redrawing leaves some outlines as they were; the guide asks for every outline to be checked, and the compare
step shows how many points each outline gained.

The photos are COCO 2017 photos and stay out of Git; the scripts rebuild the same kit every time.

```sh
.venv/bin/python -B -m pytest evaluation/tight_reference/tests -q -p no:cacheprovider
```
