# Verification record

Prepared on 31 October 2026 by claude-ai-assistant (AI assistant), working with the repository
owner. Visual judgements below are the assistant's; a human reviewer has not yet confirmed them.

## Environment

| Item | Value |
|---|---|
| Source repository commit | `d8f6041e9f947229243d24adaf50ee4311993681` (`origin/main`, "Merge pull request #15") on branch `evaluation/annotation-error-pack` |
| Windows | Windows 11 (10.0.26300), Python 3.12.10, Pillow 12.3.0, pytest 9.1.1, Git 2.49.0 (`core.autocrlf=true`) |
| Linux | `python:3.12-slim` container (WSL2 kernel 6.18), Python 3.12.15, Pillow 12.3.0, pytest 9.1.1, Docker 29.5.3 |
| Application | the repository's API and dataset-import worker containers on the same commit, database at migration 015 |

## Commands actually run

```sh
# build + all verification checks (Windows)
.venv/Scripts/python.exe -B evaluation/annotation_error_pack/build_pack.py
# reproducibility (Windows, then Linux against the Windows outputs)
.venv/Scripts/python.exe -B evaluation/annotation_error_pack/build_pack.py --check
docker run ... python:3.12-slim /venv/bin/python -B evaluation/annotation_error_pack/build_pack.py --check
# independent Linux build into a separate read-only-source folder, compared file by file
docker run ... /venv/bin/python -B evaluation/annotation_error_pack/build_pack.py --out /out --no-verify
# tests (Windows, then Linux where the real importer runs)
.venv/Scripts/python.exe -B -m pytest evaluation/annotation_error_pack/tests -q -p no:cacheprovider
docker run ... /venv/bin/python -m pytest evaluation/annotation_error_pack/tests -q -p no:cacheprovider
# standalone preview tool on each extracted archive (output written outside the repository)
.venv/Scripts/python.exe -B -m backend.app.datasets.preview_annotations coco <x>/annotations/instances.json --image-root <x>/images --out <tmp> --max-images 0
.venv/Scripts/python.exe -B -m backend.app.datasets.preview_annotations labelme <x> --out <tmp> --max-images 0
# application import of all four archives
curl -X POST http://localhost:8000/api/v1/datasets -F name=... -F format=coco|labelme -F archive=@<zip>
```

## Results

### Builder verification (`build_pack.py`): 96 of 96 checks passed

- **Counts:** each COCO ZIP holds exactly one JSON, 5 image records and 5 image files; each LabelMe
  ZIP holds exactly 3 JSON files and 3 image files; nothing else is in any ZIP.
- **No answer key, guidelines, overlays or reports inside the ZIPs.**
- **ZIP hygiene:** CRC test passed; no directory entries; all entries stored; regular-file modes;
  no unsafe or case-colliding names.
- **References:**
  - Every COCO `file_name` matches exactly one archive file under the importer's search rules.
  - Every LabelMe `imagePath` resolves beside its JSON, with `imageData` null.
  - No unreferenced images.
- **Images open:** every referenced image opens with its declared size, in both versions of every
  archive, using the project's own inspector.
- **COCO baseline content:** the annotations equal the source annotations of the five images;
  `info`, `licenses` and all 80 categories equal the source; every used category is defined;
  annotation IDs are unique; all boxes have positive width and height.
- **Image identity:** every baseline/altered image pair is byte-identical and equals the pinned
  source SHA-256.
- **Single-field differences:**
  - Each COCO altered case differs from its packaged baseline only in its target annotation's
    documented field, with no difference anywhere else.
  - L02 and L03 differ only in `/shapes/N/points` and `/shapes/N/label` respectively, with shape
    order and `group_id` preserved, and their baselines are the exact upstream bytes.
  - L01 is byte-identical in both archives.
- **Answer key:** exactly the 16 required entries, and the pairs point at each other.
- **Comparisons:** 8 comparison PNGs exist.
- **Sources:** every file under `datasets/` and `PennFudanPed/` has the same raw SHA-256 before and
  after the build.

### Diagnostics: existing versus introduced

Reader, image and geometry diagnostics from the project's inspector, run on both versions of
every archive. Pointers are packaged pointers; the source pointer is in brackets.

| Archive member | Existing (present in baseline and altered) | Introduced by edits |
|---|---|---|
| COCO `annotations/instances.json` | `repeated_polygon_vertex` and `self_intersecting_polygon` on `/annotations/1` (`/93`, **C03's target**), `/18` (`/143`), `/19` (`/144`), `/30` (`/167`), `/37` (`/174`) | none |
| LabelMe `2011_000006.json` | `self_intersecting_polygon` on `/shapes/0` and `/shapes/1`; `polygon_outside_image` on `/shapes/3` | none |
| LabelMe `2011_000003.json`, `2011_000025.json` | none | none |

No diagnostic was introduced or removed by any edit. Every deliberate error is structurally
valid, so the current structural checks cannot detect it; that is expected and is why the pack
targets visual guideline checking.

### Tests (`tests/test_build_pack.py`)

- **Windows:** 19 passed, 4 skipped (the real-importer tests need POSIX file APIs).
- **Linux:** 23 passed, including `json_zip.unpack_archive` accepting each of the four ZIPs with
  every image resolved and nothing flagged unsupported.
- **Coverage:**
  - full verification;
  - two builds byte-identical;
  - exact archive contents;
  - one field per altered case;
  - exact transformation values (decimal, no float noise);
  - image identity;
  - reader parsing;
  - answer-key structure;
  - refusal to write into or above source datasets;
  - a missing source file, changed source bytes, unexpected target values and a wrong annotation
    ID each stop the build;
  - the L01 blocker is re-evaluated if a person appears;
  - an out-of-image shift is refused;
  - edge extensions on all four edges, plus the 10 px minimum and the inside-image rule;
  - car and person IDs are taken from the source categories;
  - the importer's candidate-matching rule.

### Regression check: the rest of the repository

This task adds files only under `evaluation/annotation_error_pack/`. No application, frontend,
dataset or configuration file changed, and the Docker build context excludes `evaluation/`.

**Backend:** the full suite was run together with the pack tests on Linux:
2,618 passed, 515 skipped, 3 failed. All three failures are unrelated to this pack:

- **The two `test_json_dataset_workflow.py` COCO correction tests** fail identically on a clean
  checkout of `d8f6041`. The cause is `ModuleNotFoundError: pycocotools` in the local
  `prism-linux-venv` environment. `pycocotools==2.0.11` is listed in `requirements.txt` but is not
  installed there. This is an environment gap, not a code defect, and it also affects saving COCO
  corrections in a locally running application using that environment.
- **`test_dataset_import_jobs.py::test_emit_waits_for_ack_and_does_not_overwrite_pending_result`**
  passed on clean `main` and 5 out of 5 times on this branch when run alone. It fails only
  intermittently inside the long full run, so it is timing-sensitive.

**Frontend:** typecheck, lint, 129 of 129 tests and the production build all pass.

### Reproducibility

- `--check` passed on Windows and on Linux.
- An independent Linux build produced **byte-identical archives and answer key**.
- The 8 PNGs differed in bytes but had **0 differing pixels**. PNG compression output differs
  between the two zlib builds, even with the same Pillow version, so `--check` compares PNGs by
  decoded pixels and everything else by bytes.

### Standalone preview tool

| Archive | Overlays | Images | Objects |
|---|---|---|---|
| `coco_baseline_5` | 5 | 5 | 58 |
| `coco_errors_5` | 5 | 5 | 58 |
| `labelme_baseline_3` | 3 | 3 | 18 |
| `labelme_errors_3` | 3 | 3 | 18 |

- Diagnostic totals are identical within each pair.
- The only change in label totals is one object per format moving from person to car (C04 and L03).
- The C01 altered overlay was opened and shows the box ending at mid-shin while the polygon
  continues to the shoes.

### Application workflow actually tested: ZIP upload and import only

All four archives were uploaded through `POST /api/v1/datasets`. All four import jobs completed,
and the datasets are `ready`: 5 samples / 58 objects (COCO) and 3 samples / 18 objects (LabelMe),
with no dataset issues. Comparing the imported samples through the API:

- **Sample IDs:** identical between the paired datasets.
- **Integrity status:** identical in baseline and altered for every sample. The `invalid` samples
  (`000000001000`, `000000085157`, `000000153229`, `2011_000006`) are invalid because of the
  existing diagnostics above, not because of the edits.
- **Imported objects:** each altered dataset differs from its baseline only in the target object's
  edited field.
  - C01: box bottom 599.99 → 486.914
  - C02: box top 152.13 → 197.812
  - C03: box top 147.26 → 87.84
  - C04: label car
  - C05: polygon x + 64
  - L02: polygon y + 37.5 and its polygon-derived box
  - L03: label car
  - L01: no change
- **Floating-point display detail:** the API shows C02's unchanged bottom edge as
  `380.53999999999996` (baseline) and `380.54` (altered). This comes from the application adding
  y + height in binary floating point. The stored annotation keeps the bottom edge at exactly
  380.54.

**Not tested:** auditing, feedback, recheck, correction and export were **not** run with this pack,
and no model-based (visual guideline) check was run, so no detection results are claimed.

The test import created four datasets in the local development database ("Eval pack: ..."); no
other application data was changed.

## Hashes

### Source images (identical in the source, the baseline ZIP and the altered ZIP)

| Image | SHA-256 |
|---|---|
| `000000199055.jpg` | `b1dda03e61f7c502aaf30959faf6dd86e522372106a7389e5934f0e8b931c604` |
| `000000001000.jpg` | `24bb77a31928404e45a0454b06f6a0bd54a8db103590c9d7917288a1e0269f05` |
| `000000153229.jpg` | `6fa2b9017bab8ceb1635e1eff3ffd43225127d2149f0d465fdc5e30d41174003` |
| `000000100624.jpg` | `853ddfac5c8827aa25601a84670c8b162830421f9759ef0e21f9be980aa1ae98` |
| `000000085157.jpg` | `027adfd5c8d13be607a05da2098af34e7350b5f70a5bfd16f266e8c802a3a59b` |
| `2011_000003.jpg` | `94bc550055447e1fab3982571d7114eb4a02a868054f34cb15f95cadf3e48c9a` |
| `2011_000006.jpg` | `a0d04eb84ed3cd07282d61833d219c11fd6d98f53dc0135d4b3c720b26cfb244` |
| `2011_000025.jpg` | `5dfbdc6981c971986773e226d827f44f7497cf5ca0d4fa1556fd7575aba301ce` |

### Source annotations (LF form; equal to the upstream manifests)

| File | SHA-256 |
|---|---|
| `instances_people30.json` | `5831ea6bf37eaef78783b7425343f9ac2e614d3e9668bbcc6b307a6a830481d0` |
| `2011_000003.json` | `0aa7c87bd4f11d3ab38d63fa9bc2a11640d77c0eca2a13968e852478f63021dc` |
| `2011_000006.json` | `8f7f13768d9b25927f3dd19d317f33807b13d571d56ec801eea57798999ec9b8` |
| `2011_000025.json` | `df548de624182d99286bc3f5b0f18e287ee6d1fab00f53784b9deaf7c00870ab` |

All 48 files under `datasets/` had identical raw SHA-256 values before any work and after all
builds, previews and tests; `git status` shows no change under `datasets/` or `PennFudanPed/`.

### Outputs

| File | SHA-256 |
|---|---|
| `archives/coco_baseline_5.zip` | `70e999efe291340292a89bba70555af775d9a514c91253ac3ccbcec0cdac292b` |
| `archives/coco_errors_5.zip` | `718d7e66109b3ed409803494eb433bfbe789894aabaeddb23b7300971c7c9a22` |
| `archives/labelme_baseline_3.zip` | `3c6fd9dee53f7c69ff15b8d49315c77e26b43335b59fd7dd150d570d4ccba734` |
| `archives/labelme_errors_3.zip` | `c278412729aa75de0c1cd2eaaea4080b0820067a338354da89978e065683ac4f` |
| `answer_key.json` | `91cd8e9bcb815f80a64c5255021602c9fc0377358d62b15d54da7b981d8ec9dc` |

The archive and answer-key hashes are platform-independent. The PNG hashes depend on the zlib build.

## Visual inspection

- **Every baseline target** was inspected on a 2–3× zoomed crop of the source image with its box
  and polygon: C01–C05, L02 and L03. The assessments are in `answer_key.json`
  (`guideline_assessment`).
- **All eight comparison PNGs** were opened and checked. Each shows the baseline on the left and
  the altered version on the right, identifies the target with a coloured outline and tag, draws
  the original geometry dashed on the altered side, and states the change and its exact
  displacement. Every deliberate change is visually apparent.
- Three rendering problems were found during this check and fixed before recording:
  - the tag covered C02's moved head area;
  - the LabelMe caption named the wrong colours;
  - the blocked L01 panel was titled "deliberate error".
- **L01:** the bus cab was zoomed to confirm that no person is visible.
- **Additional context views:** the source overlays of all 30 COCO images and all 3 LabelMe images
  were generated during selection; only the candidate images were opened.

## Blocked cases, conflicts and unresolved issues

1. **L01 blocked.** No third distinct LabelMe image with a person exists among the official examples
   (`2011_000025` has two buses and a car, and no visible person). It is packaged unchanged and
   documented; no substitute was made.
2. **C04 baseline conflict (recorded, not resolved).** The person's box and polygon include the
   worn backpack, which also has its own annotation. The guidelines do not say whether worn items
   belong to the person, so guidelines 9 and 10 are uncertain for that baseline. The case tests
   guideline 4 only.
3. **C03 existing polygon diagnostics** on the target itself (repeated vertex, self-intersection)
   make guideline 10 uncertain for that target. The case tests guideline 9 only.
4. **Guideline 9 measurement limit.** Box-to-polygon gaps were measured (0.00 px for every COCO
   target), but the polygon-to-visible-person distance was judged by eye, not pixel-measured.
5. **Guideline overlap.** Guideline 7 ("no visible body part outside the box") also fails when
   guideline 5 or 6 fails; the answer key marks it as covered by the more specific guideline.
6. **C05 and guideline 11.** The displaced outline overlaps a neighbouring person; whether that
   breaks guideline 11 is left as uncertain for the reviewer.
7. **Human confirmation pending.** All visual judgements were made by the AI assistant and need a
   human reviewer's confirmation (the `reviewer` field says so).
8. **Redistribution.** The LabelMe (PASCAL VOC) image rights are unconfirmed, so the ZIPs and PNGs
   are not committed (`.gitignore`); they regenerate byte-identically with `build_pack.py`.

## Requirements not fully met

- **8 image pairs / 16 cases:** 7 of 8 pairs (14 cases) are complete; the L01 pair is blocked by
  the source data (see above).
- **Application workflow:** only upload/import was exercised; auditing, feedback, correction and
  export were not.
- **Sharing:** the archives and comparison images were not committed because image redistribution
  terms are not confirmed for all images.
