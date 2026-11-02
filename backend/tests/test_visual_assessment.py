"""Regression evidence validation. Mocked verdicts do NOT measure model accuracy."""
import json

import httpx
import pytest
from pydantic import ValidationError

from backend.app.core.config import AssetStorageSettings
from backend.app.inference.annotation_guideline import GuidelineClient
from backend.app.models.feedback import ExecutionPin, RecordedPrediction, RulePin, unit_id
from backend.app.models.visual_assessment import VisualAssessment
from backend.app.services.feedback_prompt import prepare_payload
from backend.app.services.visual_assessment import grounded_prediction, object_inventory
from backend.tests.feedback_fixtures import assets, book, case, record
from backend.tests.test_image_review_feedback import feedback
from backend.tests.test_json_dataset_workflow import documents, imported


def pin(**kwargs):
    return ExecutionPin(model='test-vision', model_digest='sha256:' + 'a' * 64,
        prompt_version='annotation-guideline-v4', preprocessing='original-and-annotation-overlay-v1',
        num_ctx=32768, **kwargs)


def observation(key='penn_fudan:1', *, outcome='pass', geometry=None, region=None):
    return {'object_key': key, 'geometry': ['box'] if geometry is None else geometry,
            'outcome': outcome, 'reason': 'Synthetic visible-evidence observation.',
            'region': region}


def assessment(*observations):
    return VisualAssessment.model_validate_json(json.dumps({
        'case_id': 'a' * 64, 'rule_id': 'SCENE-1', 'observations': observations}))


def source(count=1):
    return object_inventory([{'object_id': i, 'label': 'person', 'bbox': [1, 1, 10, 10]}
                             for i in range(1, count + 1)], 'penn_fudan')


def fake_client(execution, result, *, drift=False):
    requests = []
    tags = 0

    def handler(request):
        nonlocal tags
        requests.append(request)
        if request.url.path == '/api/version':
            return httpx.Response(200, json={'version': '0.33.3'})
        if request.url.path == '/api/tags':
            tags += 1
            return httpx.Response(200, json={'models': [{'name': execution.model,
                'digest': ('b' if drift and tags > 1 else 'a') * 64}]})
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'capabilities': ['vision']})
        assert request.url.path == '/api/chat'
        content = result(request) if callable(result) else json.dumps(result)
        return httpx.Response(200, json={'model': execution.model, 'done': True, 'done_reason': 'stop',
            'prompt_eval_count': 2000, 'message': {'role': 'assistant', 'content': content}})

    return GuidelineClient(execution, transport=httpx.MockTransport(handler)), requests


@pytest.mark.parametrize('format,geometry', [
    ('labelme', ['label', 'polygon']), ('coco', ['label', 'box', 'polygon'])])
def test_source_inventory_does_not_turn_derived_bounds_into_a_supplied_box(tmp_path, format, geometry):
    publications, settings, _, _ = imported(tmp_path, format)
    storage = AssetStorageSettings(root=settings.asset_root)
    target = RulePin(rulebook_id='book', rulebook_version=1, rule=book().rules[0])
    payload = prepare_payload(publications[0].manifest, target, [], pin(), storage)
    inputs = json.loads(payload['messages'][-1]['content'])['target']['inputs']
    row = inputs['object_inventory'][0]
    assert row['available_geometry'] == geometry
    assert row['object_key'] == publications[0].sample.objects[0].source_key
    assert row['object_id'] == 1
    assert payload['truncate'] is False and payload['shift'] is False
    assert payload['options']['num_gpu'] == 0
    assert 'observations' in payload['format']['properties']
    assert 'outcome' not in payload['format']['properties']  # backend computes the verdict


def test_wrong_label_does_not_remove_a_visible_person_from_consideration(tmp_path):
    document = documents('labelme')
    document['shapes'][0]['label'] = 'car'
    publications, settings, _, _ = imported(tmp_path, 'labelme', data=document)
    target = RulePin(rulebook_id='book', rulebook_version=1, rule=book().rules[0])
    payload = prepare_payload(publications[0].manifest, target, [], pin(), AssetStorageSettings(root=settings.asset_root))
    inventory = json.loads(payload['messages'][-1]['content'])['target']['inputs']['object_inventory']
    assert len(inventory) == 1 and inventory[0]['label'] == 'car'
    assert 'not its possibly incorrect annotation label' in payload['messages'][0]['content']


@pytest.mark.parametrize('unseen_geometry', ['box', 'mask'])
def test_polygon_only_input_cannot_support_box_or_mask_binary_verdict(unseen_geometry):
    inventory = object_inventory([{'object_id': 1, 'source_key': '/shapes/0', 'label': 'person',
        'bbox': [1, 2, 9, 10], 'bbox_origin': 'derived_from_polygon',
        'polygons': [[[1, 2], [9, 2], [9, 10]]]}], 'labelme')
    result = assessment(observation('/shapes/0', geometry=[unseen_geometry]))
    with pytest.raises(ValueError, match='unsupported_visual_geometry'):
        grounded_prediction(result, inventory, (20, 16))


@pytest.mark.parametrize('outcome,expected', [
    ('fail', 'fail'), ('unsure', 'unsure'), ('unavailable', 'unavailable'), ('not_applicable', 'pass')])
def test_one_passing_object_cannot_hide_another_applicable_error_or_uncertainty(outcome, expected):
    other = observation('penn_fudan:2', outcome=outcome, region=[0, 0, 2, 2] if outcome == 'fail' else None)
    result = assessment(observation(), other)
    prediction = grounded_prediction(result, source(2), (20, 16))
    assert prediction.outcome == expected
    assert result.observations[1].outcome == outcome


def test_all_not_applicable_is_unavailable_not_a_vacuous_pass():
    result = grounded_prediction(assessment(observation(outcome='not_applicable', geometry=[])), source(), (20, 16))
    assert result.outcome == 'unavailable'
    assert 'No applicable annotation' in result.reason


def test_missing_or_unknown_objects_cannot_be_silently_ignored():
    with pytest.raises(ValueError, match='object_coverage_mismatch'):
        grounded_prediction(assessment(observation()), source(2), (20, 16))
    with pytest.raises(ValueError, match='object_coverage_mismatch'):
        grounded_prediction(assessment(observation('invented-object')), source(), (20, 16))
    with pytest.raises(ValidationError):
        assessment(observation(), observation())


@pytest.mark.parametrize('changes', [
    {'outcome': 'fail', 'region': None}, {'geometry': []},
    {'geometry': ['box', 'box']}, {'region': [3, 2, 1, 4]},
    {'region': [0, 0, float('inf'), 2]}, {'region': [False, 0, 2, 2]},
    {'reason': ' '}, {'reason': 'bad\x00reason'}, {'object_key': '\ud800'},
])
def test_unsupported_or_unlocatable_observations_are_rejected(changes):
    with pytest.raises((ValidationError, UnicodeError)):
        assessment(observation() | changes)


def test_location_may_point_outside_annotation_but_must_stay_inside_image():
    # Missing feet lie outside a bad box. Clamping to that box would hide the error.
    result = assessment(observation(outcome='fail', region=[10, 12, 18, 16]))
    assert grounded_prediction(result, source(), (20, 16)).outcome == 'fail'
    with pytest.raises(ValueError, match='review_region_out_of_bounds'):
        grounded_prediction(result, source(), (20, 15))


def test_feedback_and_first_pass_use_same_image_and_native_geometry(tmp_path):
    item = case(tmp_path)
    settings = assets(tmp_path)
    before = {p: p.read_bytes() for p in settings.root.rglob('*') if p.is_file()}
    baseline = prepare_payload(item.manifest, item.target, [], pin(), settings)
    recheck = prepare_payload(item.manifest, item.target, [], pin(), settings, reviewer_feedback=feedback())
    left = json.loads(baseline['messages'][-1]['content'])
    right = json.loads(recheck['messages'][-1]['content'])
    assert left == {key: value for key, value in right.items() if key != 'reviewer_feedback'}
    assert baseline['messages'][:-1] == recheck['messages'][:-1]
    assert right['reviewer_feedback']['comment'] == feedback().comment
    assert before == {p: p.read_bytes() for p in settings.root.rglob('*') if p.is_file()}


def test_v4_retains_confirmed_conditions_and_never_sends_heldout_answers(tmp_path):
    from backend.tests.feedback_fixtures import inputs
    from backend.tests.test_guideline_context import context_rule, ANIMALS
    frozen = inputs(tmp_path)
    heldout = frozen.truth.cases[0]
    heldout.reason = 'SECRET_HELDOUT_ANSWER'
    example = frozen.policy.cases[0]
    target = RulePin(rulebook_id='book_ctx', rulebook_version=1, rule=context_rule())
    # Training and target share the exact confirmed rule; target truth remains separate.
    example.target = target
    example.reference.rule_id = target.rule.rule_id
    example.model_proposal = record(example.manifest, target, example.expected)
    example = type(example).model_validate_json(example.model_dump_json())
    payload = prepare_payload(heldout.manifest, target, [example], pin(), assets(tmp_path))
    document = json.loads(payload['messages'][-1]['content'])
    assert document['guideline']['context_sentences'] == [ANIMALS[0], ANIMALS[8]]
    assert document['examples'][0]['expected'] == example.expected
    assert 'expected' not in document['target']
    assert 'SECRET_HELDOUT_ANSWER' not in json.dumps(payload)
    assert document['examples'][0]['inputs']['object_inventory'] == document['target']['inputs']['object_inventory']
    assert document['examples'][0]['image_attachment'] != document['target']['image_attachment']


def test_legacy_v3_still_accepts_its_frozen_single_verdict_protocol(tmp_path):
    item = case(tmp_path)
    execution = ExecutionPin(model='test-vision', model_digest='sha256:' + 'a' * 64,
        prompt_version='annotation-guideline-v3', preprocessing='original-and-annotation-overlay-v1')
    response = record(item.manifest, item.target, 'pass').prediction.model_dump(mode='json')
    client, requests = fake_client(execution, response)
    try:
        result = client.predict(item.manifest, item.target, [], assets(tmp_path))
    finally:
        client.close()
    assert result.prediction.outcome == 'pass' and result.model_verified
    assert 'visual_assessment' not in result.model_dump(mode='json')
    request = next(json.loads(request.content) for request in requests if request.url.path == '/api/chat')
    assert 'object_inventory' not in json.loads(request['messages'][-1]['content'])['target']['inputs']
    assert 'observations' not in request['format']['properties']


def test_mocked_shifted_box_failure_is_retained_with_object_and_review_location(tmp_path):
    item = case(tmp_path)
    # A controlled response tests plumbing, NOT whether a real model detects this.
    failure = observation(outcome='fail', region=[0, 1, 1, 3])
    failure['reason'] = 'The supplied box misses the visible left foot.'
    reply = {'case_id': unit_id(item.manifest, item.target), 'rule_id': item.target.rule.rule_id,
             'observations': [failure]}
    client, requests = fake_client(pin(), reply)
    try:
        result = client.predict(item.manifest, item.target, [], assets(tmp_path))
    finally:
        client.close()
    assert result.prediction.outcome == 'fail' and result.model_verified
    assert 'Object #1' in result.prediction.reason
    assert 'left foot' in result.prediction.reason
    assert result.visual_assessment.observations[0].region == (0, 1, 1, 3)
    assert sum(request.url.path == '/api/tags' for request in requests) == 2


def test_omitted_second_annotation_fails_closed_through_the_client(tmp_path):
    document = documents('labelme')
    document['shapes'].append({**document['shapes'][0], 'points': [[11, 2], [18, 2], [18, 10], [11, 10]]})
    publications, settings, _, _ = imported(tmp_path, 'labelme', data=document)
    manifest = publications[0].manifest
    target = RulePin(rulebook_id='book', rulebook_version=1, rule=book().rules[0])
    response = {'case_id': unit_id(manifest, target), 'rule_id': target.rule.rule_id,
                'observations': [observation('/shapes/0', geometry=['polygon'])]}
    client, _ = fake_client(pin(), response)
    try:
        result = client.predict(manifest, target, [], AssetStorageSettings(root=settings.asset_root))
    finally:
        client.close()
    assert result.prediction.outcome == 'unavailable'
    assert result.prediction.reason == 'object_coverage_mismatch'


def test_missing_objects_do_not_trigger_a_vacuous_pass_or_an_inference_request(tmp_path):
    document = documents('labelme')
    document['shapes'] = []
    publications, settings, _, _ = imported(tmp_path, 'labelme', data=document)
    manifest = publications[0].manifest
    target = RulePin(rulebook_id='book', rulebook_version=1, rule=book().rules[0])
    client, requests = fake_client(pin(), {})
    try:
        result = client.predict(manifest, target, [], AssetStorageSettings(root=settings.asset_root))
    finally:
        client.close()
    assert result.prediction.reason == 'no_annotation_objects_to_assess'
    assert result.prediction.outcome == 'unavailable'
    assert not any(request.url.path == '/api/chat' for request in requests)


def test_changed_source_cannot_be_used_for_a_verified_observation(tmp_path):
    item = case(tmp_path)
    settings = assets(tmp_path)
    (settings.root / item.manifest.annotation_text.storage_key).write_bytes(b'changed after import')
    client, requests = fake_client(pin(), {})
    try:
        result = client.predict(item.manifest, item.target, [], settings)
    finally:
        client.close()
    assert result.prediction.outcome == 'unavailable'
    assert result.prediction.reason == 'source_unavailable'
    assert not any(request.url.path == '/api/chat' for request in requests)


def test_object_observations_reach_pending_findings_without_writing_reviews(tmp_path, monkeypatch):
    from backend.app.models.feedback import FeedbackRuntime
    from backend.app.services.feedback import augment_evaluation
    from backend.app.services.evaluation import RuleEvaluationResult
    item = case(tmp_path)
    response = {'case_id': unit_id(item.manifest, item.target), 'rule_id': item.target.rule.rule_id,
                'observations': [observation()]}
    client, _ = fake_client(pin(), response)
    monkeypatch.setattr('backend.app.services.feedback.GuidelineClient', lambda execution: client)
    original = RuleEvaluationResult(findings=[], internal_check_failures=0)
    result = augment_evaluation(original, book(), item.manifest, FeedbackRuntime(execution=pin()),
        assets(tmp_path), audit_id='new_audit', sample_id=item.manifest.sample_id, sample_revision=1)
    finding = result.findings[0]
    assert finding.outcome.value == 'pass' and finding.object_id is None
    assert finding.review.resolution.value == 'pending' and finding.review.revision == 0
    assert finding.evidence.measurements['model_proposal']['visual_assessment']['observations'] == [observation()]
    assert original.findings == []


@pytest.mark.parametrize('change,reason', [
    ('unknown_object', 'object_coverage_mismatch'),
    ('unsupported_mask', 'unsupported_visual_geometry'),
    ('outside_image', 'review_region_out_of_bounds'),
    ('wrong_case', 'identity_mismatch'), ('drift', 'model_identity_changed'),
    ('legacy_reply', 'invalid_proposal'),
])
def test_bad_model_claims_never_publish_a_verified_pass(tmp_path, change, reason):
    item = case(tmp_path)
    response = {'case_id': unit_id(item.manifest, item.target), 'rule_id': item.target.rule.rule_id,
                'observations': [observation()]}
    if change == 'unknown_object':
        response['observations'][0]['object_key'] = 'invented'
    elif change == 'unsupported_mask':
        response['observations'][0]['geometry'] = ['mask']
    elif change == 'outside_image':
        response['observations'][0]['region'] = [0, 0, 9, 9]
    elif change == 'wrong_case':
        response['case_id'] = 'b' * 64
    elif change == 'legacy_reply':
        response = record(item.manifest, item.target, 'pass').prediction.model_dump(mode='json')
    client, _ = fake_client(pin(), response, drift=change == 'drift')
    try:
        result = client.predict(item.manifest, item.target, [], assets(tmp_path))
    finally:
        client.close()
    assert result.prediction.outcome == 'unavailable'
    assert result.prediction.reason == reason
    assert not result.model_verified and result.visual_assessment is None


def test_legacy_record_roundtrip_does_not_add_new_fields_or_change_fingerprints(tmp_path):
    item = case(tmp_path)
    original = record(item.manifest, item.target).model_dump(mode='json')
    assert 'visual_assessment' not in original
    assert RecordedPrediction.model_validate_json(json.dumps(original)).model_dump(mode='json') == original


def test_recorded_aggregate_cannot_disagree_with_object_results(tmp_path):
    item = case(tmp_path)
    original = record(item.manifest, item.target, 'pass').model_dump(mode='json')
    original['visual_assessment'] = {'case_id': unit_id(item.manifest, item.target),
        'rule_id': item.target.rule.rule_id, 'observations': [observation(outcome='unsure')]}
    with pytest.raises(ValidationError):
        RecordedPrediction.model_validate_json(json.dumps(original))
