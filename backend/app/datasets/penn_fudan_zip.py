"""Bounded ZIP adapter and basic source inspection. No audit/rules/inference/SQL."""

from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import re
import stat
import struct
import unicodedata
import warnings
import zipfile
import zlib

from PIL import Image, UnidentifiedImageError

from ..core.config import AssetStorageSettings
from ..core.dataset_import import ImportInputError, ImportResourceError, ImportStorageError
from ..models.asset_manifest import SampleAssetManifest
from ..models.dataset import Sample, source_issue
from ..models.dataset_import import ImportPolicy, ImportRequest, SamplePublication
from ..services.asset_snapshot import capture_sample
from ..services.dataset_upload import sync_directory
from ..storage.workflow_repository import read_manifest_asset_file
from .penn_fudan import parse_annotations, parse_annotation_metadata

_ROLES = {"PNGImages": ("original_image", ".png"), "Annotation": ("annotation_text", ".txt"),
          "PedMasks": ("raw_mask", "_mask.png")}
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", re.I)
_CHUNK = 1024 * 1024


def _parts(info):
    name = info.orig_filename
    if (not name or name != info.filename or "\x00" in name or "\\" in name
            or name.startswith("/") or len(name.encode("utf-8")) > 2048):
        raise ImportInputError()
    pieces = name.rstrip("/").split("/")
    for piece in pieces:
        if (not piece or piece in {".", ".."} or ":" in piece or piece[-1] in {" ", "."}
                or any(ord(char) < 32 or char in '<>"|?*' for char in piece)
                or len(piece.encode("utf-8")) > 255 or _RESERVED.match(piece)):
            raise ImportInputError()
    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or bool(info.flag_bits & (1 | 64)):
        raise ImportInputError()
    if kind == stat.S_IFDIR and not info.is_dir() or kind == stat.S_IFREG and info.is_dir():
        raise ImportInputError()
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise ImportInputError()
    return tuple(pieces)


def _preflight(handle, policy):
    """Bound central-directory allocation BEFORE ZipFile builds ZipInfo objects.

    Only ordinary single-disk ZIP (stored/deflate), no ZIP64 or prepended executable.
    Central metadata <=16 MiB, each central filename <=2048 encoded bytes.
    """
    handle.seek(0, 2)
    size = handle.tell()
    if size > policy.max_archive_bytes:
        raise ImportResourceError()
    handle.seek(max(0, size - 65557))
    tail = handle.read(65557)
    index = tail.rfind(b"PK\x05\x06")
    if index < 0 or len(tail) - index < 22:
        raise ImportInputError()
    disk, central_disk, per_disk, count, central_size, offset, comment = struct.unpack_from("<4H2IH", tail, index + 4)
    eocd = size - len(tail) + index
    if (disk or central_disk or per_disk != count or count == 65535
            or central_size == 0xffffffff or offset == 0xffffffff
            or eocd + 22 + comment != size or offset + central_size != eocd):
        raise ImportInputError()
    if count > policy.max_entries or central_size > min(16 * 1024**2, policy.max_archive_bytes):
        raise ImportResourceError()
    handle.seek(offset)
    consumed = 0
    for _ in range(count):
        header = handle.read(46)
        if len(header) != 46 or header[:4] != b"PK\x01\x02":
            raise ImportInputError()
        filename, extra, annotation = struct.unpack_from("<3H", header, 28)
        if filename > 2048:
            raise ImportResourceError()
        skip = filename + extra + annotation
        consumed += 46 + skip
        if consumed > central_size:
            raise ImportInputError()
        handle.seek(skip, 1)
    if consumed != central_size:
        raise ImportInputError()
    handle.seek(0)
    if count and handle.read(4) != b"PK\x03\x04":
        raise ImportInputError()
    handle.seek(0)


@contextmanager
def verified_archive(path: Path, request: ImportRequest):
    """Keep one no-follow file descriptor and verify identity before AND after use."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            current = os.fstat(handle.fileno())
            if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
                raise ImportStorageError()
            def verify():
                handle.seek(0)
                digest, count = sha256(), 0
                while chunk := handle.read(_CHUNK):
                    count += len(chunk)
                    if count > request.policy.max_archive_bytes:
                        raise ImportResourceError()
                    digest.update(chunk)
                if count != request.archive_size_bytes or digest.hexdigest() != request.archive_sha256:
                    raise ImportStorageError()
                handle.seek(0)
            verify()
            _preflight(handle, request.policy)
            yield handle
            verify()
    finally:
        if descriptor is not None:
            os.close(descriptor)


def unpack_archive(path: Path, destination: Path, request: ImportRequest):
    """Validate every identity, stream every CRC, and map ONLY recognized roles."""
    try:
        with verified_archive(path, request) as handle, zipfile.ZipFile(handle) as archive:
            entries = archive.infolist()
            if len(entries) > request.policy.max_entries:
                raise ImportResourceError()
            aliases, full_paths, mapped, roots, role_candidates = {}, {}, [], set(), []
            expanded_metadata = 0
            for info in entries:
                parts = _parts(info)
                # Reject ZIP64 entry metadata even if the outer EOCD fits ordinary ZIP.
                position = 0
                while position < len(info.extra):
                    if len(info.extra) - position < 4:
                        raise ImportInputError()
                    tag, length = struct.unpack_from("<HH", info.extra, position)
                    if tag == 1 or position + 4 + length > len(info.extra):
                        raise ImportInputError()
                    position += 4 + length
                canonical = tuple(unicodedata.normalize("NFC", part).casefold() for part in parts)
                if canonical in full_paths:
                    raise ImportInputError()
                full_paths[canonical] = info.is_dir()
                for depth in range(1, len(parts) + 1):
                    key = canonical[:depth]
                    if key in aliases and aliases[key] != parts[:depth]:
                        raise ImportInputError()
                    aliases[key] = parts[:depth]
                if info.file_size < 0 or info.file_size > request.policy.max_file_bytes:
                    raise ImportResourceError()
                expanded_metadata += info.file_size
                if expanded_metadata > request.policy.max_expanded_bytes:
                    raise ImportResourceError()
                if info.is_dir() and info.file_size:
                    raise ImportInputError()
                metadata = "__MACOSX" in parts or parts[-1] == ".DS_Store"
                if not metadata:
                    for index, part in enumerate(parts):
                        if part in _ROLES:
                            if index > 1 or len(parts) > index + 2:
                                raise ImportInputError("INVALID_LAYOUT")
                            roots.add(parts[:index])
                            if not info.is_dir() and len(parts) == index + 2:
                                role_candidates.append((info, parts, index))
                mapped.append((info, parts, metadata))
            for path_key, is_directory in full_paths.items():
                if any(full_paths.get(path_key[:depth]) is False for depth in range(1, len(path_key))):
                    raise ImportInputError()  # A regular file cannot also be a directory ancestor.
            if len(roots) != 1:
                raise ImportInputError("INVALID_LAYOUT")
            root = next(iter(roots))
            recognized, ids, id_aliases = {}, set(), {}
            for info, parts, index in role_candidates:
                role, suffix = _ROLES[parts[index]]
                filename = parts[-1]
                if not filename.endswith(suffix):
                    continue
                sample = filename[:-len(suffix)]
                if (sample != sample.strip() or not sample.strip() or sample in {".", ".."} or len(sample) > 255
                        or len((sample + "_mask.png").encode("utf-8")) > 255):
                    raise ImportInputError("INVALID_LAYOUT")
                alias = unicodedata.normalize("NFC", sample).casefold()
                if alias in id_aliases and id_aliases[alias] != sample:
                    raise ImportInputError("INVALID_LAYOUT")
                id_aliases[alias] = sample
                key = (sample, role)
                if key in recognized:
                    raise ImportInputError("INVALID_LAYOUT")
                recognized[key] = (info, parts)
                ids.add(sample)
            if not ids:
                raise ImportInputError("INVALID_LAYOUT")
            if len(ids) > request.policy.max_samples:
                raise ImportResourceError()
            by_name = {info.filename: (sample, role, parts)
                       for (sample, role), (info, parts) in recognized.items()}
            destination.mkdir(mode=0o700)
            for role in _ROLES:
                (destination / role).mkdir(mode=0o700)
            total, unsupported = 0, False
            for info, parts, metadata in mapped:
                chosen = by_name.get(info.filename)
                if not info.is_dir() and chosen is None and not metadata:
                    relative = parts[len(root):] if parts[:len(root)] == root else parts
                    documentation = (len(relative) == 1 and
                                     (relative[0].lower().endswith((".txt", ".md")) or
                                      relative[0].lower() in {"readme", "license", "copying"}))
                    unsupported |= not documentation
                target = destination.joinpath(*parts[len(root):]) if chosen else None
                destination_file = target.open("xb") if target is not None else None
                written = 0
                try:
                    # Ignored files are also streamed, CRC-checked and counted.
                    with archive.open(info, "r") as source:
                        while chunk := source.read(_CHUNK):
                            written += len(chunk)
                            total += len(chunk)
                            if written > request.policy.max_file_bytes or total > request.policy.max_expanded_bytes:
                                raise ImportResourceError()
                            if chosen and chosen[1] == "annotation_text" and written > request.policy.max_annotation_bytes:
                                raise ImportResourceError()
                            if destination_file is not None:
                                destination_file.write(chunk)
                    if written != info.file_size:
                        raise ImportInputError()
                finally:
                    if destination_file is not None:
                        destination_file.close()
            return sorted(ids), unsupported
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, RuntimeError, NotImplementedError, zlib.error, UnicodeError) as error:
        if isinstance(error, (ImportResourceError, ImportStorageError)):
            raise
        raise ImportInputError() from error
    except OSError as error:
        raise ImportStorageError() from error


class _PreparedCapture:
    """File-only capture sink: NEVER creates a public manifest or opens PostgreSQL."""
    def __init__(self, root):
        self.asset_settings = AssetStorageSettings(root=root)

    def save_manifest(self, manifest):
        manifest = SampleAssetManifest.model_validate(manifest.model_dump())
        # capture fsyncs file contents, but import publication additionally requires
        # every newly installed directory entry to be durable before SQL registration.
        directories = {self.asset_settings.root}
        for role in ("original_image", "raw_mask", "annotation_text"):
            key = getattr(manifest, role).storage_key
            if key is not None:
                current = (self.asset_settings.root / key).parent
                while current != self.asset_settings.root:
                    directories.add(current)
                    current = current.parent
        for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
            sync_directory(directory)
        return manifest


def _png(content, policy, *, mask=False):
    # Reject an oversized declared PNG before Pillow's global bomb warning can
    # turn a resource-policy failure into an ordinary damaged-source diagnostic.
    if len(content) >= 24 and content[:8] == b"\x89PNG\r\n\x1a\n" and content[12:16] == b"IHDR":
        width, height = struct.unpack_from(">II", content, 16)
        if width * height > policy.max_image_pixels:
            raise ImportResourceError()
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(BytesIO(content)) as image:
            if image.format != "PNG" or getattr(image, "is_animated", False):
                raise ValueError("Unsupported image representation.")
            width, height = image.size
            if width * height > policy.max_image_pixels:
                raise ImportResourceError()
            if mask and image.mode not in {"1", "L", "P", "I", "I;16", "I;16B", "I;16L"}:
                raise ValueError("Unsupported instance mask.")
            channels = len(image.getbands())
            image.verify()
        with Image.open(BytesIO(content)) as image:
            image.load()
        return (width, height), channels


def inspect_captured(manifest, ordinal, *, asset_root: Path, policy: ImportPolicy):
    """Inspect verified captured bytes, never a second read of mutable source files."""
    issues, objects, count, size = [], [], None, None
    decoded = {}
    for role, field in (("original_image", "image"), ("raw_mask", "mask"), ("annotation_text", "annotation")):
        entry = getattr(manifest, role)
        if entry.availability.value != "available":
            issues.append(source_issue(("missing_" if entry.availability.value == "missing" else "unreadable_") + field,
                                       field=field))
            continue
        budget = policy.max_annotation_bytes if field == "annotation" else policy.max_file_bytes
        _, content = read_manifest_asset_file(manifest, role, AssetStorageSettings(root=asset_root), max_bytes=budget)
        if field != "annotation":
            try:
                decoded[field] = _png(content, policy, mask=field == "mask")
                if field == "image":
                    size = decoded[field][0]
            except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
                    Image.DecompressionBombError, Image.DecompressionBombWarning):
                issues.append(source_issue("invalid_" + field, field=field))
        else:
            try:
                text = content.decode("utf-8")  # No newline/whitespace normalization of captured bytes.
                objects = parse_annotations(text)
                if len(objects) > policy.max_objects_per_sample:
                    raise ImportResourceError()
                metadata = parse_annotation_metadata(text)
                if metadata["object_count"] is not None and metadata["object_count"] >= 0 and metadata["object_count"] == len(objects):
                    count = len(objects)
                else:
                    issues.append(source_issue("invalid_annotation", field=field))
                if metadata["errors"] or metadata["coordinate_origin"] != (1, 1):
                    issues.append(source_issue("invalid_metadata", field="annotation.metadata"))
                decoded[field] = metadata
            except (UnicodeError, ValueError):
                objects, count = [], None
                issues.append(source_issue("invalid_annotation", field=field))
    if "image" in decoded and "mask" in decoded and decoded["image"][0] != decoded["mask"][0]:
        issues.append(source_issue("invalid_metadata", field="mask"))
    if "image" in decoded and "annotation" in decoded:
        expected = (*decoded["image"][0], decoded["image"][1])
        if decoded["annotation"]["image_size"] != expected:
            issues.append(source_issue("invalid_metadata", field="annotation.metadata"))
    seen = set()
    for obj in objects:
        x1, y1, x2, y2 = obj["bbox"]
        if (obj["object_id"] in seen or obj["object_id"] <= 0 or x1 < 1 or y1 < 1 or x2 < x1 or y2 < y1
                or size is not None and (x2 > size[0] or y2 > size[1])):
            if not any(issue.field == "annotation.objects" for issue in issues):
                issues.append(source_issue("invalid_object", field="annotation.objects"))
        seen.add(obj["object_id"])
    return SamplePublication(manifest=manifest, ordinal=ordinal, annotation_object_count=count,
                             sample=Sample(dataset_id=manifest.dataset_id, sample_id=manifest.sample_id,
                                           revision=1, image_size=size, objects=objects,
                                           integrity_status="invalid" if issues else "valid", issues=issues))


def prepare_sample(dataset_id, sample_id, ordinal, *, source_root, asset_root, policy):
    manifest = capture_sample(dataset_id=dataset_id, sample_id=sample_id, sample_revision=1,
                              source_root=source_root, repository=_PreparedCapture(asset_root))
    return inspect_captured(manifest, ordinal, asset_root=asset_root, policy=policy)
