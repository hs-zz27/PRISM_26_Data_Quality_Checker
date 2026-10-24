"""Validate local model endpoints without resolving DNS or making requests."""

import os
from ipaddress import ip_address
from urllib.parse import urlsplit


def validate_local_model_host(host: str) -> str:
    """Allow loopback, plus Docker's exact host alias when explicitly enabled.

    The Compose deployment sets the opt-in in the container environment. Native
    execution remains loopback-only. Credentials, routing overrides and other
    hostnames are never accepted, even when Docker access is enabled.
    """
    parsed = urlsplit(host)
    hostname = parsed.hostname
    local = hostname == "localhost"
    if hostname == "host.docker.internal":
        local = os.environ.get("CDIF_ALLOW_DOCKER_OLLAMA") == "1"
    elif not local and hostname is not None:
        try:
            local = ip_address(hostname).is_loopback
        except ValueError:
            pass
    if (
        parsed.scheme not in ("http", "https")
        or not local
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.port == 0
    ):
        raise ValueError(
            "Model host must be a loopback URL (or explicitly enabled Docker host) "
            "without credentials, paths or queries."
        )
    return host
