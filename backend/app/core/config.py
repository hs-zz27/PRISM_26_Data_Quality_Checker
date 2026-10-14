"""Load and validate backend configuration independently of the working directory."""

import tomllib
from ipaddress import ip_address
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


OLLAMA_CONFIG_PATH = Path(__file__).resolve().parents[2] / "ollama.toml"


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
