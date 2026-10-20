"""Authored dependency-override HTTP regressions. NOT collected or executed."""
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError
import pytest

from backend.app.main import create_app
from backend.app.routers import datasets as routes
from backend.app.core.dataset_import import ImportAcceptanceError, ImportOutcomeUnresolved
from backend.app.storage.dataset_repository import DatasetStorageError
from backend.tests.dataset_http_fakes import Catalogue, UploadService, dataset, http_settings


@pytest.fixture
def api(tmp_path):
    settings, catalogue, service = http_settings(tmp_path), Catalogue(), UploadService()
    application = create_app(dataset_upload_settings_factory=lambda: settings)
    application.dependency_overrides[routes.get_dataset_repository] = lambda: catalogue
    application.dependency_overrides[routes.get_dataset_import_repository] = lambda: object()
    application.dependency_overrides[routes.get_dataset_upload_service] = lambda: service
    with TestClient(application, raise_server_exceptions=False) as client:
        yield client, catalogue, service, application


def submit(client, *, data=None, files=None, **kwargs):
    return client.post("/api/v1/datasets", data={"name": "Example", "format": "penn_fudan"} if data is None else data,
                       files={"archive": ("../../ignored-client-name.zip", b"not-inspected-in-route", "application/zip")} if files is None else files,
                       **kwargs)


def test_acceptance_returns_public_queued_pair_closes_handle_without_import(api):
    client, catalogue, service, _ = api
    response = submit(client)
    assert response.status_code == 202
    body = response.json()
    assert set(body) == {"dataset", "job"}
    assert body["dataset"]["status"] == "importing" and body["dataset"]["sample_count"] is None
    assert body["job"]["status"] == "queued" and body["job"]["progress"]["total"] is None
    assert service.calls == [(b"not-inspected-in-route", "Example", "penn_fudan")]
    assert all(handle.closed for handle in service.handles)
    assert catalogue.calls == [] and "ignored-client-name" not in response.text
    for forbidden in ("archive_key", "archive_sha256", "claim_token", "worker_id", "asset_root", "source_root"):
        assert forbidden not in response.text


@pytest.mark.parametrize("data", [{"format": "penn_fudan"}, {"name": "Example"},
    {"name": " ", "format": "penn_fudan"}, {"name": "x\x00y", "format": "penn_fudan"},
    {"name": "x"*256, "format": "penn_fudan"}, {"name": "x", "format": "coco"},
    *[{"name": "x", "format": "penn_fudan", key: "forbidden"} for key in
      ("dataset_id", "revision", "root", "policy", "job_id", "worker_id", "status")]])
def test_strict_form_fields_return_safe_422_without_acceptance(api, data):
    client, _, service, _ = api
    response = submit(client, data=data)
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert service.calls == []


@pytest.mark.parametrize("parts", [
    [("archive", ("a.zip", b"zip", "application/zip")), ("archive", ("b.zip", b"zip", "application/zip")),
     ("name", (None, "x")), ("format", (None, "penn_fudan"))],
    [("archive", ("a.zip", b"zip", "application/zip")), ("name", (None, "x")),
     ("name", (None, "y")), ("format", (None, "penn_fudan"))],
    [("archive", (None, "not a file")), ("name", (None, "x")), ("format", (None, "penn_fudan"))],
    [("archive", ("a.zip", b"zip", "application/zip")), ("name", ("name.txt", b"x")),
     ("format", (None, "penn_fudan"))],
])
def test_duplicate_fields_and_wrong_part_types_are_rejected(api, parts):
    client, _, service, _ = api
    response = client.post("/api/v1/datasets", files=parts)
    assert response.status_code == 422 and service.calls == []


@pytest.mark.parametrize("representation", ["application/json", "application/x-www-form-urlencoded", "application/x-tar"])
def test_non_multipart_representation_is_415(api, representation):
    client, _, service, _ = api
    response = client.post("/api/v1/datasets", content=b"unsupported", headers={"Content-Type": representation})
    assert response.status_code == 415 and response.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
    assert service.calls == []


def test_unsupported_archive_mime_and_whole_body_encoding_are_415(api):
    client, _, service, _ = api
    assert submit(client, files={"archive": ("a.rar", b"rar", "application/x-rar-compressed")}).status_code == 415
    assert submit(client, headers={"Content-Encoding": "gzip"}).status_code == 415
    assert service.calls == []


def test_actual_file_cap_is_enforced_during_parsing_before_acceptance(api):
    client, _, service, _ = api
    response = submit(client, files={"archive": ("a.zip", b"x"*257, "application/zip")})
    assert response.status_code == 413 and response.json()["error"]["code"] == "UPLOAD_TOO_LARGE"
    assert service.calls == []


@pytest.mark.parametrize("failure", [ImportAcceptanceError("PRIVATE_PATH"), ImportOutcomeUnresolved("PRIVATE_KEY"), DatasetStorageError("PRIVATE_SQL")])
def test_narrow_acceptance_failures_are_safe_503_not_false_202(api, failure):
    client, _, service, _ = api
    service.failure = failure
    response = submit(client)
    assert response.status_code == 503 and response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "PRIVATE" not in response.text and response.json()["error"]["request_id"]
    assert all(handle.closed for handle in service.handles)


@pytest.mark.parametrize("failure", [ValueError("PRIVATE_INTERNAL_VALUE"), RuntimeError("PRIVATE_BUG")])
def test_unrelated_internal_errors_remain_safe_500(api, failure):
    client, _, service, _ = api
    service.failure = failure
    response = submit(client)
    assert response.status_code == 500 and response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "PRIVATE" not in response.text and all(handle.closed for handle in service.handles)


def test_internal_pydantic_and_response_errors_remain_500(api):
    client, catalogue, service, _ = api
    try:
        TypeAdapter(int).validate_python("PRIVATE_INVALID_INTERNAL")
    except ValidationError as error:
        service.failure = error
    assert submit(client).status_code == 500
    catalogue.get_dataset = lambda identity: {"unexpected": "PRIVATE_INVALID_RESPONSE"}
    response = client.get("/api/v1/datasets/dataset_a")
    assert response.status_code == 500 and "PRIVATE" not in response.text


@pytest.mark.parametrize("state", ["importing", "ready", "invalid", "failed", "cancelled"])
def test_all_dataset_states_are_browsable_without_transition(api, state):
    client, catalogue, _, _ = api
    catalogue.value = dataset(status=state)
    response = client.get("/api/v1/datasets/dataset_a")
    assert response.status_code == 200 and response.json()["status"] == state
    assert client.get("/api/v1/datasets").json()["items"][0] == response.json()
    assert client.get("/api/v1/datasets/dataset_a/samples").status_code == 200
    assert catalogue.value.status.value == state


def test_current_and_exact_revision_reads_pin_all_asset_links(api):
    client, _, _, _ = api
    url = "/api/v1/datasets/dataset_a/samples/sample_a"
    for suffix, revision in [("", 2), ("?revision=1", 1), ("?revision=2", 2)]:
        body = client.get(url+suffix).json()
        assert body["revision"] == revision and body["issues"]
        assert body["assets"]["image"]["url"].endswith(f"?revision={revision}")
        assert body["assets"]["thumbnail"]["revision"] == revision
        assert body["assets"]["mask"] is None
    for path in ["/api/v1/datasets/missing", "/api/v1/datasets/missing/samples",
                 "/api/v1/datasets/dataset_a/samples/missing", url+"?revision=99"]:
        response = client.get(path)
        assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize("url", ["/api/v1/datasets?limit=0", "/api/v1/datasets?limit=101",
    "/api/v1/datasets?offset=-1", "/api/v1/datasets?offset=9223372036854775808",
    "/api/v1/datasets?limit=true", "/api/v1/datasets?limit=1&limit=2", "/api/v1/datasets?root=private",
    "/api/v1/datasets/%00", "/api/v1/datasets/%20",
    "/api/v1/datasets/dataset_a/samples/sample_a?revision=0",
    "/api/v1/datasets/dataset_a/samples/sample_a?revision=2147483648",
    "/api/v1/datasets/dataset_a/samples/sample_a?revision=1&revision=2"])
def test_invalid_read_parameters_are_422(api, url):
    response = api[0].get(url)
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_default_and_beyond_end_pagination(api):
    client, catalogue, _, _ = api
    first = client.get("/api/v1/datasets").json()
    assert first["limit"] == 20 and first["offset"] == 0 and first["total"] == 1
    last = client.get("/api/v1/datasets?limit=1&offset=99").json()
    assert last["items"] == [] and last["next_offset"] is None and last["total"] == 1


@pytest.mark.parametrize('content_type,body,expected', [
    ('multipart/form-data', b'incomplete', 422),
    ('multipart/form-data; boundary=a; boundary=b', b'incomplete', 422),
    ('multipart/form-data; boundary=abc; charset=latin-1', b'incomplete', 415),
    ('multipart/form-data; boundary=' + 'x'*71, b'incomplete', 422),
    ('multipart/form-data; boundary=b',
     b'--b\r\nContent-Disposition: form-data; name="name"\r\n\r\n\xff\r\n--b--\r\n', 422),
    ('multipart/form-data; boundary=b',
     b'--b\r\nContent-Disposition: form-data; name="name"\r\nContent-Disposition: form-data\r\n\r\nx\r\n--b--\r\n', 422),
    ('multipart/form-data; boundary=b',
     b'--b\r\nContent-Disposition: form-data; name="name"\r\nX-Private: forbidden\r\n\r\nx\r\n--b--\r\n', 422),
    ('multipart/form-data; boundary=b',
     b'--b\r\nContent-Disposition: form-data; name="' + b'x'*8193 + b'"\r\n\r\nx\r\n--b--\r\n', 422),
])
def test_malformed_multipart_headers_utf8_and_header_budget_are_safe(api, content_type, body, expected):
    client, _, service, _ = api
    response = client.post('/api/v1/datasets', content=body, headers={'Content-Type': content_type})
    assert response.status_code == expected and service.calls == []
    assert 'forbidden' not in response.text
