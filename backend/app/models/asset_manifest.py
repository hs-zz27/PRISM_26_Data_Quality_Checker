"""Immutable metadata for one dataset sample revision's source assets."""

from enum import StrEnum
from typing import Literal
from pathlib import PurePosixPath, PureWindowsPath
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, model_serializer

from .rule import NonBlankText


class AssetKind(StrEnum):
    """Source asset roles captured for a sample revision."""

    ORIGINAL_IMAGE = "original_image"
    RAW_MASK = "raw_mask"
    ANNOTATION_TEXT = "annotation_text"


class AssetAvailability(StrEnum):
    """Whether the source asset was captured and can be verified."""

    AVAILABLE = "available"
    MISSING = "missing"
    UNREADABLE = "unreadable"


class AssetManifestEntry(BaseModel):
    """Private identity metadata for one captured source asset."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    availability: AssetAvailability = Field(strict=False)
    storage_key: str | None = None
    content_type: NonBlankText | None = None
    sha256: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)

    @field_validator("storage_key")
    @classmethod
    def validate_storage_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or value.strip() != value or "\\" in value or "\x00" in value:
            raise ValueError("storage_key must be a clean relative POSIX path.")
        posix = PurePosixPath(value)
        windows = PureWindowsPath(value)
        if posix.is_absolute() or windows.is_absolute() or windows.drive:
            raise ValueError("storage_key must be relative to the private asset root.")
        if any(part in ("", ".", "..") for part in value.split("/")):
            raise ValueError("storage_key must not contain traversal components.")
        return value

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("sha256 must be a lowercase SHA-256 digest.")
        return value

    @model_validator(mode="after")
    def validate_identity(self) -> "AssetManifestEntry":
        identity = (self.storage_key, self.content_type, self.sha256, self.size_bytes)
        if self.availability is AssetAvailability.AVAILABLE:
            if any(value is None for value in identity):
                raise ValueError("available assets require storage key, type, digest and size.")
        elif any(value is not None for value in identity):
            raise ValueError("unavailable assets cannot contain stored file identity metadata.")
        return self


class SampleAssetManifest(BaseModel):
    """The immutable source-asset manifest for an exact dataset sample revision."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    dataset_id: NonBlankText
    sample_id: NonBlankText
    sample_revision: int = Field(ge=1)
    annotation_format: Literal["penn_fudan", "coco", "labelme"] = "penn_fudan"
    image_key: str | None = None
    original_image: AssetManifestEntry
    raw_mask: AssetManifestEntry
    annotation_text: AssetManifestEntry

    @model_validator(mode="after")
    def format_binding(self):
        if self.annotation_format == "penn_fudan" and self.image_key is not None:
            raise ValueError("Penn-Fudan does not use a JSON image key.")
        if self.annotation_format != "penn_fudan" and self.image_key is None:
            raise ValueError("JSON annotations require a pinned image record key.")
        return self

    @model_serializer(mode="wrap")
    def preserve_legacy_identity(self, handler):
        # Existing feedback cases/policies hash nested manifest JSON. Keep the
        # historical Penn-Fudan representation byte-for-byte hash compatible.
        value = handler(self)
        if self.annotation_format == "penn_fudan" and self.image_key is None:
            value.pop("annotation_format", None)
            value.pop("image_key", None)
        return value
