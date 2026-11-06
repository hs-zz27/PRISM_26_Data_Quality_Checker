"""Where the person check's Mask R-CNN weights live.

Kept free of Torch so the API can report whether the file is installed without loading the model.
"""
import os
from pathlib import Path

MODEL_NAME = "maskrcnn_resnet50_fpn_v2_coco"
CHECKPOINT_NAME = "maskrcnn_resnet50_fpn_v2_coco-73cbd019.pth"
CHECKPOINT_ENV = "PERSON_SEGMENTER_CHECKPOINT"
_DEFAULT_LOCATIONS = (Path("/models") / CHECKPOINT_NAME,
                      Path(__file__).resolve().parents[2] / "data" / "models" / CHECKPOINT_NAME)


def checkpoint_path() -> Path | None:
    """The installed checkpoint file, or None. Only checks that the file exists."""
    configured = (os.environ.get(CHECKPOINT_ENV) or "").strip()
    candidates = [Path(configured)] if configured else list(_DEFAULT_LOCATIONS)
    return next((path for path in candidates if path.is_file()), None)
