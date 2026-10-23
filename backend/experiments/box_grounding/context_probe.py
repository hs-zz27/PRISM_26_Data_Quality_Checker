"""Paired box diagnostics or full-scope validation; inference never reads labels.

The control is the unchanged tight crop v2 request. The candidate adds marked
surrounding context and explicit target wording. This is a combined intervention,
not a pixel-only ablation. Each target gets both arms, with alternating order.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re

import httpx

from .client import INFRASTRUCTURE_REASONS, InferenceUnavailable, OllamaClient
from .context_views import CONTEXT_VARIANT, ContextView, render_context
from .crops import BoxCrop, render_box_crops
from .prompts import (PACKET, CROP_V2_VARIANT, BoxPrediction, PreparedRequest,
                      crop_identity, crop_request, load_prompt_texts, prepare,
                      read_text_exactly, response_schema, rule_document)
from .run_files import RunFiles, RunFilesError, utc_now
from .runner import case_origin, code_fingerprints
from .scope import Scope, ScopeCase, changed_since_load, load_scope, reject_label_fields
from .settings import DEFAULT_MODEL, ExecutionSettings, collect_environment
from .views import ViewLimits

PLAN_LIMITS = {"context-probe-plan-v1": 8, "context-validation-plan-v1": 256}


@dataclass(frozen=True)
class ProbeTarget:
    case: ScopeCase
    crop: BoxCrop
    context: ContextView
    requests: dict[str, PreparedRequest]


def prepare_targets(scope: Scope, plan: dict, execution: ExecutionSettings,
                    *, packet: Path = PACKET) -> list[ProbeTarget]:
    """Validate the complete label-free plan and render both arms before any network call."""
    reject_label_fields(plan)
    if (set(plan) != {"schema", "scope_sha256", "targets"}
            or plan["schema"] not in PLAN_LIMITS
            or plan["scope_sha256"] != scope.sha256):
        raise ValueError("Plan schema or source scope hash mismatch.")
    maximum = PLAN_LIMITS[plan["schema"]]
    if not isinstance(plan["targets"], list) or not 1 <= len(plan["targets"]) <= maximum:
        raise ValueError(f"This plan requires one to {maximum} target boxes.")
    if execution.identity_schema != "bound-v1":
        raise ValueError("This probe requires bound-v1 identity schemas.")
    texts = load_prompt_texts(packet)
    if scope.document["system_prompt_sha256"] != texts.system_sha256:
        raise ValueError("System prompt differs from the source scope.")
    instruction, _ = read_text_exactly(packet / "context_instruction_v1.txt")
    cases = {c.case_name: c for c in scope.cases}
    seen = set()
    targets = []
    limits = ViewLimits(execution.max_image_bytes, execution.max_pixels)
    for item in plan["targets"]:
        if not isinstance(item, dict) or set(item) != {"case_name", "box_number"}:
            raise ValueError("Each target must contain only case_name and box_number.")
        name, number = item["case_name"], item["box_number"]
        if (not isinstance(name, str) or type(number) is not int or name not in cases
                or not 1 <= number <= len(cases[name].objects) or (name, number) in seen):
            raise ValueError("Unknown, repeated or invalid target box.")
        seen.add((name, number))
        case = cases[name]
        crop = render_box_crops(case.image_bytes, case.objects, case_origin(case), limits)[number - 1]
        context = render_context(case, crop, limits)
        identity = crop_identity(scope, case, crop)
        document = {
            "case_id": case.case_id, "rule_id": scope.rule_id, "guideline": rule_document(scope),
            "task": {"variant": CONTEXT_VARIANT, "instruction_version": "context-instruction-v1",
                     "instruction": instruction},
            "box": {"box_number": number, "object_id": crop.object_id, "label": crop.label},
            "target": {"image_attachment": 1, "marker": "red frame; judge its interior only"},
        }
        requests = {
            CROP_V2_VARIANT: crop_request(scope, case, crop, texts, execution, variant=CROP_V2_VARIANT),
            CONTEXT_VARIANT: prepare(texts, json.dumps(document, ensure_ascii=False, allow_nan=False),
                                     [context.png], response_schema(BoxPrediction, identity, execution), execution),
        }
        targets.append(ProbeTarget(case, crop, context, requests))
    if plan["schema"] == "context-validation-plan-v1":
        required = {(case.case_name, number + 1) for case in scope.cases
                    for number in range(len(case.objects))}
        if seen != required or any(not case.objects for case in scope.cases):
            raise ValueError("Validation plan must include every box of every nonempty source case.")
    return targets


def run_probe(scope_path: Path, plan_path: Path, run_dir: Path, execution: ExecutionSettings,
              *, dry_run: bool = False, transport: httpx.BaseTransport | None = None,
              packet: Path = PACKET, say=print) -> dict:
    files = RunFiles(run_dir)
    files.require_absent()
    scope = load_scope(scope_path)
    plan_bytes = plan_path.read_bytes()
    plan = json.loads(plan_bytes)
    targets = prepare_targets(scope, plan, execution, packet=packet)
    total = len(targets) * 2
    validation = plan["schema"] == "context-validation-plan-v1"
    role = "full-scope validation" if validation else "selected development diagnostic"
    say(f"Context {role}: {len(targets)} boxes, {total} sequential requests; no labels read.")
    for target in targets:
        say(f"  {target.case.case_name} box {target.crop.box_number}: "
            f"tight {target.crop.width}x{target.crop.height}; context {target.context.size}")
    if dry_run:
        say("Dry run complete. No network contact or output files.")
        return {"stage": "dry-run", "planned_requests": total}
    with OllamaClient(execution, transport=transport) as client:
        client.verify()  # pin must already be installed; never downloads or switches a model
        files.create()
        files.write_document("run.json", {
            "schema": "context-probe-run-v1", "created_at": utc_now(), "plan": plan,
            "plan_sha256": sha256(plan_bytes).hexdigest(), "scope_sha256": scope.sha256,
            "execution": execution.model_dump(mode="json"), "ollama_version": client.server_version(),
            "environment": collect_environment(), "code_sha256": code_fingerprints(packet),
            "labels_read_during_inference": False, "planned_requests": total,
            "order": "paired, alternating first arm", "retries": 0,
            "intervention": "context pixels plus outside-target red frame plus revised instruction",
            "padding": "half width/height per side, minimum 32 pixels, clipped to source",
            "margin": 2, "resize": "none", "evaluation_role": role,
        })
        records = []
        stage, reason, consecutive = "completed", None, 0
        started = utc_now()
        try:
            for index, target in enumerate(targets):
                arms = [CROP_V2_VARIANT, CONTEXT_VARIANT]
                if index % 2:
                    arms.reverse()
                for arm in arms:
                    case, crop, context = target.case, target.crop, target.context
                    request = target.requests[arm]
                    png = crop.png if arm == CROP_V2_VARIANT else context.png
                    image_path = files.save_crop(arm + "-" + case.case_name, crop.box_number, png)
                    geometry = {
                        "source_bbox": crop.bbox, "source_pil_box": crop.pil_box,
                        "source_window": crop.pil_box if arm == CROP_V2_VARIANT else context.source_window,
                        "target_window": (0, 0, crop.width, crop.height) if arm == CROP_V2_VARIANT else context.target_window,
                        "image_size": (crop.width, crop.height) if arm == CROP_V2_VARIANT else context.size,
                    }
                    say(f"[{len(records)+1}/{total}] {case.case_name} box {crop.box_number} {arm}: sending ...")
                    fields = dict(case_name=case.case_name, box_number=crop.box_number, arm=arm,
                                  request_sha256=request.sha256, image_sha256=sha256(png).hexdigest(),
                                  image_path=image_path, geometry=geometry)
                    files.event("request_started", **fields)
                    result = client.chat(request, BoxPrediction, crop_identity(scope, case, crop))
                    prediction = result.prediction.model_dump(mode="json") if result.prediction else None
                    record = {**fields, "case_id": case.case_id, "object_id": crop.object_id,
                              "source_image_sha256": case.inputs["image_sha256"],
                              "source_annotation_sha256": case.inputs["annotation_sha256"],
                              "prediction": prediction, "outcome": prediction["outcome"] if prediction else "unavailable",
                              "error": result.reason, "model_verified": result.model_verified,
                              "chat_attempted": result.chat_attempted, "duration_ms": result.duration_ms,
                              "chat_ms": result.chat_ms, "metrics": result.metrics, "content": result.content}
                    files.append("results.jsonl", record)
                    records.append(record)
                    say(f"    {record['outcome']} in {result.duration_ms/1000:.1f}s"
                        + (f" ({result.reason})" if result.reason else ""))
                    consecutive = consecutive + 1 if result.reason in INFRASTRUCTURE_REASONS else 0
                    if consecutive >= 3:
                        stage, reason = "aborted", "three consecutive infrastructure failures"
                        break
                if stage == "aborted":
                    break
        except KeyboardInterrupt:
            stage, reason = "interrupted", "operator interruption; saved results retained"
        except Exception:
            stage, reason = "failed", "unexpected worker error; see terminal traceback"
            raise
        finally:
            changes = changed_since_load(scope)
            if changes:
                stage, reason = "invalidated", "source files changed during the run"
            summary = dict(stage=stage, reason=reason, started_at=started, finished_at=utc_now(),
                           planned_requests=total, completed_requests=len(records),
                           model_calls=sum(r["chat_attempted"] for r in records), source_files_changed=changes)
            files.write_document("summary.json", summary)
        say(f"Context {role} {stage}: {len(records)}/{total} saved responses. Score separately.")
        return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=PACKET / "context_probe_plan.json")
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--model-digest", required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--keep-alive-seconds", type=int, default=3600)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args.run_name):
        parser.error("Invalid run name.")
    try:
        execution = ExecutionSettings(model=args.model, model_digest=args.model_digest,
                                      identity_schema="bound-v1", num_thread=args.threads,
                                      timeout_seconds=args.timeout_seconds, keep_alive_seconds=args.keep_alive_seconds)
        result = run_probe(args.scope, args.plan, Path(".cache/box-grounding/runs") / args.run_name,
                           execution, dry_run=args.dry_run, say=lambda line: print(line, flush=True))
        return 0 if result["stage"] in {"completed", "dry-run"} else 2
    except (ValueError, OSError, RunFilesError, InferenceUnavailable) as error:
        print(f"ERROR: {error}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
