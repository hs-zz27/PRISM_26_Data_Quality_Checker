"""Permanent human-readable names for audits and logical rulebooks.

The server allocates each number once (see migration 016) and never reuses
it. Labels are formatted here, from the stored numbers, so every API response
uses one format. Internal IDs remain the identifiers for requests and
relationships; clients must never parse a label to recover an ID.

Formats: ``Audit 014``, ``Audit 014 R01`` for the first recheck of Audit 014,
``Rules 003`` for a rulebook (versions are shown as ``Rules 003 v3``). Numbers
use at least three digits and recheck suffixes at least two, growing past
999 and 99 without truncation.
"""

from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator

NonBlank = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
ReviewerId = Annotated[str, StringConstraints(min_length=1, max_length=255, pattern=r"\S")]

TITLE_MAX_LENGTH = 120


def audit_label(number: int, recheck_number: int | None = None) -> str:
    base = f"Audit {number:03d}"
    return base if recheck_number is None else f"{base} R{recheck_number:02d}"


def rulebook_label(number: int, version: int | None = None) -> str:
    base = f"Rules {number:03d}"
    return base if version is None else f"{base} v{version}"


class _Title(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(description="Editable description shown after the permanent label.")
    title_revision: int = Field(ge=0, description="0 until the default title is first edited.")
    title_updated_at: AwareDatetime | None = None
    title_updated_by: str | None = None


class AuditName(_Title):
    """An audit's permanent label. A recheck keeps its original root's number."""

    audit_id: NonBlank
    root_audit_id: NonBlank = Field(description="The original audit this recheck descends from; itself for a root.")
    number: int = Field(ge=1)
    recheck_number: int | None = Field(default=None, ge=1)
    label: str = Field(description="Audit 014, or Audit 014 R01 for a recheck.")


class RulebookName(_Title):
    """One number per logical rulebook, shared by all of its versions."""

    rulebook_id: NonBlank
    number: int = Field(ge=1)
    label: str = Field(description="Rules 003; show a version as Rules 003 v3.")


class TitleUpdate(BaseModel):
    """Replace a title at the title revision the caller last read."""

    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(description=f"1-{TITLE_MAX_LENGTH} characters; surrounding whitespace is removed.")
    expected_title_revision: int = Field(ge=0, description="Last-read title revision.")
    reviewer_id: ReviewerId

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("The title must not be blank.")
        if len(value) > TITLE_MAX_LENGTH:
            raise ValueError(f"The title must be at most {TITLE_MAX_LENGTH} characters.")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("The title must not contain control characters or line breaks.")
        return value


class DisplayNameLookup(BaseModel):
    """Names for the requested IDs; unknown IDs are omitted, never invented."""

    model_config = ConfigDict(extra="forbid", strict=True)

    audits: list[AuditName] = Field(default_factory=list)
    rulebooks: list[RulebookName] = Field(default_factory=list)
