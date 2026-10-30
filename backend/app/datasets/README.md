# Annotation readers and structural inspection: phases 1–3

These readers also feed the managed COCO and LabelMe import, audit, correction
and native export paths. See [format integration](../../api/ANNOTATION_FORMATS.md)
for the supported workflow and remaining limitations. The description below covers
the standalone reader layer. Neither a successful parse nor an empty issue list
proves annotation quality or image compatibility.

## Usage

From the repository root in the project's Python environment:

```python
from backend.app.datasets.coco import read_coco
from backend.app.datasets.labelme import read_labelme

dataset = read_coco("path/to/instances.json")
# Alternatively, one image annotation document:
# dataset = read_labelme("path/to/image.json")

for image in dataset.images:
    print(image.key, image.file_name, image.width, image.height)
    for obj in image.annotations:
        print(obj.key, obj.label, obj.bbox, obj.bbox_origin, obj.polygons)
for issue in dataset.all_issues:
    print(issue.severity, issue.code, issue.location, issue.message)
print("Unassigned annotations:", len(dataset.unassigned_annotations))
```

`parse_coco(dict)` and `parse_labelme(dict)` accept already decoded JSON objects.
`read_*` additionally preserves original file bytes and the source path. File reads
are limited to 64 MiB by default, configurable through `max_bytes`. Invalid JSON,
duplicate JSON keys, non-finite numbers and missing required top-level lists raise
`AnnotationReadError`. Readable malformed records are retained with diagnostics.

## Shared representation

`models/annotation_document.py` provides image records, annotation records, mask
references and diagnostics. `source` keeps all supplied fields, including unknown
metadata, categories, COCO crowd/area fields and LabelMe flags. Parsed source data
is copied from caller input; it is not deeply immutable. Consumers must treat it
as evidence and create separate corrected records. Exact source bytes remain the
authority when using file readers.

Document-local JSON pointers identify records even when IDs are missing or
duplicated. A caller combining files must namespace these keys by document identity.
COCO duplicate image/category references are not resolved arbitrarily. Unmatched
or ambiguous image annotations remain in `unassigned_annotations`. LabelMe shapes
retain their group IDs but are not merged, and group IDs are not object IDs.

Coordinates are zero-based, unrounded image coordinates. Normalized boxes are
`(left, top, right, bottom)`:

- COCO `[x, y, width, height]` becomes `(x, y, x + width, y + height)`.
- LabelMe rectangle endpoints become axis-aligned bounds, allowing either drawing
  direction. The original point ordering remains in the source record.
- Polygon vertices and separate parts remain intact. A convenience polygon box
  is explicitly `derived_from_polygon`; it is not a supplied box annotation.
- A malformed supplied COCO box is never replaced by a polygon-derived box.
- No coordinates are rounded, clipped, scaled or rasterized. These boxes do not
  specify pixel inclusion. Do not send them into the legacy Penn-Fudan checks,
  which use one-based inclusive integer coordinates, without an explicit adapter.

Optional mask references preserve encoding and supplied data. No mask is generated
or decoded. A sample without masks does not require a fabricated empty mask.

## Supported subset and limits

COCO: detection/instance documents with `images`, `annotations` and `categories`,
XYWH boxes and polygon-list segmentation. Multiple parts per instance, empty
images, fractional coordinates and arbitrary class names are retained. COCO RLE
and keypoints produce explicit unsupported diagnostics; decoding, crowd-mask
semantics, panoptic and caption formats are not implemented.

LabelMe: one JSON document per image, explicit `polygon` and `rectangle` shapes,
labels, group IDs and source metadata. Other shapes, missing shape types, embedded
images and embedded shape masks are retained with unsupported diagnostics. No
implicit legacy shape-type assumption is made.

The parsing functions never resolve paths, open referenced images, download URLs
or decode embedded data. Optional image and geometry inspection is now available
as a separate step below. There is no pass/fail audit outcome at this layer.

## Phase 3: inspect actual images and geometry

```python
from backend.app.datasets.annotation_validation import inspect_document

report = inspect_document(dataset, image_root="path/to/dataset")
for image in report.images:
    print(image.file_name, image.availability, image.actual_width, image.actual_height)
for issue in report.all_issues:
    print(issue.code, issue.location, issue.message)
```

Or use the read-only command from the repository root:

```sh
.venv/bin/python -B -m backend.app.datasets.inspect_annotations coco path/to/instances.json --image-root path/to/images
.venv/bin/python -B -m backend.app.datasets.inspect_annotations labelme path/to/dataset/annotations/image.json --image-root path/to/dataset
```

The command prints a JSON inspection report, including decoded dimensions,
image hashes, annotation counts and diagnostics. Exit codes are `0` for no
diagnostics, `1` for diagnostics (including unsupported checks), and `2` for an
unreadable document or invalid configuration. These are not acceptance/rejection
decisions and do not measure model accuracy. Files are not modified or exported.

Path rules:

- COCO image references are relative to `image_root`.
- LabelMe image references are relative to the annotation JSON directory. For
  in-memory documents without a source path they are relative to `image_root`.
- `--reference-base` (or the Python `reference_base` argument) explicitly changes
  the reference directory. The permitted root remains `image_root`.
- Nested directories and parent references inside the root are allowed. Absolute
  paths, URLs, Windows drive/separator paths and resolved paths outside the root
  are reported. External symlink targets are not read.
- This tool inspects local files that are not being concurrently modified. It
  does not replace managed archive extraction, snapshots or their safeguards.

The inspector decodes single-frame PNG/JPEG files, compares declared dimensions,
and checks supplied boxes/polygons against actual image extents. A mismatch is
reported without rescaling coordinates. It checks polygon zero signed area,
repeated vertices and non-adjacent edge intersections/touches. An explicit repeated
closing vertex is allowed. Parts are checked separately; overlaps between distinct
parts or objects are not interpreted as errors. Original polygon-part indices are
preserved in diagnostic locations, including after an earlier malformed part.

Bounds use continuous extents `[0, width]` and `[0, height]`, not a semantic fit
tolerance or rasterization convention. Derived polygon boxes are not treated as
supplied box annotations. Geometry is not clipped, corrected or dropped. Structural
diagnostics are separate from guideline-based quality findings and human decisions.

Default inspection limits are 64 MiB per image, 16 million decoded pixels and
512 vertices per polygon topology check. `ValidationLimits` allows explicit
configuration. Larger polygons retain their coordinates and bounds diagnostics;
skipped topology checks are marked unsupported. Image availability describes image
decoding only, not annotation correctness or completion of all checks.

Embedded LabelMe images are deferred; the inspector does not silently substitute
external pixels when `imageData` is supplied. EXIF-oriented images get an unsupported
diagnostic, and dimension/bounds checks are skipped until coordinate mapping is
implemented. Mask decoding/validation, model evidence and guideline assessment are
not part of this inspection phase. Reader diagnostics are included in the report,
and both the parsed document and source files remain unchanged.

## Integration still needed

1. Verify these readers and the local inspector against teammate 1's real samples,
   and agree on archive layouts while keeping the existing import safeguards.
2. Dispatch imports by format. The current `dataset_import_jobs.py` directly calls
   `penn_fudan_zip.py`; `DatasetFormat` still only exposes Penn-Fudan.
3. Extend catalogue/storage/API contracts to preserve new geometry and provenance.
   `ImportedAnnotationObject` currently requires integer boxes; `SamplePublication`
   assumes every valid sample has a mask. Requirements must become format-aware.
4. Adapt audit and model evidence readers. They currently parse Penn-Fudan text.
   Derived geometry must not be used as independent proof that its source is correct.
5. Add polygon display, editing, correction serialization and format-aware exports.
   None of these is enabled by the readers alone.

## Verification

Fixtures in `backend/tests/test_annotation_readers.py` and
`backend/tests/test_annotation_validation.py` are synthetic. Validation tests
create temporary images; they use no database, model or downloaded datasets. Run:

```sh
.venv/bin/python -B -m pytest backend/tests/test_annotation_validation.py backend/tests/test_annotation_readers.py backend/tests/test_penn_fudan.py -q -p no:cacheprovider
```

Real dataset compatibility and model accuracy remain unverified until representative
public samples are tested. No additional dependencies are needed for these readers.

## Format references

- [COCO data formats](https://cocodataset.org/#format-data) and
  [official COCO API](https://github.com/cocodataset/cocoapi/blob/master/PythonAPI/pycocotools/coco.py).
- [LabelMe's official JSON reader](https://github.com/wkentaro/labelme/blob/main/labelme/_label_file.py).

The adapters implement the documented subset above rather than copying upstream code.
