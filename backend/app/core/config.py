"""Load and validate backend configuration independently of the working directory."""

import os
import tomllib
from ipaddress import ip_address
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from dotenv import dotenv_values
from psycopg import ProgrammingError
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, field_validator


BACKEND_DIRECTORY = Path(__file__).resolve().parents[2]
OLLAMA_CONFIG_PATH = BACKEND_DIRECTORY / "ollama.toml"
DEFAULT_ENV_PATH = BACKEND_DIRECTORY.parent / ".env"


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

    @field_validator("host")
    @classmethod
    def require_loopback_host(cls, host: str) -> str:
        """Keep requests local and reject credentials or URL routing overrides."""
        parsed = urlsplit(host)
        hostname = parsed.hostname
        local = hostname == "localhost"
        if not local and hostname is not None:
            try:
                local = ip_address(hostname).is_loopback
            except ValueError:
                local = False
        if (
            parsed.scheme not in ("http", "https")
            or not local
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Ollama host must be a loopback URL without credentials, paths or queries."
            )
        # Accessing port also validates malformed or out-of-range numbers.
        if parsed.port == 0:
            raise ValueError("Ollama port must be greater than zero.")
        return host


class _BackendConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ollama: OllamaSettings


def load_ollama_settings(path: str | Path | None = None) -> OllamaSettings:
    """Read settings from TOML, without caching or silently replacing bad values.

    The default path is resolved from this module. Explicit paths are useful
    for tests or alternate local configurations. File, TOML and validation
    errors propagate to the caller before any inference request is made.
    """
    config_path = OLLAMA_CONFIG_PATH if path is None else Path(path)
    with config_path.open("rb") as file:
        configuration = tomllib.load(file)
    return _BackendConfiguration.model_validate(configuration).ollama


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
