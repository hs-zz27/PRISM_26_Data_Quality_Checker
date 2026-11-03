"""Core feedback mechanics with synthetic images and mocked replies, not accuracy claims."""
import json

import pytest

from backend.app.inference.annotation_guideline import GuidelineClient
from backend.app.models.feedback import FeedbackRuntime, RulePin
from backend.app.models.review_feedback import ImageReviewFeedback
from backend.app.services.evaluation import RuleEvaluationResult
from backend.app.services.feedback import augment_evaluation
from backend.app.services.review_correction import flagged_object_keys
from backend.tests.applicability_fixtures import (
    WOMAN, labelme_document, polygon_shape, rectangle_shape, requires_importer, rulebook, v6_pin,
)
from backend.tests.test_focused_protocol import FIT, HEAD, ScriptedOllama, focused, predict, sample

pytestmark = requires_importer


def feedback(**kwargs):
    return ImageReviewFeedback(reviewer_id='reviewer', source_audit_id='audit_original',
        comment='The outline may miss the visible foot. Check its boundary against the image.', **kwargs)


@pytest.mark.parametrize('response,expected', [('yes', 'pass'), ('no', 'fail'), ('unclear', 'unsure')])
def test_fresh_visual_reply_can_agree_disagree_or_remain_uncertain(tmp_path, response, expected):
    result, server, _ = predict(tmp_path, lambda d: focused(response), FIT, feedback=feedback())
    assert result.prediction.outcome == expected
    assert server.chat_count == 2  # exactly one visual request per target, no extra text stage
    assert 'feedback_comparison' not in result.model_dump(mode='json')
    for payload in server.chats:
        assert sum(len(message.get('images', [])) for message in payload['messages']) == 4
        assert json.loads(payload['messages'][-1]['content'])['reviewer_feedback']['comment'] == feedback().comment


def test_object_feedback_changes_only_its_context_and_never_the_image_annotation_or_rule(tmp_path):
    manifest, assets = sample(tmp_path)
    manifest_before = manifest.model_dump(mode='json')
    target = RulePin(rulebook_id='book', rulebook_version=1, rule=FIT)
    target_before = target.model_dump(mode='json')
    baseline = ScriptedOllama(lambda d: focused('yes'))
    recheck = ScriptedOllama(lambda d: focused('no' if 'reviewer_feedback' in d else 'yes'))
    outputs = []
    for server, comment in [(baseline, None), (recheck, feedback(object_keys=['/shapes/0']))]:
        client = GuidelineClient(v6_pin(), transport=server.transport())
        try:
            outputs.append(client.predict(manifest, target, [], assets, reviewer_feedback=comment))
        finally:
            client.close()
    assert outputs[0].prediction.outcome == 'pass'
    assert outputs[1].prediction.outcome == 'fail'
    assert outputs[0].prediction.case_id == outputs[1].prediction.case_id
    assert baseline.chats[1] == recheck.chats[1]  # other object's comment-free visual request is identical
    before = baseline.chats[0]
    after = recheck.chats[0]
    assert before['messages'][:-1] == after['messages'][:-1]  # originals, overlays and crops
    before_document = json.loads(before['messages'][-1]['content'])
    after_document = json.loads(after['messages'][-1]['content'])
    assert after_document.pop('reviewer_feedback')['scope'] == 'object'
    assert before_document == after_document
    assert manifest.model_dump(mode='json') == manifest_before
    assert target.model_dump(mode='json') == target_before


def test_unknown_object_reference_is_reported_before_inference(tmp_path):
    result, server, _ = predict(tmp_path, lambda d: focused(), FIT,
                               feedback=feedback(object_keys=['/shapes/99']))
    assert server.requests == []
    assert result.prediction.outcome == 'unavailable'
    assert result.prediction.reason == 'feedback_target_not_found'


def test_feedback_cannot_make_a_polygon_only_object_eligible_for_a_box_rule(tmp_path):
    document = labelme_document(rectangle_shape([[1, 1], [8, 12]]), polygon_shape(WOMAN))
    result, server, _ = predict(tmp_path, lambda d: focused(geometry='box'), HEAD,
                               document=document, feedback=feedback(object_keys=['/shapes/1']))
    assert server.chat_count == 1
    sent = json.loads(server.chats[0]['messages'][-1]['content'])
    assert sent['focus']['object_key'] == '/shapes/0'
    assert 'reviewer_feedback' not in sent
    assert [item.object_key for item in result.scope.ineligible] == ['/shapes/1']


def test_invalid_model_reply_does_not_become_a_failure_just_because_the_reviewer_claimed_one(tmp_path):
    result, _, _ = predict(tmp_path, lambda d: '{', FIT, feedback=feedback())
    assert result.prediction.outcome == 'unavailable'
    assert not result.model_verified


def test_feedback_does_not_persist_into_a_later_call_without_feedback(tmp_path):
    manifest, assets = sample(tmp_path)
    target = RulePin(rulebook_id='book', rulebook_version=1, rule=FIT)
    server = ScriptedOllama(lambda d: focused('no' if 'reviewer_feedback' in d else 'yes'))
    client = GuidelineClient(v6_pin(), transport=server.transport())
    try:
        first = client.predict(manifest, target, [], assets, reviewer_feedback=feedback())
        second = client.predict(manifest, target, [], assets)
    finally:
        client.close()
    assert first.prediction.outcome == 'fail' and second.prediction.outcome == 'pass'
    assert all('reviewer_feedback' not in json.loads(chat['messages'][-1]['content']) for chat in server.chats[2:])


def test_recheck_records_feedback_but_leaves_human_decision_pending(tmp_path, monkeypatch):
    manifest, assets = sample(tmp_path)
    original = manifest.model_dump(mode='json')
    server = ScriptedOllama(lambda d: focused('no' if d['focus']['object_key'] == '/shapes/0' else 'yes'))
    monkeypatch.setattr('backend.app.services.feedback.GuidelineClient',
                        lambda execution: GuidelineClient(execution, transport=server.transport()))
    result = augment_evaluation(RuleEvaluationResult(findings=[], internal_check_failures=0),
        rulebook(FIT), manifest, FeedbackRuntime(execution=v6_pin()), assets, audit_id='audit_recheck',
        sample_id=manifest.sample_id, sample_revision=1, reviewer_feedback=feedback(object_keys=['/shapes/0']))
    finding = result.findings[0]
    assert finding.review.resolution.value == 'pending'
    assert finding.evidence.measurements['reviewer_feedback']['comment'] == feedback().comment
    assert 'feedback_comparison' not in finding.evidence.measurements['model_proposal']
    assert flagged_object_keys([finding]) == ['/shapes/0']
    assert manifest.model_dump(mode='json') == original
