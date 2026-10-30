# Human annotation development datasets

Prepared on 29 October 2026. Downloaded data is local and excluded from Git.
This index is tracked; originals, manifests and validation reports remain on disk.
These are development samples, not a pre-reviewed accuracy benchmark.

## COCO people subset

Location: `datasets/coco_people_30/`

- `images/`: 30 original COCO 2017 validation JPEGs containing annotated people.
- `annotations/instances_people30.json`: 117 person annotations and 242 total
  annotations. All original annotations for each selected image are retained,
  including other object categories. Category IDs and geometry are unchanged.
- `source/instances_val2017.json`: exact original full validation annotation JSON.
- `download_manifest.json`: sources, selection criteria, IDs, licence references
  and SHA-256 checksums. Metadata and licence records also remain in the JSON.
- `validation_report.json`: local reader/image/geometry inspection report.

Selection uses seed 42: 10 images with one annotated person, 10 with multiple
people, and 10 additional images with multipart person polygons. Every annotation
in a selected image has polygon-list segmentation; RLE/crowd-mask examples were
not selected for this first reader-compatible subset. Parts have 3–512 vertices.
Selection was not based on validator outcomes. Multipart geometry does not itself
prove that a person is occluded.

The source validation JSON was extracted from the official archive using HTTP
byte ranges and verified against its ZIP CRC and uncompressed size. Images came
from COCO's official S3 storage endpoint using HTTPS. Only the selected images
were downloaded, not the full image archive. The folder is about 24.8 MB including
the full source annotation JSON; the 30 images total about 4.6 MB.

Sources: [COCO downloads](https://cocodataset.org/#download),
[COCO terms](https://cocodataset.org/#termsofuse),
[official annotation archive](https://s3.amazonaws.com/images.cocodataset.org/annotations/annotations_trainval2017.zip).
Preserve image-specific licence and attribution metadata when sharing samples.

Inspect from the repository root:

```sh
.venv/bin/python -B -m backend.app.datasets.inspect_annotations coco datasets/coco_people_30/annotations/instances_people30.json --image-root datasets/coco_people_30/images
```

## Official LabelMe examples

Location: `datasets/labelme_people_examples/`

Original JPEG and JSON pairs, downloaded without modifications:

- `2011_000003`: person polygons, grouped parts, a bottle and an ignore region.
- `2011_000006`: person polygons, furniture and an ignore region.
- `2011_000025`: vehicle annotations; included as the third upstream format example,
  not as an image with person annotations.

Both human examples contain seven person-labelled shapes in total. Shape count
is not necessarily person-instance count because group IDs join polygon parts.
All three JSON files use external JPEGs (`imageData` is null). The folder is about
460 KB including source notices, manifests and reports.

`download_manifest.json` records exact URLs and hashes. `*.validation.json` records
each sample's inspection. `UPSTREAM_README.md` and `UPSTREAM_REPOSITORY_LICENSE`
are included. Retain the source/PASCAL VOC image attribution; the repository's
software licence alone is not a separate image-rights grant.

Source: [official LabelMe instance-segmentation examples](https://github.com/wkentaro/labelme/tree/main/examples/instance_segmentation/data_annotated).

```sh
.venv/bin/python -B -m backend.app.datasets.inspect_annotations labelme datasets/labelme_people_examples/2011_000003.json --image-root datasets/labelme_people_examples
```

## Existing Penn-Fudan dataset

Location: `PennFudanPed/` in the repository root, already present. It was not
redownloaded, relocated or modified.

- `PNGImages/`: 170 images.
- `Annotation/`: 170 text annotation files.
- `PedMasks/`: 170 supplied instance masks.
- `readme.txt`: original format description and usage notice.

Source: [Penn-Fudan pedestrian dataset](https://www.cis.upenn.edu/~jshi/ped_html/).
Keep masks as instance ID pixels, not display-colour images. Original images and
annotations must be preserved when making deliberate error examples or corrections.

## Verification and limits

`datasets/validation_summary.json` contains the local verification summary:

- COCO: 30/30 images decoded; no image-dimension mismatch. Exact source-record
  preservation and downloaded file hashes verified. Geometry inspection emitted
  17 repeated-vertex and 17 edge-intersection/touch diagnostics affecting 15
  annotation records. These require review against COCO conventions and the
  chosen guidelines; they are not established semantic annotation errors.
- LabelMe: 3/3 images decoded. `2011_000003` and `2011_000025` had no structural
  diagnostics. `2011_000006` had two polygon edge-intersection/touch diagnostics
  and one polygon-outside-image diagnostic. Source geometry is unchanged.
- Penn-Fudan: all 170 images and masks decoded, all matching annotation/mask
  files present, and image/mask dimensions matched. This was an asset check,
  not a rerun of the audit model or a certification of annotation quality.

No model inference was run. Validation exit code 1 means diagnostics or incomplete
checks, not automatic dataset rejection. No LabelMe-to-COCO or COCO-to-LabelMe
conversion was performed. COCO and LabelMe work through the standalone readers, the
inspector and the preview demo (`backend.app.datasets.preview_annotations`). Upload,
audit, correction and export for these formats exist in the application but are not
verified end to end here, and mask-based and model-based audit checks remain Penn-Fudan
only. See `backend/app/datasets/INTEGRATION_CHECKLIST.md`.
