# Local verification — 1 November 2026

- Generated 42 altered cases, 42 PNG comparisons, two mixed-upload controls and six ZIPs.
- Two distinct source photographs, intentionally repeated across the combinations and depths.
- Source annotation and photograph hashes matched their pinned values and stayed unchanged.
- Every altered target lost less than 3% of its source polygon area.
- Every native document parsed without reader diagnostics; every original and altered target
  polygon passed the existing self-intersection and degeneracy checks.
- Tests verified native metadata, labels, IDs, unrelated objects, source photos, edit depths,
  area accounting, local coverage removal and protection of source/existing-pack folders.
- All six archives passed the real managed ZIP unpacker: 21, 21 and 8 samples for each format.
  Every referenced photo resolved, sample identities were unique, and local image/geometry
  inspection produced no diagnostics. No answer key or edit descriptions are in the ZIPs.
- Test command: `.venv/bin/python -B -m pytest evaluation/polygon_boundary_pack/tests -q -p no:cacheprovider`
- Result: **15 passed in 2.97 seconds**. No skips.
- Representative generated previews inspected: both six-pixel three-region combinations and
  the two-pixel COCO finger case. This is visual preparation, not new human ground truth.

No models, database or application services were started. No HTTP upload, live model audit,
correction workflow or detection accuracy was tested. New human review remains pending;
the intended missing-body-part interpretation must not be scored as confirmed truth yet.
The original annotation evaluation pack and all application code remain unchanged.
