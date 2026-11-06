"""Local Mask R-CNN person segmenter. It sees only the photo, never the annotation, rule or feedback.

Loads torchvision ``maskrcnn_resnet50_fpn_v2`` (COCO weights) from a local checkpoint on the CPU; nothing is
downloaded. Returns person outlines plus things people carry (backpack, umbrella, handbag, suitcase), which an
annotation may include. Pass/fail decisions belong to ``services.person_coverage``.
"""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torchvision.models.detection import maskrcnn_resnet50_fpn_v2
from torchvision.transforms.functional import to_tensor

from ..core.person_model import CHECKPOINT_NAME, MODEL_NAME, checkpoint_path

PERSON = 1
CARRIED = {27, 28, 31, 33}  # COCO: backpack, umbrella, handbag, suitcase
MIN_SCORE = 0.5
MASK_PROBABILITY = 0.5


class SegmenterUnavailable(RuntimeError):
    """No usable checkpoint, or the model failed. Findings become unavailable, never pass."""


@dataclass(frozen=True)
class Person:
    mask: np.ndarray  # HxW bool
    box: tuple[int, int, int, int]  # inclusive pixel extent x0, y0, x1, y1
    score: float


@dataclass(frozen=True)
class Segmentation:
    people: list[Person]
    carried: list[np.ndarray]


def extent(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


class PersonSegmenter:
    def __init__(self, path: Path):
        try:
            model = maskrcnn_resnet50_fpn_v2(weights=None, weights_backbone=None)
            model.load_state_dict(torch.load(path, map_location="cpu", weights_only=True))
        except Exception as error:  # a corrupt or wrong file fails in several ways
            raise SegmenterUnavailable(f"Cannot load {path.name}: {type(error).__name__}") from error
        self.model = model.eval()
        self.identity = {"model": MODEL_NAME, "checkpoint_sha256": sha256(path.read_bytes()).hexdigest(),
                         "min_score": MIN_SCORE, "mask_probability": MASK_PROBABILITY}

    def segment(self, image: Image.Image) -> Segmentation:
        try:
            with torch.no_grad():
                prediction = self.model([to_tensor(image.convert("RGB"))])[0]
        except Exception as error:
            raise SegmenterUnavailable(f"Inference failed: {type(error).__name__}") from error
        people, carried = [], []
        for label, score, soft in zip(prediction["labels"].tolist(), prediction["scores"].tolist(), prediction["masks"]):
            if score < MIN_SCORE:
                continue
            mask = soft[0].numpy() >= MASK_PROBABILITY
            if not mask.any():
                continue
            if label == PERSON:
                people.append(Person(mask=mask, box=extent(mask), score=round(float(score), 4)))
            elif label in CARRIED:
                carried.append(mask)
        return Segmentation(people=people, carried=carried)


_segmenter: PersonSegmenter | None = None


def get_segmenter() -> PersonSegmenter:
    """One model per process (audit samples run in worker child processes)."""
    global _segmenter
    if _segmenter is None:
        path = checkpoint_path()
        if path is None:
            raise SegmenterUnavailable(f"No person segmenter checkpoint ({CHECKPOINT_NAME}) is installed.")
        _segmenter = PersonSegmenter(path)
    return _segmenter
