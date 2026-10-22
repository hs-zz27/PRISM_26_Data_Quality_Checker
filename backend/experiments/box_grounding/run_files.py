"""Run directory layout: write-once settings and summary, append-only progress and results.

An existing run directory is never reused or overwritten, and there is no resume. A partial
or failed run stays exactly as it was written; a new attempt needs a new directory.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
from typing import Any

RUN_FILE = "run.json"
PROGRESS_FILE = "progress.jsonl"
RESULTS_FILE = "results.jsonl"
SUMMARY_FILE = "summary.json"
SCORE_FILE = "score.json"
CROPS_DIRECTORY = "crops"
DEFAULT_RUNS_ROOT = Path(".cache/box-grounding/runs")


class RunFilesError(RuntimeError):
    """A run directory problem the operator can fix by choosing a new directory."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def encode_json(value: Any) -> bytes:
    """UTF-8 and LF on every platform, so run records are portable and hashable."""
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()


def encode_line(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode()


class RunFiles:
    def __init__(self, directory: Path):
        self.directory = directory

    def require_absent(self) -> None:
        """Fail fast, before any network contact, if this name would reuse an existing run."""
        if self.directory.exists():
            raise RunFilesError(
                f"Run directory already exists: {self.directory}. Existing runs are preserved; "
                "choose a new --run-name."
            )

    def create(self) -> None:
        self.require_absent()
        try:
            self.directory.mkdir(parents=True)
        except FileExistsError as error:
            raise RunFilesError(f"Run directory already exists: {self.directory}.") from error
        (self.directory / CROPS_DIRECTORY).mkdir()

    def write_once(self, name: str, content: bytes) -> Path:
        target = self.directory / name
        try:
            with target.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError as error:
            raise RunFilesError(f"Refusing to overwrite {target}.") from error
        return target

    def write_document(self, name: str, document: Any) -> Path:
        return self.write_once(name, encode_json(document))

    def append(self, name: str, record: dict[str, Any]) -> None:
        with (self.directory / name).open("ab") as stream:
            stream.write(encode_line(record))
            stream.flush()
            os.fsync(stream.fileno())

    def event(self, event: str, **fields: Any) -> None:
        self.append(PROGRESS_FILE, {"event": event, "time": utc_now(), **fields})

    def save_crop(self, case_name: str, box_number: int, png: bytes) -> str:
        relative = f"{CROPS_DIRECTORY}/{case_name}/box-{box_number:02d}.png"
        folder = self.directory / CROPS_DIRECTORY / case_name
        folder.mkdir(exist_ok=True)
        self.write_once(relative, png)
        return relative


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_bytes())


def read_lines(path: Path) -> list[dict[str, Any]]:
    """Read JSON lines, tolerating one truncated final line from an interrupted write."""
    if not path.exists():
        return []
    lines = path.read_bytes().split(b"\n")
    records = []
    for number, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            if number == len(lines) - 1 or not any(rest.strip() for rest in lines[number + 1:]):
                break  # a final partial line from an interrupted write
            raise RunFilesError(f"Corrupt record in {path.name} at line {number + 1}.") from None
    return records
