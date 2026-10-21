"""Mocked local protocol, evidence and pending-finding coverage."""
import json
from base64 import b64decode
import httpx
import pytest
from pydantic import ValidationError
from backend.app.inference.annotation_guideline import GuidelineClient
from backend.app.core.feedback import FeedbackError
from backend.app.models.feedback import ExecutionPin
from backend.app.services.feedback_prompt import prepare_payload
from backend.app.services.feedback_evidence import evidence
from backend.app.services.feedback import augment_evaluation
from backend.app.services.evaluation import RuleEvaluationResult
from backend.tests.feedback_fixtures import inputs, assets, book, record

def client_for(execution, case, *, outcome='fail', drift=False, broken=False, capabilities=None,
               metadata_padding=0, chat_padding=0):
    requests = []
    tags = 0

    def handler(request):
        nonlocal tags
        requests.append(request)
        if request.url.path == '/api/tags':
            tags += 1
            digest = 'b' * 64 if drift and tags > 1 else 'a' * 64
            return httpx.Response(200, json={'models': [{'name': execution.model, 'digest': digest}]})
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'capabilities': ['vision'] if capabilities is None else capabilities,
                                             'template': 'x' * metadata_padding})
        assert request.url.path == '/api/chat'
        prediction = record(case.manifest, case.target, outcome).prediction.model_dump(mode='json')
        if broken:
            prediction['outcome'] = 'accepted'
        return httpx.Response(200, json={'model': execution.model, 'done': True, 'done_reason': 'stop', 'prompt_eval_count': 1024, 'message': {'role': 'assistant', 'content': json.dumps(prediction)}, 'padding': 'x' * chat_padding})
    return (GuidelineClient(execution, transport=httpx.MockTransport(handler)), requests)

def test_target_labels_comments_and_private_paths_never_enter_prompt(tmp_path):
    frozen = inputs(tmp_path)
    target = frozen.truth.cases[0]
    example = frozen.policy.cases[0]
    target.reason = 'SECRET_HELDOUT_REASON'
    example.finding_review.comment = 'UNTRUSTED_REVIEW_COMMENT'
    payload = prepare_payload(target.manifest, target.target, [example], frozen.runtime, assets(tmp_path))
    user = payload['messages'][1]
    content = json.loads(user['content'])
    assert content['examples'][0]['expected'] == example.expected
    assert 'expected' not in content['target'] and 'reason' not in content['target']
    encoded = json.dumps(payload)
    assert 'SECRET_HELDOUT_REASON' not in encoded and 'UNTRUSTED_REVIEW_COMMENT' not in encoded
    assert 'storage_key' not in encoded and str(tmp_path) not in encoded
    assert len(user['images']) == 2
    for encoded_image, case in zip(user['images'], [example, target]):
        assert b64decode(encoded_image) == (assets(tmp_path).root / case.manifest.original_image.storage_key).read_bytes()
    baseline = prepare_payload(target.manifest, target.target, [], frozen.runtime, assets(tmp_path))
    assert json.loads(baseline['messages'][1]['content'])['examples'] == []
    assert len(baseline['messages'][1]['images']) == 1

@pytest.mark.parametrize('outcome', ['pass', 'fail', 'unsure', 'unavailable'])
def test_real_protocol_mock_preserves_finite_outcomes_and_digest(tmp_path, outcome):
    frozen = inputs(tmp_path)
    case = frozen.truth.cases[0]
    client, requests = client_for(frozen.runtime, case, outcome=outcome)
    try:
        result = client.predict(case.manifest, case.target, frozen.policy.cases, assets(tmp_path))
    finally:
        client.close()
    assert result.prediction.outcome == outcome and result.model_verified is True
    assert result.duration_ms >= 0 and sum((r.url.path == '/api/tags' for r in requests)) == 2
    assert all((r.url.host in {'localhost', '127.0.0.1', '::1'} for r in requests))


def test_consecutive_requests_keep_model_warm_with_independent_context(tmp_path):
    frozen = inputs(tmp_path)
    case = frozen.truth.cases[0]
    client, requests = client_for(frozen.runtime, case)
    try:
        baseline = client.predict(case.manifest, case.target, [], assets(tmp_path))
        candidate = client.predict(case.manifest, case.target, frozen.policy.cases, assets(tmp_path))
    finally:
        client.close()

    assert baseline.model_verified and candidate.model_verified
    chats = [json.loads(request.content) for request in requests if request.url.path == '/api/chat']
    assert len(chats) == 2
    for chat in chats:
        assert chat['keep_alive'] == 300
        assert chat['options']['num_gpu'] == 0
        assert [message['role'] for message in chat['messages']] == ['system', 'user']
    assert len(chats[0]['messages'][1]['images']) == 1
    assert json.loads(chats[0]['messages'][1]['content'])['examples'] == []
    assert len(chats[1]['messages'][1]['images']) == 2
    assert len(json.loads(chats[1]['messages'][1]['content'])['examples']) == 1
    # Warm residency must not skip either request's pre/post identity checks.
    assert sum(request.url.path == '/api/tags' for request in requests) == 4
    assert sum(request.url.path == '/api/show' for request in requests) == 4


def test_model_metadata_has_a_separate_bound_from_predictions(tmp_path):
    frozen = inputs(tmp_path)
    case = frozen.truth.cases[0]
    client, _ = client_for(frozen.runtime, case, metadata_padding=100_000)
    try:
        result = client.predict(case.manifest, case.target, [], assets(tmp_path))
    finally:
        client.close()
    assert result.model_verified is True
    assert result.prediction.outcome == 'fail'


@pytest.mark.parametrize('padding', [
    {'metadata_padding': 1024 * 1024},
    {'metadata_padding': 100_000, 'chat_padding': 70_000},
])
def test_metadata_and_prediction_bounds_both_remain_enforced(tmp_path, padding):
    frozen = inputs(tmp_path)
    case = frozen.truth.cases[0]
    client, _ = client_for(frozen.runtime, case, **padding)
    try:
        result = client.predict(case.manifest, case.target, [], assets(tmp_path))
    finally:
        client.close()
    assert result.model_verified is False
    assert result.prediction.outcome == 'unavailable'
    assert result.prediction.reason == 'response_limit'

@pytest.mark.parametrize('options', [{'drift': True}, {'broken': True}, {'capabilities': ['completion']}])
def test_identity_drift_invalid_output_and_no_vision_are_unavailable(tmp_path, options):
    frozen = inputs(tmp_path)
    case = frozen.truth.cases[0]
    client, _ = client_for(frozen.runtime, case, **options)
    try:
        result = client.predict(case.manifest, case.target, [], assets(tmp_path))
    finally:
        client.close()
    assert result.prediction.outcome == 'unavailable' and result.model_verified is False

def test_offline_server_never_returns_pass(tmp_path):
    frozen = inputs(tmp_path)
    case = frozen.truth.cases[0]

    def offline(request):
        raise httpx.ConnectError('private internal endpoint', request=request)
    client = GuidelineClient(frozen.runtime, transport=httpx.MockTransport(offline))
    try:
        result = client.predict(case.manifest, case.target, [], assets(tmp_path))
    finally:
        client.close()
    assert result.prediction.outcome == 'unavailable'
    assert 'private' not in result.prediction.reason

def test_missing_mask_allowed_but_changed_source_rejected(tmp_path):
    frozen = inputs(tmp_path)
    case = frozen.truth.cases[0]
    assert case.manifest.raw_mask.availability.value == 'missing'
    image, data = evidence(case.manifest, frozen.runtime, assets(tmp_path))
    assert image and data['objects'] and (data['image_size'] == [4, 3])
    (assets(tmp_path).root / case.manifest.original_image.storage_key).write_bytes(b'not original')
    with pytest.raises(FeedbackError):
        evidence(case.manifest, frozen.runtime, assets(tmp_path))

def test_context_and_output_bounds_are_enforced_before_evaluation(tmp_path):
    frozen = inputs(tmp_path)
    case = frozen.truth.cases[0]
    tiny = ExecutionPin.model_validate(frozen.runtime.model_dump() | dict(num_ctx=1024))
    with pytest.raises(FeedbackError) as caught:
        prepare_payload(case.manifest, case.target, frozen.policy.cases, tiny, assets(tmp_path))
    assert caught.value.code == 'CONTEXT_NOT_REPRESENTABLE'

    def oversized(request):
        return httpx.Response(200, content=b'x' * 70000)
    client = GuidelineClient(frozen.runtime, transport=httpx.MockTransport(oversized))
    try:
        result = client.predict(case.manifest, case.target, [], assets(tmp_path))
    finally:
        client.close()
    assert result.prediction.outcome == 'unavailable'

def test_model_findings_are_pending_and_measurements_not_rewritten(tmp_path, monkeypatch):
    frozen = inputs(tmp_path)
    case = frozen.truth.cases[0]
    client, _ = client_for(frozen.runtime, case)
    monkeypatch.setattr('backend.app.services.feedback.GuidelineClient', lambda pin: client)
    from backend.app.models.feedback import FeedbackRuntime
    original = RuleEvaluationResult(findings=[], internal_check_failures=0)
    result = augment_evaluation(original, book(), case.manifest, FeedbackRuntime(execution=frozen.runtime), assets(tmp_path), audit_id='new_audit', sample_id=case.manifest.sample_id, sample_revision=1)
    finding = result.findings[0]
    assert finding.review.resolution.value == 'pending' and finding.review.revision == 0
    assert finding.evidence.measurements['model_proposal']['model_verified'] is True
    assert original.findings == [] and result.internal_check_failures == 0

def test_malformed_duplicate_response_is_unavailable(tmp_path):
    frozen = inputs(tmp_path)
    case = frozen.truth.cases[0]

    def duplicates(request):
        return httpx.Response(200, content=b'{"models":[],"models":[]}')
    client = GuidelineClient(frozen.runtime, transport=httpx.MockTransport(duplicates))
    try:
        result = client.predict(case.manifest, case.target, [], assets(tmp_path))
    finally:
        client.close()
    assert result.prediction.outcome == 'unavailable' and result.model_verified is False

def test_programming_failure_is_not_disguised_as_passing_or_expected_unavailability(tmp_path, monkeypatch):
    frozen = inputs(tmp_path)
    case = frozen.truth.cases[0]
    client, _ = client_for(frozen.runtime, case)

    def broken(*args):
        raise RuntimeError('Synthetic programming error.')
    monkeypatch.setattr('backend.app.inference.annotation_guideline.prepare_payload', broken)
    try:
        with pytest.raises(RuntimeError):
            client.predict(case.manifest, case.target, [], assets(tmp_path))
    finally:
        client.close()


def test_public_audit_accepts_explicit_policy_but_not_private_runtime():
    from backend.app.models.audit_submission import AuditRequest
    base = dict(dataset_id='dataset', rulebook_id='book', rulebook_version=1)
    assert AuditRequest(**base, feedback_policy_id='policy_a').feedback_policy_id == 'policy_a'
    with pytest.raises(ValidationError):
        AuditRequest(**base, feedback_runtime={'model': 'untrusted'})
