# Integration checklist: where the application still assumes Penn-Fudan

State of `main` at commit b4dc408 (29 Oct 2026). This task changed only the readers,
validation, demo and docs. Everything below is for the owner to decide. Nothing in it was
applied. Each item names the code that matters.

## Already in place (earlier commits, not part of this task)
Format dispatch in `services/dataset_import_jobs.py`; `datasets/json_zip.py` and
`json_sample.py`; `json_audit.py`; `json_corrections.py`; format-aware export in
`export_archive.py`; `JsonAnnotationObject` in `models/dataset.py`; migration 014.
These were not re-reviewed or end-to-end tested here.

## Decisions needing the owner (shared models)

| # | Issue | Where | Suggested change |
|---|---|---|---|
| 1 | A mask that was **never supplied** is stored as `missing`, the same as a referenced file that is gone. `raw_mask` is required for every format. | `models/asset_manifest.py` (`AssetAvailability` has only available/missing/unreadable; `raw_mask` required); `services/asset_snapshot.py` ~285-295 | Add `not_supplied` and make `raw_mask` optional for COCO/LabelMe. Must keep Penn-Fudan manifest hashes byte-identical (see `preserve_legacy_identity`) |
| 2 | Readable annotations with mistakes make the whole sample `integrity_status="invalid"`, mixing "unreadable" with "possibly wrong". **Real-data evidence:** in the 30-image COCO subset, 11 images carry an error-level geometry diagnostic and would be marked invalid (from code reading; import not run). | `datasets/json_sample.py::publication`; `models/dataset.py` (`Sample` demands no issues when valid) | Keep `invalid` for unreadable input; carry reader/geometry diagnostics in a separate field. Needs a `Sample` change |
| 3 | A correction is refused unless **zero** errors remain, so a partial fix cannot be saved. | `services/json_corrections.py` (the `errors` check, final `integrity_status` check) | Allow saving while diagnostics remain; re-report them |
| 4 | `object_id` is positional (`index + 1`). | `json_sample.display_objects` | Feedback should reference `source_key` plus the pinned `image_key`, not `object_id` |
| 5 | Missing `shape_type` in LabelMe is "unsupported". Current upstream LabelMe rejects such shapes; old releases treated them as polygons. | `datasets/labelme.py` | Keep, unless Samsung's files omit it |
| 6 | LabelMe embedded `imageData` is not decoded; the sample imports with no image and an error. | `json_zip._resolve_image`, `labelme.py` | Decide whether to support it once Samsung's format is known |
| 7 | Penn-Fudan boxes are 1-based inclusive integers; JSON formats are 0-based continuous. No adapter exists. | `models/system.py` states it; `checks/*` | Write one explicit, tested adapter before sharing checks |

## Still Penn-Fudan only

| Area | Code | Behaviour for COCO/LabelMe |
|---|---|---|
| Audit engine | `services/audit.py:190`, `services/detection_audit.py:93` (`parse_annotations`) | Not used; `sample_worker` routes JSON samples to `json_audit` |
| Mask checks | `json_audit.py` | All mask checks report `unavailable` |
| Person-detection evidence | `detection_audit.py` | Not format-aware |
| Box-grounding / enclosure | `services/enclosure.py:172`, `enclosure_feedback.py` | `unsupported_coordinate_system` |
| Metadata check | `checks/metadata.py` | Penn-Fudan declared metadata only |
| Mask upload / replacement | `services/corrections.py:89` | Rejected with `JSON_SOURCE_NOT_CORRECTABLE` |
| Editor limits | `json_corrections.py` | No add/delete of records, no RLE or unsupported shapes, a supplied box cannot be removed |
| Export | `export_archive.py` | Per-image JSON under `data/<id>/`; masks omitted; no merged COCO file |
| Snapshot capture | `asset_snapshot.py` | Docstring and `PedMasks` constants are Penn-specific |
| Frontend | `features/corrections/jsonGeometry.ts` etc. | Not reviewed in this task |

## Platform
Managed import and export use `O_NOFOLLOW`, `O_DIRECTORY`, `dir_fd` and `fcntl`
(`penn_fudan_zip.py:104`, `dataset_upload.py:24`, `dataset_import_jobs.py:91`,
`export_files.py`, `export_archive.py:149`, `asset_snapshot.py:27`). They need POSIX
(WSL or Docker on Windows). The readers, inspector and demo were made portable.

## To verify before relying on it
- ZIP layout search for COCO tries the JSON's folder, `images/`, parents and the root;
  several matches give `INVALID_LAYOUT`. Test with real archives (needs the database).
- Import resource limits (`max_samples`, `max_objects_per_sample`) against a real COCO file.
- Any COCO with RLE, keypoints or LabelMe non-polygon shapes imports as `invalid` with
  `unsupported_source`.
