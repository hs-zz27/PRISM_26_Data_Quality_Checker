"""Experimental, image-only lower-leg/foot localization; never annotation ground truth."""
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from .feedback import Digest, EvidenceLimits, ExecutionPin, Record

PROTOCOL = 'visible-feet-localization-v1'
Coordinate = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class FeetLocalizationPin(EvidenceLimits):
    protocol: Literal['visible-feet-localization-v1'] = PROTOCOL
    model: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    model_digest: Annotated[str, StringConstraints(pattern=r'^sha256:[0-9a-f]{64}$')]
    num_ctx: int = Field(ge=1024, le=131072)
    num_predict: int = Field(ge=128, le=2048)
    num_thread: int | None = Field(default=None, ge=1, le=1024)
    timeout_seconds: int = Field(ge=1, le=600)
    max_request_bytes: int = Field(ge=1024, le=128 * 1024**2)
    max_response_bytes: int = Field(ge=1024, le=65536)
    temperature: Literal[0] = 0

    @classmethod
    def from_transport_settings(cls, source: ExecutionPin):
        # Copy only numerical/identity limits. Never mislabel this image-only
        # protocol as v6 focused overlays in the recorded execution evidence.
        return cls.model_validate({name: getattr(source, name) for name in cls.model_fields if name != 'protocol'})

    def transport_settings(self) -> ExecutionPin:
        # Internal adapter for existing bounded HTTP and local-model verification.
        # No v6 prompt, renderer or predict() call is used by this operation.
        return ExecutionPin(**self.model_dump(exclude={'protocol'}),
            prompt_version='annotation-guideline-v6', preprocessing='target-focus-overlay-v1')


class EdgeInterval(Record):
    """Estimated range for an extremal body-part edge in original pixel-edge coordinates."""
    low: Coordinate
    high: Coordinate

    @model_validator(mode='after')
    def ordered(self):
        if self.low > self.high:
            raise ValueError('An edge interval must be ordered.')
        return self


class LowerLegRegion(Record):
    region_id: Literal['leg_1', 'leg_2']
    x_min: EdgeInterval
    y_min: EdgeInterval
    x_max: EdgeInterval
    y_max: EdgeInterval

    @model_validator(mode='after')
    def possible_rectangle(self):
        if self.x_min.high >= self.x_max.low or self.y_min.high >= self.y_max.low:
            raise ValueError('Edge uncertainty must still describe a positive-area region.')
        return self


class FeetObservation(Record):
    localization_id: Digest
    state: Literal['localized', 'not_person', 'not_fully_visible', 'uncertain']
    reason: Annotated[str, StringConstraints(min_length=1, max_length=400)]
    regions: list[LowerLegRegion] = Field(max_length=2)

    @model_validator(mode='after')
    def complete(self):
        if self.state == 'localized':
            if {r.region_id for r in self.regions} != {'leg_1', 'leg_2'}:
                raise ValueError('This prototype needs two distinct, completely localized lower legs and feet.')
            if self.regions[0].model_dump(exclude={'region_id'}) == self.regions[1].model_dump(exclude={'region_id'}):
                raise ValueError('Repeating one region cannot establish localization of two feet.')
        elif self.regions:
            raise ValueError('Unresolved or inapplicable observations must not invent coordinates.')
        return self


class FeetLocalizationRecord(Record):
    execution: FeetLocalizationPin
    localization_id: Digest
    image_sha256: Digest
    image_size: tuple[int, int]
    target_description: Annotated[str, StringConstraints(min_length=1, max_length=500, pattern=r'\S')]
    request_sha256: Digest
    model_verified: bool
    observation: FeetObservation | None = None
    failure: str | None = None
    response_excerpt: Annotated[str, StringConstraints(max_length=2000)] | None = None
    validation_errors: list[Annotated[str, StringConstraints(max_length=200)]] = Field(default_factory=list, max_length=8)
    duration_ms: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode='after')
    def coherent(self):
        if min(self.image_size) < 1:
            raise ValueError('Positive image dimensions required.')
        if self.model_verified:
            if self.failure is not None or self.observation is None:
                raise ValueError('Verified localization requires a complete observation.')
            if self.observation.localization_id != self.localization_id:
                raise ValueError('Localization identity differs.')
            for region in self.observation.regions:
                if any(edge.high > bound for edge, bound in (
                    (region.x_min, self.image_size[0]), (region.x_max, self.image_size[0]),
                    (region.y_min, self.image_size[1]), (region.y_max, self.image_size[1]))):
                    raise ValueError('Localized coordinates exceed the original image.')
        elif self.observation is not None or self.failure is None:
            raise ValueError('Unverified execution must carry a failure, not an observation.')
        return self


class FeetBoxResult(Record):
    outcome: Literal['pass', 'fail', 'unsure', 'unavailable', 'not_applicable']
    reason: str
    localization_id: Digest
    image_sha256: Digest
    annotation_sha256: Digest
    object_key: str
    canonical_box: tuple[float, float, float, float] | None = None
    violated_edges: list[str] = Field(default_factory=list)
    uncertain_edges: list[str] = Field(default_factory=list)
    review_required: Literal[True] = True
