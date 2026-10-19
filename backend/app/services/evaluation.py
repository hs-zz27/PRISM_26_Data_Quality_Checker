"""Evaluate confirmed rules against one collected measurement report.

The measurement service intentionally stops before policy decisions.  This
module is the narrow boundary where a confirmed rule's parameters are applied
to those measurements.  It never changes a rulebook, a measurement report or
human review state, and it always emits a finding for a supported rule even
when the result is a pass.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import logging
from math import isfinite
from typing import Any

from pydantic import ValidationError

from ..checks.registry import RuleParameterError, get_check_definition, validate_check_parameters
from ..models.finding import Evidence, Finding, FindingOutcome
from ..models.measurements import AuditMeasurements, CheckEnvelope
from ..models.rule import ConfirmationState, Rule, Rulebook


class RuleEvaluationError(ValueError):
    """The rulebook or measurement report cannot be evaluated safely."""


class RulebookNotConfirmedError(RuleEvaluationError):
    """Evaluation was requested for a non-final or empty rulebook."""


class CheckExecutionError(RuntimeError):
    """An evaluator explicitly attributed a failure to one check boundary."""


@dataclass(frozen=True)
class RuleEvaluationResult:
    """Findings plus whether an individual check failed internally."""

    findings: list[Finding]
    internal_check_failures: int = 0


logger = logging.getLogger(__name__)


def _require_identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")
    return value


def _require_revision(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("sample_revision must be a positive integer.")
    return value


def _json_safe(value: Any) -> Any:
    """Copy measurement values into the finite JSON vocabulary.

    Existing checks use tuples and sets internally and NumPy scalar values can
    appear when a caller builds a report directly.  PostgreSQL JSONB and the
    Pydantic evidence model must see ordinary JSON values instead.  No values
    are normalized semantically: this only changes container/number wrappers.
    """
    if isinstance(value, Mapping):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(child) for child in value]
    if isinstance(value, (set, frozenset)):
        converted = [_json_safe(child) for child in value]
        try:
            return sorted(converted)
        except TypeError:
            return converted
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("Measurement evidence must contain finite numbers.")
        return value
    # NumPy scalar values expose item() without making NumPy a domain-model
    # dependency.  The result is recursively checked above.
    item = getattr(value, "item", None)
    if callable(item):
        return _json_safe(item())
    raise ValueError(f"Measurement evidence contains unsupported value {type(value).__name__}.")


def _snapshot_rulebook(rulebook: Rulebook) -> Rulebook:
    if not isinstance(rulebook, Rulebook):
        raise TypeError("rulebook must be a Rulebook instance.")
    try:
        # Rulebook and nested Rule objects are mutable.  Dumping and validating
        # again prevents a caller's post-construction mutation from bypassing
        # confirmation, citation and uniqueness checks.
        snapshot = Rulebook.model_validate(rulebook.model_dump())
    except ValidationError:
        raise
    if snapshot.confirmation_state is not ConfirmationState.CONFIRMED:
        raise RulebookNotConfirmedError("Only a confirmed rulebook can be evaluated.")
    if not snapshot.rules or any(
        rule.confirmation_state is not ConfirmationState.CONFIRMED for rule in snapshot.rules
    ):
        raise RulebookNotConfirmedError(
            "A confirmed rulebook must be nonempty and contain only confirmed rules."
        )
    return snapshot


def _snapshot_report(measurements: AuditMeasurements) -> dict[str, Any]:
    """Validate the structural parts used by the evaluator and deep-copy them."""
    if not isinstance(measurements, Mapping):
        raise TypeError("measurements must be an audit measurement mapping.")
    for name in ("image_id", "allowed_labels", "status", "review_status", "errors", "checks", "objects"):
        if name not in measurements:
            raise RuleEvaluationError(f"Audit measurements are missing {name!r}.")
    if not isinstance(measurements["image_id"], str) or not measurements["image_id"].strip():
        raise RuleEvaluationError("Audit measurements have an invalid image_id.")
    if measurements["status"] not in ("completed", "incomplete"):
        raise RuleEvaluationError("Audit measurements have an invalid processing status.")
    if measurements["review_status"] != "pending_review":
        raise RuleEvaluationError("Audit measurements must remain pending human review.")
    if not isinstance(measurements["checks"], Mapping):
        raise RuleEvaluationError("Audit measurements checks must be a mapping.")
    if not isinstance(measurements["objects"], Sequence) or isinstance(measurements["objects"], (str, bytes)):
        raise RuleEvaluationError("Audit measurements objects must be a sequence.")

    report = deepcopy(dict(measurements))
    # Validate all report values, not only the evidence selected by one rule.
    # This makes an invalid persisted measurement impossible to turn into a
    # partially trustworthy finding later.
    try:
        _json_safe(report)
    except ValueError as error:
        raise RuleEvaluationError(str(error)) from error
    for index, row in enumerate(report["objects"]):
        if not isinstance(row, Mapping):
            raise RuleEvaluationError(f"Object measurement row {index} is not a mapping.")
        annotation = row.get("annotation")
        if not isinstance(annotation, Mapping):
            raise RuleEvaluationError(f"Object measurement row {index} has no annotation.")
        object_id = annotation.get("object_id")
        if type(object_id) is not int:
            raise RuleEvaluationError(f"Object measurement row {index} has an invalid object_id.")
        if not isinstance(row.get("checks"), Mapping):
            raise RuleEvaluationError(f"Object measurement row {index} has invalid checks.")
    check_maps = [("sample", report["checks"])] + [
        (f"object:{index}", row["checks"])
        for index, row in enumerate(report["objects"])
    ]
    for location, checks in check_maps:
        for check_name, envelope in checks.items():
            if envelope is not None and not isinstance(envelope, Mapping):
                raise RuleEvaluationError(
                    f"Measurement check {check_name!r} at {location} is not an envelope."
                )
    return report


def _envelope(value: object) -> CheckEnvelope | None:
    if not isinstance(value, Mapping):
        return None
    return value  # type: ignore[return-value]


def _result_measurements(envelope: Mapping[str, Any] | None, *, source_index: int | None = None) -> dict[str, Any]:
    if envelope is None:
        return {}
    evidence: dict[str, Any] = {
        "status": envelope.get("status"),
        "reason": envelope.get("reason"),
        "detail": envelope.get("detail"),
    }
    if envelope.get("result") is not None:
        evidence["result"] = _json_safe(envelope["result"])
    if source_index is not None:
        evidence["source_index"] = source_index
    return evidence


def _annotation_unavailable_reason(report: Mapping[str, Any]) -> str | None:
    """Distinguish a parsed empty annotation from missing/unusable source text."""
    annotation_text = report.get("annotation_text")
    if not isinstance(annotation_text, str) or not annotation_text.strip():
        return "annotation_load_failed"
    for error in report.get("errors", []):
        if not isinstance(error, Mapping):
            continue
        reason = error.get("reason")
        if reason in ("annotation_load_failed", "annotation_parse_failed"):
            return str(reason)
    if not report.get("objects"):
        metadata = report.get("checks", {}).get("annotation_metadata")
        metadata_result = metadata.get("result") if isinstance(metadata, Mapping) else None
        if not isinstance(metadata_result, Mapping):
            return "annotation_evidence_unavailable"
        declared = metadata_result.get("declared")
        actual_count = metadata_result.get("actual_object_count")
        # Zero parsed rows alone can also mean an unreadable annotation format.
        # Only the object-count declaration matters here; missing image/coordinate
        # metadata must not block labels on an explicitly empty annotation.
        declared_count = declared.get("object_count") if isinstance(declared, Mapping) else None
        if (
            type(actual_count) is not int or actual_count != 0
            or type(declared_count) is not int or declared_count != 0
        ):
            return "annotation_evidence_unavailable"
    return None


def _finding_id(
    *, audit_id: str, sample_id: str, sample_revision: int, rule_id: str, occurrence: str,
) -> str:
    identity = "\x1f".join((audit_id, sample_id, str(sample_revision), rule_id, occurrence))
    digest = sha256(identity.encode("utf-8")).hexdigest()
    return f"finding_{digest}"


def _make_finding(
    *,
    rule: Rule,
    outcome: FindingOutcome,
    reason: str | None,
    evidence: Mapping[str, Any] | None,
    audit_id: str,
    sample_id: str,
    sample_revision: int,
    object_id: int | None,
    occurrence: str,
    rulebook_id: str,
    rulebook_version: int,
) -> Finding:
    try:
        return Finding(
            finding_id=_finding_id(
                audit_id=audit_id, sample_id=sample_id, sample_revision=sample_revision,
                rule_id=rule.rule_id, occurrence=occurrence,
            ),
            audit_id=audit_id,
            sample_id=sample_id,
            sample_revision=sample_revision,
            object_id=object_id,
            rulebook_id=rulebook_id,
            rulebook_version=rulebook_version,
            rule_id=rule.rule_id,
            description=rule.description,
            check_name=rule.check_name,
            outcome=outcome,
            reason=reason,
            parameters=deepcopy(rule.parameters),
            origin=rule.origin,
            source_sentence=rule.source_sentence,
            evidence=Evidence(measurements=deepcopy(dict(evidence or {}))),
        )
    except ValidationError:
        raise


def _generic_outcome(
    envelope: Mapping[str, Any] | None, *, field: str = "matches",
) -> tuple[FindingOutcome, str | None]:
    """Turn an explicitly boolean measurement into a machine outcome."""
    if envelope is None:
        return FindingOutcome.UNAVAILABLE, "evidence_unavailable"
    if envelope.get("status") == "unavailable":
        return FindingOutcome.UNAVAILABLE, envelope.get("reason") or "evidence_unavailable"
    if envelope.get("status") != "completed":
        return FindingOutcome.UNSURE, "ambiguous_evidence"
    result = envelope.get("result")
    if not isinstance(result, Mapping):
        return FindingOutcome.UNSURE, "ambiguous_evidence"
    if result.get("measurement_available") is False:
        return FindingOutcome.UNAVAILABLE, result.get("reason") or "evidence_unavailable"
    value = result.get(field)
    if type(value) is bool:
        return (FindingOutcome.PASS if value else FindingOutcome.FAIL), None
    # A completed check with a missing or null decision is not a violation.  It
    # is an ambiguous result and must stay visible as unsure.
    return FindingOutcome.UNSURE, "ambiguous_evidence"


def _numeric_outcome(
    envelope: Mapping[str, Any] | None,
    *,
    field: str,
    predicate: Any,
) -> tuple[FindingOutcome, str | None]:
    if envelope is None:
        return FindingOutcome.UNAVAILABLE, "evidence_unavailable"
    if envelope.get("status") == "unavailable":
        return FindingOutcome.UNAVAILABLE, envelope.get("reason") or "evidence_unavailable"
    if envelope.get("status") != "completed":
        return FindingOutcome.UNSURE, "ambiguous_evidence"
    result = envelope.get("result")
    if not isinstance(result, Mapping):
        return FindingOutcome.UNSURE, "ambiguous_evidence"
    if result.get("measurement_available") is False:
        return FindingOutcome.UNAVAILABLE, result.get("reason") or "evidence_unavailable"
    if result.get("measurement_available") is not True:
        return FindingOutcome.UNSURE, "ambiguous_evidence"
    value = result.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return FindingOutcome.UNSURE, "ambiguous_evidence"
    if not isfinite(float(value)):
        return FindingOutcome.UNSURE, "ambiguous_evidence"
    try:
        matches = bool(predicate(float(value)))
    except (TypeError, ValueError, ArithmeticError):
        return FindingOutcome.UNSURE, "ambiguous_evidence"
    return (FindingOutcome.PASS if matches else FindingOutcome.FAIL), None


def _unsupported_finding(
    rule: Rule,
    *,
    audit_id: str,
    sample_id: str,
    sample_revision: int,
    rulebook: Rulebook,
) -> Finding:
    return _make_finding(
        rule=rule,
        outcome=FindingOutcome.UNAVAILABLE,
        reason="unsupported_check",
        evidence=None,
        audit_id=audit_id,
        sample_id=sample_id,
        sample_revision=sample_revision,
        object_id=None,
        occurrence="sample",
        rulebook_id=rulebook.rulebook_id,
        rulebook_version=rulebook.version,
    )


def _internal_check_failure_finding(
    rule: Rule,
    *,
    audit_id: str,
    sample_id: str,
    sample_revision: int,
    rulebook: Rulebook,
) -> Finding:
    """Keep a check failure safe and reviewable without exposing internals."""
    return _make_finding(
        rule=rule,
        outcome=FindingOutcome.UNAVAILABLE,
        reason="check_execution_failed",
        evidence={
            "check_name": rule.check_name,
            "evidence_status": "internal_error",
        },
        audit_id=audit_id,
        sample_id=sample_id,
        sample_revision=sample_revision,
        object_id=None,
        occurrence="sample",
        rulebook_id=rulebook.rulebook_id,
        rulebook_version=rulebook.version,
    )


def _evaluate_sample_rule(
    rule: Rule,
    report: Mapping[str, Any],
    parameters: Mapping[str, Any],
) -> list[tuple[FindingOutcome, str | None, int | None, str, Mapping[str, Any]]]:
    envelope = _envelope(report["checks"].get(rule.check_name))
    if rule.check_name == "box_overlaps":
        if envelope is None:
            outcome, reason = FindingOutcome.UNAVAILABLE, "evidence_unavailable"
            return [(outcome, reason, None, "sample", {})]
        if envelope.get("status") == "unavailable":
            return [(
                FindingOutcome.UNAVAILABLE,
                envelope.get("reason") or "evidence_unavailable",
                None,
                "sample",
                _result_measurements(envelope),
            )]
        if envelope.get("status") != "completed":
            return [(FindingOutcome.UNSURE, "ambiguous_evidence", None, "sample", _result_measurements(envelope))]
        result = envelope.get("result")
        pairs = result.get("pairs") if isinstance(result, Mapping) else None
        if not isinstance(pairs, Sequence) or isinstance(pairs, (str, bytes)):
            return [(FindingOutcome.UNSURE, "ambiguous_evidence", None, "sample", _result_measurements(envelope))]
        if not pairs:
            return [(FindingOutcome.PASS, None, None, "sample", _result_measurements(envelope))]
        findings = []
        for pair_index, pair in enumerate(pairs):
            if not isinstance(pair, Mapping):
                findings.append((FindingOutcome.UNSURE, "ambiguous_evidence", None, f"pair:{pair_index}", {
                    "pair_index": pair_index,
                }))
                continue
            pair_envelope = {"status": "completed", "reason": None, "detail": None, "result": pair}
            outcome, reason = _numeric_outcome(
                pair_envelope,
                field="iou",
                predicate=lambda value, maximum=parameters["max_iou"]: value <= maximum,
            )
            evidence = _result_measurements(pair_envelope)
            evidence["pair_index"] = pair_index
            findings.append((outcome, reason, None, f"pair:{pair_index}", evidence))
        return findings

    sample_fields = {
        "annotation_ids": "valid",
        "mask_ids": "matches",
        "mask_dimensions": "matches",
        "annotation_metadata": "matches",
    }
    outcome, reason = _generic_outcome(
        envelope, field=sample_fields.get(rule.check_name, "matches"),
    )
    return [(outcome, reason, None, "sample", _result_measurements(envelope))]


def _evaluate_object_rule(
    rule: Rule,
    report: Mapping[str, Any],
    parameters: Mapping[str, Any],
) -> list[tuple[FindingOutcome, str | None, int | None, str, Mapping[str, Any]]]:
    rows = report["objects"]
    if not rows:
        if rule.check_name == "allowed_labels":
            annotation_reason = _annotation_unavailable_reason(report)
            if annotation_reason is not None:
                return [(FindingOutcome.UNAVAILABLE, annotation_reason, None, "sample", {})]
            return [(
                FindingOutcome.PASS,
                None,
                None,
                "sample",
                {"allowed_labels": deepcopy(list(parameters["allowed_labels"])), "object_count": 0},
            )]
        return [(FindingOutcome.UNAVAILABLE, "evidence_unavailable", None, "sample", {})]

    findings = []
    configured_labels = parameters.get("allowed_labels")

    for index, row in enumerate(rows):
        annotation = row["annotation"]
        object_id = int(annotation["object_id"])
        check_key = "label" if rule.check_name == "allowed_labels" else rule.check_name
        envelope = _envelope(row["checks"].get(check_key))
        if rule.check_name == "allowed_labels":
            actual_label = annotation.get("label")
            if not isinstance(actual_label, str) or not actual_label:
                outcome, reason = FindingOutcome.FAIL, "invalid_label"
            elif actual_label in configured_labels:
                outcome, reason = FindingOutcome.PASS, None
            else:
                outcome, reason = FindingOutcome.FAIL, "label_not_allowed"
        elif rule.check_name == "box_validity":
            outcome, reason = _generic_outcome(envelope, field="valid")
        elif rule.check_name == "box_mask_alignment":
            outcome, reason = _numeric_outcome(
                envelope,
                field="max_edge_difference",
                predicate=lambda value, maximum=parameters["max_edge_difference"]: value <= maximum,
            )
        elif rule.check_name == "mask_outside_box":
            outcome, reason = _numeric_outcome(
                envelope,
                field="outside_fraction",
                predicate=lambda value, maximum=parameters["max_outside_fraction"]: value <= maximum,
            )
        elif rule.check_name == "mask_box_fill":
            outcome, reason = _numeric_outcome(
                envelope,
                field="fill_fraction",
                predicate=lambda value, minimum=parameters["min_fill_fraction"], maximum=parameters["max_fill_fraction"]:
                    minimum <= value <= maximum,
            )
        elif rule.check_name == "mask_components":
            outcome, reason = _numeric_outcome(
                envelope,
                field="component_count",
                predicate=lambda value, maximum=parameters["max_component_count"]: value <= maximum,
            )
        else:
            outcome, reason = _generic_outcome(envelope)
        evidence = _result_measurements(envelope, source_index=index)
        evidence["annotation"] = _json_safe(annotation)
        if rule.check_name == "allowed_labels":
            evidence["actual_label"] = _json_safe(annotation.get("label"))
            evidence["allowed_labels"] = deepcopy(list(configured_labels))
        findings.append((
            outcome,
            reason,
            object_id,
            f"object:{index}",
            evidence,
        ))
    return findings


def evaluate_rules_detailed(
    rulebook: Rulebook,
    measurements: AuditMeasurements,
    *,
    audit_id: str,
    sample_id: str,
    sample_revision: int,
) -> RuleEvaluationResult:
    """Evaluate every confirmed rule and return independent pending findings.

    The explicit audit/sample identifiers are part of the finding identity.  A
    source occurrence index is included in the deterministic ID, so two rows
    with the same invalid object ID remain two distinct findings.
    """
    audit_id = _require_identifier(audit_id, "audit_id")
    sample_id = _require_identifier(sample_id, "sample_id")
    sample_revision = _require_revision(sample_revision)
    snapshot = _snapshot_rulebook(rulebook)
    report = _snapshot_report(measurements)

    # Validate all executable rule parameter documents before emitting any
    # finding.  A half-evaluated audit is worse than an explicit boundary error.
    parameters_by_rule: dict[str, dict[str, Any]] = {}
    for rule in snapshot.rules:
        try:
            validated = validate_check_parameters(rule.check_name, rule.parameters)
        except RuleParameterError as error:
            raise RuleEvaluationError(str(error)) from error
        parameters_by_rule[rule.rule_id] = (
            validated.model_dump(mode="python") if validated is not None else {}
        )

    findings: list[Finding] = []
    internal_check_failures = 0
    seen_ids: set[str] = set()
    for rule in snapshot.rules:
        definition = get_check_definition(rule.check_name)
        if definition is None:
            candidate_rows = [_unsupported_finding(
                rule,
                audit_id=audit_id,
                sample_id=sample_id,
                sample_revision=sample_revision,
                rulebook=snapshot,
            )]
        elif definition.scope in ("sample", "pair", "object"):
            try:
                if definition.scope in ("sample", "pair"):
                    rows = _evaluate_sample_rule(
                        rule, report, parameters_by_rule[rule.rule_id],
                    )
                else:
                    rows = _evaluate_object_rule(
                        rule, report, parameters_by_rule[rule.rule_id],
                    )
            except CheckExecutionError as error:
                internal_check_failures += 1
                logger.exception(
                    "Supported check execution failed",
                    extra={
                        "audit_id": audit_id,
                        "sample_id": sample_id,
                        "rule_id": rule.rule_id,
                        "check_name": rule.check_name,
                        "diagnostic": str(error),
                    },
                )
                candidate_rows = [_internal_check_failure_finding(
                    rule,
                    audit_id=audit_id,
                    sample_id=sample_id,
                    sample_revision=sample_revision,
                    rulebook=snapshot,
                )]
            else:
                candidate_rows = [
                    _make_finding(
                        rule=rule,
                        outcome=outcome,
                        reason=reason,
                        evidence=evidence,
                        audit_id=audit_id,
                        sample_id=sample_id,
                        sample_revision=sample_revision,
                        object_id=object_id,
                        occurrence=occurrence,
                        rulebook_id=snapshot.rulebook_id,
                        rulebook_version=snapshot.version,
                    )
                    for outcome, reason, object_id, occurrence, evidence in rows
                ]
        else:
            raise RuleEvaluationError(f"Check {rule.check_name!r} has no evaluator scope.")

        for finding in candidate_rows:
            if finding.finding_id in seen_ids:
                raise RuleEvaluationError("The evaluator generated a duplicate finding ID.")
            seen_ids.add(finding.finding_id)
            findings.append(finding)
    return RuleEvaluationResult(
        findings=findings,
        internal_check_failures=internal_check_failures,
    )


def evaluate_rules(
    rulebook: Rulebook,
    measurements: AuditMeasurements,
    *,
    audit_id: str,
    sample_id: str,
    sample_revision: int,
) -> list[Finding]:
    """Evaluate rules while preserving the original list-of-findings interface."""
    return evaluate_rules_detailed(
        rulebook,
        measurements,
        audit_id=audit_id,
        sample_id=sample_id,
        sample_revision=sample_revision,
    ).findings


def evaluate_confirmed_rules(
    measurements: AuditMeasurements,
    *,
    repository: Any,
    rulebook_id: str,
    rulebook_version: int,
    audit_id: str,
    sample_id: str,
    sample_revision: int,
) -> list[Finding]:
    """Load exactly one saved rulebook version, then evaluate it."""
    rulebook_id = _require_identifier(rulebook_id, "rulebook_id")
    if type(rulebook_version) is not int or rulebook_version < 1:
        raise ValueError("rulebook_version must be a positive integer.")
    rulebook = repository.get_rulebook(rulebook_id, version=rulebook_version)
    if rulebook.rulebook_id != rulebook_id or rulebook.version != rulebook_version:
        raise RuleEvaluationError("The repository returned a rulebook different from the requested pin.")
    return evaluate_rules(
        rulebook,
        measurements,
        audit_id=audit_id,
        sample_id=sample_id,
        sample_revision=sample_revision,
    )


def evaluate_and_save_findings(
    rulebook: Rulebook,
    measurements: AuditMeasurements,
    *,
    finding_repository: Any,
    audit_id: str,
    sample_id: str,
    sample_revision: int,
) -> list[Finding]:
    """Evaluate and persist a frozen finding batch through an explicit repository."""
    findings = evaluate_rules(
        rulebook,
        measurements,
        audit_id=audit_id,
        sample_id=sample_id,
        sample_revision=sample_revision,
    )
    finding_repository.save_findings(findings)
    return findings
