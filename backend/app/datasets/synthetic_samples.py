"""SYNTHETIC COCO and LabelMe fixtures for reader development and demos.

Everything written here is invented. These files demonstrate that the readers handle
documented format features and problem cases. They are NOT public datasets, NOT Samsung
data, and prove nothing about compatibility with real files. Each document carries a
synthetic marker so the demo can print it.

Problem cases are included on purpose so that diagnostics can be seen; none of them is
presented as a measured error rate.
"""

import json
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = (160, 120)
COCO_MARKER = "SYNTHETIC fixture: invented for reader tests; not a real dataset."


def _canvas(boxes=(), polygons=()):
    image = Image.new("RGB", SIZE, (235, 235, 235))
    draw = ImageDraw.Draw(image)
    for left, top, right, bottom in boxes:
        draw.rectangle((left, top, right, bottom), fill=(150, 190, 235))
    for points in polygons:
        draw.polygon(points, fill=(240, 190, 140))
    draw.text((3, 2), "SYNTHETIC", fill=(90, 90, 90))
    return image


def _write_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def coco_document() -> dict:
    """The COCO fixture as decoded JSON (also used directly by tests)."""
    def image(identifier, name, width=160, height=120):
        return {"id": identifier, "file_name": name, "width": width, "height": height}

    return {
        "info": {"description": COCO_MARKER},
        "categories": [{"id": 1, "name": "widget"}, {"id": 2, "name": "gadget"}],
        "images": [
            image(1, "box_only.png"), image(2, "polygon_only.png"), image(3, "multipart.png"),
            image(4, "rle_retained.png"), image(5, "bad_geometry.png"),
            image(6, "missing_file.png"), image(7, "dimension_mismatch.png", 200, 100),
            image(8, "malformed_box.png"), image(9, "empty.png"), image(10, "empty.png"),
        ],
        "annotations": [
            # 1: box only. No polygon and no mask were ever supplied.
            {"id": 1, "image_id": 1, "category_id": 1, "bbox": [20, 20, 50, 40], "iscrowd": 0},
            # 2: polygon only. The box shown by the reader is derived, not supplied.
            {"id": 2, "image_id": 2, "category_id": 1, "iscrowd": 0,
             "segmentation": [[90.5, 20, 140, 30, 120.25, 80]]},
            # 3: two polygon parts plus fractional box; a second object reuses id 3.
            {"id": 3, "image_id": 3, "category_id": 1, "bbox": [10.5, 60, 80, 40], "iscrowd": 0,
             "segmentation": [[10.5, 60, 50, 60, 50, 100, 10.5, 100], [70, 70, 90.5, 70, 90.5, 100, 70, 100]]},
            {"id": 3, "image_id": 3, "category_id": 2, "bbox": [100, 10, 20, 20], "iscrowd": 0},
            # 4: RLE is retained but not decoded (deferred).
            {"id": 4, "image_id": 4, "category_id": 1, "bbox": [30, 30, 40, 40], "iscrowd": 1,
             "segmentation": {"counts": [1000, 40, 18160], "size": [120, 160]}},
            # 5: readable but geometrically suspicious annotations; neither is changed.
            {"id": 5, "image_id": 5, "category_id": 1, "bbox": [10, 10, 50, 50], "iscrowd": 0,
             "segmentation": [[10, 10, 60, 60, 60, 10, 10, 60]]},
            {"id": 6, "image_id": 5, "category_id": 2, "bbox": [140, 100, 40, 30], "iscrowd": 0},
            # 6: the image file is referenced but absent.
            {"id": 7, "image_id": 6, "category_id": 1, "bbox": [5, 5, 20, 20], "iscrowd": 0},
            # 7: declared size disagrees with the real image.
            {"id": 8, "image_id": 7, "category_id": 1, "bbox": [10, 10, 30, 30], "iscrowd": 0},
            # unresolved image reference: kept as an unassigned annotation.
            {"id": 9, "image_id": 999, "category_id": 1, "bbox": [1, 1, 5, 5], "iscrowd": 0},
            # 8: malformed supplied box; the valid polygon is NOT used to replace it.
            {"id": 10, "image_id": 8, "category_id": 1, "bbox": [1, 2, 3], "iscrowd": 0,
             "segmentation": [[40, 40, 80, 40, 80, 80, 40, 80]]},
        ],
    }


def labelme_documents() -> dict[str, dict | str]:
    """File stem -> decoded document (or raw text for the deliberately unreadable file)."""
    def base(path, shapes, width=160, height=120):
        return {"version": "synthetic", "flags": {"synthetic": True}, "shapes": shapes,
                "imagePath": path, "imageData": None, "imageWidth": width, "imageHeight": height}

    def shape(label, kind, points, group=None):
        return {"label": label, "points": points, "group_id": group, "shape_type": kind, "flags": {}}

    return {
        "lm_clean": base("lm_clean.png", [
            shape("widget", "polygon", [[10, 10], [60, 10], [60, 50], [10, 50]], group=1),
            shape("widget", "polygon", [[70, 20], [90, 20], [90, 40]], group=1),
            shape("gadget", "rectangle", [[140, 100], [100, 70]]),  # drawn right-to-left
        ]),
        "lm_unsupported_shapes": base("lm_unsupported_shapes.png", [
            shape("widget", "circle", [[50, 50], [70, 50]]),
            shape("widget", "point", [[20, 20]]),
            {"label": "widget", "points": [[1, 1], [2, 2], [3, 1]]},  # no shape_type
            shape("gadget", "polygon", [[100, 60], [140, 60], [120, 100]]),
        ]),
        "lm_missing_image": base("does_not_exist.png", [
            shape("widget", "rectangle", [[10, 10], [40, 40]])]),
        "lm_dimension_mismatch": base("lm_dimension_mismatch.png", [
            shape("widget", "polygon", [[150, 100], [200, 100], [200, 150]])], 320, 240),
        "lm_unreadable": '{"shapes": [',
    }


def write_synthetic_dataset(directory: str | Path) -> dict[str, Path]:
    """Write the fixtures under ``directory`` (must not already contain files)."""
    root = Path(directory)
    if root.exists() and any(root.iterdir()):
        raise FileExistsError("Refusing to write synthetic fixtures into a non-empty directory.")
    coco_images, labelme = root / "coco" / "images", root / "labelme"
    coco_images.mkdir(parents=True), labelme.mkdir(parents=True)

    _canvas(boxes=[(20, 20, 70, 60)]).save(coco_images / "box_only.png")
    _canvas(polygons=[[(90.5, 20), (140, 30), (120.25, 80)]]).save(coco_images / "polygon_only.png")
    _canvas(polygons=[[(10.5, 60), (50, 60), (50, 100), (10.5, 100)], [(70, 70), (90.5, 70), (90.5, 100), (70, 100)]],
            boxes=[(100, 10, 120, 30)]).save(coco_images / "multipart.png")
    _canvas(boxes=[(30, 30, 70, 70)]).save(coco_images / "rle_retained.png")
    _canvas(boxes=[(10, 10, 60, 60)]).save(coco_images / "bad_geometry.png")
    # missing_file.png is intentionally never written.
    _canvas(boxes=[(10, 10, 40, 40)]).save(coco_images / "dimension_mismatch.png")
    _canvas(boxes=[(40, 40, 80, 80)]).save(coco_images / "malformed_box.png")
    _canvas().save(coco_images / "empty.png")
    coco_path = root / "coco" / "instances_synthetic.json"
    _write_json(coco_path, coco_document())

    pictures = {
        "lm_clean": _canvas(boxes=[(100, 70, 140, 100)], polygons=[[(10, 10), (60, 10), (60, 50), (10, 50)],
                                                                  [(70, 20), (90, 20), (90, 40)]]),
        "lm_unsupported_shapes": _canvas(polygons=[[(100, 60), (140, 60), (120, 100)]]),
        "lm_dimension_mismatch": _canvas(),
    }
    for stem, picture in pictures.items():
        picture.save(labelme / f"{stem}.png")
    for stem, document in labelme_documents().items():
        target = labelme / f"{stem}.json"
        if isinstance(document, str):
            target.write_text(document, encoding="utf-8")
        else:
            _write_json(target, document)

    (root / "README_SYNTHETIC.txt").write_text(
        "All files here are SYNTHETIC test fixtures written by synthetic_samples.py.\n"
        "They are not public datasets and not Samsung data. Problem cases are deliberate.\n",
        encoding="utf-8")
    return {"root": root, "coco_json": coco_path, "coco_images": coco_images, "labelme_dir": labelme}
