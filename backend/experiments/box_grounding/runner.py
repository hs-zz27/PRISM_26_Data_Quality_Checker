"""Sequential experiment runner: one model request at a time, nothing hidden.

Compares the raw-image control with the per-box crop candidate over the development cases.
Inference never reads expected labels; scoring is a separate step. Settings, versions and
hashes are written before the first model call, every result is appended as soon as it
exists, and failures stay in the record as explicit "unavailable" outcomes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from typing import Any, Literal

import httpx

from backend.app.datasets.penn_fudan import parse_annotation_metadata
from backend.app.models.feedback import EvidenceLimits, Prediction

from .client import INFRASTRUCTURE_REASONS, InferenceUnavailable, ModelCall, OllamaClient
from .crops import CROP_PREPROCESSING, BoxCrop, render_box_crops, validate_scope
from .prompts import (
    BOX_SCHEMA_VERSION, CROP_VARIANT, CROP_VARIANTS, CROP_V2_VARIANT, DEFAULT_ARMS, PACKET,
    PER_BOX_INSTRUCTION_VERSION, PREPROCESSING, RAW_SCHEMA_VERSION,
    RAW_VARIANT, SYSTEM_PROMPT_VERSION, VARIANTS, BoxPrediction, ContextNotRepresentable, PreparedRequest,
    PromptTexts, crop_identity, crop_request, load_prompt_texts, raw_identity, raw_request, rule_document,
)
from .run_files import RESULTS_FILE, RUN_FILE, SUMMARY_FILE, RunFiles, utc_now
from .scoring import BoxVerdict, aggregate_sample
from .scope import DEVELOPMENT, Scope, ScopeCase, changed_since_load, load_scope
from .settings import DEFAULT_HOST, DEFAULT_MODEL, ExecutionSettings, collect_environment
from .views import RenderError, ViewLimits

EXPERIMENT_VERSION = "box-grounding-per-box-v1"
RUN_SCHEMA = "box-grounding-run-v1"
RESULT_SCHEMA = "box-grounding-result-v1"
SUMMARY_SCHEMA = "box-grounding-summary-v1"
PLACEHOLDER_DIGEST = "sha256:" + "0" * 64
DIGEST_PATTERN = re.compile("sha256:[0-9a-f]{64}")
Order = Literal["interleaved", "blocked"]
Say = Callable[[str], None]
TIMING_CAVEATS = (
    "Requests run one at a time on one machine. Ollama may keep the model and its prompt prefix "
    "warm, so the first request and the arm that runs first can differ in timing.",
    "Interleaved order alternates which arm runs first for each case; blocked order runs one arm "
    "completely before the other. Neither removes thermal or background-load effects.",
    "Durations are model-call time including the verification requests, as in the historical "
    "records; they exclude rendering, preparation and any overhead outside the call.",
)


class RunError(RuntimeError):
    """An operator-facing problem found before or during a run."""


def print_line(text: str) -> None:
    print(text, flush=True)


def ascii_text(text: str) -> str:
    """Console-safe text; Windows consoles may not encode arbitrary model output."""
    return text.encode("ascii", "replace").decode("ascii")


@dataclass(frozen=True)
class RunConfig:
    scope_path: Path
    run_dir: Path
    arms: tuple[str, ...] = DEFAULT_ARMS
    order: Order = "interleaved"
    cases: tuple[str, ...] | None = None
    host: str = DEFAULT_HOST
    model: str = DEFAULT_MODEL
    digest_request: str = "installed"  # installed, historical, or an explicit sha256:... digest
    num_ctx: int = 16384
    num_predict: int = 2048
    num_thread: int | None = None  # None keeps automatic thread selection
    timeout_seconds: int = 600
    max_request_bytes: int = 64 * 1024**2
    max_response_bytes: int = 65536
    keep_alive_seconds: int = 300
    max_consecutive_failures: int = 3
    dry_run: bool = False

    def __post_init__(self) -> None:
        if not self.arms or len(set(self.arms)) != len(self.arms) or not set(self.arms) <= set(VARIANTS):
            raise RunError(f"Arms must be a non-empty, duplicate-free subset of {list(VARIANTS)}.")
        if len(set(self.arms) & set(CROP_VARIANTS)) > 1:
            raise RunError("Choose one crop version per run, optionally paired with the raw control.")
        if self.order not in ("interleaved", "blocked"):
            raise RunError("Order must be interleaved or blocked.")
        if self.digest_request not in ("installed", "historical") and not DIGEST_PATTERN.fullmatch(self.digest_request):
            raise RunError("Model digest must be installed, historical or sha256:<64 hex digits>.")
        if self.max_consecutive_failures < 1:
            raise RunError("max_consecutive_failures must be at least 1.")

    def execution(self, digest: str) -> ExecutionSettings:
        return ExecutionSettings(
            host=self.host, model=self.model, model_digest=digest, num_ctx=self.num_ctx,
            num_predict=self.num_predict, num_thread=self.num_thread, timeout_seconds=self.timeout_seconds,
            max_request_bytes=self.max_request_bytes, max_response_bytes=self.max_response_bytes,
            keep_alive_seconds=self.keep_alive_seconds,
        )


# --- planning -----------------------------------------------------------------------------------

def select_cases(scope: Scope, names: tuple[str, ...] | None) -> tuple[ScopeCase, ...]:
    """Development cases only. Training cases are never used in this first comparison."""
    development = scope.development_cases()
    if names is None:
        selected = development
    else:
        by_name = {case.case_name: case for case in scope.cases}
        unknown = [name for name in names if name not in by_name]
        wrong = [name for name in names if name in by_name and by_name[name].partition != DEVELOPMENT]
        if unknown or wrong or len(set(names)) != len(names):
            raise RunError(f"Cases must be distinct development cases. Unknown: {unknown}; not development: {wrong}.")
        selected = tuple(case for case in development if case.case_name in names)
    if not selected:
        raise RunError("The scope has no development cases to run.")
    return selected


def plan_units(cases: tuple[ScopeCase, ...], arms: tuple[str, ...], order: Order) -> list[tuple[str, ScopeCase]]:
    if order == "blocked":
        return [(arm, case) for arm in arms for case in cases]
    units = []
    for index, case in enumerate(cases):
        # Alternate who goes first so neither arm always benefits from a warm cache.
        units.extend((arm, case) for arm in (arms if index % 2 == 0 else tuple(reversed(arms))))
    return units


def case_origin(case: ScopeCase) -> tuple[int, int]:
    origin = parse_annotation_metadata(case.inputs["annotation_text"])["coordinate_origin"]
    return tuple(origin) if origin is not None else (0, 0)  # type: ignore[return-value]


@dataclass(frozen=True)
class CasePreflight:
    """Everything that can be decided without the model, so unrepresentable work is explicit."""

    case: ScopeCase
    gate_error: str | None  # sample-level rejection shared by both arms
    raw: PreparedRequest | None
    raw_error: str | None
    crops: tuple[BoxCrop, ...]
    crop_error: str | None
    box_requests: tuple[PreparedRequest | None, ...]
    box_errors: tuple[str | None, ...]
    render_ms: float


def preflight_case(scope: Scope, case: ScopeCase, arms: tuple[str, ...], texts: PromptTexts,
                   execution: ExecutionSettings) -> CasePreflight:
    limits = ViewLimits(execution.max_image_bytes, execution.max_pixels)
    try:
        validate_scope(case.image_bytes, case.objects, case_origin(case), limits)
    except RenderError as error:
        return CasePreflight(case, str(error), None, None, (), None, (), (), 0.0)
    raw = raw_error = crop_error = None
    crops: tuple[BoxCrop, ...] = ()
    requests: list[PreparedRequest | None] = []
    errors: list[str | None] = []
    render_ms = 0.0
    if RAW_VARIANT in arms:
        try:
            raw = raw_request(scope, case, texts, execution)
        except ContextNotRepresentable as error:
            raw_error = error.code
    crop_variant = next((arm for arm in arms if arm in CROP_VARIANTS), None)
    if crop_variant is not None:
        start = time.monotonic()
        try:
            crops = render_box_crops(case.image_bytes, case.objects, case_origin(case), limits)
        except RenderError as error:
            crop_error = str(error)
        render_ms = (time.monotonic() - start) * 1000
        for crop in crops:
            try:
                requests.append(crop_request(scope, case, crop, texts, execution, variant=crop_variant))
                errors.append(None)
            except ContextNotRepresentable as error:
                requests.append(None)
                errors.append(error.code)
    return CasePreflight(case, None, raw, raw_error, crops, crop_error, tuple(requests), tuple(errors), render_ms)


def format_dry_run(preflights: list[CasePreflight], units: list[tuple[str, ScopeCase]], config: RunConfig) -> str:
    lines = [f"Dry run: {len(units)} planned units over {len(preflights)} cases; nothing is sent or written.",
             f"Order: {config.order}. Arms: {', '.join(config.arms)}."]
    chat_requests = 0
    for item in preflights:
        name, boxes = item.case.case_name, len(item.case.objects)
        if item.gate_error:
            lines.append(f"  {name}: {boxes} boxes -> REJECTED before any request ({item.gate_error}); "
                         "retained as unavailable in both arms")
            continue
        parts = []
        if item.raw is not None or item.raw_error:
            chat_requests += item.raw is not None
            parts.append(f"raw {item.raw.text_bytes} text bytes / {len(item.raw.body)} request bytes"
                         if item.raw else f"raw rejected ({item.raw_error})")
        if item.crop_error:
            parts.append(f"crops rejected ({item.crop_error})")
        elif item.box_requests:
            ok = [r for r in item.box_requests if r is not None]
            chat_requests += len(ok)
            largest = max((len(r.body) for r in ok), default=0)
            parts.append(f"{len(ok)}/{len(item.box_requests)} crop requests (largest {largest} bytes)")
        lines.append(f"  {name}: {boxes} boxes; " + "; ".join(parts))
    lines.append(f"Total chat requests that would be sent: {chat_requests}.")
    return "\n".join(lines)


# --- provenance ---------------------------------------------------------------------------------

def historical_digest(packet: Path) -> str | None:
    path = packet / "reference_results" / "reviewed_comparison.json"
    if not path.exists():
        return None
    digest = json.loads(path.read_bytes()).get("execution", {}).get("model_digest")
    return digest if isinstance(digest, str) else None


def resolve_digest(client: OllamaClient, request: str, packet: Path) -> dict[str, Any]:
    """Pin the digest for the whole run. A digest differing from history is reported, not hidden."""
    try:
        installed = client.installed_digest()
    except InferenceUnavailable as error:
        raise RunError(f"Cannot identify the installed model ({error}). Is Ollama running with the model pulled?") from error
    historical = historical_digest(packet)
    if request == "installed":
        pinned = installed
    elif request == "historical":
        if historical is None:
            raise RunError("No historical digest is recorded in this checkout.")
        pinned = historical
    else:
        pinned = request
    if installed != pinned:
        raise RunError(f"Installed digest {installed} differs from the requested {pinned}; stopping before any inference.")
    return {"requested": request, "installed": installed, "historical": historical, "pinned": pinned,
            "installed_matches_historical": None if historical is None else installed == historical}


def code_fingerprints(packet: Path) -> dict[str, str]:
    names = sorted(p.name for p in packet.iterdir() if p.suffix in (".py", ".txt") and p.is_file())
    return {name: sha256((packet / name).read_bytes()).hexdigest() for name in names}


def schema_hash(schema: dict[str, Any]) -> str:
    return sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def variant_descriptions(texts: PromptTexts) -> dict[str, Any]:
    descriptions = {
        RAW_VARIANT: {"preprocessing": PREPROCESSING[RAW_VARIANT], "output_schema": RAW_SCHEMA_VERSION,
                      "requests_per_case": 1, "per_box_wording": False, "examples": 0},
        CROP_VARIANT: {"preprocessing": CROP_PREPROCESSING, "output_schema": BOX_SCHEMA_VERSION,
                       "requests_per_case": "one per annotation box", "per_box_wording": True, "examples": 0,
                       "instruction_version": PER_BOX_INSTRUCTION_VERSION,
                       "instruction_sha256": texts.per_box_instruction_sha256,
                       "crop_padding_pixels": 0, "crop_resize": "none", "surrounding_context": "none"},
    }
    descriptions[CROP_VARIANT]["source_geometry_in_model_input"] = True
    descriptions[CROP_V2_VARIANT] = {
        **descriptions[CROP_VARIANT], "source_geometry_in_model_input": False,
        "source_geometry_in_local_records": True,
    }
    return descriptions


# --- the run ------------------------------------------------------------------------------------

class Runner:
    def __init__(self, config: RunConfig, scope: Scope, texts: PromptTexts, preflights: list[CasePreflight],
                 units: list[tuple[str, ScopeCase]], files: RunFiles, client: OllamaClient,
                 execution: ExecutionSettings, say: Say):
        self.config, self.scope, self.texts = config, scope, texts
        self.preflight = {item.case.case_name: item for item in preflights}
        self.units, self.files, self.client, self.execution, self.say = units, files, client, execution, say
        self.records: list[dict[str, Any]] = []
        self.model_calls = 0
        self.consecutive_infrastructure_failures = 0
        self.aborted_reason: str | None = None

    # progress ---------------------------------------------------------------------------------
    def note_failure(self, reason: str | None, outcome: str) -> None:
        """Count consecutive server-side failures. Aborting is a safety stop, never a retry."""
        if outcome == "unavailable" and reason in INFRASTRUCTURE_REASONS:
            self.consecutive_infrastructure_failures += 1
        else:
            self.consecutive_infrastructure_failures = 0
        if self.consecutive_infrastructure_failures >= self.config.max_consecutive_failures:
            self.aborted_reason = (f"{self.consecutive_infrastructure_failures} consecutive server failures "
                                   f"(last: {reason}). Check Ollama and the settings, then start a new run.")

    # one box / one sample ---------------------------------------------------------------------
    def box_record(self, crop: BoxCrop, saved: str, request: PreparedRequest | None,
                   call: ModelCall | None, error: str | None) -> dict[str, Any]:
        prediction = None if call is None or call.prediction is None else call.prediction.model_dump(mode="json")
        verified = bool(call and call.model_verified)
        outcome = prediction["outcome"] if prediction else "unavailable"
        reason = prediction["reason"] if prediction else (error or (call.reason if call else None))
        return {
            "box_number": crop.box_number, "annotation_index": crop.annotation_index,
            "object_id": crop.object_id, "label": crop.label, "bbox": list(crop.bbox),
            "pil_box": list(crop.pil_box), "crop_size": [crop.width, crop.height],
            "crop_sha256": crop.png_sha256, "crop_file": saved,
            "request_sha256": request.sha256 if request else None,
            "outcome": outcome, "reason": reason,
            "evidence_references": prediction["evidence_references"] if prediction else [],
            "model_verified": verified, "chat_attempted": bool(call and call.chat_attempted),
            "duration_ms": call.duration_ms if call else 0.0, "chat_ms": call.chat_ms if call else None,
            "metrics": call.metrics if call else {}, "content": call.content if call else None,
        }

    def run_crops(self, item: CasePreflight, arm: str) -> dict[str, Any]:
        boxes: list[dict[str, Any]] = []
        total = len(item.crops)
        for position, crop in enumerate(item.crops):
            request, error = item.box_requests[position], item.box_errors[position]
            saved = self.files.save_crop(item.case.case_name, crop.box_number, crop.png)
            label = f"    box {crop.box_number}/{total} (object {crop.object_id})"
            self.files.event("box_started", arm=arm, case_name=item.case.case_name, box_number=crop.box_number)
            call = None
            if request is None:
                self.say(f"{label}: not sent ({error}); retained as unavailable")
            else:
                self.say(f"{label}: sending crop {crop.width}x{crop.height} ...")
                call = self.client.chat(request, BoxPrediction,
                                        crop_identity(self.scope, item.case, crop))
                self.model_calls += call.chat_attempted
            record = self.box_record(crop, saved, request, call, error)
            boxes.append(record)
            self.files.event("box_finished", arm=arm, case_name=item.case.case_name,
                             box_number=crop.box_number, outcome=record["outcome"], reason=record["reason"],
                             duration_ms=record["duration_ms"])
            self.say(f"{label}: {record['outcome']} in {record['duration_ms'] / 1000:.1f} s"
                     + ("" if record["model_verified"] or request is None else f" ({ascii_text(str(record['reason']))})"))
            self.note_failure(record["reason"] if not record["model_verified"] else None, record["outcome"])
            if self.aborted_reason:
                break
        return {"boxes": boxes}

    def run_raw(self, item: CasePreflight) -> dict[str, Any]:
        request = item.raw
        assert request is not None
        self.say(f"    sending the full image and all {len(item.case.objects)} annotation boxes ...")
        call = self.client.chat(request, Prediction, raw_identity(self.scope, item.case))
        self.model_calls += call.chat_attempted
        prediction = None if call.prediction is None else call.prediction.model_dump(mode="json")
        self.note_failure(None if call.model_verified else call.reason, prediction["outcome"] if prediction else "unavailable")
        return {"prediction": prediction, "failure": call.reason, "model_verified": call.model_verified,
                "chat_attempted": call.chat_attempted, "request_sha256": request.sha256, "metrics": call.metrics,
                "content": call.content, "duration_ms": call.duration_ms, "chat_ms": call.chat_ms}

    def rejected(self, item: CasePreflight, arm: str) -> str | None:
        if item.gate_error:
            return item.gate_error
        if arm == RAW_VARIANT:
            return item.raw_error
        return item.crop_error

    def run_unit(self, arm: str, item: CasePreflight) -> dict[str, Any]:
        base: dict[str, Any] = {
            "schema": RESULT_SCHEMA, "arm": arm, "case_name": item.case.case_name, "case_id": item.case.case_id,
            "partition": item.case.partition, "preprocessing": PREPROCESSING[arm],
            "boxes_in_source": len(item.case.objects), "started_at": utc_now(), "render_ms": item.render_ms,
            "prediction": None, "boxes": [], "request_sha256": None, "model_calls": 0,
            "duration_ms": 0.0, "chat_ms": 0.0,
        }
        rejection = self.rejected(item, arm)
        if rejection:
            self.say(f"    rejected before any request ({rejection}); retained as unavailable")
            outcome, reason, aggregate = "unavailable", rejection, "rejected_before_request"
        elif arm == RAW_VARIANT:
            raw = self.run_raw(item)
            base.update(prediction=raw["prediction"], request_sha256=raw["request_sha256"],
                        model_calls=int(raw["chat_attempted"]), duration_ms=raw["duration_ms"],
                        chat_ms=raw["chat_ms"] or 0.0, metrics=raw["metrics"], content=raw["content"],
                        model_verified=raw["model_verified"])
            outcome = raw["prediction"]["outcome"] if raw["prediction"] else "unavailable"
            reason = raw["prediction"]["reason"] if raw["prediction"] else raw["failure"]
            aggregate = "single_request"
        else:
            result = self.run_crops(item, arm)
            boxes = result["boxes"]
            verdicts = [BoxVerdict(b["outcome"], b["model_verified"]) for b in boxes]
            if len(boxes) < len(item.crops):  # stopped by the safety abort; never aggregate a partial sample
                outcome, aggregate = "unavailable", "stopped_before_all_boxes"
            else:
                outcome, aggregate = aggregate_sample(verdicts)
            numbers = [str(b["box_number"]) for b in boxes if b["outcome"] == outcome and outcome in ("fail", "unsure", "unavailable")]
            reason = aggregate + (f": boxes {', '.join(numbers)}" if numbers else "")
            base.update(boxes=boxes, model_calls=sum(b["chat_attempted"] for b in boxes),
                        duration_ms=sum(b["duration_ms"] for b in boxes),
                        chat_ms=sum(b["chat_ms"] or 0.0 for b in boxes))
        base.update(outcome=outcome, reason=reason, aggregate_reason=aggregate, finished_at=utc_now())
        return base

    # the loop ----------------------------------------------------------------------------------
    def execute(self) -> None:
        total = len(self.units)
        for number, (arm, case) in enumerate(self.units, start=1):
            if self.aborted_reason:
                break
            item = self.preflight[case.case_name]
            tag = f"[{number}/{total}] {case.case_name} {arm}"
            self.say(f"{tag}: starting ({len(case.objects)} annotation boxes)")
            self.files.event("unit_started", arm=arm, case_name=case.case_name, unit=number, units_total=total)
            record = self.run_unit(arm, item)
            self.records.append(record)
            self.files.append(RESULTS_FILE, record)
            self.files.event("unit_finished", arm=arm, case_name=case.case_name, unit=number,
                             outcome=record["outcome"], reason=record["reason"], duration_ms=record["duration_ms"],
                             model_calls=record["model_calls"])
            self.say(f"{tag}: done -> {record['outcome']} in {record['duration_ms'] / 1000:.1f} s "
                     f"({record['model_calls']} model call{'s' if record['model_calls'] != 1 else ''})")

    def summary(self, stage: str, reason: str | None, started_at: str) -> dict[str, Any]:
        done = {(r["arm"], r["case_name"]) for r in self.records}
        arms: dict[str, Any] = {}
        for arm in self.config.arms:
            rows = [r for r in self.records if r["arm"] == arm]
            outcomes: dict[str, int] = {}
            for row in rows:
                outcomes[row["outcome"]] = outcomes.get(row["outcome"], 0) + 1
            arms[arm] = {"units": len(rows), "outcomes": outcomes, "model_calls": sum(r["model_calls"] for r in rows),
                         "duration_ms": sum(r["duration_ms"] for r in rows)}
        return {
            "schema": SUMMARY_SCHEMA, "stage": stage, "reason": reason, "started_at": started_at,
            "finished_at": utc_now(), "planned_units": len(self.units), "completed_units": len(self.records),
            "not_run": [{"arm": a, "case_name": c.case_name} for a, c in self.units if (a, c.case_name) not in done],
            "model_calls": self.model_calls, "arms": arms, "source_files_changed": changed_since_load(self.scope),
            "labels_read_during_inference": False,
        }


def run_settings(config: RunConfig, scope: Scope, texts: PromptTexts, execution: ExecutionSettings,
                 digest: dict[str, Any], version: str | None, cases: tuple[ScopeCase, ...],
                 units: list[tuple[str, ScopeCase]], packet: Path) -> dict[str, Any]:
    return {
        "schema": RUN_SCHEMA, "experiment_version": EXPERIMENT_VERSION, "created_at": utc_now(),
        "run_name": config.run_dir.name, "arms": list(config.arms), "order": config.order,
        "cases": [case.case_name for case in cases], "planned_units": len(units),
        "variants": variant_descriptions(texts), "execution": execution.model_dump(mode="json"),
        "digest": digest, "ollama_version": version, "environment": collect_environment(),
        "provenance": {
            "scope_sha256": scope.sha256, "corpus_version": scope.document.get("corpus_version"),
            "manifest_sha256": scope.document.get("manifest_sha256"),
            "system_prompt_version": SYSTEM_PROMPT_VERSION, "system_prompt_sha256": texts.system_sha256,
            "rule": rule_document(scope),
            "schema_sha256": {RAW_SCHEMA_VERSION: schema_hash(Prediction.model_json_schema()),
                              BOX_SCHEMA_VERSION: schema_hash(BoxPrediction.model_json_schema())},
            "code_sha256": code_fingerprints(packet),
        },
        "sequential": True, "retries": "none", "resume": "not supported",
        "labels_read_during_inference": False,
        "max_consecutive_failures": config.max_consecutive_failures, "timing_caveats": list(TIMING_CAVEATS),
    }


def run_experiment(config: RunConfig, *, transport: httpx.BaseTransport | None = None, say: Say = print_line,
                   packet: Path = PACKET) -> dict[str, Any]:
    say("Box-grounding experiment starting. Inference reads no expected labels; scoring is a separate step.")
    texts = load_prompt_texts(packet)
    scope = load_scope(config.scope_path, EvidenceLimits())
    if scope.document.get("system_prompt_sha256") != texts.system_sha256:
        raise RunError("The system prompt changed since this scope was prepared; prepare again.")
    cases = select_cases(scope, config.cases)
    units = plan_units(cases, config.arms, config.order)
    planning = config.execution(PLACEHOLDER_DIGEST)  # request bodies do not include the digest
    preflights = [preflight_case(scope, case, config.arms, texts, planning) for case in cases]
    say(f"Scope verified: {len(scope.cases)} cases, {len(cases)} development cases selected, {len(units)} planned units.")
    if config.dry_run:
        say(format_dry_run(preflights, units, config))
        return {"stage": "dry-run", "planned_units": len(units)}
    files = RunFiles(config.run_dir)
    files.require_absent()
    client = OllamaClient(planning, transport=transport)
    started_at = utc_now()
    try:
        digest = resolve_digest(client, config.digest_request, packet)
        if digest["installed_matches_historical"] is False:
            say("WARNING: the installed model digest differs from the historical one. "
                "Do not claim equivalence with the historical results without reporting this.")
        execution = config.execution(digest["pinned"])
        client.execution = execution
        files.create()  # only after the model identity is settled, so a refused run leaves nothing behind
        files.write_document(RUN_FILE, run_settings(config, scope, texts, execution, digest,
                                                    client.server_version(), cases, units, packet))
        files.event("run_started", planned_units=len(units), run_name=config.run_dir.name)
        say(f"Run directory: {config.run_dir}. Pinned model {config.model} {digest['pinned'][:19]}...")
        runner = Runner(config, scope, texts, preflights, units, files, client, execution, say)
        stage, reason = "completed", None
        try:
            runner.execute()
            if runner.aborted_reason:
                stage, reason = "aborted", runner.aborted_reason
        except KeyboardInterrupt:
            stage, reason = "interrupted", "Interrupted by the operator. Partial results are preserved."
        except BaseException as error:
            stage, reason = "failed", repr(error)
            raise
        finally:
            summary = runner.summary(stage, reason, started_at)
            files.write_document(SUMMARY_FILE, summary)
            files.event("run_finished", stage=stage, reason=reason)
    finally:
        client.close()
    say(f"Run {stage}: {summary['completed_units']}/{summary['planned_units']} units, {summary['model_calls']} model calls."
        + (f" {reason}" if reason else ""))
    return summary
