"""Correction-only server policy and allowlisted safe failures; no import I/O."""
from pathlib import Path
import os

from dotenv import dotenv_values
from pydantic import Field, field_validator, model_validator

from .config import DEFAULT_ENV_PATH, load_asset_storage_settings
from .dataset_import import load_import_settings
from .export import load_export_settings
from .dataset_http import MultipartPolicy
from ..models.correction import StrictRecord


class CorrectionError(RuntimeError):
    CODES = {
        'STALE_SAMPLE_REVISION': (409, 'The sample changed. Refresh before resubmitting.'),
        'MASK_UPLOAD_UNAVAILABLE': (409, 'The staged mask is expired, consumed or belongs to different evidence.'),
        'AUDIT_NOT_READY': (409, 'Wait for a terminal parent audit before rechecking.'),
        'AUDIT_NOT_RECHECKABLE': (409, 'The parent audit has no supported immutable catalogue lineage or feedback policy.'),
        'SOURCE_AUDIT_MISMATCH': (409, 'Refresh the sample: the source audit does not pin this exact base revision.'),
        'SOURCE_NOT_CORRECTABLE': (422, 'A verified PNG image and a valid resulting integer-ID mask are required.'),
        'VALIDATION_ERROR': (422, 'One or more correction or recheck fields are invalid.'),
        'UPLOAD_TOO_LARGE': (413, 'The correction request exceeds a configured resource limit.'),
        'UNSUPPORTED_MEDIA_TYPE': (415, 'Upload a multipart PNG mask without content encoding.'),
        'DEPENDENCY_UNAVAILABLE': (503, 'Correction storage or durable publication is unavailable.'),
    }

    def __init__(self, code):
        self.status, message = self.CODES[code]
        self.code = code
        super().__init__(message)


class CorrectionStorageError(CorrectionError):
    def __init__(self):
        super().__init__('DEPENDENCY_UNAVAILABLE')


class CorrectionCommitUnknown(CorrectionStorageError):
    commit_outcome_unknown = True


class CorrectionPolicy(StrictRecord):
    max_mask_bytes: int = Field(default=16 * 1024**2, ge=1, le=64 * 1024**2)
    max_asset_bytes: int = Field(default=64 * 1024**2, ge=1, le=256 * 1024**2)
    max_annotation_bytes: int = Field(default=2 * 1024**2, ge=1024, le=16 * 1024**2)
    max_pixels: int = Field(default=16_000_000, ge=1, le=64_000_000)
    upload_ttl_seconds: int = Field(default=3600, ge=60, le=86400)


    @model_validator(mode='after')
    def coherent_limits(self):
        if max(self.max_mask_bytes, self.max_annotation_bytes) > self.max_asset_bytes:
            raise ValueError('Published correction assets must fit the verified-asset limit.')
        return self


class CorrectionSettings(StrictRecord):
    asset_root: Path
    policy: CorrectionPolicy = Field(default_factory=CorrectionPolicy)
    multipart: MultipartPolicy = Field(default_factory=MultipartPolicy)

    @field_validator('asset_root')
    @classmethod
    def absolute_root(cls, value):
        if not value.is_absolute():
            raise ValueError('Managed asset roots must be absolute.')
        return value

    @property
    def body_limit(self):
        return self.policy.max_mask_bytes + self.multipart.overhead_bytes


def load_correction_settings(*, env_path=None):
    path = DEFAULT_ENV_PATH if env_path is None else Path(env_path)
    try:
        values = {**dotenv_values(path, interpolate=False), **os.environ}
        policy = CorrectionPolicy(**{k: int(values['CORRECTION_' + k.upper()]) for k in CorrectionPolicy.model_fields if 'CORRECTION_' + k.upper() in values})
        root = load_asset_storage_settings(env_path=path).root
        # Files enter the existing asset root, never a public/static directory.
        # Reject existing root overlap instead of changing import/export policies.
        imports = load_import_settings(env_path=path)
        exports = load_export_settings(env_path=path)
        resolved = root.resolve(strict=False)
        for other in (imports.root, exports.root):
            other = other.resolve(strict=False)
            if resolved == other or resolved in other.parents or other in resolved.parents:
                raise ValueError('Private roots overlap.')
        return CorrectionSettings(asset_root=resolved, policy=policy)
    except (OSError, ValueError, RuntimeError) as error:
        raise CorrectionStorageError() from error
