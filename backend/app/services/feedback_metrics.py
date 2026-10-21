"""Violation-positive sample/rule metrics, including every intended truth unit."""
from ..models.feedback import ArmMetrics, ComparisonMetrics, CaseComparison, EvaluationInput, checked
from ..models.feedback import unit_id

def compute_metrics(inputs, results):
    inputs = checked(EvaluationInput, inputs)
    results = [checked(CaseComparison, r) for r in results]
    cases = inputs.truth.cases
    if len(results) != len(cases):
        raise ValueError('All intended cases are required.')
    for case, result in zip(cases, results, strict=True):
        if result.case_id != unit_id(case.manifest, case.target):
            raise ValueError('Result order/identity mismatch.')
        if any((p.prediction.rule_id != case.target.rule.rule_id for p in (result.baseline, result.candidate))):
            raise ValueError('Result rule mismatch.')
    arms = []
    for arm in ('baseline', 'candidate'):
        tp = fp = fn = tn = negative_abstentions = unsure = unavailable = 0
        elapsed = 0.0
        for case, result in zip(cases, results, strict=True):
            recorded = getattr(result, arm)
            outcome = recorded.prediction.outcome
            elapsed += recorded.duration_ms
            unsure += outcome == 'unsure'
            unavailable += outcome == 'unavailable'
            if case.expected == 'fail':
                if outcome == 'fail':
                    tp += 1
                else:
                    fn += 1
            elif outcome == 'fail':
                fp += 1
            elif outcome == 'pass':
                tn += 1
            else:
                negative_abstentions += 1
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
        arms.append(ArmMetrics(intended=len(cases), tp=tp, fp=fp, fn=fn, tn=tn, negative_abstentions=negative_abstentions, unsure=unsure, unavailable=unavailable, coverage=(len(cases) - unsure - unavailable) / len(cases), precision=precision, recall=recall, f1=f1, total_duration_ms=elapsed))
    baseline, candidate = arms
    assessment = 'inconclusive'
    if baseline.f1 is not None and candidate.f1 is not None and (not baseline.unavailable) and (not candidate.unavailable):
        if candidate.f1 > baseline.f1 and candidate.coverage >= baseline.coverage:
            assessment = 'improved'
        elif candidate.f1 < baseline.f1 or candidate.coverage < baseline.coverage:
            assessment = 'regressed'
        elif candidate.f1 == baseline.f1 and candidate.coverage == baseline.coverage:
            assessment = 'equal'
    return ComparisonMetrics(baseline=baseline, candidate=candidate, assessment=assessment)
