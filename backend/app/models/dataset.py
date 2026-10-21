"""Strict catalogue transports; source evidence is not a correction request."""

from enum import StrEnum
from typing import Annotated
from urllib.parse import quote

from pydantic import (
    AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints,
    field_validator, model_validator,
)

from .audit_read import SampleAssets
from .job import ErrorDetail, Job, JobKind, JobStatus


from .identifiers import _no_nul, Identifier, Count, Revision, Dimension


MAX_SAMPLE_OBJECTS = 10_000
MAX_ISSUES = 256

# Never interpolate paths, source text, archive filenames or exception messages.
ISSUE_MESSAGES = {
    "missing_image": "The source image is missing.",
    "missing_mask": "The source mask is missing.",
    "missing_annotation": "The source annotation is missing.",
    "unreadable_image": "The source image could not be read.",
    "unreadable_mask": "The source mask could not be read.",
    "unreadable_annotation": "The source annotation could not be read.",
    "invalid_image": "The source image could not be decoded safely.",
    "invalid_mask": "The source mask could not be decoded safely.",
    "invalid_annotation": "The source annotation could not be parsed completely.",
    "invalid_metadata": "The source annotation metadata is incomplete or inconsistent.",
    "invalid_object": "Source annotation objects contain invalid values or duplicate identities.",
    "unsupported_source": "A source representation is not supported.",
    "unsupported_extra": "Unsupported extra entries were not imported as sample assets.",
    "invalid_archive": "The archive is not a usable supported ZIP archive.",
    "invalid_layout": "The archive has no unambiguous recognizable sample layout.",
    "resource_limit": "The import exceeded a configured resource limit.",
    "import_failed": "The import could not be completed.",
    "import_cancelled": "The import was cancelled.",
    "issues_truncated": "Additional source problems were omitted from this summary.",
}
_ISSUE_FIELDS = {None, "archive", "layout", "sample", "image", "mask", "annotation",
                 "annotation.objects", "annotation.metadata"}


def source_issue(code: str, *, field: str | None = None) -> ErrorDetail:
    """Construct only a known safe diagnostic, never a free-form public error."""
    if code not in ISSUE_MESSAGES or field not in _ISSUE_FIELDS:
        raise ValueError("Unsupported catalogue issue code or field.")
    return ErrorDetail(field=field, message=ISSUE_MESSAGES[code])


def validate_issues(values: list[ErrorDetail]) -> list[ErrorDetail]:
    for value in values:
        if value.field not in _ISSUE_FIELDS or value.message not in ISSUE_MESSAGES.values():
            raise ValueError("Catalogue diagnostics must use safe allowlisted fields and messages.")
    return values


class CatalogueModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")


class DatasetFormat(StrEnum):
    PENN_FUDAN = "penn_fudan"


class DatasetStatus(StrEnum):
    IMPORTING = "importing"
    READY = "ready"
    INVALID = "invalid"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IntegrityStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


class ImportedAnnotationObject(CatalogueModel):
    """Keep order, duplicate/negative IDs and invalid boxes for later auditing."""

    object_id: int
    label: str
    bbox: tuple[int, int, int, int]


class Dataset(CatalogueModel):
    dataset_id: Identifier
    name: Annotated[str, StringConstraints(min_length=1, max_length=255, pattern=r"\S"),
                    AfterValidator(_no_nul)]
    format: DatasetFormat = Field(strict=False)
    status: DatasetStatus = Field(strict=False)
    sample_count: Count | None
    object_count: Count | None
    import_job_id: Identifier
    created_at: AwareDatetime
    issues: list[ErrorDetail] = Field(default_factory=list, max_length=MAX_ISSUES)

    _safe_issues = field_validator("issues")(validate_issues)

    @model_validator(mode="after")
    def coherent_counts(self) -> "Dataset":
        if self.object_count is not None and self.sample_count is None:
            raise ValueError("A complete object count requires a known sample count.")
        if self.sample_count == 0 and self.object_count not in (None, 0):
            raise ValueError("An empty catalogue cannot have a positive object count.")
        if self.status is DatasetStatus.READY and (
            self.sample_count is None or self.sample_count == 0
        ):
            raise ValueError("Ready requires at least one discovered sample.")
        if self.status in (DatasetStatus.INVALID, DatasetStatus.FAILED) and not self.issues:
            raise ValueError("Invalid/failed datasets require a safe diagnostic.")
        return self


class Sample(CatalogueModel):
    sample_id: Identifier
    dataset_id: Identifier
    revision: Revision
    image_size: tuple[Dimension, Dimension] | None
    integrity_status: IntegrityStatus = Field(strict=False)
    objects: list[ImportedAnnotationObject] = Field(max_length=MAX_SAMPLE_OBJECTS)
    assets: SampleAssets = Field(default_factory=SampleAssets)
    issues: list[ErrorDetail] = Field(default_factory=list, max_length=MAX_ISSUES)

    _safe_issues = field_validator("issues")(validate_issues)

    @field_validator("assets", mode="before")
    @classmethod
    def revalidate_assets(cls, value):
        if isinstance(value, SampleAssets):
            return SampleAssets.model_validate(value.model_dump())
        return value

    @model_validator(mode="after")
    def coherent_evidence(self) -> "Sample":
        for kind in ("image", "mask", "mask_preview", "thumbnail"):
            reference = getattr(self.assets, kind)
            expected = (f"/api/v1/datasets/{quote(self.dataset_id, safe='')}/samples/"
                        f"{quote(self.sample_id, safe='')}/assets/{kind}?revision={self.revision}")
            if reference is not None and (
                type(reference.revision) is not int or reference.revision != self.revision
                or reference.url != expected or reference.content_type != "image/png"
            ):
                raise ValueError("Asset references must pin this exact sample identity and role.")
        if self.integrity_status is IntegrityStatus.VALID:
            if self.image_size is None or self.issues:
                raise ValueError("Valid basic evidence requires dimensions and no integrity issues.")
        elif not self.issues:
            raise ValueError("Invalid/unavailable evidence requires a safe diagnostic.")
        return self


class _Page(CatalogueModel):
    limit: int = Field(ge=1, le=100)
    offset: Count
    total: Count
    next_offset: Count | None

    def validate_page(self, size: int) -> None:
        expected_size = min(self.limit, max(0, self.total - self.offset))
        expected_next = self.offset + size if self.offset + size < self.total else None
        if size != expected_size or self.next_offset != expected_next:
            raise ValueError("Page items, total and next_offset are inconsistent.")


class DatasetPage(_Page):
    items: list[Dataset]

    @model_validator(mode="after")
    def coherent_page(self) -> "DatasetPage":
        self.validate_page(len(self.items))
        return self


class SamplePage(_Page):
    items: list[Sample]

    @model_validator(mode="after")
    def coherent_page(self) -> "SamplePage":
        self.validate_page(len(self.items))
        return self


class DatasetImportStarted(CatalogueModel):
    dataset: Dataset
    job: Job

    @model_validator(mode="after")
    def coherent_acceptance(self) -> "DatasetImportStarted":
        # Job is shared and mutable: validate its entire nested document again.
        checked = Job.model_validate(self.job.model_dump())
        if (self.dataset.status is not DatasetStatus.IMPORTING
                or checked.job_id != self.dataset.import_job_id
                or checked.kind is not JobKind.DATASET_IMPORT
                or checked.status is not JobStatus.QUEUED
                or checked.result is not None or checked.error is not None
                or checked.progress.phase != "queued" or self.dataset.issues
                or checked.progress.processed != 0 or checked.progress.total is not None
                or self.dataset.sample_count is not None or self.dataset.object_count is not None
                or checked.created_at != self.dataset.created_at):
            raise ValueError("Acceptance must describe one newly queued import.")
        return self
