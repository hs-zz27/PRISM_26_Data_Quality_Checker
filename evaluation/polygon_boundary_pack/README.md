# Partial person-polygon boundary examples

42 deliberately altered annotations across **two source photographs**, in their native
formats: 21 COCO cases and 21 LabelMe cases. Each format has these seven combinations at
2, 4 and 6 original-image pixels:

- head only
- hand or wrist only
- foot only
- head and hand
- head and foot
- hand and foot
- head, hand and foot

These are local edits that leave most of each body part and at least 97% of the person's
original polygon area intact. They do not remove entire heads, hands or feet. Photographs
are copied byte-for-byte, never edited. This is an additional pack; the original
`evaluation/annotation_error_pack/` is unchanged.

## Upload files

Generated files are in `archives/`:

- `coco_mixed_8.zip`: seven 4-pixel combinations plus one unchanged-source control.
- `labelme_mixed_8.zip`: the equivalent eight native LabelMe samples.
- `coco_baseline_21.zip` and `coco_errors_21.zip`: 21 matched baseline/altered samples.
- `labelme_baseline_21.zip` and `labelme_errors_21.zip`: 21 matched baseline/altered samples.

Start with a mixed archive, choose its matching import format and inspect individual
samples. The larger paired archives allow comparison by case ID. The same two photos
repeat intentionally; these are **not 42 independent images or a held-out benchmark**.

Each case lives in its own folder so source filenames, annotation IDs, class labels,
image metadata and unrelated objects can remain unchanged. The COCO source box continues
to enclose the visible person; only the target segmentation and its recomputed `area`
change. LabelMe changes only the target's `points`; its displayed box is derived geometry.

## What changes and what the measurements mean

COCO uses the man with the skateboard in `000000199055.jpg`, annotation `424921`.
The edits target a hair tip, a finger on the raised hand beside his sunglasses and the
shoe on the image right. Each selected convex tip moves inward toward the chord between
its two neighbours by 2, 4 or 6 pixels. All other vertices stay untouched.

LabelMe uses `258421.jpg`, shape `/shapes/0`, the man in a white shirt and tan trousers.
Head edits clip only the topmost 2, 4 or 6 pixel strip. Wrist/cuff and shoe-toe edits move
a local convex vertex inward by that distance. His other geometry and the woman's shape
are unchanged. The hands overlap and the source polygon is coarse; the wrist case needs
particular care when reviewing its anatomical interpretation.

The depths describe **test transformations**, not approved annotation tolerances and not
a measured distance from a true human boundary. The generator checks that edits remove
polygon area, remain local and do not introduce self-intersections or reader errors.

## Review before scoring

`comparisons/PC001.png` through `PC021.png` and `PL001.png` through `PL021.png` show the
source and altered person outlines plus zoomed photo, source-outline, altered-outline and
orange removed-patch views for every edited region. The orange patch is the rasterised
annotation difference, not a body-part segmentation ground truth.

Useful starting comparisons:

- `comparisons/PC008.png`: 4-pixel hair-tip edit.
- `comparisons/PC009.png`: 4-pixel finger edit.
- `comparisons/PC010.png`: 4-pixel shoe edit.
- `comparisons/PC014.png`: all three at 4 pixels.
- `comparisons/PL014.png`: all three in LabelMe at 4 pixels.

Every new subtle case is marked **human review pending** in `answer_key.json`, with
`confirmed_result: null`. Record which missing portion is actually visible, whether the
alteration crosses it, and whether its boundary can be resolved. A 2-pixel change may be
indistinguishable from source boundary uncertainty or may merely remove source background
padding. Such a case must remain uncertain or be excluded, rather than forced to fail.
Original source annotations are controls, not a promise of pixel-exact correctness.

The earlier human approval of the LabelMe source's overall fit does not confirm any of
these new subtle edits. Keep unconfirmed expectations out of accuracy claims.

Use `guidelines.txt` as team-authored development text; it is not Samsung-provided.
Review extracted rules before auditing. Upload **only a dataset ZIP**. Keep the answer
key, previews, edit descriptions and expected results out of model prompts, uploads,
feedback examples and held-out evaluation inputs.

## Rebuild and verify

From the repository root, with the existing local source datasets and virtual environment:

```sh
.venv/bin/python -B evaluation/polygon_boundary_pack/build_pack.py
.venv/bin/python -B -m pytest evaluation/polygon_boundary_pack/tests -q -p no:cacheprovider
```

No models, services, downloads or extra dependencies are needed. The builder validates
source hashes and refuses to write into source folders or the existing evaluation pack.
`verification.json` records generated archive hashes and generation checks. Tests also
exercise the real ZIP importer and source/geometry preservation. They establish dataset
integrity and upload compatibility, not model detection accuracy or UI behavior.

## Source attribution and local outputs

COCO uses the existing COCO 2017 validation subset. Its original image record, `flickr_url`,
licence ID 4 (CC BY 2.0), category definitions and licence records remain in every COCO JSON.
See `datasets/coco_people_30/download_manifest.json` and `datasets/README.md`.

LabelMe uses Christian Mills' original annotation of Pexels photo 258421, pinned by the
existing downloader. Preserve the separate photo and annotation attribution and limitations
in `datasets/labelme_l01_example/README.md` and its download manifest.

Generated ZIPs, comparisons and answer keys stay local and are ignored by Git, consistent
with the existing evaluation pack. No source images are added to version control.
