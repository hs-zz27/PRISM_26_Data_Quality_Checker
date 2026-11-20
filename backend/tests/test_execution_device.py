"""PRISM_EXECUTION_DEVICE: cpu by default, auto to use a GPU when the machine has one."""
import json

import httpx
import numpy as np
import pytest
import torch
from PIL import Image

from backend.app.core import execution_device as setting
from backend.app.core.person_model import checkpoint_path
from backend.app.inference import person_segmenter
from backend.app.inference.ollama_client import propose_rules
from backend.app.services.system_status import SystemStatusService, build_capabilities


@pytest.fixture
def device(monkeypatch):
    def choose(value):
        monkeypatch.setenv(setting.DEVICE_ENV, value)
    monkeypatch.delenv(setting.DEVICE_ENV, raising=False)
    return choose


def gpus(monkeypatch, *, cuda, mps):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: cuda)
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: mps)


def test_the_default_is_cpu_and_forbids_gpu_layers_in_ollama(device, tmp_path):
    assert setting.execution_device(env_path=tmp_path / "missing.env") == "cpu"
    assert setting.ollama_device_options() == {"num_gpu": 0}


def test_auto_lets_ollama_place_the_model(device):
    device(" AUTO ")
    assert setting.execution_device() == "auto" and setting.ollama_device_options() == {}


def test_the_setting_is_read_from_the_env_file_and_the_process_environment_wins(device, tmp_path):
    env = tmp_path / ".env"
    env.write_text("PRISM_EXECUTION_DEVICE=auto\n")
    assert setting.execution_device(env_path=env) == "auto"
    device("cpu")
    assert setting.execution_device(env_path=env) == "cpu"


@pytest.mark.parametrize("value", ["gpu", "cuda", "yes"])
def test_an_unknown_value_is_an_error_not_a_silent_choice(device, value):
    device(value)
    with pytest.raises(ValueError, match="PRISM_EXECUTION_DEVICE"):
        setting.execution_device()
    with pytest.raises(ValueError):
        setting.ollama_device_options()


def test_cpu_keeps_the_person_model_on_the_cpu_even_when_a_gpu_is_present(device, monkeypatch):
    gpus(monkeypatch, cuda=True, mps=True)
    assert person_segmenter.select_device() == "cpu"


@pytest.mark.parametrize("cuda,mps,expected", [(True, True, "cuda"), (False, True, "mps"), (False, False, "cpu")])
def test_auto_prefers_an_nvidia_gpu_then_an_apple_gpu_then_the_cpu(device, monkeypatch, cuda, mps, expected):
    device("auto")
    gpus(monkeypatch, cuda=cuda, mps=mps)
    assert person_segmenter.select_device() == expected


def test_a_device_that_cannot_be_used_makes_the_person_model_unavailable(tmp_path):
    with pytest.raises(person_segmenter.SegmenterUnavailable):
        person_segmenter.PersonSegmenter(tmp_path / "missing.pth", device="cpu")


def test_the_rule_extraction_request_follows_the_setting(device):
    requests = []

    def handle(request):
        requests.append((request.url.path, json.loads(request.content)))
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"model_info": {"general.architecture": "test"}})
        return httpx.Response(500, json={"error": "stop here"})

    for value, expected in (("cpu", 0), ("auto", None)):
        device(value)
        requests.clear()
        with pytest.raises(Exception):
            propose_rules("Label every person.", transport=httpx.MockTransport(handle))
        options = requests[-1][1]["options"]
        assert requests[-1][0] == "/api/chat" and options.get("num_gpu") == expected
        assert options["temperature"] == 0


def test_status_and_capabilities_report_the_configured_setting(device):
    service = SystemStatusService(ollama_settings=lambda: int("not configured"), person_model_checkpoint=lambda: None)
    assert service.status().execution_device == "cpu" and build_capabilities().execution_device == "cpu"
    device("auto")
    assert service.status().execution_device == "auto" and build_capabilities().execution_device == "auto"


needs_gpu = pytest.mark.skipif(
    checkpoint_path() is None or not (torch.cuda.is_available() or torch.backends.mps.is_available()),
    reason="needs the person model weights and a GPU on this machine")


@needs_gpu
def test_the_person_model_finds_the_same_people_on_the_gpu_as_on_the_cpu(device):
    device("auto")
    on_gpu = person_segmenter.PersonSegmenter(checkpoint_path())
    on_cpu = person_segmenter.PersonSegmenter(checkpoint_path(), device="cpu")
    assert on_gpu.device in ("cuda", "mps") and on_gpu.identity["device"] == on_gpu.device
    assert on_cpu.identity["device"] == "cpu"
    photo = Image.open("PennFudanPed/PNGImages/FudanPed00001.png")
    gpu, cpu = on_gpu.segment(photo), on_cpu.segment(photo)
    assert len(gpu.people) == len(cpu.people) >= 1
    for a, b in zip(gpu.people, cpu.people):
        assert isinstance(a.mask, np.ndarray) and (a.mask & b.mask).sum() / (a.mask | b.mask).sum() > 0.98
        assert abs(a.score - b.score) < 0.01
