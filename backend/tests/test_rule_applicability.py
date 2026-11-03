"""Requirement applicability, provenance-based eligibility and per-target result combination.

Pure logic only: no model, database or filesystem. All annotations here are SYNTHETIC.
"""
import pytest

from backend.app.models.applicability import RuleApplicability
from backend.app.models.feedback import ObjectAssessment, TargetedPrediction
from backend.app.services.rule_applicability import (
    combine_results, derive_applicability, effective_applicability, keys_needing_review, object_geometry,
    resolve_scope)
from backend.tests.applicability_fixtures import IMAGE, assessment, visual_rule

CASE = 'c' * 64


def poly(key, object_id, label='person', *, origin='derived_from_polygon'):
    return {'source_key': key, 'object_id': object_id, 'label': label, 'bbox': [1, 1, 5, 5],
            'bbox_origin': origin, 'polygons': [[[1, 1], [5, 1], [5, 5]]]}


def rect(key, object_id, label='person'):
    return {'source_key': key, 'object_id': object_id, 'label': label, 'bbox': [1, 1, 5, 5],
            'bbox_origin': 'supplied', 'polygons': []}


def coco(key, object_id, label='person'):
    return {'source_key': key, 'object_id': object_id, 'label': label, 'bbox': [0, 0, 6, 6],
            'bbox_origin': 'supplied', 'polygons': [[[1, 1], [5, 1], [5, 5]]]}


def scope_for(text, objects, *, fmt='labelme', masks=None, **rule_kwargs):
    return resolve_scope(visual_rule('R', text, **rule_kwargs), objects, image=IMAGE, annotation_format=fmt, masks=masks)


def reply(scope, assessments, outcome='pass'):
    return TargetedPrediction(case_id=CASE, rule_id='R', outcome=outcome, reason='Synthetic.',
                              assessments=[ObjectAssessment(**item) for item in assessments],
                              evidence_references=['image'])


# ----------------------------------------------------------------------------- derivation

@pytest.mark.parametrize('text,geometry,combine,status', [
    ('The bounding box must include the visible head.', ['box'], 'all', 'clear'),
    ('Boxes include the feet and lower legs of every visible person.', ['box'], 'all', 'clear'),
    ('The polygon must follow the visible outline of the person.', ['polygon'], 'all', 'clear'),
    ('Polygon parts of one object must all belong to the same person.', ['polygon'], 'all', 'clear'),
    ('Annotations of occluded people cover only the visible part of the person.', [], 'all', 'clear'),
    ('Annotation labels agree with the visible scene.', [], 'all', 'clear'),
    ('Every person must be annotated with a bounding box or a polygon.', ['box', 'polygon'], 'any', 'clear'),
    ('The polygon must stay inside its bounding box.', ['polygon', 'box'], 'all', 'clear'),
    ('The mask must cover the whole visible person.', ['mask'], 'all', 'clear'),
    ('The box includes the head and the polygon follows the person.', ['box', 'polygon'], 'any', 'ambiguous'),
    ('Annotations other than boxes must follow the visible person.', [], 'any', 'ambiguous'),
])
def test_applicability_follows_the_role_of_each_geometry_term(text, geometry, combine, status):
    result = derive_applicability(text)
    assert (result.geometry, result.combine, result.status) == (geometry, combine, status)
    assert result.source == 'derived-v1' and result.basis


def test_a_geometry_word_inside_a_definition_does_not_make_a_requirement_geometry_specific():
    assert derive_applicability('A box means the rectangle that encloses the visible person.').geometry == []
    mixed = 'Annotation means a polygon or a box. Each annotation must label the visible class.'
    assert derive_applicability(mixed).geometry == []


def test_geometry_words_in_context_sentences_never_change_applicability():
    rule = visual_rule('R', 'Annotation labels agree with the visible scene.', context=[
        'Scope: this guideline covers bounding boxes, polygons and masks.'])
    applicability, origin = effective_applicability(rule)
    assert applicability.geometry == [] and origin == 'derived_at_audit'


def test_a_reviewer_set_applicability_is_authoritative_and_recorded_as_the_rules_own():
    explicit = RuleApplicability(geometry=['polygon'], source='reviewer', basis='Reviewer decided.')
    rule = visual_rule('R', 'The box and polygon both follow the person.', applicability=explicit)
    applicability, origin = effective_applicability(rule)
    assert applicability == explicit and origin == 'rule'
    with pytest.raises(ValueError):
        RuleApplicability(geometry=['box'], source='reviewer', status='ambiguous', basis='x')


# ------------------------------------------------------------------------- provenance

def test_a_polygon_derived_box_is_not_a_supplied_box():
    assert object_geometry(poly('/shapes/0', 1), annotation_format='labelme').box == 'derived_from_polygon'
    assert object_geometry(rect('/shapes/0', 1), annotation_format='labelme').box == 'supplied'
    assert object_geometry(coco('/annotations/0', 1), annotation_format='coco').polygon == 'supplied'
    penn = {'object_id': 1, 'label': 'PASpersonWalking', 'bbox': [1, 2, 3, 4]}
    assert object_geometry(penn, annotation_format='penn_fudan').box == 'supplied'


def test_polygon_only_labelme_never_runs_box_requirements_against_derived_boxes():
    objects = [poly('/shapes/0', 1), poly('/shapes/1', 2)]
    scope = scope_for('The bounding box must include the visible head.', objects)
    assert scope.eligible == []
    assert scope.unavailable.code == 'supplied_box_not_available'
    assert 'derived from polygons' in scope.unavailable.detail
    assert [item.object_key for item in scope.ineligible] == ['/shapes/0', '/shapes/1']
    assert all(item.missing == ['box'] and 'derived from its polygon' in item.detail for item in scope.ineligible)


def test_labelme_rectangles_are_supplied_boxes_and_can_be_checked():
    scope = scope_for('The bounding box must include the visible head.', [rect('/shapes/0', 1)])
    assert [item.object_key for item in scope.eligible] == ['/shapes/0'] and scope.unavailable is None


def test_mixed_objects_evaluate_the_eligible_and_report_the_rest():
    objects = [poly('/shapes/0', 1), rect('/shapes/1', 2), poly('/shapes/2', 3)]
    scope = scope_for('The bounding box must include the visible head.', objects)
    assert [item.object_key for item in scope.eligible] == ['/shapes/1']
    assert [item.object_key for item in scope.ineligible] == ['/shapes/0', '/shapes/2']
    assert scope.unavailable is None


def test_coco_with_supplied_boxes_and_polygons_uses_the_geometry_each_requirement_names():
    objects = [coco('/annotations/0', 1), coco('/annotations/1', 2)]
    box = scope_for('The bounding box must include the visible head.', objects, fmt='coco')
    polygon = scope_for('The polygon must follow the visible person.', objects, fmt='coco')
    both = scope_for('The polygon must stay inside its bounding box.', objects, fmt='coco')
    assert len(box.eligible) == len(polygon.eligible) == len(both.eligible) == 2
    assert box.applicability.geometry == ['box'] and polygon.applicability.geometry == ['polygon']
    assert both.applicability.combine == 'all'


def test_compound_requirements_need_every_geometry_and_alternatives_need_one():
    objects = [coco('/annotations/0', 1), poly('/shapes/1', 2), rect('/shapes/2', 3)]
    compound = scope_for('The polygon must stay inside its bounding box.', objects, fmt='coco')
    assert [i.object_key for i in compound.eligible] == ['/annotations/0']
    assert {i.object_key: i.missing for i in compound.ineligible} == {'/shapes/1': ['box'], '/shapes/2': ['polygon']}
    alternative = scope_for('Every person must be annotated with a bounding box or a polygon.', objects, fmt='coco')
    assert len(alternative.eligible) == 3


def test_a_requirement_without_geometry_applies_to_every_annotation():
    scope = scope_for('Annotation labels agree with the visible scene.', [poly('/shapes/0', 1), rect('/shapes/1', 2)])
    assert len(scope.eligible) == 2 and scope.ineligible == []


@pytest.mark.parametrize('mask,code', [('absent', 'mask_not_supplied'),
                                       ('referenced_unavailable', 'mask_referenced_unavailable'),
                                       ('unsupported_encoding', 'mask_encoding_unsupported'),
                                       ('supplied_not_shown', 'mask_not_visible_to_visual_check')])
def test_mask_requirements_distinguish_never_supplied_unsupported_and_not_shown(mask, code):
    scope = scope_for('The mask must cover the whole visible person.', [poly('/shapes/0', 1)],
                      masks={'/shapes/0': mask})
    assert scope.eligible == [] and scope.unavailable.code == code


def test_a_supplied_mask_does_not_make_a_box_requirement_applicable():
    penn = {'object_id': 1, 'label': 'person', 'bbox': [1, 2, 9, 10]}
    scope = scope_for('The polygon must follow the visible person.', [penn], fmt='penn_fudan',
                      masks={'penn_fudan:1': 'supplied_not_shown'})
    assert scope.eligible == [] and scope.unavailable.code == 'no_eligible_annotation'


def test_a_numeric_tolerance_is_never_judged_visually_even_for_a_box_rule():
    scope = scope_for('Boxes may extend at most 3 pixels beyond the visible person.', [rect('/shapes/0', 1)])
    assert scope.eligible == [] and scope.unavailable.code == 'measurement_not_supported'
    assert 'cannot measure' in scope.unavailable.detail and scope.ineligible[0].object_key == '/shapes/0'


def test_a_sample_without_annotations_is_unavailable_not_passing():
    assert scope_for('Annotation labels agree with the visible scene.', []).unavailable.code == 'no_annotations'


# ------------------------------------------------------------------------ combination

def man_woman():
    objects = [poly('/shapes/0', 1), poly('/shapes/1', 2)]
    return scope_for('The polygon must follow the visible person.', objects)


def test_a_finding_about_the_man_does_not_become_a_finding_about_the_woman():
    scope = man_woman()
    outcome, reason, result = combine_results(scope, reply(scope, [
        assessment('/shapes/0', 'fail', reason='Polygon lies right of the man.'),
        assessment('/shapes/1', 'pass', reason='Polygon follows the woman.')], outcome='fail'))
    assert outcome == 'fail'
    by_key = {r.object_key: r.outcome for r in result.results}
    assert by_key == {'/shapes/0': 'fail', '/shapes/1': 'pass'}
    assert keys_needing_review(result) == ['/shapes/0']
    assert '/shapes/0: fail' in reason and '/shapes/1: pass' in reason


def test_the_per_target_results_decide_the_outcome_not_the_models_summary():
    scope = man_woman()
    outcome, _, result = combine_results(scope, reply(scope, [assessment('/shapes/0', 'fail'),
                                                              assessment('/shapes/1', 'pass')], outcome='pass'))
    assert outcome == 'fail' and result.model_reported_outcome == 'pass'
    assert result.outcome_source == 'derived_from_target_results' and result.notes
    outcome, *_ = combine_results(scope, reply(scope, [assessment('/shapes/0'), assessment('/shapes/1')], outcome='fail'))
    assert outcome == 'pass'


def test_unknown_context_and_repeated_references_are_rejected_not_counted():
    objects = [rect('/shapes/0', 1), poly('/shapes/1', 2)]
    scope = scope_for('The bounding box must include the visible head.', objects)
    _, _, result = combine_results(scope, reply(scope, [
        assessment('/shapes/0', 'pass', geometry='box'), assessment('/shapes/1', 'fail', geometry='box'),
        assessment('/shapes/9', 'fail', geometry='box'), assessment('/shapes/0', 'fail', geometry='box')]))
    assert [r.outcome for r in result.results] == ['unsure']
    assert len(result.rejected_references) == 3
    assert any('context annotation' in item for item in result.rejected_references)


def test_a_target_the_model_did_not_assess_makes_the_result_unsure_never_pass():
    scope = man_woman()
    outcome, _, result = combine_results(scope, reply(scope, [assessment('/shapes/0', 'pass')], outcome='pass'))
    assert outcome == 'unsure'
    assert {r.object_key: r.flags for r in result.results}['/shapes/1'] == ['no_assessment']


def test_an_assessment_of_geometry_that_was_never_supplied_cannot_fail_or_pass():
    scope = scope_for('Annotations follow the visible person.', [poly('/shapes/0', 1)])
    outcome, _, result = combine_results(scope, reply(scope, [
        assessment('/shapes/0', 'fail', geometry='box', reason='The box misses the feet.')], outcome='fail'))
    assert outcome == 'unsure' and 'geometry_not_supplied' in result.results[0].flags
    assert result.results[0].model_outcome == 'fail'


def test_a_failure_that_blames_an_unsupplied_bounding_box_is_downgraded_unless_it_says_derived():
    scope = scope_for('Annotations follow the visible person.', [poly('/shapes/0', 1)])
    outcome, _, result = combine_results(scope, reply(scope, [
        assessment('/shapes/0', 'fail', reason='The bounding box does not reach the feet.')]))
    assert outcome == 'unsure' and 'reason_cites_unsupplied_box' in result.results[0].flags
    outcome, *_ = combine_results(scope, reply(scope, [
        assessment('/shapes/0', 'fail', reason='The polygon, and the box derived from it, stop above the feet.')]))
    assert outcome == 'fail'


def test_blur_is_not_accepted_as_evidence_that_hidden_anatomy_should_be_annotated():
    scope = scope_for('Annotations follow the visible person.', [poly('/shapes/0', 1)])
    outcome, _, result = combine_results(scope, reply(scope, [assessment(
        '/shapes/0', 'fail', reason='The legs are blurry and hidden, so the polygon should include them.')]))
    assert outcome == 'unsure' and 'blur_offered_as_evidence' in result.results[0].flags


def test_judging_geometry_the_requirement_does_not_name_is_downgraded():
    scope = scope_for('The polygon must follow the visible person.', [coco('/annotations/0', 1)], fmt='coco')
    outcome, _, result = combine_results(scope, reply(scope, [assessment('/annotations/0', 'fail', geometry='box')]))
    assert outcome == 'unsure' and 'geometry_not_in_requirement' in result.results[0].flags


# ----------------------------------------------------- conditions and uncertainty

def occlusion():
    return scope_for('Annotations of occluded people cover only the visible part of the person.',
                     [poly('/shapes/0', 1), poly('/shapes/1', 2)])


def test_an_absent_condition_excludes_that_target_and_the_rest_decide():
    scope = occlusion()
    outcome, _, result = combine_results(scope, reply(scope, [
        assessment('/shapes/0', 'pass', condition='not_present'),
        assessment('/shapes/1', 'pass', condition='applies')]))
    assert outcome == 'pass'
    assert 'condition_not_present' in {r.object_key: r.flags for r in result.results}['/shapes/0']


def test_when_no_target_meets_the_condition_the_result_is_unavailable_not_a_pass():
    scope = occlusion()
    outcome, _, result = combine_results(scope, reply(scope, [
        assessment('/shapes/0', 'pass', condition='not_present'),
        assessment('/shapes/1', 'pass', condition='not_present')]))
    assert outcome == 'unavailable' and result.unavailable.code == 'condition_not_present'


def test_an_unclear_condition_propagates_as_unsure_even_when_the_model_said_fail_or_pass():
    scope = occlusion()
    outcome, _, result = combine_results(scope, reply(scope, [
        assessment('/shapes/0', 'fail', condition='unclear'), assessment('/shapes/1', 'pass')]))
    assert outcome == 'unsure'
    assert {r.object_key: r.outcome for r in result.results}['/shapes/0'] == 'unsure'


def test_a_model_that_can_establish_nothing_yields_unavailable():
    scope = man_woman()
    outcome, _, result = combine_results(scope, reply(scope, [assessment('/shapes/0', 'unavailable'),
                                                              assessment('/shapes/1', 'unavailable')]))
    assert outcome == 'unavailable' and result.unavailable.code == 'no_valid_assessment'


@pytest.mark.parametrize('other,expected', [('pass', 'unavailable'), ('fail', 'fail'), ('unsure', 'unsure')])
def test_an_unavailable_applicable_target_cannot_be_hidden_by_a_passing_target(other, expected):
    scope = man_woman()
    outcome, _, result = combine_results(scope, reply(scope, [
        assessment('/shapes/0', other), assessment('/shapes/1', 'unavailable')]))
    assert outcome == expected
    assert result.results[1].outcome == 'unavailable'


def test_a_long_explanation_is_bounded_but_full_results_stay_in_the_record():
    scope = man_woman()
    long = 'x' * 300
    _, reason, result = combine_results(scope, reply(scope, [assessment('/shapes/0', 'fail', reason=long),
                                                             assessment('/shapes/1', 'fail', reason=long)]))
    assert len(reason) <= 1024 and all(r.reason == long for r in result.results)


# ------------------------------------------------------------ derived-v2 (prompt v6 only)

RULE_5_TEXT = ('For a person who is partly hidden by another object or by the image edge, annotate only the visible portions, '
               'and do not extend boxes or polygons to cover body parts that cannot be seen.')


def test_derived_v1_misreads_a_prohibition_as_an_excluded_geometry_and_v2_does_not():
    v1, v2 = derive_applicability(RULE_5_TEXT), derive_applicability(RULE_5_TEXT, 'derived-v2')
    # v1 is frozen (stored rules and v5 audits depend on it): it drops boxes and reports ambiguity.
    assert (v1.geometry, v1.status, v1.source) == (['polygon'], 'ambiguous', 'derived-v1')
    # v2: "do not extend boxes or polygons" prohibits an action; it does not exclude a geometry type.
    assert (v2.geometry, v2.combine, v2.status, v2.source) == (['box', 'polygon'], 'any', 'clear', 'derived-v2')


@pytest.mark.parametrize('text,geometry,status', [
    ('Annotations other than boxes must follow the visible person.', [], 'ambiguous'),
    ('Do not draw a box without a polygon.', ['box'], 'ambiguous'),
    ('Use no masks for people.', [], 'ambiguous'),
    ('The polygon must not extend outside the image.', ['polygon'], 'clear'),
    ('The bounding box includes the visible head.', ['box'], 'clear'),
    ('The polygon must stay inside its bounding box.', ['polygon', 'box'], 'clear'),
])
def test_derived_v2_still_flags_real_exclusions_and_keeps_every_other_result(text, geometry, status):
    result = derive_applicability(text, 'derived-v2')
    assert (result.geometry, result.status) == (geometry, status)


def test_an_unknown_algorithm_is_rejected_and_a_stored_applicability_is_never_rewritten():
    with pytest.raises(ValueError):
        derive_applicability('The box includes the head.', 'derived-v9')
    stored = RuleApplicability(geometry=['polygon'], source='derived-v1', status='ambiguous', basis='Stored by extraction.')
    rule = visual_rule('R', RULE_5_TEXT, applicability=stored)
    for algorithm in ('derived-v1', 'derived-v2'):
        applicability, origin = effective_applicability(rule, algorithm)
        assert applicability == stored and origin == 'rule'


def test_resolve_scope_uses_the_requested_algorithm_so_a_rectangle_only_sample_keeps_the_occlusion_rule():
    objects = [rect('/shapes/0', 1)]
    assert scope_for(RULE_5_TEXT, objects).unavailable is not None                      # v1: polygon only -> ineligible
    v2 = resolve_scope(visual_rule('R', RULE_5_TEXT), objects, image=IMAGE, annotation_format='labelme', algorithm='derived-v2')
    assert [o.object_key for o in v2.eligible] == ['/shapes/0'] and v2.unavailable is None
