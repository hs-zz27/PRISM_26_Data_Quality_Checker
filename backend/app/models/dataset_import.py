"""Private catalogue/import contracts. Never expose these in HTTP response models."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, ConfigDict, Field, StringConstraints, model_validator

from .asset_manifest import AssetManifestEntry, SampleAssetManifest
from .dataset import (
    CatalogueModel, Count, Identifier, IntegrityStatus, MAX_SAMPLE_OBJECTS,
    Revision, Sample,
)
from .job import Job, JobKind, JobStatus


class ImportPolicy(CatalogueModel):
    """Conservative development resource limits, not dataset quality requirements."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True,
                              revalidate_instances="always")
    max_archive_bytes: int = Field(default=512 * 1024**2, ge=1, le=2**40)
    max_entries: int = Field(default=10_000, ge=1, le=100_000)
    max_file_bytes: int = Field(default=64 * 1024**2, ge=1, le=2**32)
    max_expanded_bytes: int = Field(default=2 * 1024**3, ge=1, le=2**40)
    max_annotation_bytes: int = Field(default=2 * 1024**2, ge=1, le=64 * 1024**2)
    max_image_pixels: int = Field(default=16_000_000, ge=1, le=64_000_000)
    max_samples: int = Field(default=3_000, ge=1, le=100_000)
    max_objects_per_sample: int = Field(default=MAX_SAMPLE_OBJECTS, ge=1, le=MAX_SAMPLE_OBJECTS)
    max_result_bytes: int = Field(default=16 * 1024**2, ge=1024, le=64 * 1024**2)
    max_seconds: int = Field(default=900, ge=1, le=86_400)
    lease_seconds: int = Field(default=30, ge=2, le=300)
    renew_seconds: float = Field(default=5.0, gt=0, allow_inf_nan=False)
    poll_seconds: float = Field(default=0.2, gt=0, le=5, allow_inf_nan=False)

    @model_validator(mode="after")
    def coherent_limits(self) -> "ImportPolicy":
        if not self.max_annotation_bytes <= self.max_file_bytes <= self.max_expanded_bytes:
            raise ValueError("Require annotation <= file <= expanded byte budgets.")
        if not self.poll_seconds <= self.renew_seconds < self.lease_seconds / 2:
            raise ValueError("Require poll <= renew < half the lease duration.")
        return self


class ImportRequest(CatalogueModel):
    """A durable server-selected upload identity; no request object or live root."""

    job_id: Identifier
    dataset_id: Identifier
    format: Literal["penn_fudan", "coco", "labelme"] = "penn_fudan"
    archive_key: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    archive_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    archive_size_bytes: Count
    policy: ImportPolicy
    started_at: AwareDatetime | None = None
    deadline: AwareDatetime | None = None

    @model_validator(mode="after")
    def coherent_request(self) -> "ImportRequest":
        AssetManifestEntry.validate_storage_key(self.archive_key)
        if not 0 < self.archive_size_bytes <= self.policy.max_archive_bytes:
            raise ValueError("Archive identity must fit the saved upload policy.")
        if (self.started_at is None) != (self.deadline is None):
            raise ValueError("Start and deadline must both be present or absent.")
        if self.started_at is not None and (
            (self.deadline - self.started_at).total_seconds() != self.policy.max_seconds
        ):
            raise ValueError("Deadline must match the original single-attempt policy.")
        return self


class ImportClaim(CatalogueModel):
    """Untrusted ownership snapshot, always checked against locked database rows."""

    job: Job
    request: ImportRequest
    worker_id: Identifier
    claim_token: Identifier
    lease_until: AwareDatetime

    @model_validator(mode="after")
    def coherent_claim(self) -> "ImportClaim":
        job = Job.model_validate(self.job.model_dump())
        if (job.job_id != self.request.job_id or job.kind is not JobKind.DATASET_IMPORT
                or job.status not in (JobStatus.RUNNING, JobStatus.CANCELLING)
                or self.request.started_at is None or job.result is not None
                or self.lease_until <= self.request.started_at):
            raise ValueError("Import claim identities/state/times disagree.")
        return self


class SamplePublication(CatalogueModel):
    """Private prepared evidence. Links are generated at read time, not supplied."""

    manifest: SampleAssetManifest
    ordinal: Count
    sample: Sample
    annotation_object_count: Count | None

    @model_validator(mode="after")
    def coherent_revision(self) -> "SamplePublication":
        manifest = SampleAssetManifest.model_validate(self.manifest.model_dump())
        sample = Sample.model_validate(self.sample.model_dump())
        if (manifest.dataset_id, manifest.sample_id, manifest.sample_revision) != (
            sample.dataset_id, sample.sample_id, sample.revision
        ):
            raise ValueError("Sample and manifest identities disagree.")
        if manifest.annotation_format != sample.annotation_format:
            raise ValueError("Sample and manifest formats disagree.")
        if any(value is not None for value in sample.assets.model_dump().values()):
            raise ValueError("Prepared evidence cannot supply public asset links.")
        if self.annotation_object_count is not None and self.annotation_object_count != len(sample.objects):
            raise ValueError("A complete annotation count must match preserved parsed objects.")
        if manifest.annotation_text.availability.value != "available" and (
            self.annotation_object_count is not None or sample.objects
        ):
            raise ValueError("Unavailable annotation cannot supply parsed objects or a complete count.")
        if manifest.original_image.availability.value != "available" and sample.image_size is not None:
            raise ValueError("Unavailable image cannot supply decoded dimensions.")
        if sample.integrity_status is IntegrityStatus.VALID and (
            self.annotation_object_count is None or any(
                entry.availability.value != "available" for entry in (
                    manifest.original_image, manifest.annotation_text,
                    *([manifest.raw_mask] if manifest.annotation_format == "penn_fudan" else [])
                )
            )
        ):
            raise ValueError("Valid basic evidence requires all roles and a complete annotation count.")
        return self


class CatalogueRevision(CatalogueModel):
    """Exact audit-start authority; intentionally has no mutable source_root."""

    dataset_id: Identifier
    sample_id: Identifier
    revision: Revision
    manifest: SampleAssetManifest

    @model_validator(mode="after")
    def coherent_pin(self) -> "CatalogueRevision":
        checked = SampleAssetManifest.model_validate(self.manifest.model_dump())
        if (self.dataset_id, self.sample_id, self.revision) != (
            checked.dataset_id, checked.sample_id, checked.sample_revision
        ):
            raise ValueError("Catalogue pin and manifest identities disagree.")
        return self
