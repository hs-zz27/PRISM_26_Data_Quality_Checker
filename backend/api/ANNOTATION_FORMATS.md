# Annotation formats and image feedback

## Supported workflow

Choose Penn-Fudan, COCO or LabelMe when creating a dataset, then upload a ZIP for
that format. COCO requires its instance JSON and referenced PNG/JPEG images.
LabelMe requires per-image JSON and external PNG/JPEG images. Confirm the extracted
plain-text guidelines before auditing. Findings and import diagnostics remain
subject to human review; an invalid structural status never makes a final rejection.

The managed workflow retains original bytes and source identities, displays supplied
boxes and polygon outlines, runs applicable confirmed checks, saves correction
revisions, rechecks an image, and exports the selected reviewed annotations in their
native format. Export rebases image references; exported JSON is not byte-identical
to the source. Original images are preserved. Exported native ZIPs can be imported again.

Penn-Fudan coordinates are one-based inclusive integers. COCO and LabelMe use
zero-based continuous coordinates. Polygon-derived bounds are marked as derived,
not treated as supplied boxes. Annotation identity includes the pinned source
document/image and the source JSON pointer, not only a positional display number.

## Feedback inside the audit workspace

1. Select an image and enter **Feedback for this image** beside its findings.
2. **Recheck with feedback** saves the comment with a new image attempt and reruns
   the checks against the same confirmed guidelines. Available visual checks receive
   the comment as an observation to verify. Deterministic checks still evaluate the
   actual annotation data. No annotation is changed by this action.
3. **Suggest a correction** additionally asks the pinned local vision model for
   a bounded draft. A missing model, uncertain answer or invalid response is shown
   as unavailable/unsure, never as a successful correction.
4. Preview the proposed geometry on the same image, then explicitly choose
   **Apply correction and recheck**. This uses the existing revision endpoint,
   records reviewer identity and reason, and checks the saved revision. Final human
   acceptance/rejection is still required. If enqueue fails after saving, the UI
   identifies the saved revision and lets the reviewer retry its recheck.

Feedback is scoped to that image attempt. An ordinary recheck does not inherit it.
It does not edit guidelines or activate a future-batch policy. The separate held-out
evaluation and explicit policy activation workflow remains available through the
command palette as **Policy evaluation (future batches)**; it is no longer a primary
Feedback navigation section.

## Corrections and outstanding limitations

- JSON corrections edit existing supported boxes, polygons and labels. They preserve
  source identities and metadata. Adding/deleting JSON objects, changing geometry
  types and generating replacement pixel masks from feedback are not implemented.
- A partial JSON fix may be saved while other records still have diagnostics. Those
  diagnostics remain visible. Changes cannot introduce new structural geometry errors.
- Model proposals currently edit at most four existing objects, with bounded geometry
  and image/context size. They require human inspection; automated tests do not
  establish the local model's correction accuracy.
- Supplied Penn-Fudan pixel masks retain their existing validation and manual mask
  revision workflow. COCO RLE, embedded LabelMe masks/images, keypoints and unsupported
  LabelMe shapes are retained and reported as unsupported. They are not silently
  decoded or treated as valid. Missing `shape_type` is not guessed.
- JSON samples without a standalone mask file use `not_supplied`, distinct from a
  referenced file that is missing. Mask-dependent checks explicitly report unavailable.
- The specialized pedestrian enclosure renderer still requires Penn-Fudan coordinates.
  JSON visual checks use the general confirmed `annotation_guideline` path; there is
  no silent coordinate conversion into the specialized renderer.
- Actual Samsung data has not been supplied or verified. The teammate's standalone
  diagnostics/preview improvements remain on their unmerged branch.

## Verification and deployment

Native ZIP import/capture, coordinate preservation, corrections, export/reimport and
partial fixes have automated filesystem tests. Database integration uses isolated
PostgreSQL test schemas. UI tests verify image-scoped submission and explicit
preview/apply before revision creation. The correction-model protocol uses mocked
responses; a real local-model quality and latency evaluation remains necessary.

Database migrations 014 and 015 add format metadata and the optional-mask state.
API and worker processes must use the same updated code and migrated schema.
Code/test completion does not mean an already running Docker stack has been rebuilt.
