"""Keep Docker host access explicit without opening remote model endpoints."""

import pytest
from pydantic import ValidationError

from backend.app.core.config import OllamaSettings
from backend.app.models.enclosure import EnclosureExecutionPin


def enclosure_pin(host):
    return EnclosureExecutionPin(
        host=host, model="test", model_digest="sha256:" + "a" * 64,
        system_prompt_sha256="b" * 64, instruction_sha256="c" * 64,
        num_ctx=1024, num_predict=128, request_timeout_seconds=60,
        sample_budget_seconds=120, max_boxes=12, max_request_bytes=1024,
        max_response_bytes=1024, keep_alive_seconds=300,
    )


@pytest.mark.parametrize("factory", [
    lambda host: OllamaSettings(model="test", host=host, timeout_seconds=1.0,
                                context_length=1024, max_output_tokens=128),
    lambda host: enclosure_pin(host),
])
def test_docker_alias_requires_explicit_opt_in(monkeypatch, factory):
    host = "http://host.docker.internal:11434"
    monkeypatch.delenv("CDIF_ALLOW_DOCKER_OLLAMA", raising=False)
    with pytest.raises(ValidationError):
        factory(host)
    monkeypatch.setenv("CDIF_ALLOW_DOCKER_OLLAMA", "1")
    assert factory(host).host == host
    # Saving and reloading a pin applies the same endpoint policy.
    model = factory(host)
    assert type(model).model_validate_json(model.model_dump_json()).host == host


@pytest.mark.parametrize("host", [
    "http://host.docker.internal.evil.test:11434",
    "http://192.168.1.10:11434",
    "http://host.docker.internal:11434/api",
    "http://@host.docker.internal:11434",
    "http://user:secret@host.docker.internal:11434",
    "http://host.docker.internal:11434?override=1",
    "http://host.docker.internal:11434#fragment",
    "http://host.docker.internal:0",
    "http://host.docker.internal:99999",
])
def test_docker_opt_in_keeps_other_restrictions(monkeypatch, host):
    monkeypatch.setenv("CDIF_ALLOW_DOCKER_OLLAMA", "1")
    for factory in (lambda: OllamaSettings(model="test", host=host, timeout_seconds=1.0,
                                           context_length=1024, max_output_tokens=128),
                    lambda: enclosure_pin(host)):
        with pytest.raises(ValidationError):
            factory()
