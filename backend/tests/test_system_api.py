"""The three system routes over HTTP with a minimal app; the full-app checks skip without POSIX locking."""

import inspect
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.core.config import OllamaSettings
from backend.app.routers.system import get_system_status_service, router
from backend.app.services.system_status import (
    OllamaProbe,
    SystemStatusService,
    build_capabilities,
    probe_ollama,
)

MODEL = "qwen3-vl:4b-instruct"


def settings():
    return OllamaSettings(
        model=MODEL, host="http://127.0.0.1:11434", timeout_seconds=30.0, context_length=4096, max_output_tokens=512,
    )


def fake_service(probe=None, **options):
    values = dict(
        ollama_settings=settings,
        ollama_probe=lambda s: probe or OllamaProbe(reachable=True, model_names=(MODEL,)),
        person_model_checkpoint=lambda: None,
    )
    return SystemStatusService(**(values | options))


def client_for(service=None, **client_options):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    if service is not None:
        app.dependency_overrides[get_system_status_service] = lambda: service
    return TestClient(app, **client_options)


def test_health_is_liveness_only_and_independent_of_every_dependency():
    class Explodes:
        def status(self):
            raise AssertionError("health must not consult readiness probes")

    response = client_for(Explodes()).get("/api/v1/health")
    assert response.status_code == 200 and response.json() == {"status": "ok", "api_version": "v1"}


def test_capabilities_match_the_registry_and_need_no_service():
    response = client_for().get("/api/v1/capabilities")
    assert response.status_code == 200
    assert response.json() == build_capabilities().model_dump(mode="json")


@pytest.mark.parametrize("probe,expected", [
    (OllamaProbe(True, (MODEL,)), (True, True)),
    (OllamaProbe(True, ("other:latest",)), (True, False)),
    (OllamaProbe(False, None), (False, None)),
])
def test_status_reports_each_ollama_state_with_http_200(probe, expected):
    response = client_for(fake_service(probe)).get("/api/v1/system/status")
    assert response.status_code == 200
    ollama = response.json()["ollama"]
    assert (ollama["reachable"], ollama["model_available"]) == expected
    assert response.json()["execution_device"] == "cpu"


def test_status_reports_whether_the_person_model_is_installed(tmp_path):
    weights = tmp_path / "weights.pth"
    weights.write_bytes(b"x")
    body = client_for(fake_service(person_model_checkpoint=lambda: weights)).get("/api/v1/system/status").json()
    assert body["person_model"] == {"model": "maskrcnn_resnet50_fpn_v2_coco", "checkpoint_file": "present"}
    missing = client_for(fake_service()).get("/api/v1/system/status").json()
    assert missing["person_model"]["checkpoint_file"] == "missing"


def test_the_status_probe_makes_one_get_request_and_nothing_else():
    methods = []

    def handler(request):
        methods.append((request.method, request.url.path))
        return httpx.Response(200, json={"models": [{"name": MODEL}]})

    service = fake_service(ollama_probe=lambda s: probe_ollama(s, transport=httpx.MockTransport(handler)))
    assert client_for(service).get("/api/v1/system/status").status_code == 200
    assert methods == [("GET", "/api/tags")]


def test_the_routes_are_synchronous_so_blocking_probes_stay_off_the_event_loop():
    for route in router.routes:
        assert not inspect.iscoroutinefunction(route.endpoint)


def test_route_operation_ids_and_tags():
    routes = {(route.path, tuple(route.methods), route.operation_id) for route in router.routes}
    assert routes == {
        ("/health", ("GET",), "getHealth"),
        ("/capabilities", ("GET",), "getCapabilities"),
        ("/system/status", ("GET",), "getSystemStatus"),
    }
    assert all(route.tags == ["System"] for route in router.routes)


def test_reads_have_no_request_body_or_parameters():
    document = client_for().app.openapi()
    for path in ("/api/v1/health", "/api/v1/capabilities", "/api/v1/system/status"):
        operation = document["paths"][path]["get"]
        assert "requestBody" not in operation and "parameters" not in operation
        assert set(operation["responses"]) == {"200", "500"}


# --- the full application (needs the audit engine, which needs POSIX file locking) -----------------------

def full_app():
    return pytest.importorskip(
        "backend.app.main", reason="the full app imports the audit engine, which needs POSIX file locking",
    ).create_app()


def test_the_full_app_exposes_each_system_operation_exactly_once():
    from fastapi.routing import APIRoute, iter_route_contexts

    app = full_app()
    ids = [context.original_route.operation_id for context in iter_route_contexts(app.routes)
           if isinstance(context.original_route, APIRoute) and context.original_route.include_in_schema]
    assert len(ids) == len(set(ids))
    for identifier in ("getHealth", "getCapabilities", "getSystemStatus"):
        assert ids.count(identifier) == 1
    spec = app.openapi()
    assert {"/api/v1/health", "/api/v1/capabilities", "/api/v1/system/status"} <= set(spec["paths"])


def test_an_unexpected_failure_uses_the_safe_error_envelope_without_leaking():
    class Broken:
        def status(self):
            raise RuntimeError("secret credential /private/path")

    app = full_app()
    app.dependency_overrides[get_system_status_service] = lambda: Broken()
    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/system/status")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR" and "request_id" in body["error"]
    assert "secret" not in json.dumps(body) and "/private" not in json.dumps(body)
