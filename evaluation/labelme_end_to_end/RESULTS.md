# LabelMe end to end: results

Run on 13 November 2026 against the running application on `main` at `68fd968` plus this branch.
Machine: one Windows laptop, CPU only. Person model: Mask R-CNN (`maskrcnn_resnet50_fpn_v2`, COCO weights).
Chat model, used for rule extraction only: `qwen3-vl:4b-instruct` through Ollama. Seed `20261113`.

## Results table

| Dataset | Format | Images | Annotations | Errors injected | Errors found | False alarms | Seconds per image |
|---|---|---|---|---|---|---|---|
| `labelme_people_clean` | LabelMe | 29 | 130 | 0 | 0 | 34 | 12.5 |
| `labelme_people_errors` | LabelMe | 29 | 128 | 16 | 16 | 26 | 10.8 |
| `labelme_native_examples` | LabelMe | 3 | 18 | 0 | 0 | 4 | 10.3 |

How each column is counted is in [README.md](README.md#how-the-score-is-counted). Three things to read with
the table:

- **Errors found, 16 of 16.** For 13 of them the report is new: the check that reports the error did not fail
  on that annotation in the unchanged file. For the other 3 (`000000174371` feet cut, `000000398438` extra
  background, `000000578871` head cut) the tool already faulted the same outline before the error was injected,
  so these 3 do not show that the injected error itself was detected.
- **False alarms are high.** In the unchanged files the person check faults 33 of 130 human-drawn outlines, and
  one image is reported with an unannotated person. Nobody has reviewed these one by one, so some may be real
  flaws in the source outlines. Either way a reviewer would have to look at about one outline in four.
- **Seconds per image** is the audit only, on this CPU, with three measured checks. It includes loading the
  person model in each sample process. Rule extraction took a further 128 seconds, once.

### Errors found, by type

| Error | Injected | Found | Reported by |
|---|---|---|---|
| `head_cut` | 2 | 2 | `person_coverage` |
| `feet_cut` | 3 | 3 | `person_coverage` |
| `extra_background` | 3 | 3 | `person_coverage` |
| `shifted` | 3 | 3 | `unannotated_people` on all 3; `person_coverage` on 1 |
| `wrong_label` | 3 | 3 | `allowed_labels` |
| `annotation_removed` | 2 | 2 | `unannotated_people` |

A shifted outline no longer matches its person. `person_coverage` then answers "unsure, no matching person" for
the outline (2 of 3 cases), and `unannotated_people` reports the person at the place the outline was moved from.

### False alarms, by cause

| | Clean | Errors | Native examples |
|---|---|---|---|
| Person check on a one-part outline at least 100 px tall | 21 | 12 | 2 |
| Person check on a one-part outline under 100 px tall | 6 | 6 | 0 |
| Person check on one part of a person drawn in several parts | 6 | 6 | 1 |
| Person check on an annotation whose label is not `person` | 0 | 1 | 1 |
| Unannotated person reported where none was removed | 1 | 1 | 0 |
| **Total** | **34** | **26** | **4** |

In the native examples the label check also reports 11 annotations whose label is not `person` (sofa, chair,
bottle, bus, car, `__ignore__`). The guideline allows only `person`, so these are correct reports and are not
counted as false alarms.

`unsure` is not a false alarm, but it is work for the reviewer: the person check answered `unsure` for 33 of the
130 unchanged annotations (35 of 128 in the error set, 11 of 18 in the native examples), mostly small people and
parts of a person drawn in several parts.

## What the run proved, step by step

| Step | Result |
|---|---|
| Import | All three archives imported: 29, 29 and 3 files. Every file with 1 to 9 person polygons imported. |
| Exact vertices | Every polygon the application shows has the file's exact numbers, in the file's order: 0 differences across 276 annotations. Every annotation is identified by its stable key `/shapes/N`. No polygon is shown with a supplied box. |
| Nothing repaired | 6 files in each people set and 1 native file have source polygons that repeat a vertex, touch themselves or leave the image. They are imported unchanged, marked `invalid`, and the problem is listed in the review screen. |
| Guideline to rules | The model turned 2 of the 3 plain-English sentences into the right checks. See gap 1. |
| Audit | All three audits completed with findings for every image; nothing was `unavailable`. |
| Review screen | Checked in a browser on 4 images (1, 1, 9 and 10 polygons). On the 3 uncorrected images the drawn polygon points equal the uploaded file's points; the fourth shows its corrected revision. Each object shows its `/shapes/N` key. |
| Suggested correction | For both `head_cut` images the measured suggestion redrew the outline with the head added. Asking for the suggestion changed nothing in the dataset. |
| Apply and recheck | Applying saved revision 2; the recheck of that revision passed the person check for the corrected outline (`fail` to `pass`, both images). Other annotations in the file kept their exact points. |
| Review decisions | A decision was recorded for all 29 images (9 accepted, 20 rejected). Nothing was decided automatically. |
| Export | The LabelMe export has `accepted/` (9) and `rejected/` (20) folders and `review_report.csv`. Each exported file equals the revision it was reviewed at; the 27 uncorrected files equal the upload point for point, and the 2 corrected files carry their corrected outline. |
| Re-import | The export ZIP imports again as a LabelMe dataset: 29 files, 0 differences in labels or points. |

## Gaps found

Fixed in this branch:

- **File problems were shown as raw data.** The review screen showed the reader's diagnostics as a block of JSON
  inside a closed section. It now lists each problem in words next to the annotation it belongs to (number, label
  and `/shapes/N` key), and says that nothing was repaired.

Found and not fixed here, because the code belongs to another work area:

1. **Rule extraction missed the label sentence.** "Only people are annotated in these images, so the only allowed
   label is person." was kept as a rule with no check. The run assigned `allowed_labels` by hand, as a reviewer
   would on the rule screen, and records that in `results.json`. (`backend/app/inference/`)
2. **A person drawn in several parts is judged part by part.** LabelMe joins parts with `group_id`. The person
   check compares each part with the whole person, so parts are reported `unsure` or as missing the rest of the
   person: 13 false alarms across the three sets, and most of the `unsure` answers. (`person_coverage.py`)
3. **The person check faults about a quarter of unchanged human-drawn outlines.** See the false-alarm table.
   Whether the tolerance is right depends on the mentor's answer about how tight "tight" must be.
   (`person_coverage.py`)
4. **Re-imported exports lose the file names.** An export stores every image as `image.jpg` inside a folder named
   after the sample, so after re-import every sample is called `image_<hash>`. Points and labels are intact; only
   the readable name is lost. (`backend/app/services/export_archive.py`)
5. **A misleading message.** A file with a polygon problem is described as "The source annotation could not be
   parsed completely", although it was parsed and is shown in full. The new list beside it says what the problem
   is. (`backend/app/models/dataset.py`)

## What is not proven

- **Files with exactly 10 person polygons, on real photos.** The local photos have 1 to 9 people. Files with 1 to
  10 polygons are covered by `backend/tests/test_labelme_person_polygons.py` using made-up polygons on a test
  image: import, exact vertices, stable keys and findings.
- **Accuracy in general.** 29 photos and 16 seeded errors on one machine is a working check, not a benchmark. The
  errors are large and regular (a fifth of the height cut, a 35% shift); small or irregular errors were not tried.
- **The unchanged outlines.** They are COCO's human annotations and have not been reviewed here, so the false
  alarm counts are an upper bound on wrong reports, not a measured error rate.
- **The chat-model checks.** Only the three measured checks ran. `annotation_guideline` was not part of this run.
- **Manual polygon editing in the browser.** The correction here was the suggested one, applied through the same
  endpoint the screen uses. Dragging polygon points in the editor was not exercised in this run.
- **Rectangle shapes, embedded images and other LabelMe shape types.** The test data is polygons only.
- **Samsung data.** None has been supplied.

## Reproduce

```sh
.venv/bin/python -B evaluation/labelme_end_to_end/build_dataset.py
.venv/bin/python -B evaluation/labelme_end_to_end/run_flow.py
```

The numbers above come from `results.json` of this run (kept local). Timings and the model's rule extraction
will differ between machines and runs; the datasets and the answer key will not.
