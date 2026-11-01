# L01 source pair

`258421.jpg` and `258421.json` are unmodified members of Christian Mills'
[LabelMe instance-segmentation tutorial dataset](https://huggingface.co/datasets/cj-mills/labelme-instance-segmentation-toy-dataset).
The [author's tutorial](https://christianjmills.com/posts/torchvision-labelme-annotation-tutorials/segmentation-polygons/index.html)
identifies the photos as Pexels images and the annotations as his LabelMe work.
This is an existing annotation, not a COCO conversion or a newly drawn polygon.

The source image is 768 × 1152 pixels with two person polygons. L01 targets
`/shapes/0`, the man on the right: 27 vertices, `group_id: null`. The altered
copy shifts this polygon right by 76.8 pixels. Sources remain unchanged.

## Download

From the repository root, run:

```sh
.venv/bin/python -B evaluation/annotation_error_pack/tools/download_l01_source.py
```

The downloader fetches the pinned archive, checks its SHA-256, reads only the
two named members, checks their hashes and refuses to replace different existing
files. Exact archive URL, revision, member paths and hashes are in
`download_manifest.json`. The archive is the upstream distribution; independent
direct image/annotation download URLs are not published by that repository.

## Rights and attribution

The dataset card declares MIT for the dataset. That is not a replacement for the
[Pexels photo licence](https://www.pexels.com/license/), which permits downloading,
use and modification subject to its restrictions, including no implied endorsement,
offensive portrayal or redistribution as stock imagery. Credit Pexels and
Christian Mills' annotation dataset. Photographer identity was not independently
verified: the photo page for ID 258421 returned HTTP 403 during retrieval.

Use this pair for local annotation evaluation; the deliberate error is in the
annotation, not a statement about the depicted people. Original source files,
ZIPs and overlays remain ignored by Git. No permission for unrestricted
redistribution or model training is asserted here.

Human evaluation approval is recorded separately in
`evaluation/annotation_error_pack/human_review.md`; acquisition does not imply
that the visual answer key has been human-confirmed.
