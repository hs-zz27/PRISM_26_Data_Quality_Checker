"""File-only Penn-Fudan layout builder; preserves exact captured source bytes."""
from hashlib import sha256
import json
import os
import re
import time
import zipfile

from ..core.export import (
    ExportDependencyError, ExportLimitError, ExportNameError, ExportStopped,
)
from ..models.export import FrozenExport
from .export_files import (
    CHUNK_BYTES, directory_fd, fingerprint, open_relative, write_private,
)


def manifest_bytes(frozen):
    return (json.dumps(frozen.manifest.model_dump(mode='json'), sort_keys=True,
                       ensure_ascii=True, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')


def member_names(frozen):
    """Reject, never rename, unsafe or cross-platform-colliding selected IDs."""
    names, seen = [], set()
    for sample in frozen.samples:
        if not sample.entry.included:
            continue
        sample_id = sample.entry.sample_id
        base = sample_id.split('.')[0].upper()
        if (re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,199}', sample_id) is None
                or sample_id.endswith('.') or base in {'CON','PRN','AUX','NUL'}
                or re.fullmatch(r'(COM|LPT)[1-9]', base)):
            raise ExportNameError()
        rows = [('annotation_text', f'Annotation/{sample_id}.txt'),
                ('raw_mask', f'PedMasks/{sample_id}_mask.png')]
        if frozen.manifest.format != 'penn_fudan':
            rows = [('annotation_text', f'data/{sample_id}/annotations.json')]
            if frozen.manifest.include_images:
                rows.append(('original_image', f'data/{sample_id}/{_image_name(sample.manifest)}'))
        elif frozen.manifest.include_images:
            rows.append(('original_image', f'PNGImages/{sample_id}.png'))
        for role, name in rows:
            key = name.casefold()
            if key in seen:
                raise ExportNameError()
            seen.add(key)
            names.append((sample, role, name))
    return names


def _image_name(manifest):
    return 'image.jpg' if manifest.original_image.content_type == 'image/jpeg' else 'image.png'


def native_annotation(content, manifest):
    """One native document per selected image, preserving source fields and IDs.

    File references are explicitly rebased to the adjacent exported image.
    Other samples from a shared COCO document are not leaked into this export.
    """
    from copy import deepcopy
    from ..datasets.json_sample import read_annotation
    document, record = read_annotation(content, manifest.annotation_format, manifest.image_key)
    result = deepcopy(document.source)
    if document.format == 'coco':
        result['images'] = [{**deepcopy(record.source), 'file_name': _image_name(manifest)}]
        result['annotations'] = [deepcopy(obj.source) for obj in record.annotations]
    else:
        result['imagePath'] = _image_name(manifest)
    return (json.dumps(result, ensure_ascii=True, allow_nan=False) + '\n').encode('utf-8')


class _BoundedOutput:
    """Seekable ZIP sink; enforce high-water file length BEFORE every write."""
    def __init__(self, handle, maximum, check):
        self.handle, self.maximum, self.check = handle, maximum, check

    def write(self, content):
        self.check()
        if self.handle.tell() + len(content) > self.maximum:
            raise ExportLimitError()
        return self.handle.write(content)

    def seek(self, offset, whence=0):
        self.check()
        result = self.handle.seek(offset, whence)
        if result < 0 or result > self.maximum:
            raise ExportLimitError()
        return result

    def tell(self):
        return self.handle.tell()

    def flush(self):
        self.check()
        self.handle.flush()


def _info(name):
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100600 << 16
    info.flag_bits = 0
    return info


def _readme(frozen):
    included = sum(e.included for e in frozen.manifest.entries)
    return (
        'Reviewed dataset export\n'
        f'Selection: {frozen.manifest.selection.value}. Included samples: {included}.\n'
        'Membership and review provenance were frozen from a terminal snapshot-backed audit.\n'
        'Historical audited revisions are exported, not current catalogue heads.\n'
        'all_reviewed is diagnostic: included rejected samples remain rejected and are not certified for training.\n'
        'pending_review and needs_correction samples are excluded. See every decision in manifest.json.\n'
        + ('Original images are included.\n' if frozen.manifest.include_images else
           'Original images were explicitly omitted; this archive is not a standalone image dataset.\n')
        + ('Captured annotation text, masks and requested images are copied without transformation.\n'
           if frozen.manifest.format == 'penn_fudan' else
           'Each data/<sample>/ directory contains a native JSON document for that image. Image references are rebased.\n'
           'COCO documents contain only that image and its assigned annotations; categories and other source fields are retained.\n'
           'Original image bytes are unchanged. Structural problems and unsupported annotations may still be present.\n')
        + 'Layout is not a certificate of valid content. No annotation correction or recheck is performed.\n'
        'Zero included samples is a valid manifest-only export. No data files are fabricated.\n'
    ).encode('utf-8')


def build_archive(frozen: FrozenExport, *, settings, directory, stop_event, deadline_end, progress):
    """No SQL. Parent supplies only immutable input and an attempt-owned directory."""
    frozen = FrozenExport.model_validate_json(frozen.model_dump_json())
    def check():
        if stop_event.is_set():
            raise ExportStopped()
        if time.monotonic() >= deadline_end:
            raise ExportLimitError()
    check()
    names = member_names(frozen)
    document = manifest_bytes(frozen)
    if len(document) > frozen.policy.max_metadata_bytes:
        raise ExportLimitError()
    readme = _readme(frozen)
    total_input = len(document) + len(readme)
    if total_input > frozen.policy.max_input_bytes:
        raise ExportLimitError()
    write_private(directory, 'manifest.json', document)
    with directory_fd(directory, private=True) as parent:
        descriptor = os.open('archive.zip', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        with os.fdopen(descriptor, 'w+b') as output:
            with zipfile.ZipFile(_BoundedOutput(output, frozen.policy.max_archive_bytes, check),
                                 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6,
                                 allowZip64=True, strict_timestamps=True) as archive:
                processed, previous = 0, None
                for sample, role, name in names:
                    check()
                    if previous is not None and previous != sample.entry.ordinal:
                        processed += 1
                        progress(processed)
                    previous = sample.entry.ordinal
                    asset = getattr(sample.manifest, role)
                    if (asset.availability.value != 'available' or asset.storage_key is None
                            or asset.size_bytes is None or asset.sha256 is None):
                        raise ExportDependencyError('Frozen included asset is not available.')
                    if asset.size_bytes > frozen.policy.max_file_bytes:
                        raise ExportLimitError()
                    # Open once; hash exactly the chunks copied into this ZIP member.
                    with open_relative(settings.asset_root, asset.storage_key) as source:
                        before = os.fstat(source.fileno())
                        if before.st_size != asset.size_bytes:
                            raise ExportDependencyError('Captured source size does not match.')
                        hashed, count = sha256(), 0
                        transform = role == 'annotation_text' and frozen.manifest.format != 'penn_fudan'
                        captured = bytearray()
                        with archive.open(_info(name), 'w', force_zip64=True) as target:
                            while True:
                                check()
                                chunk = source.read(min(CHUNK_BYTES, frozen.policy.max_file_bytes - count + 1))
                                if not chunk:
                                    break
                                count += len(chunk)
                                total_input += len(chunk)
                                if (count > frozen.policy.max_file_bytes or count > asset.size_bytes
                                        or total_input > frozen.policy.max_input_bytes):
                                    raise ExportLimitError()
                                hashed.update(chunk)
                                if transform:
                                    captured.extend(chunk)
                                else:
                                    target.write(chunk)
                                check()
                            if transform:
                                if count != asset.size_bytes or hashed.hexdigest() != asset.sha256:
                                    raise ExportDependencyError('Captured annotation integrity verification failed.')
                                encoded = native_annotation(bytes(captured), sample.manifest)
                                if len(encoded) > frozen.policy.max_file_bytes:
                                    raise ExportLimitError()
                                for offset in range(0, len(encoded), CHUNK_BYTES):
                                    check()
                                    target.write(encoded[offset:offset + CHUNK_BYTES])
                        if (count != asset.size_bytes or hashed.hexdigest() != asset.sha256
                                or fingerprint(before) != fingerprint(os.fstat(source.fileno()))):
                            raise ExportDependencyError('Captured source integrity verification failed.')
                if previous is not None:
                    processed += 1
                    progress(processed)
                for name, content in [('manifest.json', document), ('README.txt', readme)]:
                    check()
                    with archive.open(_info(name), 'w', force_zip64=True) as target:
                        for offset in range(0, len(content), CHUNK_BYTES):
                            check()
                            target.write(content[offset:offset + CHUNK_BYTES])
            check()
            output.flush()
            os.fsync(output.fileno())
        os.fsync(parent)
    check()
