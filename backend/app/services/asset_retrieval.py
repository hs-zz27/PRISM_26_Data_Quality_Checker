"""Retrieve exact revision-pinned source assets and derived previews."""

from dataclasses import dataclass
from enum import StrEnum

from ..models.asset_manifest import AssetKind
from ..storage.workflow_repository import SampleAssetManifestRepository
from .asset_preview import MAX_PREVIEW_SOURCE_BYTES, PreviewKind, SamplePreviewService


class PublicAssetKind(StrEnum):
    """Asset names exposed by the revision-pinned HTTP asset endpoint."""

    IMAGE = "image"
    MASK = "mask"
    MASK_PREVIEW = "mask_preview"
    THUMBNAIL = "thumbnail"


@dataclass(frozen=True)
class RetrievedAsset:
    """Binary asset content and the server-selected response content type."""

    content: bytes
    content_type: str


class SampleAssetService:
    """Read one exact source revision without consulting mutable dataset files."""

    _RAW_ASSETS = {
        PublicAssetKind.IMAGE: AssetKind.ORIGINAL_IMAGE,
        PublicAssetKind.MASK: AssetKind.RAW_MASK,
    }
    _PREVIEWS = {
        PublicAssetKind.MASK_PREVIEW: PreviewKind.MASK_PREVIEW,
        PublicAssetKind.THUMBNAIL: PreviewKind.THUMBNAIL,
    }

    def __init__(
        self,
        repository: SampleAssetManifestRepository,
        *,
        preview_service: SamplePreviewService | None = None,
    ) -> None:
        self.repository = repository
        self.preview_service = (
            SamplePreviewService(repository) if preview_service is None else preview_service
        )

    @staticmethod
    def _require_identifier(value: object, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string.")
        return value

    @staticmethod
    def _require_revision(value: object) -> int:
        if type(value) is not int or value < 1:
            raise ValueError("sample_revision must be a positive integer.")
        return value

    def retrieve(
        self,
        dataset_id: str,
        sample_id: str,
        sample_revision: int,
        asset_kind: PublicAssetKind | str,
    ) -> RetrievedAsset:
        """Retrieve raw or derived bytes for exactly one stored sample revision."""
        dataset_id = self._require_identifier(dataset_id, "dataset_id")
        sample_id = self._require_identifier(sample_id, "sample_id")
        sample_revision = self._require_revision(sample_revision)
        kind = PublicAssetKind(asset_kind)

        if kind in self._PREVIEWS:
            preview = self.preview_service.render(
                dataset_id, sample_id, sample_revision, self._PREVIEWS[kind],
            )
            return RetrievedAsset(content=preview.content, content_type=preview.content_type)

        source_kind = self._RAW_ASSETS[kind]
        content = self.repository.read_asset_bytes(
            dataset_id,
            sample_id,
            sample_revision,
            source_kind,
            max_bytes=MAX_PREVIEW_SOURCE_BYTES,
        )
        # These public source roles are PNGs. Stored metadata must not choose
        # an executable HTTP content type. Preserve even malformed captured
        # bytes for inspection; only preview requests decode the source.
        return RetrievedAsset(content=content, content_type="image/png")
