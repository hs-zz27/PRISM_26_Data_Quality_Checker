"""Complete geometry fits the v2 wire format without changing saved evidence."""
from copy import deepcopy
import json

import httpx
import pytest

from backend.app.core.config import AssetStorageSettings
from backend.app.core.feedback import FeedbackError
from backend.app.inference.annotation_guideline import GuidelineClient, InferenceUnavailable
from backend.app.models.feedback import ExecutionPin, RulePin
from backend.app.services.feedback_evidence import evidence
from backend.app.services.feedback_prompt import prepare_payload
from backend.app.services.prompt_encoding import compact_rule, compact_schema, correction_rows
from backend.app.services.review_correction import propose_correction
from backend.tests.feedback_fixtures import book
from backend.tests.test_image_review_feedback import feedback
from backend.tests.test_json_dataset_workflow import documents, imported


def pin(**changes):
    return ExecutionPin(model='test-vision', model_digest='sha256:' + 'a'*64,
                        prompt_version='annotation-guideline-v2', **changes)


@pytest.mark.parametrize('format', ['coco', 'labelme'])
def test_compact_evidence_preserves_each_native_field_and_vertex_once(tmp_path, format):
    source = documents(format)
    key = 'annotations' if format == 'coco' else 'shapes'
    source[key][0]['extra'] = {'review_context': 'Keep this custom attribute exactly: 人'}
    publications, settings, original, _ = imported(tmp_path, format, data=source)
    manifest = publications[0].manifest
    storage = AssetStorageSettings(root=settings.asset_root)
    _, data = evidence(manifest, pin(), storage, compact=True)
    assert data['annotation_columns'] == ['object_key', 'object_id', 'label', 'source']
    assert data['annotations'][0][3] == source[key][0]
    assert 'annotation_text' not in data and 'objects' not in data
    assert data['coordinate_system'] == 'zero_based_continuous'
    assert data['annotations'][0][0] == f'/{key}/0'
    target = RulePin(rulebook_id=book().rulebook_id, rulebook_version=1, rule=book().rules[0])
    payload = prepare_payload(manifest, target, [], pin(num_ctx=16384, num_predict=2048), storage)
    assert payload['truncate'] is False and payload['shift'] is False
    assert payload['options']['num_ctx'] == 16384 and payload['options']['num_gpu'] == 0
    context = json.loads(payload['messages'][1]['content'])
    assert context['target']['inputs'] == data
    assert context['guideline']['description'] == target.rule.description


def test_compact_rule_preserves_scope_parameters_and_distinct_citation():
    rule = book().rules[0].model_copy(update={'description': 'Visible portions only. If uncertain request review.',
        'source_sentence': 'Scope includes people labelled incorrectly.', 'parameters': {'threshold': 0.25}})
    result = compact_rule(rule)
    assert result['description'] == rule.description
    assert result['source_sentence'] == rule.source_sentence
    assert result['parameters'] == rule.parameters


def test_schema_compaction_keeps_constraints_and_properties_named_title():
    schema = {'title': 'Display name', 'type': 'object', 'required': ['title'],
              'properties': {'title': {'title': 'Field name', 'type': 'string', 'minLength': 1}},
              'additionalProperties': False}
    original = deepcopy(schema)
    result = compact_schema(schema)
    assert schema == original
    assert result == {'type': 'object', 'required': ['title'],
                      'properties': {'title': {'type': 'string', 'minLength': 1}}, 'additionalProperties': False}


def test_correction_rows_keep_all_vertices_groups_and_edit_identity():
    objects = [{'source_key': '/shapes/7', 'object_id': 8, 'source_id': None,
                'label': 'person', 'group_id': 14, 'bbox_origin': 'derived_from_polygon',
                'bbox': [1, 2, 9, 10], 'polygons': [[[1.125, 2.25], [9, 2], [9, 10]]]}]
    before = deepcopy(objects)
    assert correction_rows(objects) == [['/shapes/7', 8, 'person', None, objects[0]['polygons'], 14]]
    assert objects == before


@pytest.mark.parametrize('version', ['0.32.0', '0.33.2', '0.33.3-rc1', 'unknown', None])
def test_v2_refuses_servers_without_verified_nontruncating_support(version):
    paths = []
    def transport(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={'version': version})
    client = GuidelineClient(pin(), transport=httpx.MockTransport(transport))
    try:
        with pytest.raises(InferenceUnavailable, match='nontruncating_server_required'):
            client.verify()
    finally:
        client.close()
    assert paths == ['/api/version']


def test_oversized_complete_evidence_still_fails_before_inference(tmp_path):
    source = documents('labelme')
    source['shapes'][0]['extra'] = 'x' * 20000
    pubs, settings, _, _ = imported(tmp_path, 'labelme', data=source)
    target = RulePin(rulebook_id=book().rulebook_id, rulebook_version=1, rule=book().rules[0])
    with pytest.raises(FeedbackError) as error:
        prepare_payload(pubs[0].manifest, target, [], pin(), AssetStorageSettings(root=settings.asset_root))
    assert error.value.code == 'CONTEXT_NOT_REPRESENTABLE'


def test_complete_large_coco_geometry_fits_without_losing_records(tmp_path):
    import math
    source = documents('coco')
    template = source['annotations'][0]
    source['annotations'] = []
    for index in range(21):
        count = 19 if index < 11 else 18  # 389 source vertices, matching the real regression's size.
        points = [[round(5 + 3*math.cos(i*2*math.pi/count), 2),
                   round(6 + 3*math.sin(i*2*math.pi/count), 2)] for i in range(count)]
        source['annotations'].append({**deepcopy(template), 'id': 90+index,
            'segmentation': [[value for point in points for value in point]]})
    pubs, settings, _, _ = imported(tmp_path, 'coco', data=source)
    target = RulePin(rulebook_id=book().rulebook_id, rulebook_version=1, rule=book().rules[0])
    payload = prepare_payload(pubs[0].manifest, target, [], pin(num_ctx=16384, num_predict=2048),
                              AssetStorageSettings(root=settings.asset_root))
    records = json.loads(payload['messages'][1]['content'])['target']['inputs']['annotations']
    assert len(records) == 21
    assert [record[3] for record in records] == source['annotations']
    assert payload['truncate'] is False and payload['shift'] is False
    observed = prepare_payload(pubs[0].manifest, target, [], pin(num_ctx=16384, num_predict=2048),
        AssetStorageSettings(root=settings.asset_root), reviewer_feedback=feedback())
    assert json.loads(observed['messages'][1]['content'])['reviewer_feedback']['comment'] == feedback().comment


@pytest.mark.parametrize('response_change,expected', [({}, 'fail'),
    ({'prompt_eval_count': 16384}, 'unavailable'), ({'done_reason': 'length'}, 'unavailable')])
def test_v2_checks_server_support_identity_and_complete_response(tmp_path, response_change, expected):
    from backend.tests.feedback_fixtures import case, assets
    from backend.app.models.feedback import unit_id
    sample = case(tmp_path)
    requests = []
    def transport(request):
        requests.append(request)
        if request.url.path == '/api/version':
            return httpx.Response(200, json={'version': '0.33.3'})
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': 'test-vision', 'digest': 'a'*64}]})
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'capabilities': ['vision']})
        assert request.url.path == '/api/chat'
        payload = json.loads(request.content)
        assert payload['truncate'] is False and payload['shift'] is False
        prediction = {'case_id': unit_id(sample.manifest, sample.target), 'rule_id': sample.target.rule.rule_id,
                      'outcome': 'fail', 'reason': 'Synthetic result.', 'evidence_references': ['image']}
        return httpx.Response(200, json={'model': 'test-vision', 'done': True, 'done_reason': 'stop',
            'prompt_eval_count': 1000, 'message': {'content': json.dumps(prediction)}, **response_change})
    client = GuidelineClient(pin(num_predict=2048), transport=httpx.MockTransport(transport))
    try:
        result = client.predict(sample.manifest, sample.target, [], assets(tmp_path))
    finally:
        client.close()
    assert result.prediction.outcome == expected
    assert result.model_verified == (expected == 'fail')
    assert sum(request.url.path == '/api/chat' for request in requests) == 1


def test_v2_correction_is_a_bounded_draft_with_explicit_nontruncation(tmp_path):
    from backend.app.services.json_audit import audit_json_sample
    pubs, settings, _, _ = imported(tmp_path, 'labelme')
    manifest = pubs[0].manifest
    storage = AssetStorageSettings(root=settings.asset_root)
    measurements = audit_json_sample(manifest, storage)
    original = deepcopy(measurements)
    requests = []
    class Client:
        def __init__(self, execution): pass
        def verify(self): pass
        def close(self): pass
        def request(self, method, path, payload):
            requests.append(payload)
            context = json.loads(payload['messages'][1]['content'])
            assert context['object_columns'][0] == 'object_key'
            assert context['objects'][0][0] == '/shapes/0'
            assert context['objects'][0][4] == original['objects'][0]['annotation']['polygons']
            return {'model': 'test-vision', 'done': True, 'prompt_eval_count': 1000,
                    'message': {'content': json.dumps({'case_id': context['case_id'], 'status': 'proposed',
                        'reason': 'Synthetic correction.', 'edits': [{'object_key': '/shapes/0',
                            'label': 'pedestrian', 'bbox': None, 'polygons': None}]})}}
    result = propose_correction(measurements, feedback(), manifest, book(), storage,
                                pin(num_predict=2048), client_factory=Client)
    assert result['status'] == 'proposed' and result['protocol'] == 'review-correction-v2'
    assert result['objects'][0]['label'] == 'pedestrian'
    assert measurements == original
    assert requests[0]['truncate'] is False and requests[0]['shift'] is False
