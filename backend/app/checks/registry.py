"""Describe the small, explicit set of checks the rule evaluator can execute.

Rules are data, not Python code.  This registry is deliberately boring: every
supported check has a fixed evidence scope and a fixed Pydantic parameter
schema.  A rule with a name that is not present here is still a valid rulebook
record, but the evaluator reports it as unavailable instead of guessing how to
execute it.
"""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)


def _strict_finite_number(value: object) -> float:
    """Accept JSON numbers, but not booleans or numeric strings."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("must be a finite JSON number")
    result = float(value)
    if result != result or result in (float("inf"), float("-inf")):
        raise ValueError("must be a finite JSON number")
    return result


def _strict_nonnegative_integer(value: object) -> int:
    if type(value) is not int:
        raise ValueError("must be an integer")
    return value


FiniteNumber = Annotated[float, BeforeValidator(_strict_finite_number)]
NonnegativeInteger = Annotated[int, BeforeValidator(_strict_nonnegative_integer)]


class _RuleParameters(BaseModel):
    """Base class for all parameter documents accepted by executable checks."""

    model_config = ConfigDict(extra="forbid", strict=True)


class EmptyParameters(_RuleParameters):
    """Checks whose semantics do not require a threshold or label list."""


class AllowedLabelsParameters(_RuleParameters):
    allowed_labels: list[Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]] = Field(
        min_length=1,
    )

    @field_validator("allowed_labels")
    @classmethod
    def require_unique_labels(cls, labels: list[str]) -> list[str]:
        if len(labels) != len(set(labels)):
            raise ValueError("allowed_labels must not contain duplicates")
        return labels


class MaximumIoUParameters(_RuleParameters):
    max_iou: FiniteNumber = Field(ge=0, le=1)


class MaximumEdgeDifferenceParameters(_RuleParameters):
    max_edge_difference: NonnegativeInteger = Field(ge=0)


class MaximumOutsideFractionParameters(_RuleParameters):
    max_outside_fraction: FiniteNumber = Field(ge=0, le=1)


class FillFractionParameters(_RuleParameters):
    min_fill_fraction: FiniteNumber = Field(ge=0, le=1)
    max_fill_fraction: FiniteNumber = Field(ge=0, le=1)

    @model_validator(mode="after")
    def require_ordered_bounds(self) -> "FillFractionParameters":
        if self.min_fill_fraction > self.max_fill_fraction:
            raise ValueError("min_fill_fraction must not exceed max_fill_fraction")
        return self


class MaximumComponentCountParameters(_RuleParameters):
    max_component_count: NonnegativeInteger = Field(ge=1)


@dataclass(frozen=True)
class CheckDefinition:
    """Execution metadata for one supported measurement check."""

    name: str
    scope: Literal["sample", "object", "pair"]
    parameter_model: type[_RuleParameters]


CHECK_REGISTRY: dict[str, CheckDefinition] = {
    "allowed_labels": CheckDefinition("allowed_labels", "object", AllowedLabelsParameters),
    "annotation_ids": CheckDefinition("annotation_ids", "sample", EmptyParameters),
    "mask_ids": CheckDefinition("mask_ids", "sample", EmptyParameters),
    "mask_dimensions": CheckDefinition("mask_dimensions", "sample", EmptyParameters),
    "annotation_metadata": CheckDefinition("annotation_metadata", "sample", EmptyParameters),
    "box_validity": CheckDefinition("box_validity", "object", EmptyParameters),
    "box_overlaps": CheckDefinition("box_overlaps", "pair", MaximumIoUParameters),
    "box_mask_alignment": CheckDefinition(
        "box_mask_alignment", "object", MaximumEdgeDifferenceParameters,
    ),
    "mask_outside_box": CheckDefinition(
        "mask_outside_box", "object", MaximumOutsideFractionParameters,
    ),
    "mask_box_fill": CheckDefinition("mask_box_fill", "object", FillFractionParameters),
    "mask_components": CheckDefinition(
        "mask_components", "object", MaximumComponentCountParameters,
    ),
}


class RuleParameterError(ValueError):
    """A confirmed rule uses an invalid parameter document."""

    def __init__(self, check_name: str, message: str) -> None:
        self.check_name = check_name
        super().__init__(f"Invalid parameters for check {check_name!r}: {message}")


def get_check_definition(check_name: str | None) -> CheckDefinition | None:
    """Return a definition, or ``None`` for an absent/unsupported check."""
    if check_name is None:
        return None
    return CHECK_REGISTRY.get(check_name)


def validate_check_parameters(
    check_name: str | None, parameters: dict[str, object],
) -> _RuleParameters | None:
    """Validate parameters without changing the caller's mutable dictionary.

    Unsupported names intentionally skip schema validation.  Their parameter
    values were already constrained to finite JSON by ``Rule`` and are copied
    into the finding snapshot, but there is no executable interpretation to
    validate against.
    """
    definition = get_check_definition(check_name)
    if definition is None:
        return None
    try:
        return definition.parameter_model.model_validate(dict(parameters))
    except ValidationError as error:
        raise RuleParameterError(check_name or "", str(error)) from error
