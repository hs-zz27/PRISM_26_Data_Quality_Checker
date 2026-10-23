"""Opt-in pedestrian-box enclosure check: acceptance pin and per-sample model proposals.

Model proposals augment, never rewrite, deterministic measurements or reviews. One sample-level
finding per enclosure rule holds every box's proposal; all findings stay pending human review.
"""

from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any

import httpx

from ..core.enclosure import load_enclosure_runtime
from ..core.feedback import FeedbackError
from ..datasets.penn_fudan import parse_annotation_metadata
from ..inference.enclosure_client import EnclosureClient
from ..models.enclosure import (
    CHECK_NAME, PROTOCOL_VERSION, BoxVerdict, EnclosureRuntime, aggregate_sample,
)
from ..models.feedback import EvidenceLimits
from ..models.finding import FindingOutcome
from .enclosure_prompt import ContextNotRepresentable, box_identity, build_request, case_identity
from .enclosure_views import ContextView, EnclosureViewError, ViewLimits, render_views
from .evaluation import RuleEvaluationResult, _make_finding
from .feedback_evidence import evidence as read_evidence


def prepare_enclosure_runtime(request: Any, book: Any,
                              loader: Callable[[], EnclosureRuntime | None] = load_enclosure_runtime) -> Any:
    """Freeze the pin at acceptance. No inference; absent configuration leaves the rule unavailable."""
    uses = any(rule.check_name == CHECK_NAME for rule in book.rules)
    if not uses:
        if request.enclosure_runtime is not None:
            raise FeedbackError("POLICY_INCOMPATIBLE")
        return request
    if request.feedback_policy_id is not None:
        raise FeedbackError("POLICY_INCOMPATIBLE")  # a supplied policy is never ignored for this method
    if request.source_mode.value != "snapshot":
        raise FeedbackError("POLICY_INCOMPATIBLE")
    runtime = request.enclosure_runtime if request.enclosure_runtime is not None else loader()
    return request if runtime is None else request.model_copy(update={"enclosure_runtime": runtime})


def _box_record(view: ContextView, call: Any, failure: str | None, request_sha256: str | None) -> dict[str, Any]:
    target, response = view.target, None if call is None else call.response
    verified = bool(call and call.model_verified)
    return {
        "annotation_index": target.annotation_index, "box_number": target.box_number,
        "object_id": target.object_id, "label": target.label, "bbox": list(target.bbox),
        "view_sha256": view.png_sha256, "view_size": list(view.size), "source_window": list(view.source_window),
        "target_window": list(view.target_window), "request_sha256": request_sha256,
        "outcome": response.outcome if response else "unavailable",
        "reason": response.reason if response else (failure or (call.failure if call else None)),
        "evidence_references": list(response.evidence_references) if response else [],
        "model_verified": verified, "duration_ms": call.duration_ms if call else 0.0,
        "chat_ms": call.chat_ms if call else None,
    }


def _judge_boxes(views: tuple[ContextView, ...], rule: dict[str, Any], case_id: str, runtime: EnclosureRuntime,
                 clock: Callable[[], float], transport: httpx.BaseTransport | None) -> list[dict[str, Any]]:
    pin = runtime.execution
    deadline = clock() + pin.sample_budget_seconds
    records: list[dict[str, Any]] = []
    client = EnclosureClient(pin, transport=transport)
    try:
        for view in views:  # strictly sequential; every intended box is accounted for
            remaining = deadline - clock()
            try:
                prepared = build_request(pin, rule, case_id, view)
            except ContextNotRepresentable as error:
                records.append(_box_record(view, None, error.code, None))
                continue
            if remaining < 1:
                records.append(_box_record(view, None, "sample_budget_exhausted", prepared.sha256))
                continue
            call = client.chat(prepared, box_identity(case_id, rule["rule_id"], view),
                               min(float(pin.request_timeout_seconds), remaining))
            records.append(_box_record(view, call, None, prepared.sha256))
    finally:
        client.close()
    return records


def _finding_for(rule: Any, runtime: EnclosureRuntime, facts: dict[str, Any], outcome: str, reason: str,
                 boxes: list[dict[str, Any]], failure: str | None, ids: dict[str, Any]) -> Any:
    document = {"protocol_version": PROTOCOL_VERSION, "view_version": runtime.execution.view_version,
                "execution": runtime.execution.model_dump(mode="json"), **facts,
                "boxes_intended": facts["boxes_intended"], "failure": failure,
                "aggregate": {"outcome": outcome, "reason": reason}, "boxes": boxes}
    return _make_finding(rule=rule, outcome=FindingOutcome(outcome), reason=reason, object_id=None,
                         occurrence="sample", evidence={"enclosure": document}, **ids)


def augment_enclosure(evaluation: Any, rulebook: Any, manifest: Any, runtime: Any, asset_settings: Any, *,
                      audit_id: str, sample_id: str, sample_revision: int,
                      clock: Callable[[], float] = time.monotonic,
                      transport: httpx.BaseTransport | None = None,
                      evidence_reader: Callable[..., Any] = read_evidence) -> Any:
    """Replace each enclosure rule's placeholder with one finding holding every box's proposal."""
    if runtime is None:
        return evaluation
    runtime = EnclosureRuntime.model_validate(runtime)
    targets = [rule for rule in rulebook.rules if rule.check_name == CHECK_NAME]
    if not targets:
        return evaluation
    pin = runtime.execution
    ids = dict(audit_id=audit_id, sample_id=sample_id, sample_revision=sample_revision,
               rulebook_id=rulebook.rulebook_id, rulebook_version=rulebook.version)
    findings = [f for f in evaluation.findings if f.check_name != CHECK_NAME]
    limits = EvidenceLimits(max_image_bytes=pin.max_image_bytes, max_annotation_bytes=pin.max_annotation_bytes,
                            max_pixels=pin.max_pixels)
    try:
        image, inputs = evidence_reader(manifest, limits, asset_settings)
        facts = {"image_sha256": inputs["image_sha256"], "annotation_sha256": inputs["annotation_sha256"],
                 "boxes_intended": len(inputs["objects"])}
    except FeedbackError:
        image = inputs = None
        facts = {"image_sha256": manifest.original_image.sha256, "annotation_sha256": manifest.annotation_text.sha256,
                 "boxes_intended": None}
    for rule in targets:
        rule_document = rule.model_dump(mode="json")
        failure, views = ("evidence_unavailable" if inputs is None else None), ()
        if inputs is not None:
            origin = parse_annotation_metadata(inputs["annotation_text"])["coordinate_origin"]
            try:
                views = render_views(image, inputs["objects"], tuple(origin) if origin else (0, 0),
                                     ViewLimits(pin.max_image_bytes, pin.max_pixels), max_boxes=pin.max_boxes)
            except EnclosureViewError as error:
                failure = error.code
        if failure:  # zero, too many or invalid boxes: explicit unavailable, never an all-pass
            findings.append(_finding_for(rule, runtime, facts, "unavailable", failure, [], failure, ids))
            continue
        case_id = case_identity(rulebook_id=rulebook.rulebook_id, rulebook_version=rulebook.version,
                                rule_id=rule.rule_id, sample_id=sample_id, sample_revision=sample_revision,
                                image_sha256=facts["image_sha256"], annotation_sha256=facts["annotation_sha256"])
        boxes = _judge_boxes(views, rule_document, case_id, runtime, clock, transport)
        outcome, label = aggregate_sample([BoxVerdict(outcome=b["outcome"], model_verified=b["model_verified"])
                                           for b in boxes])
        marked = [str(b["box_number"]) for b in boxes if b["outcome"] == outcome and outcome != "pass"]
        reason = label + (f": boxes {', '.join(marked)}" if marked else "")
        findings.append(_finding_for(rule, runtime, {**facts, "case_id": case_id}, outcome, reason, boxes, None, ids))
    return RuleEvaluationResult(findings=findings, internal_check_failures=evaluation.internal_check_failures)
