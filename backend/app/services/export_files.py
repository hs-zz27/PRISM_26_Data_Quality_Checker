"""Export-local ordinary-file opens, bounded integrity checks and fsync helpers.

POSIX worker/storage support is explicit: directory-relative O_NOFOLLOW opens
avoid traversing symlinks, and O_NONBLOCK prevents a substituted FIFO from hanging.
No directory creation occurs at import, acceptance, metadata reads or listing.
"""
from contextlib import contextmanager
from hashlib import sha256
import os
from pathlib import Path
import stat

from ..core.export import ExportDependencyError, ExportLimitError
from ..models.export import Artifact

CHUNK_BYTES = 65_536


def _parts(key):
    if (type(key) is not str or not key or '\\' in key or '\x00' in key
            or any(p in ('', '.', '..') for p in key.split('/'))):
        raise ExportDependencyError("Invalid private export file identity.")
    return key.split('/')


@contextmanager
def directory_fd(path: Path, *, create=False, private=False):
    if not path.is_absolute():
        raise ExportDependencyError("Export filesystem root is not absolute.")
    descriptor = None
    try:
        descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        for part in path.parts[1:]:
            if part in ('', '.', '..'):
                raise ExportDependencyError("Invalid export directory.")
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        if private and (metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077):
            raise ExportDependencyError("Export directory must be private to the service account.")
        yield descriptor
    except OSError as error:
        if isinstance(error, ExportDependencyError):
            raise
        raise ExportDependencyError("Private export storage is unavailable.") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def create_attempt(root, token):
    # The token is validated by ExportClaim. Never reuse a directory on restart.
    if len(token) != 32 or any(c not in '0123456789abcdef' for c in token):
        raise ExportDependencyError("Invalid export attempt identity.")
    with directory_fd(root, create=True, private=True) as parent:
        try:
            os.mkdir(token, 0o700, dir_fd=parent)
            os.fsync(parent)
        except OSError as error:
            raise ExportDependencyError("Cannot allocate a unique export attempt.") from error
    return root / token


@contextmanager
def open_relative(root, key):
    parts = _parts(key)
    descriptor = None
    try:
        with directory_fd(root) as parent:
            current = os.dup(parent)
            try:
                for part in parts[:-1]:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                    os.close(current)
                    current = child
                descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
            finally:
                os.close(current)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ExportDependencyError("Export source must be an ordinary unlinked-copy file.")
        with os.fdopen(descriptor, 'rb') as handle:
            descriptor = None
            yield handle
    except OSError as error:
        raise ExportDependencyError("A private export file is unavailable.") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def fingerprint(metadata):
    return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns,
            metadata.st_ctime_ns, metadata.st_nlink)


def verify_handle(handle, *, size, digest, limit, check=lambda: None):
    """Verify bounded actual bytes, leaving the SAME opened descriptor at offset 0."""
    before = os.fstat(handle.fileno())
    if size > limit:
        raise ExportLimitError()
    if before.st_size != size or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ExportDependencyError("Export file identity does not match its registration.")
    actual, hashed = 0, sha256()
    while True:
        check()
        chunk = handle.read(min(CHUNK_BYTES, limit - actual + 1))
        if not chunk:
            break
        actual += len(chunk)
        if actual > limit or actual > size:
            raise ExportLimitError()
        hashed.update(chunk)
    check()
    if (actual != size or hashed.hexdigest() != digest
            or fingerprint(before) != fingerprint(os.fstat(handle.fileno()))):
        raise ExportDependencyError("Export file integrity verification failed.")
    handle.seek(0)


def identify(root, key, limit, *, check=lambda: None):
    with open_relative(root, key) as handle:
        metadata = os.fstat(handle.fileno())
        if metadata.st_size < 1 or metadata.st_size > limit:
            raise ExportLimitError()
        h, n = sha256(), 0
        while True:
            check()
            chunk = handle.read(min(CHUNK_BYTES, limit - n + 1))
            if not chunk:
                break
            n += len(chunk)
            if n > limit:
                raise ExportLimitError()
            h.update(chunk)
        if n != metadata.st_size or fingerprint(metadata) != fingerprint(os.fstat(handle.fileno())):
            raise ExportDependencyError("Export artifact changed during registration.")
    return Artifact(key=key, size_bytes=n, sha256=h.hexdigest())


def write_private(directory, filename, content):
    with directory_fd(directory, private=True) as parent:
        descriptor = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.fsync(parent)
