"""Authored count/denominator regressions; synthetic predictions prove plumbing only."""
import pytest
from backend.app.services.feedback_metrics import compute_metrics
from backend.tests.feedback_fixtures import inputs, comparison

def test_violation_positive_abstention_and_negative_abstention(tmp_path):
    frozen = inputs(tmp_path)
    results = [comparison(frozen.truth.cases[0], 'unsure', 'fail'), comparison(frozen.truth.cases[1], 'unavailable', 'pass')]
    metrics = compute_metrics(frozen, results)
    assert (metrics.baseline.tp, metrics.baseline.fn, metrics.baseline.tn, metrics.baseline.negative_abstentions) == (0, 1, 0, 1)
    assert metrics.baseline.recall == 0 and metrics.baseline.precision is None and (metrics.baseline.f1 == 0)
    assert metrics.baseline.coverage == 0 and metrics.baseline.unsure == metrics.baseline.unavailable == 1
    assert metrics.candidate.tp == metrics.candidate.tn == 1 and metrics.candidate.f1 == 1
    assert metrics.assessment == 'inconclusive'

def test_synthetic_quality_gain_separate_from_duration(tmp_path):
    frozen = inputs(tmp_path)
    results = [comparison(c, 'pass', c.expected) for c in frozen.truth.cases]
    report = compute_metrics(frozen, results)
    assert report.assessment == 'improved' and report.baseline.f1 == 0 and (report.candidate.f1 == 1)
    assert report.baseline.total_duration_ms == report.candidate.total_duration_ms == 2

def test_no_positive_denominator_is_null_not_perfect(tmp_path):
    frozen = inputs(tmp_path, expected=('pass',))
    result = compute_metrics(frozen, [comparison(frozen.truth.cases[0], 'pass', 'pass')])
    assert result.baseline.tn == 1 and result.baseline.precision is None and (result.baseline.recall is None)
    assert result.baseline.f1 is None and result.assessment == 'inconclusive'

def test_partial_duplicate_and_reordered_results_cannot_complete(tmp_path):
    frozen = inputs(tmp_path)
    results = [comparison(c) for c in frozen.truth.cases]
    for invalid in [results[:1], [results[0], results[0]], list(reversed(results))]:
        with pytest.raises(ValueError):
            compute_metrics(frozen, invalid)
