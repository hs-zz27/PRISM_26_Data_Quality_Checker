# CPU detection evidence

This package runs the existing CPU person detector on one decoded image,
compares its detections with that image's annotations, and returns typed
evidence for later audit integration. It builds on the merged
`PersonDetector` and `match_detections_to_annotations` and does not change them.

It produces evidence only: no findings, pass/fail outcomes, reviewer decisions
or annotation corrections. Model output is fallible and never establishes that
an annotation is right or wrong.

This package is **not** audit integration, annotation correction, rechecking or
feedback learning. Real-model validation and benchmark runs are described below
but have **not been performed** for this delivery; no performance number is
claimed.

Files:

- `app/models/detection_evidence.py`: result models and status enums.
- `app/services/detection_evidence.py`: `DetectionEvidenceService`.
- `benchmarks/benchmark_detection.py`: validation and CPU timing command line.
- `tests/test_detection_evidence.py`, `tests/test_detection_benchmark.py`.

## Download the pretrained weights

Use `fasterrcnn_mobilenet_v3_large_320_fpn` with the explicit
`FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.COCO_V1` weights from
[Torchvision](https://docs.pytorch.org/vision/stable/models/generated/torchvision.models.detection.fasterrcnn_mobilenet_v3_large_320_fpn.html).
The checkpoint is approximately **77.84 MB (74.24 MiB)**. It is already pretrained;
no training or Ollama model is needed for this detector.

With the project's Python 3.12 environment activated and its pinned dependencies
installed, run this from the **project root** in Bash or Zsh:

```bash
python -B - <<'PY'
import hashlib
from pathlib import Path

import torch
from torchvision.models.detection import (
    FasterRCNN_MobileNet_V3_Large_320_FPN_Weights,
)

directory = Path(".cache/torch/hub/checkpoints")
directory.mkdir(parents=True, exist_ok=True)
weights = FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.COCO_V1
checkpoint = directory / Path(weights.url).name
expected_prefix = "907ea3f9"

if not checkpoint.exists():
    torch.hub.download_url_to_file(
        weights.url, str(checkpoint), hash_prefix=expected_prefix, progress=True,
    )

with checkpoint.open("rb") as stream:
    checksum = hashlib.file_digest(stream, "sha256").hexdigest()
if not checksum.startswith(expected_prefix):
    raise SystemExit(f"Checksum mismatch: {checkpoint}. Move the file aside and retry.")

print(f"Checkpoint: {checkpoint}")
print(f"SHA-256: {checksum}")
PY
```

On PowerShell, save the Python body between the `PY` markers to a temporary
`.py` file outside the checkout and run it with the activated environment's
`python`, keeping the working directory at the project root.

This explicitly downloads from the official weight URL only when the file is
missing and checks the expected SHA-256 prefix for both new and cached files.
It does not load the model or run inference. The resulting checkpoint is:

```text
.cache/torch/hub/checkpoints/fasterrcnn_mobilenet_v3_large_320_fpn-907ea3f9.pth
```

Keep the weights local: `.cache/` is already excluded by `.gitignore`. Each
developer downloads their own copy; do not commit the checkpoint. Application
imports and detector setup never download it automatically.

A successful download verifies file availability and checksum only. Follow
[Validating a real checkpoint](#validating-a-real-checkpoint) to load the model
and inspect detections on representative images; accuracy and CPU benchmarks
remain separate validation steps.

## Service interface

```python
class DetectionEvidenceService:
    def __init__(
        self, *, person_labels: Collection[str],
        detector: DetectorLike | None = None,
        checkpoint_path: str | Path | None = None,
    ): ...

    def collect(
        self, image: PIL.Image.Image,
        annotations: Sequence[AnnotationObject] | None,
        *, min_confidence: float, min_iou: float,
    ) -> DetectionEvidence: ...
```

- Provide exactly one of `detector` (any object with
  `detect(image, min_score) -> DetectionResult`, used by tests) or
  `checkpoint_path` (an existing local checkpoint).
- Constructing the service is the explicit setup step. For a checkpoint it
  hashes the file once and loads the model once. Importing the module does no
  model work, reads no checkpoint and changes no Torch settings.
- `min_confidence` must be finite in [0, 1] and `min_iou` finite in (0, 1].
  Booleans are rejected. There are no defaults.
- `annotations` uses the existing `AnnotationObject` shape. An empty list is a
  real sample with no objects. `None` means the annotations were unavailable.

```python
from PIL import Image
from backend.app.services.detection_evidence import DetectionEvidenceService

service = DetectionEvidenceService(
    person_labels=["person"],  # the exact labels your rules treat as people
    checkpoint_path=".cache/torch/hub/checkpoints/fasterrcnn_mobilenet_v3_large_320_fpn-907ea3f9.pth",
)
image = Image.open("example.png")
annotations = [
    {"object_id": 1, "label": "person", "bbox": (11, 21, 60, 120)},
    {"object_id": 2, "label": "car", "bbox": (70, 80, 150, 140)},
]
evidence = service.collect(image, annotations, min_confidence=0.5, min_iou=0.5)

print(evidence.status)
for pair in evidence.matching.matches:
    print(pair.detection.index, pair.annotation.index, pair.iou)
payload = evidence.model_dump(mode="json")  # plain JSON-compatible data
```

Reuse one service for many images. Reuse holds within one service instance; a
separate spawned process loads its own copy.

## Label scope

`person_labels` is required and has no default. Labels are matched exactly and
case-sensitively, so `"person"` and `"PASpersonWalking"` are different labels
unless you list both. Annotations with any other label are reported in
`excluded_annotations` (original index, object ID, label) and are never compared;
they are neither unmatched nor invalid. An annotation whose label cannot be read
is passed to the matcher and reported as an invalid record.

## Coordinates and references

- Detector boxes: zero-based, continuous pixel-edge `[x_min, y_min, x_max, y_max]`
  in original-image pixels (a full-image box is `(0, 0, width, height)`).
- Annotation boxes: one-based, inclusive integers. Matching converts them to
  `[xmin - 1, ymin - 1, xmax, ymax]`, so `[1, 1, 1, 1]` becomes `[0, 0, 1, 1]`.
- Detection references are positions in `detector_output.detections`.
- Annotation references are positions in the caller's original list, even after
  label filtering. Duplicate object IDs stay separate records.

## Status and unavailable results

| `status` | Meaning | `reason` |
| --- | --- | --- |
| `compared` | Detection and comparison completed. | none |
| `compared_incomplete` | Compared, but some records were invalid (see `matching.invalid_records`). | none |
| `comparison_unavailable` | Detection completed; annotations were unavailable, so nothing was compared. Detections are kept. | `annotations_unavailable` |
| `detector_unavailable` | The checkpoint is missing or unusable. | `weights_missing` or `weights_unusable` |
| `inference_failed` | The detector ran but failed. | `inference_failed` |

- Successful inference with zero detections is `compared` with an empty
  detection list. Failures are never reported as empty results.
- Ambiguous matching groups are normal `compared` evidence, left for review.
- Public evidence holds reason codes only: no checkpoint paths, user names,
  stack traces or raw exception text. The internal exception is logged and, for
  setup problems, kept on `service.setup_failure`.
- Only `WeightsLoadError` and `InferenceError` become evidence. Other errors,
  such as a wrong argument type, propagate to the caller.

## Thresholds

The detector is asked for every person candidate it produces (score cutoff
`0.0`) and the matcher applies `min_confidence`. Detections below it are listed
in `matching.confidence_filtered_detections` and are not called unmatched. When
annotations are unavailable no threshold is applied, so `detector_output` holds
every candidate. torchvision still discards some candidates internally (low
region-proposal scores, non-maximum suppression and a per-image detection
limit); those were never examined.

## Checkpoint requirements and identity

- An existing local checkpoint for `fasterrcnn_mobilenet_v3_large_320_fpn`.
  Nothing is downloaded and random weights are never substituted.
- `detector_identity.checkpoint_sha256` is the SHA-256 of the file, computed once
  at setup and checked again after loading; a file that changed while loading is
  reported as `weights_unusable`. It is `null` when the file cannot be read.
- An injected test detector reports `checkpoint_source: "injected_detector"` and
  no hash. No hash is invented.

## Illustrative results

Hand-written examples, not program output. Long constant text is shortened.

```json
{
  "status": "compared",
  "reason": null,
  "image_width": 100, "image_height": 100,
  "min_confidence": 0.5, "min_iou": 0.5,
  "person_labels": ["person"],
  "detector_identity": {
    "architecture": "fasterrcnn_mobilenet_v3_large_320_fpn",
    "checkpoint_source": "local_checkpoint",
    "checkpoint_sha256": "<64 lowercase hex characters>"
  },
  "detector_output": {
    "image_width": 100, "image_height": 100, "min_score": 0.0,
    "model_name": "fasterrcnn_mobilenet_v3_large_320_fpn",
    "weights_file": "<checkpoint file name>",
    "coordinate_convention": "...",
    "detections": [{"x_min": 0.0, "y_min": 0.0, "x_max": 10.0, "y_max": 10.0, "score": 0.9}]
  },
  "annotations_available": true, "annotation_count": 2,
  "excluded_annotations": [{"index": 1, "object_id": 2, "label": "car"}],
  "matching": {
    "policy": "connected_components_v1", "min_confidence": 0.5, "min_iou": 0.5,
    "matches": [{
      "detection": {"index": 0, "score": 0.9},
      "annotation": {"index": 0, "object_id": 1, "label": "person"},
      "iou": 1.0
    }],
    "ambiguous_groups": [], "unmatched_detections": [], "unmatched_annotations": [],
    "confidence_filtered_detections": [], "invalid_records": [],
    "comparison_complete": true
  },
  "matching_policy": "connected_components_v1",
  "limitations": "..."
}
```

```json
{
  "status": "detector_unavailable",
  "reason": "weights_missing",
  "image_width": 100, "image_height": 100,
  "min_confidence": 0.5, "min_iou": 0.5,
  "person_labels": ["person"],
  "detector_identity": {
    "architecture": "fasterrcnn_mobilenet_v3_large_320_fpn",
    "checkpoint_source": "local_checkpoint",
    "checkpoint_sha256": null
  },
  "detector_output": null,
  "annotations_available": true, "annotation_count": 2,
  "excluded_annotations": [],
  "matching": null,
  "limitations": "..."
}
```

## Synthetic tests

These use a fake detector and need no database, Ollama, dataset or weights. They
were authored but **have not been run**. From the project root, with the virtual
environment active:

```bash
python -B -m pytest backend/tests/test_detection_evidence.py backend/tests/test_detection_benchmark.py -q --tb=short -p no:cacheprovider
```

Also rerun the existing suites for the modules this builds on:

```bash
python -B -m pytest backend/tests/test_person_detector.py backend/tests/test_detection_matching.py -q -p no:cacheprovider
```

Synthetic images and randomly initialized weights check interfaces only, never
detection quality.

## Validating a real checkpoint

Needs an existing pretrained checkpoint and a small set of representative
images. Everything below only reads those files.

1. Check that the architecture loads the checkpoint and list detections:

   ```bash
   python -B -m backend.benchmarks.benchmark_detection \
     --weights PATH/TO/fasterrcnn_mobilenet_v3_large_320_fpn-907ea3f9.pth \
     --images a.jpg b.jpg c.jpg --warmup 0 --iterations 1 \
     --list-detections-above 0.5 --output validation.json
   ```

   A load failure, unreadable image or inference failure stops the run with a
   message and a nonzero exit status. On PowerShell, put the command on one line.
2. Open `validation.json`. Each image lists its detections as
   `x_min, y_min, x_max, y_max, score` in original-image pixels (zero-based,
   top-left origin).
3. Look at each image yourself and note obvious detections, obvious misses and
   ambiguous cases. Choose the score cutoff you review at deliberately.
4. The report records the architecture, checkpoint SHA-256, Python, Torch and
   torchvision versions and your settings. Keep it with your notes.
5. Keep two questions apart: "did it run?" (this tool) and "is it accurate?"
   (your review). Do not compute precision or recall without independently
   reviewed ground truth and a defined matching procedure.

## Benchmark

Detector only (no annotations):

```bash
python -B -m backend.benchmarks.benchmark_detection \
  --weights PATH/TO/checkpoint.pth --images a.jpg b.jpg \
  --warmup 2 --iterations 10 --output detector_only.json
```

With matching:

```bash
python -B -m backend.benchmarks.benchmark_detection \
  --weights PATH/TO/checkpoint.pth --images a.jpg b.jpg \
  --annotations annotations.json --person-label person \
  --min-confidence 0.5 --min-iou 0.5 \
  --warmup 2 --iterations 10 --output with_matching.json
```

Annotation file: a JSON object mapping each image file name to its annotations.
Boxes are `[xmin, ymin, xmax, ymax]`, one-based and inclusive. Every image needs
an entry (`[]` for no objects), and names must match `--images`.

```json
{
  "a.jpg": [{"object_id": 1, "label": "person", "bbox": [10, 20, 50, 90]}],
  "b.jpg": []
}
```

An existing `--output` file is never overwritten. Run `--help` for all options.

Reading the report:

- `model_load`: checkpoint hashing and model construction, measured once. The
  per-image timings exclude it.
- `timings.decode_ms`: reading and decoding the image file.
- `timings.detector_ms`: `PersonDetector.detect`, including its RGB and tensor
  conversion. Preprocessing cannot be separated without changing the detector.
- `timings.matching_ms`: label scoping, matching and evidence construction,
  with annotations only.
- `timings.total_ms`: decode plus detection plus matching.
- Each timing has count, min, median, mean and max in milliseconds. `p95_ms` uses
  the nearest-rank method and appears only with at least 20 samples.
- `peak_process_memory` is the process-lifetime high-water mark in bytes (peak
  resident set; peak working set on Windows), including Python, Torch and the
  model. It is not per-image memory.
- Warm-up passes are not measured. The first measured pass may still benefit from
  file caching.

These numbers cover this detection path only. They exclude database work,
snapshot staging and process supervision, so they are not audit throughput. This
package recommends no batch size: choose one after measuring on the target
hardware with representative images.

## Known limitations

- No real-model validation or benchmark has been run for this delivery.
- Model reuse holds within one service instance, not across separate spawned
  audit processes. Each process would load and hash its own copy.
- Detector scores are not calibrated probabilities.
- Hashing and loading happen in the constructor, so building a service is slow
  compared with a `collect` call.
- Image EXIF rotation is not applied; boxes refer to the pixels as decoded.
- The benchmark reads local image files. It does not write overlays, and it does
  not use the Penn-Fudan layout.
- On macOS and Linux memory comes from `getrusage`; on Windows from the process
  counters. Other platforms may report no value.

## Proposed seam for audit integration

Not implemented here. The audit sample step would:

1. Build one `DetectionEvidenceService` per worker process, with the person
   labels and checkpoint path from configuration, before processing samples.
2. For each sample, call `collect(image, annotations, min_confidence=..., min_iou=...)`
   with the decoded image, the sample's annotations (or `None` if they could not
   be read) and thresholds from the confirmed rule.
3. Store `evidence.model_dump(mode="json")` as measurement evidence. Turning it
   into findings stays a separate, rule-driven step: `detector_unavailable`,
   `inference_failed` and `comparison_unavailable` need to appear as unavailable
   evidence, not as a pass.

Questions for coordination: how rules supply `person_labels` and thresholds, and
whether spawned workers should keep a long-lived process to avoid reloading the
model for every sample.
