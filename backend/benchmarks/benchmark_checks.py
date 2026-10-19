"""Small CPU timing workloads; no database, models, or test runner required.

Run from the project root with:
    .venv/bin/python -B -m backend.benchmarks.benchmark_checks

Optionally pass --baseline-dir containing the earlier geometry.py and
segmentation.py to compare both implementations within the same process.
Synthetic collector/kernel timings exclude process startup, snapshot staging,
rule evaluation and PostgreSQL, so they are not complete audit-job throughput.
"""

import argparse
import importlib.util
import json
from pathlib import Path
from statistics import median
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from types import FunctionType

import cv2
import numpy as np
from PIL import Image

from backend.app.checks import geometry, segmentation
from backend.app.services import audit


def load_baseline(directory: Path, name: str):
    module_name = f"backend.app.checks._benchmark_baseline_{name}"
    spec = importlib.util.spec_from_file_location(module_name, directory / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def milliseconds(operation, repeats: int, iterations: int) -> float:
    operation()
    samples = []
    for _ in range(repeats):
        started = perf_counter()
        for _ in range(iterations):
            operation()
        samples.append((perf_counter() - started) * 1000 / iterations)
    return median(samples)


def sample_workload(root: Path, count: int):
    """Include source decoding and all collector checks in a synthetic sample."""
    for directory in ("PNGImages", "PedMasks", "Annotation"):
        (root / directory).mkdir(parents=True)
    mask = np.zeros((1024, 2048), dtype=np.uint16)
    lines = [
        "Image size (X x Y x C) : 2048 x 1024 x 3",
        f'Objects with ground truth : {count} {{ "person" }}',
        "Top left pixel co-ordinates : (1, 1)",
    ]
    for index in range(count):
        left = 50 + index * 100
        mask[120:632, left:left + 64] = index + 1
        lines.append(
            f'Bounding box for object {index + 1} "person" (Xmin, Ymin) - (Xmax, Ymax) : '
            f'({left + 1}, 121) - ({left + 64}, 632)'
        )
    Image.new("RGB", (2048, 1024)).save(root / "PNGImages/sample.png")
    Image.fromarray(mask).save(root / "PedMasks/sample_mask.png")
    (root / "Annotation/sample.txt").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--iterations", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1 or args.iterations < 1:
        parser.error("repeats and iterations must be positive")
    old_segmentation = load_baseline(args.baseline_dir, "segmentation") if args.baseline_dir else None
    old_geometry = load_baseline(args.baseline_dir, "geometry") if args.baseline_dir else None
    cases = []
    for name, bounds in (
        ("compact_instance", (400, 120, 464, 632)),
        ("full_image_instance", (0, 0, 2048, 1024)),
    ):
        mask = np.zeros((1024, 2048), dtype=np.uint16)
        left, top, right, bottom = bounds
        mask[top:bottom, left:right] = 1
        obj = {"object_id": 1, "label": "person", "bbox": (left + 1, top + 1, right, bottom)}
        cases.append((name, lambda m=mask, o=obj: segmentation.measure_instance_mask(o, m),
                      None if old_segmentation is None else
                      lambda m=mask, o=obj: old_segmentation.measure_instance_mask(o, m)))
    for count in (10, 50, 100):
        objects = [{"object_id": i + 1, "label": "person", "bbox": (i + 1, 1, i + 20, 50)}
                   for i in range(count)]
        cases.append((f"box_pairs_{count}_objects",
                      lambda items=objects: geometry.measure_pairwise_box_iou(items, (2048, 1024)),
                      None if old_geometry is None else
                      lambda items=objects: old_geometry.measure_pairwise_box_iou(items, (2048, 1024))))
    results = []
    with TemporaryDirectory(prefix="prism-cpu-benchmark-") as working:
        # Keep the collector identical and replace only the two changed kernels
        # in its private globals; no production module is monkeypatched.
        old_audit = None if old_segmentation is None else FunctionType(
            audit.audit_sample.__code__,
            {**audit.audit_sample.__globals__,
             "measure_instance_mask": old_segmentation.measure_instance_mask,
             "measure_pairwise_box_iou": old_geometry.measure_pairwise_box_iou},
            argdefs=audit.audit_sample.__defaults__,
        )
        for count in (2, 10):
            root = Path(working) / str(count)
            sample_workload(root, count)
            before = None if old_audit is None else milliseconds(
                lambda: old_audit(root, "sample", None), args.repeats, args.iterations,
            )
            after = milliseconds(
                lambda: audit.audit_sample(root, "sample", None), args.repeats, args.iterations,
            )
            results.append({
                "workload": f"sample_collection_{count}_objects",
                "baseline_ms": before, "current_ms": after,
                "speedup": None if before is None else before / after,
            })
    for name, current, baseline in cases:
        before = None if baseline is None else milliseconds(baseline, args.repeats, args.iterations)
        after = milliseconds(current, args.repeats, args.iterations)
        results.append({
            "workload": name, "baseline_ms": before, "current_ms": after,
            "speedup": None if before is None else before / after,
        })
    print(json.dumps({
        "opencv_threads": cv2.getNumThreads(), "repeats": args.repeats,
        "iterations_per_repeat": args.iterations, "median_timings": results,
    }, indent=2))


if __name__ == "__main__":
    main()
