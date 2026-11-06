"""Capabilities and readiness probes with mocked services; no Ollama, weights or database."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from backend.app.checks.registry import CHECK_REGISTRY
from backend.app.core.config import OllamaSettings
from backend.app.core.person_detection import (
    DETECTOR_ARCHITECTURE,
    DetectionConfigurationError,
    DetectionSettings,
)
from backend.app.models.system import DetectorStatus, OllamaStatus
from backend.app.services.system_status import (
    OLLAMA_PROBE_SECONDS,
    OllamaProbe,
    SystemStatusService,
    build_capabilities,
    detector_packages_available,
    model_is_installed,
    probe_ollama,
    validation_not_recorded,
)

MODEL = "qwen3-vl:4b-instruct"


def ollama_settings(model=MODEL):
    return OllamaSettings(
        model=model, host="http://127.0.0.1:11434", timeout_seconds=30.0, context_length=4096, max_output_tokens=512,
    )


def tags(*names):
    return {"models": [{"name": name, "model": name} for name in names]}


def mock_probe(handler):
    return probe_ollama(ollama_settings(), transport=httpx.MockTransport(handler))


def service(**options):
    values = dict(
        ollama_settings=ollama_settings,
        ollama_probe=lambda settings: OllamaProbe(reachable=True, model_names=(MODEL,)),
        detection_settings=lambda: DetectionSettings(enabled=False),
        checkpoint_location=lambda: None,
        packages_available=lambda: True,
        person_model_checkpoint=lambda: None,
    )
    return SystemStatusService(**(values | options))


# --- the Ollama probe ---------------------------------------------------------------------------

def test_the_probe_lists_models_with_one_metadata_request_and_never_generates():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.extensions["timeout"]))
        return httpx.Response(200, json=tags(MODEL, "other:latest"))

    probe = mock_probe(handler)
    assert probe == OllamaProbe(reachable=True, model_names=("other:latest", MODEL))  # sorted
    assert [(method, path) for method, path, _ in seen] == [("GET", "/api/tags")]
    # Every phase of the request has the same short, finite timeout.
    assert set(seen[0][2].values()) == {OLLAMA_PROBE_SECONDS}


def test_the_probe_timeout_is_short_and_finite():
    assert 0 < OLLAMA_PROBE_SECONDS <= 5


@pytest.mark.parametrize("error", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
def test_an_unreachable_server_is_an_expected_state(error):
    def handler(request):
        raise error

    assert mock_probe(handler) == OllamaProbe(reachable=False, model_names=None)


@pytest.mark.parametrize("response", [
    httpx.Response(500, text="boom"),
    httpx.Response(200, text="not json"),
    httpx.Response(200, json={"models": "nope"}),
    httpx.Response(200, json=[]),
    httpx.Response(307, headers={"location": "http://example.invalid/"}),
])
def test_a_reachable_server_with_an_unusable_answer_leaves_the_models_unknown(response):
    calls = []

    def handler(request):
        calls.append(request)
        return response

    assert mock_probe(handler) == OllamaProbe(reachable=True, model_names=None)
    assert len(calls) == 1  # redirects are not followed


def test_model_names_match_with_the_default_latest_tag():
    assert model_is_installed("llama3", ["llama3:latest"])
    assert model_is_installed("llama3:8b", ["llama3:8b"])
    assert not model_is_installed("llama3:8b", ["llama3:latest"])
    assert not model_is_installed("llama3", [])


# --- Ollama status ---------------------------------------------------------------------------------

def test_status_reports_a_reachable_server_with_the_model_installed():
    ollama = service().status().ollama
    assert (ollama.configuration, ollama.reachable, ollama.model, ollama.model_available) == ("valid", True, MODEL, True)


def test_a_missing_model_is_reported_as_unavailable_not_as_an_error():
    probe = OllamaProbe(reachable=True, model_names=("another:latest",))
    assert service(ollama_probe=lambda settings: probe).status().ollama.model_available is False


def test_an_unreachable_server_leaves_model_availability_unknown():
    probe = OllamaProbe(reachable=False, model_names=None)
    ollama = service(ollama_probe=lambda settings: probe).status().ollama
    assert (ollama.reachable, ollama.model, ollama.model_available) == (False, MODEL, None)


@pytest.mark.parametrize("error", [OSError("no file"), ValueError("bad toml")])
def test_unreadable_ollama_settings_are_invalid_and_never_probed(error):
    def broken():
        raise error

    def refuse(settings):
        raise AssertionError("an invalid configuration must not be probed")

    ollama = service(ollama_settings=broken, ollama_probe=refuse).status().ollama
    assert (ollama.configuration, ollama.reachable, ollama.model, ollama.model_available) == (
        "invalid", False, None, None,
    )


def test_ollama_status_rejects_contradictory_states():
    with pytest.raises(ValidationError):
        OllamaStatus(configuration="valid", reachable=False, model=MODEL, model_available=True)
    with pytest.raises(ValidationError):
        OllamaStatus(configuration="invalid", reachable=True, model=None, model_available=None)


# --- detector status ----------------------------------------------------------------------------------

def test_a_disabled_detector_with_no_checkpoint_configured():
    detector = service().status().detector
    assert (detector.integration_supported, detector.configuration, detector.checkpoint_file) == (
        True, "disabled", "not_configured",
    )
    assert detector.validation == "unknown" and detector.architecture == DETECTOR_ARCHITECTURE


def test_an_enabled_detector_with_a_present_checkpoint_is_still_unvalidated(tmp_path):
    weights = tmp_path / "weights.pth"
    weights.write_bytes(b"not a real model")
    detector = service(
        detection_settings=lambda: DetectionSettings(enabled=True), checkpoint_location=lambda: weights,
    ).status().detector
    assert (detector.configuration, detector.checkpoint_file, detector.validation) == ("enabled", "present", "unknown")


def test_a_missing_checkpoint_file_is_reported_as_missing(tmp_path):
    detector = service(checkpoint_location=lambda: tmp_path / "absent.pth").status().detector
    assert detector.checkpoint_file == "missing"
    assert service(checkpoint_location=lambda: tmp_path).status().detector.checkpoint_file == "missing"  # a folder


def test_an_unreadable_checkpoint_location_is_reported_without_raising():
    class Unreadable:
        def is_file(self):
            raise PermissionError("denied")

    assert service(checkpoint_location=Unreadable).status().detector.checkpoint_file == "unreadable"


def test_invalid_detection_settings_are_a_state_not_an_error():
    def invalid():
        raise DetectionConfigurationError("bad")

    detector = service(detection_settings=invalid).status().detector
    assert detector.configuration == "invalid"
    assert service(checkpoint_location=invalid).status().detector.checkpoint_file == "not_configured"


def test_missing_detector_packages_are_reported():
    assert service(packages_available=lambda: False).status().detector.integration_supported is False


def test_validation_is_unknown_unless_a_record_says_otherwise():
    assert validation_not_recorded() == "unknown"
    assert service(validation_status=lambda: "failed").status().detector.validation == "failed"
    with pytest.raises(ValidationError):
        DetectorStatus(
            integration_supported=True, configuration="enabled", checkpoint_file="present",
            validation="works", architecture=DETECTOR_ARCHITECTURE,
        )


# --- person model status ------------------------------------------------------------------------------

def test_the_person_model_is_present_only_when_its_weights_file_is_installed(tmp_path):
    weights = tmp_path / "weights.pth"
    weights.write_bytes(b"x")
    status = service(person_model_checkpoint=lambda: weights).status().person_model
    assert (status.model, status.checkpoint_file) == ("maskrcnn_resnet50_fpn_v2_coco", "present")
    assert service().status().person_model.checkpoint_file == "missing"


def test_an_unreadable_person_model_location_is_reported_without_raising():
    def unreadable():
        raise PermissionError("denied")

    assert service(person_model_checkpoint=unreadable).status().person_model.checkpoint_file == "unreadable"


def test_the_person_model_file_comes_from_the_setting_when_one_is_given(tmp_path, monkeypatch):
    from backend.app.core import person_model

    weights = tmp_path / "custom.pth"
    monkeypatch.setenv(person_model.CHECKPOINT_ENV, str(weights))
    assert person_model.checkpoint_path() is None  # configured, not installed
    weights.write_bytes(b"x")
    assert person_model.checkpoint_path() == weights


# --- what a status read must never do --------------------------------------------------------------------

def test_a_status_read_never_hashes_a_checkpoint(tmp_path, monkeypatch):
    weights = tmp_path / "weights.pth"
    weights.write_bytes(b"x" * 1024)

    def refuse(*args, **kwargs):
        raise AssertionError("a status read must not hash anything")

    monkeypatch.setattr(hashlib, "sha256", refuse)
    detector = service(
        detection_settings=lambda: DetectionSettings(enabled=True), checkpoint_location=lambda: weights,
    ).status().detector
    assert detector.checkpoint_file == "present"


def test_status_output_contains_no_private_paths_or_raw_errors(tmp_path):
    private = tmp_path / "private-user-folder"
    private.mkdir()
    weights = private / "weights.pth"
    weights.write_bytes(b"x")

    def broken():
        raise ValueError("secret credential /private/path")

    text = json.dumps(service(
        ollama_settings=broken, checkpoint_location=lambda: weights, person_model_checkpoint=lambda: weights,
    ).status().model_dump(mode="json"), allow_nan=False)
    assert "private-user-folder" not in text and "secret" not in text and "weights.pth" not in text


def test_importing_and_checking_packages_never_imports_torch():
    code = (
        "import sys\n"
        "from backend.app.services.system_status import detector_packages_available, build_capabilities\n"
        "assert isinstance(detector_packages_available(), bool)\n"
        "build_capabilities()\n"
        "assert 'torch' not in sys.modules and 'torchvision' not in sys.modules\n"
    )
    root = Path(__file__).resolve().parents[2]
    done = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_the_package_check_returns_a_boolean():
    assert detector_packages_available() in (True, False)


# --- capabilities -------------------------------------------------------------------------------------------

def test_capabilities_list_exactly_the_registry_checks():
    capabilities = build_capabilities()
    assert [check.name for check in capabilities.checks] == sorted(CHECK_REGISTRY)
    assert {check.implementation for check in capabilities.checks} == {"implemented"}
    assert capabilities.dataset_formats == ["penn_fudan", "coco", "labelme"] and capabilities.guideline_input_formats == ["plain_text"]
    assert capabilities.coordinate_convention == "format_specific" and capabilities.execution_device == "cpu"


def test_detection_is_an_evidence_feature_not_a_guideline_check():
    capabilities = build_capabilities()
    assert {feature.name for feature in capabilities.evidence_features} == {
        "person_detection", "detection_annotation_matching",
    }
    assert not {"person_detection", "detection_annotation_matching"} & {c.name for c in capabilities.checks}


def test_capabilities_do_not_claim_features_that_do_not_exist():
    text = json.dumps(build_capabilities().model_dump(mode="json")).lower()
    for planned in ("posture", "person_count", "mask_person", "detection_mask"):
        assert planned not in text


def test_an_installed_checkpoint_does_not_change_capabilities(tmp_path, monkeypatch):
    before = build_capabilities()
    weights = tmp_path / "weights.pth"
    weights.write_bytes(b"x")
    monkeypatch.setenv("DETECTION_ENABLED", "true")
    monkeypatch.setenv("DETECTION_CHECKPOINT_PATH", str(weights))
    assert build_capabilities() == before
    json.dumps(before.model_dump(mode="json"), allow_nan=False)
