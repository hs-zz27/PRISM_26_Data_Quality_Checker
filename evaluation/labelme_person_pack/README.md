# LabelMe person pack

A larger LabelMe test set for the person checks: 250 photos, 749 human-drawn person outlines and 188 seeded
errors of known type, place and size. It measures what the checks catch and how often they fault an outline
nobody changed. The first measured results are in [RESULTS.md](RESULTS.md).

It complements [labelme_end_to_end](../labelme_end_to_end/README.md), which proves the whole application flow
on 29 photos. This pack is larger and its errors are graded, so it can be used to set the tightness levels, to
check that a missing body part is named correctly, and to test a polygon-overlap check when one exists.

## What is here

| File | What it is |
|---|---|
| `build_pack.py` | Builds the two datasets and the seeded errors. No model or service needed; about 4 seconds. |
| `score_pack.py` | Scores the application's measured person checks against the answer key, without the application. |
| `fetch_photos.py` | Downloads more source photos, to make the pack larger. |
| `save_detections.py` | Runs the person model once per new photo and saves its output for `score_pack.py`. |
| `crowd.py` | Builds and scores the crowd datasets: one, some or all of the people annotated. |
| `overlap.py` | Measures how much two people's outlines share, in the human-drawn files and the seeded errors. |
| `tests/` | Checks that every error changes the right place by the right amount and that the build is reproducible. |

Generated and kept out of Git (third-party photos, and the answer key must never be uploaded): `archives/`,
`answer_key.json`, `crowd_answer_key.json`, `verification.json`, `results.json`, `crowd_results.json`, `overlap_results.json`.

## The datasets

Both are LabelMe: one JSON file per photo, next to the photo, with `imageData` null. Every object is a polygon.
No object has a box.

| Dataset | Photos | Annotations | What it is |
|---|---|---|---|
| `labelme_pack_clean` | 250 | 749 | COCO 2017 validation person outlines, written as LabelMe polygon shapes with the exact source numbers. 1 to 10 people per file. |
| `labelme_pack_errors` | 250 | 734 | The same photos with one seeded error in 188 files. The other 62 are unchanged controls. |

There is no large public set of person polygons that is LabelMe from the start; the upstream LabelMe examples
are three files. So the outlines come from COCO, where people drew them, and only the file format is converted.
A photo is used when it has 1 to 10 people and every one is drawn as polygons (photos with a COCO "crowd"
region are left out). A person drawn in several parts becomes several shapes that share a `group_id`.

## The seeded errors

Seed `20261114`. The same seed and source files always give the same archives, byte for byte. Files are put in
a seeded order; every fourth one is left unchanged, and each other file takes the error type used least so far
that fits it, which keeps the types balanced. An outline error is only put on an outline drawn in one part and
at least 100 px tall (150 px for a body part). An error that would make the file structurally invalid is
skipped: these are annotation mistakes, not broken files.

| Error | What changes | Check expected to report it |
|---|---|---|
| `head_missing` | The head is cut off the outline, at the neck. | `person_coverage`, missing |
| `arm_missing` | One forearm and hand are cut out of the outline. | `person_coverage`, missing |
| `leg_missing` | One lower leg and foot are cut out of the outline. | `person_coverage`, missing |
| `loose_3`, `loose_6`, `loose_12` | The whole outline is moved outward by 3%, 6% or 12% of the person's height. | `person_coverage`, extra |
| `shrunk_3`, `shrunk_6` | The whole outline is moved inward by 3% or 6% of the person's height. | `person_coverage`, missing |
| `overlap_10`, `overlap_30` | The outline is grown into the person standing next to it until it covers 10% or 30% of that person's outline. | None yet: there is no polygon-overlap check. |
| `wrong_label` | The label `person` becomes `car`. | `allowed_labels` |
| `annotation_removed` | One person's annotation is deleted. The person stands apart from the others. | `unannotated_people` |

The body parts are found from the body points (nose, shoulders, elbows, wrists, knees, ankles) that the COCO
annotators marked on each person, not from a model. An overlap error is only made between two people of
similar size who stand next to each other.

`answer_key.json` lists, for each file: the error, the annotation's stable key (`/shapes/N`), its original
bounds, the body part or level, how many pixels were removed and added, the box where the outline really
changed (`changed_box`), and the check and region kind expected to report it. Photos are copied, never edited.

An altered outline is traced again from pixels, so its unchanged stretches can differ from the source by about
one pixel. Unaltered outlines keep the exact source numbers.

## The crowd datasets

`crowd.py` builds three more datasets from the photos with at least 5 people, all drawn as polygons: 62 photos
and 478 people on disk (5 to 13 people per photo). They are the same photos three times:

| Dataset | People annotated | What it is |
|---|---|---|
| `labelme_crowd_all` | 478 | Every person in the crowd is annotated. Nothing should be reported as unannotated. |
| `labelme_crowd_some` | 221 | A seeded half of the people are annotated; the other 257 are left out. |
| `labelme_crowd_one` | 62 | One person is annotated (at least 60 px tall when there is one); the other 416 are left out. |

`crowd_answer_key.json` lists, for each file and dataset, every person left out, with their box and height.
Annotations that stay keep the exact source numbers. Seed `20261115`.

```sh
.venv/bin/python -B evaluation/labelme_person_pack/crowd.py build
.venv/bin/python -B evaluation/labelme_person_pack/crowd.py score
```

## How to run

From the repository root. The source photos and the COCO person file are local downloads (`.cache/downloads/`).

```sh
.venv/bin/python -B evaluation/labelme_person_pack/build_pack.py
.venv/bin/python -B -m pytest evaluation/labelme_person_pack/tests -q -p no:cacheprovider
.venv/bin/python -B evaluation/labelme_person_pack/score_pack.py
```

`score_pack.py` calls the same functions the audit worker calls and reads the person model's saved output for
each photo, so it finishes in a few seconds. To see the pack in the application instead, import the two ZIP
files from `archives/` as LabelMe datasets.

## Making the pack larger

2,354 photos of the COCO validation set fit the pack; 250 are on disk. To add more:

```sh
.venv/bin/python -B evaluation/labelme_person_pack/fetch_photos.py --count 500    # about 80 MB
.venv/bin/python -B evaluation/labelme_person_pack/save_detections.py             # about 1 s per new photo
.venv/bin/python -B evaluation/labelme_person_pack/build_pack.py
.venv/bin/python -B evaluation/labelme_person_pack/score_pack.py
```

Adding photos changes which file gets which error, so results from packs of different sizes are not compared
file by file.

## How the score is counted

- **Reported:** `person_coverage` answers `fail` for the altered outline. For a removed annotation, a person is
  reported as unannotated where the annotation used to be.
- **Right kind and place:** the report contains a region of the expected kind (`missing` or `extra`) that
  touches the box where the outline was changed.
- **Already failing before:** the same outline already failed in the unchanged file. For these, a report does
  not show that the seeded error itself was caught; "right kind and place" is the stronger evidence.
- `wrong_label` is not scored here: `allowed_labels` is a plain label comparison with no model behind it.

The source outlines were drawn by people and are not pixel-perfect, so a report on an unchanged outline can be
a real flaw in the source. Nobody has reviewed them one by one.
