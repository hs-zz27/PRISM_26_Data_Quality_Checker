"""Strict correction transports; imported evidence remains permissive."""
from typing import Annotated
import unicodedata

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from .dataset import Count, Identifier, ImportedAnnotationObject, JsonAnnotationObject, Revision, MAX_SAMPLE_OBJECTS
from .dataset_import import SamplePublication
from .asset_manifest import AssetManifestEntry
from .review_feedback import FeedbackText


def label_text(value):
    if not value.strip() or '"' in value or any(unicodedata.category(c).startswith('C') or c in '\u2028\u2029' for c in value):
        raise ValueError('Labels must be nonblank and cannot contain quotes, controls or line separators.')
    try:
        value.encode('utf-8', errors='strict')
    except UnicodeError as error:
        raise ValueError('Labels must be valid UTF-8 text.') from error
    return value


def stored_identifier(value):
    try:
        value.encode('utf-8', errors='strict')
    except UnicodeError as error:
        raise ValueError('Identifiers must be valid UTF-8.') from error
    return value


StoredIdentifier = Annotated[Identifier, AfterValidator(stored_identifier)]


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, revalidate_instances='always')


Text = Annotated[str, StringConstraints(min_length=1, max_length=4096, pattern=r'\S')]
PositiveID = Annotated[int, Field(ge=1, le=65535)]
Coordinate = Annotated[int, Field(ge=1, le=2147483647)]
type CorrectionBoundingBox = tuple[Coordinate, Coordinate, Coordinate, Coordinate]


class CorrectionObject(StrictRecord):
    object_id: PositiveID
    label: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    bbox: CorrectionBoundingBox

    _label = field_validator('label')(label_text)

    @field_validator('bbox', mode='before')
    @classmethod
    def json_array(cls, value):
        # FastAPI validates parsed JSON in Python mode. Preserve the documented
        # four-element JSON array without coercing its integer components.
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode='after')
    def ordered(self):
        if self.bbox[0] > self.bbox[2] or self.bbox[1] > self.bbox[3]:
            raise ValueError('One-based inclusive boxes must be ordered.')
        return self


class JsonCorrectionObject(JsonAnnotationObject):
    """Native JSON geometry edits; identity changes are checked against the source."""

    @field_validator('bbox', mode='before')
    @classmethod
    def json_box(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator('polygons', mode='before')
    @classmethod
    def json_points(cls, value):
        if isinstance(value, list):
            return [[tuple(point) if isinstance(point, list) else point for point in part]
                    if isinstance(part, list) else part for part in value]
        return value

    @field_validator('label')
    @classmethod
    def valid_label(cls, value):
        if len(value) > 255 or '\x00' in value:
            raise ValueError('A label must contain at most 255 characters and no NUL.')
        return value


class CorrectionRequest(StrictRecord):
    expected_revision: Revision
    reviewer_id: StoredIdentifier
    reason: Text
    objects: list[CorrectionObject | JsonCorrectionObject] = Field(max_length=MAX_SAMPLE_OBJECTS)
    mask_upload_id: StoredIdentifier | None = None
    source_audit_id: StoredIdentifier | None = None

    @field_validator('reason', 'reviewer_id', 'mask_upload_id', 'source_audit_id')
    @classmethod
    def stored_text(cls, value):
        if value is not None:
            if '\x00' in value:
                raise ValueError('Text cannot contain NUL.')
            try:
                value.encode('utf-8', errors='strict')
            except UnicodeError as error:
                raise ValueError('Text must be valid UTF-8.') from error
        return value

    @model_validator(mode='after')
    def unique_objects(self):
        if len({o.object_id for o in self.objects}) != len(self.objects):
            raise ValueError('Object IDs must be unique.')
        return self


class MaskUploadRequest(StrictRecord):
    mask: str = Field(json_schema_extra={'format': 'binary'})
    expected_revision: Annotated[int, Field(ge=1, le=2147483646)]


class MaskUpload(StrictRecord):
    mask_upload_id: StoredIdentifier
    dataset_id: StoredIdentifier
    sample_id: StoredIdentifier
    base_revision: Revision
    image_size: tuple[Coordinate, Coordinate]
    mask_ids: list[Annotated[int, Field(ge=0, le=65535)]] = Field(max_length=65536)
    expires_at: AwareDatetime

    @field_validator('mask_ids')
    @classmethod
    def sorted_ids(cls, value):
        if value != sorted(set(value)):
            raise ValueError('Mask IDs must be unique and sorted.')
        return value


class AnnotationRevision(StrictRecord):
    dataset_id: StoredIdentifier
    sample_id: StoredIdentifier
    revision: Revision
    parent_revision: Revision | None
    objects: list[ImportedAnnotationObject | JsonAnnotationObject] = Field(max_length=MAX_SAMPLE_OBJECTS)
    mask_changed: bool | None
    reviewer_id: StoredIdentifier | None
    reason: Text | None
    source_audit_id: StoredIdentifier | None
    created_at: AwareDatetime

    @model_validator(mode='after')
    def provenance(self):
        if self.parent_revision is not None:
            if (self.parent_revision != self.revision - 1 or self.reviewer_id is None
                    or self.reason is None or self.mask_changed is None):
                raise ValueError('Correction provenance must be complete and sequential.')
        elif any(v is not None for v in (self.reviewer_id, self.reason, self.source_audit_id)):
            raise ValueError('Unknown/import provenance must not fabricate reviewer attribution.')
        elif self.revision == 1 and self.mask_changed is not False:
            raise ValueError('An original import must record no correction mask change.')
        elif self.revision > 1 and self.mask_changed is not None:
            raise ValueError('Unknown later provenance must not assert a mask-change verdict.')
        return self


class RevisionPage(StrictRecord):
    items: list[AnnotationRevision]
    limit: int = Field(ge=1, le=100)
    offset: Count
    total: Count
    next_offset: Count | None

    @model_validator(mode='after')
    def coherent(self):
        size = min(self.limit, max(0, self.total - self.offset))
        expected = self.offset + size if self.offset + size < self.total else None
        if len(self.items) != size or self.next_offset != expected:
            raise ValueError('History pagination is inconsistent.')
        return self


class RecheckRequest(StrictRecord):
    sample_ids: list[StoredIdentifier] = Field(min_length=1, max_length=3000, json_schema_extra={'uniqueItems': True})
    sample_revisions: dict[StoredIdentifier, Revision] = Field(min_length=1, max_length=3000)
    reviewer_id: StoredIdentifier
    feedback: FeedbackText | None = None
    propose_correction: bool = False
    # Optional: the annotations the feedback is about, by stable source key (not display number).
    # A correction proposal may then edit only these. Omitted: the annotations the recheck flags.
    object_keys: list[Annotated[str, StringConstraints(min_length=1, max_length=256)]] | None = Field(
        default_factory=lambda: None, min_length=1, max_length=16, json_schema_extra={'uniqueItems': True},
        exclude_if=lambda value: value is None)

    @model_validator(mode='after')
    def selection(self):
        if self.object_keys is not None and (self.feedback is None or len(set(self.object_keys)) != len(self.object_keys)):
            raise ValueError('Reviewed object keys require reviewer feedback and must be unique.')
        if self.propose_correction and self.feedback is None:
            raise ValueError('A correction proposal requires reviewer feedback.')
        if self.feedback is not None and (len(self.sample_ids) != 1 or '\x00' in self.feedback):
            raise ValueError('Feedback must identify exactly one image and contain no NUL.')
        if len(set(self.sample_ids)) != len(self.sample_ids) or set(self.sample_revisions) != set(self.sample_ids):
            raise ValueError('Unique sample IDs and exactly matching revision keys are required.')
        return self


class StagedMask(StrictRecord):
    public: MaskUpload
    asset: AssetManifestEntry
    created_at: AwareDatetime
    consumed_revision: Revision | None = None

    @model_validator(mode='after')
    def identity(self):
        if (self.asset.availability.value != 'available' or self.asset.content_type != 'image/png'
                or self.asset.size_bytes < 1 or self.public.expires_at <= self.created_at
                or (self.consumed_revision is not None and self.consumed_revision != self.public.base_revision + 1)):
            raise ValueError('Staged mask identity/timing is invalid.')
        return self


class PreparedCorrection(StrictRecord):
    request: CorrectionRequest
    base: SamplePublication
    revised: SamplePublication

    @model_validator(mode='after')
    def identities(self):
        before, after = self.base, self.revised
        if (before.sample.dataset_id != after.sample.dataset_id or before.sample.sample_id != after.sample.sample_id
                or before.ordinal != after.ordinal or before.sample.revision != self.request.expected_revision
                or after.sample.revision != before.sample.revision + 1
                or after.manifest.original_image != before.manifest.original_image
                or after.manifest.annotation_format != before.manifest.annotation_format
                or after.manifest.image_key != before.manifest.image_key
                or after.annotation_object_count != len(self.request.objects)
                or [o.model_dump() for o in after.sample.objects] != [o.model_dump() for o in self.request.objects]
                or (before.manifest.annotation_format == 'penn_fudan' and after.sample.integrity_status.value != 'valid')
                or after.sample.image_size is None
                or after.manifest.annotation_text.availability.value != 'available'
                or after.manifest.annotation_text.content_type != ('text/plain' if before.manifest.annotation_format == 'penn_fudan' else 'application/json')
                or after.manifest.annotation_text.size_bytes < 1
                or after.manifest.annotation_text.storage_key == before.manifest.annotation_text.storage_key):
            raise ValueError('Prepared correction disagrees with its exact base and request.')
        return self
