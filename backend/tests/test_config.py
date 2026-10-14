import tomllib

import pytest
from pydantic import ValidationError

from backend.app.core.config import OLLAMA_CONFIG_PATH, OllamaSettings, load_ollama_settings


def test_default_configuration_is_independent_of_working_directory(tmp_path, monkeypatch):
    expected = load_ollama_settings()
    monkeypatch.chdir(tmp_path)
    assert load_ollama_settings() == expected


def test_alternate_toml_file_selects_model(tmp_path):
    path = tmp_path / "ollama.toml"
    path.write_text(
        OLLAMA_CONFIG_PATH.read_text().replace(
            load_ollama_settings().model, "another-local-model:latest"
        )
    )
    assert load_ollama_settings(path).model == "another-local-model:latest"


def test_missing_configuration_does_not_silently_use_defaults(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_ollama_settings(tmp_path / "missing.toml")


def test_malformed_toml_is_reported(tmp_path):
    path = tmp_path / "ollama.toml"
    path.write_text("[ollama\n")
    with pytest.raises(tomllib.TOMLDecodeError):
        load_ollama_settings(path)


@pytest.mark.parametrize("contents", [
    "[wrong_section]\nmodel = 'local:latest'\n",
    "[ollama]\nmodel = 'local:latest'\n",
    OLLAMA_CONFIG_PATH.read_text() + "unsupported_setting = true\n",
])
def test_missing_or_unknown_settings_are_rejected(tmp_path, contents):
    path = tmp_path / "ollama.toml"
    path.write_text(contents)
    with pytest.raises(ValidationError):
        load_ollama_settings(path)


@pytest.mark.parametrize("changes", [
    {"model": " "},
    {"host": "https://ollama.com"},
    {"host": "http://127.0.0.1:99999"},
    {"timeout_seconds": float("inf")},
    {"timeout_seconds": -1},
    {"context_length": 0},
    {"context_length": True},
    {"max_output_tokens": -1},
    {"max_output_tokens": "512"},
])
def test_invalid_settings_are_rejected(changes):
    values = {**load_ollama_settings().model_dump(), **changes}
    with pytest.raises(ValidationError):
        OllamaSettings.model_validate(values)


def test_shared_settings_cannot_be_modified():
    settings = load_ollama_settings()
    with pytest.raises(ValidationError, match="frozen"):
        settings.model = "replacement:latest"
