"""Compact extraction responses expanded into source-linked public proposals."""

import json
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..checks.registry import (
    RuleParameterError, extraction_check_catalog, get_check_definition,
    validate_check_parameters,
)
from ..models.json_value import FiniteJsonValue
from ..models.rule import RuleProposal


SourceID = Annotated[int, Field(ge=1)]


class _CompactRule(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    source_id: SourceID
    context_ids: list[SourceID]
    check_id: int = Field(ge=0)
    parameters: dict[str, FiniteJsonValue]


class _CompactResponse(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

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

    Generation constrains field names, reference ranges and parameter value
    types. The selected check's exact required/forbidden parameters are still
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
        'type': 'object', 'additionalProperties': False, 'required': ['rules'],
        'properties': {'rules': {'type': 'array', 'items': {
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
        }}},
    }


def expand_response(content: str, sources: list[str]) -> list[RuleProposal]:
    """Copy source text locally; the model cannot invent or rewrite citations."""
    response = _CompactResponse.model_validate_json(content)
    catalog = extraction_catalog()
    proposals = []
    seen = set()
    for item in response.rules:
        references = [item.source_id, *item.context_ids]
        if (any(reference > len(sources) for reference in references)
                or len(set(references)) != len(references)):
            raise ValueError('Invalid or repeated source reference.')
        if item.check_id > len(catalog):
            raise ValueError('Unknown check reference.')
        identity = (item.source_id, item.check_id, json.dumps(item.parameters, sort_keys=True))
        if identity in seen:
            raise ValueError('Duplicate rule mapping.')
        seen.add(identity)
        check_name = None if item.check_id == 0 else catalog[item.check_id - 1]['check_name']
        proposals.append(RuleProposal(
            rule_id=f'RULE-{len(proposals) + 1}',
            description='\n'.join(sources[reference - 1] for reference in references),
            check_name=check_name, parameters=item.parameters,
            source_sentence=sources[item.source_id - 1],
        ))
    return _RuleResponse(rules=proposals).rules
