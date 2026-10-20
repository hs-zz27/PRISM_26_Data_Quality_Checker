"""Authored bridge regressions; no database required, not executed."""
from io import BytesIO
import pytest

from backend.app.core.dataset_http import DatasetRequestError
from backend.app.core.dataset_import import ImportAcceptanceError
from backend.app.services.dataset_http import DatasetUploadService
from backend.tests.dataset_http_fakes import http_settings


class Ledger:
    def __init__(self):
        self.request = None
    def enqueue(self, request, **kwargs):
        self.request = request
        return request


def test_stream_rewinds_copies_durably_and_caller_retains_handle_ownership(tmp_path):
    settings = http_settings(tmp_path).imports
    ledger = Ledger()
    stream = BytesIO(b'original-byte-stream')
    stream.seek(5)
    request = DatasetUploadService(ledger, settings).submit(stream, name='Example', format='penn_fudan')
    assert (settings.root / request.archive_key).read_bytes() == b'original-byte-stream'
    assert not stream.closed
    stream.close()


def test_empty_file_is_client_422_category_before_staging(tmp_path):
    settings = http_settings(tmp_path).imports
    ledger = Ledger()
    with pytest.raises(DatasetRequestError):
        DatasetUploadService(ledger, settings).submit(BytesIO(), name='Example', format='penn_fudan')
    assert ledger.request is None and not settings.root.exists()


def test_spool_read_failure_is_safe_dependency_failure(tmp_path):
    class Broken:
        def seek(self, offset):
            raise OSError('PRIVATE_SPOOL')
    with pytest.raises(ImportAcceptanceError) as caught:
        DatasetUploadService(Ledger(), http_settings(tmp_path).imports).submit(Broken(), name='x', format='penn_fudan')
    assert 'PRIVATE_SPOOL' not in str(caught.value)
