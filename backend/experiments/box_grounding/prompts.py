"""Request payloads for the raw-image control and the per-box crop candidate.

Builders receive source evidence and the rule only. They never receive an expected label,
a reviewer decision or a training answer; those are read later, by scoring alone.
Both variants share the exact historical system prompt. The candidate's per-box wording
lives in the user message and is versioned in its own file, so the intervention is
"crops plus explicit per-box wording", not a pure pixel-only change.
"""

from __future__ import annotations

from base64 import b64encode
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints

from backend.app.models.feedback import Digest, Outcome, Prediction, Record
from backend.app.models.identifiers import Identifier
from backend.app.models.rule import Rule

from .crops import CROP_PREPROCESSING, BoxCrop
from .scope import Scope, ScopeCase
from .settings import ExecutionSettings
from .views import MAX_BOXES

PACKET = Path(__file__).resolve().parent
RAW_VARIANT = "raw-image-v1"
CROP_VARIANT = "box-crop-v1"
CROP_V2_VARIANT = "box-crop-v2"
CROP_VARIANTS = (CROP_VARIANT, CROP_V2_VARIANT)
DEFAULT_ARMS = (RAW_VARIANT, CROP_VARIANT)
VARIANTS = (RAW_VARIANT, *CROP_VARIANTS)
RAW_PREPROCESSING = "original-png-no-transform-v1"
PREPROCESSING = {RAW_VARIANT: RAW_PREPROCESSING, **{arm: CROP_PREPROCESSING for arm in CROP_VARIANTS}}
RAW_SCHEMA_VERSION = "prediction-v1"
BOX_SCHEMA_VERSION = "box-prediction-v1"
PER_BOX_INSTRUCTION_VERSION = "per-box-instruction-v1"
SYSTEM_PROMPT_VERSION = "annotation-guideline-v1"
# Production sends the evidence fields in this order; keep it for the control.
INPUT_ORDER = ("annotation_text", "objects", "image_size", "image_sha256", "annotation_sha256")


class ContextNotRepresentable(ValueError):
    """The complete request cannot fit the configured bounds. Context is never truncated."""

    code = "context_limit"


class BoxPrediction(Record):
    """One box's proposal. Identity fields must be echoed exactly or the result is unavailable."""

    case_id: Digest
    rule_id: Identifier
    box_number: int = Field(ge=1, le=MAX_BOXES)
    object_id: int
    outcome: Outcome
    reason: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    evidence_references: list[Literal["image", "annotations", "guideline"]] = Field(max_length=3)


@dataclass(frozen=True)
class PromptTexts:
    system: str
    system_sha256: str
    per_box_instruction: str
    per_box_instruction_sha256: str


@dataclass(frozen=True)
class PreparedRequest:
    payload: dict[str, Any]
    body: bytes  # the exact bytes sent, so the hash below describes the real request
    sha256: str
    text_bytes: int
    image_count: int


def read_text_exactly(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    return raw.decode("utf-8", errors="strict"), sha256(raw).hexdigest()


def load_prompt_texts(packet: Path = PACKET) -> PromptTexts:
    system, system_hash = read_text_exactly(packet / "system_prompt_v1.txt")
    instruction, instruction_hash = read_text_exactly(packet / "per_box_instruction_v1.txt")
    return PromptTexts(system, system_hash, instruction, instruction_hash)


def rule_document(scope: Scope) -> dict[str, Any]:
    """The guideline as a production Rule. The historical rule JSON is not in the handoff,
    so this is a documented reconstruction, identical for both variants."""
    rule = Rule(rule_id=scope.rule_id, description=scope.guideline, origin="reviewer",
                confirmation_state="confirmed")
    return rule.model_dump(mode="json")


def prepare(texts: PromptTexts, content: str, images: list[bytes], schema: dict[str, Any],
            execution: ExecutionSettings) -> PreparedRequest:
    """Reject unrepresentable context instead of truncating it, using the production bounds."""
    text_bytes = len((texts.system + content + json.dumps(schema)).encode())
    reserved = execution.num_predict + 1024 * len(images)
    if text_bytes > max(0, execution.num_ctx - reserved) // 2:
        raise ContextNotRepresentable("The text context does not fit the configured window.")
    options: dict[str, Any] = {"num_gpu": 0, "temperature": 0, "num_ctx": execution.num_ctx,
                               "num_predict": execution.num_predict}
    if execution.num_thread is not None:
        options["num_thread"] = execution.num_thread
    payload = {
        "model": execution.model, "stream": False, "keep_alive": execution.keep_alive_seconds,
        "messages": [{"role": "system", "content": texts.system},
                     {"role": "user", "content": content,
                      "images": [b64encode(image).decode("ascii") for image in images]}],
        "format": schema, "options": options,
    }
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
    if len(body) > execution.max_request_bytes:
        raise ContextNotRepresentable("The request exceeds the configured byte limit.")
    return PreparedRequest(payload, body, sha256(body).hexdigest(), text_bytes, len(images))


def raw_request(scope: Scope, case: ScopeCase, texts: PromptTexts,
                execution: ExecutionSettings) -> PreparedRequest:
    """The control: the original image bytes and all annotation evidence in one request."""
    inputs = {key: case.inputs[key] for key in INPUT_ORDER}
    content = json.dumps({
        "case_id": case.case_id, "rule_id": scope.rule_id, "guideline": rule_document(scope),
        "examples": [], "target": {"image_attachment": 1, "inputs": inputs},
    }, ensure_ascii=False, allow_nan=False)
    return prepare(texts, content, [case.image_bytes], Prediction.model_json_schema(), execution)


def crop_request(scope: Scope, case: ScopeCase, crop: BoxCrop, texts: PromptTexts,
                 execution: ExecutionSettings, *, variant: str = CROP_VARIANT) -> PreparedRequest:
    """One crop; v2 omits source geometry from model input, retaining it in local records."""
    if variant not in CROP_VARIANTS:
        raise ValueError(f"Unsupported crop variant: {variant}")
    document = {
        "case_id": case.case_id, "rule_id": scope.rule_id, "guideline": rule_document(scope),
        "task": {"variant": variant, "instruction_version": PER_BOX_INSTRUCTION_VERSION,
                 "instruction": texts.per_box_instruction},
        "box": {"box_number": crop.box_number, "object_id": crop.object_id, "label": crop.label,
                "bbox": list(crop.bbox),
                "bbox_convention": "one-based inclusive source pixels (xmin, ymin, xmax, ymax)",
                "crop_size": [crop.width, crop.height], "source_image_size": case.image_size},
        "target": {"image_attachment": 1},
    }
    if variant == CROP_V2_VARIANT:
        for field in ("bbox", "bbox_convention", "source_image_size"):
            del document["box"][field]
    content = json.dumps(document, ensure_ascii=False, allow_nan=False)
    return prepare(texts, content, [crop.png], BoxPrediction.model_json_schema(), execution)


def raw_identity(scope: Scope, case: ScopeCase) -> dict[str, Any]:
    return {"case_id": case.case_id, "rule_id": scope.rule_id}


def crop_identity(scope: Scope, case: ScopeCase, crop: BoxCrop) -> dict[str, Any]:
    return {"case_id": case.case_id, "rule_id": scope.rule_id,
            "box_number": crop.box_number, "object_id": crop.object_id}
