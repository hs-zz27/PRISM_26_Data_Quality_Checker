"""Create manual drafts without extraction, review actions or background work."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from ..models.rule import ConfirmationState, Rule, Rulebook, RuleOrigin
from ..models.rulebook_creation import RulebookCreate

if TYPE_CHECKING:
    from ..storage.repository import RulebookRepository

def create_rulebook(
    request: RulebookCreate, *, repository: RulebookRepository,
) -> Rulebook:
    """Validate editor content and return only the repository's committed snapshot.

    Both input models revalidate existing instances, including mutable nested
    content. Request validation and draft construction finish before storage.
    Only server-assigned metadata is added; no reviewer action is fabricated.

    Every invocation generates a fresh ID. Saves are not retried, including
    generated-ID conflicts. Separate successful requests create separate books.
    Repository failures propagate unchanged; an unsaved draft is never returned.
    """
    if not isinstance(request, RulebookCreate):
        raise TypeError("request must be a RulebookCreate instance.")
    validated = RulebookCreate.model_validate(request)
    rules = [
        Rule(
            **rule.model_dump(),
            origin=RuleOrigin.REVIEWER,
            confirmation_state=ConfirmationState.DRAFT,
        )
        for rule in validated.rules
    ]
    draft = Rulebook(
        rulebook_id=f"rulebook_{uuid4().hex}",
        version=1,
        guideline_text=validated.guideline_text,
        rules=rules,
        confirmation_state=ConfirmationState.DRAFT,
    )
    return repository.save_rulebook(draft, expected_version=0)
