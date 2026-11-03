"""Prompt v6: one bounded request per eligible target, outcomes derived from structured fields.

A mock local model server replaces Ollama. Every image and annotation is SYNTHETIC. These tests verify
mechanics (request content, identities, aggregation, failure handling); they say nothing about how a real
model answers.
"""
from dataclasses import replace
import json

import httpx
import pytest

from backend.app.core.config import AssetStorageSettings
from backend.app.core.feedback import FeedbackError
from backend.app.inference.annotation_guideline import DIAGNOSTIC_EXCERPT_CHARS, MAX_FOCUSED_TARGETS, GuidelineClient
from backend.app.models.applicability import RuleApplicability
from backend.app.models.feedback import FeedbackRuntime, FocusedAssessment, RulePin
from backend.app.models.review_feedback import ImageReviewFeedback
from backend.app.services.evaluation import RuleEvaluationResult
from backend.app.services.feedback import augment_evaluation
from backend.app.services.review_correction import flagged_object_keys
from backend.app.services.rule_applicability import FocusedCall, combine_focused, resolve_scope
from backend.tests.applicability_fixtures import (
    FakeOllama, IMAGE, MAN, WOMAN, imported, labelme_document, polygon_shape, rectangle_shape, requires_importer,
    rulebook, two_polygons, v5_pin, v6_pin, visual_rule)

pytestmark = requires_importer      # tests that build a stored sample need the managed importer

HEAD = visual_rule('RULE-2', 'The bounding box includes the visible head.')
FIT = visual_rule('RULE-7', 'The polygon follows the visible boundary of that same person.')
OCCLUDED = visual_rule('RULE-5', 'For a person partly hidden by another object or by the image edge, annotate only the '
                                 'visible portions, and do not extend boxes or polygons to cover body parts that cannot be seen.')


def focused(compliance='yes', condition='applies', geometry='polygon', reason='What is visible at the boundary.'):
    return {'condition': condition, 'compliance': compliance, 'geometry': geometry, 'reason': reason}


class ScriptedOllama(FakeOllama):
    """FakeOllama whose responder may return a raw string or a dict carrying '__raw__'/'__done_reason__'."""

    def handler(self, request):
        if request.url.path != '/api/chat':
            return super().handler(request)
        payload = json.loads(request.content)
        self.chats.append(payload)
        self.requests.append(request.url.path)
        document = json.loads(payload['messages'][-1]['content'])
        reply = self.answer(document)
        done_reason = 'stop'
        if isinstance(reply, str):
            content = reply
        else:
            reply = dict(reply)
            done_reason = reply.pop('__done_reason__', 'stop')
            body = {'case_id': document['case_id'], 'rule_id': document['rule_id'],
                    'object_key': document['focus']['object_key'], 'evidence_references': ['image', 'annotations'], **reply}
            content = json.dumps(body)
        return httpx.Response(200, json={'model': 'test-vision', 'done': True, 'done_reason': done_reason,
                                         'prompt_eval_count': 1000, 'eval_count': 40,
                                         'message': {'role': 'assistant', 'content': content}})


def sample(tmp_path, document=None):
    pubs, settings, _, _ = imported(tmp_path, 'labelme', data=document or two_polygons())
    return pubs[0].manifest, AssetStorageSettings(root=settings.asset_root)


def predict(tmp_path, responder, *rules, document=None, pin=None, diagnostics=False, feedback=None):
    manifest, assets = sample(tmp_path, document)
    server = ScriptedOllama(responder)
    client = GuidelineClient(pin or v6_pin(), transport=server.transport(), capture_diagnostics=diagnostics)
    try:
        recorded = client.predict(manifest, RulePin(rulebook_id='b', rulebook_version=1, rule=rules[0]), [], assets,
                                  **({} if feedback is None else {'reviewer_feedback': feedback}))
        return recorded, server, client
    finally:
        client.close()


def by_key(recorded):
    return {r.object_key: r for r in recorded.scope.results}


# ------------------------------------------------------------------------- requests

def test_one_bounded_request_is_made_per_eligible_target_each_bound_to_its_key(tmp_path):
    recorded, server, client = predict(tmp_path, lambda d: focused(), FIT)
    assert server.chat_count == 2 and [c['object_key'] for c in client.call_log] == ['/shapes/0', '/shapes/1']
    seen = []
    for chat in server.chats:
        document = json.loads(chat['messages'][-1]['content'])
        key = document['focus']['object_key']
        seen.append(key)
        assert [t['object_key'] for t in document['requirement_scope']['eligible_targets']] == [key]
        other = '/shapes/1' if key == '/shapes/0' else '/shapes/0'
        context = {c['object_key']: c for c in document['requirement_scope']['context_objects']}
        assert 'judged in its own request' in context[other]['why_not_a_target']
        images = [m for m in chat['messages'] if m.get('images')]
        assert len(images) == 4 and all(len(m['images']) == 1 for m in images)
        assert all(key in m['content'] for m in images)                       # every caption names the target
        assert chat['truncate'] is False and chat['shift'] is False
        assert chat['format']['properties']['object_key'] and 'outcome' not in chat['format']['properties']
        assert document['focus']['crop_window_original_px'] and document['focus']['mapping']
    assert seen == ['/shapes/0', '/shapes/1']
    assert recorded.prediction.outcome == 'pass' and recorded.model_verified is True


def test_polygon_only_data_makes_no_request_at_all_for_a_box_rule(tmp_path):
    recorded, server, client = predict(tmp_path, lambda d: focused(), HEAD)
    assert server.requests == [] and client.call_log == []
    assert recorded.prediction.outcome == 'unavailable' and recorded.model_verified is False
    assert recorded.scope.unavailable.code == 'supplied_box_not_available' and recorded.scope.outcome_source == 'no_model_call'


def test_a_context_annotation_without_the_required_geometry_is_never_a_target(tmp_path):
    document = labelme_document(rectangle_shape([[1, 1], [8, 12]]), polygon_shape(WOMAN))
    recorded, server, _ = predict(tmp_path, lambda d: focused(geometry='box'), HEAD, document=document)
    assert server.chat_count == 1
    content = json.loads(server.chats[0]['messages'][-1]['content'])
    [context] = content['requirement_scope']['context_objects']
    assert context['object_key'] == '/shapes/1' and context['derived_box_is_not_an_annotation'] is True
    assert [r.object_key for r in recorded.scope.results] == ['/shapes/0']


def test_display_numbers_are_never_the_identity(tmp_path):
    recorded, server, _ = predict(tmp_path, lambda d: focused(compliance='no' if d['focus']['object_key'] == '/shapes/0' else 'yes'), FIT)
    for chat in server.chats:
        document = json.loads(chat['messages'][-1]['content'])
        row = document['requirement_scope']['eligible_targets'][0]
        assert row['object_key'].startswith('/shapes/') and row['object_id'] in (1, 2)
    assert by_key(recorded)['/shapes/0'].outcome == 'fail' and by_key(recorded)['/shapes/1'].outcome == 'pass'


def test_reviewer_feedback_travels_with_each_focused_request(tmp_path):
    feedback = ImageReviewFeedback(reviewer_id='reviewer', source_audit_id='audit_parent',
                                   comment='The right-hand outline looks displaced.', propose_correction=False)
    _, server, _ = predict(tmp_path, lambda d: focused(), FIT, feedback=feedback)
    for chat in server.chats:
        assert json.loads(chat['messages'][-1]['content'])['reviewer_feedback']['comment'].startswith('The right-hand')


def test_policy_examples_are_not_part_of_this_protocol(tmp_path):
    manifest, assets = sample(tmp_path)
    client = GuidelineClient(v6_pin(), transport=ScriptedOllama(lambda d: focused()).transport())
    try:
        with pytest.raises(FeedbackError) as error:
            client.predict(manifest, RulePin(rulebook_id='b', rulebook_version=1, rule=FIT), [object()], assets)
    finally:
        client.close()
    assert error.value.code == 'POLICY_INCOMPATIBLE'


def test_too_many_targets_is_unavailable_before_inference_and_never_silently_shortened(tmp_path):
    shapes = [polygon_shape([[i, 2], [i + 1, 2], [i + 1, 6], [i, 6]]) for i in range(MAX_FOCUSED_TARGETS + 1)]
    recorded, server, _ = predict(tmp_path, lambda d: focused(), FIT, document=labelme_document(*shapes))
    assert server.requests == [] and recorded.prediction.outcome == 'unavailable'
    assert recorded.scope.unavailable.code == 'too_many_targets'
    assert len(recorded.scope.eligible) == MAX_FOCUSED_TARGETS + 1       # none were dropped from the record


def test_request_bounds_still_apply_per_target(tmp_path, monkeypatch):
    recorded, server, client = predict(tmp_path, lambda d: focused(), FIT, pin=v6_pin(num_ctx=1024))
    assert server.chat_count == 0 and recorded.prediction.outcome == 'unavailable'
    assert {r.flags[1] for r in recorded.scope.results} == {'call:context_limit'}
    assert [c['status'] for c in client.call_log] == ['context_limit', 'context_limit']
    # An attachment larger than the pinned image bound is rejected, whatever the rendering produces.
    import backend.app.services.focused_prompt as focused_prompt
    real_render = focused_prompt.render_focus
    monkeypatch.setattr(focused_prompt, 'render_focus', lambda *args: replace(
        real_render(*args), overlay_crop=real_render(*args).overlay_crop + b'\0' * 4096))
    oversized = tmp_path / 'oversized'
    oversized.mkdir()
    recorded, server, _ = predict(oversized, lambda d: focused(), FIT, pin=v6_pin(max_image_bytes=2048))
    assert server.chat_count == 0 and {tuple(r.flags) for r in recorded.scope.results} == {('call_failed', 'call:context_limit')}


# ----------------------------------------------------------------------- aggregation

def test_a_valid_failure_on_one_target_is_kept_when_the_other_request_fails(tmp_path):
    def responder(document):
        return focused(compliance='no') if document['focus']['object_key'] == '/shapes/0' else '{"not": "valid"}'
    recorded, _, client = predict(tmp_path, responder, FIT, diagnostics=True)
    results = by_key(recorded)
    assert results['/shapes/0'].outcome == 'fail'
    assert results['/shapes/1'].outcome == 'unavailable' and results['/shapes/1'].flags == ['call_failed', 'call:invalid_proposal']
    assert recorded.prediction.outcome == 'fail'                    # the failure is not discarded
    assert recorded.model_verified is True
    assert [c['status'] for c in client.call_log] == ['ok', 'invalid_proposal']


def test_an_applicable_target_without_a_trusted_answer_can_never_allow_an_overall_pass(tmp_path):
    def responder(document):
        return focused() if document['focus']['object_key'] == '/shapes/1' else '{"not": "valid"}'
    recorded, *_ = predict(tmp_path, responder, FIT)
    assert by_key(recorded)['/shapes/1'].outcome == 'pass'
    assert recorded.prediction.outcome == 'unavailable' and recorded.scope.unavailable.code == 'no_valid_assessment'


def test_when_every_request_fails_nothing_is_verified_and_nothing_is_passed(tmp_path):
    recorded, server, client = predict(tmp_path, lambda d: dict(focused(), __done_reason__='length'), FIT)
    assert recorded.model_verified is False and recorded.prediction.outcome == 'unavailable'
    assert {r.flags[1] for r in recorded.scope.results} == {'call:incomplete_response'}
    assert server.chat_count == 2                                    # bounded: one try per target, no retries


def test_an_answer_for_another_annotation_is_rejected_and_the_requested_target_stays_uncertain(tmp_path):
    def responder(document):
        key = document['focus']['object_key']
        return dict(focused(), object_key='/shapes/1') if key == '/shapes/0' else focused()
    recorded, *_ = predict(tmp_path, responder, FIT)
    results = by_key(recorded)
    assert results['/shapes/0'].outcome == 'unsure' and results['/shapes/0'].flags == ['no_assessment', 'reference_mismatch']
    assert results['/shapes/1'].outcome == 'pass'
    assert any('answered where /shapes/0' in item for item in recorded.scope.rejected_references)
    assert recorded.prediction.outcome == 'unsure'


def test_a_reply_about_the_wrong_case_or_rule_is_an_identity_failure_not_a_result(tmp_path):
    def responder(document):
        return json.dumps({'case_id': 'f' * 64, 'rule_id': document['rule_id'],
                           'object_key': document['focus']['object_key'], 'evidence_references': ['image'], **focused()})
    recorded, *_ = predict(tmp_path, responder, FIT)
    assert {r.flags[1] for r in recorded.scope.results} == {'call:identity_mismatch'}


def test_duplicate_unknown_and_ineligible_results_never_become_trusted(tmp_path):
    scope = resolve_scope(FIT, [{'source_key': '/shapes/0', 'object_id': 1, 'label': 'person', 'bbox': [0, 0, 1, 1],
                                 'bbox_origin': 'derived_from_polygon', 'polygons': [[[0, 0], [1, 0], [1, 1]]]},
                                {'source_key': '/shapes/1', 'object_id': 2, 'label': 'person', 'bbox': [0, 0, 1, 1],
                                 'bbox_origin': 'derived_from_polygon', 'polygons': [[[0, 0], [1, 0], [1, 1]]]}],
                          image=IMAGE, annotation_format='labelme', algorithm='derived-v2')
    good = FocusedAssessment(case_id='c' * 64, rule_id='RULE-7', object_key='/shapes/0', condition='applies',
                             compliance='yes', geometry='polygon', reason='Fine.', evidence_references=['image'])
    outcome, _, final = combine_focused(scope, [
        FocusedCall('/shapes/0', assessment=good), FocusedCall('/shapes/0', assessment=good),   # duplicate
        FocusedCall('/shapes/1', assessment=good),
        FocusedCall('/shapes/9', assessment=good)])                                              # unknown key
    results = {r.object_key: r for r in final.results}
    assert results['/shapes/0'].outcome == 'unsure' and 'no_assessment' in results['/shapes/0'].flags
    assert results['/shapes/1'].outcome == 'pass'
    assert outcome == 'unsure'
    assert any('repeated' in item for item in final.rejected_references)
    assert any('/shapes/9' in item and 'not an eligible target' in item for item in final.rejected_references)


# ------------------------------------------------------------- conditional requirements

def condition_run(tmp_path, man, woman, rule=OCCLUDED):
    return predict(tmp_path, lambda d: man if d['focus']['object_key'] == '/shapes/0' else woman, rule)[0]


def test_a_condition_that_applies_is_assessed_for_compliance(tmp_path):
    recorded = condition_run(tmp_path, focused('no'), focused('yes'))
    results = by_key(recorded)
    assert (results['/shapes/0'].outcome, results['/shapes/1'].outcome) == ('fail', 'pass')
    assert recorded.prediction.outcome == 'fail'


def test_an_absent_condition_excludes_that_target_even_if_the_model_also_says_no(tmp_path):
    recorded = condition_run(tmp_path, focused('no', condition='not_present'), focused('yes'))
    results = by_key(recorded)
    assert results['/shapes/0'].outcome == 'unavailable' and 'condition_not_present' in results['/shapes/0'].flags
    assert recorded.prediction.outcome == 'pass'                     # decided only by the target the condition applies to


def test_when_the_condition_is_absent_everywhere_the_result_is_unavailable_not_a_pass(tmp_path):
    recorded = condition_run(tmp_path, focused('yes', condition='not_present'), focused('yes', condition='not_present'))
    assert recorded.prediction.outcome == 'unavailable' and recorded.scope.unavailable.code == 'condition_not_present'
    assert recorded.model_verified is True


def test_unclear_compliance_is_uncertain_and_never_a_pass(tmp_path):
    recorded = condition_run(tmp_path, focused('unclear'), focused('yes'))
    assert by_key(recorded)['/shapes/0'].outcome == 'unsure' and recorded.prediction.outcome == 'unsure'


@pytest.mark.parametrize('compliance', ['yes', 'no'])
def test_an_unclear_condition_is_uncertain_whatever_compliance_says(tmp_path, compliance):
    recorded = condition_run(tmp_path, focused(compliance, condition='unclear'), focused('yes'))
    results = by_key(recorded)
    assert results['/shapes/0'].outcome == 'unsure' and 'condition_unclear' in results['/shapes/0'].flags
    assert recorded.prediction.outcome == 'unsure'


def test_the_outcome_follows_the_structured_fields_never_the_explanation_text(tmp_path):
    recorded = condition_run(tmp_path, focused('yes', reason='This outline is a clear violation and fails the rule.'),
                             focused('no', reason='This outline is perfectly correct and passes.'))
    results = by_key(recorded)
    assert (results['/shapes/0'].outcome, results['/shapes/1'].outcome) == ('pass', 'fail')


def test_a_failure_under_one_rule_is_not_carried_to_another_rule(tmp_path):
    manifest, assets = sample(tmp_path)
    server = ScriptedOllama(lambda d: focused('no') if d['rule_id'] == 'RULE-7' else focused('yes'))
    client = GuidelineClient(v6_pin(), transport=server.transport())
    try:
        outcomes = {rule.rule_id: client.predict(manifest, RulePin(rulebook_id='b', rulebook_version=1, rule=rule), [], assets)
                    .prediction.outcome for rule in (FIT, OCCLUDED)}
    finally:
        client.close()
    assert outcomes == {'RULE-7': 'fail', 'RULE-5': 'pass'}


# ---------------------------------------------------- grounding and applicability version

def test_geometry_that_was_never_supplied_cannot_pass_or_fail_a_target(tmp_path):
    recorded, *_ = predict(tmp_path, lambda d: focused('no', geometry='box', reason='The box is too small.'), OCCLUDED)
    assert {r.outcome for r in recorded.scope.results} == {'unsure'}
    assert all('geometry_not_supplied' in r.flags for r in recorded.scope.results)


def test_applicability_for_v6_uses_derived_v2_while_v5_and_stored_rules_keep_their_behaviour(tmp_path):
    manifest, assets = sample(tmp_path)
    from backend.app.services.feedback_evidence import compute_scope
    v6 = compute_scope(manifest, OCCLUDED, v6_pin(), assets)
    v5 = compute_scope(manifest, OCCLUDED, v5_pin(), assets)
    assert (v6.applicability.source, v6.applicability.geometry, v6.applicability.status) == ('derived-v2', ['box', 'polygon'], 'clear')
    assert (v5.applicability.source, v5.applicability.geometry, v5.applicability.status) == ('derived-v1', ['polygon'], 'ambiguous')
    stored = OCCLUDED.model_copy(update={'applicability': RuleApplicability(
        geometry=['polygon'], source='reviewer', basis='Reviewer decided.')})
    for pin in (v5_pin(), v6_pin()):
        scope = compute_scope(manifest, stored, pin, assets)
        assert scope.applicability_origin == 'rule' and scope.applicability.source == 'reviewer'


# ------------------------------------------------------------------------- diagnostics

def test_diagnostics_are_opt_in_bounded_and_contain_no_prompt_or_image_data(tmp_path):
    noisy = 'x' * 5000
    recorded, _, client = predict(tmp_path, lambda d: noisy, FIT, diagnostics=True)
    assert recorded.prediction.outcome == 'unavailable'
    assert len(client.diagnostics) == 2
    for entry in client.diagnostics:
        assert len(entry['content_excerpt']) == DIAGNOSTIC_EXCERPT_CHARS and entry['code'] == 'invalid_proposal'
        assert set(entry) <= {'object_key', 'rule_id', 'code', 'done_reason', 'prompt_eval_count', 'eval_count', 'content_excerpt'}
    quiet_dir = tmp_path / 'quiet'
    quiet_dir.mkdir()
    _, _, quiet = predict(quiet_dir, lambda d: noisy, FIT)
    assert quiet.diagnostics == []


# ---------------------------------------------------------------- through the audit path

def test_the_audit_records_per_target_results_and_flags_only_the_failing_annotation(tmp_path, monkeypatch):
    manifest, assets = sample(tmp_path)
    server = ScriptedOllama(lambda d: focused('no' if d['focus']['object_key'] == '/shapes/0' else 'yes'))
    monkeypatch.setattr('backend.app.services.feedback.GuidelineClient',
                        lambda execution: GuidelineClient(execution, transport=server.transport()))
    result = augment_evaluation(RuleEvaluationResult(findings=[], internal_check_failures=0), rulebook(HEAD, FIT), manifest,
                                FeedbackRuntime(execution=v6_pin()), assets, audit_id='audit_t',
                                sample_id=manifest.sample_id, sample_revision=1)
    findings = {f.rule_id: f for f in result.findings}
    assert findings['RULE-2'].outcome.value == 'unavailable' and server.chat_count == 2
    scope = findings['RULE-7'].evidence.measurements['model_proposal']['scope']
    assert {r['object_key']: r['outcome'] for r in scope['results']} == {'/shapes/0': 'fail', '/shapes/1': 'pass'}
    assert scope['image']['image_sha256'] == manifest.original_image.sha256
    assert flagged_object_keys([findings['RULE-7']]) == ['/shapes/0']
    assert findings['RULE-7'].review.resolution.value == 'pending'
