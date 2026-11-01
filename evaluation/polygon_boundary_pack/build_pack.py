#!/usr/bin/env python3
"""Generate native COCO/LabelMe partial-boundary errors, without editing photos.

Run from the repository root with the repository virtual environment. No model,
database, service, download or new dependency is needed. Outputs contain source
photos and stay local. The source records are pinned to the existing dataset.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import itertools
import json
import math
from pathlib import Path
import sys
import zipfile

from PIL import Image, ImageChops, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.app.datasets.annotation_validation import _polygon_issues
from backend.app.datasets.coco import parse_coco
from backend.app.datasets.labelme import parse_labelme
from backend.app.models.annotation_validation import ValidationLimits

DEPTHS = (2, 4, 6)
PARTS = ("head", "hand", "foot")
COMBINATIONS = tuple(c for n in (1, 2, 3) for c in itertools.combinations(PARTS, n))
ZIP_DATE = (1980, 1, 1, 0, 0, 0)
GREEN, RED, GREY = (30, 210, 100), (255, 65, 65), (170, 170, 170)
SOURCES = (
    {
        "format": "coco", "prefix": "PC", "target_id": 424921,
        "annotation": "datasets/coco_people_30/annotations/instances_people30.json",
        "annotation_sha256_lf": "5831ea6bf37eaef78783b7425343f9ac2e614d3e9668bbcc6b307a6a830481d0",
        "image": "datasets/coco_people_30/images/000000199055.jpg",
        "image_sha256": "b1dda03e61f7c502aaf30959faf6dd86e522372106a7389e5934f0e8b931c604",
        "target_description": "man with red vest and skateboard",
        "landmarks": {"head": (0, 16), "hand": (0, 21), "foot": (1, 2)},
        "region_description": {"head": "a small tip of hair at the crown",
                               "hand": "part of a finger on the raised hand beside the sunglasses",
                               "foot": "a small part of the lower shoe on the image right"},
        "source_notice": "COCO 2017 validation; image licence 4 (CC BY 2.0). Preserve the image flickr_url and source licence metadata.",
    },
    {
        "format": "labelme", "prefix": "PL", "target_index": 0,
        "annotation": "datasets/labelme_l01_example/258421.json",
        "annotation_sha256_lf": "6dff908bc14e61aee78aef2a9e83a027027f2635c6920efa14de14b0efa7268a",
        "image": "datasets/labelme_l01_example/258421.jpg",
        "image_sha256": "fffae741a0fd8453cf199338cf1988ad992fb38208b37aac28808e25244572a8",
        "target_description": "man in white shirt and tan trousers on the image right",
        "landmarks": {"head": (0, 17), "hand": (0, 10), "foot": (0, 1)},
        "clip_head_top": True,
        "region_description": {"head": "a narrow strip at the top of the hair",
                               "hand": "a wrist/cuff patch beside the overlapping hands; review the boundary",
                               "foot": "a small part of the shoe toe at the bottom left of the man's outline"},
        "source_notice": "Christian Mills' native LabelMe tutorial dataset, Pexels photo 258421. Preserve the separate annotation/photo attribution and source notices in datasets/labelme_l01_example/README.md; generated photos stay local.",
    },
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()


def area(points):
    return abs(sum(a[0] * b[1] - b[0] * a[1]
                   for a, b in zip(points, points[1:] + points[:1]))) / 2


def cross(a, b, p):
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def inset_tip(points, index, depth):
    """Move one convex tip toward its adjacent chord by an exact Euclidean depth.

    The moved point must remain strictly inside the original local triangle.
    This replaces the tip with a shallower tip; it cannot expand that triangle
    or remove a whole body part. Final polygons are also checked for crossings.
    """
    before, point, after = points[index - 1], points[index], points[(index + 1) % len(points)]
    dx, dy = after[0] - before[0], after[1] - before[1]
    length = math.hypot(dx, dy)
    if not length or depth <= 0:
        raise ValueError("Expected a nonzero chord and positive depth.")
    signed_distance = cross(before, after, point) / length
    if depth >= abs(signed_distance):
        raise ValueError("Inset would cross the local chord; choose a smaller depth.")
    direction = -1 if signed_distance > 0 else 1
    moved = [point[0] + direction * (-dy / length) * depth,
             point[1] + direction * (dx / length) * depth]
    signs = [cross(a, b, moved) for a, b in ((before, point), (point, after), (after, before))]
    if not (all(s > 0 for s in signs) or all(s < 0 for s in signs)):
        raise ValueError("Inset must remain strictly inside its original local triangle.")
    result = copy.deepcopy(points)
    result[index] = moved
    removed = area(points) - area(result)
    if removed <= 0:
        raise ValueError("Selected point is not a removable convex tip.")
    return result, {"vertex_index": index, "before": list(point), "after": moved,
                    "vertex_displacement_px": depth, "removed_area_px2": removed,
                    "adjacent_vertices": [list(before), list(after)]}


def source_input(spec):
    raw = (REPO / spec["annotation"]).read_bytes()
    photo = (REPO / spec["image"]).read_bytes()
    if sha(raw.replace(b"\r\n", b"\n")) != spec["annotation_sha256_lf"] or sha(photo) != spec["image_sha256"]:
        raise ValueError(f"Pinned source changed: {spec['annotation']}")
    source = json.loads(raw)
    if spec["format"] == "coco":
        target = next(a for a in source["annotations"] if a["id"] == spec["target_id"])
        image_id = target["image_id"]
        source["images"] = [i for i in source["images"] if i["id"] == image_id]
        source["annotations"] = [a for a in source["annotations"] if a["image_id"] == image_id]
    return source, photo


def target(document, spec):
    if spec["format"] == "coco":
        return next(a for a in document["annotations"] if a["id"] == spec["target_id"])
    return document["shapes"][spec["target_index"]]


def parts_of(record, format):
    if format == "coco":
        return [[list(p) for p in zip(part[::2], part[1::2])] for part in record["segmentation"]]
    return [copy.deepcopy(record["points"])]


def clip_top(points, depth):
    """Intersect the polygon with y >= min_y + depth; preserve the rest."""
    cut = min(p[1] for p in points) + depth
    result = []
    for a, b in zip(points[-1:] + points[:-1], points):
        a_in, b_in = a[1] >= cut, b[1] >= cut
        if a_in != b_in:
            t = (cut - a[1]) / (b[1] - a[1])
            result.append([a[0] + t * (b[0] - a[0]), cut])
        if b_in:
            result.append(list(b))
    return result, cut


def altered_copy(document, spec, regions, depth):
    altered = copy.deepcopy(document)
    original_parts = parts_of(target(document, spec), spec["format"])
    parts = copy.deepcopy(original_parts)
    edits = []
    for region in regions:
        part_index, vertex = spec["landmarks"][region]
        if region == "head" and spec.get("clip_head_top"):
            parts[part_index], cut = clip_top(parts[part_index], depth)
            point = original_parts[part_index][vertex]
            edit = {"operation": "clip_narrow_top_strip", "depth_px": depth, "cut_line_y": cut,
                    "before": list(point), "after": [point[0], cut],
                    "removed_area_px2": area(original_parts[part_index]) - area(parts[part_index])}
        else:
            changed, edit = inset_tip(original_parts[part_index], vertex, depth)
            # A prior head clip can insert/delete vertices. Find this untouched
            # source landmark by its exact coordinates rather than a stale index.
            current = parts[part_index].index(original_parts[part_index][vertex])
            parts[part_index][current] = changed[vertex]
            edit.update(operation="inset_convex_vertex", depth_px=depth)
        edit.update(region=region, part_index=part_index, description=spec["region_description"][region])
        edits.append(edit)
    obj = target(altered, spec)
    if spec["format"] == "coco":
        obj["segmentation"] = [[v for p in points for v in p] for points in parts]
        # A segmentation change also changes its area; the source box still
        # describes the same visible person, so it deliberately stays unchanged.
        obj["area"] = sum(area(p) for p in parts)
    else:
        obj["points"] = parts[0]
    removed = sum(area(p) for p in original_parts) - sum(area(p) for p in parts)
    original_area = sum(area(p) for p in original_parts)
    if not 0 < removed < original_area * 0.03:
        raise ValueError("These cases must remove less than 3% of the person's source polygon area.")
    # Check original and altered geometry independently. No broken polygon is
    # allowed to stand in for a small semantic annotation-fit error.
    for points in original_parts + parts:
        if findings := _polygon_issues([tuple(p) for p in points], "polygon", ValidationLimits()):
            raise ValueError(f"Polygon is not structurally clean: {findings}")
    return altered, edits, removed / original_area * 100


def archive_members(case_id, document, photo, spec):
    name = Path(spec["image"]).name
    if spec["format"] == "coco":
        return {f"{case_id}/annotations/instances.json": encode(document), f"{case_id}/images/{name}": photo}
    return {f"{case_id}/{Path(spec['annotation']).name}": encode(document), f"{case_id}/{name}": photo}


def write_zip(path, members):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(members.items()):
            info = zipfile.ZipInfo(name, ZIP_DATE)
            info.create_system, info.external_attr = 3, 0o100644 << 16
            archive.writestr(info, data)


def render_panel(photo, polygons, bounds, size, color):
    crop = photo.crop(bounds)
    scale = min(size[0] / crop.width, size[1] / crop.height)
    scaled = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.Resampling.NEAREST)
    canvas = Image.new("RGB", size, (24, 30, 38))
    left, top = (size[0] - scaled.width) // 2, (size[1] - scaled.height) // 2
    canvas.paste(scaled, (left, top))
    draw = ImageDraw.Draw(canvas)
    for points in polygons:
        mapped = [(left + (x - bounds[0]) * scale, top + (y - bounds[1]) * scale) for x, y in points]
        draw.line(mapped + [mapped[0]], fill=color, width=2)
    return canvas


def compare(path, case_id, photo_bytes, original, altered, spec, edits, removed_pct):
    with Image.open(io.BytesIO(photo_bytes)) as opened:
        photo = opened.convert("RGB")
    base = parts_of(target(original, spec), spec["format"])
    changed = parts_of(target(altered, spec), spec["format"])
    points = [p for part in base for p in part]
    bounds = (max(0, math.floor(min(p[0] for p in points) - 12)),
              max(0, math.floor(min(p[1] for p in points) - 12)),
              min(photo.width, math.ceil(max(p[0] for p in points) + 12)),
              min(photo.height, math.ceil(max(p[1] for p in points) + 12)))
    width, overview_h, row_h = 1064, 420, 284
    canvas = Image.new("RGB", (width, 530 + len(edits) * row_h + 55), (245, 247, 250))
    draw = ImageDraw.Draw(canvas)
    font, small = ImageFont.load_default(size=20), ImageFont.load_default(size=15)
    depth = edits[0]["depth_px"]
    draw.text((20, 12), f"{case_id} | {' + '.join(e['region'] for e in edits)} | {depth} px local inset", fill="black", font=font)
    draw.text((20, 40), "Synthetic annotation edit; photo unchanged. New small-error expectations need human review.", fill="black", font=small)
    for x, data, color, label in ((20, base, GREEN, "SOURCE BASELINE"), (542, changed, RED, "ALTERED POLYGON")):
        draw.text((x, 68), label, fill=color, font=font)
        canvas.paste(render_panel(photo, data, bounds, (502, overview_h), color), (x, 98))
    for row, edit in enumerate(edits):
        y = 530 + row * row_h
        x0, y0 = edit["before"]
        radius = 23
        crop = (max(0, math.floor(x0 - radius)), max(0, math.floor(y0 - radius)),
                min(photo.width, math.ceil(x0 + radius)), min(photo.height, math.ceil(y0 + radius)))
        labels = ("PHOTO ONLY", "SOURCE OUTLINE", "ALTERED OUTLINE", "REMOVED PATCH (ORANGE)")
        for column, label in enumerate(labels):
            draw.text((20 + column * 261, y + 30), label, fill="black", font=small)
        draw.text((20, y), f"{edit['region'].upper()}: {edit['description']}", fill="black", font=small)
        for col, data, color in ((0, [], GREY), (1, base, GREEN), (2, changed, RED)):
            canvas.paste(render_panel(photo, data, crop, (240, 210), color), (20 + col * 261, y + 54))
        # Rasterised, magnified review aid; continuous area measurements above
        # remain separate. Neither this tint nor any overlay is in upload ZIPs.
        patch = render_panel(photo, [], crop, (240, 210), RED)
        scale = min(240 / (crop[2] - crop[0]), 210 / (crop[3] - crop[1]))
        ox, oy = (240 - round((crop[2] - crop[0]) * scale)) // 2, (210 - round((crop[3] - crop[1]) * scale)) // 2
        mapped = lambda p: (ox + (p[0] - crop[0]) * scale, oy + (p[1] - crop[1]) * scale)
        masks = []
        for polygons in (base, changed):
            mask = Image.new("L", patch.size)
            md = ImageDraw.Draw(mask)
            for polygon in polygons:
                md.polygon([mapped(p) for p in polygon], fill=255)
            masks.append(mask)
        removed = ImageChops.subtract(*masks)
        patch.paste((255, 130, 20), mask=removed)
        canvas.paste(patch, (803, y + 54))
    draw.text((20, canvas.height - 42), f"Source polygon area removed: {removed_pct:.4f}%. Insets are test edits, not acceptable-error tolerances.", fill="black", font=small)
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path)


def build(out=HERE):
    out = Path(out).resolve()
    for protected in (REPO / "datasets", REPO / "PennFudanPed", REPO / "evaluation" / "annotation_error_pack"):
        if out == protected or out.is_relative_to(protected) or protected.is_relative_to(out):
            raise ValueError(f"Output must not overwrite sources or the existing pack: {out}")
    out.mkdir(parents=True, exist_ok=True)
    source_hashes = {str(REPO / s[k]): sha((REPO / s[k]).read_bytes()) for s in SOURCES for k in ("annotation", "image")}
    entries, archives = [], []
    for spec in SOURCES:
        original, photo = source_input(spec)
        parser = parse_coco if spec["format"] == "coco" else parse_labelme
        if parser(original).all_issues:
            raise ValueError("Selected source has reader diagnostics.")
        groups = {name: {} for name in ("baseline_21", "errors_21", "mixed_8")}
        for index, (depth, regions) in enumerate(itertools.product(DEPTHS, COMBINATIONS), start=1):
            case_id = f"{spec['prefix']}{index:03}"
            altered, edits, percentage = altered_copy(original, spec, regions, depth)
            if parser(altered).all_issues:
                raise ValueError(f"New reader diagnostics: {case_id}")
            groups["baseline_21"].update(archive_members(case_id, original, photo, spec))
            groups["errors_21"].update(archive_members(case_id, altered, photo, spec))
            if depth == 4:
                groups["mixed_8"].update(archive_members(case_id, altered, photo, spec))
            entry = {"case_id": case_id, "format": spec["format"], "regions": list(regions),
                     "source_annotation": spec["annotation"], "source_image": spec["image"],
                     "source_image_sha256": spec["image_sha256"],
                     "target": spec.get("target_id", f"/shapes/{spec.get('target_index')}"),
                     "target_description": spec["target_description"], "edits": edits,
                     "removed_polygon_area_percent": percentage,
                     "intended_result": "suspected polygon-fit failure; use uncertain if the boundary cannot be resolved",
                     "human_review_status": "pending", "confirmed_result": None,
                     "baseline_status": "source unchanged; not certified pixel-exact",
                     "whole_image_reviewed": False,
                     "comparison": f"comparisons/{case_id}.png"}
            if spec["format"] == "coco":
                entry["related_change"] = "Target area recomputed from altered polygons; supplied bbox and other objects unchanged."
            else:
                entry["related_change"] = "No supplied box; any derived box follows the altered polygon. Other shapes unchanged."
            entries.append(entry)
            compare(out / entry["comparison"], case_id, photo, original, altered, spec, edits, percentage)
        control_id = spec["prefix"] + "000"
        groups["mixed_8"].update(archive_members(control_id, original, photo, spec))
        entries.append({"case_id": control_id, "format": spec["format"], "regions": [],
                        "control": True, "source_annotation": spec["annotation"], "source_image": spec["image"],
                        "intended_result": "unchanged-source control; examine for false alarms, not certified pixel-exact",
                        "human_review_status": "pending", "confirmed_result": None})
        for group, members in groups.items():
            name = f"{spec['format']}_{group}.zip"
            write_zip(out / "archives" / name, members)
            archives.append({"file": f"archives/{name}", "format": spec["format"],
                             "samples": 8 if group == "mixed_8" else 21,
                             "sha256": sha((out / "archives" / name).read_bytes())})
    key = {"description": "42 partial-boundary edits, two unique source photos, plus two unchanged mixed-upload controls.",
           "depth_units": "Original-image pixels: Euclidean convex-vertex inset, or vertical top-strip depth for LabelMe heads; not a semantic tolerance",
           "human_review_status": "pending for all new subtle cases", "source_notices": [s["source_notice"] for s in SOURCES],
           "evaluation_limit": "Repeated-photo paired stress tests. Not 42 independent examples, not a held-out accuracy benchmark. Do not send this key, edit labels or comparisons to the model.",
           "cases": entries, "archives": archives}
    (out / "answer_key.json").write_bytes(encode(key))
    if any(sha(Path(path).read_bytes()) != checksum for path, checksum in source_hashes.items()):
        raise ValueError("Source files changed during the build.")
    report = {"altered_cases": 42, "unique_source_photos": 2, "comparisons": 42,
              "archives": archives, "source_files_unchanged": True,
              "structural_reader_and_polygon_checks": "passed", "model_inference_run": False,
              "human_review_status": "pending"}
    (out / "verification.json").write_bytes(encode(report))
    print(json.dumps(report, indent=2))
    return key


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=HERE)
    build(parser.parse_args().out)
