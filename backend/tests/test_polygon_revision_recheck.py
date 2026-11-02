"""A manual edit changes only the revision sent to the visual check, not the rule.

File-backed integration with synthetic images; no database or real model calls.
The fixtures do not establish visual accuracy or learning from corrections.
"""
from base64 import b64decode
from copy import deepcopy
import json

import pytest

from backend.app.core.config import AssetStorageSettings
from backend.app.core.correction import CorrectionSettings
from backend.app.models.correction import CorrectionRequest
from backend.app.models.feedback import ExecutionPin, RulePin
from backend.app.services.feedback_prompt import prepare_payload
from backend.app.services.json_corrections import prepare_json_correction
from backend.app.storage.workflow_repository import read_manifest_asset_file
from backend.tests.feedback_fixtures import book
from backend.tests.test_json_dataset_workflow import documents, imported


@pytest.mark.parametrize('format', ['coco', 'labelme'])
def test_manual_polygon_edit_reaches_new_revision_prompt_without_rewriting_source_or_rules(tmp_path, format):
    document = documents(format)
    records = document['annotations'] if format == 'coco' else document['shapes']
    second = deepcopy(records[0])
    if format == 'coco':
        second['id'] = 91
        second['bbox'] = [11, 2, 7, 8]
        second['segmentation'] = [[11, 2, 18, 2, 18, 10, 11, 10]]
    else:
        second['points'] = [[11, 2], [18, 2], [18, 10], [11, 10]]
        second['group_id'] = 43
    records.append(second)
    publications, settings, original_annotation, original_image = imported(tmp_path, format, data=document)
    original = publications[0]
    storage = AssetStorageSettings(root=settings.asset_root)
    objects = [object.model_dump(mode='json') for object in original.sample.objects]
    # A human inserts a point and adjusts the visible outline. No object is added.
    objects[0]['polygons'] = [[[1, 2], [5, 1.5], [9, 2], [9, 10], [1, 10]]]
    if format == 'labelme':
        objects[0]['bbox'] = [1, 1.5, 9, 10]
    request = CorrectionRequest.model_validate({
        'expected_revision': 1, 'reviewer_id': 'human', 'reason': 'Adjusted the visible head boundary.',
        'source_audit_id': 'audit_original', 'objects': objects,
    })
    revised = prepare_json_correction(original, request, CorrectionSettings(asset_root=settings.asset_root)).revised
    assert revised.manifest.sample_revision == 2
    assert revised.manifest.original_image == original.manifest.original_image
    assert revised.manifest.annotation_text.sha256 != original.manifest.annotation_text.sha256
    assert revised.sample.objects[1] == original.sample.objects[1]
    if format == 'coco':
        assert revised.sample.objects[0].bbox == original.sample.objects[0].bbox
    assert read_manifest_asset_file(original.manifest, 'annotation_text', storage)[1] == original_annotation

    rulebook = book()
    target = RulePin(rulebook_id=rulebook.rulebook_id, rulebook_version=rulebook.version, rule=rulebook.rules[0])
    pin = ExecutionPin(model='test-vision', model_digest='sha256:' + 'a' * 64,
        prompt_version='annotation-guideline-v4', preprocessing='original-and-annotation-overlay-v1', num_ctx=32768)
    before = prepare_payload(original.manifest, target, [], pin, storage)
    after = prepare_payload(revised.manifest, target, [], pin, storage)
    before_data = json.loads(before['messages'][-1]['content'])
    after_data = json.loads(after['messages'][-1]['content'])
    assert before_data['case_id'] != after_data['case_id']
    assert before_data['guideline'] == after_data['guideline']
    assert before_data['target']['inputs']['object_inventory'] == after_data['target']['inputs']['object_inventory']
    assert after_data['examples'] == []  # Saving a correction does not activate a teaching policy.
    assert 'reviewer_feedback' not in after_data
    assert 'Adjusted the visible head boundary.' not in json.dumps(after)
    assert 'expected' not in after_data['target']
    assert b64decode(before['messages'][1]['images'][0]) == original_image
    assert b64decode(after['messages'][1]['images'][0]) == original_image
    # Reconstruct native records from the compact table and compare the exact edit.
    inputs = after_data['target']['inputs']
    native = [dict(zip(inputs['source_columns'], row[3])) | row[4] for row in inputs['annotations']]
    if format == 'coco':
        assert native[0]['segmentation'] == [[1, 2, 5, 1.5, 9, 2, 9, 10, 1, 10]]
    else:
        assert native[0]['points'] == objects[0]['polygons'][0]
    assert native[1] == second
