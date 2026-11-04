"""Display-name helpers for API tests.

``without_display_names`` is for tests whose primary repository is a mock: it
attaches no names, as a store without name rows would, so responses keep their
previous shape. Name behaviour itself is tested in test_display_names.py.
"""

from backend.app.models.rule import RulebookRecord
from backend.app.routers.audits import get_audit_display_names
from backend.app.routers.rechecks import get_recheck_display_names
from backend.app.routers.rulebooks import get_rulebook_display_names


class NoDisplayNames:
    def with_audit_names(self, audits, *, after_write=False):
        return list(audits)

    def with_rulebook_names(self, books, *, after_write=False):
        return [RulebookRecord.model_validate(book.model_dump(mode="json")) for book in books]


def without_display_names(app):
    """Override every router's names reader on ``app``."""
    for dependency in (get_audit_display_names, get_recheck_display_names, get_rulebook_display_names):
        app.dependency_overrides[dependency] = NoDisplayNames
    return app


def document(body):
    """A rulebook response as the repository stores it: the name is checked, then removed.

    Names are joined at read time and are never part of the stored document.
    """
    if isinstance(body, dict) and "name" in body:
        name = body["name"]
        assert name["rulebook_id"] == body["rulebook_id"]
        assert name["label"] == f"Rules {name['number']:03d}"
        body = {key: value for key, value in body.items() if key != "name"}
    return body


def documents(items):
    return [document(item) for item in items]
