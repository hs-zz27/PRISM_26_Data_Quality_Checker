# Integration checklist

Status of COCO and LabelMe support on `main` plus PR #15, checked on 31 October 2026 by
reading the code of `main` (after the datasets commit 2e900a8 and the later workflow commits).
The workflow itself is documented in [ANNOTATION_FORMATS.md](../../api/ANNOTATION_FORMATS.md).
This file does not replace it; it records what is resolved, what is still open, and what was
not verified.

## What `main` already provides

| Capability | Where |
|---|---|
| COCO and LabelMe ZIP import | `datasets/json_zip.py`, `services/dataset_import_jobs.py` |
| Per-image JSON audit (mask-dependent checks report `unavailable`) | `services/json_audit.py` |
| A never-supplied mask is `not_supplied`, distinct from a referenced file that is `missing` | `AssetAvailability.NOT_SUPPLIED` in `models/asset_manifest.py`; `services/asset_snapshot.py`; migration `015_optional_mask_assets.sql` |
| JSON corrections, including partial fixes with other diagnostics left visible | `services/json_corrections.py` |
| Image-scoped reviewer feedback, recheck and bounded correction proposals | `models/review_feedback.py`, `services/review_correction.py` |
| Native export and re-import | `services/export_archive.py` |

## Concerns from the first checklist

| # | Concern | Status on `main` |
|---|---|---|
| 1 | Never-supplied mask stored as `missing` | **Resolved.** `not_supplied` for non-Penn-Fudan formats; Penn-Fudan still requires a mask |
| 2 | Any error-level diagnostic marks the sample `invalid` | **Still true.** `datasets/json_sample.py` line 71 is unchanged. The effect is smaller: corrections no longer require `valid`, and `ANNOTATION_FORMATS.md` states an invalid structural status never forces a final rejection. On the real 30-image COCO subset, 11 images carry an error-level geometry diagnostic and would be labelled `invalid`. Decision for the owner: keep the label, or separate "unreadable" from "readable with possible mistakes" |
| 3 | A correction needs zero remaining errors | **Resolved.** A partial fix saves; the edit must not introduce a new structural error |
| 4 | `object_id` is positional | **Resolved in use.** Feedback and corrections identify objects by `source_key` plus the pinned `image_key`; `object_id` remains a display number |
| 5 | LabelMe shape with no `shape_type` | Consistent: flagged unsupported, not guessed (current upstream LabelMe rejects such a shape) |
| 6 | Embedded LabelMe `imageData` | Consistent: retained, not decoded |
| 7 | Penn-Fudan boxes are 1-based inclusive; COCO and LabelMe are 0-based continuous | **Still open.** No adapter exists, and `main` states there is no silent conversion |

## Still Penn-Fudan specific on `main`

| Area | Code | Behaviour for COCO / LabelMe |
|---|---|---|
| Specialized pedestrian enclosure renderer | `services/enclosure.py` | `unsupported_coordinate_system`; JSON visual checks use the general confirmed-guideline path |
| Pixel-mask checks | `services/json_audit.py` | `unavailable` (`pixel_mask_not_supplied_in_supported_encoding`) |
| Mask upload or replacement | `services/corrections.py` | Rejected with `JSON_SOURCE_NOT_CORRECTABLE` |
| Geometry editing | `services/json_corrections.py` | Existing boxes, polygons and labels only: no add or delete, no change of geometry type, no RLE |
| COCO RLE, keypoints, embedded masks, non-polygon LabelMe shapes | readers | Retained and reported as unsupported |

## Platform

Managed import, export and snapshot code uses POSIX file flags (`O_NOFOLLOW`, `fcntl`, and
related): `asset_snapshot.py`, `export_files.py`, `dataset_upload.py`, `dataset_import_jobs.py`,
`export_archive.py`, `penn_fudan_zip.py`. They need Linux, WSL or Docker. PR #15 makes
`datasets/annotation_validation.py` (the readers' image check, and so the preview) work on
native Windows; before it, that file used `os.O_NOFOLLOW` unconditionally.

## Not verified here

- A full ZIP upload, audit, feedback, correction or export run. They need the database and a
  POSIX host. What was run is listed in [VERIFICATION.md](VERIFICATION.md).
- Samsung data (not supplied).
- COCO RLE, keypoints and crowd annotations on real data (covered by synthetic fixtures only).
- The repository defines no CI workflow (`.github` is absent), so there are no automated
  checks beyond whatever GitHub shows on the pull request.
