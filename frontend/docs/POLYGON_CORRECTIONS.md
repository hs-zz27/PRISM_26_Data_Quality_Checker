# Visual polygon correction: step 2

## Status

Implemented locally. The owner reported 29 frontend tests passed and one failed
because a background-query assertion did not wait for rendering. That assertion
now waits for the warning; its rerun is pending. The owner also reported 30 backend
tests passed and 10 PostgreSQL integration tests skipped. The database tests require
the explicit connection setting below. No tests were run by the implementation
agent, at the owner's request. Browser validation, typecheck and real-model
detection and learning accuracy remain unverified.

## Reviewer workflow

1. In an audit's review screen, open the image and choose **Correct**.
2. For a COCO or LabelMe polygon, select its outline from the image or object menu.
3. Drag a vertex, enter its exact X/Y coordinates, or focus it and use arrow keys.
   Arrows move 1 original-image pixel; Shift + arrow moves 10. Coordinates retain
   fractional precision and stay within the image.
4. **Add point after selected** inserts the midpoint of the next edge. Drag it to
   the desired boundary. **Remove selected point** keeps at least three distinct
   points. Multipart polygons retain their separate parts and source identity.
5. Use **Undo/Redo**, zoom and scroll, and **Show saved outline** to inspect the edit.
   Cyan is the draft, dashed white is the saved outline, yellow is a supplied box.
6. Enter a correction reason and choose **Save as new revision**. Inspect the loaded
   revision, then choose **Re-check this sample**. The result links back to the
   original audit workspace. Agree with or override findings there separately.

Saving changes annotation geometry, not the image or guideline. Independent COCO
supplied boxes are not silently resized when a polygon changes. Polygon-derived
bounds update to cover every part. LabelMe's optional duplicate closing point is
kept consistent. Labels and supplied JSON-format boxes remain editable through
**Advanced annotation fields**; this step adds visual polygon editing only.
Penn-Fudan's existing box editor and replacement-mask upload remain available.

## Revision handling

The correction draft keeps its starting revision until saved or explicitly
reloaded. A background update cannot replace unsaved edits. A stale save is rejected
by the existing API revision check and the draft remains available. Switching
samples or choosing Reload requests confirmation when edits would be discarded.
Browser refresh/close has an unsaved-edit warning. Navigation through unrelated
application links is not intercepted by a global route blocker in this step.

After a successful save, editing and rechecking are locked until the saved revision
has loaded. A failed reload does not repeat the save. Use Reload current revision
to recover. If another reviewer has already saved a later revision, that revision
is shown explicitly and should be inspected before rechecking.

The backend still validates source identity, geometry bounds and topology before
publishing a revision. Crossed edges can exist temporarily in the editor while
moving points; a new structural error is rejected on save. No object creation,
deletion, geometry-type conversion, mask painting or automatic annotation acceptance
is introduced by this editor. This is manual correction and visual reevaluation,
not automatic model training or activation of feedback examples.

## Owner-run checks

From the repository root:

```sh
npm --prefix frontend run test -- \
  src/features/corrections/polygonEditing.test.ts \
  src/features/corrections/PolygonEditor.test.tsx \
  src/features/corrections/CorrectionsPage.test.tsx \
  src/features/corrections/correctionValidation.test.ts \
  src/features/audits/AuditReviewWorkspace.test.tsx
npm --prefix frontend run typecheck
```

```sh
.venv/bin/python -B -m pytest \
  backend/tests/test_polygon_revision_recheck.py \
  backend/tests/test_json_dataset_workflow.py \
  backend/tests/test_rechecks.py \
  -q -rs -p no:cacheprovider
```

The ten tests in `test_rechecks.py` require a running PostgreSQL server and
`PRISM_TEST_DATABASE_URL`. To run them using the repository's local `.env` database
settings, without printing the credentials:

```sh
PRISM_TEST_DATABASE_URL="$(.venv/bin/python -c 'from backend.app.core.config import load_database_settings; print(load_database_settings().conninfo.get_secret_value())')" \
  .venv/bin/python -B -m pytest backend/tests/test_rechecks.py -q -rs -p no:cacheprovider
```

These fixtures create a unique temporary schema per test and remove that schema
afterward. They preserve application tables; the database user needs permission
to create schemas. `-rs` displays skip reasons rather than hiding them in the count.

Frontend tests cover scaled and scrolled pointer coordinates, cancelled gestures,
keyboard edits, fractional precision, insertion/deletion, undo/redo, source-outline
comparison, save/recheck request contents, save conflicts, failed/delayed reloads,
and sample switching. Backend tests exercise real temporary source files and show
that a revised polygon reaches a new visual request with the same image and rule.
They do not call Ollama or establish visual accuracy.

For browser acceptance, open a human-reviewed LabelMe or COCO case, correct one
outline and compare another object's coordinates before and after. Save, check the
revision history, and recheck the new revision. Confirm that the original revision
remains available and the reviewer still decides the outcome. Try cancelling a
drag, zooming while inspecting, adding a point near a hand/foot, and a deliberately
crossed polygon that must be rejected by the API. A recheck of an old audit retains
that audit's execution version; start a new v4 audit first to exercise step 1 too.
