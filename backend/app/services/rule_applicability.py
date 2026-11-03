"""Decide, before any model call, which annotations a requirement can be checked against.

No I/O and no model here. Three jobs:

1. ``derive_applicability`` reads the REQUIREMENT text (never its context sentences) and states which
   supplied geometry it judges. It looks at the role each geometry term plays rather than at the
   presence of a word: definitions are skipped, negated mentions are not requirements, "box or
   polygon" is an alternative while "polygon inside its box" needs both, and anything it cannot
   settle is returned as ambiguous so a reviewer sees it. It is a deliberate, versioned heuristic
   (``derived-v1``), not language understanding; a reviewer can override it per rule.
2. ``resolve_scope`` applies the rule's applicability to each annotation's geometry PROVENANCE:
   a polygon-derived box is not a supplied box, a mask the visual check cannot see is not usable.
   Annotations without the required geometry are reported as ineligible context, never discarded.
   When nothing is eligible the result is "unavailable" with a reason and no model call is made.
3. ``combine_results`` turns the model's per-target assessments into the sample-level outcome
   deterministically, ties each result to a stable object key, downgrades assessments that cite
   geometry that was never supplied, and propagates uncertainty.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from types import SimpleNamespace
from typing import Any

from ..models.applicability import (
    APPLICABILITY_ALGORITHM, APPLICABILITY_ALGORITHMS, GeometryKind, IneligibleObject, ObjectGeometry, RuleApplicability,
    ScopeImage, ScopeObject, ScopeUnavailable, TargetResult, TargetScope,
)

# An explicit measured value (number plus unit). Visual model checks cannot measure such values.
MEASURED_VALUE = re.compile(
    r"(?<![\w.])\d+(?:[.,]\d+)?\s*(?:px\b|pixels?\b|%|per\s*cent\b|percent\b|degrees?\b|°|"
    r"mm\b|cm\b|millimet(?:er|re)s?\b|centimet(?:er|re)s?\b|met(?:er|re)s?\b|"
    r"milliseconds?\b|seconds?\b|ms\b|fps\b|frames?\b)",
    re.IGNORECASE,
)

_TERMS: dict[GeometryKind, re.Pattern] = {
    "box": re.compile(r"\b(?:bounding[\s-]*box(?:es)?|bbox(?:es)?|boxes|box|rectangles?)\b", re.IGNORECASE),
    "polygon": re.compile(r"\b(?:polygons?|polygon\s+parts?|polygonal|contours?|segmentation\s+outlines?)\b",
                          re.IGNORECASE),
    "mask": re.compile(r"\b(?:(?:instance|pixel|segmentation|binary)[\s-]+masks?|masks?|rle)\b", re.IGNORECASE),
}
_DEFINITION = re.compile(
    r"\b(?:means|refers\s+to|is\s+defined\s+as|are\s+defined\s+as|is\s+called|denotes|stands\s+for)\b",
    re.IGNORECASE)
_NEGATION = re.compile(
    r"\b(?:not|no|never|without|except|excluding|other\s+than|neither|nor)\b(?:\s+\w+){0,2}\s*$", re.IGNORECASE)
# derived-v2: a term is EXCLUDED only when an exclusion marker applies to the term itself (optionally through
# an article), not when a negation governs a verb ("do not extend boxes").
_EXCLUSION = re.compile(
    r"(?:\bother\s+than|\bexcept(?:\s+for)?|\bexcluding|\bapart\s+from|\bnot\s+including|\bbut\s+not|"
    r"\bwithout|\bneither|\bnor|\bno)\s+(?:(?:a|an|the|any|its|their|these|those)\s+)?$", re.IGNORECASE)
_ALTERNATIVE = re.compile(r"\bor\b|and\s*/\s*or|/|\beither\b", re.IGNORECASE)
_COMPARISON = re.compile(
    r"\b(?:inside|within|outside|contains?|enclos\w+|match(?:es)?|agree\w*|disagree\w*|consisten\w+|fits?|"
    r"overlap\w*|relative\s+to|compared\s+(?:to|with)|against|between|derived\s+from|align\w*|"
    r"beyond|past)\b|\bboth\b", re.IGNORECASE)
_NAMES = {"box": "bounding box", "polygon": "polygon", "mask": "mask"}


def _sentences(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[.;!?])\s+", " ".join(text.split())) if part]


def _names(kinds: Sequence[str]) -> str:
    return " and ".join(_NAMES[k] for k in kinds)


def derive_applicability(requirement: str, algorithm: str = APPLICABILITY_ALGORITHM) -> RuleApplicability:
    """State which supplied geometry the requirement judges, from the requirement text only.

    ``derived-v1`` (the default) is what stored rules and v5 audits use and is frozen. ``derived-v2`` differs
    only in what counts as an EXCLUDED geometry term: v1 treated any mention within a few words after a
    negation as excluded, so a prohibition such as "do not extend boxes or polygons" dropped boxes. v2 counts a
    term as excluded only when an exclusion marker applies to the term itself ("other than boxes", "without a
    polygon", "no masks").
    """
    if algorithm not in APPLICABILITY_ALGORITHMS:
        raise ValueError("Unknown applicability algorithm.")
    excluded = _NEGATION if algorithm == "derived-v1" else _EXCLUSION
    sentences = [s for s in _sentences(requirement) if not _DEFINITION.search(s)]
    mentions: list[tuple[int, int, GeometryKind]] = []
    negated: list[GeometryKind] = []
    text = " ".join(sentences)
    for kind, pattern in _TERMS.items():
        for match in pattern.finditer(text):
            if excluded.search(text[max(0, match.start() - 40):match.start()]):
                negated.append(kind)
            else:
                mentions.append((match.start(), match.end(), kind))
    mentions.sort()
    kinds: list[GeometryKind] = []
    for _, _, kind in mentions:
        if kind not in kinds:
            kinds.append(kind)
    if negated:
        return RuleApplicability(
            geometry=kinds, combine="any", status="ambiguous", source=algorithm,
            basis=("The requirement mentions a geometry type in a negated or excluded phrase "
                   f"({_names(sorted(set(negated)))}). Check which geometry it applies to."))
    if not kinds:
        return RuleApplicability(
            geometry=[], source=algorithm,
            basis="The requirement names no geometry type, so it can be checked against any annotation.")
    if len(kinds) == 1:
        return RuleApplicability(
            geometry=kinds, source=algorithm,
            basis=f"The requirement judges the supplied {_names(kinds)} only.")
    span = text[mentions[0][0]:mentions[-1][1]]
    alternative, comparison = bool(_ALTERNATIVE.search(span)), bool(_COMPARISON.search(span))
    if alternative and not comparison:
        return RuleApplicability(
            geometry=kinds, combine="any", source=algorithm,
            basis=(f"The requirement allows alternatives ({_names(kinds).replace(' and ', ' or ')}); "
                   "an annotation is eligible when it has any of them supplied."))
    if comparison and not alternative:
        return RuleApplicability(
            geometry=kinds, combine="all", source=algorithm,
            basis=(f"The requirement relates the {_names(kinds)} to each other; an annotation is "
                   "eligible only when both were supplied."))
    return RuleApplicability(
        geometry=kinds, combine="any", status="ambiguous", source=algorithm,
        basis=(f"The requirement mentions {_names(kinds)} without making clear whether they are "
               "alternatives or both required. Treated as alternatives until a reviewer decides; "
               "consider splitting the rule or setting its applicability."))


def effective_applicability(rule, algorithm: str = APPLICABILITY_ALGORITHM) -> tuple[RuleApplicability, str]:
    """The rule's own applicability (never rewritten), else one derived now and recorded as such."""
    if rule.applicability is not None:
        return rule.applicability, "rule"
    return derive_applicability(rule.description, algorithm), "derived_at_audit"


def object_key(obj: Mapping[str, Any]) -> str:
    return obj.get("source_key", f"penn_fudan:{obj['object_id']}")


def object_geometry(obj: Mapping[str, Any], *, annotation_format: str, mask: str = "absent") -> ObjectGeometry:
    """Provenance of each geometry of one normalized annotation."""
    bbox = obj.get("bbox")
    origin = obj.get("bbox_origin")
    if bbox is None:
        box = "absent"
    elif origin == "derived_from_polygon":
        box = "derived_from_polygon"
    elif origin == "supplied" or annotation_format == "penn_fudan":
        box = "supplied"
    else:
        box = "absent"
    return ObjectGeometry(box=box, polygon="supplied" if obj.get("polygons") else "absent", mask=mask)


def _has(geometry: ObjectGeometry, kind: GeometryKind) -> bool:
    # Pixel masks are never shown to the visual check, so a mask is not usable evidence for it.
    return {"box": geometry.box == "supplied", "polygon": geometry.polygon == "supplied", "mask": False}[kind]


def _missing_detail(geometry: ObjectGeometry, missing: Sequence[GeometryKind]) -> str:
    parts = []
    for kind in missing:
        if kind == "box":
            parts.append("has no supplied bounding box" + (
                "; the box shown for it is derived from its polygon and is not an annotation"
                if geometry.box == "derived_from_polygon" else ""))
        elif kind == "polygon":
            parts.append("has no supplied polygon")
        else:
            parts.append({"supplied_not_shown": "has a supplied mask, which the visual check cannot see",
                          "referenced_unavailable": "has a mask file that is referenced but missing or unreadable",
                          "unsupported_encoding": "has a mask in an encoding that is kept but not decoded",
                          "absent": "has no supplied mask"}[geometry.mask])
    return ("; ".join(parts) or "lacks the required geometry").capitalize()[:400]


def _unavailable(rule_geometry: Sequence[GeometryKind], objects: list[ScopeObject],
                 ineligible: list[IneligibleObject]) -> ScopeUnavailable:
    required = _names(list(rule_geometry)).replace(" and ", " or ") if rule_geometry else "geometry"
    listing = "; ".join(f"{o.object_key} ({o.label or 'unlabelled'})" for o in ineligible[:6])
    suffix = f" Ineligible annotations: {listing}." if listing else ""
    if "mask" in rule_geometry and len(rule_geometry) == 1:
        states = {o.geometry.mask for o in objects}
        code = ("mask_encoding_unsupported" if "unsupported_encoding" in states else
                "mask_referenced_unavailable" if "referenced_unavailable" in states else
                "mask_not_visible_to_visual_check" if "supplied_not_shown" in states else "mask_not_supplied")
        text = {"mask_encoding_unsupported": "A mask is present only in an encoding that is kept but not decoded.",
                "mask_referenced_unavailable": "A mask file is referenced by the sample but is missing or unreadable.",
                "mask_not_visible_to_visual_check": "Masks were supplied, but the visual check is not shown pixel masks.",
                "mask_not_supplied": "No mask was supplied for this sample."}[code]
        return ScopeUnavailable(code=code, detail=f"{text} This requirement needs a supplied mask.{suffix}"[:400])
    if "box" in rule_geometry and any(o.geometry.box == "derived_from_polygon" for o in objects) \
            and not any(o.geometry.box == "supplied" for o in objects):
        return ScopeUnavailable(
            code="supplied_box_not_available",
            detail=("No supplied bounding box exists in this sample. The boxes shown are derived from "
                    f"polygons and are not annotations, so this requirement was not checked.{suffix}")[:400])
    return ScopeUnavailable(
        code="no_eligible_annotation",
        detail=f"No annotation has the supplied {required} this requirement judges.{suffix}"[:400])


def resolve_scope(rule, objects: Sequence[Mapping[str, Any]], *, image: ScopeImage, annotation_format: str,
                  masks: Mapping[str, str] | None = None, algorithm: str = APPLICABILITY_ALGORITHM) -> TargetScope:
    """Eligible targets, ineligible context and, when nothing is eligible, the reason."""
    applicability, origin = effective_applicability(rule, algorithm)
    masks = masks or {}
    scope_objects = []
    for obj in objects:
        key = object_key(obj)
        scope_objects.append(ScopeObject(
            object_key=key, object_id=int(obj["object_id"]), label=str(obj.get("label") or "")[:255],
            geometry=object_geometry(obj, annotation_format=annotation_format, mask=masks.get(key, "absent"))))
    eligible: list[ScopeObject] = []
    ineligible: list[IneligibleObject] = []
    for item in scope_objects:
        needed = list(applicability.geometry)
        have = [kind for kind in needed if _has(item.geometry, kind)]
        ok = (not needed) or (bool(have) if applicability.combine == "any" else len(have) == len(needed))
        if ok:
            eligible.append(item)
        else:
            missing = [kind for kind in needed if kind not in have]
            ineligible.append(IneligibleObject(**item.model_dump(), missing=missing,
                                               detail=_missing_detail(item.geometry, missing)))
    scope = TargetScope(image=image, applicability=applicability, applicability_origin=origin,
                        eligible=eligible, ineligible=ineligible)
    if MEASURED_VALUE.search(rule.description) or (rule.source_sentence and MEASURED_VALUE.search(rule.source_sentence)):
        scope.eligible, scope.ineligible = [], [IneligibleObject(**o.model_dump(), missing=[], detail=
            "Context only: the requirement states a measured value.") for o in scope_objects]
        scope.unavailable = ScopeUnavailable(
            code="measurement_not_supported",
            detail=("The requirement states a numeric measurement. A visual model cannot measure exact "
                    "distances, and no measurement check implements this requirement, so it was not judged."))
    elif not scope_objects:
        scope.unavailable = ScopeUnavailable(code="no_annotations", detail="The sample has no annotations to check.")
    elif not eligible:
        scope.unavailable = _unavailable(applicability.geometry, scope_objects, ineligible)
    return scope


_UNSUPPLIED_BOX = re.compile(r"\b(?:supplied|annotated|given)\s+(?:bounding[\s-]*)?box(?:es)?\b|\bbounding[\s-]*box(?:es)?\b",
                             re.IGNORECASE)
_DERIVED = re.compile(r"\bderived\b", re.IGNORECASE)
_BLUR = re.compile(r"\b(?:blur\w*|out[\s-]of[\s-]focus)\b", re.IGNORECASE)
_HIDDEN = re.compile(r"\b(?:hidden|occlud\w*|obscur\w*|not\s+visible|invisible|cut\s+off|truncat\w*)\b", re.IGNORECASE)
_OBLIGATION = re.compile(r"\b(?:should|must|needs?|missing|fails?\s+to|does\s+not\s+include|omits?)\b", re.IGNORECASE)
DOWNGRADING_FLAGS = {"geometry_not_supplied", "geometry_not_in_requirement", "reason_cites_unsupplied_box",
                     "blur_offered_as_evidence"}


def _grounding_flags(assessment, target: ScopeObject, applicability: RuleApplicability) -> list[str]:
    flags = []
    judged = assessment.geometry
    if (judged == "box" and target.geometry.box != "supplied") or (
            judged == "polygon" and target.geometry.polygon != "supplied"):
        flags.append("geometry_not_supplied")
    elif applicability.geometry and judged not in applicability.geometry:
        flags.append("geometry_not_in_requirement")
    text = assessment.reason
    if (assessment.outcome == "fail" and target.geometry.box != "supplied"
            and _UNSUPPLIED_BOX.search(text) and not _DERIVED.search(text)):
        flags.append("reason_cites_unsupplied_box")
    if assessment.outcome == "fail" and _BLUR.search(text) and _HIDDEN.search(text) and _OBLIGATION.search(text):
        flags.append("blur_offered_as_evidence")
    return flags


def combine_results(scope: TargetScope, response) -> tuple[str, str, TargetScope]:
    """Sample-level (outcome, reason, scope) from the model's per-target assessments.

    Only assessments of eligible targets count. A target the model did not assess, an assessment
    whose geometry was never supplied, and an unclear condition all become ``unsure``. A target
    whose own condition (for example occlusion) is absent is excluded. The model's own overall
    outcome is kept for audit but never overrides the per-target results.
    """
    by_key = {o.object_key: o for o in scope.eligible}
    seen: set[str] = set()
    rejected: list[str] = []
    results: list[TargetResult] = []
    taken = {}
    for assessment in response.assessments:
        key = assessment.object_key
        if key in seen:
            rejected.append(f"{key[:120]}: repeated assessment ignored")
            # A contradictory repeated answer cannot leave the first answer authoritative.
            taken[key] = None
        elif key not in by_key:
            context = any(o.object_key == key for o in scope.ineligible)
            rejected.append(f"{key[:120]}: {'context annotation, not a target' if context else 'unknown annotation'}")
        else:
            taken[key] = assessment
        seen.add(key)
    for target in scope.eligible:
        assessment = taken.get(target.object_key)
        if assessment is None:
            results.append(TargetResult(object_key=target.object_key, outcome="unsure", flags=["no_assessment"]))
            continue
        flags = _grounding_flags(assessment, target, scope.applicability)
        outcome = assessment.outcome
        if assessment.condition == "not_present":
            flags.append("condition_not_present")
            outcome = "unavailable"
        elif assessment.condition == "unclear":
            flags.append("condition_unclear")
            outcome = "unsure" if outcome in ("pass", "fail") else outcome
        if outcome in ("pass", "fail") and DOWNGRADING_FLAGS & set(flags):
            outcome = "unsure"
        results.append(TargetResult(
            object_key=target.object_key, outcome=outcome, model_outcome=assessment.outcome,
            geometry=assessment.geometry, condition=assessment.condition, reason=assessment.reason, flags=flags))
    return _finalize(scope, results, rejected, response.outcome)


def _finalize(scope: TargetScope, results: list[TargetResult], rejected: list[str],
              reported_outcome: str | None, extra_notes: Sequence[str] = ()) -> tuple[str, str, TargetScope]:
    """Sample outcome, reason and scope from per-target results (shared by every protocol)."""
    counted = [r for r in results if "condition_not_present" not in r.flags]
    unavailable = None
    if not counted:
        final = "unavailable"
        unavailable = {"code": "condition_not_present",
                       "detail": "The requirement's own condition was reported absent for every eligible annotation."}
    elif any(r.outcome == "fail" for r in counted):
        final = "fail"
    elif any(r.outcome == "unsure" for r in counted):
        final = "unsure"
    elif any(r.outcome == "unavailable" for r in counted):
        final = "unavailable"
        unavailable = {"code": "no_valid_assessment",
                       "detail": "The model could not establish the requirement for every applicable annotation."}
    elif any(r.outcome == "pass" for r in counted):
        final = "pass"
    else:
        final = "unavailable"
        unavailable = {"code": "no_valid_assessment",
                       "detail": "The model could not establish the requirement for any eligible annotation."}
    labels = {o.object_key: o.label for o in scope.eligible}
    lines = []
    for r in results:
        name = f"{labels.get(r.object_key) or 'unlabelled'} {r.object_key}"
        line = f"{name}: {r.outcome}" + (f" - {r.reason}" if r.reason else "")
        if r.flags:
            line += f" [{', '.join(r.flags)}]"
        lines.append(line)
    reason = "; ".join(lines) or "No eligible annotation was assessed."
    if len(reason) > 1024:
        reason = reason[:1023] + "…"
    update = {"results": results, "rejected_references": rejected[:32],
              "model_reported_outcome": reported_outcome, "outcome_source": "derived_from_target_results"}
    if unavailable is not None:
        update["unavailable"] = ScopeUnavailable(**unavailable)
    notes = list(scope.notes)
    if reported_outcome is not None and reported_outcome != final:
        notes.append(f"The model reported {reported_outcome}; the outcome follows its per-target results."[:400])
    notes.extend(extra_notes)
    return final, reason, scope.model_copy(update={**update, "notes": notes})


@dataclass(frozen=True)
class FocusedCall:
    """The outcome of ONE per-target request (protocol v6).

    Exactly one of ``assessment``, ``failure`` or ``answered_for`` describes what came back:
    a validated FocusedAssessment for the requested key; an execution/validation failure code
    (no trusted answer was obtained); or the key the model answered for when it was not the key asked about.
    """
    object_key: str
    assessment: Any | None = None
    failure: str | None = None
    answered_for: str | None = None
    duration_ms: float = 0.0


_COMPLIANCE_OUTCOME = {"yes": "pass", "no": "fail", "unclear": "unsure"}


def combine_focused(scope: TargetScope, calls: Sequence[FocusedCall]) -> tuple[str, str, TargetScope]:
    """Sample-level (outcome, reason, scope) from per-target requests; every eligible target gets a result.

    The outcome of a target is derived from the model's structured fields, never from its prose:
    condition not_present excludes the target; an unclear condition or unclear compliance is ``unsure``;
    otherwise compliance yes/no is pass/fail. A target whose request failed is ``unavailable`` (so an
    applicable target without a trusted answer can never allow an overall pass) and carries the failure code;
    a target answered under a different key is ``unsure`` with ``no_assessment`` and the reference is rejected.
    A failure on one target never discards a valid failure on another.
    """
    by_key: dict[str, list[FocusedCall]] = {}
    for call in calls:
        by_key.setdefault(call.object_key, []).append(call)
    known = {o.object_key for o in scope.eligible}
    rejected = [f"{key[:120]}: not an eligible target" for key in by_key if key not in known]
    results: list[TargetResult] = []
    for target in scope.eligible:
        entries = by_key.get(target.object_key, [])
        if len(entries) != 1:
            rejected.append(f"{target.object_key[:120]}: {'repeated' if entries else 'missing'} request result ignored")
            results.append(TargetResult(object_key=target.object_key, outcome="unsure", flags=["no_assessment"]))
            continue
        call = entries[0]
        if call.failure is not None:
            results.append(TargetResult(object_key=target.object_key, outcome="unavailable",
                                        flags=["call_failed", f"call:{call.failure}"[:60]]))
            continue
        if call.assessment is None or call.answered_for is not None:
            if call.answered_for is not None:
                rejected.append(f"{call.answered_for[:120]}: answered where {target.object_key[:100]} was requested")
            results.append(TargetResult(object_key=target.object_key, outcome="unsure",
                                        flags=["no_assessment", "reference_mismatch"] if call.answered_for else ["no_assessment"]))
            continue
        a = call.assessment
        flags: list[str] = []
        if a.condition == "not_present":
            outcome = model_outcome = "unavailable"
            flags.append("condition_not_present")
        elif a.condition == "unclear":
            outcome = model_outcome = "unsure"
            flags.append("condition_unclear")
        else:
            outcome = model_outcome = _COMPLIANCE_OUTCOME[a.compliance]
        flags += _grounding_flags(SimpleNamespace(geometry=a.geometry, reason=a.reason, outcome=outcome), target,
                                  scope.applicability)
        if outcome in ("pass", "fail") and DOWNGRADING_FLAGS & set(flags):
            outcome = "unsure"
        results.append(TargetResult(object_key=target.object_key, outcome=outcome, model_outcome=model_outcome,
                                    geometry=a.geometry, condition=a.condition, reason=a.reason, flags=flags))
    timing = "; ".join(f"{c.object_key} {c.failure or ('mismatch' if c.answered_for else 'ok')} {c.duration_ms / 1000:.0f}s"
                       for c in calls)
    return _finalize(scope, results, rejected[:32], None, [f"One request per target: {timing}"[:400]])


def keys_needing_review(scope: TargetScope | None) -> list[str]:
    """Object keys whose result was fail or unsure; the only candidates for a proposed correction."""
    if scope is None:
        return []
    return [r.object_key for r in scope.results if r.outcome in ("fail", "unsure")]
