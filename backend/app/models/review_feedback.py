"""Reviewer context belongs to one image attempt, never a global policy."""
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from .identifiers import Identifier

FeedbackText = Annotated[str, StringConstraints(min_length=1, max_length=4096, pattern=r'\S')]

class ImageReviewFeedback(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    reviewer_id: Identifier
    source_audit_id: Identifier
    comment: FeedbackText
    propose_correction: bool = False

    @field_validator('comment')
    @classmethod
    def safe_text(cls, value):
        if '\x00' in value:
            raise ValueError('Feedback cannot contain NUL.')
        value.encode('utf-8')
        return value
