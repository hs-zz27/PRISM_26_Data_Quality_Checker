"""Offline feedback regressions; only synthetic pixels and mocked model transport."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest
from pydantic import ValidationError

from backend.app.core.feedback import FeedbackError
from backend.app.models.enclosure import EnclosureEvidence
from backend.app.models.correction import CorrectionObject
from backend.app.models.finding import FindingReview
from backend.app.models.feedback import (
    CaseSnapshot, EvaluationInput, ExecutionPin, FrozenSet, RulePin,
    fingerprint,
)
from backend.app.services.enclosure_feedback import (
    EnclosureFeedbackClient, _views, build_feedback_request, example_views,
    validate_prediction,
)
from backend.app.services.corrections import annotation_bytes
from backend.app.services.enclosure_prompt import build_request
from backend.tests.feedback_fixtures import NOW, assets, case, inputs
from backend.tests.test_enclosure_core import pin
from backend.tests.test_enclosure_integration import FakeOllama


def enclosure_case(tmp_path, name='training', *, outcome='pass', approved=True, override=False, box_count=1,
                   execution_pin=None):
    original = case(tmp_path, name)
    if box_count != 1:
        annotation = annotation_bytes([CorrectionObject(object_id=i+1, label='person', bbox=(1,1,4,3))
            for i in range(box_count)], (4,3), 3, 4096)
        entry = original.manifest.annotation_text
        (assets(tmp_path).root / entry.storage_key).write_bytes(annotation)
        original.manifest = original.manifest.model_copy(update={'annotation_text': entry.model_copy(
            update={'size_bytes': len(annotation), 'sha256': sha256(annotation).hexdigest()})})
    document = original.target.rule.model_dump(mode='python')
    document['check_name'] = 'pedestrian_box_enclosure'
    target = RulePin(rulebook_id=original.target.rulebook_id, rulebook_version=1,
                     rule=type(original.target.rule).model_validate(document))
    execution = ExecutionPin.for_enclosure(execution_pin or pin())
    fake = FakeOllama(decide=lambda _: (outcome, 'Synthetic framed target.'), digest='ee' * 32)
    result = EnclosureFeedbackClient(execution, transport=fake.transport()).predict(original.manifest, target, [], assets(tmp_path))
    values = original.model_dump(mode='python') | {'target': target,
        'expected': ('fail' if outcome == 'pass' else 'pass') if override else outcome,
        'reason': 'Human checked only the framed target.'}
    if approved:
        values.update(finding_id='finding_' + name,
            finding_review=FindingReview(revision=1, resolution='rejected' if override else 'accepted',
                reviewer_id='human', comment='Checked this single box.', updated_at=NOW),
            model_execution=execution, model_proposal=result)
    return CaseSnapshot.model_validate(values)


def enclosure_inputs(tmp_path):
    training = enclosure_case(tmp_path, outcome='pass', override=True)
    target = enclosure_case(tmp_path, 'heldout', outcome='pass', approved=False)
    return EvaluationInput(
        policy=FrozenSet(resource_id='policy_a', name='Reviewed one-box example', reviewer_id='human', created_at=NOW, cases=[training]),
        truth=FrozenSet(resource_id='truth_a', name='Separate truth', reviewer_id='human', created_at=NOW, cases=[target]),
        runtime=training.model_execution, requested_by='human')


def test_baseline_request_is_byte_identical_and_candidate_preserves_target_pixels(tmp_path):
    frozen = enclosure_inputs(tmp_path)
    case = frozen.truth.cases[0]
    pin = frozen.runtime.enclosure
    [view] = _views(case.manifest, pin, assets(tmp_path))
    rule = case.target.rule.model_dump(mode='json')
    expected = build_request(pin, rule, 'ab' * 32, view)
    baseline = build_feedback_request(pin, rule, 'ab' * 32, view, [])
    assert baseline.body == expected.body
    examples = example_views(case.manifest, case.target, frozen.policy.cases, pin, assets(tmp_path))
    candidate = build_feedback_request(pin, rule, 'ab' * 32, view, examples)
    assert candidate.payload['messages'][1]['images'][0] == baseline.payload['messages'][1]['images'][0]
    assert candidate.payload['format'] == baseline.payload['format']
    content = json.loads(candidate.payload['messages'][1]['content'])
    assert content['examples'][0]['expected'] == 'fail'  # Human override, not the old machine pass.
    assert 'expected' not in content['target']
    assert not any(key in content['box'] for key in ('bbox', 'source_window', 'target_window'))
    assert str(tmp_path) not in candidate.body.decode()
    assert len(candidate.payload['messages'][1]['images']) == 2


def test_both_comparison_arms_reuse_the_production_box_evaluator(tmp_path):
    frozen = enclosure_inputs(tmp_path)
    target = frozen.truth.cases[0]
    target.reason = 'SECRET_HELDOUT_REASON'
    fake = FakeOllama(decide=lambda content: ('fail' if content.get('examples') else 'pass', 'Synthetic result.'), digest='ee' * 32)
    client = EnclosureFeedbackClient(frozen.runtime, frozen.policy, transport=fake.transport())
    baseline = client.predict(target.manifest, target.target, [], assets(tmp_path))
    candidate = client.predict(target.manifest, target.target, frozen.policy.cases, assets(tmp_path))
    assert baseline.prediction.outcome == 'pass' and candidate.prediction.outcome == 'fail'
    assert baseline.enclosure.boxes[0].view_sha256 == candidate.enclosure.boxes[0].view_sha256
    assert candidate.enclosure.feedback_policy_sha256 == fingerprint(frozen.policy)
    assert len(fake.chat_bodies) == 2
    assert all('SECRET_HELDOUT_REASON' not in json.dumps(body) for body in fake.chat_bodies)
    validate_prediction(baseline, target.manifest, target.target, frozen.runtime)
    validate_prediction(candidate, target.manifest, target.target, frozen.runtime, frozen.policy)
    assert fake.max_in_flight == 1


def test_candidate_checks_every_target_box_and_aggregates_a_verified_failure(tmp_path):
    frozen = enclosure_inputs(tmp_path)
    target = enclosure_case(tmp_path, 'multiple', approved=False, box_count=2)
    fake = FakeOllama(decide=lambda content: (
        'fail' if content['box']['box_number'] == 2 else 'pass', 'Synthetic target result.'), digest='ee'*32)
    result = EnclosureFeedbackClient(frozen.runtime, frozen.policy, transport=fake.transport()).predict(
        target.manifest, target.target, frozen.policy.cases, assets(tmp_path))
    assert result.prediction.outcome == 'fail'
    assert [b.outcome for b in result.enclosure.boxes] == ['pass', 'fail']
    assert len(fake.chat_bodies) == 2
    assert all(len(body['messages'][1]['images']) == 2 for body in fake.chat_bodies)


def test_mutated_per_box_evidence_and_false_human_labels_are_rejected(tmp_path):
    original = enclosure_case(tmp_path)
    document = original.model_dump(mode='python')
    document['expected'] = 'fail'
    with pytest.raises(ValidationError):
        CaseSnapshot.model_validate(document)
    raw = original.model_proposal.enclosure.model_dump(mode='json')
    raw['aggregate']['outcome'] = 'fail'
    with pytest.raises(ValidationError):
        EnclosureEvidence.model_validate_json(json.dumps(raw))
    document = original.model_dump(mode='python')
    document['model_proposal']['enclosure']['case_id'] = '00' * 32
    with pytest.raises(ValidationError):
        CaseSnapshot.model_validate(document)


def test_a_sample_review_cannot_invent_per_box_labels_for_multiple_boxes(tmp_path):
    original = enclosure_case(tmp_path)
    document = original.model_dump(mode='python')
    evidence = document['model_proposal']['enclosure']
    second = deepcopy(evidence['boxes'][0]) | {'annotation_index': 1, 'box_number': 2}
    evidence['boxes'].append(second)
    evidence['boxes_intended'] = 2
    with pytest.raises(ValidationError, match='single-box'):
        CaseSnapshot.model_validate(document)


def test_an_override_needs_the_reviewers_reason(tmp_path):
    original = enclosure_case(tmp_path, override=True)
    document = original.model_dump(mode='python')
    document['finding_review']['comment'] = ''
    with pytest.raises(ValidationError, match='human reason'):
        CaseSnapshot.model_validate(document)


def test_rendered_example_must_match_its_frozen_view_hash(tmp_path):
    frozen = enclosure_inputs(tmp_path)
    document = frozen.policy.cases[0].model_dump(mode='python')
    document['model_proposal']['enclosure']['boxes'][0]['view_sha256'] = '00' * 32
    changed = CaseSnapshot.model_validate(document)
    target = frozen.truth.cases[0]
    with pytest.raises(FeedbackError):
        example_views(target.manifest, target.target, [changed], frozen.runtime.enclosure, assets(tmp_path))


def test_comparison_rejects_overlap_and_changed_execution(tmp_path):
    frozen = enclosure_inputs(tmp_path)
    document = frozen.model_dump(mode='python')
    document['truth']['cases'] = [frozen.policy.cases[0].model_dump(mode='python')]
    with pytest.raises(ValidationError):
        EvaluationInput.model_validate(document)
    document = frozen.model_dump(mode='python')
    document['runtime'] = ExecutionPin.for_enclosure(pin(num_thread=4)).model_dump(mode='python')
    with pytest.raises(ValidationError, match='execution pin'):
        EvaluationInput.model_validate(document)


def test_complete_example_context_is_rejected_instead_of_truncated(tmp_path):
    from backend.app.services.enclosure_prompt import ContextNotRepresentable
    frozen = enclosure_inputs(tmp_path)
    target = frozen.truth.cases[0]
    pin = frozen.runtime.enclosure
    [view] = _views(target.manifest, pin, assets(tmp_path))
    examples = example_views(target.manifest, target.target, frozen.policy.cases, pin, assets(tmp_path))
    baseline = build_request(pin, target.target.rule.model_dump(mode='json'), 'ab' * 32, view)
    tiny = pin.model_copy(update={'max_request_bytes': len(baseline.body) + 1})
    with pytest.raises(ContextNotRepresentable):
        build_feedback_request(tiny, target.target.rule.model_dump(mode='json'), 'ab' * 32, view, examples)


def test_bad_identity_remains_unavailable_with_full_box_provenance(tmp_path):
    frozen = enclosure_inputs(tmp_path)
    target = frozen.truth.cases[0]
    def corrupt(_, reply, request):
        body = json.loads(reply['message']['content'])
        body['box_number'] = 2
        reply['message']['content'] = json.dumps(body)
        return reply
    fake = FakeOllama(behavior=corrupt, digest='ee' * 32)
    result = EnclosureFeedbackClient(frozen.runtime, frozen.policy, transport=fake.transport()).predict(
        target.manifest, target.target, frozen.policy.cases, assets(tmp_path))
    assert result.prediction.outcome == 'unavailable'
    assert not result.model_verified
    assert result.enclosure.boxes[0].reason == 'identity_mismatch'


def test_new_optional_provenance_does_not_change_legacy_fingerprints(tmp_path):
    original = inputs(tmp_path)
    document = original.model_dump(mode='json')
    assert 'enclosure' not in document['runtime']
    assert 'enclosure' not in document['policy']['cases'][0]['model_proposal']
    restored = EvaluationInput.model_validate_json(json.dumps(document))
    assert fingerprint(restored) == fingerprint(document)
