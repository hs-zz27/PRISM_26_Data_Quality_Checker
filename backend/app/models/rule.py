"""Define source-linked rules, parameters, confirmation and rulebook versions."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .json_value import FiniteJsonValue


NonBlankText = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]


def _empty(value) -> bool:
    return not value


# Applicable context and extraction notes are omitted from serialized rules when
# empty, so rules without them keep their historical JSON and semantic hashes.
ContextSentences = Annotated[list[NonBlankText], Field(default_factory=list, exclude_if=_empty)]
ExtractionNotes = Annotated[list[NonBlankText], Field(default_factory=list, exclude_if=_empty)]


class ConfirmationState(StrEnum):
    """Reviewer confirmation state for a rule or rulebook."""

    DRAFT = "draft"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class RuleOrigin(StrEnum):
    """Whether a rule was extracted from text or authored by a reviewer."""

    EXTRACTED = "extracted"
    REVIEWER = "reviewer"


class RuleProposal(BaseModel):
    """Model-proposed content before application metadata and rulebook validation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    rule_id: NonBlankText
    description: NonBlankText
    check_name: NonBlankText | None = None
    parameters: dict[str, FiniteJsonValue] = Field(default_factory=dict)
    source_sentence: NonBlankText
    context_sentences: ContextSentences
    extraction_notes: ExtractionNotes


class Rule(BaseModel):
    """A proposed or reviewer-confirmed requirement, separate from its check.

    Parameters contain JSON-compatible values, such as allowed labels or
    thresholds. An unknown or absent check name is permitted so unsupported
    requirements can be retained. Extracted rules require an exact citation;
    reviewer-authored rules may have no source sentence.

    ``context_sentences`` are exact guideline lines (scope, definitions,
    exceptions, conditions, uncertainty policy) that qualify this requirement.
    They travel with the rule into every audit and recheck request. They are
    context, not separate requirements. ``extraction_notes`` are backend
    explanations for the reviewer, for example why a mapping was kept unsupported.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    rule_id: NonBlankText
    description: NonBlankText
    check_name: NonBlankText | None = None
    parameters: dict[str, FiniteJsonValue] = Field(default_factory=dict)
    # Accept stored string values while keeping enum members in Python.
    origin: RuleOrigin = Field(strict=False)
    source_sentence: NonBlankText | None = None
    confirmation_state: ConfirmationState = Field(default=ConfirmationState.DRAFT, strict=False)
    context_sentences: ContextSentences
    extraction_notes: ExtractionNotes

    @model_validator(mode="after")
    def require_extracted_source(self):
        if self.origin == RuleOrigin.EXTRACTED and self.source_sentence is None:
            raise ValueError("An extracted rule must cite a source sentence.")
        if len(set(self.context_sentences)) != len(self.context_sentences):
            raise ValueError("Context sentences must not repeat.")
        if self.source_sentence is not None and self.source_sentence in self.context_sentences:
            raise ValueError("A rule's own source sentence is not context for itself.")
        return self


class Rulebook(BaseModel):
    """Group rules with their original plain text guidelines and a version.

    Citation matching is exact and does not alter the supplied text. A
    confirmed rulebook contains at least one rule, all confirmed. Rules
    rejected during review can remain in a draft or an earlier saved version.
    Confirmation fields record state; the review service manages reviewer
    actions and appends attributed revisions without changing older versions.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    rulebook_id: NonBlankText
    version: int = Field(default=1, ge=1)
    guideline_text: str
    rules: list[Rule] = Field(default_factory=list)
    confirmation_state: ConfirmationState = Field(default=ConfirmationState.DRAFT, strict=False)

    @model_validator(mode="after")
    def validate_rules(self):
        rule_ids = [rule.rule_id for rule in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("Rule IDs must be unique within a rulebook.")

        for rule in self.rules:
            if rule.source_sentence is not None and rule.source_sentence not in self.guideline_text:
                raise ValueError(f"Rule {rule.rule_id} cites text absent from the guidelines.")
            if any(sentence not in self.guideline_text for sentence in rule.context_sentences):
                raise ValueError(f"Rule {rule.rule_id} cites context absent from the guidelines.")

        if self.confirmation_state == ConfirmationState.CONFIRMED:
            if not self.rules or any(rule.confirmation_state != ConfirmationState.CONFIRMED for rule in self.rules):
                raise ValueError("A confirmed rulebook must contain only confirmed rules and cannot be empty.")
        return self


class RulebookPage(BaseModel):
    """A page of latest rulebooks or one rulebook's saved version history."""

    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[Rulebook]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)
