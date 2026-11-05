"""Synthetic geometry and mocked transport checks, not a visual-accuracy benchmark."""
from base64 import b64decode
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json

import httpx
from PIL import Image
from pydantic import ValidationError
import pytest

from backend.app.core.feedback import FeedbackError
from backend.app.inference.feet_localization import FeetLocalizationClient
from backend.app.models.feedback import ExecutionPin
from backend.app.models.feet_localization import FeetLocalizationPin, FeetLocalizationRecord, FeetObservation
from backend.app.services.feet_localization import canonical_box, compare_feet_to_box, prepare_feet_localization


def image_bytes():
    out = BytesIO()
    Image.new('RGB', (100, 100), 'white').save(out, format='PNG')
    return out.getvalue()


def pin(**changes):
    source = ExecutionPin(model='test-vision', model_digest='sha256:' + 'a' * 64,
        num_predict=2048, prompt_version='annotation-guideline-v6', preprocessing='target-focus-overlay-v1')
    settings = FeetLocalizationPin.from_transport_settings(source).model_dump()
    return FeetLocalizationPin.model_validate({**settings, **changes})


def observation(identity):
    def edge(low):
        return {'low': low, 'high': low + 2}
    return {'localization_id': identity, 'state': 'localized', 'reason': 'Synthetic visible lower legs and shoes.',
        'regions': [{'region_id': name, 'x_min': edge(x), 'y_min': edge(40),
                     'x_max': edge(x + 10), 'y_max': edge(80)}
                    for name, x in [('leg_1', 10), ('leg_2', 30)]]}


def record(state='localized'):
    _, metadata = prepare_feet_localization(image_bytes(), 'Person in red clothing', pin())
    data = observation(metadata['localization_id'])
    if state != 'localized':
        data.update(state=state, regions=[])
    return FeetLocalizationRecord(**metadata, model_verified=True,
        observation=FeetObservation.model_validate(data), duration_ms=1)


def compare(source, box, **changes):
    values = dict(image_sha256=source.image_sha256, annotation_sha256='b' * 64,
                  object_key='/annotations/0', box=box, annotation_format='coco', origin='supplied')
    return compare_feet_to_box(source, **{**values, **changes})


@pytest.mark.parametrize('bottom,expected', [(90, 'pass'), (82, 'pass'), (81, 'unsure'), (80, 'unsure'), (79.9, 'fail')])
def test_containment_respects_uncertainty_and_adds_no_arbitrary_tolerance(bottom, expected):
    result = compare(record(), [0, 0, 50, bottom])
    assert result.outcome == expected
    assert result.review_required is True
    if expected == 'fail':
        assert result.violated_edges == ['leg_1:bottom', 'leg_2:bottom']


@pytest.mark.parametrize('box,edge', [([13, 0, 50, 90], 'left'), ([0, 43, 50, 90], 'top'),
                                    ([0, 0, 39, 90], 'right'), ([0, 0, 50, 79], 'bottom')])
def test_every_box_edge_is_compared(box, edge):
    result = compare(record(), box)
    assert result.outcome == 'fail'
    assert any(value.endswith(':' + edge) for value in result.violated_edges)


def test_same_record_compares_correct_and_shortened_boxes_without_mutation():
    source = record()
    before = source.model_dump(mode='json')
    good, bad = [0, 0, 50, 90], [0, 0, 50, 79]
    assert compare(source, good).outcome == 'pass'
    assert compare(source, bad).outcome == 'fail'
    assert source.model_dump(mode='json') == before
    assert good == [0, 0, 50, 90] and bad == [0, 0, 50, 79]


@pytest.mark.parametrize('state,expected', [('uncertain', 'unsure'), ('not_person', 'not_applicable'),
                                         ('not_fully_visible', 'not_applicable')])
def test_unclear_or_absent_condition_is_never_a_compliance_verdict(state, expected):
    assert compare(record(state), [0, 0, 50, 79]).outcome == expected


def test_image_binding_and_supplied_geometry_are_required():
    assert compare(record(), [0, 0, 50, 90], image_sha256='f' * 64).outcome == 'unavailable'
    assert compare(record(), [0, 0, 50, 90], origin='derived_from_polygon').outcome == 'unavailable'


@pytest.mark.parametrize('box', [[0, 0, 0, 20], [-1, 0, 50, 90], [0, 0, 101, 90],
                               [True, 0, 50, 90], [0, 0, float('nan'), 90], [0, 0, 50]])
def test_invalid_boxes_are_unavailable_not_geometric_failures(box):
    assert compare(record(), box).outcome == 'unavailable'


def test_penn_fudan_inclusive_pixels_convert_to_outer_pixel_edges():
    assert canonical_box([1, 1, 1, 1], 'penn_fudan', 'supplied', (100, 100)) == (0, 0, 1, 1)
    assert canonical_box([1, 1, 50, 90], 'penn_fudan', 'supplied', (100, 100)) == (0, 0, 50, 90)
    assert compare(record(), [1, 1, 50, 90], annotation_format='penn_fudan').outcome == 'pass'


def test_request_is_only_original_pixels_and_identity_without_box_feedback_or_expected_answer():
    image = image_bytes()
    payload, metadata = prepare_feet_localization(image, 'Person in red clothing', pin())
    data = json.loads(payload['messages'][-1]['content'])
    assert set(data) == {'localization_id', 'image_size', 'target_description'}
    assert b64decode(payload['messages'][-1]['images'][0]) == image
    assert metadata['image_sha256'] == sha256(image).hexdigest()
    assert payload['truncate'] is False and payload['shift'] is False
    assert payload['options']['num_gpu'] == 0
    assert metadata['execution'].protocol == 'visible-feet-localization-v1'
    assert 'prompt_version' not in metadata['execution'].model_dump()


def test_ambiguous_duplicate_incomplete_and_inverted_region_responses_are_rejected():
    base = observation('a' * 64)
    bad_cases = []
    bad = deepcopy(base)
    bad['regions'].pop()
    bad_cases.append(bad)
    bad = deepcopy(base)
    bad['regions'][1]['region_id'] = 'leg_1'
    bad_cases.append(bad)
    bad = deepcopy(base)
    bad['regions'][1] = {**bad['regions'][0], 'region_id': 'leg_2'}
    bad_cases.append(bad)
    bad = deepcopy(base)
    bad['regions'][0]['x_min'] = {'low': 20, 'high': 19}
    bad_cases.append(bad)
    bad = deepcopy(base)
    bad['state'] = 'uncertain'
    bad_cases.append(bad)
    for data in bad_cases:
        with pytest.raises(ValidationError):
            FeetObservation.model_validate(data)


class ModelServer:
    def __init__(self, fault=None):
        self.fault = fault
        self.chats = []

    def handle(self, request):
        if request.url.path == '/api/version':
            return httpx.Response(200, json={'version': '0.33.3'})
        if request.url.path == '/api/tags':
            digest = 'f' * 64 if self.fault == 'model_changed' and self.chats else 'a' * 64
            return httpx.Response(200, json={'models': [{'name': 'test-vision', 'digest': digest}]})
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'capabilities': ['vision']})
        payload = json.loads(request.content)
        self.chats.append(payload)
        if self.fault == 'timeout':
            raise httpx.ReadTimeout('Synthetic timeout')
        doc = json.loads(payload['messages'][-1]['content'])
        data = observation(doc['localization_id'])
        if self.fault == 'foreign_identity':
            data['localization_id'] = 'f' * 64
        if self.fault == 'outside_image':
            data['regions'][0]['y_max'] = {'low': 101, 'high': 102}
        content = '{' if self.fault == 'invalid_json' else json.dumps(data)
        return httpx.Response(200, json={'model': 'test-vision', 'done': True,
            'done_reason': 'length' if self.fault == 'truncated' else 'stop',
            'prompt_eval_count': 1000, 'eval_count': 100,
            'message': {'role': 'assistant', 'content': content}})


def run_model(fault=None):
    server = ModelServer(fault)
    client = FeetLocalizationClient(pin(), transport=httpx.MockTransport(server.handle))
    try:
        result = client.localize(image_bytes(), 'Person in red clothing')
    finally:
        client.close()
    return result, server


def test_one_verified_model_call_can_supply_both_comparisons():
    source, server = run_model()
    assert source.model_verified and source.failure is None
    assert compare(source, [0, 0, 50, 90]).outcome == 'pass'
    assert compare(source, [0, 0, 50, 79]).outcome == 'fail'
    assert len(server.chats) == 1
    assert FeetLocalizationRecord.model_validate_json(source.model_dump_json()) == source


@pytest.mark.parametrize('fault', ['foreign_identity', 'outside_image', 'invalid_json', 'truncated', 'model_changed', 'timeout'])
def test_untrusted_or_failed_localizations_cannot_produce_a_verdict(fault):
    source, server = run_model(fault)
    assert not source.model_verified and source.observation is None and source.failure
    assert compare(source, [0, 0, 50, 79]).outcome == 'unavailable'
    assert len(server.chats) == 1  # no automatic retry


def test_input_and_context_limits_are_checked_before_inference():
    with pytest.raises(FeedbackError):
        prepare_feet_localization(image_bytes(), 'Person in red clothing', pin(num_ctx=1024))
    with pytest.raises(FeedbackError):
        prepare_feet_localization(b'not an image', 'Person in red clothing', pin())
    with pytest.raises(FeedbackError):
        prepare_feet_localization(image_bytes(), 'Person in red clothing', pin(max_pixels=10))


@pytest.mark.parametrize('format', ['PNG', 'JPEG'])
@pytest.mark.parametrize('orientation', [1, 6])
def test_image_verification_preserves_bytes_and_rejects_unmapped_exif_rotation(format, orientation):
    output = BytesIO()
    exif = Image.Exif()
    exif[274] = orientation
    Image.new('RGB', (100, 80), 'white').save(output, format=format, exif=exif)
    raw = output.getvalue()
    if orientation != 1:
        with pytest.raises(FeedbackError) as error:
            prepare_feet_localization(raw, 'Person in red clothing', pin())
        assert error.value.code == 'FEEDBACK_NOT_READY'
    else:
        payload, metadata = prepare_feet_localization(raw, 'Person in red clothing', pin())
        assert metadata['image_size'] == (100, 80)
        assert b64decode(payload['messages'][-1]['images'][0]) == raw


@pytest.mark.parametrize('format', ['PNG', 'JPEG'])
def test_truncated_image_is_rejected_before_inference(format):
    output = BytesIO()
    Image.new('RGB', (100, 100), 'white').save(output, format=format)
    raw = output.getvalue()
    with pytest.raises(FeedbackError) as error:
        prepare_feet_localization(raw[:len(raw) // 2], 'Person in red clothing', pin())
    assert error.value.code == 'FEEDBACK_NOT_READY'
