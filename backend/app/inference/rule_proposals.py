"""Compact extraction responses expanded into source-linked public proposals.

Representation of requirements and their context
-------------------------------------------------
Every nonblank guideline line is a source unit with a request-local ID. The model
classifies EVERY line and maps requirement lines to checks:

* ``requirement``: the line states an obligation (it becomes one or more rules);
* ``context``: the line only defines terms, scope, exceptions, conditions or how
  unclear evidence is reported (it never becomes a rule by itself);
* ``requirement_and_context``: both at once (rules, and context for other rules);
* ``other``: headings or text without annotation content.

The backend then builds each rule from the original text only:

* ``description`` and ``source_sentence`` are the requirement line itself;
* ``context_sentences`` are the exact lines the model attached through
  ``context_ids`` plus, for visual (model-assisted) and unsupported rules, every
  line classified as context. Document-wide context is attached deterministically,
  so a model that forgets to cite the scope, a class definition or the uncertainty
  policy cannot silently drop it from a visual check;
* a requirement whose meaning the selected check cannot execute is kept with
  ``check_name`` null and an ``extraction_notes`` explanation, never removed;
* lines the model leaves unclassified, and requirement lines without a mapping,
  become unsupported rules with a note, so an incomplete response is never
  presented as complete coverage.

Responses with invalid references, contradictory classifications, unknown checks
or malformed parameters are rejected as before.
"""

import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..checks.registry import (
    CHECK_REGISTRY, RuleParameterError, extraction_check_catalog, get_check_definition,
    validate_check_parameters,
)
from ..models.json_value import FiniteJsonValue
from ..models.rule import RuleProposal
from .extraction_grounding import explicit_vocabulary, grounded_numeric_parameters, measured_value


SourceID = Annotated[int, Field(ge=1)]
LineRole = Literal["requirement", "context", "requirement_and_context", "other"]
REQUIREMENT_ROLES = {"requirement", "requirement_and_context"}
CONTEXT_ROLES = {"context", "requirement_and_context"}

# An explicit measured value: a number followed by a measurement unit. Visual
# model checks cannot measure such values, so they are never mapped to them.
_MEASURED_VALUE = re.compile(
    r"(?<![\w.])\d+(?:[.,]\d+)?\s*(?:px\b|pixels?\b|%|per\s*cent\b|percent\b|degrees?\b|°|"
    r"mm\b|cm\b|millimet(?:er|re)s?\b|centimet(?:er|re)s?\b|met(?:er|re)s?\b|"
    r"milliseconds?\b|seconds?\b|ms\b|fps\b|frames?\b)",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(\s*(?:%|percent\b|per\s*cent\b))?", re.IGNORECASE)


class _CompactLine(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    source_id: SourceID
    role: LineRole


class _CompactRule(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    source_id: SourceID
    context_ids: list[SourceID]
    check_id: int = Field(ge=0)
    parameters: dict[str, FiniteJsonValue]


class _CompactResponse(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    lines: list[_CompactLine]
    rules: list[_CompactRule]


class _RuleResponse(BaseModel):
    """Independent validation after references have been resolved."""

    model_config = ConfigDict(extra='forbid', strict=True)
    rules: list[RuleProposal]

    @model_validator(mode='after')
    def validate_check_mappings(self) -> '_RuleResponse':
        for proposal in self.rules:
            if proposal.check_name is None:
                if proposal.parameters:
                    raise ValueError('An unmapped requirement must have empty parameters.')
                continue
            if get_check_definition(proposal.check_name) is None:
                raise ValueError('Unknown supported-check name.')
            try:
                validate_check_parameters(proposal.check_name, proposal.parameters)
            except RuleParameterError as error:
                raise ValueError(str(error)) from error
        return self


def source_units(text: str) -> list[str]:
    """Address nonblank source lines without changing their text or punctuation.

    A one-line paragraph remains a single source unit; it can support several
    check mappings. Line references are private to this one extraction request.
    """
    return [line for line in text.splitlines() if line.strip()]


def extraction_catalog() -> list[dict[str, Any]]:
    return [{'check_id': index, **entry}
            for index, entry in enumerate(extraction_check_catalog(), start=1)]


def compact_response_schema(source_count: int) -> dict[str, Any]:
    """Use one small object shape, without duplicating it for every check.

    Generation constrains field names, reference ranges, line roles and parameter
    value types. The selected check's exact required/forbidden parameters are still
    enforced independently by the executable registry after decoding.
    """
    catalog = extraction_catalog()
    parameters = {}
    for entry in catalog:
        for name, definition in entry['parameters_schema'].get('properties', {}).items():
            if name in parameters and parameters[name] != definition:
                raise ValueError('Conflicting registry parameter definitions.')
            parameters[name] = definition
    reference = {'type': 'integer', 'minimum': 1, 'maximum': source_count}
    return {
        'type': 'object', 'additionalProperties': False, 'required': ['lines', 'rules'],
        'properties': {
            'lines': {'type': 'array', 'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['source_id', 'role'],
                'properties': {'source_id': reference,
                               'role': {'type': 'string', 'enum': list(LineRole.__args__)}},
            }},
            'rules': {'type': 'array', 'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['source_id', 'context_ids', 'check_id', 'parameters'],
                'properties': {
                    'source_id': reference,
                    'context_ids': {'type': 'array', 'items': dict(reference)},
                    'check_id': {'type': 'integer', 'minimum': 0, 'maximum': len(catalog)},
                    'parameters': {
                        'type': 'object', 'additionalProperties': False,
                        'properties': parameters,
                    },
                },
            }},
        },
    }


def _mentions(text: str, term: str) -> bool:
    return re.search(r'(?<![a-z])' + re.escape(term.lower()), text.lower()) is not None


def _stated_numbers(text: str) -> set[float]:
    """Numbers written in the text; percentages also count as their fraction."""
    values = set()
    for number, percent in _NUMBER.findall(text):
        value = float(number)
        values.add(value)
        if percent:
            values.add(round(value / 100, 12))
    return values


def guard_mapping(check_name: str | None, parameters: dict[str, Any], requirement: str,
                  context: list[str]) -> tuple[str | None, dict[str, Any], list[str]]:
    """Retain only mappings grounded in the corresponding source requirement."""
    definition = get_check_definition(check_name)
    if definition is None:
        return check_name, parameters, []
    stated = '\n'.join([requirement, *context])
    measured = measured_value(stated)
    if definition.model_assisted and measured:
        return None, {}, [
            f"The requirement or its context states an exact measured value ('{measured}'). "
            f"{check_name} is a visual model judgement and cannot measure it, so the requirement "
            "is kept unsupported for review. No existing check was substituted."]
    if (check_name != 'allowed_labels' and definition.evidence_terms
            and not any(_mentions(stated, term) for term in definition.evidence_terms)):
        return None, {}, [
            f"{check_name} was not accepted: the requirement does not mention what that check "
            f"measures (expected one of: {', '.join(definition.evidence_terms)}). Kept unsupported for review."]
    if check_name == 'allowed_labels' and not explicit_vocabulary(requirement, parameters['allowed_labels']):
        return None, {}, [
            "allowed_labels was not accepted: a complete explicit permitted vocabulary with "
            "these exact names is not stated in the requirement itself. Examples and partial "
            "name matches do not define an allowed-label list. Kept unsupported for review."]
    if not grounded_numeric_parameters(check_name, parameters, requirement, context):
        return None, {}, [
            f"{check_name} was not accepted: the parameter value with its matching quantity, "
            "comparison direction and units is not stated unambiguously in the guideline text. "
            "List numbers and unrelated measurements are not thresholds. Kept unsupported for review."]
    return check_name, parameters, []


def expand_response(content: str, sources: list[str]) -> list[RuleProposal]:
    """Copy source text locally; the model cannot invent or rewrite citations."""
    response = _CompactResponse.model_validate_json(content)
    catalog = extraction_catalog()
    count = len(sources)

    roles: dict[int, str] = {}
    for line in response.lines:
        if line.source_id > count or line.source_id in roles:
            raise ValueError('Invalid or repeated line classification.')
        roles[line.source_id] = line.role
    document_context = sorted(i for i, role in roles.items() if role in CONTEXT_ROLES)

    drafts = []
    seen = set()
    for order, item in enumerate(response.rules):
        references = [item.source_id, *item.context_ids]
        if any(reference > count for reference in references) or len(set(references)) != len(references):
            raise ValueError('Invalid or repeated source reference.')
        if item.check_id > len(catalog):
            raise ValueError('Unknown check reference.')
        role = roles.get(item.source_id)
        if role is not None and role not in REQUIREMENT_ROLES:
            raise ValueError('A rule cites a line classified as context or other text.')
        identity = (item.source_id, item.check_id, json.dumps(item.parameters, sort_keys=True))
        if identity in seen:
            raise ValueError('Duplicate rule mapping.')
        seen.add(identity)
        check_name = None if item.check_id == 0 else catalog[item.check_id - 1]['check_name']
        # Malformed output is rejected before any guard can soften it to "unsupported".
        if check_name is None and item.parameters:
            raise ValueError('An unmapped requirement must have empty parameters.')
        try:
            validate_check_parameters(check_name, item.parameters)
        except RuleParameterError as error:
            raise ValueError(str(error)) from error
        definition = get_check_definition(check_name)
        guard_context = set(item.context_ids)
        if definition is None or definition.model_assisted:
            guard_context.update(document_context)
        guard_context.discard(item.source_id)
        cited = [sources[i - 1] for i in sorted(guard_context)]
        check_name, parameters, notes = guard_mapping(check_name, item.parameters, sources[item.source_id - 1], cited)
        drafts.append({'order': (item.source_id, order), 'source_id': item.source_id,
                       'context_ids': list(item.context_ids), 'check_name': check_name,
                       'parameters': parameters, 'notes': notes})

    mapped = {draft['source_id'] for draft in drafts}
    for index in range(1, count + 1):
        role = roles.get(index)
        if role in REQUIREMENT_ROLES and index not in mapped:
            note = ('Classified as a requirement, but no check mapping was returned. Kept unsupported '
                    'so the reviewer can decide how it is checked.')
        elif role is None and index not in mapped:
            note = ('The model did not classify this line. Kept as an unsupported requirement so the '
                    'reviewer can decide whether it states a requirement. A context reference does '
                    'not establish that this line contains no separate obligation.')
        else:
            continue
        drafts.append({'order': (index, len(drafts) + len(sources)), 'source_id': index, 'context_ids': [],
                       'check_name': None, 'parameters': {}, 'notes': [note]})

    merged: dict[tuple, dict] = {}
    for draft in sorted(drafts, key=lambda d: d['order']):
        key = (draft['source_id'], draft['check_name'], json.dumps(draft['parameters'], sort_keys=True))
        if key in merged:      # e.g. two mappings of one line both kept unsupported
            target = merged[key]
            target['context_ids'] = sorted(set(target['context_ids']) | set(draft['context_ids']))
            target['notes'] += [note for note in draft['notes'] if note not in target['notes']]
        else:
            merged[key] = draft

    proposals = []
    for draft in merged.values():
        definition = get_check_definition(draft['check_name'])
        attach_document = definition is None or definition.model_assisted
        context = set(draft['context_ids']) | (set(document_context) if attach_document else set())
        context.discard(draft['source_id'])
        requirement = sources[draft['source_id'] - 1]
        sentences = []
        for index in sorted(context):
            sentence = sources[index - 1]
            if sentence != requirement and sentence not in sentences:
                sentences.append(sentence)
        proposals.append(RuleProposal(
            rule_id=f'RULE-{len(proposals) + 1}', description=requirement,
            check_name=draft['check_name'], parameters=draft['parameters'],
            source_sentence=requirement, context_sentences=sentences, extraction_notes=draft['notes'],
        ))
    return _RuleResponse(rules=proposals).rules


def model_assisted_checks() -> list[str]:
    return sorted(name for name, definition in CHECK_REGISTRY.items() if definition.model_assisted)
