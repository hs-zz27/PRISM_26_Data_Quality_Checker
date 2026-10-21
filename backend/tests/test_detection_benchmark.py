"""Benchmark CLI behaviour with a fake detector; no weights, datasets or timing assumptions."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.app.inference.person_detector import MODEL_NAME, InferenceError, WeightsLoadError
from backend.app.models.detection import DetectionResult, PersonDetection
from backend.benchmarks import benchmark_detection as benchmark

WEIGHTS = b"pretend weights"
ANNOTATIONS = {
    "a.png": [{"object_id": 1, "label": "person", "bbox": [1, 1, 10, 10]}],
    "b.png": [],
}


class FakeDetector:
    """One clear person and one low-scoring candidate, like the real detector's output."""

    def __init__(self, error=None):
        self.error = error
        self.calls = 0

    def detect(self, image, min_score):
        self.calls += 1
        if self.error:
            raise self.error
        width, height = image.size
        candidates = [
            PersonDetection(x_min=0.0, y_min=0.0, x_max=10.0, y_max=10.0, score=0.9),
            PersonDetection(x_min=20.0, y_min=20.0, x_max=30.0, y_max=30.0, score=0.2),
        ]
        return DetectionResult(
            image_width=width, image_height=height, min_score=float(min_score),
            model_name="fake", weights_file="fake.pth",
            detections=[c for c in candidates if c.score >= min_score],
        )


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    weights = tmp_path / "weights.pth"
    weights.write_bytes(WEIGHTS)
    images = []
    for name in ("a.png", "b.png"):
        path = tmp_path / name
        Image.new("RGB", (64, 48), "white").save(path)
        images.append(path)
    detector = FakeDetector()
    monkeypatch.setattr(benchmark, "load_detector", lambda path: detector)
    monkeypatch.setattr(benchmark, "peak_memory_bytes", lambda: 1234)
    return SimpleNamespace(root=tmp_path, weights=weights, images=images, detector=detector)


def base(ws):
    return ["--weights", str(ws.weights), "--images", *map(str, ws.images)]


def write_annotations(ws, content=None):
    path = ws.root / "annotations.json"
    path.write_text(json.dumps(ANNOTATIONS if content is None else content), encoding="utf-8")
    return path


def comparison_args(ws, annotations_path, *extra):
    return [
        *base(ws), "--annotations", str(annotations_path), "--person-label", "person",
        "--min-confidence", "0.5", "--min-iou", "0.5", *extra,
    ]


def assert_refused(args, capsys, fragment):
    with pytest.raises(SystemExit) as info:
        benchmark.main(args)
    assert info.value.code == 2
    assert fragment in capsys.readouterr().err


# --- argument validation ----------------------------------------------------

def test_help_is_available(capsys):
    with pytest.raises(SystemExit) as info:
        benchmark.main(["--help"])
    assert info.value.code == 0
    assert "--weights" in capsys.readouterr().out


def test_required_arguments_are_enforced(capsys):
    assert_refused([], capsys, "--weights")


def test_missing_weights_are_refused(workspace, capsys):
    args = ["--weights", str(workspace.root / "nope.pth"), "--images", str(workspace.images[0])]
    assert_refused(args, capsys, "--weights")


def test_missing_image_is_refused(workspace, capsys):
    args = ["--weights", str(workspace.weights), "--images", str(workspace.root / "missing.png")]
    assert_refused(args, capsys, "--images")


def test_duplicate_image_names_are_refused(workspace, capsys):
    other = workspace.root / "other"
    other.mkdir()
    Image.new("RGB", (8, 8)).save(other / "a.png")
    args = ["--weights", str(workspace.weights), "--images", str(workspace.images[0]), str(other / "a.png")]
    assert_refused(args, capsys, "more than once")


@pytest.mark.parametrize("flag,value,fragment", [
    ("--warmup", "-1", "--warmup"),
    ("--iterations", "0", "--iterations"),
    ("--list-detections-above", "1.5", "--list-detections-above"),
    ("--list-detections-above", "nan", "--list-detections-above"),
])
def test_invalid_run_settings_are_refused(workspace, capsys, flag, value, fragment):
    assert_refused([*base(workspace), flag, value], capsys, fragment)


@pytest.mark.parametrize("flag", ["--person-label", "--min-confidence", "--min-iou"])
def test_comparison_settings_without_annotations_are_refused(workspace, capsys, flag):
    value = "person" if flag == "--person-label" else "0.5"
    assert_refused([*base(workspace), flag, value], capsys, "only apply with --annotations")


def test_annotations_need_labels_and_thresholds(workspace, capsys):
    path = write_annotations(workspace)
    assert_refused([*base(workspace), "--annotations", str(path), "--min-confidence", "0.5", "--min-iou", "0.5"],
                   capsys, "--person-label")
    assert_refused([*base(workspace), "--annotations", str(path), "--person-label", "person"],
                   capsys, "--min-confidence and --min-iou")


@pytest.mark.parametrize("value", ["nan", "inf", "-0.1", "1.5"])
def test_invalid_confidence_threshold_is_refused(workspace, capsys, value):
    path = write_annotations(workspace)
    args = [*base(workspace), "--annotations", str(path), "--person-label", "person",
            "--min-confidence", value, "--min-iou", "0.5"]
    assert_refused(args, capsys, "--min-confidence")


@pytest.mark.parametrize("value", ["0", "nan", "inf", "-1", "1.5"])
def test_invalid_iou_threshold_is_refused(workspace, capsys, value):
    path = write_annotations(workspace)
    args = [*base(workspace), "--annotations", str(path), "--person-label", "person",
            "--min-confidence", "0.5", "--min-iou", value]
    assert_refused(args, capsys, "--min-iou")


@pytest.mark.parametrize("content,fragment", [
    ("{not json", "cannot read"),
    ("[]", "expected a JSON object"),
    ('{"a.png": {}, "b.png": []}', "expected a JSON object"),
    ('{"a.png": [], "b.png": [], "c.png": []}', "c.png"),
    ('{"a.png": []}', "b.png"),
])
def test_bad_annotation_files_are_refused(workspace, capsys, content, fragment):
    path = workspace.root / "annotations.json"
    path.write_text(content, encoding="utf-8")
    assert_refused(comparison_args(workspace, path), capsys, fragment)


# --- safe output ----------------------------------------------------------------

def test_an_existing_output_file_is_never_overwritten(workspace, capsys):
    output = workspace.root / "report.json"
    output.write_text("keep me", encoding="utf-8")
    assert_refused([*base(workspace), "--output", str(output)], capsys, "already exists")
    assert output.read_text(encoding="utf-8") == "keep me"
    assert workspace.detector.calls == 0


def test_an_output_folder_that_does_not_exist_is_refused(workspace, capsys):
    output = workspace.root / "missing-folder" / "report.json"
    assert_refused([*base(workspace), "--output", str(output)], capsys, "does not exist")


# --- successful runs ----------------------------------------------------------------

def test_detector_only_run_writes_a_report_and_leaves_sources_alone(workspace, capsys):
    output = workspace.root / "report.json"
    sources = [workspace.weights, *workspace.images]
    before = [path.read_bytes() for path in sources]

    code = benchmark.main([*base(workspace), "--warmup", "1", "--iterations", "3", "--output", str(output)])

    assert code == 0
    text = output.read_text(encoding="utf-8")
    report = json.loads(text)
    assert report == json.loads(capsys.readouterr().out)
    assert report["architecture"] == MODEL_NAME
    assert report["checkpoint_sha256"] == hashlib.sha256(WEIGHTS).hexdigest()
    assert report["images"]["count"] == 2
    first = report["images"]["files"][0]
    assert (first["file_name"], first["width"], first["height"]) == ("a.png", 64, 48)
    assert (report["settings"]["warmup_passes"], report["settings"]["measured_passes"]) == (1, 3)
    assert report["settings"]["comparison"] is False
    assert report["timings"]["total_ms"]["count"] == 6
    assert report["timings"]["total_ms"]["p95_ms"] is None  # fewer than 20 samples
    assert report["timings"]["matching_ms"] is None
    assert report["peak_process_memory"] == {"unit": "bytes", "after_model_load": 1234, "after_measurement": 1234}
    assert workspace.detector.calls == 2 * (1 + 3)
    assert str(workspace.root) not in text
    assert [path.read_bytes() for path in sources] == before


def test_comparison_run_times_matching_separately(workspace, capsys):
    annotations = write_annotations(workspace)
    output = workspace.root / "report.json"
    code = benchmark.main(comparison_args(
        workspace, annotations, "--warmup", "0", "--iterations", "2", "--output", str(output),
    ))
    assert code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["settings"]["comparison"] is True
    assert report["settings"]["person_labels"] == ["person"]
    assert (report["settings"]["min_confidence"], report["settings"]["min_iou"]) == (0.5, 0.5)
    assert report["timings"]["matching_ms"]["count"] == 4
    assert report["incomplete_comparison_samples"] == 0


def test_invalid_annotation_records_are_counted_as_incomplete_comparisons(workspace):
    annotations = write_annotations(workspace, {"a.png": ["junk"], "b.png": []})
    output = workspace.root / "report.json"
    code = benchmark.main(comparison_args(
        workspace, annotations, "--warmup", "0", "--iterations", "2", "--output", str(output),
    ))
    assert code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["incomplete_comparison_samples"] == 2


def test_detections_can_be_listed_for_manual_review(workspace):
    output = workspace.root / "report.json"
    code = benchmark.main([
        *base(workspace), "--warmup", "0", "--iterations", "1",
        "--list-detections-above", "0.5", "--output", str(output),
    ])
    assert code == 0
    files = json.loads(output.read_text(encoding="utf-8"))["images"]["files"]
    assert files[0]["detections"] == [{"x_min": 0.0, "y_min": 0.0, "x_max": 10.0, "y_max": 10.0, "score": 0.9}]


# --- failures ---------------------------------------------------------------------------

def test_an_unreadable_image_stops_the_run_before_the_model_loads(workspace, capsys):
    bad = workspace.root / "c.png"
    bad.write_bytes(b"not an image")
    output = workspace.root / "report.json"
    code = benchmark.main([
        "--weights", str(workspace.weights), "--images", str(workspace.images[0]), str(bad),
        "--output", str(output),
    ])
    assert code == 1
    assert "c.png" in capsys.readouterr().err
    assert workspace.detector.calls == 0 and not output.exists()


def test_unusable_weights_stop_the_run(workspace, monkeypatch, capsys):
    def fail(path):
        raise WeightsLoadError("bad checkpoint")

    monkeypatch.setattr(benchmark, "load_detector", fail)
    output = workspace.root / "report.json"
    assert benchmark.main([*base(workspace), "--output", str(output)]) == 1
    assert "could not load the checkpoint" in capsys.readouterr().err
    assert not output.exists()


def test_inference_failure_stops_the_run_without_a_report(workspace, monkeypatch, capsys):
    monkeypatch.setattr(benchmark, "load_detector", lambda path: FakeDetector(error=InferenceError("boom")))
    output = workspace.root / "report.json"
    assert benchmark.main([*base(workspace), "--output", str(output)]) == 1
    assert "inference failed" in capsys.readouterr().err
    assert not output.exists()


def test_inference_failure_during_a_comparison_stops_the_run(workspace, monkeypatch, capsys):
    monkeypatch.setattr(benchmark, "load_detector", lambda path: FakeDetector(error=InferenceError("boom")))
    annotations = write_annotations(workspace)
    assert benchmark.main(comparison_args(workspace, annotations)) == 1
    assert "detection did not complete" in capsys.readouterr().err


# --- calculations -----------------------------------------------------------------------------

def test_summary_statistics():
    summary = benchmark.summarize([4.0, 1.0, 3.0, 2.0])
    assert summary["count"] == 4
    assert (summary["min_ms"], summary["median_ms"], summary["max_ms"], summary["mean_ms"]) == (1.0, 2.5, 4.0, 2.5)
    assert summary["p95_ms"] is None


def test_percentile_uses_nearest_rank_and_needs_enough_samples():
    assert benchmark.summarize([float(n) for n in range(20, 0, -1)])["p95_ms"] == 19.0
    assert benchmark.summarize([float(n) for n in range(1, 101)])["p95_ms"] == 95.0
    assert benchmark.summarize([float(n) for n in range(1, 20)])["p95_ms"] is None


def test_summarize_needs_samples():
    with pytest.raises(ValueError):
        benchmark.summarize([])


def test_memory_units_follow_the_platform():
    assert benchmark.ru_maxrss_to_bytes(2048, "linux") == 2048 * 1024
    assert benchmark.ru_maxrss_to_bytes(2048, "darwin") == 2048


def test_windows_uses_its_own_memory_counter(monkeypatch):
    monkeypatch.setattr(benchmark.sys, "platform", "win32")
    monkeypatch.setattr(benchmark, "_windows_peak_working_set", lambda: 42)
    assert benchmark.peak_memory_bytes() == 42


def test_peak_memory_is_a_positive_integer_or_unavailable():
    value = benchmark.peak_memory_bytes()
    assert value is None or (isinstance(value, int) and value > 0)


def test_importing_the_benchmark_does_no_model_work_or_run_anything():
    code = (
        "import torch\n"
        "def refuse(*args, **kwargs): raise AssertionError('model work at import time')\n"
        "torch.load = refuse\n"
        "torch.hub.load_state_dict_from_url = refuse\n"
        "import backend.benchmarks.benchmark_detection\n"
    )
    root = Path(__file__).resolve().parents[2]
    done = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
