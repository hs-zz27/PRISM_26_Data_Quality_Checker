"""Bounded local vision proposals; baseline/candidate differ only in example context."""
import json
import time
import re
import httpx
from pydantic import ValidationError
from ..core.config import load_ollama_settings
from ..core.feedback import FeedbackError
from dataclasses import replace
from ..models.applicability import ScopeUnavailable
from ..models.feedback import (
    FocusedAssessment, Prediction, RecordedPrediction, ExecutionPin, TargetedPrediction, checked, unit_id)
from ..models.visual_assessment import VisualAssessment
from ..services.feedback_prompt import prepare_payload
from ..services.visual_assessment import grounded_prediction

# Model descriptions can include licenses and tensor metadata. Keep their
# finite transport bound separate from the much smaller prediction envelope.
_METADATA_RESPONSE_LIMIT = 1024 * 1024

# v6 makes one request per eligible target; this bounds the calls (and CPU time) per rule and sample.
MAX_FOCUSED_TARGETS = 8
# Opt-in local diagnostics keep only a bounded excerpt of the MODEL'S reply, never prompts or images.
DIAGNOSTIC_EXCERPT_CHARS = 2000

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate response field.')
        result[key] = value
    return result

class InferenceUnavailable(RuntimeError):
    pass

class GuidelineClient:
    """Clients are constructed inside the child; no sockets in spawn arguments."""

    def __init__(self, execution: ExecutionPin, *, transport=None, capture_diagnostics=False):
        self.execution = checked(ExecutionPin, execution)
        self.capture_diagnostics = capture_diagnostics
        self.call_log = []        # one record per request of the last v6 predict(); in memory only
        self.diagnostics = []     # opt-in excerpts of replies that failed validation; in memory only
        self._last_exchange = {}
        if self.execution.enclosure is not None:
            raise FeedbackError('POLICY_INCOMPATIBLE')
        host = load_ollama_settings().host
        self.client = httpx.Client(base_url=host, trust_env=False, follow_redirects=False, timeout=self.execution.timeout_seconds, transport=transport)

    def close(self):
        self.client.close()

    def request(self, method, path, payload=None, *, response_limit=None):
        limit = self.execution.max_response_bytes if response_limit is None else response_limit
        data = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        if data is not None and len(data) > self.execution.max_request_bytes:
            raise InferenceUnavailable('context_limit')
        try:
            with self.client.stream(method, path, content=data, headers={'Content-Type': 'application/json'}) as response:
                response.raise_for_status()
                parts = []
                total = 0
                for part in response.iter_bytes():
                    total += len(part)
                    if total > limit:
                        raise InferenceUnavailable('response_limit')
                    parts.append(part)
                result = json.loads(b''.join(parts), object_pairs_hook=unique_object)
            if not isinstance(result, dict) or result.get('error'):
                raise InferenceUnavailable('invalid_response')
            return result
        except (httpx.HTTPError, ValueError, UnicodeError, RecursionError) as error:
            raise InferenceUnavailable('inference_unavailable') from error

    def verify(self):
        if self.execution.prompt_version in {'annotation-guideline-v2', 'annotation-guideline-v3', 'annotation-guideline-v4', 'annotation-guideline-v5', 'annotation-guideline-v6'}:
            version = self.request('GET', '/api/version').get('version')
            match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)', version or '') if isinstance(version, str) else None
            if match is None or tuple(map(int, match.groups())) < (0, 33, 3):
                raise InferenceUnavailable('nontruncating_server_required')
        tag = self.execution.model
        models = self.request('GET', '/api/tags', response_limit=_METADATA_RESPONSE_LIMIT).get('models')
        if not isinstance(models, list):
            raise InferenceUnavailable('identity_unavailable')
        matched = [m for m in models if isinstance(m, dict) and m.get('name') in {tag, tag + ':latest'}]
        digest = matched[0].get('digest') if len(matched) == 1 else None
        if isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest):
            digest = 'sha256:' + digest
        if digest != self.execution.model_digest:
            raise InferenceUnavailable('model_identity_changed')
        metadata = self.request('POST', '/api/show', {'model': tag}, response_limit=_METADATA_RESPONSE_LIMIT)
        if metadata.get('remote_host') or metadata.get('remote_model') or (not isinstance(metadata.get('capabilities'), list)) or ('vision' not in metadata['capabilities']):
            raise InferenceUnavailable('local_vision_unverified')

    def _chat_content(self, payload):
        """One verified chat call; returns the model's JSON text. Same checks as the v1-v3 path."""
        response = self.request('POST', '/api/chat', payload)
        message = response.get('message')
        self._last_exchange = {key: response.get(key) for key in ('done_reason', 'prompt_eval_count', 'eval_count')
                               if type(response.get(key)) in (int, str)}
        if self.capture_diagnostics and isinstance(message, dict) and isinstance(message.get('content'), str):
            self._last_exchange['content_excerpt'] = message['content'][:DIAGNOSTIC_EXCERPT_CHARS]
        if response.get('model') not in {self.execution.model, self.execution.model + ':latest'}:
            raise InferenceUnavailable('model_identity_changed')
        if type(response.get('prompt_eval_count')) is not int or not 0 < response['prompt_eval_count'] < self.execution.num_ctx - self.execution.num_predict:
            raise InferenceUnavailable('context_unverified')
        if response.get('done') is not True or response.get('done_reason') == 'length':
            raise InferenceUnavailable('incomplete_response')
        if not isinstance(message, dict) or not isinstance(message.get('content'), str):
            raise InferenceUnavailable('invalid_response')
        return message['content']

    def _predict_scoped(self, manifest, target, examples, asset_settings, reviewer_feedback):
        """v5: decide eligibility first, judge only eligible annotations, record per-target results."""
        from ..services.feedback_evidence import compute_scope
        from ..services.rule_applicability import combine_results
        case_id = unit_id(manifest, target)
        start = time.monotonic()
        verified = False
        scope = None

        def recorded(prediction):
            return RecordedPrediction(prediction=prediction, duration_ms=(time.monotonic() - start) * 1000,
                                      model_verified=verified, **({} if scope is None else {'scope': scope}))
        try:
            scope = compute_scope(manifest, target.rule, self.execution, asset_settings)
            if scope.unavailable is not None:
                # Nothing eligible: there is nothing for a model to judge. No request is made.
                return recorded(Prediction(case_id=case_id, rule_id=target.rule.rule_id, outcome='unavailable',
                                           reason=scope.unavailable.detail, evidence_references=[]))
            if len(scope.eligible) > 32:
                # The response schema is bounded; never silently omit eligible targets.
                raise InferenceUnavailable('context_limit')
            self.verify()
            payload = prepare_payload(manifest, target, examples, self.execution, asset_settings, scope=scope,
                                      **({'reviewer_feedback': reviewer_feedback} if reviewer_feedback is not None else {}))
            content = self._chat_content(payload)
            try:
                json.loads(content, object_pairs_hook=unique_object)
                proposal = TargetedPrediction.model_validate_json(content)
            except (ValidationError, ValueError, RecursionError) as error:
                raise InferenceUnavailable('invalid_proposal') from error
            if proposal.case_id != case_id or proposal.rule_id != target.rule.rule_id:
                raise InferenceUnavailable('identity_mismatch')
            self.verify()
            verified = True
            outcome, reason, scope = combine_results(scope, proposal)
            prediction = Prediction(case_id=case_id, rule_id=target.rule.rule_id, outcome=outcome, reason=reason,
                                    evidence_references=proposal.evidence_references)
        except InferenceUnavailable as error:
            self._note_failure(target.rule.rule_id, str(error))
            prediction = Prediction(case_id=case_id, rule_id=target.rule.rule_id, outcome='unavailable', reason=str(error), evidence_references=[])
        except FeedbackError as error:
            if error.code not in {'FEEDBACK_NOT_READY', 'CONTEXT_NOT_REPRESENTABLE'}:
                raise
            prediction = Prediction(case_id=case_id, rule_id=target.rule.rule_id, outcome='unavailable', reason='source_unavailable' if error.code == 'FEEDBACK_NOT_READY' else 'context_limit', evidence_references=[])
        return recorded(prediction)

    def _focused_call(self, manifest, target, asset_settings, reviewer_feedback, scope, key, case_id):
        """ONE per-target request. Any failure is returned as a coded result for THIS target, never raised."""
        from ..services.rule_applicability import FocusedCall
        self._last_exchange = {}
        try:
            payload = prepare_payload(manifest, target, [], self.execution, asset_settings, scope=scope, focus_key=key,
                                      **({'reviewer_feedback': reviewer_feedback} if reviewer_feedback is not None else {}))
            content = self._chat_content(payload)
            try:
                json.loads(content, object_pairs_hook=unique_object)
                assessment = FocusedAssessment.model_validate_json(content)
            except (ValidationError, ValueError, RecursionError) as error:
                raise InferenceUnavailable('invalid_proposal') from error
            if assessment.case_id != case_id or assessment.rule_id != target.rule.rule_id:
                raise InferenceUnavailable('identity_mismatch')
            if assessment.object_key != key:
                return FocusedCall(object_key=key, answered_for=assessment.object_key)
            return FocusedCall(object_key=key, assessment=assessment)
        except InferenceUnavailable as error:
            code = str(error)
        except FeedbackError as error:
            if error.code not in {'FEEDBACK_NOT_READY', 'CONTEXT_NOT_REPRESENTABLE'}:
                raise
            code = 'source_unavailable' if error.code == 'FEEDBACK_NOT_READY' else 'context_limit'
        if self.capture_diagnostics:
            self.diagnostics.append({'object_key': key, 'rule_id': target.rule.rule_id, 'code': code, **self._last_exchange})
        return FocusedCall(object_key=key, failure=code)

    def _predict_focused(self, manifest, target, examples, asset_settings, reviewer_feedback):
        """v6: eligibility first, then ONE bounded request per eligible target with focused evidence.

        Visual request count = number of eligible targets (at most MAX_FOCUSED_TARGETS; beyond that the result is
        unavailable before inference, never a silently shortened list). A failed request marks only its own
        target unavailable; other targets keep their results, so a valid failure is never discarded.
        Reviewer feedback is untrusted context for a fresh visual assessment of unchanged evidence.
        """
        from ..services.feedback_evidence import compute_scope
        from ..services.rule_applicability import combine_focused
        if examples:
            raise FeedbackError('POLICY_INCOMPATIBLE')
        case_id = unit_id(manifest, target)
        start = time.monotonic()
        verified = False
        scope = None
        self.call_log, self.diagnostics = [], []

        def recorded(prediction):
            return RecordedPrediction(prediction=prediction, duration_ms=(time.monotonic() - start) * 1000,
                                      model_verified=verified, **({} if scope is None else {'scope': scope}))
        try:
            scope = compute_scope(manifest, target.rule, self.execution, asset_settings)
            if reviewer_feedback is not None and reviewer_feedback.object_keys is not None:
                known_keys = {item.object_key for item in [*scope.eligible, *scope.ineligible]}
                if not set(reviewer_feedback.object_keys) <= known_keys:
                    raise InferenceUnavailable('feedback_target_not_found')
            if scope.unavailable is None and len(scope.eligible) > MAX_FOCUSED_TARGETS:
                scope = scope.model_copy(update={'unavailable': ScopeUnavailable(
                    code='too_many_targets',
                    detail=f'{len(scope.eligible)} annotations are eligible; one request per target is limited to {MAX_FOCUSED_TARGETS}.')})
            if scope.unavailable is not None:
                # Nothing eligible (or too many to judge one by one): no request is made.
                return recorded(Prediction(case_id=case_id, rule_id=target.rule.rule_id, outcome='unavailable',
                                           reason=scope.unavailable.detail, evidence_references=[]))
            self.verify()
            calls = []
            for item in scope.eligible:
                began = time.monotonic()
                call = self._focused_call(manifest, target, asset_settings, reviewer_feedback, scope, item.object_key, case_id)
                call = replace(call, duration_ms=(time.monotonic() - began) * 1000)
                calls.append(call)
                self.call_log.append({'object_key': call.object_key,
                                      'status': call.failure or ('reference_mismatch' if call.answered_for else 'ok'),
                                      'duration_ms': round(call.duration_ms, 1), **self._last_exchange_counts()})
            self.verify()
            verified = any(call.failure is None for call in calls)
            outcome, reason, scope = combine_focused(scope, calls)
            references = list(dict.fromkeys(ref for call in calls if call.assessment for ref in call.assessment.evidence_references))
            prediction = Prediction(case_id=case_id, rule_id=target.rule.rule_id, outcome=outcome, reason=reason,
                                    evidence_references=references[:3] if verified else [])
        except InferenceUnavailable as error:
            self._note_failure(target.rule.rule_id, str(error))
            prediction = Prediction(case_id=case_id, rule_id=target.rule.rule_id, outcome='unavailable', reason=str(error), evidence_references=[])
        except FeedbackError as error:
            if error.code not in {'FEEDBACK_NOT_READY', 'CONTEXT_NOT_REPRESENTABLE'}:
                raise
            prediction = Prediction(case_id=case_id, rule_id=target.rule.rule_id, outcome='unavailable',
                                    reason='source_unavailable' if error.code == 'FEEDBACK_NOT_READY' else 'context_limit',
                                    evidence_references=[])
        return recorded(prediction)

    def _note_failure(self, rule_id, code, key=None):
        """Opt-in local diagnostics: the failure code and a bounded excerpt of the model's reply, never the prompt."""
        if self.capture_diagnostics:
            self.diagnostics.append({'object_key': key, 'rule_id': rule_id, 'code': code, **self._last_exchange})

    def _last_exchange_counts(self):
        return {key: self._last_exchange[key] for key in ('prompt_eval_count', 'eval_count') if key in self._last_exchange}

    def predict(self, manifest, target, examples, asset_settings, *, reviewer_feedback=None):
        if self.execution.prompt_version == 'annotation-guideline-v6':
            return self._predict_focused(manifest, target, examples, asset_settings, reviewer_feedback)
        if self.execution.prompt_version == 'annotation-guideline-v5':
            return self._predict_scoped(manifest, target, examples, asset_settings, reviewer_feedback)
        case_id = unit_id(manifest, target)
        start = time.monotonic()
        verified = False
        assessment = None
        try:
            self.verify()
            payload = prepare_payload(manifest, target, examples, self.execution, asset_settings,
                                      **({'reviewer_feedback': reviewer_feedback} if reviewer_feedback is not None else {}))
            object_level = self.execution.prompt_version == 'annotation-guideline-v4'
            if object_level:
                # This is our own prepared, verified input, not model-authored inventory.
                source = json.loads(payload['messages'][-1]['content'])['target']['inputs']
                if not source['object_inventory']:
                    raise InferenceUnavailable('no_annotation_objects_to_assess')
            response = self.request('POST', '/api/chat', payload)
            if response.get('model') not in {self.execution.model, self.execution.model + ':latest'}:
                raise InferenceUnavailable('model_identity_changed')
            if type(response.get('prompt_eval_count')) is not int or not 0 < response['prompt_eval_count'] < self.execution.num_ctx - self.execution.num_predict:
                raise InferenceUnavailable('context_unverified')
            if response.get('done') is not True or response.get('done_reason') == 'length':
                raise InferenceUnavailable('incomplete_response')
            message = response.get('message')
            if not isinstance(message, dict) or not isinstance(message.get('content'), str):
                raise InferenceUnavailable('invalid_response')
            try:
                json.loads(message['content'], object_pairs_hook=unique_object)
                if object_level:
                    assessment = VisualAssessment.model_validate_json(message['content'])
                else:
                    prediction = Prediction.model_validate_json(message['content'])
            except (ValidationError, ValueError, RecursionError) as error:
                raise InferenceUnavailable('invalid_proposal') from error
            if object_level:
                try:
                    prediction = grounded_prediction(assessment, source['object_inventory'], source['image_size'])
                except ValueError as error:
                    raise InferenceUnavailable(str(error)) from error
            if prediction.case_id != case_id or prediction.rule_id != target.rule.rule_id:
                raise InferenceUnavailable('identity_mismatch')
            self.verify()
            verified = True
        except InferenceUnavailable as error:
            self._note_failure(target.rule.rule_id, str(error))
            assessment = None
            prediction = Prediction(case_id=case_id, rule_id=target.rule.rule_id, outcome='unavailable', reason=str(error), evidence_references=[])
        except FeedbackError as error:
            if error.code not in {'FEEDBACK_NOT_READY', 'CONTEXT_NOT_REPRESENTABLE'}:
                raise
            assessment = None
            prediction = Prediction(case_id=case_id, rule_id=target.rule.rule_id, outcome='unavailable', reason='source_unavailable' if error.code == 'FEEDBACK_NOT_READY' else 'context_limit', evidence_references=[])
        return RecordedPrediction(prediction=prediction, duration_ms=(time.monotonic() - start) * 1000,
                                  model_verified=verified, visual_assessment=assessment)
