"""Authored live route/schema/no-startup-effects checks. NOT executed."""
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
import pytest

from backend.app.main import create_app
from backend.app.core.dataset_http import MultipartPolicy, load_dataset_http_settings
from backend.app.core.dataset_import import ImportConfigurationError
from backend.tests.dataset_http_fakes import http_settings

EXPECTED = {
    ("/api/v1/datasets", "post", "importDataset"),
    ("/api/v1/datasets", "get", "listDatasets"),
    ("/api/v1/datasets/{dataset_id}", "get", "getDataset"),
    ("/api/v1/datasets/{dataset_id}/samples", "get", "listDatasetSamples"),
    ("/api/v1/datasets/{dataset_id}/samples/{sample_id}", "get", "getDatasetSample"),
}


def test_exact_five_dataset_routes_once_in_current_inventory(tmp_path):
    app = create_app(dataset_upload_settings_factory=lambda: http_settings(tmp_path))
    spec = app.openapi()
    actual = {(path, method, operation["operationId"]) for path, methods in spec["paths"].items()
              for method, operation in methods.items()}
    ids = [context.original_route.operation_id for context in iter_route_contexts(app.routes)
           if isinstance(context.original_route, APIRoute) and context.original_route.include_in_schema]
    assert len(actual) == len(ids) == len(set(ids)) == 50
    assert EXPECTED <= actual
    assert {row for row in actual if row[2] in {entry[2] for entry in EXPECTED}} == EXPECTED
    for operation_id in ("getSampleAsset", "getJob", "cancelJob", "extractRulebook", "createRulebook",
                         "listAuditSamples", "reviewFinding", "reviewSample"):
        assert ids.count(operation_id) == 1
    assert ("/api/v1/audits", "post", "startAudit") in actual
    assert ("/api/v1/audits", "get", "listAudits") in actual
    for operation_id in ("startExport", "listExports", "getExport", "getExportManifest", "downloadExport"):
        assert ids.count(operation_id) == 1
    assert {
        ("/api/v1/datasets/{dataset_id}/samples/{sample_id}/mask-uploads", "post", "uploadCorrectionMask"),
        ("/api/v1/datasets/{dataset_id}/samples/{sample_id}/revisions", "post", "correctAnnotations"),
        ("/api/v1/datasets/{dataset_id}/samples/{sample_id}/revisions", "get", "listAnnotationRevisions"),
        ("/api/v1/audits/{audit_id}/rechecks", "post", "recheckSamples"),
    } <= actual


def test_live_multipart_schema_is_strict_required_and_response_is_real_model(tmp_path):
    spec = create_app(dataset_upload_settings_factory=lambda: http_settings(tmp_path)).openapi()
    operation = spec["paths"]["/api/v1/datasets"]["post"]
    body = operation["requestBody"]
    schema = body["content"]["multipart/form-data"]["schema"]
    assert body["required"] is True and schema["additionalProperties"] is False
    assert set(schema["required"]) == {"archive", "name", "format"}
    assert schema["properties"]["archive"]["format"] == "binary"
    assert schema["properties"]["format"]["const"] == "penn_fudan"
    assert {"202", "413", "415", "422", "500", "503"} <= set(operation["responses"])
    assert "DatasetImportStarted" in operation["responses"]["202"]["content"]["application/json"]["schema"]["$ref"]
    for path, method, _ in EXPECTED - {("/api/v1/datasets", "post", "importDataset")}:
        assert "409" not in spec["paths"][path][method]["responses"]


def test_create_app_and_openapi_do_not_load_settings_or_start_work(tmp_path, monkeypatch):
    from backend.app.storage import database
    from backend.app.services import dataset_upload, dataset_import_jobs
    from backend.app.routers import datasets
    def forbidden(*args, **kwargs):
        pytest.fail("App/OpenAPI creation must not open storage, load request settings or run work.")
    monkeypatch.setattr(database, "database_connection", forbidden)
    monkeypatch.setattr(dataset_upload, "private_directory", forbidden)
    monkeypatch.setattr(dataset_import_jobs, "run_claimed_import", forbidden)
    monkeypatch.setattr(datasets, "DatasetRepository", forbidden)
    application = create_app(dataset_upload_settings_factory=forbidden)
    assert "/api/v1/datasets" in application.openapi()["paths"]
    assert list(tmp_path.iterdir()) == []


def test_http_env_precedence_loading_creates_no_directories(tmp_path, monkeypatch):
    import os
    for key in tuple(os.environ):
        if key.startswith(("DATASET_HTTP_", "DATASET_IMPORT_")) or key == "ASSET_STORAGE_ROOT":
            monkeypatch.delenv(key)
    env = tmp_path / ".env"
    imports, assets = tmp_path / "imports", tmp_path / "assets"
    env.write_text(f"DATASET_IMPORT_ROOT={imports}\nASSET_STORAGE_ROOT={assets}\nDATASET_HTTP_SPOOL_BYTES=131072\n")
    monkeypatch.setenv("DATASET_HTTP_SPOOL_BYTES", "262144")
    settings = load_dataset_http_settings(env_path=env)
    assert settings.multipart.spool_bytes == 262144
    assert not imports.exists() and not assets.exists()


@pytest.mark.parametrize("value", ["", "true", "NaN", "0", "1.5"])
def test_invalid_http_configuration_is_safe(tmp_path, monkeypatch, value):
    monkeypatch.setenv("DATASET_HTTP_SPOOL_BYTES", value)
    with pytest.raises(ImportConfigurationError):
        load_dataset_http_settings(env_path=tmp_path / "missing.env")
