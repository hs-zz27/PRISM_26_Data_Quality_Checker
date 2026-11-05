"""Which supplied geometry a requirement judges, and which annotations it can be checked against.

Two separate things live here:

* ``RuleApplicability`` is metadata of a RULE. It states which kinds of supplied annotation geometry
  the requirement is about (a bounding box, a polygon, a mask), or none when the requirement is
  independent of geometry type. It is derived from the requirement text, shown to the reviewer, may
  be set by the reviewer, and is pinned with the rule.
* ``TargetScope`` is the RECORD of one check on one sample: which annotations were eligible targets,
  which were not and why, how the model judged each target, and how the sample-level outcome was
  derived. It is stored in the finding's evidence so a result is always tied to named annotations.

Provenance matters: a box computed from a polygon is NOT a supplied bounding box, and a mask the
visual check cannot see is NOT usable evidence even when it was supplied.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

GeometryKind = Literal["box", "polygon", "mask"]
TargetOutcome = Literal["pass", "fail", "unsure", "unavailable"]
Condition = Literal["applies", "not_present", "unclear"]
Basis = Annotated[str, StringConstraints(min_length=1, max_length=400, pattern=r"\S")]
ObjectKey = Annotated[str, StringConstraints(min_length=1, max_length=256)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=400)]

# derived-v1 remains the fallback for v5 audits. New extractions and focused protocols
# use v2, which narrows geometry exclusions; stored applicability is never re-derived.
APPLICABILITY_ALGORITHM = "derived-v1"
APPLICABILITY_ALGORITHMS = ("derived-v1", "derived-v2")


class RuleApplicability(BaseModel):
    """Supplied geometry a requirement judges.

    ``geometry`` lists the kinds the requirement is about. ``combine`` says how an annotation must
    have them: ``all`` means every listed kind must have been supplied for that annotation (for
    example a polygon judged against its own box); ``any`` means alternatives (a box or a polygon).
    An empty list means the requirement does not depend on a geometry type and every annotation is
    eligible. ``status`` is ``ambiguous`` when the text could not be resolved confidently; the
    reviewer should check it. ``source`` records who decided: the backend derivation or a reviewer.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    geometry: list[GeometryKind] = Field(default_factory=list, max_length=3)
    combine: Literal["any", "all"] = "all"
    source: Literal["derived-v1", "derived-v2", "reviewer"] = "derived-v1"
    status: Literal["clear", "ambiguous"] = "clear"
    basis: Basis

    @model_validator(mode="after")
    def consistent(self):
        if len(set(self.geometry)) != len(self.geometry):
            raise ValueError("Applicability geometry kinds must not repeat.")
        if self.source == "reviewer" and self.status != "clear":
            raise ValueError("A reviewer-set applicability is authoritative and cannot be ambiguous.")
        return self


class ObjectGeometry(BaseModel):
    """How each geometry of one annotation was obtained."""

    model_config = ConfigDict(extra="forbid")

    box: Literal["supplied", "derived_from_polygon", "absent"]
    polygon: Literal["supplied", "absent"]
    # The visual check never receives pixel masks, so a supplied mask is reported but not usable.
    # referenced_unavailable: the format expects a mask file and it is missing or unreadable, which
    # is different from a mask that was never supplied (absent).
    mask: Literal["supplied_not_shown", "referenced_unavailable", "unsupported_encoding", "absent"]


class ScopeObject(BaseModel):
    """One annotation identified by its stable source key; object_id is only a display number."""

    model_config = ConfigDict(extra="forbid")

    object_key: ObjectKey
    object_id: int
    label: str = Field(max_length=255)
    geometry: ObjectGeometry


class IneligibleObject(ScopeObject):
    """An annotation that is context for this requirement, not a target."""

    missing: list[GeometryKind]
    detail: ShortText


class ScopeUnavailable(BaseModel):
    """Why no model call was made. ``code`` is stable; ``detail`` is for the reviewer."""

    model_config = ConfigDict(extra="forbid")

    code: Literal[
        "no_annotations", "no_eligible_annotation", "supplied_box_not_available",
        "mask_not_supplied", "mask_referenced_unavailable", "mask_encoding_unsupported",
        "mask_not_visible_to_visual_check",
        "measurement_not_supported", "condition_not_present", "no_valid_assessment", "too_many_targets",
    ]
    detail: ShortText


class TargetResult(BaseModel):
    """The final result for one eligible target after backend checks of the model's assessment."""

    model_config = ConfigDict(extra="forbid")

    object_key: ObjectKey
    outcome: TargetOutcome
    model_outcome: TargetOutcome | None = None
    model_compliance: Literal["yes", "no", "unclear"] | None = Field(
        default=None, exclude_if=lambda value: value is None,
        description="Raw focused-model compliance answer, when retained by the execution protocol.",
    )
    geometry: Literal["box", "polygon"] | None = None
    condition: Condition | None = None
    reason: ShortText | None = None
    flags: list[str] = Field(default_factory=list, max_length=8)


class ScopeImage(BaseModel):
    """Image identity that, with object_key, makes an annotation reference unambiguous."""

    model_config = ConfigDict(extra="forbid")

    sample_id: str
    sample_revision: int
    image_key: str | None = None
    image_sha256: str
    annotation_sha256: str


class TargetScope(BaseModel):
    """Eligibility and per-target results recorded with a visual finding."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["target-scope-v1"] = "target-scope-v1"
    image: ScopeImage
    applicability: RuleApplicability
    applicability_origin: Literal["rule", "derived_at_audit"]
    eligible: list[ScopeObject] = Field(default_factory=list)
    ineligible: list[IneligibleObject] = Field(default_factory=list)
    unavailable: ScopeUnavailable | None = None
    results: list[TargetResult] = Field(default_factory=list)
    rejected_references: list[str] = Field(default_factory=list, max_length=32)
    model_reported_outcome: TargetOutcome | None = None
    outcome_source: Literal["no_model_call", "derived_from_target_results"] = "no_model_call"
    notes: list[ShortText] = Field(default_factory=list, max_length=8)
