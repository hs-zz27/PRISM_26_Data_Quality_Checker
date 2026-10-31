# COCO and LabelMe annotation readers

Standalone, independently testable readers for COCO JSON and LabelMe JSON, a shared
annotation representation, a structural inspector, and a verification demo with
overlays. Penn-Fudan (`penn_fudan.py`, `penn_fudan_zip.py`) is unchanged.

These readers also feed the managed COCO and LabelMe import, audit, correction and native
export paths. See [format integration](../../api/ANNOTATION_FORMATS.md) for the supported
workflow and its remaining limitations. This file covers the standalone reader layer, the
shared representation and the read-only preview. Existing Penn-Fudan readers and API
contracts are unchanged.

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
each came from, and every diagnostic. Writes one overlay PNG per image. No audit, no model,
no source file modified.

```powershell
# Synthetic fixtures (writes them to a NEW directory, then previews them)
python -B -m backend.app.datasets.preview_annotations synthetic .\synthetic_demo --max-images 0

# Your COCO subset
python -B -m backend.app.datasets.preview_annotations coco datasets\coco_people_30\annotations\instances_people30.json --image-root datasets\coco_people_30\images --out preview_coco --max-images 0

# LabelMe: one file, or a directory of files (skip companion report files explicitly)
python -B -m backend.app.datasets.preview_annotations labelme datasets\labelme_people_examples --pattern "2011_*.json" --exclude "*.validation.json" --out preview_labelme --max-images 0
```

(On macOS/Linux use `/` in paths.) Options: `--json` full machine-readable report,
`--no-overlay`, `--exclude PATTERN` (LabelMe directories; repeatable), `--max-images N`
(0 = all; detail, image checks and overlays cover the first N images per document, totals
always cover every image), `--scale S` (default: automatic, about 1000 px on the long
side; use a larger value to magnify small objects), `--vertices` (also mark polygon
vertices; off by default because they clutter crowded scenes), `--reference-base`.
Exit code `0` no diagnostics, `1` diagnostics or unreadable files, `2` nothing usable or
bad arguments. A skipped overlay is reported in the output and does not change the exit code.

### What an overlay shows

- **Image panel (left).** Drawn at one uniform scale computed from the *decoded* image size.
  The declared size is never used, so a wrong declared size cannot shift anything.
  Coordinates are only multiplied by that scale, never rounded, tightened or repaired.
- **Solid lines** are original geometry exactly as supplied (boxes, polygons, every part of
  a multipart polygon). **Dashed lines** are a box this tool derived from polygon vertices;
  it is not in the source file. An object keeps one colour for its lines, its number and its
  list entry. A thin dark outline keeps lines visible on any background.
- **Numbered markers** sit next to their objects and are placed to avoid each other (small
  objects first). A marker pushed away from its object is tied back with a leader line.
  The number is a preview number only. `*` after a number: the object's box is derived.
  `!` after a number: the object has diagnostics.
- **Object list (right).** Colour, number, class and the source identifier (the COCO
  annotation ID; for LabelMe the JSON pointer and `group_id`). Source identifiers are never
  replaced by display numbers.
- **Below the image,** separate from it and wrapped: how to read the picture, the scale,
  any declared-size mismatch, every diagnostic with its message, and every object or part
  that was not drawn or only partly drawn, with the reason.

### Safety behaviour

- **Never overwrites.** Overlays are created exclusively. If the name is taken, the new file
  gets `-2`, `-3`, ... and the existing file is left alone. Pointing `--out` at the image
  folder is therefore safe. `--out` naming a file is an error.
- **One bad image never stops the rest.** A failure becomes `overlay skipped: <reason>`.
- **Invalid boxes** (for example negative width) are drawn from their supplied corners, not
  normalized, and keep their diagnostics.
- **Extreme coordinates** (beyond +-1e7, non-finite, or an element with over 50,000
  vertices) are not drawn. The object stays in the report with the reason. Lines are
  clipped to the image panel analytically, so large values cannot cause long loops.
  Geometry that extends beyond the image is clipped at the edge in the preview only, and
  the preview says so.
- **Output size is validated before allocating:** panel up to 24 million pixels, canvas up to
  36 million pixels and 12,000 pixels per side; otherwise the overlay is skipped with the reason.

The older JSON-only inspector `inspect_annotations.py` still works.

## Running the tests

```powershell
python -B -m pytest backend/tests/test_annotation_readers.py backend/tests/test_annotation_validation.py backend/tests/test_annotation_summary.py backend/tests/test_annotation_demo.py backend/tests/test_penn_fudan.py -q -p no:cacheprovider
```

These use only synthetic fixtures and temporary files: no database, model or download.
Two tests in `test_annotation_validation.py` need POSIX features (a FIFO; a symlink, which
Windows allows only with extra privileges) and skip themselves where those are unavailable.

## Integration status

The managed workflow (ZIP import, audit, correction, feedback, native export) is described in
[ANNOTATION_FORMATS.md](../../api/ANNOTATION_FORMATS.md). What still assumes Penn-Fudan, which
earlier concerns are now resolved on `main`, and the decisions that remain open are in
[INTEGRATION_CHECKLIST.md](INTEGRATION_CHECKLIST.md). The preview described above is
standalone: it reads source files directly and does not use the database or the managed workflow.

## Format references

- [COCO data formats](https://cocodataset.org/#format-data) and the
  [official COCO API](https://github.com/cocodataset/cocoapi/blob/master/PythonAPI/pycocotools/coco.py).
- [LabelMe's JSON reader](https://github.com/wkentaro/labelme/blob/main/labelme/_label_file.py).

The adapters implement the documented subset above and do not copy upstream code.
