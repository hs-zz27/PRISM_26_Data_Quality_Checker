"""Read permanent display names and apply attributed title edits.

Numbers are allocated only by the database triggers in migration 016. Reads
here never allocate or modify names; a record without a name is returned
without one rather than being named on read.
"""

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import logging
from typing import Any

import psycopg
from pydantic import ValidationError

from ..core.config import DatabaseSettings, load_database_settings
from ..models.audit import AuditRun
from ..models.display_names import AuditName, DisplayNameLookup, RulebookName, TitleUpdate, audit_label, rulebook_label
from ..models.rule import Rulebook, RulebookRecord
from .database import DatabaseError, database_connection
from .repository import RulebookNotFoundError
from .workflow_repository import AuditNotFoundError, WorkflowStorageError

MAX_LOOKUP_IDS = 100
logger = logging.getLogger(__name__)


class DisplayNameStorageError(WorkflowStorageError):
    """Names cannot be read or written, or a stored name is invalid."""


class DisplayTitleConflictError(RuntimeError):
    """The title changed after the caller read it."""


def _audit_name(row: dict[str, Any]) -> AuditName:
    return AuditName(
        audit_id=row["audit_id"], root_audit_id=row["root_audit_id"],
        number=row["display_number"], recheck_number=row["recheck_number"],
        label=audit_label(row["display_number"], row["recheck_number"]),
        title=row["title"], title_revision=row["title_revision"],
        title_updated_at=row["title_updated_at"], title_updated_by=row["title_updated_by"],
    )


def _rulebook_name(row: dict[str, Any]) -> RulebookName:
    return RulebookName(
        rulebook_id=row["rulebook_id"], number=row["display_number"],
        label=rulebook_label(row["display_number"]),
        title=row["title"], title_revision=row["title_revision"],
        title_updated_at=row["title_updated_at"], title_updated_by=row["title_updated_by"],
    )


def _unique(ids: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(ids))


class DisplayNameRepository:
    """Settings resolve on first use, so constructing one never connects or fails."""

    def __init__(
        self, *, settings: DatabaseSettings | None = None,
        settings_from: Callable[[], DatabaseSettings] | None = None,
    ) -> None:
        if settings is not None and not isinstance(settings, DatabaseSettings):
            raise TypeError("settings must be a DatabaseSettings instance.")
        self._settings = settings
        self._settings_from = load_database_settings if settings_from is None else settings_from

    @property
    def settings(self) -> DatabaseSettings:
        if self._settings is None:
            settings = self._settings_from()
            if not isinstance(settings, DatabaseSettings):
                raise TypeError("settings must be a DatabaseSettings instance.")
            self._settings = settings
        return self._settings

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        try:
            with database_connection(self.settings, readonly=not write) as connection:
                yield connection
        except DatabaseError as error:
            raise DisplayNameStorageError("Cannot read or write display names.") from error

    def audit_names(self, audit_ids: Iterable[str]) -> dict[str, AuditName]:
        ids = _unique(audit_ids)
        if not ids:
            return {}
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM audit_names WHERE audit_id = ANY(%s)", (ids,)).fetchall()
        try:
            return {row["audit_id"]: _audit_name(row) for row in rows}
        except ValidationError as error:
            raise DisplayNameStorageError("A saved audit name failed validation.") from error

    def rulebook_names(self, rulebook_ids: Iterable[str]) -> dict[str, RulebookName]:
        ids = _unique(rulebook_ids)
        if not ids:
            return {}
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM rulebook_names WHERE rulebook_id = ANY(%s)", (ids,)).fetchall()
        try:
            return {row["rulebook_id"]: _rulebook_name(row) for row in rows}
        except ValidationError as error:
            raise DisplayNameStorageError("A saved rulebook name failed validation.") from error

    def lookup(self, *, audit_ids: list[str], rulebook_ids: list[str]) -> DisplayNameLookup:
        if len(_unique(audit_ids)) > MAX_LOOKUP_IDS or len(_unique(rulebook_ids)) > MAX_LOOKUP_IDS:
            raise ValueError(f"Request at most {MAX_LOOKUP_IDS} audit and {MAX_LOOKUP_IDS} rulebook IDs.")
        audits = self.audit_names(audit_ids)
        books = self.rulebook_names(rulebook_ids)
        return DisplayNameLookup(
            audits=[audits[i] for i in _unique(audit_ids) if i in audits],
            rulebooks=[books[i] for i in _unique(rulebook_ids) if i in books],
        )

    def with_audit_names(self, audits: list[AuditRun], *, after_write: bool = False) -> list[AuditRun]:
        """Attach each audit's name and its rulebook's name; other fields are unchanged.

        ``after_write`` is for responses to a committed write: a failure to read
        names then omits them instead of reporting the committed write as failed,
        which could prompt a retry that duplicates it.
        """
        try:
            names = self.audit_names(audit.audit_id for audit in audits)
            books = self.rulebook_names(audit.rulebook_id for audit in audits)
        except DisplayNameStorageError:
            if not after_write:
                raise
            logger.warning("Display names unavailable after a committed write; returning the record without them.")
            names, books = {}, {}
        return [audit.model_copy(update={
            "name": names.get(audit.audit_id), "rulebook_name": books.get(audit.rulebook_id),
        }) for audit in audits]

    def with_rulebook_names(self, books: list[Rulebook], *, after_write: bool = False) -> list[RulebookRecord]:
        try:
            names = self.rulebook_names(book.rulebook_id for book in books)
        except DisplayNameStorageError:
            if not after_write:
                raise
            logger.warning("Display names unavailable after a committed write; returning the record without them.")
            names = {}
        # The document is validated exactly as a stored one is; only the name is added.
        return [RulebookRecord.model_validate({**book.model_dump(mode="json"), "name": names.get(book.rulebook_id)})
                for book in books]

    def update_audit_title(self, audit_id: str, update: TitleUpdate) -> AuditName:
        return _update_title(self, "audit", audit_id, update)

    def update_rulebook_title(self, rulebook_id: str, update: TitleUpdate) -> RulebookName:
        return _update_title(self, "rulebook", rulebook_id, update)


_TABLES = {"audit": ("audit_names", "audit_id"), "rulebook": ("rulebook_names", "rulebook_id")}


def _update_title(repository: DisplayNameRepository, kind: str, resource_id: str, update: TitleUpdate) -> AuditName | RulebookName:
    if not isinstance(update, TitleUpdate):
        raise TypeError("update must be a TitleUpdate instance.")
    update = TitleUpdate.model_validate(update.model_dump())
    table, key = _TABLES[kind]
    now = datetime.now(UTC)
    with repository._connection(write=True) as connection:
        # Only the title fields change; the guard trigger keeps numbers fixed.
        row = connection.execute(
            f"""UPDATE {table} SET title = %s, title_revision = title_revision + 1,
                    title_updated_at = %s, title_updated_by = %s
                WHERE {key} = %s AND title_revision = %s RETURNING *""",
            (update.title, now, update.reviewer_id, resource_id, update.expected_title_revision),
        ).fetchone()
        if row is None:
            exists = connection.execute(f"SELECT 1 FROM {table} WHERE {key} = %s", (resource_id,)).fetchone()
            if exists is None:
                raise AuditNotFoundError(resource_id) if kind == "audit" else RulebookNotFoundError(resource_id, None)
            raise DisplayTitleConflictError(f"The {kind} title changed after it was read.")
        connection.execute(
            """INSERT INTO display_title_edits
                   (resource_kind, resource_id, title_revision, title, reviewer_id, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (kind, resource_id, row["title_revision"], row["title"], update.reviewer_id, now),
        )
    try:
        return _audit_name(row) if kind == "audit" else _rulebook_name(row)
    except ValidationError as error:
        raise DisplayNameStorageError("A saved name failed validation.") from error
