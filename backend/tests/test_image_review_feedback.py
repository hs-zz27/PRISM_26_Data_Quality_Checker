"""Image feedback changes only an explicit recheck; drafts never save annotations."""
from copy import deepcopy
from base64 import b64decode
from io import BytesIO
import json

import pytest
from pydantic import ValidationError
from PIL import Image

from backend.app.models.correction import RecheckRequest
from backend.app.models.review_feedback import ImageReviewFeedback
from backend.app.services.feedback_prompt import prepare_payload
from backend.app.services.review_correction import Edit, apply_draft_edits, propose_correction
from backend.tests.feedback_fixtures import inputs, assets, book


def feedback():
    return ImageReviewFeedback(reviewer_id='reviewer', source_audit_id='audit_parent',
                               comment='Check whether the box misses the feet.', propose_correction=True)


@pytest.mark.parametrize('overrides', [
    {'feedback': '   '}, {'feedback': 'bad\x00text'}, {'propose_correction': True},
    {'feedback': 'Check feet', 'sample_ids': ['a', 'b'], 'sample_revisions': {'a': 1, 'b': 1}},
])
def test_feedback_requires_one_image_and_explicit_text(overrides):
    with pytest.raises(ValidationError):
        RecheckRequest.model_validate(dict(sample_ids=['a'], sample_revisions={'a': 1},
                                          reviewer_id='reviewer') | overrides)


def test_feedback_enters_only_requested_prompt_and_does_not_replace_guideline(tmp_path):
    frozen = inputs(tmp_path)
    case = frozen.truth.cases[0]
    before = prepare_payload(case.manifest, case.target, [], frozen.runtime, assets(tmp_path))
    with_feedback = prepare_payload(case.manifest, case.target, [], frozen.runtime, assets(tmp_path),
                                    reviewer_feedback=feedback())
    context = json.loads(with_feedback['messages'][1]['content'])
    assert context['reviewer_feedback']['comment'] == feedback().comment
    assert context['guideline'] == case.target.rule.model_dump(mode='json')
    assert prepare_payload(case.manifest, case.target, [], frozen.runtime, assets(tmp_path)) == before
    assert 'reviewer_feedback' not in json.loads(before['messages'][1]['content'])


@pytest.mark.parametrize('failure', [None, 'bounds', 'identity', 'unsure', 'drift'])
def test_model_draft_is_bounded_and_does_not_write_assets(tmp_path, failure):
    frozen = inputs(tmp_path)
    manifest = frozen.truth.cases[0].manifest
    settings = assets(tmp_path)
    measurements = {'objects': [{'annotation': {'object_id': 1, 'label': 'person', 'bbox': [1, 1, 4, 3]}}]}
    unchanged = deepcopy(measurements)
    original_files = {p.relative_to(settings.root): p.read_bytes() for p in settings.root.rglob('*') if p.is_file()}
    calls = []

    class Client:
        def __init__(self, execution): self.verifications = 0
        def verify(self):
            self.verifications += 1
            if failure == 'drift' and self.verifications == 2:
                from backend.app.inference.annotation_guideline import InferenceUnavailable
                raise InferenceUnavailable('identity_drift')
        def close(self): calls.append('closed')
        def request(self, method, path, payload):
            calls.append(payload)
            context = json.loads(payload['messages'][1]['content'])
            assert context['reviewer_feedback'] == feedback().comment
            assert payload['options']['num_gpu'] == 0
            images = payload['messages'][1]['images']
            assert len(images) == 2
            assert b64decode(images[0]) == original_files[next(p for p in original_files if str(p).endswith(f'{manifest.sample_id}/1/original_image'))]
            with Image.open(BytesIO(b64decode(images[1]))) as overlay:
                assert overlay.size == (4, 3)
            draft = dict(case_id=context['case_id'], status='proposed', reason='Synthetic model suggestion.',
                         edits=[dict(object_key='penn_fudan:1', label=None, bbox=[1, 1, 3, 3], polygons=None)])
            if failure == 'bounds': draft['edits'][0]['bbox'] = [1, 1, 99, 3]
            if failure == 'identity': draft['edits'][0]['object_key'] = 'penn_fudan:999'
            if failure == 'unsure': draft.update(status='unsure', edits=[])
            return dict(model=frozen.runtime.model, done=True, done_reason='stop', prompt_eval_count=1000,
                        message={'content': json.dumps(draft)})

    result = propose_correction(measurements, feedback(), manifest, book(), settings, frozen.runtime, client_factory=Client)
    assert result['status'] == ('proposed' if failure is None else 'unsure' if failure == 'unsure' else 'unavailable')
    assert result['view_version'] == 'original-and-annotation-overlay-v1'
    assert (result['objects'] is not None) == (failure is None)
    assert measurements == unchanged
    assert original_files == {p.relative_to(settings.root): p.read_bytes() for p in settings.root.rglob('*') if p.is_file()}
    assert calls[-1] == 'closed'


def test_json_polygon_draft_preserves_source_identity_and_updates_derived_bounds():
    obj = dict(object_id=1, source_key='/shapes/4', source_id=None, group_id=17,
               coordinate_system='zero_based_continuous', label='person', bbox=[1, 1, 4, 4],
               bbox_origin='derived_from_polygon', polygons=[[[1, 1], [4, 1], [4, 4]]])
    edit = Edit.model_validate_json(json.dumps(dict(object_key='/shapes/4', label=None, bbox=None,
                                                     polygons=[[[0.25, 1], [5, 1], [5, 5]]])))
    result = apply_draft_edits([obj], [edit], (20, 20), 'labelme')
    assert result[0]['bbox'] == [0.25, 1, 5, 5]
    assert result[0]['source_key'] == '/shapes/4' and result[0]['group_id'] == 17
    assert obj['bbox'] == [1, 1, 4, 4]


def test_missing_model_returns_explanation_without_calling_model(tmp_path):
    frozen = inputs(tmp_path)
    result = propose_correction({}, feedback(), frozen.truth.cases[0].manifest, book(), assets(tmp_path), None)
    assert result['status'] == 'unavailable' and result['objects'] is None
