"""Read-only verification demo for COCO and LabelMe samples.

Prints every image, object, label, geometry type and diagnostic, and writes overlay
PNGs so a person can check that the annotations line up with the pixels. This is not
an audit: it applies no guidelines, runs no model, changes no source file and never
repairs, clips, rescales or drops geometry.

Overlay legend (also written onto each overlay image):
  solid lines   ORIGINAL geometry exactly as supplied in the source file
  dashed lines  DERIVED geometry computed by this tool (a box from polygon vertices)
"""

import argparse
from collections import Counter
from dataclasses import replace
from fnmatch import fnmatch
from io import BytesIO
import json
from pathlib import Path
import re
import sys

from PIL import Image, ImageDraw, ImageFont

from ..models.annotation_document import AnnotationDocument, ReaderIssue
from .annotation_reader import AnnotationReadError
from .annotation_summary import summarize_annotation
from .annotation_validation import _decode_metadata, _image_bytes, image_path_for, inspect_document
from ..models.annotation_validation import ValidationLimits
from .coco import read_coco
from .labelme import read_labelme
from .synthetic_samples import COCO_MARKER, write_synthetic_dataset

PALETTE = [(230, 25, 75), (60, 180, 75), (0, 130, 200), (245, 130, 48),
           (145, 30, 180), (0, 170, 170), (240, 50, 230), (128, 128, 0)]
MIN_LEGEND_WIDTH = 640


def is_synthetic(document: AnnotationDocument) -> bool:
    if document.format == "coco":
        info = document.source.get("info")
        return isinstance(info, dict) and info.get("description") == COCO_MARKER
    flags = document.source.get("flags")
    return isinstance(flags, dict) and flags.get("synthetic") is True


# ---------------------------------------------------------------- input loading

def load_inputs(fmt: str, path: Path, *, recursive: bool = False, pattern: str = "*.json",
                exclude: tuple[str, ...] = ()):
    """Return (documents, unreadable). An unreadable file never hides readable ones."""
    if fmt == "coco" or path.is_file():
        files = [path]
    else:
        files = sorted(path.rglob(pattern) if recursive else path.glob(pattern))
        # Companion files (reports, manifests) are not annotations; skipping them is explicit.
        files = [f for f in files if not any(fnmatch(f.name, skip) for skip in exclude)]
    reader = read_coco if fmt == "coco" else read_labelme
    documents, unreadable = [], []
    for file in files:
        try:
            documents.append(reader(file))
        except AnnotationReadError as error:
            unreadable.append({"path": str(file), "reason": str(error)})
        except OSError as error:
            unreadable.append({"path": str(file), "reason": f"Cannot open file: {type(error).__name__}"})
    return documents, unreadable


# ----------------------------------------------------------------------- report

def _issue(issue: ReaderIssue) -> dict:
    # LabelMe has one image per document, so image-level issues have no JSON pointer.
    return {"severity": issue.severity, "code": issue.code, "location": issue.location or "(document)",
            "message": issue.message}


def _under(location: str, key: str) -> bool:
    return location == key or location.startswith(key + "/")


def _object_entry(obj, index, geometry_issues, shape_notes):
    summary = summarize_annotation(obj)
    issues = [*obj.issues, *(g for g in geometry_issues if _under(g.location, obj.key))]
    source = obj.source if isinstance(obj.source, dict) else {}
    return {
        "index": index, "key": obj.key, "source_id": obj.source_id, "label": obj.label,
        "category_id": obj.category_id, "group_id": obj.group_id,
        "source_shape_type": source.get("shape_type"),
        "box": {"state": summary.box.state, "detail": summary.box.detail,
                "value": list(obj.bbox) if obj.bbox is not None else None},
        "polygon": {"state": summary.polygon.state, "detail": summary.polygon.detail,
                    "parts": summary.polygon_parts, "vertex_counts": list(summary.polygon_vertex_counts)},
        "mask": {"state": summary.mask.state, "detail": summary.mask.detail,
                 "encoding": obj.mask.encoding if obj.mask else None},
        "issues": [_issue(i) for i in issues],
    }


def build_report(documents, unreadable, *, image_root, reference_base=None, max_images=5,
                 limits=None) -> dict:
    """Pure data: what was read, what each object contains, and every diagnostic."""
    limits = limits or ValidationLimits()
    entries, totals = [], Counter()
    states = {name: Counter() for name in ("box", "polygon", "mask")}
    labels = Counter()
    for number, document in enumerate(documents, 1):
        shown = document.images if max_images == 0 else document.images[:max_images]
        root = image_root(document) if callable(image_root) else image_root
        inspection = inspect_document(replace(document, images=shown), root,
                                      reference_base=reference_base, limits=limits)
        images = []
        for image_index, (record, checked) in enumerate(zip(shown, inspection.images)):
            objects = [_object_entry(obj, i, inspection.geometry_issues, None)
                       for i, obj in enumerate(record.annotations, 1)]
            images.append({
                "key": record.key, "source_id": record.source_id, "file_name": record.file_name,
                "declared_size": [record.width, record.height],
                "decoded_size": [checked.actual_width, checked.actual_height]
                                if checked.actual_width is not None else None,
                "availability": checked.availability, "sha256": checked.sha256,
                "issues": [_issue(i) for i in (*record.issues, *checked.issues)],
                "objects": objects,
            })
        for record in document.images:
            for obj in record.annotations:
                summary = summarize_annotation(obj)
                for name in states:
                    states[name][getattr(summary, name).state] += 1
                labels[obj.label if obj.label is not None else "<no label>"] += 1
        for obj in document.unassigned_annotations:
            labels[obj.label if obj.label is not None else "<no label>"] += 1
        all_issues = document.all_issues
        entries.append({
            "number": number, "format": document.format, "source_path": document.source_path,
            "synthetic": is_synthetic(document),
            "image_count": len(document.images),
            "object_count": sum(len(r.annotations) for r in document.images),
            "unassigned_count": len(document.unassigned_annotations),
            "images_inspected": len(shown),
            "document_issues": [_issue(i) for i in document.issues],
            "unassigned": [_object_entry(o, i, [], None)
                           for i, o in enumerate(document.unassigned_annotations, 1)],
            "images": images,
            "reader_issue_total": len(all_issues),
            "geometry_issue_total": len(inspection.geometry_issues),
        })
        totals["images"] += len(document.images)
        totals["objects"] += entries[-1]["object_count"]
        totals["unassigned"] += len(document.unassigned_annotations)
        totals["reader_diagnostics"] += len(all_issues)
        totals["geometry_diagnostics"] += len(inspection.geometry_issues)
        totals["image_diagnostics"] += sum(len(i.issues) for i in inspection.images)
    return {"inspection_only": True, "documents": entries, "unreadable": unreadable,
            "totals": dict(totals), "labels": dict(sorted(labels.items())),
            "geometry_states": {name: dict(counter) for name, counter in states.items()}}


def diagnostic_total(report) -> int:
    t = report["totals"]
    return (t.get("reader_diagnostics", 0) + t.get("geometry_diagnostics", 0)
            + t.get("image_diagnostics", 0) + len(report["unreadable"]))


# ------------------------------------------------------------------------- text

def _fmt_box(box):
    return "(" + ", ".join(f"{v:g}" for v in box) + ")"


def render_text(report, overlays=None) -> str:
    overlays = overlays or {}
    out = ["=== Annotation preview: read-only. No audit, no model, no source file modified. ==="]
    for doc in report["documents"]:
        tag = "  [SYNTHETIC FIXTURE - not a real dataset]" if doc["synthetic"] else ""
        out.append("")
        out.append(f"Document {doc['number']}: {doc['format']}{tag}")
        out.append(f"  path: {doc['source_path']}")
        out.append(f"  images: {doc['image_count']}   objects: {doc['object_count']}   "
                   f"unassigned annotations: {doc['unassigned_count']}")
        for issue in doc["document_issues"]:
            out.append(f"  ! [{issue['severity']}] {issue['code']} {issue['location']}: {issue['message']}")
        if doc["images_inspected"] < doc["image_count"]:
            out.append(f"  (detail and image/geometry checks cover the first {doc['images_inspected']} "
                       f"of {doc['image_count']} images; use --max-images 0 for all)")
        for image in doc["images"]:
            dw, dh = image["declared_size"]
            actual = image["decoded_size"]
            out.append(f"  Image {image['key'] or '(document)'}  file={image['file_name']}  "
                       f"declared={dw}x{dh}  decoded={'x'.join(map(str, actual)) if actual else 'n/a'}  "
                       f"image={image['availability']}")
            for issue in image["issues"]:
                out.append(f"    ! [{issue['severity']}] {issue['code']} {issue['location']}: {issue['message']}")
            for obj in image["objects"]:
                out.extend(_object_lines(obj))
            if not image["objects"]:
                out.append("    (no objects)")
            if image["key"] in overlays.get((doc["number"]), {}):
                out.append(f"    overlay: {overlays[doc['number']][image['key']]}")
        if doc["unassigned"]:
            out.append("  Unassigned annotations (no unambiguous image):")
            for obj in doc["unassigned"]:
                out.extend(_object_lines(obj))
    if report["unreadable"]:
        out.append("")
        out.append("UNREADABLE INPUT (no annotations could be read from these files):")
        for item in report["unreadable"]:
            out.append(f"  {item['path']}: {item['reason']}")
    t = report["totals"]
    out += ["", "--- Totals (all images read, not only those shown) ---",
            f"images={t.get('images', 0)} objects={t.get('objects', 0)} unassigned={t.get('unassigned', 0)} "
            f"unreadable_files={len(report['unreadable'])}",
            "labels: " + (", ".join(f"{k} x{v}" for k, v in report["labels"].items()) or "none")]
    for name, counter in report["geometry_states"].items():
        out.append(f"{name} states: " + (", ".join(f"{k}={v}" for k, v in sorted(counter.items())) or "no objects"))
    out.append(f"diagnostics: reader={t.get('reader_diagnostics', 0)} geometry={t.get('geometry_diagnostics', 0)} "
               f"image={t.get('image_diagnostics', 0)} unreadable_files={len(report['unreadable'])}")
    out.append("Diagnostics are structural facts, not annotation-quality verdicts.")
    return "\n".join(out)


def _object_lines(obj):
    head = (f"    object {obj['key']}  source_id={obj['source_id']}  label={obj['label']!r}  "
            f"group={obj['group_id'] if obj['group_id'] is not None else '-'}")
    if obj["source_shape_type"] is not None:
        head += f"  shape_type={obj['source_shape_type']}"
    box, polygon, mask = obj["box"], obj["polygon"], obj["mask"]
    unsupported_shape = any(i["code"] == "unsupported_shape" for i in obj["issues"])
    lines = [head]
    if unsupported_shape:
        kind = obj["source_shape_type"] if obj["source_shape_type"] is not None else "missing shape_type"
        lines.append(f"      shape   : UNSUPPORTED ({kind}) - kept in source, not normalized, not drawn")
    lines += [
             "      box     : " + box["state"] + (f" {_fmt_box(box['value'])}" if box["value"] else "")
             + (" [DERIVED, not in source]" if box["state"] == "derived" else ""),
             "      polygon : " + polygon["state"]
             + (f" {polygon['parts']} part(s), vertices {polygon['vertex_counts']}" if polygon["parts"] else ""),
             "      mask    : " + mask["state"] + (f" ({mask['encoding']})" if mask["encoding"] else "")]
    for entry in (box, polygon, mask):
        if entry["detail"]:
            lines.append(f"        note: {entry['detail']}")
    for issue in obj["issues"]:
        lines.append(f"      ! [{issue['severity']}] {issue['code']} {issue['location']}: {issue['message']}")
    return lines


# ---------------------------------------------------------------------- overlay

def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def _dashed_line(draw, start, end, color, width=1, dash=6, gap=4):
    (x1, y1), (x2, y2) = start, end
    length = max(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5, 1e-9)
    ux, uy, position = (x2 - x1) / length, (y2 - y1) / length, 0.0
    while position < length:
        stop = min(position + dash, length)
        draw.line([(x1 + ux * position, y1 + uy * position), (x1 + ux * stop, y1 + uy * stop)],
                  fill=color, width=width)
        position += dash + gap


def _dashed_rectangle(draw, box, color, width=1):
    left, top, right, bottom = box
    corners = [(left, top), (right, top), (right, bottom), (left, bottom)]
    for start, end in zip(corners, corners[1:] + corners[:1]):
        _dashed_line(draw, start, end, color, width)


def draw_overlay(image_bytes: bytes, record, objects, *, synthetic: bool, scale: int | None,
                 declared_mismatch: str | None, out_path: Path) -> dict:
    """Draw original (solid) and derived (dashed) geometry; coordinates stay unscaled."""
    with Image.open(BytesIO(image_bytes)) as source:
        base = source.convert("RGB")
    width, height = base.size
    factor = scale or max(1, min(4, 640 // max(width, height)))
    if factor > 1:
        base = base.resize((width * factor, height * factor), Image.NEAREST)
    font = _font(13)
    notes = [("SYNTHETIC FIXTURE - not a real dataset" if synthetic else None),
             "solid = ORIGINAL geometry as supplied     dashed = DERIVED by this tool (not in source)",
             "* = derived box     ! = has diagnostics     "
             f"coordinates are drawn unscaled (image shown at {factor}x)"]
    if declared_mismatch:
        notes.append(declared_mismatch)
    not_drawn = 0
    canvas_w = max(base.width, MIN_LEGEND_WIDTH)
    legend_h = 18 * (len([n for n in notes if n]) + 1) + 6
    canvas = Image.new("RGB", (canvas_w, base.height + legend_h), (255, 255, 255))
    canvas.paste(base, (0, 0))
    draw = ImageDraw.Draw(canvas)

    def at(point):
        return (point[0] * factor, point[1] * factor)

    for index, (obj, entry) in enumerate(zip(record.annotations, objects)):
        color = PALETTE[index % len(PALETTE)]
        drawn = False
        for part in obj.polygons:
            points = [at(p) for p in part]
            if len(points) >= 2:
                draw.line(points + [points[0]], fill=color, width=2)
                for x, y in points:
                    draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=color)
                drawn = True
        mark = ""
        if obj.bbox is not None and obj.bbox_origin == "supplied":
            draw.rectangle([*at(obj.bbox[:2]), *at(obj.bbox[2:])], outline=color, width=2)
            drawn = True
        elif obj.bbox is not None and obj.bbox_origin == "derived_from_polygon":
            _dashed_rectangle(draw, [*at(obj.bbox[:2]), *at(obj.bbox[2:])], color)
            mark, drawn = "*", True
        if not drawn:
            not_drawn += 1
            continue
        anchor = obj.bbox[:2] if obj.bbox is not None else obj.polygons[0][0]
        flag = "!" if entry["issues"] else ""
        text = f"{index + 1}:{obj.label or '?'}{mark}{flag}"
        x, y = at(anchor)
        draw.text((max(0, x), max(0, y - 14)), text, fill=color, font=font)
    if not_drawn:
        notes.append(f"{not_drawn} object(s) not drawn: no supported box or polygon geometry")
    y = base.height + 4
    for line in (n for n in notes if n):
        draw.text((4, y), line, fill=(0, 0, 0), font=font)
        y += 18
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, format="PNG")
    return {"path": str(out_path), "not_drawn": not_drawn, "scale": factor}


def write_overlays(documents, report, *, image_root, reference_base, out_dir: Path, scale):
    """Return {document number: {image key: overlay path or skip reason}}."""
    written = {}
    for document, entry in zip(documents, report["documents"]):
        root = image_root(document) if callable(image_root) else image_root
        results = written.setdefault(entry["number"], {})
        for position, (record, image) in enumerate(zip(document.images, entry["images"]), 1):
            if image["availability"] != "available" or record.file_name is None:
                results[record.key] = "skipped: image not available"
                continue
            if any(i["code"] in {"unsupported_image_orientation", "embedded_image_unavailable"}
                   for i in image["issues"]):
                results[record.key] = "skipped: coordinate mapping for this image is unsupported"
                continue
            path = image_path_for(document, record.file_name, root, reference_base=reference_base)
            raw = _image_bytes(path, ValidationLimits())
            _decode_metadata(raw, ValidationLimits())  # same safety limits as inspection
            mismatch = None
            if image["decoded_size"] and image["declared_size"] != image["decoded_size"]:
                mismatch = ("DECLARED SIZE %sx%s != DECODED %sx%s (coordinates drawn unscaled)"
                            % (*image["declared_size"], *image["decoded_size"]))
            stem = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(record.file_name).stem) or "image"
            # The position keeps two records that name the same file from overwriting each other.
            target = out_dir / f"{entry['number']:02d}_{position:03d}_{stem}.overlay.png"
            info = draw_overlay(raw, record, image["objects"], synthetic=entry["synthetic"],
                                scale=scale, declared_mismatch=mismatch, out_path=target)
            results[record.key] = info["path"]
    return written


# -------------------------------------------------------------------------- CLI

def _parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--out", help="Directory for overlay PNGs (default: annotation_preview_output)")
        p.add_argument("--no-overlay", action="store_true", help="Print only; write no images")
        p.add_argument("--json", action="store_true", help="Print the full report as JSON")
        p.add_argument("--max-images", type=int, default=5,
                       help="Images to detail and overlay per document; 0 means all (default 5)")
        p.add_argument("--scale", type=int, help="Integer overlay magnification (default: automatic)")
        p.add_argument("--reference-base", help="Override the directory relative image references start from")

    coco = sub.add_parser("coco", help="One COCO instances JSON file")
    coco.add_argument("annotations")
    coco.add_argument("--image-root", required=True, help="Directory COCO file_name values are relative to")
    common(coco)
    lm = sub.add_parser("labelme", help="One LabelMe JSON file, or a directory of them")
    lm.add_argument("annotations")
    lm.add_argument("--image-root", help="Allowed image root (default: the JSON file's directory)")
    lm.add_argument("--recursive", action="store_true")
    lm.add_argument("--pattern", default="*.json", help="File pattern inside a directory (default *.json)")
    lm.add_argument("--exclude", action="append", metavar="PATTERN",
                    help="File name pattern to skip, e.g. *.validation.json (repeatable)")
    common(lm)
    syn = sub.add_parser("synthetic", help="Write SYNTHETIC fixtures to a new directory, then preview them")
    syn.add_argument("directory", help="Must be new or empty")
    common(syn)
    return parser


def _run(jobs, args, out_dir):
    documents, unreadable, roots = [], [], {}
    for fmt, path, root in jobs:
        found, bad = load_inputs(fmt, path, recursive=getattr(args, "recursive", False),
                                 pattern=getattr(args, "pattern", "*.json"),
                                 exclude=tuple(getattr(args, "exclude", None) or ()))
        for document in found:
            roots[id(document)] = root
        documents += found
        unreadable += bad
    if not documents:
        raise AnnotationReadError("No readable annotation document was found.") if not unreadable else \
            AnnotationReadError("; ".join(f"{u['path']}: {u['reason']}" for u in unreadable))
    resolver = lambda document: roots[id(document)]  # noqa: E731
    report = build_report(documents, unreadable, image_root=resolver,
                          reference_base=args.reference_base, max_images=args.max_images)
    overlays = {}
    if not args.no_overlay:
        overlays = write_overlays(documents, report, image_root=resolver,
                                  reference_base=args.reference_base, out_dir=out_dir, scale=args.scale)
    return report, overlays


def main(argv=None):
    try:
        sys.stdout.reconfigure(errors="backslashreplace")  # Windows consoles reject some labels
    except (AttributeError, ValueError):
        pass
    args = _parser().parse_args(argv)
    if args.max_images < 0 or (args.scale is not None and args.scale < 1):
        print(json.dumps({"error": "--max-images must be >= 0 and --scale >= 1"}), file=sys.stderr)
        return 2
    try:
        if args.command == "synthetic":
            made = write_synthetic_dataset(args.directory)
            out_dir = Path(args.out) if args.out else made["root"] / "overlays"
            jobs = [("coco", made["coco_json"], made["coco_images"]),
                    ("labelme", made["labelme_dir"], made["labelme_dir"])]
        else:
            path = Path(args.annotations)
            if args.command == "coco":
                root = Path(args.image_root)
            else:
                root = Path(args.image_root) if args.image_root else (path if path.is_dir() else path.parent)
            out_dir = Path(args.out) if args.out else Path("annotation_preview_output")
            jobs = [(args.command, path, root)]
        report, overlays = _run(jobs, args, out_dir)
    except (AnnotationReadError, ValueError, OSError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2
    if args.json:
        report["overlays"] = {str(k): v for k, v in overlays.items()}
        print(json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False))
    else:
        print(render_text(report, overlays))
        if overlays:
            print(f"\nOverlays written under: {out_dir}")
    return 1 if diagnostic_total(report) else 0


if __name__ == "__main__":
    raise SystemExit(main())
