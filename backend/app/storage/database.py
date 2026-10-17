"""PostgreSQL connections and explicit, transactional schema initialization."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..core.config import DatabaseConfigurationError, DatabaseSettings, load_database_settings


MIGRATIONS_DIRECTORY = Path(__file__).with_name("migrations")
# A separate advisory-lock namespace from per-rulebook write locks.
_MIGRATION_LOCK = (1347572557, 1)
_STATEMENT_TIMEOUT_MS = 30_000
_LOCK_TIMEOUT_MS = 5_000


class DatabaseError(RuntimeError):
    """PostgreSQL is unavailable or an operation could not be completed."""


class CommitOutcomeUnknownError(DatabaseError):
    """The server may have committed before the connection failed."""


_RETRYABLE_SQLSTATES = {
    "40001",  # serialization_failure
    "40P01",  # deadlock_detected
    "55P03",  # lock_not_available
    "57P01",  # admin_shutdown
    "57P02",  # crash_shutdown
    "57P03",  # cannot_connect_now
    "08000",  # connection_exception
    "08003",  # connection_does_not_exist
    "08006",  # connection_failure
}


def is_retryable_database_error(error: BaseException) -> bool:
    """Classify only recognized transient PostgreSQL failures for retries."""
    current: BaseException | None = error
    while current is not None:
        if getattr(current, "sqlstate", None) in _RETRYABLE_SQLSTATES:
            return True
        current = current.__cause__
    return False


@contextmanager
def database_connection(
    settings: DatabaseSettings, *, readonly: bool = False,
) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    """Own one connection and transaction, committing or rolling back on exit.

    Read transactions use one consistent snapshot for pagination. Writers use
    READ COMMITTED so a query after waiting for a rulebook lock sees its latest
    committed version. Connections close on both successful and failed calls.
    """
    connection: psycopg.Connection[dict[str, Any]] | None = None
    try:
        connection = psycopg.connect(
            settings.conninfo.get_secret_value(),
            connect_timeout=settings.connect_timeout_seconds,
            row_factory=dict_row,
        )
        try:
            connection.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                if readonly else "SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE"
            )
            connection.execute(
                "SELECT set_config('statement_timeout', %s, true), "
                "set_config('lock_timeout', %s, true)",
                (str(_STATEMENT_TIMEOUT_MS), str(_LOCK_TIMEOUT_MS)),
            )
            yield connection
        except psycopg.Error as error:
            try:
                connection.rollback()
            except psycopg.Error:
                pass
            raise DatabaseError("Cannot read or write the PostgreSQL database.") from error
        else:
            try:
                connection.commit()
            except psycopg.Error as error:
                # A server rejection (for example a deferred constraint or
                # serialization failure) has a known rollback outcome. A lost
                # connection, including failures without SQLSTATE, does not.
                sqlstate = error.sqlstate
                if sqlstate is not None and not (
                    sqlstate.startswith("08") or sqlstate in {"57P01", "57P02", "57P03"}
                ):
                    raise DatabaseError("The PostgreSQL transaction did not commit.") from error
                raise CommitOutcomeUnknownError(
                    "The PostgreSQL commit outcome is unknown."
                ) from error
    except psycopg.Error as error:
        raise DatabaseError("Cannot read or write the PostgreSQL database.") from error
    finally:
        if connection is not None:
            connection.close()


def initialize_database(settings: DatabaseSettings | None = None) -> None:
    """Apply unapplied SQL migrations in one transaction, safely on repeated runs.

    Imports and repository construction never create or alter database tables.
    Concurrent initializers are serialized using a transaction advisory lock.
    An unrecognized migration history fails instead of guessing how to upgrade.
    """
    settings = load_database_settings() if settings is None else settings
    migrations = sorted(MIGRATIONS_DIRECTORY.glob("[0-9][0-9][0-9]_*.sql"))
    versions = [int(path.name[:3]) for path in migrations]
    if not versions or versions != list(range(1, len(versions) + 1)):
        raise DatabaseError("Database migration files are missing or out of order.")

    with database_connection(settings) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(%s, %s)", _MIGRATION_LOCK)
        connection.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   version INTEGER PRIMARY KEY CHECK (version >= 1),
                   applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        applied = [row["version"] for row in connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()]
        if applied != versions[:len(applied)]:
            raise DatabaseError("The database migration history is incompatible with this backend.")
        for version, path in zip(versions, migrations):
            if version not in applied:
                connection.execute(path.read_text(encoding="utf-8"))
                connection.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))


def main() -> None:
    try:
        initialize_database()
    except (DatabaseConfigurationError, DatabaseError) as error:
        raise SystemExit(str(error)) from None
    print("PostgreSQL schema is ready.")


if __name__ == "__main__":
    main()
