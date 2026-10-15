"""Requests and history records for reviewer-controlled rulebook changes."""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .rule import NonBlankText, Rule


class RulebookReviewAction(StrEnum):
    EDIT = "edit"
    CONFIRM = "confirm"
    REJECT = "reject"
    REVISE = "revise"


class RulebookAction(BaseModel):
    """Identify the reviewed version and the person submitting the action."""

    model_config = ConfigDict(extra="forbid", strict=True)

    expected_version: int = Field(ge=1)
    reviewer_id: NonBlankText


class RulebookReplace(RulebookAction):
    """Replace the complete draft contents, including individual rule decisions.

    Omitted rules are removed. New IDs must use reviewer origin; retained IDs
    preserve their origin. Confirmation states are explicit reviewer decisions.
    """

    guideline_text: str
    rules: list[Rule]


class RulebookReviewRecord(BaseModel):
    """Attribution for one new snapshot, assigned by the backend."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    rulebook_id: NonBlankText
    version: int = Field(ge=2)
    reviewer_id: NonBlankText
    action: RulebookReviewAction = Field(strict=False)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))


class RulebookReviewPage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[RulebookReviewRecord]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)
