import os
import tomllib

import pytest
from pydantic import ValidationError

from backend.app.core.config import OLLAMA_CONFIG_PATH, OllamaSettings, load_ollama_settings
from backend.app.core import config


@pytest.fixture(autouse=True)
def isolated_ollama_environment(tmp_path, monkeypatch):
    for name in ("OLLAMA_MODEL", "OLLAMA_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "DEFAULT_ENV_PATH", tmp_path / ".env")


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


def test_dotenv_overrides_model_and_port_from_any_working_directory(tmp_path, monkeypatch):
    defaults = load_ollama_settings()
    config.DEFAULT_ENV_PATH.write_text(
        "OLLAMA_MODEL=another-local-model:latest\n"
        "OLLAMA_BASE_URL=http://127.0.0.1:11435\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    settings = load_ollama_settings()
    assert settings.model == "another-local-model:latest"
    assert settings.host == "http://127.0.0.1:11435"
    assert settings.timeout_seconds == defaults.timeout_seconds
    assert settings.context_length == defaults.context_length
    assert settings.max_output_tokens == defaults.max_output_tokens


def test_process_environment_overrides_dotenv_without_mutation(monkeypatch):
    config.DEFAULT_ENV_PATH.write_text(
        "OLLAMA_MODEL=file-model:latest\n"
        "OLLAMA_BASE_URL=http://127.0.0.1:11435\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OLLAMA_MODEL", "process-model:latest")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11436")
    before = dict(os.environ)
    settings = load_ollama_settings()
    assert settings.model == "process-model:latest"
    assert settings.host == "http://127.0.0.1:11436"
    assert dict(os.environ) == before


def test_dotenv_only_changes_specified_settings_without_mutating_environment():
    defaults = load_ollama_settings()
    config.DEFAULT_ENV_PATH.write_text("OLLAMA_MODEL=local-override:latest\n", encoding="utf-8")
    before = dict(os.environ)
    settings = load_ollama_settings()
    assert settings.model == "local-override:latest"
    assert settings.host == defaults.host
    assert dict(os.environ) == before


def test_explicit_env_file_does_not_read_root_env(tmp_path):
    config.DEFAULT_ENV_PATH.write_text("OLLAMA_MODEL=root-model:latest\n", encoding="utf-8")
    alternate = tmp_path / "alternate.env"
    alternate.write_text("OLLAMA_MODEL=alternate-model:latest\n", encoding="utf-8")
    assert load_ollama_settings(env_path=alternate).model == "alternate-model:latest"
    defaults = tomllib.loads(OLLAMA_CONFIG_PATH.read_text())["ollama"]
    assert load_ollama_settings(env_path=tmp_path / "missing.env").model == defaults["model"]


def test_overrides_are_reloaded_on_each_call(monkeypatch):
    config.DEFAULT_ENV_PATH.write_text("OLLAMA_MODEL=first:latest\n", encoding="utf-8")
    first = load_ollama_settings()
    config.DEFAULT_ENV_PATH.write_text("OLLAMA_MODEL=second:latest\n", encoding="utf-8")
    assert load_ollama_settings().model == "second:latest"
    monkeypatch.setenv("OLLAMA_MODEL", "third:latest")
    assert load_ollama_settings().model == "third:latest"
    assert first.model == "first:latest"


@pytest.mark.parametrize("contents", [
    "OLLAMA_MODEL=\n", "OLLAMA_MODEL\n", "OLLAMA_BASE_URL=\n",
    "OLLAMA_BASE_URL=http://127.0.0.1:99999\n",
    "OLLAMA_BASE_URL=https://cloud.example\n",
])
def test_invalid_dotenv_overrides_are_rejected(contents):
    config.DEFAULT_ENV_PATH.write_text(contents, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_ollama_settings()


def test_invalid_process_override_does_not_fall_back(monkeypatch):
    config.DEFAULT_ENV_PATH.write_text("OLLAMA_MODEL=file-model:latest\n", encoding="utf-8")
    monkeypatch.setenv("OLLAMA_MODEL", " ")
    with pytest.raises(ValidationError):
        load_ollama_settings()


def test_server_bind_setting_does_not_override_client_url(monkeypatch):
    defaults = load_ollama_settings()
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11435")
    assert load_ollama_settings().host == defaults.host


def test_dotenv_values_are_not_interpolated(monkeypatch):
    monkeypatch.setenv("PRISM_MODEL_TAG", "substituted")
    config.DEFAULT_ENV_PATH.write_text(
        "OLLAMA_MODEL='model:${PRISM_MODEL_TAG}'\n", encoding="utf-8",
    )
    assert load_ollama_settings().model == "model:${PRISM_MODEL_TAG}"
