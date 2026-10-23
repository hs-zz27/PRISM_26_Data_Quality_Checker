"""Synthetic corpus and fake local Ollama server shared by the runner tests.

No dataset, model or database is needed. Pixel colors differ per box, so crop tests can
prove exactly which source pixels reached the model.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import shutil
from typing import Any

import httpx
from PIL import Image

from backend.experiments.box_grounding.prepare import PACKET, prepare, write_json
from backend.experiments.box_grounding.runner import RunConfig, run_experiment
from backend.experiments.box_grounding.scope import load_scope

GUIDELINE = "Each annotation box must enclose a visible pedestrian; a box entirely on background is a violation."
MODEL = "qwen3-vl:4b-instruct"
DIGEST = "sha256:" + "ab" * 32
HISTORICAL = json.loads((PACKET / "reference_results/reviewed_comparison.json").read_bytes())["execution"]["model_digest"]
BACKGROUND = (60, 60, 60)
COLORS = [(10, 200, 10), (10, 10, 200), (200, 200, 10), (10, 200, 200), (200, 10, 200)]
Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class CaseSpec:
    name: str
    partition: str
    size: tuple[int, int]
    boxes: tuple[tuple[int, str, Box], ...]
    decision: str = "pass"


DEFAULT_SPECS = (
    CaseSpec("case-02", "development", (12, 9), (
        (1, "PASpersonStanding", (1, 1, 4, 3)), (1, "PASpersonStanding", (5, 4, 8, 6)),
        (3, "PASpersonWalking", (9, 7, 12, 9))), decision="fail"),
    CaseSpec("case-03", "development", (8, 6), ((1, "PASpersonStanding", (2, 2, 5, 5)),)),
    CaseSpec("case-04", "development", (10, 8), (
        (1, "PASpersonStanding", (1, 1, 3, 8)), (2, "PASpersonWalking", (6, 1, 10, 4)))),
    CaseSpec("case-06", "training", (6, 6), ((1, "PASpersonStanding", (1, 1, 6, 6)),)),
    CaseSpec("case-13", "development", (6, 5), ()),  # zero annotated objects
)


def painted_png(spec: CaseSpec) -> bytes:
    image = Image.new("RGB", spec.size, BACKGROUND)
    for color, (_, _, (xmin, ymin, xmax, ymax)) in zip(COLORS, spec.boxes):
        for x in range(xmin - 1, xmax):
            for y in range(ymin - 1, ymax):
                image.putpixel((x, y), color)
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def annotation_text(spec: CaseSpec) -> bytes:
    names = " ".join(f'"{label}"' for _, label, _ in spec.boxes)
    lines = [f"Image size (X x Y x C) : {spec.size[0]} x {spec.size[1]} x 3",
             f"Objects with ground truth : {len(spec.boxes)} {{ {names} }}",
             "Top left pixel co-ordinates : (1, 1)"]
    for object_id, label, (x0, y0, x1, y1) in spec.boxes:
        lines.append(f'Bounding box for object {object_id} "{label}" (Xmin, Ymin) - (Xmax, Ymax) : '
                     f"({x0}, {y0}) - ({x1}, {y1})")
    return ("\n".join(lines) + "\n").encode()


@dataclass(frozen=True)
class Corpus:
    root: Path
    packet: Path
    scope_path: Path
    labels_path: Path
    specs: tuple[CaseSpec, ...]
    dataset: Path

    def scope(self):
        return load_scope(self.scope_path)

    def spec(self, name: str) -> CaseSpec:
        return next(spec for spec in self.specs if spec.name == name)


def build_corpus(root: Path, specs: tuple[CaseSpec, ...] = DEFAULT_SPECS) -> Corpus:
    dataset, packet = root / "dataset", root / "packet"
    (dataset / "PNGImages").mkdir(parents=True)
    (dataset / "Annotation").mkdir()
    packet.mkdir()
    cases = []
    for spec in specs:
        image, annotation = painted_png(spec), annotation_text(spec)
        (dataset / f"PNGImages/{spec.name}.png").write_bytes(image)
        (dataset / f"Annotation/{spec.name}.txt").write_bytes(annotation)
        cases.append(dict(
            case_name=spec.name, partition=spec.partition, image=f"PNGImages/{spec.name}.png",
            image_sha256=sha256(image).hexdigest(), image_size=list(spec.size),
            original_annotation=f"Annotation/{spec.name}.txt",
            original_annotation_sha256=sha256(annotation).hexdigest(),
            evaluation_annotation_sha256=sha256(annotation).hexdigest(), annotation_override=None,
            objects=[dict(object_id=i, label=label, bbox=list(box)) for i, label, box in spec.boxes],
            controlled_perturbation=False))
    write_json(packet / "manifest.json", dict(schema_version=1, corpus_version="synthetic-v1",
                                              guideline=GUIDELINE, cases=cases))
    (packet / "system_prompt_v1.txt").write_bytes(b"Synthetic system prompt.")
    shutil.copy(PACKET / "per_box_instruction_v1.txt", packet / "per_box_instruction_v1.txt")
    shutil.copy(PACKET / "context_instruction_v1.txt", packet / "context_instruction_v1.txt")
    shutil.copytree(PACKET / "reference_results", packet / "reference_results")
    prepare(root / "inputs", dataset_root=dataset, packet=packet)
    labels = [dict(case_id=s.name, decision=s.decision, partition=s.partition, reviewer_id="synthetic")
              for s in specs]
    labels_path = root / "labels.json"
    write_json(labels_path, dict(protocol="synthetic", limitations=[], cases=labels))
    return Corpus(root, packet, root / "inputs/scope.json", labels_path, specs, dataset)


# --- fake local Ollama ----------------------------------------------------------------------------

def always_pass(content: dict[str, Any]) -> tuple[str, str]:
    return "pass", "A pedestrian is visible."


def failing_boxes(*boxes: Box) -> Callable[[dict[str, Any]], tuple[str, str]]:
    """Fail a crop request for those boxes, and a raw request that contains any of them."""
    wanted = {tuple(box) for box in boxes}

    def decide(content: dict[str, Any]) -> tuple[str, str]:
        if "box" in content:
            hit = tuple(content["box"]["bbox"]) in wanted
        else:
            hit = any(tuple(o["bbox"]) in wanted for o in content["target"]["inputs"]["objects"])
        return ("fail", "Only background is visible.") if hit else always_pass(content)

    return decide


@dataclass
class FakeOllama:
    digest: str = DIGEST
    model: str = MODEL
    capabilities: list[str] = field(default_factory=lambda: ["completion", "vision"])
    decide: Callable[[dict[str, Any]], tuple[str, str]] = always_pass
    # behavior(chat_number, default_reply, request) -> None | dict | httpx.Response, or it may raise
    behavior: Callable[[int, dict[str, Any], httpx.Request], Any] | None = None
    digest_after_chat: str | None = None
    show_extra: dict[str, Any] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    chat_bodies: list[dict[str, Any]] = field(default_factory=list)
    chat_raw: list[bytes] = field(default_factory=list)
    chat_count: int = 0
    in_flight: int = 0
    max_in_flight: int = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            self.calls.append((request.method, request.url.path))
            path = request.url.path
            if path == "/api/tags":
                return httpx.Response(200, json={"models": [
                    {"name": self.model, "digest": self.digest.removeprefix("sha256:")}]})
            if path == "/api/show":
                return httpx.Response(200, json={"capabilities": list(self.capabilities), **self.show_extra})
            if path == "/api/version":
                return httpx.Response(200, json={"version": "0.0.0-test"})
            if path == "/api/chat":
                return self.chat(request)
            return httpx.Response(404, json={"error": "unknown path"})
        finally:
            self.in_flight -= 1

    def chat(self, request: httpx.Request) -> httpx.Response:
        self.chat_count += 1
        body = json.loads(request.content)
        self.chat_raw.append(request.content)
        self.chat_bodies.append(body)
        content = json.loads(body["messages"][1]["content"])
        outcome, reason = self.decide(content)
        prediction: dict[str, Any] = {"case_id": content["case_id"], "rule_id": content["rule_id"],
                                      "outcome": outcome, "reason": reason, "evidence_references": ["image"]}
        if "box" in content:
            prediction.update(box_number=content["box"]["box_number"], object_id=content["box"]["object_id"])
        reply = {"model": self.model, "done": True, "done_reason": "stop", "prompt_eval_count": 600,
                 "eval_count": 30, "total_duration": 1000,
                 "message": {"role": "assistant", "content": json.dumps(prediction)}}
        override = None if self.behavior is None else self.behavior(self.chat_count, reply, request)
        if self.digest_after_chat:
            self.digest = self.digest_after_chat
        if isinstance(override, httpx.Response):
            return override
        return httpx.Response(200, json=reply if override is None else override)


def make_config(corpus: Corpus, run_dir: Path, **overrides: Any) -> RunConfig:
    return RunConfig(scope_path=corpus.scope_path, run_dir=run_dir, **overrides)


def run_with(corpus: Corpus, fake: FakeOllama, run_dir: Path, **overrides: Any):
    """Run the experiment against the fake server. Returns (summary, console lines)."""
    lines: list[str] = []
    summary = run_experiment(make_config(corpus, run_dir, **overrides), transport=fake.transport(),
                             say=lines.append, packet=corpus.packet)
    return summary, lines


def read_results(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "results.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_events(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "progress.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
