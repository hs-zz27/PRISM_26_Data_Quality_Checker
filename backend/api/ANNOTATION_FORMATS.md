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
- Actual Samsung data has not been supplied or verified.
- Standalone, read-only diagnostics and annotation previews are available with
  `python -m backend.app.datasets.preview_annotations` (see
  `backend/app/datasets/README.md`). They read source files directly, change nothing, and are
  not part of the managed upload, audit or correction workflow.

## Verification and deployment

Native ZIP import/capture, coordinate preservation, corrections, export/reimport and
partial fixes have automated filesystem tests. Database integration uses isolated
PostgreSQL test schemas. UI tests verify image-scoped submission and explicit
preview/apply before revision creation. The correction-model protocol uses mocked
responses; a real local-model quality and latency evaluation remains necessary.

Database migrations 014 and 015 add format metadata and the optional-mask state.
API and worker processes must use the same updated code and migrated schema.
Code/test completion does not mean an already running Docker stack has been rebuilt.

## Manual upload-to-export check

Status: pending user execution after PR #15. Run separately for a small COCO ZIP
and a small LabelMe ZIP, each with at least two images and its referenced external
image files. Use a new test dataset so the original review history remains intact.
The standalone preview command does not exercise these steps.

1. Upload the ZIP with the matching format. Record the dataset and import IDs.
   Check that both images, object labels and supplied geometries appear. Confirm
   that LabelMe polygon-derived boxes are distinguished from supplied boxes and
   that missing optional masks are not described as missing required files.
2. Enter explicit plain-text guidelines, inspect the extracted rules and confirm
   them. Start an audit and record its ID. Inspect both flagged and apparently
   correct images. Unsupported checks must remain visibly unavailable. A structural
   `invalid` label must not be mistaken for a final reviewer rejection.
3. Select one image, record its revision and the other image's attempt/revision,
   and submit a concrete observation through **Recheck with feedback**. Verify
   that the comment belongs to the selected image's new attempt, the annotation
   revision is unchanged, and the other image was not rechecked or edited.
4. On an image with a visible annotation issue, choose **Suggest a correction**.
   If the model returns a proposal, use **Preview correction** and **Show original**
   to compare it. Neither action should save a revision. Inspect whether the draft
   actually follows the confirmed guideline and addresses the comment. If the model
   is unavailable or unsure, record that outcome; the successful proposal/apply
   path remains unverified until a usable proposal is returned.
5. Apply a suitable proposal with **Apply correction and recheck**. Verify that a
   new revision is saved for that image, reviewer/reason information is retained,
   and the recheck uses that revision. Check that the other image and confirmed
   guidelines are unchanged. The reviewer must still make the final decision.
   If saving succeeds but rechecking fails, record the saved revision and retry
   its recheck rather than applying the same correction again.
6. After the audit/rechecks complete, make the intended review decisions and export
   with images included and a selection that includes the corrected image. Inspect
   the export manifest for the selected revision. Re-import the exported ZIP as a
   new dataset of the same format and verify the corrected geometry/label, source
   identity mapping and image alignment. Check that unrelated annotations remain
   present; JSON byte equality is not expected because image paths are rebased.

For each format, record the tested commit/local changes, dataset and audit IDs,
sample IDs, before/after revisions, export ID, model tag, observed model latency
and result of each step. Capture error messages and relevant screenshots when a
step fails. Keep a failed or unrun step marked as such; a preview test or mocked
model test cannot substitute for this run.
