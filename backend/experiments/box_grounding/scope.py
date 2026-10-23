"""Load the label-free prepared scope and verify it against the bytes on disk.

The scope contains source evidence only. It never carries expected labels, review
history or training-example answers, and this module refuses to load one that does.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from typing import Any

from PIL import Image

from backend.app.datasets.penn_fudan import parse_annotations
from backend.app.models.feedback import EvidenceLimits

DEVELOPMENT = "development"
SCOPE_SCHEMA_VERSION = 1
MAX_SCOPE_BYTES = 8 * 1024**2
# Key names that would indicate human judgments leaked into inference inputs.
FORBIDDEN_KEYS = frozenset({
    "expected", "decision", "reviewer_id", "reviewed_at", "controlled_perturbation",
    "adjudication", "training_example", "history", "model_predictions_hidden",
})


class ScopeError(ValueError):
    """The prepared scope is not the label-free, verified evidence this experiment needs."""


@dataclass(frozen=True)
class ScopeCase:
    case_name: str
    case_id: str
    partition: str
    image_path: Path
    annotation_path: Path
    image_bytes: bytes
    annotation_bytes: bytes
    inputs: dict[str, Any]

    @property
    def objects(self) -> list[dict[str, Any]]:
        return self.inputs["objects"]

    @property
    def image_size(self) -> list[int]:
        return self.inputs["image_size"]


@dataclass(frozen=True)
class Scope:
    path: Path
    sha256: str
    document: dict[str, Any]
    guideline: str
    rule_id: str
    cases: tuple[ScopeCase, ...]

    def development_cases(self) -> tuple[ScopeCase, ...]:
        return tuple(case for case in self.cases if case.partition == DEVELOPMENT)


def case_identity(case_name: str, image_sha256: str, annotation_sha256: str, guideline: str) -> str:
    """Recompute the identity prepare.py assigned, so a tampered scope cannot pass."""
    identity = dict(case_name=case_name, image_sha256=image_sha256,
                    annotation_sha256=annotation_sha256, guideline=guideline)
    return sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def reject_label_fields(value: object, trail: str = "scope") -> None:
    """Fail on any human-judgment field anywhere in the document."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_KEYS:
                raise ScopeError(f"Scope contains a label or review field ({trail}.{key}).")
            reject_label_fields(child, f"{trail}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_label_fields(child, f"{trail}[{index}]")


def read_limited(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ScopeError(f"Byte limit exceeded: {path.name}")
    return data


def contained(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ScopeError(f"Scope path leaves the prepared directory: {relative}")
    return target


def load_case(root: Path, entry: dict[str, Any], guideline: str, limits: EvidenceLimits) -> ScopeCase:
    name = entry["case_name"]
    inputs = entry["inputs"]
    image_path = contained(root, entry["image"])
    annotation_path = contained(root, entry["annotation"])
    image = read_limited(image_path, limits.max_image_bytes)
    annotation = read_limited(annotation_path, limits.max_annotation_bytes)
    if sha256(image).hexdigest() != inputs["image_sha256"]:
        raise ScopeError(f"Prepared image bytes differ from the scope hash: {name}")
    if sha256(annotation).hexdigest() != inputs["annotation_sha256"]:
        raise ScopeError(f"Prepared annotation bytes differ from the scope hash: {name}")
    annotation_text = annotation.decode("utf-8", errors="strict")
    if annotation_text != inputs["annotation_text"]:
        raise ScopeError(f"Annotation text differs from the prepared file: {name}")
    try:
        objects = json.loads(json.dumps(parse_annotations(annotation_text)))
    except ValueError as error:
        raise ScopeError(f"Prepared annotation could not be parsed: {name}") from error
    # Hashing the annotation file alone does not bind the copied object list.
    # Both arms must use exactly the facts represented by the verified bytes.
    if objects != inputs["objects"]:
        raise ScopeError(f"Object facts differ from the prepared annotation: {name}")
    with Image.open(BytesIO(image)) as opened:
        if opened.format != "PNG" or getattr(opened, "n_frames", 1) != 1:
            raise ScopeError(f"Prepared image must be a single-frame PNG: {name}")
        if list(opened.size) != inputs["image_size"]:
            raise ScopeError(f"Image dimensions differ from the prepared file: {name}")
        opened.verify()
    expected_id = case_identity(name, inputs["image_sha256"], inputs["annotation_sha256"], guideline)
    if entry["case_id"] != expected_id:
        raise ScopeError(f"Case identity does not match its evidence: {name}")
    return ScopeCase(case_name=name, case_id=entry["case_id"], partition=entry["partition"],
                     image_path=image_path, annotation_path=annotation_path,
                     image_bytes=image, annotation_bytes=annotation, inputs=inputs)


def load_scope(path: Path, limits: EvidenceLimits | None = None) -> Scope:
    """Read scope.json and every case file, verifying hashes, identities and label-freedom."""
    limits = EvidenceLimits() if limits is None else limits
    raw = read_limited(path, MAX_SCOPE_BYTES)
    try:
        document = json.loads(raw)
    except ValueError as error:
        raise ScopeError("scope.json is not valid JSON.") from error
    if not isinstance(document, dict) or document.get("schema_version") != SCOPE_SCHEMA_VERSION:
        raise ScopeError("Unsupported scope schema version.")
    reject_label_fields(document)
    entries = document.get("cases")
    if not isinstance(entries, list) or not entries:
        raise ScopeError("Scope has no cases.")
    names = [entry.get("case_name") for entry in entries]
    if len(set(names)) != len(names):
        raise ScopeError("Case names must be unique.")
    guideline, rule_id = document["guideline"], document["rule_id"]
    try:
        cases = tuple(load_case(path.parent, entry, guideline, limits) for entry in entries)
    except (KeyError, TypeError, OSError, UnicodeError) as error:
        raise ScopeError(f"Scope case could not be verified: {error!r}") from error
    return Scope(path=path, sha256=sha256(raw).hexdigest(), document=document,
                 guideline=guideline, rule_id=rule_id, cases=cases)


def changed_since_load(scope: Scope) -> list[str]:
    """Names of cases whose files on disk no longer match the bytes that were verified."""
    changed = []
    for case in scope.cases:
        if (case.image_path.read_bytes() != case.image_bytes
                or case.annotation_path.read_bytes() != case.annotation_bytes):
            changed.append(case.case_name)
    return changed
