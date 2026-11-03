"""Report categories for compare_visual_audit.py. Pure logic on constructed rows; no model, no importer.

The first test reproduces the shape of a real 4B v5 result: the model assessed the shifted man and omitted the
unchanged woman, which the backend records as ``no_assessment``. That is a coverage gap, never an accusation.
"""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('comparison_report', Path(__file__).resolve().parents[1] / 'tools' / 'comparison_report.py')
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def result(key, outcome, flags=(), **extra):
    return {'object_key': key, 'outcome': outcome, 'flags': list(flags), **extra}


def row(rule, outcome='pass', results=(), verified=True, scope_extra=None, reason='r', calls=()):
    scope = {'results': list(results), 'rejected_references': [], 'unavailable': None,
             'outcome_source': 'derived_from_target_results', 'eligible': [{'object_key': r['object_key']} for r in results],
             **(scope_extra or {})}
    return {'rule_id': rule, 'outcome': outcome, 'reason': reason, 'model_verified': verified, 'duration_ms': 1000.0,
            'scope': scope, 'visual_assessment': None, 'calls': list(calls)}


def skipped(rule='RULE-2', code='supplied_box_not_available'):
    return {'rule_id': rule, 'outcome': 'unavailable', 'reason': 'No supplied box.', 'model_verified': False,
            'duration_ms': 0.0, 'calls': [], 'visual_assessment': None,
            'scope': {'results': [], 'eligible': [], 'rejected_references': [], 'outcome_source': 'no_model_call',
                      'unavailable': {'code': code, 'detail': 'x'}}}


def test_an_omitted_assessment_is_a_missing_assessment_not_a_failure_or_a_false_accusation():
    altered = row('RULE-7', 'fail', [result('/shapes/0', 'fail', model_outcome='fail'),
                                     result('/shapes/1', 'unsure', ['no_assessment'])])
    report.add_categories([altered])
    summary = report.summarize({'baseline': [], 'altered': [altered]})['altered']
    assert [x['object_key'] for x in summary['explicit_failure']] == ['/shapes/0']
    assert [x['object_key'] for x in summary['missing_assessment']] == ['/shapes/1']
    assert summary['uncertain'] == [] and summary['counts']['missing_assessment'] == 1
    legacy = report.legacy_evaluation({'v5': {'baseline': [], 'altered': [altered]}}, 'RULE-7', '/shapes/0')['v5']
    assert legacy['altered_explicit_failures_polygon_fit'] == ['/shapes/0']
    assert legacy['altered_missing_assessments_polygon_fit'] == ['/shapes/1']
    assert legacy['altered_unchanged_annotation_explicitly_failed'] == []
    # The deprecated combined fields keep their original meaning (fail + unsure) for existing consumers.
    assert legacy['altered_flagged_annotations_polygon_fit'] == ['/shapes/0', '/shapes/1']
    assert legacy['altered_unchanged_annotation_flagged'] == ['/shapes/1']


def test_every_target_category_is_distinct():
    cases = {
        'explicit_pass': result('a', 'pass'), 'explicit_failure': result('a', 'fail'),
        'uncertain': result('a', 'unsure', ['condition_unclear']),
        'missing_assessment': result('a', 'unsure', ['no_assessment', 'reference_mismatch']),
        'unavailable': result('a', 'unavailable'),
        'execution_failure': result('a', 'unavailable', ['call_failed', 'call:invalid_proposal']),
        'condition_not_present': result('a', 'unavailable', ['condition_not_present'])}
    for category, item in cases.items():
        assert report.classify_target(item)['category'] == category
    assert report.classify_target(cases['execution_failure'])['code'] == 'invalid_proposal'


def test_a_grounding_downgrade_is_uncertain_and_records_what_was_not_trusted():
    downgraded = report.classify_target(result('a', 'unsure', ['geometry_not_supplied'], model_outcome='fail'))
    assert downgraded['category'] == 'uncertain' and downgraded['downgraded_from'] == 'fail'


def test_an_eligibility_skip_is_kept_apart_from_an_execution_failure():
    skip = skipped()
    failed = row('RULE-7', 'unavailable', [], verified=False, reason='inference_unavailable',
                 scope_extra={'outcome_source': 'no_model_call'})
    bounded = skipped('RULE-5', 'too_many_targets')               # a limit, not an eligibility reason
    assert report.categorize_row(skip)['status'] == 'eligibility_skip'
    assert report.categorize_row(skip)['code'] == 'supplied_box_not_available'
    info = report.categorize_row(failed)
    assert (info['status'], info['code']) == ('execution_failure', 'inference_unavailable')
    info = report.categorize_row(bounded)
    assert (info['status'], info['code']) == ('execution_failure', 'too_many_targets')
    summary = report.summarize({'baseline': [skip, failed], 'altered': []})['baseline']
    assert summary['counts']['eligibility_skips'] == 1 and summary['counts']['rule_execution_failures'] == 1


def test_an_empty_failure_list_is_not_presented_as_success_when_nothing_was_assessed():
    failed = row('RULE-7', 'unavailable', [result('/shapes/0', 'unavailable', ['call_failed', 'call:incomplete_response']),
                                          result('/shapes/1', 'unavailable', ['call_failed', 'call:incomplete_response'])],
                 verified=False)
    report.add_categories([failed])
    summary = report.summarize({'baseline': [failed], 'altered': [failed]})['altered']
    assert summary['explicit_failure'] == [] and summary['explicit_pass'] == []
    assert summary['counts']['execution_failure'] == 2 and len(summary['execution_failures']) == 2
    [coverage] = summary['coverage']
    assert coverage['targets'] == 2 and coverage['explicit'] == 0 and coverage['execution_failure'] == 2
    legacy = report.legacy_evaluation({'v6': {'baseline': [failed], 'altered': [failed]}}, 'RULE-7', '/shapes/0')['v6']
    assert legacy['altered_explicit_failures_polygon_fit'] is None and legacy['altered_flagged_annotations_polygon_fit'] is None


def test_rejected_and_duplicate_references_are_reported_per_rule():
    item = row('RULE-7', 'unsure', [result('/shapes/0', 'unsure', ['no_assessment'])],
               scope_extra={'rejected_references': ['/shapes/9: not an eligible target', '/shapes/0: repeated assessment ignored']})
    report.add_categories([item])
    summary = report.summarize({'altered': [item]})['altered']
    assert [x['reference'] for x in summary['rejected_or_duplicate_references']] == [
        '/shapes/9: not an eligible target', '/shapes/0: repeated assessment ignored']


def test_v4_observations_map_onto_the_same_categories():
    item = {'rule_id': 'RULE-7', 'outcome': 'fail', 'reason': 'r', 'model_verified': True, 'duration_ms': 1.0, 'scope': None,
            'visual_assessment': {'observations': [{'object_key': '/shapes/0', 'outcome': 'fail'},
                                                   {'object_key': '/shapes/1', 'outcome': 'not_applicable'}]}}
    info = report.categorize_row(item)
    assert {k: v['category'] for k, v in info['targets'].items()} == {
        '/shapes/0': 'explicit_failure', '/shapes/1': 'condition_not_present'}


# -------------------------------------------------------------------- expectations (after the fact)

def altered_rows(man, woman):
    item = row('RULE-7', 'fail', [man, woman])
    report.add_categories([item])
    return {'altered': [item], 'baseline': []}


@pytest.mark.parametrize('man,woman,expected', [
    (result('/shapes/0', 'fail'), result('/shapes/1', 'pass'), {'match': 2}),
    (result('/shapes/0', 'pass'), result('/shapes/1', 'pass'), {'match': 1, 'miss': 1}),
    (result('/shapes/0', 'fail'), result('/shapes/1', 'fail'), {'match': 1, 'false_alarm': 1}),
    (result('/shapes/0', 'fail'), result('/shapes/1', 'unsure', ['no_assessment']), {'match': 1, 'unresolved': 1}),
    (result('/shapes/0', 'unsure', ['condition_unclear']), result('/shapes/1', 'pass'), {'unresolved': 1, 'match': 1}),
])
def test_expectations_distinguish_matches_misses_false_alarms_and_unresolved(man, woman, expected):
    samples = altered_rows(man, woman)
    outcome = report.evaluate_expectations(samples, {'altered': {'RULE-7': {'/shapes/0': 'fail', '/shapes/1': 'pass'}}})
    counts = {k: v for k, v in outcome['counts'].items() if v}
    assert counts == expected


def test_a_rule_that_was_not_run_is_unresolved_not_a_pass():
    outcome = report.evaluate_expectations({'altered': []}, {'altered': {'RULE-7': {'/shapes/0': 'fail'}}})
    assert outcome['counts']['unresolved'] == 1 and outcome['rows'][0]['actual_category'] == 'rule_not_run'


def test_the_legacy_polygon_fit_arguments_become_expectations_only_after_results_exist():
    samples = {'baseline': [row('RULE-7', 'pass', [result('/shapes/0', 'pass'), result('/shapes/1', 'pass')])],
               'altered': [row('RULE-7', 'fail', [result('/shapes/0', 'fail'), result('/shapes/1', 'pass')])]}
    assert report.legacy_expectations(samples, 'RULE-7', '/shapes/0') == {
        'baseline': {'RULE-7': {'/shapes/0': 'pass', '/shapes/1': 'pass'}},
        'altered': {'RULE-7': {'/shapes/0': 'fail', '/shapes/1': 'pass'}}}
