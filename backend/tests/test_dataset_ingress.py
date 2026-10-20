"""Authored ASGI ingress and multipart cleanup scenarios. NOT executed."""
import asyncio
import json
import tempfile

import pytest
from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response
from starlette import formparsers

from backend.app.routers.dataset_ingress import DatasetUploadIngress, _upload_path
from backend.app.routers.dataset_multipart import parsed_dataset_upload
from backend.app.core.dataset_http import DatasetRequestError
from backend.app.core.dataset_import import ImportConfigurationError, UploadTooLargeError
from backend.tests.dataset_http_fakes import http_settings


def scope(path="/api/v1/datasets", *, method="POST", headers=(), root_path=""):
    return {"type": "http", "method": method, "path": path, "raw_path": path.encode(),
            "root_path": root_path, "query_string": b"", "headers": list(headers),
            "scheme": "http", "http_version": "1.1", "server": ("test", 80), "client": ("local", 1)}


def invoke(settings, messages, *, headers=(), path="/api/v1/datasets", method="POST", factory=None):
    sent, consumed = [], []
    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}
    async def send(message):
        sent.append(message)
    async def downstream(current, receive, send):
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return
            consumed.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        await Response(status_code=204)(current, receive, send)
    application = DatasetUploadIngress(downstream, settings_factory=(lambda: settings) if factory is None else factory)
    asyncio.run(application(scope(path, headers=headers, method=method), receive, send))
    return sent, consumed


def status(sent):
    return next(message["status"] for message in sent if message["type"] == "http.response.start")


def test_chunked_without_content_length_is_measured_before_forwarding(tmp_path):
    settings = http_settings(tmp_path, max_archive_bytes=1)
    sent, consumed = invoke(settings, [
        {"type": "http.request", "body": b"a"*32768, "more_body": True},
        {"type": "http.request", "body": b"b"*32770, "more_body": False},
    ])
    assert status(sent) == 413 and len(consumed) == 1
    body = json.loads(b"".join(message.get("body", b"") for message in sent))
    assert body["error"]["code"] == "UPLOAD_TOO_LARGE" and body["error"]["request_id"]


def test_untruthful_small_content_length_does_not_disable_counter(tmp_path):
    settings = http_settings(tmp_path, max_archive_bytes=1)
    sent, _ = invoke(settings, [{"type": "http.request", "body": b"x"*(settings.body_limit+1)}],
                     headers=[(b"content-length", b"1")])
    assert status(sent) == 413


@pytest.mark.parametrize("headers", [[(b"content-length", b"-1")], [(b"content-length", b"1,1")],
                                      [(b"content-length", b"1"), (b"content-length", b"1")]])
def test_invalid_or_duplicate_length_headers_are_safe_422(tmp_path, headers):
    sent, consumed = invoke(http_settings(tmp_path), [], headers=headers)
    assert status(sent) == 422 and consumed == []


def test_declared_oversize_rejected_without_reading_body(tmp_path):
    settings = http_settings(tmp_path)
    sent, consumed = invoke(settings, [], headers=[(b"content-length", str(settings.body_limit+1).encode())])
    assert status(sent) == 413 and consumed == []


@pytest.mark.parametrize("path,method", [("/api/v1/rulebooks/extractions", "POST"), ("/api/v1/datasets", "GET"),
                                         ("/api/v1/jobs/job/cancel", "POST")])
def test_guard_does_not_load_upload_settings_for_other_operations(tmp_path, path, method):
    def forbidden():
        pytest.fail("Unrelated JSON/read routes must not load upload policy.")
    sent, consumed = invoke(http_settings(tmp_path), [{"type": "http.request", "body": b"body"}],
                            path=path, method=method, factory=forbidden)
    assert status(sent) == 204 and consumed == [b"body"]


def test_known_settings_failure_is_safe_503_before_request_body(tmp_path):
    def unavailable():
        raise ImportConfigurationError("PRIVATE_ROOT")
    sent, consumed = invoke(http_settings(tmp_path), [], factory=unavailable)
    assert status(sent) == 503 and consumed == []
    assert b"PRIVATE_ROOT" not in b"".join(message.get("body", b"") for message in sent)


@pytest.mark.parametrize("path,root,expected", [("/proxy/api/v1/datasets", "/proxy", True),
    ("/api/v1/datasets", "/proxy", True), ("/api/v1/datasets/", "", True),
    ("/api/v1/datasets-extra", "", False), ("/other/api/v1/datasets", "", False)])
def test_guard_path_mount_and_slash_matching(path, root, expected):
    assert _upload_path(scope(path, root_path=root)) is expected


def spy_spools(monkeypatch):
    created = []
    def spool(*args, **kwargs):
        handle = tempfile.SpooledTemporaryFile(*args, **kwargs)
        created.append(handle)
        return handle
    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", spool)
    return created


def partial_file():
    return (b'--boundary\r\nContent-Disposition: form-data; name="archive"; filename="a.zip"\r\n'
            b'Content-Type: application/zip\r\n\r\npartial')


def test_disconnect_closes_even_unfinished_spooled_file(tmp_path, monkeypatch):
    created = spy_spools(monkeypatch)
    messages = [{"type": "http.request", "body": partial_file(), "more_body": True}, {"type": "http.disconnect"}]
    async def run():
        async def receive():
            return messages.pop(0)
        request = Request(scope(headers=[(b"content-type", b"multipart/form-data; boundary=boundary")]), receive)
        with pytest.raises(ClientDisconnect):
            async with parsed_dataset_upload(request, http_settings(tmp_path)):
                pytest.fail("Disconnected upload cannot be accepted.")
    asyncio.run(run())
    assert created and all(handle.closed for handle in created)


def test_ingress_exception_during_partial_file_closes_spool(tmp_path, monkeypatch):
    created = spy_spools(monkeypatch)
    async def run():
        delivered = False
        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": partial_file(), "more_body": True}
            raise UploadTooLargeError()
        request = Request(scope(headers=[(b"content-type", b"multipart/form-data; boundary=boundary")]), receive)
        with pytest.raises(UploadTooLargeError):
            async with parsed_dataset_upload(request, http_settings(tmp_path)):
                pytest.fail("Oversized upload cannot be accepted.")
    asyncio.run(run())
    assert created and all(handle.closed for handle in created)


def test_task_cancellation_closes_partial_file_under_shield(tmp_path, monkeypatch):
    created = spy_spools(monkeypatch)
    async def run():
        blocked = asyncio.Event()
        delivered = False
        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": partial_file(), "more_body": True}
            blocked.set()
            await asyncio.Event().wait()
        request = Request(scope(headers=[(b"content-type", b"multipart/form-data; boundary=boundary")]), receive)
        async def parsing():
            async with parsed_dataset_upload(request, http_settings(tmp_path)):
                pytest.fail("Cancelled upload cannot be accepted.")
        task = asyncio.create_task(parsing())
        try:
            await asyncio.wait_for(blocked.wait(), timeout=2)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2)
    asyncio.run(run())
    assert created and all(handle.closed for handle in created)


def test_incomplete_final_boundary_is_422_and_closes_file(tmp_path, monkeypatch):
    created = spy_spools(monkeypatch)
    async def run():
        messages = [{"type": "http.request", "body": partial_file(), "more_body": False}]
        async def receive():
            return messages.pop(0)
        request = Request(scope(headers=[(b"content-type", b"multipart/form-data; boundary=boundary")]), receive)
        with pytest.raises(DatasetRequestError):
            async with parsed_dataset_upload(request, http_settings(tmp_path)):
                pytest.fail("Unterminated multipart cannot be accepted.")
    asyncio.run(run())
    assert created and all(handle.closed for handle in created)
