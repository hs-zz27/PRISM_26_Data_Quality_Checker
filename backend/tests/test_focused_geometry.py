"""COCO box/polygon confusion regressions with synthetic inputs and mocked model replies.

Tests enforce request evidence and result handling, not the model's visual accuracy.
"""
from base64 import b64decode
from copy import deepcopy
from io import BytesIO
import json

from PIL import Image
import pytest

from backend.app.core.config import AssetStorageSettings
from backend.app.inference.annotation_guideline import GuidelineClient
from backend.app.models.feedback import RulePin
from backend.app.services.focused_prompt import judged_geometry
from backend.app.services.feedback_evidence import compute_scope
from backend.tests.applicability_fixtures import imported, requires_importer, v6_pin, visual_rule
from backend.tests.test_focused_protocol import ScriptedOllama, focused

pytestmark = requires_importer
FEET = visual_rule('RULE-3', 'The bounding box of a fully visible person must include the visible feet and lower legs.')
POLYGON = visual_rule('RULE-7', 'A person polygon must follow the visible boundary of that person.')


def coco(labels=('person', 'skateboard')):
    return {'images': [{'id': 1, 'file_name': 'person.jpg', 'width': 20, 'height': 16}],
            'categories': [{'id': 1, 'name': labels[0]}, {'id': 2, 'name': labels[1]}],
            'annotations': [
                {'id': 10, 'image_id': 1, 'category_id': 1, 'bbox': [1, 1, 8, 12],
                 'segmentation': [[3, 4, 6, 4, 3, 8]], 'area': 6, 'iscrowd': 0},
                {'id': 11, 'image_id': 1, 'category_id': 2, 'bbox': [12, 1, 7, 12],
                 'segmentation': [[14, 4, 17, 4, 14, 8]], 'area': 6, 'iscrowd': 0}]}


def run(tmp_path, rule, reply, labels=('person', 'skateboard')):
    publications, settings, _, _ = imported(tmp_path, 'coco', data=coco(labels))
    manifest = publications[0].manifest
    before = manifest.model_dump(mode='json')
    server = ScriptedOllama(reply)
    client = GuidelineClient(v6_pin(num_ctx=16384), transport=server.transport())
    try:
        result = client.predict(manifest, RulePin(rulebook_id='book', rulebook_version=1, rule=rule), [],
                                AssetStorageSettings(root=settings.asset_root))
    finally:
        client.close()
    assert manifest.model_dump(mode='json') == before
    return result, server


@pytest.mark.parametrize('rule,geometry,box_color,polygon_color', [
    (FEET, 'box', (255, 0, 255), (0, 255, 255)),
    (POLYGON, 'polygon', (255, 221, 0), (255, 0, 255)),
])
def test_rule_controls_highlight_and_response_geometry_without_removing_evidence(
        tmp_path, rule, geometry, box_color, polygon_color):
    result, server = run(tmp_path, rule, lambda d: focused(geometry=geometry))
    assert result.model_verified and server.chat_count == 2
    for chat in server.chats:
        document = json.loads(chat['messages'][-1]['content'])
        assert document['focus']['judged_geometry'] == [geometry]
        assert chat['format']['properties']['geometry']['enum'] == [geometry]
        assert document['requirement_scope']['eligible_targets'][0]['supplied_geometry'] == ['box', 'polygon']
        inputs = document['target']['inputs']
        columns = inputs['source_columns']
        # Both original geometries and identities remain available, losslessly.
        for row, source in zip(inputs['annotations'], coco()['annotations']):
            assert row[3][columns.index('bbox')] == source['bbox']
            assert row[3][columns.index('segmentation')] == source['segmentation']
        images = [m for m in chat['messages'] if m.get('images')]
        assert len(images) == 4 and chat['truncate'] is False and chat['shift'] is False
        if document['focus']['object_key'] == '/annotations/0':
            overlay = Image.open(BytesIO(b64decode(images[1]['images'][0]))).convert('RGB')
            assert overlay.getpixel((1, 10)) == box_color
            assert overlay.getpixel((5, 4)) == polygon_color


@pytest.mark.parametrize('labels', [('person', 'skateboard'), ('car', 'person')])
def test_visible_class_condition_controls_applicability_even_when_labels_are_wrong(tmp_path, labels):
    def answer(document):
        if document['focus']['object_key'] == '/annotations/0':
            return focused('no', geometry='box', reason='A fully visible person has shoes below the box.')
        return focused('unclear', condition='not_present', geometry='box',
                       reason='The target is a skateboard, so the person-only rule does not apply.')
    result, server = run(tmp_path, FEET, answer, labels)
    assert server.chat_count == 2  # No hard-coded label filtering: a mislabeled person must still be checked.
    rows = {r.object_key: r for r in result.scope.results}
    assert rows['/annotations/0'].outcome == 'fail'
    assert rows['/annotations/1'].outcome == 'unavailable'
    assert rows['/annotations/1'].flags == ['condition_not_present']
    assert result.prediction.outcome == 'fail'
    assert all('person-only rule' in chat['messages'][0]['content'] for chat in server.chats)


def test_wrong_geometry_reply_is_still_downgraded_if_the_model_ignores_the_schema(tmp_path):
    result, _ = run(tmp_path, FEET, lambda d: focused('no', geometry='polygon'))
    assert result.prediction.outcome == 'unsure'
    assert all('geometry_not_in_requirement' in r.flags and r.outcome == 'unsure' for r in result.scope.results)


@pytest.mark.parametrize('description,selected', [
    ('The supplied polygon must stay inside its supplied bounding box.', ['box', 'polygon']),
    ('The supplied box or polygon must follow the visible object.', ['box', 'polygon']),
    ('The annotation label must name the visible object.', ['box', 'polygon']),
])
def test_relational_alternative_and_geometry_unspecified_rules_keep_all_relevant_geometry(tmp_path, description, selected):
    publications, settings, _, _ = imported(tmp_path, 'coco', data=coco())
    scope = compute_scope(publications[0].manifest, visual_rule('R', description), v6_pin(),
                          AssetStorageSettings(root=settings.asset_root))
    before = deepcopy(scope.model_dump(mode='json'))
    assert judged_geometry(scope, '/annotations/0') == selected
    assert scope.model_dump(mode='json') == before
