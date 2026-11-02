"""Reviewer context belongs to one image attempt, never a global policy."""
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from .identifiers import Identifier

FeedbackText = Annotated[str, StringConstraints(min_length=1, max_length=4096, pattern=r'\S')]
ObjectKey = Annotated[str, StringConstraints(min_length=1, max_length=256)]

class ImageReviewFeedback(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    reviewer_id: Identifier
    source_audit_id: Identifier
    comment: FeedbackText
    propose_correction: bool = False
    # Optional: the annotations this feedback is about (stable source keys, not display numbers). When
    # given, a correction proposal may edit only these. When absent, the annotations the recheck itself
    # flagged (fail or unsure) are the only editable ones; with neither, behaviour is unchanged.
    object_keys: list[ObjectKey] | None = Field(default=None, max_length=16, exclude_if=lambda value: value is None)

    @model_validator(mode='after')
    def unique_keys(self):
        if self.object_keys is not None and (not self.object_keys or len(set(self.object_keys)) != len(self.object_keys)):
            raise ValueError('object_keys must be nonempty and unique.')
        return self

    @field_validator('comment')
    @classmethod
    def safe_text(cls, value):
        if '\x00' in value:
            raise ValueError('Feedback cannot contain NUL.')
        value.encode('utf-8')
        return value
