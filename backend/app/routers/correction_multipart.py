"""Correction-local adapter based on the current bounded dataset parser.

Tracks raw spooled handles so disconnect, cancellation and byte-limit exceptions
also close files; Starlette's normal MultiPartException-only cleanup is not enough.
Framework parser internals are localized here and require local compatibility tests.
"""
from contextlib import asynccontextmanager
import logging
import re

import anyio
from starlette.concurrency import run_in_threadpool
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import Request
from starlette.datastructures import UploadFile

from ..core.dataset_http import (
    DatasetHttpDependencyError, DatasetRequestError, DatasetUnsupportedMediaError,
)
from ..core.dataset_import import UploadTooLargeError

logger = logging.getLogger(__name__)
_BOUNDARY = re.compile(rb"[0-9A-Za-z'()+_,./:=? -]{1,70}")


class _Parser(MultiPartParser):
    def __init__(self, headers, stream, settings):
        super().__init__(headers, stream, max_files=1, max_fields=1,
                         max_part_size=settings.multipart.scalar_bytes)
        self.settings = settings
        self.spool_max_size = settings.multipart.spool_bytes
        self.ended = False
        self._header_bytes, self._header_name, self._header_names = 0, bytearray(), set()
        self._part_bytes = 0

    def on_part_begin(self):
        self._header_bytes, self._header_name, self._header_names = 0, bytearray(), set()
        self._part_bytes = 0
        super().on_part_begin()

    def _header_budget(self, length):
        self._header_bytes += length
        if self._header_bytes > self.settings.multipart.part_header_bytes:
            raise DatasetRequestError("multipart")

    def on_header_field(self, data, start, end):
        self._header_budget(end-start)
        self._header_name.extend(data[start:end])
        super().on_header_field(data, start, end)

    def on_header_value(self, data, start, end):
        self._header_budget(end-start)
        super().on_header_value(data, start, end)

    def on_header_end(self):
        name = bytes(self._header_name).lower()
        if name not in {b"content-disposition", b"content-type"} or name in self._header_names:
            raise DatasetRequestError("multipart")
        self._header_names.add(name)
        self._header_name.clear()
        super().on_header_end()

    def on_headers_finished(self):
        from python_multipart.multipart import parse_options_header
        raw = self._current_part.content_disposition
        if b"content-disposition" not in self._header_names:
            raise DatasetRequestError("multipart")
        # Reject ambiguous parameter identities before a file handle is allocated.
        for parameter in (b"name", b"filename"):
            if len(re.findall(rb";\s*" + parameter + rb"\s*=", raw, flags=re.I)) > 1:
                raise DatasetRequestError("multipart")
        try:
            disposition, options = parse_options_header(raw)
        except (ValueError, UnicodeError) as error:
            raise DatasetRequestError("multipart") from error
        name = options.get(b"name")
        if (disposition.lower() != b"form-data" or name not in {b"mask", b"expected_revision"}
                or (b"filename" in options) != (name == b"mask")):
            raise DatasetRequestError("multipart")
        super().on_headers_finished()

    def on_part_data(self, data, start, end):
        self._part_bytes += end-start
        limit = (self.settings.policy.max_mask_bytes if self._current_part.file is not None
                 else self.settings.multipart.scalar_bytes)
        if self._part_bytes > limit:
            if self._current_part.file is not None:
                raise UploadTooLargeError()
            raise DatasetRequestError("multipart")
        super().on_part_data(data, start, end)

    def on_part_end(self):
        if self._current_part.file is None:
            try:
                bytes(self._current_part.data).decode("utf-8", errors="strict")
            except UnicodeError as error:
                raise DatasetRequestError("multipart") from error
        super().on_part_end()

    def on_end(self):
        self.ended = True
        super().on_end()


def _media(request):
    try:
        from python_multipart.multipart import parse_options_header
    except ImportError as error:
        raise DatasetHttpDependencyError("The pinned multipart parser is required.") from error
    if len(request.headers.getlist("content-type")) != 1:
        raise DatasetUnsupportedMediaError()
    raw = request.headers["content-type"]
    if len(raw) > 512:
        raise DatasetRequestError("headers")
    if len(request.headers.getlist("content-encoding")) > 1:
        raise DatasetUnsupportedMediaError()
    if request.headers.get("content-encoding", "identity").strip().lower() != "identity":
        raise DatasetUnsupportedMediaError()
    try:
        media, options = parse_options_header(raw)
    except (ValueError, UnicodeError) as error:
        raise DatasetRequestError("headers") from error
    if media.lower() != b"multipart/form-data":
        raise DatasetUnsupportedMediaError()
    for parameter in ("boundary", "charset"):
        if len(re.findall(r";\s*" + parameter + r"\s*=", raw, flags=re.I)) > 1:
            raise DatasetRequestError("headers")
    boundary = options.get(b"boundary", b"")
    if not _BOUNDARY.fullmatch(boundary) or boundary.endswith(b" "):
        raise DatasetRequestError("multipart")
    if options.get(b"charset", b"utf-8").lower() not in {b"utf-8", b"utf8"}:
        raise DatasetUnsupportedMediaError()


async def _stream(request):
    async for data in request.stream():
        # Bound parser callback/write-batch allocation even if ASGI supplies a big chunk.
        for offset in range(0, len(data), 65536):
            yield data[offset:offset+65536]


@asynccontextmanager
async def parsed_mask_upload(request: Request, settings):
    from ..core.correction import CorrectionError
    from .correction_ingress import CorrectionBodyLimit
    try:
        _media(request)
    except DatasetUnsupportedMediaError as error:
        raise CorrectionError('UNSUPPORTED_MEDIA_TYPE') from error
    from python_multipart.exceptions import MultipartParseError
    parser = _Parser(request.headers, _stream(request), settings)
    try:
        try:
            form = await parser.parse()
            if not parser.ended:
                raise CorrectionError('VALIDATION_ERROR')
            values = list(form.multi_items())
            if len(values)!=2 or {key for key,_ in values}!={'mask','expected_revision'}:
                raise CorrectionError('VALIDATION_ERROR')
            mask, revision = form['mask'], form['expected_revision']
            if (not isinstance(mask, UploadFile) or type(revision) is not str
                    or not re.fullmatch(r'[0-9]{1,10}', revision)):
                raise CorrectionError('VALIDATION_ERROR')
            revision = int(revision)
            if not 1<=revision<=2147483646:
                raise CorrectionError('VALIDATION_ERROR')
            yield mask, revision
        except (MultiPartException, MultipartParseError, DatasetRequestError) as error:
            raise CorrectionError('VALIDATION_ERROR') from error
        except (UploadTooLargeError, CorrectionBodyLimit) as error:
            raise CorrectionError('UPLOAD_TOO_LARGE') from error
        except OSError as error:
            raise CorrectionError('DEPENDENCY_UNAVAILABLE') from error
    finally:
        with anyio.CancelScope(shield=True):
            for handle in parser._files_to_close_on_error:
                try:
                    await run_in_threadpool(handle.close)
                except OSError:
                    logger.warning('Correction request spool close failed')
