"""Load and validate backend configuration independently of the working directory."""

import os
import tomllib
from pathlib import Path
from typing import Annotated

from dotenv import dotenv_values
from psycopg import ProgrammingError
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import (
    BaseModel, ConfigDict, Field, SecretStr, StringConstraints, ValidationError, field_validator,
)

from .model_host import validate_local_model_host


BACKEND_DIRECTORY = Path(__file__).resolve().parents[2]
OLLAMA_CONFIG_PATH = BACKEND_DIRECTORY / "ollama.toml"
DEFAULT_ENV_PATH = BACKEND_DIRECTORY.parent / ".env"
# Keep consecutive local requests warm without reserving memory indefinitely.
OLLAMA_KEEP_ALIVE_SECONDS = 300


class OllamaSettings(BaseModel):
    """Immutable settings for a locally installed Ollama model.

    CPU execution, disabled proxies and disabled redirects are application
    policies enforced by the client, rather than configurable settings.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    model: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    host: str
    timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    context_length: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    cpu_threads: int | None = Field(
        default=None, ge=1, le=1024, exclude_if=lambda value: value is None,
    )

    @field_validator("host")
    @classmethod
    def require_loopback_host(cls, host: str) -> str:
        return validate_local_model_host(host)


class _BackendConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ollama: OllamaSettings


class AuditSettings(BaseModel):
    """Execution bounds for one sample in a durable batch audit."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    sample_max_seconds: int = Field(default=900, ge=1, le=86_400)


class AuditConfigurationError(ValueError):
    """Audit execution settings are missing or invalid."""


def load_audit_settings(*, env_path: str | Path | None = None) -> AuditSettings:
    """Load the hard sample-processing limit without changing the environment."""
    environment_path = DEFAULT_ENV_PATH if env_path is None else Path(env_path)
    try:
        values = {**dotenv_values(environment_path, interpolate=False), **os.environ}
    except OSError:
        raise AuditConfigurationError("Cannot read the audit environment file.") from None
    raw = values.get("AUDIT_SAMPLE_MAX_SECONDS", "900")
    try:
        return AuditSettings(sample_max_seconds=int(raw))
    except (TypeError, ValueError, ValidationError):
        raise AuditConfigurationError(
            "AUDIT_SAMPLE_MAX_SECONDS must be an integer between 1 and 86400."
        ) from None


class AssetStorageConfigurationError(ValueError):
    """Private local asset-storage settings are missing or invalid."""


class AssetStorageSettings(BaseModel):
    """Immutable, private root for captured source-asset bytes."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    root: Path

    @field_validator("root")
    @classmethod
    def require_absolute_root(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("Asset storage root must be an absolute path.")
        return value


def load_asset_storage_settings(*, env_path: str | Path | None = None) -> AssetStorageSettings:
    """Load a private asset root without creating it or changing the environment."""
    environment_path = DEFAULT_ENV_PATH if env_path is None else Path(env_path)
    try:
        values = {**dotenv_values(environment_path, interpolate=False), **os.environ}
    except OSError:
        raise AssetStorageConfigurationError("Cannot read the asset storage environment file.") from None
    if "ASSET_STORAGE_ROOT" not in values:
        return AssetStorageSettings(root=BACKEND_DIRECTORY / "data" / "assets")
    raw = values["ASSET_STORAGE_ROOT"]
    if not isinstance(raw, str) or not raw.strip():
        raise AssetStorageConfigurationError("ASSET_STORAGE_ROOT must be a non-empty absolute path.")
    try:
        root = Path(raw).expanduser()
        if not root.is_absolute():
            raise ValueError
        return AssetStorageSettings(root=root.resolve(strict=False))
    except (OSError, RuntimeError, ValueError):
        raise AssetStorageConfigurationError(
            "ASSET_STORAGE_ROOT must be a non-empty absolute path."
        ) from None


def load_ollama_settings(
    path: str | Path | None = None, *, env_path: str | Path | None = None,
) -> OllamaSettings:
    """Read TOML defaults with optional model, base URL and CPU thread overrides.

    Process environment overrides root .env, which overrides TOML. Loading
    never changes the environment or caches values. Missing .env files are
    allowed; invalid values fail before an inference request. OLLAMA_BASE_URL
    selects this client's URL; OLLAMA_HOST controls Ollama's server separately.
    """
    config_path = OLLAMA_CONFIG_PATH if path is None else Path(path)
    with config_path.open("rb") as file:
        configuration = tomllib.load(file)
    settings = _BackendConfiguration.model_validate(configuration).ollama.model_dump()
    environment_path = DEFAULT_ENV_PATH if env_path is None else Path(env_path)
    values = {**dotenv_values(environment_path, interpolate=False), **os.environ}
    for name, field in (("OLLAMA_MODEL", "model"), ("OLLAMA_BASE_URL", "host")):
        if name in values:
            settings[field] = values[name]
    if "OLLAMA_CPU_THREADS" in values:
        raw = values["OLLAMA_CPU_THREADS"]
        if isinstance(raw, str) and raw.strip().lower() == "auto":
            settings["cpu_threads"] = None
        else:
            try:
                settings["cpu_threads"] = int(raw)
            except (TypeError, ValueError):
                raise ValueError("OLLAMA_CPU_THREADS must be a positive integer or 'auto'.") from None
    return OllamaSettings.model_validate(settings)


class DatabaseConfigurationError(ValueError):
    """PostgreSQL configuration is missing or invalid."""


def _database_integer(value: str, name: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise DatabaseConfigurationError(f"{name} must be an integer.") from None
    if not minimum <= number <= maximum:
        raise DatabaseConfigurationError(f"{name} must be between {minimum} and {maximum}.")
    return number


class DatabaseSettings(BaseModel):
    """Immutable connection settings with credentials hidden from representations."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    conninfo: SecretStr
    connect_timeout_seconds: int = Field(default=5, ge=1, le=60)

    @field_validator("conninfo")
    @classmethod
    def validate_conninfo(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw.strip():
            raise ValueError("PostgreSQL connection settings must not be empty.")
        try:
            parameters = conninfo_to_dict(raw)
            if "port" in parameters:
                _database_integer(parameters["port"], "PostgreSQL port", 1, 65535)
        except (ProgrammingError, DatabaseConfigurationError):
            raise ValueError("PostgreSQL connection settings are invalid.") from None
        return value


def load_database_settings(path: str | Path | None = None) -> DatabaseSettings:
    """Read root .env settings, with process environment taking precedence.

    Loading never changes the process environment. Interpolation is disabled
    so passwords containing dollar signs remain literal. DATABASE_URL can
    override individual POSTGRES_* values for a separately managed server.
    """
    env_path = DEFAULT_ENV_PATH if path is None else Path(path)
    try:
        values = {**dotenv_values(env_path, interpolate=False), **os.environ}
    except OSError:
        raise DatabaseConfigurationError("Cannot read the PostgreSQL environment file.") from None

    timeout = _database_integer(
        values.get("POSTGRES_CONNECT_TIMEOUT", "5"), "POSTGRES_CONNECT_TIMEOUT", 1, 60
    )
    url = values.get("DATABASE_URL")
    if url:
        if not url.startswith(("postgresql://", "postgres://")):
            raise DatabaseConfigurationError("DATABASE_URL must be a PostgreSQL connection URL.")
        conninfo = url
    else:
        for name in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
            value = values.get(name)
            if value is None or not value.strip():
                raise DatabaseConfigurationError(f"Set {name} in the root .env file or environment.")
        host = values.get("POSTGRES_HOST", "127.0.0.1")
        if host is None or not host.strip():
            raise DatabaseConfigurationError("POSTGRES_HOST must not be empty.")
        port = _database_integer(values.get("POSTGRES_PORT", "55432"), "POSTGRES_PORT", 1, 65535)
        try:
            conninfo = make_conninfo(
                host=host, port=port, user=values["POSTGRES_USER"],
                password=values["POSTGRES_PASSWORD"], dbname=values["POSTGRES_DB"],
            )
        except ProgrammingError:
            raise DatabaseConfigurationError("PostgreSQL connection settings are invalid.") from None
    try:
        return DatabaseSettings(conninfo=SecretStr(conninfo), connect_timeout_seconds=timeout)
    except ValueError:
        raise DatabaseConfigurationError("PostgreSQL connection settings are invalid.") from None
