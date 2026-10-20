"""Load a cached torchvision person detector and run it on the CPU.

The detector only reports evidence. Matching it against annotations and any
pass/fail decision belong to later, separate steps.
"""

from math import isfinite
from numbers import Real
from pathlib import Path

import torch
from PIL import Image
from torchvision.models.detection import fasterrcnn_mobilenet_v3_large_320_fpn
from torchvision.transforms.functional import to_tensor

from ..models.detection import DetectionResult, PersonDetection

MODEL_NAME = "fasterrcnn_mobilenet_v3_large_320_fpn"
PERSON_LABEL = 1  # COCO category id for "person"


class PersonDetectorError(Exception):
    """Base class for detector failures."""


class WeightsLoadError(PersonDetectorError):
    """The checkpoint is missing, unreadable or does not match the architecture."""


class InferenceError(PersonDetectorError):
    """The model failed or returned output that could not be used."""


def _load_model(weights_path: Path) -> torch.nn.Module:
    """Build the architecture and load a local checkpoint. Nothing is downloaded."""
    if not weights_path.is_file():
        raise WeightsLoadError(f"Detector weights not found: {weights_path}")
    try:
        # torchvision drops boxes below 0.05 by default; 0.0 lets min_score decide.
        model = fasterrcnn_mobilenet_v3_large_320_fpn(
            weights=None, weights_backbone=None, box_score_thresh=0.0,
        )
        state = torch.load(weights_path, map_location="cpu", weights_only=True)
        model.load_state_dict(state)
    except Exception as error:  # corrupt files fail in several different ways
        raise WeightsLoadError(f"Could not load detector weights from {weights_path}: {error}") from error
    return model.eval()


class PersonDetector:
    """Holds one CPU model and reuses it for every call to `detect`."""

    def __init__(self, weights_path: str | Path):
        self._weights_path = Path(weights_path)
        self._model = _load_model(self._weights_path)

    def detect(self, image: Image.Image, min_score: float) -> DetectionResult:
        """Return people scoring at least `min_score`, in original-image pixels."""
        if not isinstance(image, Image.Image):
            raise TypeError("image must be a PIL image")
        if isinstance(min_score, bool) or not isinstance(min_score, Real) or not isfinite(min_score):
            raise ValueError("min_score must be a finite number")
        if not 0 <= min_score <= 1:
            raise ValueError("min_score must be between 0 and 1")

        width, height = image.size
        try:
            tensor = to_tensor(image.convert("RGB"))  # convert returns a copy
            with torch.inference_mode():
                output = self._model([tensor])[0]
            boxes = output["boxes"].tolist()
            labels = output["labels"].tolist()
            scores = output["scores"].tolist()
            if not len(boxes) == len(labels) == len(scores):
                raise ValueError("boxes, labels and scores differ in length")
            detections = [
                PersonDetection(
                    # float error from resizing can overshoot the edge slightly
                    x_min=max(0.0, x_min), y_min=max(0.0, y_min),
                    x_max=min(float(width), x_max), y_max=min(float(height), y_max),
                    score=score,
                )
                for (x_min, y_min, x_max, y_max), label, score in zip(boxes, labels, scores)
                if label == PERSON_LABEL and score >= min_score
            ]
        except Exception as error:
            raise InferenceError(f"Person detection failed: {error}") from error

        return DetectionResult(
            image_width=width, image_height=height, min_score=float(min_score),
            model_name=MODEL_NAME, weights_file=self._weights_path.name,
            detections=detections,
        )