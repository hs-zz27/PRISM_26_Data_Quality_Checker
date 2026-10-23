"""Deterministic sample aggregation and violation-positive metrics.

The metric definitions reproduce backend/app/services/feedback_metrics.py so results are
comparable with the historical records. That module needs production evaluation inputs, so
the arithmetic is restated here over plain rows and checked against the reference results.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from backend.app.models.feedback import ArmMetrics

Outcome = Literal["pass", "fail", "unsure", "unavailable"]
Expected = Literal["pass", "fail"]
Assessment = Literal["improved", "equal", "regressed", "inconclusive"]


@dataclass(frozen=True)
class BoxVerdict:
    outcome: Outcome
    model_verified: bool


@dataclass(frozen=True)
class ScoredRow:
    case_name: str
    expected: Expected
    outcome: Outcome
    duration_ms: float


def aggregate_sample(boxes: Sequence[BoxVerdict]) -> tuple[Outcome, str]:
    """Any supported failing box fails the sample; otherwise unavailable beats unsure.

    Only a verified fail is "supported". Only all-verified passing boxes pass the sample.
    A sample with no boxes is unavailable: absence of boxes is never read as compliance.
    """
    if not boxes:
        return "unavailable", "no_boxes"
    if any(box.outcome == "fail" and box.model_verified for box in boxes):
        return "fail", "supported_failing_box"
    if any(box.outcome == "unavailable" or not box.model_verified for box in boxes):
        return "unavailable", "unavailable_box"
    if any(box.outcome == "unsure" for box in boxes):
        return "unsure", "unsure_box"
    return "pass", "all_boxes_pass"


def score_arm(rows: Sequence[ScoredRow]) -> ArmMetrics:
    """Violation-positive metrics. Unsure and unavailable stay in the intended denominator."""
    if not rows:
        raise ValueError("At least one intended case is required.")
    tp = fp = fn = tn = negative_abstentions = unsure = unavailable = 0
    elapsed = 0.0
    for row in rows:
        elapsed += row.duration_ms
        unsure += row.outcome == "unsure"
        unavailable += row.outcome == "unavailable"
        if row.expected == "fail":
            if row.outcome == "fail":
                tp += 1
            else:
                fn += 1  # a missed violation, including an abstention, is a false negative
        elif row.outcome == "fail":
            fp += 1
        elif row.outcome == "pass":
            tn += 1
        else:
            negative_abstentions += 1
    return ArmMetrics(
        intended=len(rows), tp=tp, fp=fp, fn=fn, tn=tn, negative_abstentions=negative_abstentions,
        unsure=unsure, unavailable=unavailable, coverage=(len(rows) - unsure - unavailable) / len(rows),
        precision=tp / (tp + fp) if tp + fp else None,
        recall=tp / (tp + fn) if tp + fn else None,
        f1=2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
        total_duration_ms=elapsed,
    )


def assess(baseline: ArmMetrics, candidate: ArmMetrics) -> Assessment:
    """Same rule as production: improvement needs a higher F1 without losing coverage."""
    if (baseline.f1 is None or candidate.f1 is None or baseline.unavailable or candidate.unavailable):
        return "inconclusive"
    if candidate.f1 > baseline.f1 and candidate.coverage >= baseline.coverage:
        return "improved"
    if candidate.f1 < baseline.f1 or candidate.coverage < baseline.coverage:
        return "regressed"
    if candidate.f1 == baseline.f1 and candidate.coverage == baseline.coverage:
        return "equal"
    return "inconclusive"
