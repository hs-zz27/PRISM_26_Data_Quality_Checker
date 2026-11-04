"""Classification and summaries for ``compare_visual_audit.py``. Pure: no model, no repository imports.

Report format 2 keeps the earlier fields where practical and adds explicit categories, because one list of
"flagged" annotations mixed three different things. Every eligible annotation lands in exactly ONE category:

    explicit_pass / explicit_failure   the model assessed this annotation and the structured answer is pass / fail
    uncertain                          the model (or an unclear condition, or a grounding downgrade) said unsure
    missing_assessment                 no trusted assessment was obtained for this annotation (``no_assessment``).
                                       This is a coverage gap, NOT an allegation that the annotation is wrong
    unavailable                        the evidence could not establish the requirement for this annotation
    execution_failure                  the request for this annotation failed or was invalid (code recorded)
    condition_not_present              the requirement's own condition does not hold; the annotation is excluded

A whole rule can also be an ``eligibility_skip`` (no annotation had the geometry the requirement judges; no model
call was made, which is correct behaviour) or an ``execution_failure`` (the model call itself did not succeed).
Expected outcomes are applied only here, after every result exists, and never enter a request.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

REPORT_FORMAT_VERSION = 2
# Scope reasons that mean "nothing was eligible for this requirement" (an expected, correct no-model result).
ELIGIBILITY_CODES = frozenset({
    "no_annotations", "no_eligible_annotation", "supplied_box_not_available", "mask_not_supplied",
    "mask_referenced_unavailable", "mask_encoding_unsupported", "mask_not_visible_to_visual_check",
    "measurement_not_supported"})
TARGET_CATEGORIES = ("explicit_pass", "explicit_failure", "uncertain", "missing_assessment", "unavailable",
                     "execution_failure", "condition_not_present")
_OBSERVATION_CATEGORY = {"pass": "explicit_pass", "fail": "explicit_failure", "unsure": "uncertain",
                         "not_applicable": "condition_not_present", "unavailable": "unavailable"}


def classify_target(result: Mapping[str, Any]) -> dict[str, Any]:
    """Category of one per-annotation result from a scoped (v5/v6) finding."""
    flags = list(result.get("flags") or [])
    outcome = result.get("outcome")
    if "call_failed" in flags:
        code = next((flag[5:] for flag in flags if flag.startswith("call:")), "unknown")
        return {"category": "execution_failure", "code": code, "outcome": outcome, "flags": flags}
    if "no_assessment" in flags:
        category = "missing_assessment"
    elif "condition_not_present" in flags:
        category = "condition_not_present"
    elif outcome == "fail":
        category = "explicit_failure"
    elif outcome == "pass":
        category = "explicit_pass"
    elif outcome == "unsure":
        category = "uncertain"
    else:
        category = "unavailable"
    row = {"category": category, "outcome": outcome, "flags": flags}
    model_outcome = result.get("model_outcome")
    if model_outcome in ("pass", "fail") and outcome == "unsure":
        row["downgraded_from"] = model_outcome          # a pass/fail the backend refused to trust, see flags
    return row


def categorize_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Where one (rule, sample) execution landed. Keeps eligibility skips apart from execution failures."""
    scope = row.get("scope") or {}
    unavailable = scope.get("unavailable") or {}
    verified = bool(row.get("model_verified"))
    base = {"rejected_references": list(scope.get("rejected_references") or []), "targets": {}, "failures": []}
    if (unavailable.get("code") in ELIGIBILITY_CODES and scope.get("outcome_source") == "no_model_call"
            and not verified):
        return {**base, "status": "eligibility_skip", "code": unavailable["code"]}
    if scope.get("results"):
        targets = {item["object_key"]: classify_target(item) for item in scope["results"]}
        failures = [{"object_key": key, "code": value["code"]} for key, value in targets.items()
                    if value["category"] == "execution_failure"]
        return {**base, "status": "assessed", "targets": targets, "failures": failures}
    assessment = row.get("visual_assessment")
    if assessment and assessment.get("observations"):
        targets = {item["object_key"]: {"category": _OBSERVATION_CATEGORY.get(item["outcome"], "unavailable"),
                                        "outcome": item["outcome"], "flags": []}
                   for item in assessment["observations"]}
        return {**base, "status": "assessed", "targets": targets}
    if not verified:
        code = unavailable.get("code") or str(row.get("reason") or "unknown")[:120]
        return {**base, "status": "execution_failure", "code": code,
                "failures": [{"object_key": None, "code": code}]}
    return {**base, "status": "assessed"}


def add_categories(rows: Iterable[dict[str, Any]]) -> None:
    for row in rows:
        row["categories"] = categorize_row(row)


def summarize(samples: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Per sample: each category as an explicit list, plus per-rule coverage. Empty lists are real zeros."""
    summary: dict[str, Any] = {}
    for name, rows in samples.items():
        lists: dict[str, list[dict[str, Any]]] = {category: [] for category in TARGET_CATEGORIES}
        skips, row_failures, rejected, coverage = [], [], [], []
        for row in rows:
            info = row.get("categories") or categorize_row(row)
            rule = row["rule_id"]
            if info["status"] == "eligibility_skip":
                skips.append({"rule_id": rule, "code": info["code"]})
            elif info["status"] == "execution_failure":
                row_failures.append({"rule_id": rule, "code": info["code"]})
            for reference in info["rejected_references"]:
                rejected.append({"rule_id": rule, "reference": reference})
            counts = {category: 0 for category in TARGET_CATEGORIES}
            for key, value in info["targets"].items():
                entry = {"rule_id": rule, "object_key": key, **{k: v for k, v in value.items() if k != "category"}}
                lists[value["category"]].append(entry)
                counts[value["category"]] += 1
            coverage.append({"rule_id": rule, "status": info["status"], "targets": len(info["targets"]),
                             "explicit": counts["explicit_pass"] + counts["explicit_failure"], **counts,
                             "calls": len(row.get("calls") or [])})
        summary[name] = {
            **{key: value for key, value in lists.items()},
            "eligibility_skips": skips, "execution_failures": row_failures + lists["execution_failure"],
            "rejected_or_duplicate_references": rejected, "coverage": coverage,
            "counts": {**{category: len(items) for category, items in lists.items()},
                       "eligibility_skips": len(skips), "rule_execution_failures": len(row_failures),
                       "rejected_or_duplicate_references": len(rejected)}}
    return summary


def evaluate_expectations(samples: Mapping[str, list[dict[str, Any]]],
                          expectations: Mapping[str, Mapping[str, Mapping[str, str]]]) -> dict[str, Any]:
    """Compare explicit results with expected pass/fail per (sample, rule, annotation) AFTER the fact.

    ``match`` means the explicit result equals the expectation. ``false_alarm``: expected pass, explicit failure.
    ``miss``: expected fail, explicit pass. ``unresolved``: anything without an explicit pass/fail (uncertain,
    missing, unavailable, execution failure, skip), reported with its category, so an incomplete result is never
    counted as a success or as an accusation.
    """
    rows, counts = [], {"match": 0, "false_alarm": 0, "miss": 0, "unresolved": 0}
    for sample, rules in expectations.items():
        by_rule = {row["rule_id"]: row for row in samples.get(sample, [])}
        for rule, expected in rules.items():
            row = by_rule.get(rule)
            info = (row or {}).get("categories") or (categorize_row(row) if row else None)
            for key, want in expected.items():
                got = info["targets"].get(key) if info else None
                category = got["category"] if got else (info["status"] if info else "rule_not_run")
                if category == "explicit_pass" and want == "pass" or category == "explicit_failure" and want == "fail":
                    verdict = "match"
                elif category == "explicit_failure" and want == "pass":
                    verdict = "false_alarm"
                elif category == "explicit_pass" and want == "fail":
                    verdict = "miss"
                else:
                    verdict = "unresolved"
                counts[verdict] += 1
                rows.append({"sample": sample, "rule_id": rule, "object_key": key, "expected": want,
                             "actual_category": category, "verdict": verdict})
    return {"counts": counts, "rows": rows}


def legacy_expectations(samples: Mapping[str, list[dict[str, Any]]], rule_id: str, shifted_key: str):
    """The expectation implied by --polygon-fit-rule/--shifted-key: baseline all pass; altered shifted fails."""
    expected: dict[str, dict[str, dict[str, str]]] = {"baseline": {}, "altered": {}}
    for name in ("baseline", "altered"):
        row = next((r for r in samples.get(name, []) if r["rule_id"] == rule_id), None)
        scope = (row or {}).get("scope") or {}
        keys = [item["object_key"] for item in scope.get("eligible") or []]
        if keys:
            expected[name][rule_id] = {key: "fail" if name == "altered" and key == shifted_key else "pass" for key in keys}
    return {name: rules for name, rules in expected.items() if rules}


def legacy_evaluation(results: Mapping[str, Mapping[str, list[dict[str, Any]]]], polygon_fit_rule: str,
                      shifted_key: str) -> dict[str, Any]:
    """The report-format-1 ``evaluation`` fields, with the category-separated fields added beside them.

    ``altered_flagged_annotations_polygon_fit`` and ``altered_unchanged_annotation_flagged`` keep their original
    meaning (failures AND uncertainty combined) for existing consumers but are DEPRECATED: use the
    ``..._explicit_failures``, ``..._uncertain`` and ``..._missing`` fields, which never present a coverage gap
    as an accusation.
    """
    out: dict[str, Any] = {}
    for version, samples in results.items():
        base, alt = samples["baseline"], samples["altered"]
        fit = next((row for row in alt if row["rule_id"] == polygon_fit_rule), None)
        info = (fit or {}).get("categories") or (categorize_row(fit) if fit else {"targets": {}})
        by_category = {category: sorted(k for k, v in info["targets"].items() if v["category"] == category)
                       for category in TARGET_CATEGORIES}
        combined = sorted(set(by_category["explicit_failure"]) | set(by_category["uncertain"]) |
                          set(by_category["missing_assessment"]))
        verified = bool(fit and fit.get("model_verified") and info["targets"])
        statuses = [(row.get("categories") or categorize_row(row))["status"] for batch in (base, alt) for row in batch]
        out[version] = {
            "baseline_rules_failed_false_alarms": [row["rule_id"] for row in base if row["outcome"] == "fail"],
            "altered_polygon_fit_outcome": None if fit is None else fit["outcome"],
            "altered_flagged_annotations_polygon_fit": combined if verified else None,
            "altered_explicit_failures_polygon_fit": by_category["explicit_failure"] if verified else None,
            "altered_uncertain_polygon_fit": by_category["uncertain"] if verified else None,
            "altered_missing_assessments_polygon_fit": by_category["missing_assessment"] if verified else None,
            "altered_rules_failed_other_than_polygon_fit": [row["rule_id"] for row in alt
                                                            if row["outcome"] == "fail" and row["rule_id"] != polygon_fit_rule],
            "altered_unchanged_annotation_flagged": [k for k in combined if k != shifted_key] if verified else None,
            "altered_unchanged_annotation_explicitly_failed": (
                [k for k in by_category["explicit_failure"] if k != shifted_key] if verified else None),
            "model_calls_skipped_as_unavailable_without_inference": statuses.count("eligibility_skip"),
            "unverified_execution_count": statuses.count("execution_failure"),
            "unavailable_rules": {name: [row["rule_id"] for row in batch if row["outcome"] == "unavailable"]
                                  for name, batch in (("baseline", base), ("altered", alt))},
            "total_duration_seconds": round(sum(row["duration_ms"] for batch in (base, alt) for row in batch) / 1000, 1)}
    return out


DEPRECATED_FIELDS = ["evaluation.*.altered_flagged_annotations_polygon_fit",
                     "evaluation.*.altered_unchanged_annotation_flagged"]
