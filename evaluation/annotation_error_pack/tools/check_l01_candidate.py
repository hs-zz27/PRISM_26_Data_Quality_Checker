#!/usr/bin/env python3
"""Check one candidate image + LabelMe JSON for evaluation case L01. Read-only.

Run from the repository root:

    python -B evaluation/annotation_error_pack/tools/check_l01_candidate.py \
        --image path/to/photo.jpg --annotation path/to/photo.json --out candidate_check

It tests every machine-checkable requirement and prints the values to record in the source manifest.
It does NOT decide image rights, whether the person is clearly identifiable, or whether the shifted
polygon "visibly" misaligns: those need a human (it writes an overlay for that, and reports an
overlap number only as a hint). Nothing is modified; the overlay is written with a name that never
replaces an existing file. Exit code 0 means every machine check passed, 1 means one failed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path.cwd()))

import numpy as np                       # noqa: E402
from PIL import Image, ImageDraw          # noqa: E402

from backend.app.datasets.labelme import read_labelme   # noqa: E402

# Images already used by the pack (pinned in build_pack.py IMAGE_SHA256); L01 must differ from all of them.
USED_IMAGE_SHA256 = {
    "2011_000003.jpg": "94bc550055447e1fab3982571d7114eb4a02a868054f34cb15f95cadf3e48c9a",
    "2011_000006.jpg": "a0d04eb84ed3cd07282d61833d219c11fd6d98f53dc0135d4b3c720b26cfb244",
    "2011_000025.jpg": "5dfbdc6981c971986773e226d827f44f7497cf5ca0d4fa1556fd7575aba301ce",
}
FRACTION = Decimal("0.1")
OVERLAP_HINT = 0.6          # hint only: IoU of original vs shifted polygon at or below this suggests visible misalignment
MIN_AREA_FRACTION = 0.01    # hint only: a polygon smaller than 1% of the image is hard to identify as a person


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dec(value) -> Decimal:
    return Decimal(repr(value)) if isinstance(value, float) else Decimal(value)


def num(value: Decimal):
    return int(value) if value == value.to_integral_value() else float(value)


class Report:
    def __init__(self):
        self.checks = []

    def add(self, name, ok, detail=""):
        self.checks.append({"check": name, "ok": bool(ok), "detail": detail})
        return ok

    @property
    def passed(self):
        return all(c["ok"] for c in self.checks)


def mask(points, size, scale):
    image = Image.new("L", (max(1, round(size[0] * scale)), max(1, round(size[1] * scale))), 0)
    ImageDraw.Draw(image).polygon([(float(x) * scale, float(y) * scale) for x, y in points], fill=1)
    return np.asarray(image, dtype=bool)


def overlap_ratio(before, after, size):
    scale = min(1.0, 1000 / max(size))
    a, b = mask(before, size, scale), mask(after, size, scale)
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 1.0


def unique_path(path: Path) -> Path:
    for n in range(1, 1000):
        candidate = path if n == 1 else path.with_name(f"{path.stem}-{n}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise OSError("no unused file name")


def check(image_path: Path, annotation_path: Path, label: str = "person", out_dir: Path | None = None) -> dict:
    report = Report()
    image_bytes, annotation_bytes = image_path.read_bytes(), annotation_path.read_bytes()
    info = {"image_sha256": sha256(image_bytes), "annotation_sha256_raw": sha256(annotation_bytes),
            "annotation_sha256_lf": sha256(annotation_bytes.replace(b"\r\n", b"\n")),
            "image_bytes": len(image_bytes), "annotation_bytes": len(annotation_bytes)}

    used = [n for n, h in USED_IMAGE_SHA256.items() if h == info["image_sha256"]]
    report.add("different from the three existing LabelMe images", not used and
               image_path.name not in USED_IMAGE_SHA256,
               f"same bytes or name as {used or image_path.name}" if used or image_path.name in USED_IMAGE_SHA256 else "")

    try:
        with Image.open(image_path) as picture:
            picture.load()
            fmt, size, orientation = picture.format, picture.size, picture.getexif().get(274, 1)
    except Exception as error:                                           # noqa: BLE001 - reported, not hidden
        report.add("image decodes", False, f"{type(error).__name__}")
        return {"passed": False, "checks": report.checks, "info": info}
    info.update({"image_format": fmt, "image_size": list(size)})
    report.add("image is an external PNG or JPEG file", fmt in ("PNG", "JPEG"), f"format {fmt}")
    report.add("image has no EXIF rotation (the readers do not map rotated coordinates)", orientation == 1,
               f"EXIF orientation {orientation}")

    try:
        raw = json.loads(annotation_bytes)
    except ValueError as error:
        report.add("annotation is valid JSON", False, str(error)[:120])
        return {"passed": False, "checks": report.checks, "info": info}
    report.add("annotation does not embed the image (imageData empty)", not raw.get("imageData"),
               "imageData is present" if raw.get("imageData") else "")
    declared = (raw.get("imageWidth"), raw.get("imageHeight"))
    report.add("declared image size equals decoded size", declared == tuple(size), f"declared {declared}, decoded {tuple(size)}")
    reference = raw.get("imagePath")
    info["imagePath"] = reference
    report.add("imagePath names this image file", reference == image_path.name,
               f"imagePath {reference!r} vs file {image_path.name!r} (identical rebasing of baseline and altered would be needed)"
               if reference != image_path.name else "")

    document = read_labelme(annotation_path)
    readable = [i for i in document.all_issues if i.severity == "error" and "polygon" not in i.code and "box" not in i.code]
    report.add("existing LabelMe reader reads the file without structural errors", not readable,
               "; ".join(f"{i.code} {i.location}" for i in readable)[:200])
    info["reader_issue_codes"] = sorted({i.code for i in document.all_issues})

    width, height = dec(size[0]), dec(size[1])
    delta = width * FRACTION
    info["shift_pixels"] = num(delta)
    candidates = []
    for index, obj in enumerate(document.images[0].annotations if document.images else []):
        source = raw["shapes"][index]
        if source.get("label") != label:
            continue
        points = [[dec(x), dec(y)] for x, y in source.get("points", [])] if source.get("shape_type") == "polygon" else []
        supported = source.get("shape_type") == "polygon" and len(points) >= 3 and bool(obj.polygons)
        inside = supported and all(Decimal(0) <= x <= width and Decimal(0) <= y <= height for x, y in points)
        right = inside and all(x + delta <= width for x, _ in points)
        left = inside and all(x - delta >= 0 for x, _ in points)
        entry = {"shape": index, "pointer": f"/shapes/{index}", "shape_type": source.get("shape_type"),
                 "group_id": source.get("group_id"), "vertices": len(points), "supported_polygon": supported,
                 "all_points_inside_image": inside, "shift_right_feasible": right, "shift_left_feasible": left,
                 "reader_issues": [i.code for i in obj.issues]}
        if supported and (right or left):
            direction = 1 if right else -1
            shifted = [[x + direction * delta, y] for x, y in points]
            entry["direction"] = "right" if right else "left"
            entry["polygon_area_fraction"] = round(float(
                mask([(float(x), float(y)) for x, y in points], size, min(1.0, 1000 / max(size))).sum()
                / (round(size[0] * min(1.0, 1000 / max(size))) * round(size[1] * min(1.0, 1000 / max(size))))), 4)
            entry["overlap_before_after"] = round(overlap_ratio(points, shifted, size), 3)
            entry["shifted_points"] = [[num(x), num(y)] for x, y in shifted]
            entry["original_points"] = [[num(x), num(y)] for x, y in points]
        candidates.append(entry)
    info["person_shapes"] = candidates
    report.add(f"at least one shape labelled {label!r}", bool(candidates), f"{len(candidates)} found")
    usable = [c for c in candidates if c["supported_polygon"] and (c["shift_right_feasible"] or c["shift_left_feasible"])]
    report.add("a polygon the reader supports can be shifted 10% of the width with every vertex inside the image",
               bool(usable), "; ".join(f"{c['pointer']}: inside={c['all_points_inside_image']} "
                                       f"right={c['shift_right_feasible']} left={c['shift_left_feasible']}" for c in candidates)[:300])
    hints = []
    for c in usable:
        c["hint_visibly_misaligned"] = c["overlap_before_after"] <= OVERLAP_HINT
        c["hint_large_enough"] = c.get("polygon_area_fraction", 0) >= MIN_AREA_FRACTION
        hints.append(c)
    info["usable_shapes"] = [c["pointer"] for c in usable]

    if out_dir is not None and usable:
        out_dir.mkdir(parents=True, exist_ok=True)
        with Image.open(image_path) as picture:
            base = picture.convert("RGB")
        sheet = Image.new("RGB", (base.width * 2 + 20, base.height), (255, 255, 255))
        for slot, key in ((0, "original_points"), (1, "shifted_points")):
            tile = base.copy()
            draw = ImageDraw.Draw(tile)
            for c in usable[:1]:
                pts = [tuple(p) for p in c[key]]
                draw.line(pts + [pts[0]], fill=(255, 0, 0), width=max(2, base.width // 250))
            sheet.paste(tile, (slot * (base.width + 20), 0))
        target = unique_path(out_dir / "l01_candidate_check.png")
        sheet.save(target, format="PNG")
        info["overlay"] = str(target)
    return {"passed": report.passed, "checks": report.checks, "info": info}


LICENCE_CHECKLIST = [
    "Dataset name and source page URL",
    "Exact download URL of the image and of the annotation JSON, and the version/commit/DOI they belong to",
    "Licence of the IMAGE (not only the repository or dataset software licence) and the attribution it requires",
    "Whether the licence allows downloading, altering the annotation, and redistributing the image in a repository",
    "Photographer / rights holder named by the source, if any",
]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--annotation", required=True, type=Path)
    parser.add_argument("--label", default="person")
    parser.add_argument("--out", type=Path, help="folder for the before/after overlay (never overwrites)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    for path in (args.image, args.annotation):
        if not path.is_file():
            print(f"Not a file: {path}", file=sys.stderr)
            return 2
    result = check(args.image, args.annotation, args.label, args.out)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if result["passed"] else 1
    print(f"Candidate: {args.image.name} + {args.annotation.name}")
    for c in result["checks"]:
        print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['check']}" + (f" - {c['detail']}" if c["detail"] else ""))
    info = result["info"]
    print("\nValues to record:")
    for key in ("image_sha256", "annotation_sha256_raw", "annotation_sha256_lf", "image_format", "image_size", "imagePath", "shift_pixels"):
        if key in info:
            print(f"  {key}: {info[key]}")
    for c in info.get("person_shapes", []):
        if "overlap_before_after" in c:
            print(f"  {c['pointer']}: {c['vertices']} vertices, shift {c['direction']} {info['shift_pixels']} px; "
                  f"original/shifted overlap {c['overlap_before_after']} (hint, at most {OVERLAP_HINT}: "
                  f"{'likely visible' if c['hint_visibly_misaligned'] else 'WEAK - check by eye'}); "
                  f"area {c['polygon_area_fraction']:.1%} of image ({'ok' if c['hint_large_enough'] else 'small - identifiability doubtful'})")
    if "overlay" in info:
        print(f"  overlay for human inspection: {info['overlay']}")
    print("\nStill needs a human (not checked here):")
    print("  - the person is clearly identifiable inside the chosen polygon")
    print("  - the shifted polygon visibly misaligns with the person (look at the overlay)")
    print("  - image rights, recorded separately from the source's software licence:")
    for item in LICENCE_CHECKLIST:
        print(f"      * {item}")
    print("\nRESULT:", "all machine checks passed" if result["passed"] else "NOT ELIGIBLE (see FAIL lines)")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
