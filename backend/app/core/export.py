"""Export-only configuration and safe finite failures; no import-time I/O."""
import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import field_validator, model_validator

from .config import BACKEND_DIRECTORY, DEFAULT_ENV_PATH, load_asset_storage_settings
from ..models.export import ExportPolicy, Record


class ExportDependencyError(RuntimeError):
    """Storage, configuration or corrupt persisted export evidence."""


class ExportOutcomeUnknown(ExportDependencyError):
    """Stop and reconcile durable state; do not replay or remove possible outputs."""


class ExportNotFound(LookupError):
    pass


class AuditNotReady(RuntimeError):
    pass


class AuditNotExportable(RuntimeError):
    pass


class ExportNotReady(RuntimeError):
    pass


class ExportLimitError(RuntimeError):
    pass


class ExportStopped(RuntimeError):
    pass


ERROR_MESSAGES = {
    "EXPORT_FAILED": "Export processing could not be completed.",
    "EXPORT_STORAGE_FAILED": "Export source or artifact storage could not be verified.",
    "EXPORT_RESOURCE_LIMIT": "Export processing exceeded a configured resource limit.",
    "EXPORT_TIMEOUT": "Export processing exceeded its original time limit.",
    "EXPORT_INTERRUPTED": "Export processing was interrupted; request a new export to try again.",
    "EXPORT_INVALID_NAMES": "Selected sample identifiers cannot form an unambiguous archive.",
}


class ExportNameError(RuntimeError):
    pass


class ExportSettings(Record):
    root: Path
    asset_root: Path
    import_root: Path
    policy: ExportPolicy

    @field_validator("root", "asset_root", "import_root")
    @classmethod
    def absolute(cls, value):
        if not value.is_absolute():
            raise ValueError("Export storage roots must be absolute.")
        return value

    @model_validator(mode="after")
    def disjoint(self):
        root = self.root.resolve(strict=False)
        for other in (self.asset_root, self.import_root):
            other = other.resolve(strict=False)
            if root == other or root in other.parents or other in root.parents:
                raise ValueError("Export storage must be disjoint from source/import storage.")
        return self


def load_export_settings(*, env_path=None):
    path = DEFAULT_ENV_PATH if env_path is None else Path(env_path)
    try:
        values = {**dotenv_values(path, interpolate=False), **os.environ}
        policy = {}
        for field in ExportPolicy.model_fields:
            key = "EXPORT_" + field.upper()
            if key in values:
                raw = values[key]
                if not isinstance(raw, str) or not raw.strip():
                    raise ValueError("Empty resource setting.")
                policy[field] = int(raw)
        def root(key, default):
            raw = values.get(key, str(default))
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError("Empty root.")
            value = Path(raw).expanduser()
            if not value.is_absolute():
                raise ValueError("Relative root.")
            return value.resolve(strict=False)
        return ExportSettings(
            root=root("EXPORT_ROOT", BACKEND_DIRECTORY / "data" / "exports"),
            asset_root=load_asset_storage_settings(env_path=path).root,
            import_root=root("DATASET_IMPORT_ROOT", BACKEND_DIRECTORY / "data" / "imports"),
            policy=ExportPolicy(**policy),
        )
    except (OSError, UnicodeError, RuntimeError, ValueError) as error:
        raise ExportDependencyError("Export configuration is unavailable.") from error


class ExportRequestError(ValueError):
    """Narrow HTTP query validation failure, not a catch-all internal ValueError."""
