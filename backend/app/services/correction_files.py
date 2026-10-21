"""Bounded exact-byte reads and unique immutable writes in managed asset storage."""
from contextlib import contextmanager
from hashlib import sha256
from uuid import uuid4

from ..core.correction import CorrectionError, CorrectionStorageError
from ..core.export import ExportDependencyError, ExportLimitError
from ..models.asset_manifest import AssetManifestEntry
from .export_files import directory_fd, open_relative, verify_handle, write_private


@contextmanager
def storage_errors():
    try:
        yield
    except (OSError, ExportDependencyError, ExportLimitError) as error:
        raise CorrectionStorageError() from error


def read_asset(settings, entry):
    entry = AssetManifestEntry.model_validate(entry.model_dump())
    if entry.availability.value != 'available':
        raise CorrectionError('SOURCE_NOT_CORRECTABLE')
    limit = settings.policy.max_asset_bytes
    with storage_errors():
        with open_relative(settings.asset_root,entry.storage_key) as handle:
            verify_handle(handle,size=entry.size_bytes,digest=entry.sha256,limit=limit)
            # The same open file, never a second pathname resolution. Re-hash the
            # bounded bytes actually used in decoding against the registration.
            data = handle.read(limit+1)
            if len(data) != entry.size_bytes or len(data)>limit or sha256(data).hexdigest()!=entry.sha256:
                raise CorrectionStorageError()
            return data


def write_asset(settings, data, *, role):
    if role not in {'annotation','mask'}:
        raise ValueError('Unsupported correction asset role.')
    limit = settings.policy.max_annotation_bytes if role=='annotation' else settings.policy.max_mask_bytes
    if not data or len(data)>limit:
        raise CorrectionError('UPLOAD_TOO_LARGE')
    token=uuid4().hex
    directory=settings.asset_root/'corrections'/'v1'/token
    filename='annotation.txt' if role=='annotation' else 'mask.png'
    with storage_errors():
        with directory_fd(directory,create=True,private=True):
            pass
        write_private(directory,filename,data)
    return AssetManifestEntry(availability='available',storage_key=f'corrections/v1/{token}/{filename}',
        content_type='text/plain' if role=='annotation' else 'image/png',sha256=sha256(data).hexdigest(),size_bytes=len(data))
