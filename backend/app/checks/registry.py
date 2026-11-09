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


class PersonCoverageParameters(_RuleParameters):
    """Tolerance for comparing an annotation with the detected person: max(px, fraction x person height)."""

    min_tolerance_px: NonnegativeInteger = Field(default=8, ge=0)
    tolerance_fraction: FiniteNumber = Field(default=0.04, ge=0, le=1)


class UnannotatedPeopleParameters(_RuleParameters):
    """Which detected people must have an annotation: at least this tall, found with at least this score."""

    min_person_height_px: NonnegativeInteger = Field(default=40, ge=0)
    min_score: FiniteNumber = Field(default=0.9, ge=0.5, le=1)


@dataclass(frozen=True)
class CheckDefinition:
    """Execution metadata for one supported measurement check.

    ``model_assisted`` marks visual judgements made by a local vision model; they
    cannot measure exact numeric quantities. ``evidence_terms`` lists words, at
    least one of which an extracted requirement must contain before it can be
    mapped to this check; it guards against substituting a different measured
    quantity (for example box-versus-mask for box-versus-visible-person).
    """

    name: str
    scope: Literal["sample", "object", "pair"]
    parameter_model: type[_RuleParameters]
    prompt_description: str = ""
    model_assisted: bool = False
    evidence_terms: tuple[str, ...] = ()


_MASK_TERMS = ("mask", "segmentation")


CHECK_REGISTRY: dict[str, CheckDefinition] = {
    "polygon_validity": CheckDefinition(
        "polygon_validity", "object", EmptyParameters,
        "Supplied polygons must have at least three distinct vertices, nonzero area, no non-adjacent edge intersections or repeated vertices, and remain within image bounds. Only apply when the guidelines require these structural properties; this does not judge visual fit.",
    ),
    "annotation_guideline": CheckDefinition(
        "annotation_guideline", "sample", EmptyParameters,
        "Model-assisted judgment of one explicit visual annotation guideline against an image and parsed annotations. Not a fallback for numeric checks, motion, unseen facts or unsupported evidence.",
        model_assisted=True,
    ),
    "pedestrian_box_enclosure": CheckDefinition(
        "pedestrian_box_enclosure", "sample", EmptyParameters,
        "Model-assisted judgment that each annotation box encloses a visible pedestrian, using one marked context view per box. Opt-in; a box entirely on background is a violation. Not a tightness, size or completeness check.",
        model_assisted=True,
    ),
    "person_coverage": CheckDefinition(
        "person_coverage", "object", PersonCoverageParameters,
        "Each person annotation (box, mask or polygon) must cover the whole person, including head and feet, "
        "without large extra background. Measured against a person outline detected in the image, with a pixel "
        "tolerance; reports where the annotation differs.",
        evidence_terms=("whole", "entire", "complete", "head", "feet", "foot", "cover", "contain", "include",
                        "background", "outline"),
    ),
    "unannotated_people": CheckDefinition(
        "unannotated_people", "sample", UnannotatedPeopleParameters,
        "Every clearly visible person in the image must have an annotation. Measured: people found in the image "
        "by a local person model are matched to the annotations, and each person with no annotation is reported "
        "with where it is. Use for missing annotations, not for how well an annotation fits.",
        # Phrases that require an annotation to exist. Not "every person" (coverage rules start that way), not
        # "may be annotated" ("Only real people may be annotated" restricts annotations, it does not demand them)
        # and not "must be labelled" ("must be labelled person, never car" is about which label).
        evidence_terms=("unannotated", "missing annotation", "missing person", "missing people", "left without",
                        "without an annotation", "without annotation", "must have an annotation",
                        "should have an annotation", "must each have an annotation", "needs an annotation",
                        "need an annotation", "must be annotated", "should be annotated", "shall be annotated",
                        "not annotated"),
    ),
    "allowed_labels": CheckDefinition(
        "allowed_labels", "object", AllowedLabelsParameters,
        "Each annotation label must be one of the explicitly configured allowed labels.",
        evidence_terms=("allow", "permit", "only", "one of", "valid", "vocabulary", "accepted", "label set", "list of"),
    ),
    "annotation_ids": CheckDefinition(
        "annotation_ids", "sample", EmptyParameters,
        "Annotation object IDs must be positive and unique.",
    ),
    "mask_ids": CheckDefinition(
        "mask_ids", "sample", EmptyParameters,
        "The non-background mask IDs must match the annotation object IDs.",
        evidence_terms=_MASK_TERMS,
    ),
    "mask_dimensions": CheckDefinition(
        "mask_dimensions", "sample", EmptyParameters,
        "The image and mask dimensions must match.",
        evidence_terms=_MASK_TERMS,
    ),
    "annotation_metadata": CheckDefinition(
        "annotation_metadata", "sample", EmptyParameters,
        "Declared annotation metadata must match the available image and annotation measurements.",
    ),
    "box_validity": CheckDefinition(
        "box_validity", "object", EmptyParameters,
        "Each annotation box must be valid and lie within the image bounds.",
    ),
    "box_overlaps": CheckDefinition(
        "box_overlaps", "pair", MaximumIoUParameters,
        "Each pair of annotation boxes must have IoU no greater than the configured maximum.",
        evidence_terms=("overlap", "iou", "intersection"),
    ),
    "box_mask_alignment": CheckDefinition(
        "box_mask_alignment", "object", MaximumEdgeDifferenceParameters,
        "Each box-to-mask edge difference must be no greater than the configured maximum pixels.",
        evidence_terms=_MASK_TERMS,
    ),
    "mask_outside_box": CheckDefinition(
        "mask_outside_box", "object", MaximumOutsideFractionParameters,
        "The fraction of each mask outside its annotation box must be no greater than the configured maximum.",
        evidence_terms=_MASK_TERMS,
    ),
    "mask_box_fill": CheckDefinition(
        "mask_box_fill", "object", FillFractionParameters,
        "Each mask's fill fraction inside its annotation box must stay between the configured bounds.",
        evidence_terms=_MASK_TERMS,
    ),
    "mask_components": CheckDefinition(
        "mask_components", "object", MaximumComponentCountParameters,
        "Each mask must have no more than the configured maximum number of connected components.",
        evidence_terms=_MASK_TERMS,
    ),
}


def extraction_check_catalog() -> list[dict[str, object]]:
    """Return deterministic, prompt-ready metadata derived from the registry."""
    return [
        {
            "check_name": name,
            "meaning": definition.prompt_description,
            "scope": definition.scope,
            "parameters_schema": definition.parameter_model.model_json_schema(),
        }
        for name, definition in sorted(CHECK_REGISTRY.items())
    ]


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
