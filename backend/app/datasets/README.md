# COCO and LabelMe annotation readers

Standalone, independently testable readers for COCO JSON and LabelMe JSON, a shared
annotation representation, a structural inspector, and a verification demo with
overlays. Penn-Fudan (`penn_fudan.py`, `penn_fudan_zip.py`) is unchanged.

Status words used below are deliberate:

- **Tested on synthetic fixtures**: invented files in `synthetic_samples.py`. They prove
  the code does what the documented format says, nothing more.
- **Tested on real public samples**: see [VERIFICATION.md](VERIFICATION.md) for exactly
  which files, who ran them, and what was not reproduced.
- **Not tested on Samsung data.** Samsung has not supplied images or polygon JSON. These
  readers implement the *documented public* formats. Compatibility with Samsung's exact
  files is unproven.

A successful parse or an empty diagnostic list does not prove an annotation is correct.
Diagnostics are structural facts. No quality threshold is invented here, and nothing is
repaired, clipped, rescaled, rounded or dropped.

## Quick start

```python
from backend.app.datasets.coco import read_coco
from backend.app.datasets.labelme import read_labelme
from backend.app.datasets.annotation_summary import summarize_annotation

dataset = read_coco("path/to/instances.json")      # or read_labelme("path/to/image.json")
for image in dataset.images:
    print(image.key, image.file_name, image.width, image.height)
    for obj in image.annotations:
        s = summarize_annotation(obj)
        print(obj.key, obj.source_id, obj.label, s.box.state, s.polygon.state, s.mask.state)
for issue in dataset.all_issues:
    print(issue.severity, issue.code, issue.location, issue.message)
```

`parse_coco(dict)` / `parse_labelme(dict)` take decoded JSON. `read_*` additionally keep the
exact file bytes and path. Reads are capped at 64 MiB (`max_bytes`).

## Shared representation (`models/annotation_document.py`)

| Type | Holds |
|---|---|
| `AnnotationDocument` | `format` (`coco`/`labelme`), a deep copy of the whole source JSON in `source`, exact `source_bytes`/`source_path` for file reads, `images`, `unassigned_annotations`, document-level `issues` |
| `ImageRecord` | `key`, `source_id`, `file_name` (reference only, never opened by readers), `width`, `height`, `annotations`, `issues`, `source` |
| `AnnotationRecord` | `key`, `source_id`, `label`, `category_id`, `group_id`, `bbox` + `bbox_origin`, `polygons` (list of parts) + `polygon_locations`, optional `mask`, `issues`, **`source`** (the untouched original record) |
| `MaskReference` | `encoding`, `value` as supplied, `decoded=False` |
| `ReaderIssue` | `severity` (`error` = structurally wrong, `unsupported` = kept but not handled), `code`, `location` (JSON pointer), `message` |

Identity and feedback: `key` is a document-local JSON pointer (`/annotations/12`,
`/shapes/3`). It stays unique even when source IDs are missing or duplicated. Combine it
with the image key and file identity to refer to one object. LabelMe `group_id` is *not* an
object ID and shapes sharing a group are never merged. `source` is the original evidence;
corrections must create new records instead of editing it.

### What each geometry type can be (`annotation_summary.py`)

| State | Meaning |
|---|---|
| `supplied` | present in the source and parsed |
| `derived` | computed by this tool (only: a box from polygon vertices). Never from source |
| `not_supplied` | the source never had this type. Not an error |
| `malformed` | the source had it but it could not be parsed. Original kept in `source` |
| `retained_not_decoded` | supplied in an encoding kept but not decoded (COCO RLE, LabelMe shape mask) |
| `referenced_missing` | the source points at a mask *file* that is absent. Different from `not_supplied` |

No current reader produces file-referenced masks (COCO/LabelMe JSON carry none); the state
exists so an adapter for formats that do (Penn-Fudan-style mask files, VOC) reports a
missing file differently from a mask that was never supplied. A malformed supplied box is
never replaced by a polygon-derived box.

## Coordinate conventions and conversions

All values are kept exactly as supplied (ints stay ints, floats stay floats).

| | Native | Normalized to |
|---|---|---|
| Space | origin top-left, x right, y down, image pixels | same; continuous extent `[0, width] x [0, height]` |
| COCO `bbox` | `[x, y, width, height]` | `(x, y, x + width, y + height)` |
| COCO `segmentation` polygon | flat `[x1, y1, x2, y2, ...]`, one list per part | list of `(x, y)` points per part, parts kept separate |
| LabelMe `rectangle` | two opposite corners, either drawing direction | `(min x, min y, max x, max y)`; original point order stays in `source` |
| LabelMe `polygon` | `[[x, y], ...]` | same points |
| Box from polygon | none | `(min x, min y, max x, max y)` over all parts, `bbox_origin="derived_from_polygon"` |
| Penn-Fudan (legacy) | one-based, inclusive, integer `(Xmin, Ymin)-(Xmax, Ymax)` | **not converted by these readers** |

Boxes do not say which pixels are inside. Do not feed COCO/LabelMe boxes into the legacy
Penn-Fudan checks without an explicit adapter. If one is written, the natural reading of
Penn-Fudan's inclusive one-based pixels is `(Xmin - 1, Ymin - 1, Xmax, Ymax)` in continuous
coordinates. That is an interpretation to confirm with the owner, and it is not implemented
or applied anywhere. Nothing here rasterizes polygons, converts polygons to masks, or
converts between COCO and LabelMe.

## Supported and unsupported features

### COCO (instances / detection JSON)

| Feature | Status |
|---|---|
| `images` (`id`, `file_name`, `width`, `height`), `categories` (`id`, `name`), `annotations` (`id`, `image_id`, `category_id`) | Supported |
| `bbox` `[x, y, w, h]`, integer or fractional | Supported |
| `segmentation` as polygon list, including several parts per instance | Supported, parts kept |
| `iscrowd`, `area`, `supercategory`, licences, URLs, any unknown field | Kept in `source`, not interpreted |
| Images with no annotations; annotations whose `image_id` matches no image | Kept (the latter as `unassigned_annotations`) |
| Duplicate image ID, category ID, annotation ID, or duplicate image `file_name` | Detected, every record kept, nothing chosen |
| Malformed `bbox`, odd-length or non-numeric polygon, missing `file_name`, bad dimensions, unknown category | Diagnosed, record kept |
| Segmentation RLE (uncompressed or compressed counts) | **Deferred.** Kept as `MaskReference("coco_rle")`, not decoded, diagnostic `unsupported_rle`. `pycocotools` is in `requirements.txt` (the correction editor uses it) but decoding here needs decisions on crowd semantics and size mismatches that have not been made |
| `keypoints` | Kept in `source`, diagnostic `unsupported_keypoints` |
| Panoptic, stuff, captions, DensePose | Not implemented |
| File not a JSON object with `images`, `annotations` **and** `categories` lists; duplicate JSON keys; NaN/Infinity; invalid UTF-8; over 64 MiB | **Unreadable**: `AnnotationReadError` |

### LabelMe (one JSON per image)

| Feature | Status |
|---|---|
| `shapes[]` with `shape_type` `polygon` | Supported |
| `shapes[]` with `shape_type` `rectangle`, any corner order | Supported |
| `label`, `group_id` (int or null), `flags`, `description`, any extra field | Kept; `group_id` kept, shapes not merged |
| `imagePath` (relative to the JSON file's directory), `imageWidth`, `imageHeight`, `version` | Supported |
| `circle`, `line`, `linestrip`, `point`, and any other `shape_type` | Kept, diagnostic `unsupported_shape`, no geometry normalized |
| Missing `shape_type` (older LabelMe releases) | Kept, diagnostic `unsupported_shape`. Current upstream LabelMe (checked against `labelme/_label_file.py` on `main`, 29 Oct 2026) rejects a shape with no `shape_type`; older releases treated it as a polygon. This tool does **not** assume either. Open decision, see the checklist |
| Embedded `imageData` | Kept, `unsupported_embedded_image`; image checks skipped rather than silently using a different external image |
| Shape-level `mask` | Kept, `unsupported_mask`, not decoded |
| The original MIT LabelMe **XML** format, LabelMe project folders | Not supported |
| No `shapes` list, invalid JSON, duplicate keys, NaN/Infinity | **Unreadable**: `AnnotationReadError` |

### Checks that need the image files (`annotation_validation.py`)

Missing or unreadable image, unsafe reference (absolute path, URL, drive, escape from the
root), size over limits, declared vs decoded dimension mismatch, box or polygon outside the
decoded image, zero-area polygon, repeated vertex, self-intersection or touching edges,
EXIF-rotated image (unsupported until a coordinate mapping exists), multi-frame image.
Geometry is never clipped or corrected. Polygons over 512 vertices skip the topology check
and say so (`polygon_topology_limit`).

## Reader errors versus annotation mistakes

- **Unreadable**: no annotation can be trusted to exist. `AnnotationReadError`. In the demo
  these are listed under `UNREADABLE INPUT`, separate from everything else.
- **Readable with diagnostics**: the object is kept with its original data. `error` means
  structurally wrong (duplicate ID, malformed box, polygon outside the image),
  `unsupported` means kept but not handled (RLE, circle).
- A diagnostic is not a rejection and not a quality verdict. Whether a box, polygon or mask
  is *correct* depends on the supplied guidelines and the reviewer.

## Verification demo

Read-only. Prints each image, object, label, which geometry types are present and where
each came from, and every diagnostic. Writes overlay PNGs. No audit, no model, no source
file modified.

```powershell
# Synthetic fixtures (writes them to a NEW directory, then previews them)
python -B -m backend.app.datasets.preview_annotations synthetic .\synthetic_demo --max-images 0

# Your COCO subset
python -B -m backend.app.datasets.preview_annotations coco datasets\coco_people_30\annotations\instances_people30.json --image-root datasets\coco_people_30\images --out preview_coco --max-images 5

# LabelMe: one file, or a directory of files (skip companion report files explicitly)
python -B -m backend.app.datasets.preview_annotations labelme datasets\labelme_people_examples --pattern "2011_*.json" --exclude "*.validation.json" --out preview_labelme --max-images 0
```

(On macOS/Linux use `/` in paths.) Options: `--json` full machine-readable report,
`--no-overlay`, `--exclude PATTERN` (LabelMe directories; repeatable), `--max-images N` (0 = all; detail, image checks and overlays cover the first
N images per document, totals always cover every image), `--scale N`, `--reference-base`.
Exit code `0` no diagnostics, `1` diagnostics or unreadable files, `2` nothing usable or bad
arguments.

Overlay legend, written onto every image: **solid** lines are original supplied geometry,
**dashed** lines are geometry derived by this tool (a box computed from polygon vertices),
`*` marks a derived box, `!` marks an object with diagnostics. Coordinates are drawn
unscaled even when the declared size disagrees with the image; small images are
magnified for visibility, and the legend strip sits below the image so coordinates are not
shifted. Objects with no supported geometry (circles, malformed boxes) are counted as not
drawn, never invented.

The older JSON-only inspector `inspect_annotations.py` still works.

## Running the tests

```powershell
python -B -m pytest backend/tests/test_annotation_readers.py backend/tests/test_annotation_validation.py backend/tests/test_annotation_summary.py backend/tests/test_annotation_demo.py backend/tests/test_penn_fudan.py -q -p no:cacheprovider
```

These use only synthetic fixtures and temporary files: no database, model or download.
Two tests in `test_annotation_validation.py` need POSIX features (a FIFO; a symlink, which
Windows allows only with extra privileges) and skip themselves where those are unavailable.

## Where the application still assumes Penn-Fudan

See [INTEGRATION_CHECKLIST.md](INTEGRATION_CHECKLIST.md). The readers are not wired into
anything new by this task; that checklist lists what already exists and what still needs a
decision.

## Format references

- [COCO data formats](https://cocodataset.org/#format-data) and the
  [official COCO API](https://github.com/cocodataset/cocoapi/blob/master/PythonAPI/pycocotools/coco.py).
- [LabelMe's JSON reader](https://github.com/wkentaro/labelme/blob/main/labelme/_label_file.py).

The adapters implement the documented subset above and do not copy upstream code.
