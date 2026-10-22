"""Validated execution settings and run-environment facts for the experiment runner.

Everything is explicit: no implicit root .env, no database and no hardcoded paths.
"""

from __future__ import annotations

import ipaddress
import os
import platform
import sys
from importlib import metadata
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import Field, StringConstraints, field_validator

from backend.app.models.feedback import EvidenceLimits, Text

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3-vl:4b-instruct"
ModelDigest = Annotated[str, StringConstraints(pattern="^sha256:[0-9a-f]{64}$")]
PACKAGES = ("numpy", "pillow", "pydantic", "httpx", "pytest")


class SettingsError(ValueError):
    """A run setting is invalid or unsafe; nothing has been requested yet."""


def require_loopback(host: str) -> str:
    """Keep every request, including its images, on this machine."""
    parsed = urlsplit(host)
    hostname = parsed.hostname
    local = hostname == "localhost"
    if not local and hostname is not None:
        try:
            local = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            local = False
    if (parsed.scheme not in ("http", "https") or not local or parsed.username is not None
            or parsed.password is not None or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise ValueError("Ollama host must be a loopback URL without credentials, paths or queries.")
    if parsed.port == 0:
        raise ValueError("Ollama port must be greater than zero.")
    return host


class ExecutionSettings(EvidenceLimits):
    """Transport and decoding settings that stay identical for both arms of a comparison.

    Evidence limits (image bytes, annotation bytes, pixels) come from the production
    EvidenceLimits. Unlike the production pin, preprocessing is not fixed here: it is a
    property of each variant and is recorded with every result.
    """

    host: str = DEFAULT_HOST
    model: Text = DEFAULT_MODEL
    model_digest: ModelDigest
    temperature: int = Field(default=0, ge=0, le=0)
    num_ctx: int = Field(default=16384, ge=1024, le=131072)
    num_predict: int = Field(default=2048, ge=128, le=2048)
    # None keeps Ollama's automatic selection; it is recorded explicitly as null.
    num_thread: int | None = Field(default=None, ge=1, le=1024)
    timeout_seconds: int = Field(default=600, ge=1, le=3600)
    max_request_bytes: int = Field(default=64 * 1024**2, ge=1024, le=128 * 1024**2)
    max_response_bytes: int = Field(default=65536, ge=1024, le=65536)
    keep_alive_seconds: int = Field(default=300, ge=0, le=3600)

    @field_validator("host")
    @classmethod
    def local_host_only(cls, host: str) -> str:
        return require_loopback(host)


def total_memory_bytes() -> int | None:
    """Best-effort physical memory without extra dependencies; None when unknown."""
    try:
        if sys.platform == "win32":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            ("total_physical", ctypes.c_ulonglong), ("avail_physical", ctypes.c_ulonglong),
                            ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                            ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                            ("avail_extended", ctypes.c_ulonglong)]

            status = MemoryStatus()
            status.length = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.total_physical)
            return None
        return int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        return None


def package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in PACKAGES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def collect_environment() -> dict[str, object]:
    """Hardware, operating system, Python and package facts recorded with every run."""
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "total_memory_bytes": total_memory_bytes(),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "packages": package_versions(),
    }
