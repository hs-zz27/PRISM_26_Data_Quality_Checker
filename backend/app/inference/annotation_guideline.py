"""Bounded local vision proposals; baseline/candidate differ only in example context."""
import json
import time
import re
import httpx
from pydantic import ValidationError
from ..core.config import load_ollama_settings
from ..core.feedback import FeedbackError
from ..models.feedback import Prediction, RecordedPrediction, ExecutionPin, checked, unit_id
from ..services.feedback_prompt import prepare_payload

# Model descriptions can include licenses and tensor metadata. Keep their
# finite transport bound separate from the much smaller prediction envelope.
_METADATA_RESPONSE_LIMIT = 1024 * 1024

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

    def __init__(self, execution: ExecutionPin, *, transport=None):
        self.execution = checked(ExecutionPin, execution)
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

    def predict(self, manifest, target, examples, asset_settings):
        case_id = unit_id(manifest, target)
        start = time.monotonic()
        verified = False
        try:
            self.verify()
            payload = prepare_payload(manifest, target, examples, self.execution, asset_settings)
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
                prediction = Prediction.model_validate_json(message['content'])
            except (ValidationError, ValueError, RecursionError) as error:
                raise InferenceUnavailable('invalid_proposal') from error
            if prediction.case_id != case_id or prediction.rule_id != target.rule.rule_id:
                raise InferenceUnavailable('identity_mismatch')
            self.verify()
            verified = True
        except InferenceUnavailable as error:
            prediction = Prediction(case_id=case_id, rule_id=target.rule.rule_id, outcome='unavailable', reason=str(error), evidence_references=[])
        except FeedbackError as error:
            if error.code not in {'FEEDBACK_NOT_READY', 'CONTEXT_NOT_REPRESENTABLE'}:
                raise
            prediction = Prediction(case_id=case_id, rule_id=target.rule.rule_id, outcome='unavailable', reason='source_unavailable' if error.code == 'FEEDBACK_NOT_READY' else 'context_limit', evidence_references=[])
        return RecordedPrediction(prediction=prediction, duration_ms=(time.monotonic() - start) * 1000, model_verified=verified)
