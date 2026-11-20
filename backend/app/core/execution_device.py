"""Whether the local models may use a GPU.

One setting, ``PRISM_EXECUTION_DEVICE``, read from the process environment or the root ``.env``:

- ``cpu`` (the default): the person model runs on the CPU and every Ollama request forbids GPU layers.
- ``auto``: the person model uses an NVIDIA GPU, else an Apple GPU, else the CPU; Ollama places the
  language model itself, using a GPU when it has one.

Kept free of Torch so the API can report the setting without loading a model.
"""
import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values

from .config import DEFAULT_ENV_PATH

DEVICE_ENV = "PRISM_EXECUTION_DEVICE"
ExecutionDevice = Literal["cpu", "auto"]


def execution_device(*, env_path: str | Path | None = None) -> ExecutionDevice:
    """The configured setting. An unknown value is an error, never a silent choice."""
    path = DEFAULT_ENV_PATH if env_path is None else Path(env_path)
    values = {**dotenv_values(path, interpolate=False), **os.environ}
    raw = (values.get(DEVICE_ENV) or "cpu").strip().lower()
    if raw not in ("cpu", "auto"):
        raise ValueError(f"{DEVICE_ENV} must be 'cpu' or 'auto'.")
    return raw  # type: ignore[return-value]


def ollama_device_options() -> dict[str, int]:
    """Ollama request options for the setting: ``num_gpu: 0`` forbids GPU layers; leaving it out lets Ollama decide."""
    return {"num_gpu": 0} if execution_device() == "cpu" else {}
