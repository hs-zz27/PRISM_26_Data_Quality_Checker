"""Validate a local person-detector checkpoint and time the CPU detection path.

Run explicitly from the project root, for example:
    .venv/bin/python -B -m backend.benchmarks.benchmark_detection \\
        --weights PATH/TO/checkpoint.pth --images a.jpg b.jpg --output result.json

Add --annotations, --person-label, --min-confidence and --min-iou to include the
matching step. Nothing here downloads weights, writes to a database or starts a
worker. Source images, annotations and checkpoints are only read, and an
existing output file is never overwritten.

The timings are CPU measurements of this one detection path. They exclude
database work, snapshot staging and process supervision, so they are not audit
throughput, and no batch size is recommended. Execution success says nothing
about detection accuracy. See backend/CPU_DETECTION.md.
"""

import argparse
import json
import math
import os
import platform
import sys
from pathlib import Path
from statistics import mean, median
from time import perf_counter

import torch
import torchvision
from PIL import Image

from backend.app.inference.person_detector import (
    MODEL_NAME,
    InferenceError,
    PersonDetector,
    PersonDetectorError,
)
from backend.app.models.detection_evidence import EvidenceStatus
from backend.app.services.detection_evidence import (
    DETECTOR_INFERENCE_CUTOFF,
    DetectionEvidenceService,
    file_sha256,
)

MIN_SAMPLES_FOR_PERCENTILE = 20
PERCENTILE = 95
MEASUREMENT_NOTES = {
    "decode_ms": "Image.open plus a full pixel decode of the local file. The operating system may cache the file after the first pass.",
    "detector_ms": "PersonDetector.detect: RGB conversion, tensor conversion, the model forward pass and result construction. Preprocessing inside the detector cannot be timed separately without changing it.",
    "matching_ms": "Service time after detection: label scoping, matching and evidence construction. Present only with --annotations.",
    "total_ms": "decode_ms plus detection and, when comparing, the matching step. Model loading is excluded.",
    "model_load": "Checkpoint hashing and PersonDetector construction, measured once before any image is processed.",
    "peak_process_memory": "Process-lifetime high-water mark in bytes (peak resident set, or peak working set on Windows). It includes the interpreter, Torch and the loaded model and is not per-image memory.",
    "percentile": f"p{PERCENTILE} uses the nearest-rank method and is reported only with at least {MIN_SAMPLES_FOR_PERCENTILE} samples.",
    "scope": "Not audit throughput: no database work, snapshot staging, process supervision or other audit stages are included. No batch size is recommended.",
}


class BenchmarkError(Exception):
    """A run problem the operator can act on."""


def load_detector(weights: Path):
    return PersonDetector(weights)


def summarize(samples_ms: list[float]) -> dict:
    """Summary statistics in milliseconds. The percentile needs enough samples to be meaningful."""
    values = sorted(samples_ms)
    count = len(values)
    if count == 0:
        raise ValueError("at least one sample is required")
    percentile = None
    if count >= MIN_SAMPLES_FOR_PERCENTILE:
        rank = (count * PERCENTILE + 99) // 100  # nearest rank: ceil(count * 0.95)
        percentile = values[rank - 1]
    return {
        "count": count, "min_ms": values[0], "median_ms": median(values),
        f"p{PERCENTILE}_ms": percentile, "max_ms": values[-1], "mean_ms": mean(values),
    }


def ru_maxrss_to_bytes(value: int, platform_name: str) -> int:
    """getrusage reports bytes on macOS and kibibytes on Linux and other Unix systems."""
    return value if platform_name == "darwin" else value * 1024


def peak_memory_bytes() -> int | None:
    """Peak memory of this process so far in bytes, or None when it cannot be read."""
    if sys.platform == "win32":
        return _windows_peak_working_set()
    try:
        import resource
    except ImportError:
        return None
    return ru_maxrss_to_bytes(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, sys.platform)


def _windows_peak_working_set() -> int | None:
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    try:
        kernel32 = ctypes.WinDLL("kernel32")
        psapi = ctypes.WinDLL("psapi")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return None
        return int(counters.PeakWorkingSetSize)
    except (OSError, AttributeError):
        return None


def environment_info() -> dict:
    return {
        "python": platform.python_version(),
        "torch": torch.__version__,
        "torchvision": torchvision.__version__,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "logical_cpus": os.cpu_count(),
        "torch_threads": torch.get_num_threads(),
        "device": "cpu",
    }


class _TimedDetector:
    """Passes calls to the real detector and remembers how long the last one took."""

    def __init__(self, detector):
        self._detector = detector
        self.last_seconds = 0.0

    def detect(self, image, min_score):
        started = perf_counter()
        try:
            return self._detector.detect(image, min_score)
        finally:
            self.last_seconds = perf_counter() - started


def _decode(path: Path) -> Image.Image:
    try:
        image = Image.open(path)
        image.load()
    except (OSError, Image.DecompressionBombError) as error:
        raise BenchmarkError(f"cannot read image {path.name}: {error}") from error
    return image


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Annotation file: a JSON object mapping each image file name to its list of annotation "
            'objects, for example {"a.jpg": [{"object_id": 1, "label": "person", "bbox": [10, 20, 50, 90]}], '
            '"b.jpg": []}. Boxes are [xmin, ymin, xmax, ymax], one-based and inclusive. Every image needs '
            "an entry; use [] for an image with no objects."
        ),
    )
    parser.add_argument("--weights", type=Path, required=True, help="existing local checkpoint for fasterrcnn_mobilenet_v3_large_320_fpn")
    parser.add_argument("--images", type=Path, nargs="+", required=True, help="image files; names must be unique")
    parser.add_argument("--annotations", type=Path, help="JSON annotation file; enables the matching step")
    parser.add_argument("--person-label", action="append", dest="person_labels", help="annotation label that means person; repeat for several (needs --annotations)")
    parser.add_argument("--min-confidence", type=float, help="confidence threshold in [0, 1] (needs --annotations)")
    parser.add_argument("--min-iou", type=float, help="IoU threshold greater than 0 and at most 1 (needs --annotations)")
    parser.add_argument("--list-detections-above", type=float, metavar="SCORE", help="also list each image's detections scoring at least SCORE, for manual review")
    parser.add_argument("--warmup", type=int, default=1, help="unmeasured passes over all images (default 1)")
    parser.add_argument("--iterations", type=int, default=5, help="measured passes over all images (default 5)")
    parser.add_argument("--output", type=Path, help="write the JSON report to this new file; existing files are never overwritten")
    return parser


def _read_annotations(path: Path, image_names: list[str]) -> dict[str, list]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"--annotations: cannot read {path.name} as JSON: {error}") from error
    if not isinstance(data, dict) or any(not isinstance(value, list) for value in data.values()):
        raise ValueError("--annotations: expected a JSON object mapping image file names to lists of annotations")
    unknown = sorted(set(data) - set(image_names))
    if unknown:
        raise ValueError(f"--annotations: entries for images not given in --images: {', '.join(unknown)}")
    missing = [name for name in image_names if name not in data]
    if missing:
        raise ValueError(f"--annotations: no entry for: {', '.join(missing)} (use [] for an image with no objects)")
    return data


def _in_unit_range(value: float, *, allow_zero: bool) -> bool:
    return math.isfinite(value) and value <= 1 and (value >= 0 if allow_zero else value > 0)


def _validate(parser: argparse.ArgumentParser, args) -> dict[str, list] | None:
    """Check every argument before any model work. Exits with status 2 and a message on a problem."""
    if not args.weights.is_file():
        parser.error(f"--weights: file not found: {args.weights}")
    seen = set()
    for path in args.images:
        if not path.is_file():
            parser.error(f"--images: file not found: {path}")
        if path.name in seen:
            parser.error(f"--images: the file name {path.name!r} appears more than once; names must be unique")
        seen.add(path.name)
    if args.warmup < 0:
        parser.error("--warmup must be 0 or more")
    if args.iterations < 1:
        parser.error("--iterations must be at least 1")
    if args.list_detections_above is not None and not _in_unit_range(args.list_detections_above, allow_zero=True):
        parser.error("--list-detections-above must be a finite number between 0 and 1")

    annotations = None
    if args.annotations is None:
        extras = [flag for flag, value in (
            ("--person-label", args.person_labels), ("--min-confidence", args.min_confidence),
            ("--min-iou", args.min_iou),
        ) if value is not None]
        if extras:
            parser.error(f"{', '.join(extras)} only apply with --annotations")
    else:
        if not args.person_labels:
            parser.error("--person-label is required with --annotations (repeat it for several labels)")
        if args.min_confidence is None or args.min_iou is None:
            parser.error("--min-confidence and --min-iou are required with --annotations")
        if not _in_unit_range(args.min_confidence, allow_zero=True):
            parser.error("--min-confidence must be a finite number between 0 and 1")
        if not _in_unit_range(args.min_iou, allow_zero=False):
            parser.error("--min-iou must be a finite number greater than 0 and at most 1")
        try:
            annotations = _read_annotations(args.annotations, [path.name for path in args.images])
        except ValueError as error:
            parser.error(str(error))
    if args.output is not None:
        if args.output.exists():
            parser.error(f"--output: {args.output} already exists; choose a new file name (existing files are never overwritten)")
        if not args.output.parent.is_dir():
            parser.error(f"--output: the folder {args.output.parent} does not exist")
    return annotations


def run_benchmark(args, annotations: dict[str, list] | None) -> dict:
    checkpoint_started = perf_counter()
    checkpoint_sha256 = file_sha256(args.weights)
    checkpoint_hash_seconds = perf_counter() - checkpoint_started
    if checkpoint_sha256 is None:
        raise BenchmarkError(f"cannot read the weights file {args.weights.name}")

    dimensions = {}
    for path in args.images:  # fail on unreadable images before loading the model
        image = _decode(path)
        dimensions[path.name] = image.size

    load_started = perf_counter()
    try:
        detector = load_detector(args.weights)
    except PersonDetectorError as error:
        raise BenchmarkError(f"could not load the checkpoint: {error}") from error
    load_seconds = perf_counter() - load_started
    memory_after_load = peak_memory_bytes()

    comparing = annotations is not None
    timed = _TimedDetector(detector)
    service = DetectionEvidenceService(person_labels=args.person_labels, detector=timed) if comparing else None

    def process(path: Path) -> dict:
        started = perf_counter()
        image = _decode(path)
        decoded = perf_counter()
        incomplete = False
        matching_ms = None
        try:
            if comparing:
                evidence = service.collect(
                    image, annotations[path.name],
                    min_confidence=args.min_confidence, min_iou=args.min_iou,
                )
                if evidence.status not in (EvidenceStatus.COMPARED, EvidenceStatus.COMPARED_INCOMPLETE):
                    raise BenchmarkError(
                        f"detection did not complete for {path.name}: {evidence.status.value}"
                        f" ({evidence.reason.value if evidence.reason else 'no reason recorded'})"
                    )
                incomplete = evidence.status == EvidenceStatus.COMPARED_INCOMPLETE
                detections = evidence.detector_output.detections
            else:
                detections = timed.detect(image, DETECTOR_INFERENCE_CUTOFF).detections
        except InferenceError as error:
            raise BenchmarkError(f"inference failed for {path.name}: {error}") from error
        finished = perf_counter()
        detector_ms = timed.last_seconds * 1000
        if comparing:
            matching_ms = max(0.0, (finished - decoded) * 1000 - detector_ms)
        return {
            "decode_ms": (decoded - started) * 1000, "detector_ms": detector_ms,
            "matching_ms": matching_ms, "total_ms": (finished - started) * 1000,
            "incomplete": incomplete, "detections": detections,
        }

    for _ in range(args.warmup):
        for path in args.images:
            process(path)

    series = {"decode_ms": [], "detector_ms": [], "matching_ms": [], "total_ms": []}
    per_image_totals = {path.name: [] for path in args.images}
    listed = {}
    incomplete_samples = 0
    for iteration in range(args.iterations):
        for path in args.images:
            sample = process(path)
            for key in series:
                if sample[key] is not None:
                    series[key].append(sample[key])
            per_image_totals[path.name].append(sample["total_ms"])
            incomplete_samples += sample["incomplete"]
            if iteration == 0 and args.list_detections_above is not None:
                listed[path.name] = [
                    detection.model_dump() for detection in sample["detections"]
                    if detection.score >= args.list_detections_above
                ]
    memory_after_measurement = peak_memory_bytes()

    files = []
    for path in args.images:
        width, height = dimensions[path.name]
        entry = {
            "file_name": path.name, "width": width, "height": height,
            "median_total_ms": median(per_image_totals[path.name]),
        }
        if path.name in listed:
            entry["detections"] = listed[path.name]
        files.append(entry)

    return {
        "benchmark": "cpu_detection",
        "architecture": MODEL_NAME,
        "checkpoint_sha256": checkpoint_sha256,
        "environment": environment_info(),
        "settings": {
            "comparison": comparing,
            "person_labels": sorted(set(args.person_labels)) if comparing else None,
            "min_confidence": args.min_confidence,
            "min_iou": args.min_iou,
            "detector_inference_cutoff": DETECTOR_INFERENCE_CUTOFF,
            "list_detections_above": args.list_detections_above,
            "warmup_passes": args.warmup,
            "measured_passes": args.iterations,
        },
        "images": {"count": len(args.images), "files": files},
        "model_load": {
            "checkpoint_hash_seconds": checkpoint_hash_seconds,
            "load_seconds": load_seconds,
        },
        "timings": {
            "decode_ms": summarize(series["decode_ms"]),
            "detector_ms": summarize(series["detector_ms"]),
            "matching_ms": summarize(series["matching_ms"]) if comparing else None,
            "total_ms": summarize(series["total_ms"]),
        },
        "incomplete_comparison_samples": incomplete_samples if comparing else None,
        "peak_process_memory": {
            "unit": "bytes",
            "after_model_load": memory_after_load,
            "after_measurement": memory_after_measurement,
        },
        "measurement_notes": MEASUREMENT_NOTES,
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    annotations = _validate(parser, args)
    try:
        report = run_benchmark(args, annotations)
    except BenchmarkError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    text = json.dumps(report, indent=2, allow_nan=False)
    if args.output is not None:
        try:
            with args.output.open("x", encoding="utf-8") as stream:  # "x" refuses to overwrite
                stream.write(text + "\n")
        except OSError as error:
            print(f"error: could not write {args.output}: {error}", file=sys.stderr)
            return 1
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
