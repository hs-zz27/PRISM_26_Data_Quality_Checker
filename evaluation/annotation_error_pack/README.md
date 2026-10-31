# Annotation evaluation pack (COCO and LabelMe)

A small, reproducible set of paired annotation cases for evaluating the PRISM Data Quality Checker.
Each pair contains an unchanged **baseline** annotation and an **altered** copy with exactly one
deliberate error in one field of one object. The image bytes are identical within every pair.

> **The guidelines in `guidelines.txt` are team-authored development guidelines, not
> Samsung-provided requirements.** Guideline 9's 3-pixel tolerance is a proposed development
> evaluation choice that needs reviewer approval before it is used to judge real annotations.

## Cases

| Case | Format | Image | Target | Deliberate change | Primary guideline |
|---|---|---|---|---|---|
| C01 | COCO | `000000199055.jpg` | annotation `424921` | bottom edge raised 113.076 px (height × 0.8) | 6 (feet) |
| C02 | COCO | `000000001000.jpg` | annotation `1234574` | top edge lowered 45.682 px; bottom fixed | 5 (head) |
| C03 | COCO | `000000153229.jpg` | annotation `495199` | top edge moved up 59.42 px into background | 9 (extra space) |
| C04 | COCO | `000000100624.jpg` | annotation `494051` | `category_id` 1 (person) → 3 (car) | 4 (label) |
| C05 | COCO | `000000085157.jpg` | annotation `430467` | polygon part 0 shifted right 64 px | 10 (polygon fit) |
| L01 | LabelMe | `2011_000025.jpg` | — | **BLOCKED** (no person in this image) | — |
| L02 | LabelMe | `2011_000006.jpg` | `/shapes/2` | all points shifted down 37.5 px | 10 (polygon fit) |
| L03 | LabelMe | `2011_000003.jpg` | `/shapes/0` | label person → car | 4 (label) |

**L01 is blocked, not substituted.** The three official LabelMe examples include only two images
with people (`2011_000003`, `2011_000006`), and those are used by L03 and L02. `2011_000025`
contains two buses and one car and no visible person. It is packaged unchanged in both LabelMe
archives, so each still holds exactly three images, and the answer key marks both L01 entries as
blocked. So **7 of the 8 required pairs (14 of the 16 cases) are complete**; the L01 pair is
documented but cannot be evaluated.

All five COCO images use licence 4 (CC BY 2.0); images under NoDerivs licences were avoided
because the comparison PNGs draw on the photos.

## Contents

```
README.md         this file
guidelines.txt    12 numbered development guidelines
answer_key.json   16 entries (8 baseline, 8 altered); keep it out of uploads and model prompts
build_pack.py     rebuilds everything below from the source datasets, then verifies it
verification.md   what was checked, how, and what was not tested
tests/            pytest suite for the builder (Windows + Linux; real importer on Linux)
archives/         generated, not committed: the four upload ZIPs
comparisons/      generated, not committed: C01-C05, L01-L03 before/after PNGs
```

### Upload archives

| ZIP | Layout | Contents |
|---|---|---|
| `coco_baseline_5.zip`, `coco_errors_5.zip` | `annotations/instances.json` + `images/<file>.jpg` | one instance JSON: 5 image records, all 58 annotations of those images, the source `info`, `licenses` and all 80 category definitions; 5 images |
| `labelme_baseline_3.zip`, `labelme_errors_3.zip` | `<name>.json` + `<name>.jpg` side by side | 3 annotation JSON files with relative `imagePath` and `imageData: null`; 3 images |

The layouts follow the current importer (`backend/app/datasets/json_zip.py`): every `.json` in a
ZIP is parsed as an annotation, so no other JSON is included; each COCO `file_name` must match
exactly one archive file among the JSON folder, `images/`, the parent folder and the root;
LabelMe images are resolved only beside their JSON. No image reference needed rebasing:
COCO `file_name` and LabelMe `imagePath` values are unchanged from the source.
Entries are stored (not deflated) with fixed timestamps and sorted names so the archives are
byte-identical on every platform.

## Source data

- `datasets/coco_people_30/annotations/instances_people30.json` and `datasets/coco_people_30/images/`
  (COCO 2017 validation subset; see `datasets/README.md`).
- `datasets/labelme_people_examples/` (official LabelMe instance-segmentation examples).

The source files are read, never written. `build_pack.py` checks each one against pinned
SHA-256 values that equal the hashes in the upstream download manifests. Text files are compared
after CRLF→LF normalisation because Git for Windows checks them out with CRLF; the packaged JSON
is always the upstream LF form, so LabelMe baselines are byte-identical to the upstream files.

## Rebuilding

From the repository root:

```sh
# build into evaluation/annotation_error_pack/, then run all verification checks
python -B evaluation/annotation_error_pack/build_pack.py

# rebuild in a temporary folder and confirm the outputs here are reproduced exactly
python -B evaluation/annotation_error_pack/build_pack.py --check

# the builder's tests (the 4 real-importer tests run only on Linux/WSL/Docker)
python -B -m pytest evaluation/annotation_error_pack/tests -q -p no:cacheprovider
```

On Windows use `.venv\Scripts\python.exe`; on macOS/Linux `.venv/bin/python`. Requirements:
Python 3.12 and the repository's Python dependencies (Pillow, pytest). The build stops with a
clear error if a source file, identifier or expected value differs, and refuses to write into or
above `datasets/` or `PennFudanPed/`.

To inspect the cases with the standalone preview tool, extract an archive and run, for example:

```sh
python -B -m backend.app.datasets.preview_annotations coco <extracted>/annotations/instances.json --image-root <extracted>/images --out <new folder> --max-images 0
python -B -m backend.app.datasets.preview_annotations labelme <extracted> --out <new folder> --max-images 0
```

## Using the pack

1. Import `coco_baseline_5.zip` and `coco_errors_5.zip` as two COCO datasets, and the LabelMe ZIPs
   as two LabelMe datasets.
2. Confirm a rulebook from `guidelines.txt` and audit both datasets of each pair with the same rulebook.
3. Score each target object's findings against `answer_key.json` (`expected_target_result`,
   `guideline_assessment`, `related_findings_from_edit`). Score only the documented targets:
   `whole_image_reviewed` is false for every entry.

What to expect from the current tool: for COCO and LabelMe the automatic checks are structural
(box inside the image, polygon validity, IDs, metadata, overlaps); mask checks are unavailable.
Every deliberate error is structurally valid, so **no structural diagnostic changes between
baseline and altered** (verified). The errors can only be found by checking annotations against
the image content, i.e. a visual guideline check. Detection results of such a check were not
measured for this pack.

## Licences and sharing

COCO images: licence 4, *Attribution License* (CC BY 2.0); attribution sources are the
`flickr_url` values in the packaged COCO JSON. LabelMe images: PASCAL VOC images distributed with
the LabelMe examples; the repository's software licence is not an image-rights grant. Because the
redistribution terms for all images are not confirmed, the generated ZIPs and PNGs are listed in
this folder's `.gitignore` and are regenerated locally. Remove those lines only after the team
confirms that sharing is permitted.
