# LabelMe end to end

Proof that the whole flow works on LabelMe polygon data: import, audit, review, correct, recheck and export.
The measured results are in [RESULTS.md](RESULTS.md).

## What is here

| File | What it is |
|---|---|
| `build_dataset.py` | Builds the three test datasets and the seeded errors. No model or service needed. |
| `guidelines.txt` | The plain-English guideline the run turns into rules. Team-written, not Samsung-provided. |
| `run_flow.py` | Drives the running application through the whole flow and scores it against the answer key. |
| `tests/` | Checks that the datasets are reproducible, faithful to their sources and importable. |
| `RESULTS.md` | The results table and what the run did and did not prove. |

Generated and kept out of Git (they contain third-party photos, and the answer key must never be uploaded):
`archives/`, `answer_key.json`, `verification.json`, `results.json`.

## The datasets

All three are LabelMe: one JSON file per photo, next to the photo, with `imageData` null. Every object is a
polygon. No object has a box, so no object has both.

| Dataset | Photos | Where the annotations come from |
|---|---|---|
| `labelme_people_clean` | 29 | Human-drawn COCO 2017 person outlines from `datasets/coco_people_30`, written as LabelMe polygon shapes with the exact source numbers. 1 to 9 people per file. |
| `labelme_people_errors` | 29 | The same photos and files with one seeded error in 16 of them. The other 13 are unchanged controls. |
| `labelme_native_examples` | 3 | The upstream LabelMe example files from `datasets/labelme_people_examples`, byte for byte. They also contain furniture, vehicles and `__ignore__` regions. |

Only person annotations are taken from COCO, because the guideline is about people. The photo with 12 people is
left out so that every file has 1 to 10. A person COCO drew in several parts becomes several shapes that share a
`group_id`, which is how LabelMe records one object made of parts. The acceptance range of 1 to 10 polygons per
file is also covered with made-up files in `backend/tests/test_labelme_person_polygons.py`, because the local
photos stop at 9 people.

## The seeded errors

`build_dataset.py` uses seed `20261113`. The same seed and source files always give the same archives, byte for
byte. Files are put in a seeded order; every fourth one is left unchanged, and the others take the error types in
turn. A geometry error is only put on an outline that is at least 100 px tall and drawn in one part, so that the
error is clearly visible. An error that would leave the image or make the file structurally invalid is skipped:
these are annotation mistakes, not broken files.

| Error | What changes | Check expected to report it |
|---|---|---|
| `head_cut` | The top 20% of the outline's height is flattened, leaving the head outside. | `person_coverage` |
| `feet_cut` | The bottom 20% is flattened, leaving the feet outside. | `person_coverage` |
| `extra_background` | One side of the outline is stretched outward by up to 1.6 times, taking in background. | `person_coverage` |
| `shifted` | The whole outline moves sideways by 35% of its width. | `person_coverage` |
| `wrong_label` | The label `person` becomes `car`. | `allowed_labels` |
| `annotation_removed` | One person's annotation is deleted. The person stands apart from the others. | `unannotated_people` |

`answer_key.json` lists, for each file, the error, the annotation's stable key (`/shapes/N`) and its original
bounds. Photos are copied, never edited.

## How to run

From the repository root, with the local source datasets present (see `datasets/README.md`):

```sh
.venv/bin/python -B evaluation/labelme_end_to_end/build_dataset.py
.venv/bin/python -B -m pytest evaluation/labelme_end_to_end/tests -q -p no:cacheprovider
```

With the application running (API, all five workers, PostgreSQL, Ollama and the person model file):

```sh
.venv/bin/python -B evaluation/labelme_end_to_end/run_flow.py
```

It prints the results table and writes `results.json`. Rule extraction is the slow step. To reuse a confirmed
rulebook from an earlier run, add `--rulebook-id <id> --rulebook-version <n>`. Each run adds new datasets, audits
and an export to the application's database; nothing existing is changed.

## How the score is counted

- **Errors found:** the expected check reports `fail` on the altered annotation. For a removed annotation, the
  image is reported with an unannotated person where the annotation used to be.
- **False alarms:** every other `fail` from the three measured checks. An unannotated person reported in the wrong
  place counts as one.
- **Seconds per image:** time from submitting the audit to its completion, divided by the number of photos.
- `unsure` and `unavailable` are neither found nor false alarms; `results.json` lists them per error.

The source outlines were drawn by people and are not pixel-perfect, so a "false alarm" on an unchanged
annotation can be a real flaw in the source. Nobody has reviewed them one by one.
