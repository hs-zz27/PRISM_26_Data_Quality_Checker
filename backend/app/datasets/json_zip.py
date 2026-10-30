"""Bounded managed ZIP import for external-image COCO and LabelMe JSON."""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
import struct
import unicodedata
import zipfile
import zlib

from ..core.config import AssetStorageSettings
from ..core.dataset_import import ImportInputError, ImportResourceError, ImportStorageError
from ..models.asset_manifest import AssetKind
from ..services.asset_snapshot import capture_sample
from .annotation_reader import load_document, AnnotationReadError
from .annotation_validation import _image_path, ImageInputError
from .coco import parse_coco
from .labelme import parse_labelme
from .json_sample import publication
from .penn_fudan_zip import verified_archive, _parts, _PreparedCapture, _CHUNK


@dataclass(frozen=True)
class JsonSampleSource:
    sample_id: str
    annotation: Path
    image: Path | None
    image_key: str
    format: str


def _extract(path, destination, request):
    """Stream all CRCs; reject aliases, links, traversal and expansion overruns."""
    with verified_archive(path, request) as handle, zipfile.ZipFile(handle) as archive:
        entries, aliases, paths = [], {}, {}
        expanded = 0
        for info in archive.infolist():
            parts = _parts(info)
            position = 0
            while position < len(info.extra):
                if len(info.extra) - position < 4:
                    raise ImportInputError()
                tag, length = struct.unpack_from("<HH", info.extra, position)
                if tag == 1 or position + 4 + length > len(info.extra):
                    raise ImportInputError()
                position += 4 + length
            canonical = tuple(unicodedata.normalize("NFC", part).casefold() for part in parts)
            if canonical in paths:
                raise ImportInputError()
            paths[canonical] = info.is_dir()
            for depth in range(1, len(parts) + 1):
                key = canonical[:depth]
                if key in aliases and aliases[key] != parts[:depth]:
                    raise ImportInputError()
                aliases[key] = parts[:depth]
            expanded += info.file_size
            if (info.file_size < 0 or info.file_size > request.policy.max_file_bytes
                    or expanded > request.policy.max_expanded_bytes):
                raise ImportResourceError()
            if info.is_dir() and info.file_size:
                raise ImportInputError()
            entries.append((info, parts))
        for key in paths:
            if any(paths.get(key[:depth]) is False for depth in range(1, len(key))):
                raise ImportInputError()
        destination.mkdir(mode=0o700)
        total, files, unsupported = 0, [], False
        for info, parts in entries:
            suffix = Path(parts[-1]).suffix.lower()
            metadata = "__MACOSX" in parts or parts[-1] == ".DS_Store"
            chosen = not info.is_dir() and not metadata and suffix in {".json", ".png", ".jpg", ".jpeg"}
            if not chosen and not metadata and not info.is_dir():
                unsupported |= suffix not in {".txt", ".md"} and parts[-1].lower() not in {"license", "readme", "copying"}
            target = destination.joinpath(*parts) if chosen else None
            if target is not None:
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                files.append(target)
            out = target.open("xb") if target else None
            count = 0
            try:
                with archive.open(info) as source:
                    while chunk := source.read(_CHUNK):
                        count += len(chunk)
                        total += len(chunk)
                        if (count > request.policy.max_file_bytes or total > request.policy.max_expanded_bytes
                                or chosen and suffix == ".json" and count > request.policy.max_annotation_bytes):
                            raise ImportResourceError()
                        if out:
                            out.write(chunk)
                if count != info.file_size:
                    raise ImportInputError()
            finally:
                if out:
                    out.close()
    return files, unsupported


def _resolve_image(record, document, root, annotation):
    if record.file_name is None or document.format == "labelme" and document.source.get("imageData") is not None:
        return None
    bases = [annotation.parent] if document.format == "labelme" else [
        annotation.parent, annotation.parent / "images", annotation.parent.parent,
        annotation.parent.parent / "images", root, root / "images",
    ]
    candidates = set()
    for base in bases:
        if not base.is_relative_to(root):
            continue
        try:
            candidate = _image_path(record.file_name, root, base)
        except ImageInputError:
            # Invalid references are never repaired or resolved by basename search.
            continue
        if candidate.is_file():
            candidates.add(candidate)
    if len(candidates) > 1:
        raise ImportInputError("INVALID_LAYOUT")
    return next(iter(candidates), None)


def unpack_archive(path, destination, request):
    try:
        files, unsupported = _extract(path, destination, request)
        root = destination.resolve()
        samples, used = [], set()
        parser = {"coco": parse_coco, "labelme": parse_labelme}[request.format]
        for annotation in sorted(file for file in files if file.suffix.lower() == ".json"):
            try:
                document = load_document(annotation, parser, max_bytes=request.policy.max_annotation_bytes)
            except AnnotationReadError:
                if annotation == destination / "manifest.json":
                    # Only our validated export metadata may be skipped. A broken
                    # annotation named manifest.json is still an import error.
                    from ..models.export import ExportManifest
                    try:
                        metadata = ExportManifest.model_validate_json(annotation.read_bytes())
                    except ValueError:
                        raise ImportInputError("INVALID_LAYOUT")
                    if metadata.format != request.format:
                        raise ImportInputError("INVALID_LAYOUT")
                    used.add(annotation)
                    continue
                raise
            used.add(annotation)
            for record in document.images:
                if len(samples) >= request.policy.max_samples or len(record.annotations) > request.policy.max_objects_per_sample:
                    raise ImportResourceError()
                image = _resolve_image(record, document, root, annotation)
                if image is not None:
                    used.add(image)
                identity = annotation.relative_to(destination).as_posix() + "#" + record.key
                stem = Path(record.file_name or annotation.name).stem
                stem = re.sub(r"[^A-Za-z0-9_-]", "_", stem)[:100].strip("_") or "sample"
                sample_id = f"{stem}_{sha256(identity.encode()).hexdigest()[:16]}"
                samples.append(JsonSampleSource(sample_id, annotation, image, record.key, request.format))
        if not samples or len({item.sample_id for item in samples}) != len(samples):
            raise ImportInputError("INVALID_LAYOUT")
        unsupported |= any(file not in used for file in files)
        return samples, unsupported
    except (AnnotationReadError, zipfile.BadZipFile, zipfile.LargeZipFile, EOFError,
            RuntimeError, NotImplementedError, zlib.error, UnicodeError) as error:
        if isinstance(error, (ImportInputError, ImportResourceError, ImportStorageError)):
            raise
        raise ImportInputError("INVALID_LAYOUT") from error
    except OSError as error:
        raise ImportStorageError() from error


def prepare_sample(dataset_id, source, ordinal, *, source_root, asset_root, policy):
    files = {AssetKind.ANNOTATION_TEXT: (source.annotation, "application/json")}
    if source.image is not None:
        with source.image.open("rb") as handle:
            signature = handle.read(3)
        mime = "image/jpeg" if signature == b"\xff\xd8\xff" else "image/png"
        files[AssetKind.ORIGINAL_IMAGE] = (source.image, mime)
    manifest = capture_sample(dataset_id=dataset_id, sample_id=source.sample_id, sample_revision=1,
        source_root=source_root, source_files=files, annotation_format=source.format,
        image_key=source.image_key, repository=_PreparedCapture(asset_root))
    return publication(manifest, ordinal, AssetStorageSettings(root=asset_root), policy)
