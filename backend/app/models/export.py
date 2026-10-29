"""Strict reviewed-export transport and private immutable snapshot records."""
from enum import StrEnum
from typing import Annotated, Literal
import re

from pydantic import (
    AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field,
    StringConstraints, model_validator,
)

from .asset_manifest import AssetAvailability, SampleAssetManifest
from .job import Job, JobStatus
from .review import SampleReviewDecision
from .rule import NonBlankText

BIGINT_MAX = 9_223_372_036_854_775_807


def _identifier(value: str) -> str:
    if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Expected a nonblank identifier without control characters.")
    return value


Identifier = Annotated[str, StringConstraints(min_length=1, max_length=255, pattern=r"\S"),
                       AfterValidator(_identifier)]
Count = Annotated[int, Field(ge=0, le=BIGINT_MAX)]
Revision = Annotated[int, Field(ge=1, le=2_147_483_647)]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True,
                             revalidate_instances="always")


class ExportSelection(StrEnum):
    ACCEPTED_ONLY = "accepted_only"
    ALL_REVIEWED = "all_reviewed"


class ExclusionReason(StrEnum):
    PENDING_REVIEW = "pending_review"
    NEEDS_CORRECTION = "needs_correction"
    REJECTED = "rejected"
    ANNOTATION_MISSING = "annotation_text_missing"
    ANNOTATION_UNREADABLE = "annotation_text_unreadable"
    MASK_MISSING = "raw_mask_missing"
    MASK_UNREADABLE = "raw_mask_unreadable"
    IMAGE_MISSING = "original_image_missing"
    IMAGE_UNREADABLE = "original_image_unreadable"


class ExportRequest(Record):
    audit_id: Identifier
    format: Literal["penn_fudan", "coco", "labelme"]
    selection: ExportSelection = Field(strict=False)
    include_images: bool
    include_rechecks: bool = False


class Export(ExportRequest):
    export_id: Identifier
    job_id: Identifier
    status: JobStatus = Field(strict=False)
    included_count: Count | None = None
    excluded_count: Count | None = None
    download_url: str | None = None
    manifest_url: str | None = None
    created_at: AwareDatetime

    @model_validator(mode="after")
    def lifecycle(self):
        fields = (self.included_count, self.excluded_count, self.download_url, self.manifest_url)
        if self.status is JobStatus.COMPLETED:
            if any(v is None for v in fields):
                raise ValueError("Completed exports require counts and artifact URLs.")
            if (self.download_url != f"/api/v1/exports/{self.export_id}/download"
                    or self.manifest_url != f"/api/v1/exports/{self.export_id}/manifest"):
                raise ValueError("Export artifact URLs do not match its identity.")
        elif any(v is not None for v in fields):
            raise ValueError("Uncompleted exports must not advertise artifacts or counts.")
        return self


class ExportStarted(Record):
    export: Export
    job: Job

    @model_validator(mode="after")
    def identities(self):
        if (self.job.job_id != self.export.job_id or self.job.kind.value != "export"
                or self.job.status != self.export.status):
            raise ValueError("Export and job disagree.")
        return self


class ExportPage(Record):
    items: list[Export]
    limit: int = Field(ge=1, le=100)
    offset: Count
    total: Count
    next_offset: Count | None = None

    @model_validator(mode="after")
    def pagination(self):
        if len(self.items) > self.limit or len({item.export_id for item in self.items}) != len(self.items):
            raise ValueError("An export page contains too many or duplicate resources.")
        if len(self.items) > max(0, self.total - self.offset):
            raise ValueError("Export page items exceed the remaining collection.")
        expected = self.offset + len(self.items)
        expected = expected if expected < self.total else None
        if self.next_offset != expected or (not self.items and self.offset < self.total):
            raise ValueError("Export page continuation is inconsistent.")
        return self


class CapturedAvailability(Record):
    annotation_text: AssetAvailability = Field(strict=False)
    raw_mask: AssetAvailability = Field(strict=False)
    original_image: AssetAvailability = Field(strict=False)


class ExportManifestEntry(Record):
    result_audit_id: Identifier | None = None
    sample_id: Identifier
    sample_revision: Revision
    ordinal: Count
    processing_state: Literal["queued", "running", "completed", "failed", "cancelled"]
    included: bool
    review_decision: SampleReviewDecision = Field(strict=False)
    review_revision: Count
    reviewer_id: NonBlankText | None
    reviewed_at: AwareDatetime | None
    availability: CapturedAvailability
    reason: ExclusionReason | None = Field(strict=False)

    @model_validator(mode="after")
    def attribution(self):
        if self.review_revision == 0:
            if (self.review_decision is not SampleReviewDecision.PENDING_REVIEW
                    or self.reviewer_id is not None or self.reviewed_at is not None):
                raise ValueError("An unsaved review must be pending without attribution.")
        elif self.reviewer_id is None or self.reviewed_at is None:
            raise ValueError("Saved reviews require attribution.")
        if self.included != (self.reason is None):
            raise ValueError("Only excluded entries have an exclusion reason.")
        return self


def exclusion(decision, selection, include_images, availability, format="penn_fudan"):
    """Human decision first; no machine-quality veto and no history fallback."""
    if decision is SampleReviewDecision.PENDING_REVIEW:
        return ExclusionReason.PENDING_REVIEW
    if decision is SampleReviewDecision.NEEDS_CORRECTION:
        return ExclusionReason.NEEDS_CORRECTION
    if decision is SampleReviewDecision.REJECTED and selection is ExportSelection.ACCEPTED_ONLY:
        return ExclusionReason.REJECTED
    for role in ("annotation_text", "raw_mask", "original_image"):
        if role == "raw_mask" and format != "penn_fudan":
            continue
        if role == "original_image" and not include_images:
            continue
        status = getattr(availability, role)
        if status is not AssetAvailability.AVAILABLE:
            return ExclusionReason(f"{role}_{status.value}")
    return None


class ExportManifest(Record):
    format: Literal["penn_fudan", "coco", "labelme"] = "penn_fudan"
    export_id: Identifier
    audit_id: Identifier
    dataset_id: Identifier
    source_mode: Literal["snapshot"]
    rulebook_id: Identifier
    rulebook_version: Revision
    selection: ExportSelection = Field(strict=False)
    include_images: bool
    include_rechecks: bool = False
    selected_at: AwareDatetime
    entries: list[ExportManifestEntry]

    @model_validator(mode="after")
    def membership(self):
        seen = set()
        for ordinal, entry in enumerate(self.entries):
            if self.include_rechecks and entry.result_audit_id is None:
                raise ValueError("Latest-attempt exports require an explicit result audit for every entry.")
            if not self.include_rechecks and entry.result_audit_id not in (None, self.audit_id):
                raise ValueError("Historical exports must use their own audit results.")
            key = (entry.sample_id, entry.sample_revision)
            if entry.ordinal != ordinal or key in seen:
                raise ValueError("Manifest entries must be unique in contiguous audit order.")
            seen.add(key)
            expected = exclusion(entry.review_decision, self.selection,
                                 self.include_images, entry.availability, self.format)
            if entry.reason != expected or entry.included != (expected is None):
                raise ValueError("Manifest membership contradicts frozen selection policy.")
        return self


class ExportPolicy(Record):
    max_file_bytes: int = Field(default=67_108_864, ge=1, le=2_147_483_648)
    max_input_bytes: int = Field(default=2_147_483_648, ge=1, le=68_719_476_736)
    max_archive_bytes: int = Field(default=2_147_483_648, ge=1, le=68_719_476_736)
    max_samples: int = Field(default=3000, ge=1, le=100_000)
    max_metadata_bytes: int = Field(default=16_777_216, ge=1024, le=134_217_728)
    max_seconds: int = Field(default=900, ge=1, le=86_400)
    lease_seconds: int = Field(default=30, ge=10, le=300)
    poll_seconds: int = Field(default=1, ge=1, le=60)


class FrozenSample(Record):
    entry: ExportManifestEntry
    manifest: SampleAssetManifest


class FrozenExport(Record):
    manifest: ExportManifest
    samples: list[FrozenSample]
    policy: ExportPolicy

    @model_validator(mode="after")
    def exact_pins(self):
        if len(self.samples) != len(self.manifest.entries) or len(self.samples) > self.policy.max_samples:
            raise ValueError("Frozen input count is inconsistent or exceeds policy.")
        for sample, entry in zip(self.samples, self.manifest.entries):
            m = sample.manifest
            if m.annotation_format != self.manifest.format:
                raise ValueError("Export format must match its captured source format.")
            if (sample.entry != entry or m.dataset_id != self.manifest.dataset_id
                    or m.sample_id != entry.sample_id or m.sample_revision != entry.sample_revision):
                raise ValueError("Frozen manifest does not match the audited pin.")
            expected = CapturedAvailability(**{
                role: getattr(m, role).availability
                for role in CapturedAvailability.model_fields
            })
            if expected != entry.availability:
                raise ValueError("Frozen role availability disagrees with the manifest.")
        return self


class Artifact(Record):
    key: str
    size_bytes: int = Field(ge=1, le=68_719_476_736)
    sha256: Digest

    @model_validator(mode="after")
    def server_key(self):
        if re.fullmatch(r"[0-9a-f]{32}/(archive\.zip|manifest\.json)", self.key) is None:
            raise ValueError("Invalid private export artifact key.")
        return self


class PublishedArtifacts(Record):
    archive: Artifact
    manifest: Artifact

    @model_validator(mode="after")
    def one_attempt(self):
        if (not self.archive.key.endswith("/archive.zip")
                or not self.manifest.key.endswith("/manifest.json")
                or self.archive.key.split('/')[0] != self.manifest.key.split('/')[0]):
            raise ValueError("Artifacts must belong to one export attempt.")
        return self


class ExportClaim(Record):
    job: Job
    frozen: FrozenExport
    worker_id: Identifier
    claim_token: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
    deadline: AwareDatetime

    @model_validator(mode="after")
    def managed(self):
        if self.job.kind.value != "export" or self.job.status is not JobStatus.RUNNING:
            raise ValueError("An export claim requires a running export job.")
        return self
