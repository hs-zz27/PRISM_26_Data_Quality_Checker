"""ASGI byte guard scoped ONLY to POST /api/v1/datasets (and slash redirect).

A body counter is not a socket/header/time/concurrency limit. Deploy behind the
specified trusted ingress limits; never mistake Content-Length for measurement.
"""
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from ..core.dataset_http import DatasetHttpSettings, DatasetRequestError, load_dataset_http_settings
from ..core.dataset_import import ImportConfigurationError, UploadTooLargeError
from .errors import _response

SETTINGS_SCOPE_KEY = "prism.dataset_http_settings"


def _upload_path(scope):
    path = scope.get("path", "")
    root = scope.get("root_path", "")
    if root and (path == root or path.startswith(root.rstrip("/") + "/")):
        path = path[len(root.rstrip("/")):]
    return path in {"/api/v1/datasets", "/api/v1/datasets/"}


class DatasetUploadIngress:
    def __init__(self, app, *, settings_factory=None):
        self.app = app
        self.settings_factory = load_dataset_http_settings if settings_factory is None else settings_factory

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or not _upload_path(scope):
            await self.app(scope, receive, send)
            return
        started = False
        async def guarded_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                # Early rejection need not drain a potentially hostile/slow body.
                # Do not send hop-by-hop Connection headers over HTTP/2.
                if message["status"] >= 400 and scope.get("http_version") in {"1.0", "1.1"}:
                    message = {**message, "headers": [(k, v) for k, v in message.get("headers", [])
                                                      if k.lower() != b"connection"] + [(b"connection", b"close")]}
            await send(message)
        try:
            effective = await run_in_threadpool(self.settings_factory)
            # Revalidate a factory/override's mutable nested model, not its class alone.
            effective = DatasetHttpSettings.model_validate(effective.model_dump())
            scope[SETTINGS_SCOPE_KEY] = effective
            lengths = [value for key, value in scope.get("headers", []) if key.lower() == b"content-length"]
            if len(lengths) > 1:
                raise DatasetRequestError("headers")
            if lengths:
                value = lengths[0]
                if not value or len(value) > 20 or any(character not in b"0123456789" for character in value):
                    raise DatasetRequestError("headers")
                if int(value) > effective.body_limit:
                    raise UploadTooLargeError()
            consumed = 0
            async def measured_receive():
                nonlocal consumed
                message = await receive()
                if message["type"] == "http.request":
                    consumed += len(message.get("body", b""))
                    if consumed > effective.body_limit:
                        raise UploadTooLargeError()
                return message
            await self.app(scope, measured_receive, guarded_send)
        except UploadTooLargeError:
            if started:
                raise  # Never start a second HTTP response after acceptance.
            await _response(413, "UPLOAD_TOO_LARGE", "The dataset upload exceeds the configured byte limit.")(scope, receive, guarded_send)
        except DatasetRequestError:
            if started:
                raise
            await _response(422, "VALIDATION_ERROR", "One or more dataset request fields are invalid.")(scope, receive, guarded_send)
        except ImportConfigurationError:
            if started:
                raise
            await _response(503, "DEPENDENCY_UNAVAILABLE", "Dataset upload configuration is unavailable.")(scope, receive, guarded_send)
        except ClientDisconnect:
            # The multipart context closes all opened handles before propagation.
            return
