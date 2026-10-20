"""Server-only import settings and finite safe failures. Loading has no side effects."""

from pathlib import Path
import os

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from .config import BACKEND_DIRECTORY, DEFAULT_ENV_PATH, load_asset_storage_settings
from ..models.dataset_import import ImportPolicy

ERROR_MESSAGES = {
    "INVALID_ARCHIVE": "The archive is not a usable supported ZIP archive.",
    "INVALID_LAYOUT": "The archive has no unambiguous recognizable sample layout.",
    "IMPORT_RESOURCE_LIMIT": "The import exceeded a configured resource limit.",
    "IMPORT_STORAGE_FAILED": "Private import storage could not be used.",
    "IMPORT_TIMEOUT": "The import exceeded its processing time limit.",
    "IMPORT_INTERRUPTED": "Import processing was interrupted; submit a new archive to try again.",
    "IMPORT_FAILED": "The import could not be completed.",
}


class ImportConfigurationError(ValueError):
    """Safe configuration category; never include a path or connection value."""


class ImportInputError(ValueError):
    def __init__(self, code="INVALID_ARCHIVE"):
        if code not in {"INVALID_ARCHIVE", "INVALID_LAYOUT"}:
            raise ValueError("Unsupported input error code.")
        self.code = code
        super().__init__(ERROR_MESSAGES[code])


class ImportResourceError(RuntimeError):
    code = "IMPORT_RESOURCE_LIMIT"


class ImportStorageError(RuntimeError):
    code = "IMPORT_STORAGE_FAILED"


class ImportAcceptanceError(RuntimeError):
    """Narrow safe 503 boundary for staging/acceptance; no accepted result is implied."""


class UploadTooLargeError(ValueError):
    """Measured streamed archive bytes exceed the server upload cap (future HTTP 413)."""


class ImportOutcomeUnresolved(RuntimeError):
    """Stop without replaying acceptance, publication, or archive execution."""


class ImportSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    root: Path
    asset_root: Path
    policy: ImportPolicy

    @field_validator("root", "asset_root")
    @classmethod
    def absolute(cls, value):
        if not value.is_absolute():
            raise ValueError("Import roots must be absolute.")
        return value

    @model_validator(mode="after")
    def disjoint(self):
        a, b = self.root.resolve(strict=False), self.asset_root.resolve(strict=False)
        if a == b or a in b.parents or b in a.parents:
            raise ValueError("Import and immutable asset roots must be disjoint.")
        return self


def load_import_settings(*, env_path=None) -> ImportSettings:
    """Root .env, then process environment. Never create folders/connect/spawn."""
    path = DEFAULT_ENV_PATH if env_path is None else Path(env_path)
    try:
        values = {**dotenv_values(path, interpolate=False), **os.environ}
        defaults = ImportPolicy()
        policy = {}
        for field in ImportPolicy.model_fields:
            key = "DATASET_IMPORT_" + field.upper()
            if key in values:
                raw = values[key]
                if not isinstance(raw, str) or not raw.strip():
                    raise ValueError("Empty import setting.")
                policy[field] = float(raw) if type(getattr(defaults, field)) is float else int(raw)
        raw_root = values.get("DATASET_IMPORT_ROOT", str(BACKEND_DIRECTORY / "data" / "imports"))
        if not isinstance(raw_root, str) or not raw_root.strip():
            raise ValueError("Empty import root.")
        root = Path(raw_root).expanduser()
        return ImportSettings(root=root.resolve(strict=False) if root.is_absolute() else root,
                              asset_root=load_asset_storage_settings(env_path=path).root,
                              policy=ImportPolicy(**policy))
    except (OSError, UnicodeError, ValueError, RuntimeError) as error:
        raise ImportConfigurationError("Dataset import settings could not be loaded.") from error
