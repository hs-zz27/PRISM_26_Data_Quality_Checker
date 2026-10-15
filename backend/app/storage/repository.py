"""Store validated rulebook versions in PostgreSQL."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from ..core.config import DatabaseSettings, load_database_settings
from ..models.rule import Rulebook, RulebookPage
from .database import DatabaseError, database_connection


class RulebookStorageError(RuntimeError):
    """The database is unavailable or a stored record is invalid."""


class RulebookNotFoundError(LookupError):
    """A requested rulebook or explicit version does not exist."""

    def __init__(self, rulebook_id: str, version: int | None) -> None:
        self.rulebook_id = rulebook_id
        self.version = version
        target = "latest version" if version is None else f"version {version}"
        super().__init__(f"Rulebook {rulebook_id!r}, {target}, was not found.")


class RulebookVersionConflictError(RuntimeError):
    """The caller's expected latest version no longer matches storage."""

    def __init__(self, rulebook_id: str, expected_version: int, actual_version: int) -> None:
        self.rulebook_id = rulebook_id
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"Rulebook {rulebook_id!r}: expected latest version {expected_version}, "
            f"but found {actual_version}."
        )


def _require_integer(value: int, name: str, minimum: int) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer greater than or equal to {minimum}.")


class RulebookRepository:
    """Persist append-only snapshots with optimistic concurrency checks.

    Construction loads settings without connecting or changing the schema.
    Connections belong to individual operations and are always closed.
    Confirmation transitions and their review history require a dedicated
    operation later; save_rulebook never overwrites a saved version.
    """

    def __init__(self, *, settings: DatabaseSettings | None = None) -> None:
        if settings is not None and not isinstance(settings, DatabaseSettings):
            raise TypeError("settings must be a DatabaseSettings instance.")
        self.settings = load_database_settings() if settings is None else settings

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        try:
            with database_connection(self.settings, readonly=not write) as connection:
                yield connection
        except DatabaseError as error:
            raise RulebookStorageError("Cannot read or write the rulebook database.") from error

    @staticmethod
    def _decode(row: Mapping[str, Any]) -> Rulebook:
        try:
            book = Rulebook.model_validate(row["document"])
        except ValidationError as error:
            raise RulebookStorageError("A saved rulebook document failed validation.") from error
        if (
            book.rulebook_id != row["rulebook_id"]
            or book.version != row["version"]
            or book.confirmation_state.value != row["confirmation_state"]
        ):
            raise RulebookStorageError("A saved rulebook document disagrees with its metadata.")
        return book

    def save_rulebook(self, rulebook: Rulebook, *, expected_version: int = 0) -> Rulebook:
        """Append a snapshot and return an independent validated copy.

        Creation expects version 0 in storage and a version-1 record. An edit
        expects the current latest version and supplies that version plus 1.
        Invalid records raise validation errors before a write transaction.
        Stale expectations raise RulebookVersionConflictError without changes.
        """
        _require_integer(expected_version, "expected_version", 0)
        if not isinstance(rulebook, Rulebook):
            raise TypeError("rulebook must be a Rulebook instance.")
        # Validate nested data again: existing Pydantic objects are mutable.
        snapshot = Rulebook.model_validate(rulebook.model_dump())
        if snapshot.version != expected_version + 1:
            raise ValueError("The saved rulebook version must equal expected_version + 1.")
        document = Jsonb(snapshot.model_dump(mode="json"))

        with self._connection(write=True) as connection:
            # A transaction lock also covers first creation, when there is no
            # row to lock yet. Different rulebooks can be written concurrently.
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (snapshot.rulebook_id,),
            )
            row = connection.execute(
                "SELECT MAX(version) AS latest FROM rulebook_versions WHERE rulebook_id = %s",
                (snapshot.rulebook_id,),
            ).fetchone()
            actual_version = row["latest"] or 0
            if actual_version != expected_version:
                raise RulebookVersionConflictError(
                    snapshot.rulebook_id, expected_version, actual_version
                )
            connection.execute(
                """INSERT INTO rulebook_versions
                   (rulebook_id, version, confirmation_state, document)
                   VALUES (%s, %s, %s, %s)""",
                (snapshot.rulebook_id, snapshot.version, snapshot.confirmation_state.value, document),
            )
        return snapshot

    def get_rulebook(self, rulebook_id: str, version: int | None = None) -> Rulebook:
        """Load an explicit version, or the latest including drafts.

        An explicit missing version never falls back to another version.
        """
        if not isinstance(rulebook_id, str) or not rulebook_id.strip():
            raise ValueError("rulebook_id must be a non-empty string.")
        if version is not None:
            _require_integer(version, "version", 1)
        with self._connection() as connection:
            if version is None:
                row = connection.execute(
                    """SELECT * FROM rulebook_versions WHERE rulebook_id = %s
                       ORDER BY version DESC LIMIT 1""",
                    (rulebook_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM rulebook_versions WHERE rulebook_id = %s AND version = %s",
                    (rulebook_id, version),
                ).fetchone()
            if row is None:
                raise RulebookNotFoundError(rulebook_id, version)
            return self._decode(row)

    def list_rulebooks(self, *, limit: int = 20, offset: int = 0) -> RulebookPage:
        """Page latest versions, ordered by rulebook ID for stable navigation."""
        _require_integer(limit, "limit", 1)
        _require_integer(offset, "offset", 0)
        if limit > 100:
            raise ValueError("limit must not exceed 100.")
        with self._connection() as connection:
            total = connection.execute(
                "SELECT COUNT(DISTINCT rulebook_id) AS total FROM rulebook_versions"
            ).fetchone()["total"]
            rows = connection.execute(
                """SELECT saved.* FROM rulebook_versions AS saved
                   JOIN (
                       SELECT rulebook_id, MAX(version) AS version
                       FROM rulebook_versions GROUP BY rulebook_id
                   ) AS latest
                   ON saved.rulebook_id = latest.rulebook_id AND saved.version = latest.version
                   ORDER BY saved.rulebook_id ASC LIMIT %s OFFSET %s""",
                (limit, offset),
            ).fetchall()
            items = [self._decode(row) for row in rows]
        next_offset = offset + len(items) if offset + len(items) < total else None
        return RulebookPage(items=items, limit=limit, offset=offset, total=total, next_offset=next_offset)
