"""Strict editor inputs for creating manually assembled draft rulebooks."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .json_value import FiniteJsonValue
from .rule import NonBlankText

class RulebookCreateRule(BaseModel):
    """Reviewer-authored content without server-owned origin or review state."""

    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")

    rule_id: NonBlankText
    description: NonBlankText
    check_name: NonBlankText | None = None
    parameters: dict[str, FiniteJsonValue] = Field(default_factory=dict)
    source_sentence: NonBlankText | None = None

class RulebookCreate(BaseModel):
    """Create an editable draft, preserving the exact supplied guideline text.

    Empty text and an empty or omitted rules list are permitted. Non-null
    citations must be exact substrings, not semantically verified references.
    Unsupported checks remain available for later human review.
    """

    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")

    guideline_text: str
    rules: list[RulebookCreateRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_submitted_content(self) -> "RulebookCreate":
        rule_ids = [rule.rule_id for rule in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("Rule IDs must be unique within a rulebook.")
        for rule in self.rules:
            if rule.source_sentence is not None and rule.source_sentence not in self.guideline_text:
                raise ValueError("Rule citations must match the submitted guidelines exactly.")
        return self
