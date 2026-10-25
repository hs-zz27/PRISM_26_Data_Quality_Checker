"""Feedback adapters for the existing marked-context pedestrian check.

Examples are single-box human-reviewed samples. A reviewed sample-level failure
on multiple boxes never becomes an invented per-box answer. Target preprocessing,
identity validation and aggregation remain owned by the enclosure implementation.
"""
from __future__ import annotations

from base64 import b64encode
from collections.abc import Sequence
from hashlib import sha256
import json
import time
from types import SimpleNamespace
from typing import Any

from ..core.config import AssetStorageSettings
from ..core.feedback import FeedbackError
from ..datasets.penn_fudan import parse_annotation_metadata
from ..models.asset_manifest import SampleAssetManifest
from ..models.enclosure import CHECK_NAME, EnclosureEvidence, EnclosureExecutionPin, EnclosureRuntime
from ..models.feedback import (
    Binary, CaseSnapshot, EvidenceLimits, ExecutionPin, FrozenSet, Prediction, RecordedPrediction, RulePin,
    checked, fingerprint, unit_id,
)
from .enclosure_prompt import ContextNotRepresentable, PreparedRequest, build_request, case_identity
from .enclosure_views import ContextView, EnclosureViewError, ViewLimits, render_views
from .feedback_evidence import evidence

EXAMPLE_INSTRUCTION = (
    'Additional attached images are separate human-reviewed examples of this same rule. '
    'Each example has one red-framed target and its human answer. Use them as context; '
    'judge only attachment 1 for the requested box. Never copy an example answer to the target.'
)
RenderedExample = tuple[ContextView, Binary, str]


def _views(manifest: SampleAssetManifest, pin: EnclosureExecutionPin,
           asset_settings: AssetStorageSettings) -> tuple[ContextView, ...]:
    limits = EvidenceLimits(max_image_bytes=pin.max_image_bytes,
        max_annotation_bytes=pin.max_annotation_bytes, max_pixels=pin.max_pixels)
    image, inputs = evidence(manifest, limits, asset_settings)
    origin = parse_annotation_metadata(inputs['annotation_text'])['coordinate_origin']
    return render_views(image, inputs['objects'], tuple(origin) if origin else (0, 0),
        ViewLimits(pin.max_image_bytes, pin.max_pixels), max_boxes=pin.max_boxes)


def example_views(manifest: SampleAssetManifest, target: RulePin, cases: Sequence[CaseSnapshot],
                  pin: EnclosureExecutionPin, asset_settings: AssetStorageSettings) -> list[RenderedExample]:
    """Recreate exact reviewed pixels; retain order and never select/truncate examples."""
    if len(cases) > 4:
        raise FeedbackError('POLICY_INCOMPATIBLE')
    prepared = []
    for original in cases:
        case = checked(CaseSnapshot, original)
        if (case.target != target or case.manifest.dataset_id != manifest.dataset_id
                or case.finding_id is None or case.model_execution.enclosure != pin):
            raise FeedbackError('POLICY_INCOMPATIBLE')
        try:
            views = _views(case.manifest, pin, asset_settings)
        except EnclosureViewError as error:
            raise FeedbackError('FEEDBACK_NOT_READY') from error
        if len(views) != 1:
            raise FeedbackError('FEEDBACK_NOT_READY')
        view, recorded = views[0], case.model_proposal.enclosure.boxes[0]
        if (view.png_sha256 != recorded.view_sha256 or list(view.target.bbox) != recorded.bbox
                or view.target.object_id != recorded.object_id or view.target.label != recorded.label
                or list(view.size) != recorded.view_size or list(view.source_window) != recorded.source_window
                or list(view.target_window) != recorded.target_window):
            raise FeedbackError('FEEDBACK_NOT_READY')
        prepared.append((view, case.expected, case.reason))
    return prepared


def build_feedback_request(pin: EnclosureExecutionPin, rule: dict[str, Any], case_id: str,
                           view: ContextView, examples: Sequence[RenderedExample]) -> PreparedRequest:
    """With no examples this returns the original baseline request byte-for-byte."""
    baseline = build_request(pin, rule, case_id, view)
    if not examples:
        return baseline
    payload = baseline.payload
    user = payload['messages'][1]
    content = json.loads(user['content'])
    content['feedback_instruction_version'] = 'single-box-reviewer-examples-v1'
    content['feedback_instruction'] = EXAMPLE_INSTRUCTION
    content['examples'] = []
    for example, expected, reason in examples:
        user['images'].append(b64encode(example.png).decode('ascii'))
        content['examples'].append({'image_attachment': len(user['images']),
            'marker': 'red frame; judge its interior only', 'expected': expected, 'reason': reason})
    user['content'] = json.dumps(content, ensure_ascii=False, allow_nan=False)
    text_bytes = len((payload['messages'][0]['content'] + user['content'] + json.dumps(payload['format'])).encode())
    reserved = pin.num_predict + 1024 * len(user['images'])
    if text_bytes > max(0, pin.num_ctx - reserved) // 2:
        raise ContextNotRepresentable('The complete example context does not fit.')
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
    if len(body) > pin.max_request_bytes:
        raise ContextNotRepresentable('The complete example request exceeds its byte limit.')
    return PreparedRequest(payload, body, sha256(body).hexdigest(), text_bytes)


def prepare_comparison(manifest: SampleAssetManifest, target: RulePin, policy: FrozenSet,
                       execution: ExecutionPin, asset_settings: AssetStorageSettings) -> None:
    """Check full candidate context before enqueue without contacting the model."""
    pin = execution.enclosure
    if pin is None or target.rule.check_name != CHECK_NAME:
        raise FeedbackError('POLICY_INCOMPATIBLE')
    examples = example_views(manifest, target, policy.cases, pin, asset_settings)
    try:
        views = _views(manifest, pin, asset_settings)
        for view in views:
            build_feedback_request(pin, target.rule.model_dump(mode='json'), enclosure_case_id(manifest, target), view, examples)
    except EnclosureViewError as error:
        raise FeedbackError('FEEDBACK_NOT_READY') from error
    except ContextNotRepresentable as error:
        raise FeedbackError('CONTEXT_NOT_REPRESENTABLE') from error


def enclosure_case_id(manifest: SampleAssetManifest, target: RulePin) -> str:
    return case_identity(rulebook_id=target.rulebook_id, rulebook_version=target.rulebook_version,
        rule_id=target.rule.rule_id, sample_id=manifest.sample_id, sample_revision=manifest.sample_revision,
        image_sha256=manifest.original_image.sha256, annotation_sha256=manifest.annotation_text.sha256)


def recorded_enclosure(manifest: SampleAssetManifest, target: RulePin,
                       document: Any) -> RecordedPrediction:
    """Translate existing per-box evidence without changing the stored model verdicts."""
    saved = EnclosureEvidence.model_validate_json(json.dumps(document, allow_nan=False))
    if (saved.image_sha256 != manifest.original_image.sha256
            or saved.annotation_sha256 != manifest.annotation_text.sha256
            or (saved.failure is None and saved.case_id != enclosure_case_id(manifest, target))):
        raise FeedbackError('FEEDBACK_NOT_READY')
    verified = bool(saved.boxes) and (
        any(box.outcome == 'fail' and box.model_verified for box in saved.boxes)
        if saved.aggregate.outcome == 'fail' else all(box.model_verified for box in saved.boxes)
    )
    return RecordedPrediction(prediction=Prediction(case_id=unit_id(manifest, target), rule_id=target.rule.rule_id,
        outcome=saved.aggregate.outcome, reason=saved.aggregate.reason, evidence_references=['image', 'annotations', 'guideline']),
        duration_ms=sum(box.duration_ms for box in saved.boxes), model_verified=verified, enclosure=saved)


def validate_prediction(recorded: RecordedPrediction, manifest: SampleAssetManifest,
                        target: RulePin, execution: ExecutionPin, policy: FrozenSet | None = None) -> None:
    """Fence stored comparison results to the exact evidence and accepted method."""
    if execution.enclosure is None:
        if recorded.enclosure is not None:
            raise FeedbackError('DEPENDENCY_UNAVAILABLE')
        return
    if recorded.enclosure is None or recorded.enclosure.execution != execution.enclosure:
        raise FeedbackError('DEPENDENCY_UNAVAILABLE')
    expected_policy = None if policy is None else policy.resource_id
    expected_hash = None if policy is None else fingerprint(policy)
    if (recorded.enclosure.feedback_policy_id != expected_policy
            or recorded.enclosure.feedback_policy_sha256 != expected_hash):
        raise FeedbackError('DEPENDENCY_UNAVAILABLE')
    try:
        verified = recorded_enclosure(manifest, target, recorded.enclosure.model_dump(mode='json'))
    except (ValueError, FeedbackError) as error:
        raise FeedbackError('DEPENDENCY_UNAVAILABLE') from error
    if (verified.prediction != recorded.prediction or verified.model_verified != recorded.model_verified):
        raise FeedbackError('DEPENDENCY_UNAVAILABLE')


class EnclosureFeedbackClient:
    """Use the production enclosure evaluator for both comparison arms."""

    def __init__(self, execution: ExecutionPin, policy: FrozenSet | None = None, *, transport=None):
        self.execution = checked(ExecutionPin, execution)
        if self.execution.enclosure is None:
            raise FeedbackError('POLICY_INCOMPATIBLE')
        self.policy = None if policy is None else checked(FrozenSet, policy)
        self.transport = transport

    def close(self) -> None:
        pass  # The enclosure evaluator owns and closes each transport client.

    def predict(self, manifest: SampleAssetManifest, target: RulePin, examples: list[CaseSnapshot],
                asset_settings: AssetStorageSettings) -> RecordedPrediction:
        from .enclosure import augment_enclosure
        from .evaluation import RuleEvaluationResult
        if target.rule.check_name != CHECK_NAME or (examples and (self.policy is None or examples != self.policy.cases)):
            raise FeedbackError('POLICY_INCOMPATIBLE')
        started = time.monotonic()
        book = SimpleNamespace(rulebook_id=target.rulebook_id, version=target.rulebook_version, rules=[target.rule])
        result = augment_enclosure(RuleEvaluationResult(findings=[], internal_check_failures=0), book,
            manifest, EnclosureRuntime(execution=self.execution.enclosure), asset_settings,
            audit_id='feedback_comparison', sample_id=manifest.sample_id, sample_revision=manifest.sample_revision,
            transport=self.transport, feedback_policy=self.policy if examples else None)
        recorded = recorded_enclosure(manifest, target, result.findings[0].evidence.measurements['enclosure'])
        return recorded.model_copy(update={'duration_ms': (time.monotonic() - started) * 1000})
