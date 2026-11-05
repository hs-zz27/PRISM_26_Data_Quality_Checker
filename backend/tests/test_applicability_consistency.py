"""Regression coverage for the 4 November extraction and false-pass findings.

Synthetic geometry and scripted replies verify mechanics, not visual accuracy.
The saved live result omitted raw compliance, so every possible value is tested.
"""
import hashlib
import json

import pytest

from backend.app.core.feedback import load_execution_pin
from backend.app.inference.rule_proposals import expand_response, source_units
from backend.app.models.feedback import ExecutionPin, FocusedAssessment
from backend.app.services.focused_prompt import FOCUS_IMAGE_COUNT, focused_system
from backend.app.services.prompt_encoding import compact_schema, text_fits
from backend.app.services.rule_applicability import FocusedCall, combine_focused, effective_applicability
from backend.tests.applicability_fixtures import requires_importer, v6_pin
from backend.tests.test_guideline_context import VISUAL, respond
from backend.tests.test_rule_applicability import coco, man_woman, scope_for
from backend.tests.test_focused_protocol import FIT, focused, predict
from backend.tests.test_visual_feedback_recheck import feedback


OCCLUSION = ('For a person partly hidden by another object or the image edge, annotate only visible '
             'portions and do not extend boxes or polygons over body parts that cannot be seen.')


def v7_pin(**kwargs):
    return ExecutionPin.model_validate({**v6_pin(**kwargs).model_dump(), 'prompt_version': 'annotation-guideline-v7'})


def call(key, condition='applies', compliance='yes', geometry='polygon', reason='Visible boundary matches.'):
    return FocusedCall(key, assessment=FocusedAssessment(
        case_id='a' * 64, rule_id='R', object_key=key, condition=condition,
        compliance=compliance, geometry=geometry, reason=reason, evidence_references=['image', 'annotations']))


def test_new_extraction_keeps_both_geometries_without_changing_source_or_context():
    context = 'Report unclear evidence as uncertain for human review.'
    sources = source_units(OCCLUSION + '\n' + context)
    [rule] = expand_response(respond({1: 'requirement', 2: 'context'}, [(1, VISUAL, {}, [])]), sources)
    assert rule.applicability.source == 'derived-v2'
    assert rule.applicability.geometry == ['box', 'polygon']
    assert rule.applicability.status == 'clear' and rule.applicability.combine == 'any'
    assert not rule.extraction_notes
    assert rule.description == rule.source_sentence == OCCLUSION
    assert rule.context_sentences == [context]
    # Once saved, applicability is not reinterpreted by an audit's algorithm.
    assert effective_applicability(rule, 'derived-v1') == (rule.applicability, 'rule')


@pytest.mark.parametrize('text', [
    'The rule applies only to polygons, not bounding boxes.',
    'Annotations other than boxes must follow the visible person.',
    'Do not draw a box without a polygon.',
    'The box includes the head and the polygon follows the person.',
])
def test_exclusions_and_unresolved_geometry_are_not_silently_confirmed(text):
    [rule] = expand_response(respond({1: 'requirement'}, [(1, VISUAL, {}, [])]), [text])
    assert rule.applicability.status == 'ambiguous'
    assert rule.extraction_notes and rule.source_sentence == text


@pytest.mark.parametrize('compliance', ['yes', 'no', 'unclear'])
def test_reported_exclusion_cannot_turn_the_displaced_target_into_a_clean_sample_pass(compliance):
    scope = man_woman()
    calls = [call('/shapes/0', condition='not_present', compliance=compliance,
                  reason='The target polygon is displaced to the right, enclosing background foliage.'),
             call('/shapes/1')]
    before = scope.model_dump(mode='json')
    # Reproduce the old false-pass mechanism regardless of the discarded field.
    assert combine_focused(scope, calls)[0] == 'pass'
    outcome, reason, result = combine_focused(scope, calls, consistent=True)
    assert outcome == 'unsure'
    assert result.results[0].model_compliance == compliance
    assert result.results[0].condition == 'not_present'
    assert result.results[0].reason == calls[0].assessment.reason
    assert result.results[1].outcome == 'pass'
    assert 'review' in (reason + ' '.join(result.notes)).lower()
    assert scope.model_dump(mode='json') == before
    if compliance != 'unclear':
        assert result.results[0].outcome == 'unsure'
        assert result.results[0].flags == ['condition_compliance_conflict']
    else:
        assert result.results[0].flags == ['condition_not_present']


def test_real_class_exclusion_remains_excluded_without_an_annotation_failure():
    calls = [call('/shapes/0'), call('/shapes/1', condition='not_present', compliance='unclear',
                                   reason='The target is a skateboard; the rule applies only to people.')]
    outcome, _, scope = combine_focused(man_woman(), calls, consistent=True)
    assert outcome == 'unsure'  # reviewer checks the model-only exemption
    assert scope.results[1].outcome == 'unavailable'
    assert scope.results[1].flags == ['condition_not_present']
    assert all(row.outcome != 'fail' for row in scope.results)


@pytest.mark.parametrize('other,expected', [('yes', 'unsure'), ('no', 'fail'), ('unclear', 'unsure')])
def test_conflict_does_not_hide_a_valid_failure_elsewhere(other, expected):
    outcome, _, scope = combine_focused(man_woman(), [
        call('/shapes/0', condition='not_present', compliance='no'), call('/shapes/1', compliance=other)
    ], consistent=True)
    assert outcome == expected
    assert scope.results[0].outcome == 'unsure'


def test_all_valid_exclusions_are_unavailable_and_unclear_condition_is_unsure():
    calls = [call(key, condition='not_present', compliance='unclear') for key in ('/shapes/0', '/shapes/1')]
    assert combine_focused(man_woman(), calls, consistent=True)[0] == 'unavailable'
    calls[0] = call('/shapes/0', condition='unclear', compliance='unclear')
    assert combine_focused(man_woman(), calls, consistent=True)[0] == 'unsure'


@pytest.mark.parametrize('calls', [
    [call('/shapes/0'), FocusedCall('/shapes/1', failure='inference_unavailable')],
    [call('/shapes/0')],
    [call('/shapes/0'), FocusedCall('/shapes/1', answered_for='/shapes/99')],
])
def test_missing_invalid_or_failed_target_cannot_be_a_pass(calls):
    assert combine_focused(man_woman(), calls, consistent=True)[0] in ('unsure', 'unavailable')


def test_single_geometry_pass_and_failure_still_work():
    assert combine_focused(man_woman(), [call('/shapes/0'), call('/shapes/1')], consistent=True)[0] == 'pass'
    assert combine_focused(man_woman(), [call('/shapes/0', compliance='no'), call('/shapes/1')], consistent=True)[0] == 'fail'


@pytest.mark.parametrize('requirement', [
    'The supplied box or polygon follows the visible person.',
    'The supplied polygon must stay inside its bounding box.',
])
def test_one_geometry_pass_cannot_claim_coverage_of_both_supplied_geometries(requirement):
    scope = scope_for(requirement, [coco('/annotations/0', 1)], fmt='coco')
    calls = [call('/annotations/0')]
    assert combine_focused(scope, calls)[0] == 'pass'  # v6 replay preserved
    outcome, _, result = combine_focused(scope, calls, consistent=True)
    assert outcome == 'unsure'
    assert 'geometry_coverage_incomplete' in result.results[0].flags


def test_ambiguous_applicability_cannot_establish_a_clean_pass():
    scope = scope_for('The box includes the head and the polygon follows the person.',
                      [coco('/annotations/0', 1)], fmt='coco')
    assert scope.applicability.status == 'ambiguous'
    outcome, _, result = combine_focused(scope, [call('/annotations/0')], consistent=True)
    assert outcome == 'unsure' and 'applicability_ambiguous' in result.results[0].flags


def test_v6_prompt_and_serialized_results_stay_unchanged():
    assert hashlib.sha256(focused_system().encode()).hexdigest() == \
        '2cb8b6cf46b092c71a10ecb475cbb72fbfd99dc1888b8ebeffa44b3cefecf154'
    _, _, old = combine_focused(man_woman(), [call('/shapes/0'), call('/shapes/1')])
    assert all('model_compliance' not in r.model_dump(mode='json') for r in old.results)


@pytest.mark.parametrize('extra_feedback_bytes', [0, 500])
def test_v7_fits_the_recorded_full_rule_context_without_reducing_reservations(extra_feedback_bytes):
    assert text_fits(focused_system(consistent=True), 'x' * (5519 + extra_feedback_bytes),
                     compact_schema(FocusedAssessment.model_json_schema()),
                     v7_pin(num_ctx=16384, num_predict=2048), FOCUS_IMAGE_COUNT)


def test_v7_is_opt_in_and_requires_focused_preprocessing(monkeypatch):
    monkeypatch.setenv('FEEDBACK_PROMPT_VERSION', 'annotation-guideline-v7')
    monkeypatch.setenv('FEEDBACK_MODEL_DIGEST', 'sha256:' + 'a' * 64)
    execution = load_execution_pin()
    assert execution.prompt_version == 'annotation-guideline-v7'
    assert execution.preprocessing == 'target-focus-overlay-v1'
    with pytest.raises(ValueError):
        ExecutionPin.model_validate({**execution.model_dump(), 'preprocessing': 'original-and-annotation-overlay-v1'})


@requires_importer
def test_v7_real_client_path_applies_guard_and_preserves_feedback_scope(tmp_path):
    def answer(document):
        if document['focus']['object_key'] == '/shapes/0':
            return focused('no', condition='not_present', reason='The polygon is displaced into background.')
        return focused()
    result, server, _ = predict(tmp_path, answer, FIT, pin=v7_pin(),
                                feedback=feedback(object_keys=['/shapes/0']))
    assert result.model_verified and result.prediction.outcome == 'unsure'
    assert server.chat_count == 2
    assert result.scope.results[0].model_compliance == 'no'
    for chat in server.chats:
        document = json.loads(chat['messages'][-1]['content'])
        assert ('reviewer_feedback' in document) == (document['focus']['object_key'] == '/shapes/0')
        assert len([m for m in chat['messages'] if m.get('images')]) == 4
        assert 'compliance MUST be unclear' in chat['messages'][0]['content']
