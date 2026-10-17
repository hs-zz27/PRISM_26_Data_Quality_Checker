"""Database configuration tests that never need a running PostgreSQL server."""

import os

import psycopg
from psycopg.conninfo import conninfo_to_dict
from pydantic import SecretStr, ValidationError
import pytest

from backend.app.core import config
from backend.app.core.config import (
    DatabaseConfigurationError,
    DatabaseSettings,
    load_database_settings,
)
from backend.app.storage import database
from backend.app.storage.database import (
    CommitOutcomeUnknownError, DatabaseError, database_connection, is_retryable_database_error,
)


DATABASE_ENVIRONMENT_KEYS = (
    "DATABASE_URL", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB",
    "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_CONNECT_TIMEOUT",
)


@pytest.fixture(autouse=True)
def clean_database_environment(monkeypatch):
    for name in DATABASE_ENVIRONMENT_KEYS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "POSTGRES_USER=prism_test\n"
        "POSTGRES_PASSWORD=example-password\n"
        "POSTGRES_DB=prism_test\n",
        encoding="utf-8",
    )
    return path


def connection_parameters(settings):
    return conninfo_to_dict(settings.conninfo.get_secret_value())


def test_dotenv_credentials_use_local_docker_defaults(env_file):
    settings = load_database_settings(env_file)
    assert connection_parameters(settings) == {
        "user": "prism_test", "password": "example-password", "dbname": "prism_test",
        "host": "127.0.0.1", "port": "55432",
    }
    assert settings.connect_timeout_seconds == 5


def test_environment_values_take_precedence_without_mutating_environment(env_file, monkeypatch):
    monkeypatch.setenv("POSTGRES_USER", "process-user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "process-password")
    monkeypatch.setenv("POSTGRES_HOST", "database.internal")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("POSTGRES_CONNECT_TIMEOUT", "17")
    before = dict(os.environ)
    settings = load_database_settings(env_file)
    assert connection_parameters(settings) == {
        "user": "process-user", "password": "process-password", "dbname": "prism_test",
        "host": "database.internal", "port": "6543",
    }
    assert settings.connect_timeout_seconds == 17
    assert dict(os.environ) == before


def test_dotenv_does_not_interpolate_secrets(env_file, monkeypatch):
    monkeypatch.setenv("PRISM_SECRET_REFERENCE", "must-not-be-substituted")
    password = "literal${PRISM_SECRET_REFERENCE} # ' /:@"
    env_file.write_text(
        f'POSTGRES_USER=prism_test\nPOSTGRES_PASSWORD="{password}"\nPOSTGRES_DB=prism_test\n',
        encoding="utf-8",
    )
    assert connection_parameters(load_database_settings(env_file))["password"] == password


def test_password_characters_are_preserved_as_connection_values(env_file, monkeypatch):
    password = " spaces ' \\ / : @ # = \n end "
    monkeypatch.setenv("POSTGRES_PASSWORD", password)
    assert connection_parameters(load_database_settings(env_file))["password"] == password


@pytest.mark.parametrize("scheme", ["postgresql", "postgres"])
def test_database_url_overrides_individual_connection_fields(env_file, monkeypatch, scheme):
    monkeypatch.setenv(
        "DATABASE_URL", f"{scheme}://url_user:p%40ss%20word@database.example:6432/url_db?sslmode=require"
    )
    settings = load_database_settings(env_file)
    assert connection_parameters(settings) == {
        "user": "url_user", "password": "p@ss word", "host": "database.example",
        "port": "6432", "dbname": "url_db", "sslmode": "require",
    }


def test_environment_only_configuration_does_not_require_a_dotenv_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example:password@127.0.0.1:55432/example")
    settings = load_database_settings(tmp_path / "not-created.env")
    assert connection_parameters(settings)["dbname"] == "example"


def test_default_dotenv_path_does_not_depend_on_working_directory(env_file, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_ENV_PATH", env_file)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert load_database_settings() == load_database_settings(env_file)


def test_loading_configuration_does_not_cache_previous_credentials(env_file, monkeypatch):
    first = load_database_settings(env_file)
    monkeypatch.setenv("POSTGRES_PASSWORD", "updated-password")
    second = load_database_settings(env_file)
    assert connection_parameters(first)["password"] == "example-password"
    assert connection_parameters(second)["password"] == "updated-password"


@pytest.mark.parametrize("name", ["POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"])
@pytest.mark.parametrize("value", ["", "   "])
def test_blank_required_credentials_are_rejected(env_file, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(DatabaseConfigurationError):
        load_database_settings(env_file)


def test_missing_credentials_are_reported_without_fabricated_defaults(tmp_path):
    with pytest.raises(DatabaseConfigurationError):
        load_database_settings(tmp_path / "missing.env")


@pytest.mark.parametrize("value", ["0", "-1", "65536", "not-a-number", "", "1.5"])
def test_invalid_postgres_ports_are_rejected(env_file, monkeypatch, value):
    monkeypatch.setenv("POSTGRES_PORT", value)
    with pytest.raises(DatabaseConfigurationError):
        load_database_settings(env_file)


@pytest.mark.parametrize("value", ["0", "-1", "61", "not-a-number", "", "1.5"])
def test_invalid_connection_timeouts_are_rejected(env_file, monkeypatch, value):
    monkeypatch.setenv("POSTGRES_CONNECT_TIMEOUT", value)
    with pytest.raises(DatabaseConfigurationError):
        load_database_settings(env_file)


@pytest.mark.parametrize("url", [
    "sqlite:///records.db", "mysql://user:secret@localhost/db", "host=localhost password=secret",
    "postgresql://user:secret@localhost:invalid/db", "postgresql://user:secret@localhost/db?unknown_option=value",
])
def test_invalid_database_urls_are_rejected_without_echoing_credentials(env_file, monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)
    with pytest.raises(DatabaseConfigurationError) as caught:
        load_database_settings(env_file)
    assert "secret" not in str(caught.value)
    assert url not in str(caught.value)


def test_database_settings_are_frozen_and_hide_connection_credentials(env_file):
    settings = load_database_settings(env_file)
    assert isinstance(settings.conninfo, SecretStr)
    assert "example-password" not in repr(settings)
    assert "example-password" not in settings.model_dump_json()
    with pytest.raises(ValidationError, match="frozen"):
        settings.connect_timeout_seconds = 10


@pytest.mark.parametrize("changes", [
    {"connect_timeout_seconds": True}, {"connect_timeout_seconds": "5"},
    {"connect_timeout_seconds": 0}, {"connect_timeout_seconds": 61}, {"unexpected": "value"},
])
def test_direct_database_settings_require_valid_strict_values(changes):
    with pytest.raises(ValidationError):
        DatabaseSettings.model_validate({
            "conninfo": SecretStr("host=127.0.0.1 port=55432 dbname=prism"), **changes,
        })


def test_retry_classifier_accepts_known_transient_errors_only():
    transient = psycopg.errors.SerializationFailure()
    wrapped = DatabaseError("database operation failed")
    wrapped.__cause__ = transient
    assert is_retryable_database_error(transient)
    assert is_retryable_database_error(wrapped)
    assert not is_retryable_database_error(ValueError("invalid rule parameters"))


@pytest.mark.parametrize("error", [
    psycopg.OperationalError("authentication failed"),
    psycopg.InterfaceError("client protocol misuse"),
    psycopg.errors.InvalidPassword("bad password"),
])
def test_retry_classifier_rejects_permanent_connection_failures(error):
    assert not is_retryable_database_error(error)


@pytest.mark.parametrize("commit_error,expected_error", [
    (psycopg.OperationalError("acknowledgement lost"), CommitOutcomeUnknownError),
    (psycopg.errors.ConnectionFailure("connection interrupted"), CommitOutcomeUnknownError),
    (psycopg.errors.SerializationFailure("rolled back"), DatabaseError),
    (psycopg.errors.UniqueViolation("constraint rejected"), DatabaseError),
])
def test_commit_errors_distinguish_rejected_transactions_from_unknown_outcomes(
    monkeypatch, commit_error, expected_error,
):
    class Connection:
        closed = False

        def execute(self, *args):
            pass

        def commit(self):
            raise commit_error

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(database.psycopg, "connect", lambda *args, **kwargs: connection)
    settings = DatabaseSettings(conninfo=SecretStr("dbname=unused"))
    with pytest.raises(expected_error) as caught:
        with database_connection(settings):
            pass
    assert type(caught.value) is expected_error
    assert connection.closed
