"""Object-level visual observations, separate from a reviewer decision."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Geometry = Literal['box', 'polygon', 'label', 'mask']
Coordinate = Annotated[float, Field(allow_inf_nan=False, ge=0)]


class VisualObservation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    object_key: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    geometry: list[Geometry] = Field(max_length=4)
    outcome: Literal['pass', 'fail', 'unsure', 'unavailable', 'not_applicable']
    reason: Annotated[str, StringConstraints(min_length=1, max_length=320, pattern=r'\S')]
    # A location to inspect, NOT a replacement box or an automatically drawn edit.
    region: tuple[Coordinate, Coordinate, Coordinate, Coordinate] | None

    @model_validator(mode='after')
    def coherent(self):
        for text in (self.object_key, self.reason):
            text.encode('utf-8', errors='strict')
            if '\x00' in text:
                raise ValueError('NUL is not supported.')
        if len(set(self.geometry)) != len(self.geometry):
            raise ValueError('Geometry references must be unique.')
        if self.outcome in {'pass', 'fail'} and not self.geometry:
            raise ValueError('A binary finding must identify its annotation evidence.')
        if self.outcome == 'fail' and self.region is None:
            raise ValueError('A suspected error needs a visible location for review.')
        if self.region is not None:
            left, top, right, bottom = self.region
            if left >= right or top >= bottom:
                raise ValueError('The review region must have positive area.')
        return self


class VisualAssessment(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    case_id: Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]
    rule_id: Annotated[str, StringConstraints(min_length=1)]
    observations: list[VisualObservation] = Field(min_length=1, max_length=64)

    @model_validator(mode='after')
    def identities(self):
        self.rule_id.encode('utf-8', errors='strict')
        if '\x00' in self.rule_id:
            raise ValueError('NUL is not supported.')
        keys = [item.object_key for item in self.observations]
        if len(keys) != len(set(keys)):
            raise ValueError('Each object must appear once.')
        return self

    def aggregate_outcome(self):
        outcomes = {item.outcome for item in self.observations}
        return next((value for value in ('fail', 'unsure', 'unavailable', 'pass') if value in outcomes), 'unavailable')
