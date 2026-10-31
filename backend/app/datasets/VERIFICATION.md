# Verification results

Recorded 29 October 2026. Synthetic fixtures and real public data are kept apart on
purpose. **Nothing here was tested on Samsung data**, which has not been supplied.

## A. Synthetic fixtures (invented; prove documented behaviour only)

Source: `synthetic_samples.py`; every document carries a SYNTHETIC marker.

| Run | Result |
|---|---|
| Linux sandbox (Claude), readers + validation + summary + demo + Penn-Fudan tests | **136 passed** after the preview changes in section D (122 before) |
| Windows 11, Python 3.12.10, user's machine, same files at the earlier count | **118 passed, 2 skipped** of 120 (FIFO and symlink tests skip on Windows). Two tests were added afterwards |
| Same files before this change (readers, validation, Penn-Fudan) | 94 passed |
| `preview_annotations synthetic <new dir> --max-images 0`, Linux and Windows | **identical totals on both**: images=14, objects=19, unassigned=1, unreadable files=1; labels gadget x4, widget x16; box states derived 5 / malformed 1 / not_supplied 3 / supplied 10; polygon supplied 8 / not_supplied 11; mask not_supplied 18 / retained_not_decoded 1; diagnostics reader 10, geometry 4, image 4. Exit code 1 |

Mutation checks (code deliberately broken, the matching test must fail): derived box drawn
solid, overlay coordinates shifted, Windows guard on `os.O_NOFOLLOW` removed. All were caught.

Other test files touching the changed modules also pass on Linux: `test_correction_models` 48,
`test_dataset_import_zip` 40, `test_dataset_models` 45, `test_asset_manifest` 11,
`test_json_dataset_workflow` (needs `pycocotools`). Not run: the full backend suite (needs
`torch`, a database and POSIX file locking). Known failure that exists without this change:
`test_json_dataset_persistence.py::test_legacy_manifest_serialization_keeps_feedback_hash_identity`.

## B. Real public data

### LabelMe: official examples, run on both machines
Files: `2011_000003`, `2011_000006`, `2011_000025` from `wkentaro/labelme`
(`examples/instance_segmentation/data_annotated`). Run by Claude on Linux and by the user on
Windows from the copy in the repo: **identical results**.

| File | Image | Objects | Diagnostics |
|---|---|---|---|
| 2011_000003 | 500x338, matches declared | 5 | none |
| 2011_000006 | 500x375, matches declared | 10 | `self_intersecting_polygon` on shapes 0 and 1; `polygon_outside_image` on shape 3 |
| 2011_000025 | 500x375, matches declared | 3 | none |

Totals: 18 objects (person 7, sofa 4, bus 2, `__ignore__` 2, bottle, car, chair); all polygons;
every box is `derived`; no masks; geometry diagnostics 3.

The user's first run listed 3 "unreadable" files. These were `*.validation.json` report files
that sit in the same folder and matched the pattern. They are not annotations. The demo now has
`--exclude "*.validation.json"`, and with it unreadable files = 0.

### COCO: `datasets/coco_people_30` (30-image subset of COCO 2017), run by the user on Windows
Subset supplied by a teammate (repo commit 2e900a8). Claude did not run it.

| Measure | Result |
|---|---|
| Images | 30, all decoded and matching declared size, none missing |
| Objects | 242 (person 117, 36 other categories) |
| Geometry types | box supplied 242, polygon supplied 242, mask not supplied 242. No derived boxes |
| Reader diagnostics | 0 |
| Geometry diagnostics | **34** = 17 `repeated_polygon_vertex` + 17 `self_intersecting_polygon` |
| Where | 17 polygons in 15 annotations on 11 of the 30 images (counted from the printed report) |

These counts agree with the teammate's earlier report (30 images, 242 annotations, 117 persons,
17 + 17 diagnostics on 15 records). So the same numbers were obtained by two independent runs
of two different programs.

What this does and does not show:
- The two diagnostics occurred together on every flagged polygon. A repeated vertex also means
  edges touch, so they may share one cause. The coordinates were not analysed.
- 15 of 242 official COCO annotations were flagged in this small, non-random sample. A flag is a
  structural fact, not proof of an annotator mistake. Whether it matters depends on the
  guidelines. No threshold was invented.
- Per `json_sample.publication`, any error-level diagnostic makes the whole sample `invalid`.
  By that code, **11 of these 30 images would be imported as invalid**. This is from reading the
  code; the import was not run. See checklist item 2.
- COCO RLE, keypoints and crowd annotations are not in this subset, so those paths are covered
  only by synthetic fixtures.

### Overlay alignment
Reviewed by eye by Claude on three overlays (screenshots supplied by the user): COCO
`000000001000` (crowded scene, multipart polygons, two flagged persons), COCO `000000403565`
(one person with 7 polygon parts, plus a bicycle) and LabelMe `2011_000006`. In all three the
supplied boxes enclose the objects and the polygon outlines follow the object edges. Derived
boxes (LabelMe) are dashed, supplied boxes (COCO) are solid, and the legend is present.
Limits of this check: 3 of 33 real images, judged visually and not measured, and the individual
flagged polygons were not zoomed into. In the crowded scene the text labels overlap; this is a
readability issue only, not a coordinate issue.

## D. Preview improvements and safety (review of PR #15)

The pushed branch (455a2d5) did not contain the safety fixes. Before changing anything, the
old code was probed with small synthetic inputs (Linux):

| Scenario | Before | After |
|---|---|---|
| Negative-width box `[50, 10, -20, 30]` | The whole preview aborted: exit 2, `x1 must be greater than or equal to x0`, nothing printed | Other objects are drawn; the inverted box is drawn from its supplied corners and keeps its diagnostics |
| Polygon with coordinates of 1e12 and no box | The derived-box dashing loop did not finish within 8 seconds (killed) | Reported as not drawn ("beyond the preview limit"); finishes in under 5 seconds (test) |
| Box of 1e12 | Finished after 2.8 seconds | Same limit and report as above |
| An output file with the generated name already existed | Overwritten | Kept; the new overlay is written as `...-2.png` |
| `--out` is the image folder | Overwrite was possible for a source image with the generated name | Files are created exclusively; test with a source image of that exact name |
| Huge `--scale` | Not probed before | Skipped before any canvas is allocated; a test asserts no allocation happens |

Regression tests added for each row, plus one failing image not stopping the others, object
numbers versus source identifiers, annotation data unchanged by drawing, marker placement in a
crowd, colour consistency and legend wrapping. Mutation checks (code deliberately broken, the
test must fail): allowing overwrite failed 3 tests; removing the coordinate limit and removing
the size check each failed their test.

Real data, regenerated with the new renderer, using the datasets on `main`:
- COCO `coco_people_30`: 30 images, 242 objects, 34 geometry diagnostics. Unchanged from section B.
- LabelMe examples: 3 images, 18 objects, 3 geometry diagnostics. Unchanged.
- Source files: SHA-256 over all 48 files under `datasets/` was identical before and after a run.
- Inspected at full size by Claude: COCO `000000001000` (crowded tennis scene), `000000394275`
  (bus with small people), `000000488592` (bicycles and small objects near the bottom),
  `000000367680` (cars, bus, truck and people around the centre) and LabelMe `2011_000006`.
  Markers did not overlap, each object could be matched to its list entry and source ID,
  solid and dashed geometry stayed distinguishable, and diagnostics and clipped geometry were
  listed. Before/after sheets were also made for six images. The other 26 COCO and 2 LabelMe
  overlays were generated without error but not individually inspected.

Not claimed: no upload, audit or correction workflow was run for this section.

## C. Limitations found
- COCO RLE and keypoints, LabelMe embedded images and shape masks are kept, not decoded.
- Images with EXIF rotation are skipped (no coordinate mapping).
- Overlay alignment was judged by eye, not measured.
- Very small objects are tiny at the default scale; use `--scale` to magnify them.
- Marker placement is a heuristic. In extreme crowding some markers sit away from their object and are tied back with a leader line.
- Markers can cover image content next to an object. The text uses Pillow's default font.
- No end-to-end ZIP upload was run (needs the database and a POSIX host).
- Windows: the readers and demo are portable; managed import and export are not (see checklist).
