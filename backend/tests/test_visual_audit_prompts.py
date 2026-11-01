"""Original/overlay pairing, coordinate fidelity and frozen execution versions."""
from base64 import b64decode
from copy import deepcopy
from io import BytesIO
import json

from PIL import Image
import pytest
from pydantic import ValidationError

from backend.app.core.config import AssetStorageSettings
from backend.app.core.feedback import FeedbackError
from backend.app.models.feedback import ExecutionPin, RulePin
from backend.app.services.annotation_overlay import annotation_overlay
from backend.app.services.feedback_evidence import evidence, visual_evidence
from backend.app.services.feedback_prompt import prepare_payload
from backend.tests.feedback_fixtures import book, inputs, assets
from backend.tests.test_json_dataset_workflow import imported
from backend.tests.test_image_review_feedback import feedback


def visual_pin(**values):
    return ExecutionPin(model='test-vision', model_digest='sha256:'+'a'*64,
        prompt_version='annotation-guideline-v3', preprocessing='original-and-annotation-overlay-v1', **values)


@pytest.mark.parametrize('format', ['coco', 'labelme'])
def test_target_attachments_preserve_original_and_exact_native_geometry(tmp_path, format):
    pubs, settings, _, image = imported(tmp_path, format)
    manifest = pubs[0].manifest
    storage = AssetStorageSettings(root=settings.asset_root)
    snapshots = {p: p.read_bytes() for p in storage.root.rglob('*') if p.is_file()}
    original, data, overlay = visual_evidence(manifest, visual_pin(), storage)
    assert original == image
    with Image.open(BytesIO(overlay)) as picture:
        assert picture.size == (20, 16)
        assert any(picture.getpixel((x, y)) == (0, 255, 255)
                   for y in range(picture.height) for x in range(picture.width))
    target = RulePin(rulebook_id=book().rulebook_id, rulebook_version=1, rule=book().rules[0])
    request = prepare_payload(manifest, target, [], visual_pin(num_predict=2048), storage)
    assert [message['role'] for message in request['messages']] == ['system', 'user', 'user', 'user']
    assert b64decode(request['messages'][1]['images'][0]) == original
    assert b64decode(request['messages'][2]['images'][0]) == overlay
    assert 'original' in request['messages'][1]['content']
    assert 'overlay' in request['messages'][2]['content']
    context = json.loads(request['messages'][-1]['content'])
    assert context['target']['inputs'] == data
    raw, _ = evidence(manifest, visual_pin(), storage, compact=True)
    assert raw == image
    legacy_inputs = evidence(manifest, visual_pin(), storage, compact=True)[1]
    for row, previous in zip(data['annotations'], legacy_inputs['annotations']):
        assert dict(zip(data['source_columns'], row[3])) | row[4] == previous[3]
    assert context['target']['image_attachment'] == 1
    assert context['target']['annotation_overlay_attachment'] == 2
    assert 'expected' not in context['target']
    assert 'ground truth' in request['messages'][0]['content']
    assert request['truncate'] is False and request['shift'] is False
    assert request['options']['num_gpu'] == 0 and request['options']['num_ctx'] == 16384
    assert snapshots == {p: p.read_bytes() for p in storage.root.rglob('*') if p.is_file()}
    reviewed = prepare_payload(manifest, target, [], visual_pin(num_predict=2048), storage,
                               reviewer_feedback=feedback())
    review_context = json.loads(reviewed['messages'][-1]['content'])
    assert review_context['reviewer_feedback']['comment'] == feedback().comment
    assert review_context['guideline'] == context['guideline']
    assert prepare_payload(manifest, target, [], visual_pin(num_predict=2048), storage) == request


def test_native_table_preserves_missing_fields_and_explicit_null_separately(tmp_path):
    from backend.tests.test_json_dataset_workflow import documents
    source = documents('labelme')
    source['shapes'].append(deepcopy(source['shapes'][0]))
    source['shapes'][0]['custom'] = None
    source['shapes'][1]['different_custom'] = {'unchanged': [1, '人']}
    pubs, settings, _, _ = imported(tmp_path, 'labelme', data=source)
    _, data, _ = visual_evidence(pubs[0].manifest, visual_pin(), AssetStorageSettings(root=settings.asset_root))
    assert 'custom' not in data['source_columns'] and 'different_custom' not in data['source_columns']
    restored = [dict(zip(data['source_columns'], row[3])) | row[4] for row in data['annotations']]
    assert restored == source['shapes']


def test_training_pairs_and_target_are_distinct_without_truth_leakage(tmp_path):
    frozen = inputs(tmp_path)
    truth, example = frozen.truth.cases[0], frozen.policy.cases[0]
    truth.reason = 'SECRET_HELDOUT_REASON'
    request = prepare_payload(truth.manifest, truth.target, [example], visual_pin(num_ctx=32768), assets(tmp_path))
    context = json.loads(request['messages'][-1]['content'])
    assert context['examples'][0]['image_attachment'] == 1
    assert context['examples'][0]['annotation_overlay_attachment'] == 2
    assert context['examples'][0]['expected'] == example.expected
    assert context['target']['image_attachment'] == 3
    assert context['target']['annotation_overlay_attachment'] == 4
    assert len([m for m in request['messages'] if 'images' in m]) == 4
    originals = [b64decode(request['messages'][i]['images'][0]) for i in (1, 3)]
    assert originals == [evidence(c.manifest, frozen.runtime, assets(tmp_path))[0] for c in (example, truth)]
    assert 'SECRET_HELDOUT_REASON' not in json.dumps(request)
    assert 'storage_key' not in json.dumps(request)


@pytest.mark.parametrize('format,left', [('penn_fudan', 10), ('coco', 11)])
def test_shared_overlay_uses_source_coordinate_origin(format, left):
    output = BytesIO()
    Image.new('RGB', (40, 40), 'white').save(output, format='PNG')
    image = output.getvalue()
    objects = [{'object_id': 1, 'label': 'person', 'bbox': [11, 11, 25, 30]}]
    unchanged = deepcopy(objects)
    with Image.open(BytesIO(annotation_overlay(image, objects, format))) as view:
        assert view.getpixel((left, 20)) == (255, 221, 0)
        assert view.getpixel((left-1, 20)) == (255, 255, 255)
    assert objects == unchanged


def test_polygon_view_does_not_invent_a_supplied_bounding_box():
    output = BytesIO()
    Image.new('RGB', (40, 40), 'white').save(output, format='PNG')
    objects = [{'object_id': 1, 'label': 'person', 'bbox': [10, 10, 25, 30],
                'bbox_origin': 'derived_from_polygon', 'polygons': [[[10, 10], [25, 10], [25, 30]]]}]
    with Image.open(BytesIO(annotation_overlay(output.getvalue(), objects, 'labelme'))) as view:
        assert view.getpixel((25, 20)) == (0, 255, 255)
        assert view.getpixel((10, 20)) == (255, 255, 255)


def test_overlay_and_request_limits_remain_enforced(tmp_path, monkeypatch):
    pubs, settings, _, _ = imported(tmp_path, 'labelme')
    target = RulePin(rulebook_id=book().rulebook_id, rulebook_version=1, rule=book().rules[0])
    storage = AssetStorageSettings(root=settings.asset_root)
    with pytest.raises(FeedbackError, match='complete required evidence'):
        prepare_payload(pubs[0].manifest, target, [], visual_pin(num_ctx=1024), storage)
    with pytest.raises(FeedbackError):
        prepare_payload(pubs[0].manifest, target, [], visual_pin(max_request_bytes=1024), storage)
    monkeypatch.setattr('backend.app.services.annotation_overlay.annotation_overlay', lambda *args: b'x'*1025)
    with pytest.raises(FeedbackError) as error:
        prepare_payload(pubs[0].manifest, target, [], visual_pin(max_image_bytes=1024), storage)
    assert error.value.code == 'CONTEXT_NOT_REPRESENTABLE'


@pytest.mark.parametrize('version,preprocessing', [
    ('annotation-guideline-v3', 'original-png-no-transform-v1'),
    ('annotation-guideline-v2', 'original-and-annotation-overlay-v1'),
    ('annotation-guideline-v1', 'original-and-annotation-overlay-v1'),
])
def test_audit_view_and_prompt_version_cannot_be_mismatched(version, preprocessing):
    with pytest.raises(ValidationError):
        ExecutionPin(model='test', model_digest='sha256:'+'a'*64,
                     prompt_version=version, preprocessing=preprocessing)


def test_new_execution_pins_select_overlay_without_changing_old_pins(monkeypatch):
    from backend.app.core.feedback import load_execution_pin
    monkeypatch.setenv('FEEDBACK_MODEL_DIGEST', 'sha256:'+'a'*64)
    pin = load_execution_pin()
    assert pin.prompt_version == 'annotation-guideline-v3'
    assert pin.preprocessing == 'original-and-annotation-overlay-v1'
    legacy = ExecutionPin(model='old', model_digest='sha256:'+'b'*64)
    assert legacy.prompt_version == 'annotation-guideline-v1'
