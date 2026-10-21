"""Request-only dataset HTTP resource settings and narrow client error categories."""
from pathlib import Path
import os
from typing import Literal

from dotenv import dotenv_values
from fastapi import UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import DEFAULT_ENV_PATH
from .dataset_import import ImportConfigurationError, ImportSettings, load_import_settings
from ..models.dataset import Identifier


class DatasetRequestError(ValueError):
    def __init__(self, field: str | None = None):
        if field not in {None, "archive", "name", "format", "query", "multipart", "headers"}:
            raise ValueError("Unsupported request field location.")
        self.field = field
        super().__init__("One or more dataset request fields are invalid.")


class DatasetUnsupportedMediaError(ValueError):
    """Not a supported multipart/identity-encoded ZIP upload representation."""


class DatasetHttpDependencyError(RuntimeError):
    """Narrow parser/configuration prerequisite failure; never a public raw exception."""


class DatasetUpload(BaseModel):
    """Actual parsed multipart inputs; file is a handle, never a bytes blob."""
    model_config = ConfigDict(extra="forbid", strict=True)
    archive: UploadFile = Field(json_schema_extra={"format": "binary"})
    name: Identifier
    format: Literal["penn_fudan"]


class MultipartPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    overhead_bytes: int = Field(default=65536, ge=16384, le=1048576)
    scalar_bytes: int = Field(default=1024, ge=1024, le=8192)
    part_header_bytes: int = Field(default=8192, ge=512, le=65536)
    spool_bytes: int = Field(default=1048576, ge=65536, le=8388608)

    @model_validator(mode="after")
    def coherent(self):
        if self.overhead_bytes < 3 * self.part_header_bytes + 2 * self.scalar_bytes + 4096:
            raise ValueError("Multipart overhead must accommodate headers and text fields.")
        return self


class DatasetHttpSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    imports: ImportSettings
    multipart: MultipartPolicy = Field(default_factory=MultipartPolicy)

    @property
    def body_limit(self):
        return self.imports.policy.max_archive_bytes + self.multipart.overhead_bytes


def load_dataset_http_settings(*, env_path=None):
    """No directories, connections, workers or uploads are created while loading."""
    path = DEFAULT_ENV_PATH if env_path is None else Path(env_path)
    try:
        values = {**dotenv_values(path, interpolate=False), **os.environ}
        overrides = {}
        for field in MultipartPolicy.model_fields:
            key = "DATASET_HTTP_" + field.upper()
            if key in values:
                value = values[key]
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("Empty multipart setting.")
                overrides[field] = int(value)
        return DatasetHttpSettings(imports=load_import_settings(env_path=path),
                                   multipart=MultipartPolicy(**overrides))
    except (OSError, UnicodeError, ValueError, RuntimeError) as error:
        raise ImportConfigurationError("Dataset HTTP settings could not be loaded.") from error
