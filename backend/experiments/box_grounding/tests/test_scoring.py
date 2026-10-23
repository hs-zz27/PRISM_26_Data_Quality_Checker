"""Aggregation truth table and metric definitions, checked against the recorded historical results."""

from itertools import product
import json

import pytest

from backend.experiments.box_grounding.scoring import BoxVerdict, ScoredRow, aggregate_sample, assess, score_arm

from box_helpers import PACKET

OUTCOMES = ("pass", "fail", "unsure", "unavailable")
COMPARISON = json.loads((PACKET / "reference_results/reviewed_comparison.json").read_bytes())
OVERVIEW = json.loads((PACKET / "reference_results/numbered_overview.json").read_bytes())


def oracle(verdicts):
    """The rule from the assignment, written independently of the implementation."""
    supported_fail = any(o == "fail" and v for o, v in verdicts)
    unavailable = any(o == "unavailable" or not v for o, v in verdicts)
    unsure = any(o == "unsure" for o, v in verdicts)
    if not verdicts:
        return "unavailable"
    if supported_fail:
        return "fail"
    if unavailable:
        return "unavailable"
    return "unsure" if unsure else "pass"


@pytest.mark.parametrize("size", [1, 2, 3])
def test_the_aggregation_truth_table_is_exhaustive(size):
    for combo in product(product(OUTCOMES, (True, False)), repeat=size):
        verdicts = [(o, v) for o, v in combo if not (v is False and o == "pass")]  # an unverified pass cannot exist
        if len(verdicts) != size:
            continue
        got, _ = aggregate_sample([BoxVerdict(o, v) for o, v in verdicts])
        assert got == oracle(verdicts), verdicts


@pytest.mark.parametrize("verdicts,expected", [
    ([], ("unavailable", "no_boxes")),
    ([("pass", True)], ("pass", "all_boxes_pass")),
    ([("pass", True), ("pass", True)], ("pass", "all_boxes_pass")),
    ([("pass", True), ("unsure", True)], ("unsure", "unsure_box")),
    ([("unsure", True), ("unavailable", True)], ("unavailable", "unavailable_box")),
    ([("pass", True), ("unavailable", False)], ("unavailable", "unavailable_box")),
    ([("unavailable", True), ("fail", True)], ("fail", "supported_failing_box")),
    ([("unsure", True), ("fail", True)], ("fail", "supported_failing_box")),
    ([("fail", False)], ("unavailable", "unavailable_box")),  # an unverified fail is not "supported"
])
def test_aggregation_examples(verdicts, expected):
    assert aggregate_sample([BoxVerdict(o, v) for o, v in verdicts]) == expected


def rows(table, arm):
    return [ScoredRow(r["case_name"], r["expected"], r[arm]["prediction"]["outcome"], r[arm]["duration_ms"]) for r in table]


@pytest.mark.parametrize("table,arm,recorded", [
    (COMPARISON["results"], "baseline", COMPARISON["metrics"]["baseline"]),
    (COMPARISON["results"], "candidate", COMPARISON["metrics"]["candidate"]),
    (OVERVIEW["results"], "overview", OVERVIEW["metrics"]["candidate"]),
])
def test_metrics_reproduce_every_recorded_historical_result(table, arm, recorded):
    got = score_arm(rows(table, arm)).model_dump()
    for key, value in recorded.items():
        assert got[key] == pytest.approx(value), key


def test_assessments_reproduce_the_recorded_historical_assessments():
    baseline = score_arm(rows(COMPARISON["results"], "baseline"))
    assert assess(baseline, score_arm(rows(COMPARISON["results"], "candidate"))) == COMPARISON["metrics"]["assessment"]
    assert assess(baseline, score_arm(rows(OVERVIEW["results"], "overview"))) == OVERVIEW["metrics"]["assessment"]


def row(expected, outcome, ms=1000.0, name="c"):
    return ScoredRow(name, expected, outcome, ms)


def test_unsure_and_unavailable_stay_in_the_denominator_and_missed_violations_are_false_negatives():
    metrics = score_arm([row("fail", "unsure"), row("fail", "unavailable"), row("fail", "pass"), row("fail", "fail"),
                         row("pass", "unsure"), row("pass", "unavailable"), row("pass", "pass"), row("pass", "fail")])
    assert (metrics.intended, metrics.tp, metrics.fn, metrics.tn, metrics.fp) == (8, 1, 3, 1, 1)
    assert (metrics.negative_abstentions, metrics.unsure, metrics.unavailable) == (2, 2, 2)
    assert metrics.coverage == 0.5 and metrics.precision == 0.5 and metrics.recall == 0.25
    assert metrics.f1 == pytest.approx(2 / 6) and metrics.total_duration_ms == 8000.0


def test_undefined_ratios_are_none_not_zero_or_one():
    only_negatives = score_arm([row("pass", "pass")])
    assert (only_negatives.precision, only_negatives.recall, only_negatives.f1) == (None, None, None)
    never_predicts_fail = score_arm([row("fail", "pass")])
    assert never_predicts_fail.precision is None and never_predicts_fail.recall == 0.0 and never_predicts_fail.f1 == 0.0
    with pytest.raises(ValueError):
        score_arm([])


@pytest.mark.parametrize("baseline,candidate,expected", [
    ([row("fail", "fail"), row("fail", "pass")], [row("fail", "fail"), row("fail", "fail")], "improved"),
    ([row("fail", "fail"), row("fail", "fail")], [row("fail", "fail"), row("fail", "pass")], "regressed"),
    ([row("fail", "fail"), row("pass", "pass")], [row("fail", "fail"), row("pass", "pass")], "equal"),
    ([row("fail", "fail"), row("pass", "pass")], [row("fail", "fail"), row("pass", "unsure")], "regressed"),
    ([row("fail", "fail"), row("pass", "pass")], [row("fail", "fail"), row("pass", "unavailable")], "inconclusive"),
    ([row("pass", "pass")], [row("pass", "pass")], "inconclusive"),
])
def test_assessment_requires_a_better_f1_without_losing_coverage(baseline, candidate, expected):
    assert assess(score_arm(baseline), score_arm(candidate)) == expected
