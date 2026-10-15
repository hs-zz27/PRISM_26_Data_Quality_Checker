"""Review rulebooks through append-only, attributed versions."""

from pydantic import ValidationError

from ..models.review import RulebookAction, RulebookReplace, RulebookReviewAction
from ..models.rule import ConfirmationState, Rulebook, RuleOrigin
from ..storage.repository import RulebookRepository, RulebookVersionConflictError


class RulebookReviewError(ValueError):
    """A review action violates the rulebook workflow."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


def _load_current(
    rulebook_id: str, expected_version: int, repository: RulebookRepository,
) -> Rulebook:
    current = repository.get_rulebook(rulebook_id)
    if current.version != expected_version:
        raise RulebookVersionConflictError(rulebook_id, expected_version, current.version)
    return current


def _require_draft(current: Rulebook) -> None:
    if current.confirmation_state is not ConfirmationState.DRAFT:
        raise RulebookReviewError(
            "invalid_rulebook_state",
            "Only a draft can be edited, confirmed or rejected. Start a revision first.",
        )


def save_rulebook_review(
    rulebook_id: str, request: RulebookReplace, *, repository: RulebookRepository,
) -> Rulebook:
    """Save the full replacement draft and explicit per-rule decisions.

    New rules use reviewer origin. Existing IDs retain their origin, so an
    extracted rule cannot silently lose its citation requirement. The caller
    explicitly supplies each rule's state; editing never confirms the book.
    """
    if not isinstance(request, RulebookReplace):
        raise TypeError("request must be a RulebookReplace instance.")
    request = RulebookReplace.model_validate(request.model_dump())
    current = _load_current(rulebook_id, request.expected_version, repository)
    _require_draft(current)
    previous_rules = {rule.rule_id: rule for rule in current.rules}
    for rule in request.rules:
        previous = previous_rules.get(rule.rule_id)
        expected_origin = RuleOrigin.REVIEWER if previous is None else previous.origin
        if rule.origin is not expected_origin:
            raise RulebookReviewError(
                "invalid_rule_origin",
                "Retained rules must preserve origin; new rules must use reviewer origin.",
            )
    updated = Rulebook(
        rulebook_id=current.rulebook_id,
        version=current.version + 1,
        guideline_text=request.guideline_text,
        rules=request.rules,
        confirmation_state=ConfirmationState.DRAFT,
    )
    return repository.save_rulebook_review(
        updated, expected_version=request.expected_version,
        reviewer_id=request.reviewer_id, action=RulebookReviewAction.EDIT,
    )


def _apply_action(
    rulebook_id: str, request: RulebookAction, action: RulebookReviewAction,
    repository: RulebookRepository,
) -> Rulebook:
    if not isinstance(request, RulebookAction):
        raise TypeError("request must be a RulebookAction instance.")
    request = RulebookAction.model_validate(request.model_dump())
    current = _load_current(rulebook_id, request.expected_version, repository)
    values = current.model_dump()
    values["version"] = current.version + 1

    if action is RulebookReviewAction.REVISE:
        if current.confirmation_state is ConfirmationState.DRAFT:
            raise RulebookReviewError("invalid_rulebook_state", "A draft is already editable.")
        values["confirmation_state"] = ConfirmationState.DRAFT
        for rule in values["rules"]:
            rule["confirmation_state"] = ConfirmationState.DRAFT
    else:
        _require_draft(current)
        if action is RulebookReviewAction.CONFIRM:
            values["confirmation_state"] = ConfirmationState.CONFIRMED
        else:
            values["confirmation_state"] = ConfirmationState.REJECTED

    try:
        updated = Rulebook.model_validate(values)
    except ValidationError as error:
        if action is RulebookReviewAction.CONFIRM:
            raise RulebookReviewError(
                "rulebook_not_ready",
                "Confirm every retained rule and remove rejected rules before confirming the book.",
            ) from error
        raise
    return repository.save_rulebook_review(
        updated, expected_version=request.expected_version,
        reviewer_id=request.reviewer_id, action=action,
    )


def confirm_rulebook(
    rulebook_id: str, request: RulebookAction, *, repository: RulebookRepository,
) -> Rulebook:
    """Freeze a nonempty draft whose retained rules are all confirmed."""
    return _apply_action(rulebook_id, request, RulebookReviewAction.CONFIRM, repository)


def reject_rulebook(
    rulebook_id: str, request: RulebookAction, *, repository: RulebookRepository,
) -> Rulebook:
    """Reject the draft as a whole, preserving its individual rule decisions."""
    return _apply_action(rulebook_id, request, RulebookReviewAction.REJECT, repository)


def revise_rulebook(
    rulebook_id: str, request: RulebookAction, *, repository: RulebookRepository,
) -> Rulebook:
    """Copy a confirmed or rejected book into a draft with all rules reset."""
    return _apply_action(rulebook_id, request, RulebookReviewAction.REVISE, repository)
