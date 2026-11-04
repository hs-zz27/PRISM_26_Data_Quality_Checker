"""Synthetic display names for contract examples, built from the live models."""

import json

from backend.app.models.display_names import AuditName, RulebookName, audit_label, rulebook_label

from .fixtures import TIMESTAMP

AUDIT_NUMBER = 14
RULEBOOK_NUMBER = 3


def rulebook_name(rulebook_id="rulebook_demo", *, title="Person annotation checks", edited=True):
    return RulebookName.model_validate_json(json.dumps({
        "rulebook_id": rulebook_id, "number": RULEBOOK_NUMBER, "label": rulebook_label(RULEBOOK_NUMBER), "title": title,
        "title_revision": 1 if edited else 0, "title_updated_at": TIMESTAMP if edited else None,
        "title_updated_by": "reviewer_demo" if edited else None,
    })).model_dump(mode="json")


def audit_name(audit_id="audit_demo", *, recheck_number=None, root_audit_id="audit_demo", title="LabelMe demo"):
    return AuditName(
        audit_id=audit_id, root_audit_id=audit_id if recheck_number is None else root_audit_id,
        number=AUDIT_NUMBER, recheck_number=recheck_number, label=audit_label(AUDIT_NUMBER, recheck_number),
        title=title, title_revision=0,
    ).model_dump(mode="json")


def named_book(book, **changes):
    """A Rulebook example as the API returns it, with its per-book name."""
    return {**book, "name": rulebook_name(book["rulebook_id"], **changes)}


def named_audit(audit, **changes):
    """An AuditRun example with its own name and its rulebook's name."""
    return {**audit, "name": audit_name(audit["audit_id"], **changes),
            "rulebook_name": rulebook_name(audit["rulebook_id"])}
