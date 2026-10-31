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
import json
from pathlib import Path
import re
import sys


from ..models.annotation_document import AnnotationDocument, ReaderIssue
from .annotation_reader import AnnotationReadError
from .annotation_summary import summarize_annotation
from .annotation_validation import _decode_metadata, _image_bytes, image_path_for, inspect_document
from .overlay_render import OverlayObject, OverlayTooLarge, PALETTE, render_overlay  # noqa: F401
from ..models.annotation_validation import ValidationLimits
from .coco import read_coco
from .labelme import read_labelme
from .synthetic_samples import COCO_MARKER, write_synthetic_dataset



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
            result = overlays.get(doc["number"], {}).get(image["key"])
            if result and result["status"] == "written":
                renamed = "  (name taken: existing file kept, new file added)" if result["renamed"] else ""
                out.append(f"    overlay: {result['path']}  scale {result['scale']}x{renamed}")
                for item in result["objects"]:
                    for text in item["notes"]:
                        out.append(f"      preview #{item['number']}: {item['render']}: {text}")
            elif result:
                out.append(f"    overlay skipped: {result['reason']}")
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
    head = (f"    object {obj['key']} (preview #{obj['index']})  source_id={obj['source_id']}  label={obj['label']!r}  "
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

def _empty_reason(obj, entry):
    if any(i["code"] == "unsupported_shape" for i in entry["issues"]):
        kind = entry["source_shape_type"] if entry["source_shape_type"] is not None else "missing"
        return f"shape_type {kind!r} is not normalized by the readers, so there is no geometry to draw."
    if entry["box"]["state"] == "malformed" or entry["polygon"]["state"] == "malformed":
        return "The supplied box or polygon could not be parsed (see diagnostics); the original is kept in the source."
    if entry["mask"]["state"] == "retained_not_decoded":
        return f"The mask ({entry['mask']['encoding']}) is kept as supplied and is not decoded, so it is not drawn."
    return "The object has no box or polygon geometry."


def overlay_objects(record, image_entry):
    """Plain drawing input. Geometry is passed through unchanged; only labels are shortened."""
    items = []
    for obj, entry in zip(record.annotations, image_entry["objects"]):
        ident = str(obj.source_id) if obj.source_id is not None else obj.key
        if obj.group_id is not None:
            ident += f" group {obj.group_id}"
        label = (obj.label if obj.label is not None else "(no label)")
        items.append(OverlayObject(
            number=entry["index"], label=label if len(label) <= 28 else label[:25] + "...",
            ident=ident, box=obj.bbox, box_origin=obj.bbox_origin if obj.bbox is not None else None,
            polygons=obj.polygons, diagnostics=[(i["code"], i["message"]) for i in entry["issues"]],
            empty_reason=_empty_reason(obj, entry)))
    return items


def create_unique(path: Path):
    """Create a new file; never overwrite. If the name is taken, use -2, -3, ... instead."""
    for attempt in range(1, 1000):
        candidate = path if attempt == 1 else path.with_name(f"{path.stem}-{attempt}{path.suffix}")
        try:
            return open(candidate, "xb"), candidate
        except FileExistsError:
            continue
    raise OSError("Could not find an unused output file name.")


def write_overlays(documents, report, *, image_root, reference_base, out_dir: Path, scale,
                   show_vertices=False):
    """Return {document number: {image key: result}}. One bad image never stops the others."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if not out_dir.is_dir():
        raise ValueError("--out must be a directory.")
    written = {}
    for document, entry in zip(documents, report["documents"]):
        root = image_root(document) if callable(image_root) else image_root
        results = written.setdefault(entry["number"], {})
        for position, (record, image) in enumerate(zip(document.images, entry["images"]), 1):
            def skip(reason):
                results[record.key] = {"status": "skipped", "path": None, "reason": reason}
            if image["availability"] != "available" or record.file_name is None:
                skip("image not available"); continue
            if any(i["code"] in {"unsupported_image_orientation", "embedded_image_unavailable"}
                   for i in image["issues"]):
                skip("coordinate mapping for this image is unsupported"); continue
            try:
                path = image_path_for(document, record.file_name, root, reference_base=reference_base)
                raw = _image_bytes(path, ValidationLimits())
                _decode_metadata(raw, ValidationLimits())  # same safety limits as inspection
                note = None
                if image["decoded_size"] and image["declared_size"] != image["decoded_size"]:
                    note = ("The file declares %sx%s but decodes to %sx%s; the image is drawn from its "
                            "decoded size." % (*image["declared_size"], *image["decoded_size"]))
                canvas, info = render_overlay(
                    raw, overlay_objects(record, image), scale=scale, synthetic=entry["synthetic"],
                    declared_note=note, show_vertices=show_vertices)
                stem = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(record.file_name).stem) or "image"
                # The position keeps two records that name the same file from sharing a name.
                wanted = out_dir / f"{entry['number']:02d}_{position:03d}_{stem}.overlay.png"
                handle, target = create_unique(wanted)
                with handle:
                    canvas.save(handle, format="PNG")
                results[record.key] = {
                    "status": "written", "path": str(target), "renamed": target != wanted,
                    "scale": round(info.scale, 4), "canvas": list(info.canvas_size),
                    "objects": [{"number": o.number, "render": o.status, "notes": o.notes}
                                for o in info.outcomes]}
            except OverlayTooLarge as error:
                skip(f"preview would exceed the size limits: {error}")
            except Exception as error:  # noqa: BLE001 - isolate one image; the reason is reported
                skip(f"preview failed for this image ({type(error).__name__}: {str(error)[:120]})")
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
        p.add_argument("--scale", type=float, help="Preview scale from the decoded image size (default: automatic, about 1000 px on the long side)")
        p.add_argument("--vertices", action="store_true", help="Also mark polygon vertices (off by default; clutters crowded scenes)")
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
                                  reference_base=args.reference_base, out_dir=out_dir, scale=args.scale,
                                  show_vertices=args.vertices)
    return report, overlays


def main(argv=None):
    try:
        sys.stdout.reconfigure(errors="backslashreplace")  # Windows consoles reject some labels
    except (AttributeError, ValueError):
        pass
    args = _parser().parse_args(argv)
    if args.max_images < 0 or (args.scale is not None and not (args.scale > 0 and args.scale < 1e6)):
        print(json.dumps({"error": "--max-images must be >= 0 and --scale a positive number"}), file=sys.stderr)
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
