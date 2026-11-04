"""Scoped (v5) visual audit: eligibility before inference, targets tied to stable keys, bounded corrections.

A mock local model server replaces Ollama. Every image and annotation is SYNTHETIC. These are mocked
tests; they say nothing about how a real model behaves.
"""
import json

import httpx
import pytest

from backend.app.core.config import AssetStorageSettings
from backend.app.core.feedback import FeedbackError
from backend.app.inference.annotation_guideline import GuidelineClient
from backend.app.models.feedback import FeedbackRuntime, RulePin, unit_id
from backend.app.models.review_feedback import ImageReviewFeedback
from backend.app.services.evaluation import RuleEvaluationResult
from backend.app.services.feedback import augment_evaluation
from backend.app.services.json_audit import audit_json_sample
from backend.app.services.review_correction import attach_review_context, flagged_object_keys, propose_correction
from backend.tests.applicability_fixtures import (
    FakeOllama, MAN, WOMAN, assessment, imported, labelme_document, polygon_shape, rectangle_shape, requires_importer,
    rulebook, two_polygons, v3_pin, v5_pin, visual_rule)

pytestmark = requires_importer      # every test here builds a stored sample with the managed importer

HEAD = visual_rule('RULE-2', 'The bounding box includes the visible head.')
FEET = visual_rule('RULE-3', 'The bounding box includes the feet and lower legs of the visible person.')
BODY = visual_rule('RULE-4', 'The bounding box includes other visible body parts.')
OCCLUDED = visual_rule('RULE-5', 'Annotations of partly hidden people cover only the visible part of each person.')
FIT = visual_rule('RULE-7', 'The polygon follows the visible outline of the person.')
PARTS = visual_rule('RULE-8', 'Polygon parts of one object belong to the same person.')


def man_shifted(document):
    """The reproduction: only the man's polygon is displaced, the woman's is unchanged."""
    return document


def answer(document):
    rule = document['rule_id']
    if rule == 'RULE-7':
        return {'outcome': 'fail', 'reason': 'Summary.', 'assessments': [
            assessment('/shapes/0', 'fail', reason='The polygon lies to the right of the man.'),
            assessment('/shapes/1', 'pass', reason='The polygon follows the woman.')]}
    if rule == 'RULE-5':
        return {'outcome': 'pass', 'reason': 'Summary.', 'assessments': [
            assessment('/shapes/0', 'unsure', condition='unclear', reason='Overlap with the woman is unclear.'),
            assessment('/shapes/1', 'pass', condition='not_present', reason='She is not partly hidden.')]}
    return {'outcome': 'pass', 'reason': 'Summary.', 'assessments': [
        assessment('/shapes/0', 'pass'), assessment('/shapes/1', 'pass')]}


def run(tmp_path, monkeypatch, document, rules, responder=answer, pin=None, feedback=None):
    pubs, settings, _, _ = imported(tmp_path, 'labelme', data=document)
    manifest = pubs[0].manifest
    server = FakeOllama(responder)
    monkeypatch.setattr('backend.app.services.feedback.GuidelineClient',
                        lambda execution: GuidelineClient(execution, transport=server.transport()))
    result = augment_evaluation(
        RuleEvaluationResult(findings=[], internal_check_failures=0), rulebook(*rules), manifest,
        FeedbackRuntime(execution=pin or v5_pin()), AssetStorageSettings(root=settings.asset_root),
        audit_id='audit_t', sample_id=manifest.sample_id, sample_revision=1,
        **({} if feedback is None else {'reviewer_feedback': feedback}))
    return {f.rule_id: f for f in result.findings}, server, manifest, AssetStorageSettings(root=settings.asset_root)


def scope_of(finding):
    return finding.evidence.measurements['model_proposal']['scope']


# --------------------------------------------------------------- the reproduction

def test_reproduction_box_rules_on_polygon_only_data_are_unavailable_and_make_no_model_call(tmp_path, monkeypatch):
    findings, server, *_ = run(tmp_path, monkeypatch, two_polygons(), [HEAD, FEET, BODY])
    assert server.requests == [] and server.chat_count == 0
    for rule_id in ('RULE-2', 'RULE-3', 'RULE-4'):
        finding = findings[rule_id]
        assert finding.outcome.value == 'unavailable'
        assert 'derived from polygons' in finding.reason
        scope = scope_of(finding)
        assert scope['unavailable']['code'] == 'supplied_box_not_available' and scope['eligible'] == []
        assert {item['object_key'] for item in scope['ineligible']} == {'/shapes/0', '/shapes/1'}
        assert finding.evidence.measurements['model_proposal']['model_verified'] is False


def test_more_targets_than_the_response_bound_never_truncates_or_calls_the_model(tmp_path, monkeypatch):
    document = labelme_document(*(polygon_shape(MAN) for _ in range(33)))
    findings, server, *_ = run(tmp_path, monkeypatch, document, [FIT])
    assert server.requests == []
    assert findings['RULE-7'].outcome.value == 'unavailable'
    assert findings['RULE-7'].reason == 'context_limit'
    assert len(scope_of(findings['RULE-7'])['eligible']) == 33


def test_reproduction_the_polygon_failure_names_the_man_and_leaves_the_woman_passing(tmp_path, monkeypatch):
    findings, server, manifest, _ = run(tmp_path, monkeypatch, two_polygons(), [HEAD, FIT, OCCLUDED, PARTS])
    assert server.chat_count == 3                     # the box rule made no request
    fit = findings['RULE-7']
    assert fit.outcome.value == 'fail'
    scope = scope_of(fit)
    assert {r['object_key']: r['outcome'] for r in scope['results']} == {'/shapes/0': 'fail', '/shapes/1': 'pass'}
    assert scope['image']['sample_id'] == manifest.sample_id and scope['image']['image_key'] == manifest.image_key
    assert scope['image']['image_sha256'] == manifest.original_image.sha256
    assert '/shapes/0: fail' in fit.reason and '/shapes/1: pass' in fit.reason
    assert flagged_object_keys([fit]) == ['/shapes/0']


def test_reproduction_the_occlusion_answer_addresses_its_condition_and_propagates_uncertainty(tmp_path, monkeypatch):
    findings, *_ = run(tmp_path, monkeypatch, two_polygons(), [OCCLUDED])
    finding = findings['RULE-5']
    assert finding.outcome.value == 'unsure'          # the model said pass; the unclear man wins
    scope = scope_of(finding)
    assert scope['model_reported_outcome'] == 'pass' and scope['outcome_source'] == 'derived_from_target_results'
    flags = {r['object_key']: r['flags'] for r in scope['results']}
    assert 'condition_unclear' in flags['/shapes/0'] and 'condition_not_present' in flags['/shapes/1']
    assert any('the outcome follows its per-target results' in note for note in scope['notes'])


def test_findings_stay_pending_and_nothing_is_corrected_automatically(tmp_path, monkeypatch):
    findings, *_ = run(tmp_path, monkeypatch, two_polygons(), [FIT])
    assert findings['RULE-7'].review.resolution.value == 'pending'


def test_the_request_names_eligible_targets_and_marks_derived_boxes_as_not_annotations(tmp_path, monkeypatch):
    document = labelme_document(rectangle_shape([[1, 1], [8, 12]]), polygon_shape(WOMAN))
    _, server, *_ = run(tmp_path, monkeypatch, document, [HEAD], responder=lambda d: {
        'outcome': 'pass', 'reason': 'ok', 'assessments': [assessment('/shapes/0', geometry='box')]})
    [chat] = server.chats
    content = json.loads(chat['messages'][-1]['content'])
    scope = content['requirement_scope']
    assert [t['object_key'] for t in scope['eligible_targets']] == ['/shapes/0']
    assert scope['eligible_targets'][0]['supplied_geometry'] == ['box']
    [context] = scope['context_objects']
    assert context['object_key'] == '/shapes/1' and context['derived_box_is_not_an_annotation'] is True
    assert context['supplied_geometry'] == ['polygon'] and 'derived from its polygon' in context['why_not_a_target']
    assert 'requirement_scope.eligible_targets' in chat['messages'][0]['content']
    assert 'assessments' in json.dumps(chat['format'])
    assert chat['truncate'] is False and chat['shift'] is False


def test_labelme_rectangle_boxes_are_checked_by_box_requirements(tmp_path, monkeypatch):
    document = labelme_document(rectangle_shape([[1, 1], [8, 12]]))
    findings, server, *_ = run(tmp_path, monkeypatch, document, [HEAD], responder=lambda d: {
        'outcome': 'pass', 'reason': 'ok', 'assessments': [assessment('/shapes/0', geometry='box')]})
    assert server.chat_count == 1 and findings['RULE-2'].outcome.value == 'pass'


def test_mixed_objects_judge_the_eligible_annotation_and_report_the_other_as_context(tmp_path, monkeypatch):
    document = labelme_document(rectangle_shape([[1, 1], [8, 12]]), polygon_shape(WOMAN))
    findings, *_ = run(tmp_path, monkeypatch, document, [HEAD], responder=lambda d: {
        'outcome': 'fail', 'reason': 'x', 'assessments': [
            assessment('/shapes/0', 'fail', geometry='box', reason='The box ends below the head.'),
            assessment('/shapes/1', 'fail', geometry='box', reason='Claimed without a supplied box.')]})
    scope = scope_of(findings['RULE-2'])
    assert [r['object_key'] for r in scope['results']] == ['/shapes/0']
    assert scope['ineligible'][0]['object_key'] == '/shapes/1'
    assert any('context annotation' in item for item in scope['rejected_references'])
    assert flagged_object_keys([findings['RULE-2']]) == ['/shapes/0']


def test_a_failed_model_call_still_records_which_annotations_were_eligible(tmp_path):
    pubs, settings, _, _ = imported(tmp_path, 'labelme', data=two_polygons())
    manifest = pubs[0].manifest
    client = GuidelineClient(v5_pin(), transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    try:
        recorded = client.predict(manifest, RulePin(rulebook_id='b', rulebook_version=1, rule=FIT), [],
                                  AssetStorageSettings(root=settings.asset_root))
    finally:
        client.close()
    assert recorded.prediction.outcome == 'unavailable' and recorded.model_verified is False
    assert [o.object_key for o in recorded.scope.eligible] == ['/shapes/0', '/shapes/1'] and recorded.scope.results == []


def test_a_malformed_model_reply_is_unavailable_not_a_finding(tmp_path, monkeypatch):
    findings, *_ = run(tmp_path, monkeypatch, two_polygons(), [FIT], responder=lambda d: {
        'outcome': 'fail', 'reason': 'x', 'assessments': [{'object_key': '/shapes/0', 'outcome': 'fail'}]})
    assert findings['RULE-7'].outcome.value == 'unavailable' and findings['RULE-7'].reason == 'invalid_proposal'


def test_v4_requests_still_enforce_the_context_and_size_bounds(tmp_path):
    pubs, settings, _, _ = imported(tmp_path, 'labelme', data=two_polygons())
    from backend.app.services.feedback_prompt import prepare_payload
    target = RulePin(rulebook_id='b', rulebook_version=1, rule=FIT)
    storage = AssetStorageSettings(root=settings.asset_root)
    with pytest.raises(FeedbackError) as error:
        prepare_payload(pubs[0].manifest, target, [], v5_pin(num_ctx=1024), storage)
    assert error.value.code == 'CONTEXT_NOT_REPRESENTABLE'
    with pytest.raises(FeedbackError):
        prepare_payload(pubs[0].manifest, target, [], v5_pin(max_request_bytes=1024), storage)


def test_reviewer_feedback_travels_with_the_scoped_request(tmp_path, monkeypatch):
    feedback = ImageReviewFeedback(reviewer_id='reviewer', source_audit_id='audit_parent',
                                   comment="The man's outline looks shifted.", propose_correction=False,
                                   object_keys=['/shapes/0'])
    _, server, *_ = run(tmp_path, monkeypatch, two_polygons(), [FIT], feedback=feedback)
    content = json.loads(server.chats[0]['messages'][-1]['content'])
    assert content['reviewer_feedback']['comment'] == "The man's outline looks shifted."
    assert [t['object_key'] for t in content['requirement_scope']['eligible_targets']] == ['/shapes/0', '/shapes/1']


def test_existing_v3_audits_are_unscoped_and_unchanged(tmp_path, monkeypatch):
    findings, server, *_ = run(tmp_path, monkeypatch, two_polygons(), [HEAD, FIT], pin=v3_pin(), responder=lambda d: {
        'outcome': 'fail', 'reason': 'Legacy proposal.'})
    assert server.chat_count == 2                      # v3 sends every rule to the model, as documented
    assert all('requirement_scope' not in json.loads(chat['messages'][-1]['content']) for chat in server.chats)
    assert 'scope' not in findings['RULE-2'].evidence.measurements['model_proposal']
    assert findings['RULE-2'].outcome.value == 'fail'


# ------------------------------------------------------------ correction proposals

def corrections(tmp_path, edits, *, feedback_keys=None, editable_keys=None):
    pubs, settings, _, _ = imported(tmp_path, 'labelme', data=two_polygons())
    manifest = pubs[0].manifest
    storage = AssetStorageSettings(root=settings.asset_root)
    measurements = audit_json_sample(manifest, storage)
    sent = []

    class Client:
        def __init__(self, execution): pass
        def verify(self): pass
        def close(self): pass
        def request(self, method, path, payload):
            sent.append(json.loads(payload['messages'][1]['content']))
            return {'model': 'test-vision', 'done': True, 'prompt_eval_count': 1000, 'message': {'content': json.dumps(
                {'case_id': sent[-1]['case_id'], 'status': 'proposed', 'reason': 'Synthetic correction.', 'edits': edits})}}
    feedback = ImageReviewFeedback(reviewer_id='reviewer', source_audit_id='audit_parent', comment="Man's outline.",
                                   propose_correction=True, **({} if feedback_keys is None else {'object_keys': feedback_keys}))
    result = propose_correction(measurements, feedback, manifest, rulebook(FIT), storage, v5_pin(),
                                client_factory=Client, editable_keys=editable_keys)
    return result, measurements, sent


def shifted(points):
    return [[x + 1, y] for x, y in points]


def edit(key, points):
    return {'object_key': key, 'label': None, 'bbox': None, 'polygons': [shifted(points)]}


def test_a_proposal_for_the_man_leaves_the_woman_exactly_as_supplied(tmp_path):
    result, measurements, sent = corrections(tmp_path, [edit('/shapes/0', MAN)], editable_keys=['/shapes/0'])
    assert result['status'] == 'proposed' and result['editable_object_keys'] == ['/shapes/0']
    original = [row['annotation'] for row in measurements['objects']]
    assert result['objects'][0]['polygons'] != original[0]['polygons']
    assert result['objects'][1] == original[1]                     # untouched annotation preserved exactly
    assert sent[0]['editable_object_keys'] == ['/shapes/0'] and 'editing_scope' in sent[0]


def test_a_proposal_that_also_edits_the_woman_is_discarded_whole(tmp_path):
    result, _, _ = corrections(tmp_path, [edit('/shapes/0', MAN), edit('/shapes/1', WOMAN)], editable_keys=['/shapes/0'])
    assert result['status'] == 'unsure' and result['objects'] is None
    assert 'outside the reviewed targets' in result['reason']


def test_reviewer_chosen_objects_override_the_flagged_ones_and_must_exist(tmp_path):
    result, _, sent = corrections(tmp_path, [edit('/shapes/1', WOMAN)], feedback_keys=['/shapes/1'],
                                  editable_keys=['/shapes/0'])
    assert result['status'] == 'proposed' and sent[0]['editable_object_keys'] == ['/shapes/1']
    other = tmp_path / 'unknown_key'
    other.mkdir()
    unknown, _, _ = corrections(other, [edit('/shapes/1', WOMAN)], feedback_keys=['/shapes/9'])
    assert unknown['status'] == 'unavailable' and unknown['objects'] is None


def test_without_reviewed_or_flagged_objects_proposals_behave_exactly_as_before(tmp_path):
    result, _, sent = corrections(tmp_path, [edit('/shapes/1', WOMAN)])
    assert result['status'] == 'proposed' and 'editable_object_keys' not in result
    assert 'editable_object_keys' not in sent[0] and 'editing_scope' not in sent[0]


def test_the_audit_passes_only_flagged_annotations_to_the_proposal(tmp_path, monkeypatch):
    findings, _, manifest, storage = run(tmp_path, monkeypatch, two_polygons(), [FIT])
    seen = {}
    monkeypatch.setattr('backend.app.services.review_correction.propose_correction',
                        lambda *args, **kwargs: seen.update(kwargs) or {'status': 'unsure'})
    feedback = ImageReviewFeedback(reviewer_id='reviewer', source_audit_id='audit_parent', comment='x',
                                   propose_correction=True)
    attach_review_context({'objects': []}, feedback, manifest, rulebook(FIT), storage,
                          FeedbackRuntime(execution=v5_pin()), None,
                          evaluation=RuleEvaluationResult(findings=list(findings.values()), internal_check_failures=0))
    assert seen['editable_keys'] == ['/shapes/0']


def test_object_references_are_stable_across_audit_feedback_proposal_and_recheck(tmp_path, monkeypatch):
    (tmp_path / 'again').mkdir()
    (tmp_path / 'proposal').mkdir()
    first, _, manifest, _ = run(tmp_path, monkeypatch, two_polygons(), [FIT])
    again, *_ = run(tmp_path / 'again', monkeypatch, two_polygons(), [FIT])
    keys = lambda f: [r['object_key'] for r in scope_of(f)['results']]
    assert keys(first['RULE-7']) == keys(again['RULE-7']) == ['/shapes/0', '/shapes/1']
    result, measurements, _ = corrections(tmp_path / 'proposal', [edit('/shapes/0', MAN)], editable_keys=flagged_object_keys([first['RULE-7']]))
    assert [o['source_key'] for o in result['objects']] == [row['annotation']['source_key'] for row in measurements['objects']]
