"""HTTP delivery coverage for revision-pinned sample assets."""

from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from backend.app.core.config import AssetStorageConfigurationError
from backend.app.main import create_app
from backend.app.models.asset_manifest import AssetKind
from backend.app.routers.assets import get_sample_asset_service
from backend.app.services.asset_preview import PreviewUnavailableError
from backend.app.services.asset_retrieval import PublicAssetKind, RetrievedAsset, SampleAssetService
from backend.app.storage.workflow_repository import (
    AssetIntegrityError,
    AssetReadLimitError,
    AssetUnavailableError,
    SampleAssetManifestNotFoundError,
)


@pytest.fixture
def api():
    app = create_app()
    service = Mock(spec=SampleAssetService)
    app.dependency_overrides[get_sample_asset_service] = lambda: service
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, service


def test_all_public_kinds_return_binary_png_and_forward_exact_revision(api):
    client, service = api
    service.retrieve.return_value = RetrievedAsset(b"\x89PNG\r\nasset", "image/png")

    for kind in ("image", "mask", "mask_preview", "thumbnail"):
        response = client.get(
            f"/api/v1/datasets/dataset_1/samples/sample_1/assets/{kind}",
            params={"revision": 2},
        )
        assert response.status_code == 200
        assert response.content == b"\x89PNG\r\nasset"
        assert response.headers["content-type"] == "image/png"
        assert response.headers["x-content-type-options"] == "nosniff"

    assert [
        (call.args[0], call.args[1], call.args[2], call.args[3])
        for call in service.retrieve.call_args_list
    ] == [
        ("dataset_1", "sample_1", 2, PublicAssetKind.IMAGE),
        ("dataset_1", "sample_1", 2, PublicAssetKind.MASK),
        ("dataset_1", "sample_1", 2, PublicAssetKind.MASK_PREVIEW),
        ("dataset_1", "sample_1", 2, PublicAssetKind.THUMBNAIL),
    ]


@pytest.mark.parametrize("query", ["", "revision=0", "revision=-1", "revision=abc", "revision=1.5"])
def test_missing_or_invalid_revision_returns_validation_envelope_without_delivery(api, query):
    client, service = api
    suffix = "" if not query else f"?{query}"
    response = client.get(
        f"/api/v1/datasets/dataset_1/samples/sample_1/assets/image{suffix}"
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["request_id"]
    service.retrieve.assert_not_called()


def test_unsupported_asset_kind_returns_validation_envelope(api):
    client, service = api
    response = client.get(
        "/api/v1/datasets/dataset_1/samples/sample_1/assets/annotation_text",
        params={"revision": 2},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    service.retrieve.assert_not_called()


@pytest.mark.parametrize("error,status,code", [
    (SampleAssetManifestNotFoundError("private-dataset", "private-sample", 2), 404, "NOT_FOUND"),
    (AssetUnavailableError("private-dataset", "private-sample", 2, AssetKind.RAW_MASK, "missing"), 404, "NOT_FOUND"),
    (PreviewUnavailableError("private preview path"), 422, "ASSET_NOT_RENDERABLE"),
    (AssetReadLimitError("private size detail"), 422, "ASSET_TOO_LARGE"),
    (AssetIntegrityError("private filesystem path and SQL detail"), 503, "DEPENDENCY_UNAVAILABLE"),
    (RuntimeError("private SQL and filesystem detail"), 500, "INTERNAL_ERROR"),
])
def test_asset_errors_use_safe_json_envelopes(api, error, status, code):
    client, service = api
    service.retrieve.side_effect = error

    response = client.get(
        "/api/v1/datasets/dataset_1/samples/sample_1/assets/image",
        params={"revision": 2},
    )

    assert response.status_code == status
    body = response.json()
    assert body["error"]["code"] == code
    assert body["error"]["request_id"]
    assert str(error) not in response.text


def test_invalid_asset_storage_configuration_maps_to_dependency_unavailable():
    app = create_app()

    def broken_service():
        raise AssetStorageConfigurationError("private storage configuration")

    app.dependency_overrides[get_sample_asset_service] = broken_service
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/api/v1/datasets/dataset_1/samples/sample_1/assets/image",
            params={"revision": 2},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "private storage configuration" not in response.text


def test_live_openapi_documents_binary_delivery_and_safe_errors(api):
    client, _ = api
    spec = client.get("/openapi.json").json()
    operation = spec["paths"][
        "/api/v1/datasets/{dataset_id}/samples/{sample_id}/assets/{asset_kind}"
    ]["get"]
    assert operation["operationId"] == "getSampleAsset"
    assert set(operation["responses"]) == {"200", "404", "422", "500", "503"}
    assert operation["responses"]["200"]["content"]["image/png"]["schema"] == {
        "type": "string", "format": "binary",
    }
    for status in ("404", "422", "500", "503"):
        assert operation["responses"][status]["content"]["application/json"]["schema"]["$ref"].endswith(
            "/ErrorResponse"
        )
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    kind_schema = parameters["asset_kind"]["schema"]
    if "$ref" in kind_schema:
        kind_schema = spec["components"]["schemas"][kind_schema["$ref"].rsplit("/", 1)[1]]
    assert kind_schema["enum"] == [
        "image", "mask", "mask_preview", "thumbnail",
    ]
    assert parameters["revision"]["required"] and parameters["revision"]["schema"]["minimum"] == 1
    assert "storage_key" not in str(operation)
    assert "filesystem" not in str(operation).lower()
