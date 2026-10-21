"""Verify registered artifacts before headers; stream the same opened descriptor."""
import time

from starlette.responses import StreamingResponse

from ..core.export import ExportDependencyError, ExportLimitError
from ..models.export import ExportManifest
from .export_archive import manifest_bytes
from .export_files import CHUNK_BYTES, open_relative, verify_handle


class VerifiedZipResponse(StreamingResponse):
    def __init__(self, handle, manager, size):
        self._handle, self._manager = handle, manager
        self._closed = False
        def chunks():
            remaining = size
            while remaining:
                content = handle.read(min(CHUNK_BYTES, remaining))
                if not content:
                    # Headers may already have been sent: abort, never invent a
                    # new JSON envelope or silently deliver a truncated archive.
                    raise OSError('Export download was interrupted.')
                remaining -= len(content)
                yield content
        super().__init__(chunks(), media_type='application/zip', headers={
            'Content-Length': str(size),
            'Content-Disposition': 'attachment; filename="reviewed-dataset.zip"',
            'X-Content-Type-Options': 'nosniff',
            'Cache-Control': 'no-store',
        })

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.close()

    def close(self):
        if not self._closed:
            self._closed = True
            self._manager.__exit__(None, None, None)


class ExportDownloadService:
    def __init__(self, repository, settings):
        self.repository, self.settings = repository, settings

    @staticmethod
    def _check(deadline):
        if time.monotonic() >= deadline:
            raise ExportDependencyError('Export artifact verification exceeded its time bound.')

    def get_manifest(self, export_id):
        frozen, artifacts = self.repository.completed_artifacts(export_id)
        artifact = artifacts.manifest
        deadline = time.monotonic() + frozen.policy.max_seconds
        try:
            with open_relative(self.settings.root, artifact.key) as handle:
                verify_handle(handle, size=artifact.size_bytes, digest=artifact.sha256,
                              limit=frozen.policy.max_metadata_bytes,
                              check=lambda: self._check(deadline))
                content = handle.read(frozen.policy.max_metadata_bytes + 1)
            self._check(deadline)
            if content != manifest_bytes(frozen):
                raise ExportDependencyError('Completed manifest differs from frozen selection.')
            return ExportManifest.model_validate_json(content)
        except (ValueError, ExportLimitError) as error:
            raise ExportDependencyError('Completed manifest is unavailable or invalid.') from error

    def download(self, export_id):
        frozen, artifacts = self.repository.completed_artifacts(export_id)
        artifact = artifacts.archive
        deadline = time.monotonic() + frozen.policy.max_seconds
        manager = open_relative(self.settings.root, artifact.key)
        handle = manager.__enter__()
        try:
            verify_handle(handle, size=artifact.size_bytes, digest=artifact.sha256,
                          limit=frozen.policy.max_archive_bytes,
                          check=lambda: self._check(deadline))
            return VerifiedZipResponse(handle, manager, artifact.size_bytes)
        except BaseException as error:
            manager.__exit__(type(error), error, error.__traceback__)
            if isinstance(error, ExportLimitError):
                raise ExportDependencyError('Completed archive integrity verification failed.') from error
            raise
