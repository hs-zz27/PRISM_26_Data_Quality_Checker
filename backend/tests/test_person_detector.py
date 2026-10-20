"""Person detector behaviour with a fake model; no weights or downloads needed."""

import subprocess
import sys
from math import inf, nan
from pathlib import Path

import pytest
import torch
from PIL import Image
from pydantic import ValidationError
from torchvision.models.detection import fasterrcnn_mobilenet_v3_large_320_fpn

from backend.app.inference import person_detector
from backend.app.inference.person_detector import (
    InferenceError,
    PersonDetector,
    WeightsLoadError,
)
from backend.app.models.detection import COORDINATE_CONVENTION, PersonDetection


class FakeModel:
    """Returns one fixed torchvision-style output and counts its calls."""

    def __init__(self, boxes=(), labels=(), scores=(), error=None):
        self.output = {
            "boxes": torch.tensor(boxes, dtype=torch.float32).reshape(-1, 4),
            "labels": torch.tensor(labels, dtype=torch.int64),
            "scores": torch.tensor(scores, dtype=torch.float32),
        }
        self.error = error
        self.calls = 0

    def __call__(self, images):
        self.calls += 1
        if self.error:
            raise self.error
        return [self.output]


@pytest.fixture
def make_detector(monkeypatch, tmp_path):
    """Build a detector around a fake model; returns (detector, loader_calls)."""

    def build(model):
        loads = []
        monkeypatch.setattr(person_detector, "_load_model", lambda path: loads.append(path) or model)
        return PersonDetector(tmp_path / "fake.pth"), loads

    return build


def image(size=(200, 100), mode="RGB"):
    return Image.new(mode, size, "white")


def test_keeps_only_people(make_detector):
    detector, _ = make_detector(FakeModel(
        boxes=[(10, 10, 50, 90), (20, 20, 60, 80), (30, 30, 70, 70)],
        labels=[1, 3, 1], scores=[0.75, 0.75, 0.5],
    ))
    detections = detector.detect(image(), min_score=0.25).detections
    assert [(d.x_min, d.score) for d in detections] == [(10.0, 0.75), (30.0, 0.5)]


def test_score_threshold_is_inclusive(make_detector):
    detector, _ = make_detector(FakeModel(
        boxes=[(1, 1, 5, 5), (2, 2, 6, 6), (3, 3, 7, 7)], labels=[1, 1, 1], scores=[0.75, 0.5, 0.25],
    ))
    result = detector.detect(image(), min_score=0.5)
    assert [d.score for d in result.detections] == [0.75, 0.5]
    assert result.min_score == 0.5


@pytest.mark.parametrize("bad", [nan, inf, -inf, -0.1, 1.1, True, "0.5", None])
def test_invalid_threshold_is_rejected_before_inference(make_detector, bad):
    model = FakeModel()
    detector, _ = make_detector(model)
    with pytest.raises(ValueError):
        detector.detect(image(), min_score=bad)
    assert model.calls == 0


def test_no_people_is_an_empty_successful_result(make_detector):
    detector, _ = make_detector(FakeModel(boxes=[(1, 1, 5, 5)], labels=[17], scores=[0.9]))
    result = detector.detect(image(), min_score=0.5)
    assert result.detections == []


def test_result_uses_original_image_dimensions_and_coordinates(make_detector):
    detector, _ = make_detector(FakeModel(boxes=[(10.5, 20, 150, 90.25)], labels=[1], scores=[0.5]))
    result = detector.detect(image(size=(200, 100)), min_score=0.0)
    assert (result.image_width, result.image_height) == (200, 100)
    box = result.detections[0]
    assert (box.x_min, box.y_min, box.x_max, box.y_max) == (10.5, 20.0, 150.0, 90.25)
    assert result.coordinate_convention == COORDINATE_CONVENTION
    assert result.model_name == "fasterrcnn_mobilenet_v3_large_320_fpn"
    assert result.weights_file == "fake.pth"


def test_float_overshoot_at_the_image_edge_is_clipped(make_detector):
    detector, _ = make_detector(FakeModel(boxes=[(-0.01, 0, 200.01, 100.02)], labels=[1], scores=[0.5]))
    box = detector.detect(image(size=(200, 100)), min_score=0.0).detections[0]
    assert (box.x_min, box.x_max, box.y_max) == (0.0, 200.0, 100.0)


def test_model_failure_raises_instead_of_returning_no_detections(make_detector):
    detector, _ = make_detector(FakeModel(error=RuntimeError("out of memory")))
    with pytest.raises(InferenceError, match="out of memory"):
        detector.detect(image(), min_score=0.5)


def test_mismatched_model_output_raises(make_detector):
    model = FakeModel(boxes=[(1, 1, 5, 5)], labels=[1], scores=[0.5])
    model.output["scores"] = torch.tensor([0.5, 0.6])
    detector, _ = make_detector(model)
    with pytest.raises(InferenceError):
        detector.detect(image(), min_score=0.0)


def test_non_image_input_is_rejected(make_detector):
    detector, _ = make_detector(FakeModel())
    with pytest.raises(TypeError):
        detector.detect("photo.png", min_score=0.5)


def test_model_is_loaded_once_and_reused(make_detector):
    model = FakeModel(boxes=[(1, 1, 5, 5)], labels=[1], scores=[0.5])
    detector, loads = make_detector(model)
    detector.detect(image(), min_score=0.5)
    detector.detect(image(), min_score=0.5)
    assert len(loads) == 1 and model.calls == 2


@pytest.mark.parametrize("mode", ["RGB", "L", "RGBA"])
def test_callers_image_is_not_modified(make_detector, mode):
    detector, _ = make_detector(FakeModel())
    original = Image.effect_noise((40, 30), 50).convert(mode)
    before = (original.mode, original.size, original.tobytes())
    detector.detect(original, min_score=0.5)
    assert (original.mode, original.size, original.tobytes()) == before


def test_missing_weights_raise(tmp_path):
    with pytest.raises(WeightsLoadError, match="not found"):
        PersonDetector(tmp_path / "missing.pth")


def test_corrupt_weights_raise(tmp_path):
    path = tmp_path / "corrupt.pth"
    path.write_bytes(b"this is not a checkpoint")
    with pytest.raises(WeightsLoadError):
        PersonDetector(path)


def test_checkpoint_for_another_architecture_is_rejected(tmp_path):
    path = tmp_path / "other.pth"
    torch.save({"layer.weight": torch.zeros(3)}, path)
    with pytest.raises(WeightsLoadError):
        PersonDetector(path)


def test_loading_never_tries_to_download(monkeypatch, tmp_path):
    def refuse(*args, **kwargs):
        raise AssertionError("tried to download weights")

    monkeypatch.setattr(torch.hub, "load_state_dict_from_url", refuse)
    with pytest.raises(WeightsLoadError):
        PersonDetector(tmp_path / "missing.pth")


def test_importing_the_module_loads_no_model():
    code = (
        "import torch\n"
        "def refuse(*args, **kwargs): raise AssertionError('model work at import time')\n"
        "torch.load = refuse\n"
        "torch.hub.load_state_dict_from_url = refuse\n"
        "import backend.app.inference.person_detector\n"
    )
    root = Path(__file__).resolve().parents[2]
    done = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_real_architecture_runs_end_to_end_with_random_weights(tmp_path):
    """Exercises the real torchvision model; says nothing about detection quality."""
    model = fasterrcnn_mobilenet_v3_large_320_fpn(weights=None, weights_backbone=None)
    # Checkpoints from pretrained torchvision models use frozen batch norm,
    # which has no num_batches_tracked entries.
    state = {k: v for k, v in model.state_dict().items() if not k.endswith("num_batches_tracked")}
    path = tmp_path / "random.pth"
    torch.save(state, path)

    result = PersonDetector(path).detect(image(size=(160, 120)), min_score=0.0)
    assert (result.image_width, result.image_height) == (160, 120)
    assert all(d.x_max <= 160 and d.y_max <= 120 for d in result.detections)


def test_detection_must_have_positive_size():
    with pytest.raises(ValidationError):
        PersonDetection(x_min=5, y_min=5, x_max=5, y_max=9, score=0.5)