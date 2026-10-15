"""Review policy and validation without inference or database connections."""

from unittest.mock import Mock

from pydantic import ValidationError
import pytest

from backend.app.models.review import RulebookAction, RulebookReplace, RulebookReviewAction
from backend.app.models.rule import ConfirmationState, Rule, Rulebook, RuleOrigin
from backend.app.services.review import (
    RulebookReviewError, confirm_rulebook, reject_rulebook, revise_rulebook, save_rulebook_review,
)
from backend.app.storage.repository import (
    RulebookNotFoundError, RulebookRepository, RulebookStorageError, RulebookVersionConflictError,
)


SOURCE = "Use person labels. Keep boxes within the image."


@pytest.fixture
def book():
    return Rulebook(
        rulebook_id="book", guideline_text=SOURCE,
        rules=[Rule(rule_id="label", description="Use person labels.",
                    origin=RuleOrigin.EXTRACTED, source_sentence="Use person labels.",
                    check_name="allowed_labels", parameters={"allowed_labels": ["person"]})],
    )


@pytest.fixture
def repository(book):
    repository = Mock(spec_set=RulebookRepository)
    repository.get_rulebook.return_value = book
    repository.save_rulebook_review.side_effect = lambda value, **kwargs: value.model_copy(deep=True)
    return repository


def action(version=1):
    return RulebookAction(expected_version=version, reviewer_id="reviewer_1")


def replacement(book, **changes):
    values = dict(expected_version=book.version, reviewer_id="reviewer_1",
                  guideline_text=book.guideline_text, rules=book.model_copy(deep=True).rules)
    values.update(changes)
    return RulebookReplace(**values)


def test_full_review_edits_content_adds_removes_and_records_rule_decisions(book, repository):
    book.rules.append(Rule(rule_id="remove", description="Remove me.", origin="reviewer"))
    request = replacement(book)
    request.rules.pop()
    rule = request.rules[0]
    rule.description = "Only person labels are permitted."
    rule.check_name = "future_check"
    rule.parameters = {"labels": ["person"], "threshold": 0.5}
    rule.source_sentence = "Keep boxes within the image."
    rule.confirmation_state = ConfirmationState.CONFIRMED
    request.rules.append(Rule(rule_id="manual", description="Review posture.", origin="reviewer",
                              confirmation_state="rejected"))

    result = save_rulebook_review("book", request, repository=repository)

    assert result.version == 2 and result.confirmation_state is ConfirmationState.DRAFT
    assert result.rules == request.rules and [rule.rule_id for rule in result.rules] == ["label", "manual"]
    assert result.rules[0].origin is RuleOrigin.EXTRACTED
    assert result.rules[1].confirmation_state is ConfirmationState.REJECTED
    repository.save_rulebook_review.assert_called_once_with(
        result, expected_version=1, reviewer_id="reviewer_1", action=RulebookReviewAction.EDIT,
    )
    assert book.rules[0].parameters == {"allowed_labels": ["person"]}
    request.rules[0].parameters["labels"].append("vehicle")
    assert result.rules[0].parameters["labels"] == ["person"]


def test_guidelines_can_be_changed_when_retained_citations_still_match(book, repository):
    text = f"\n{SOURCE}\nReview every image.\n"
    saved = save_rulebook_review("book", replacement(book, guideline_text=text), repository=repository)
    assert saved.guideline_text == text and book.guideline_text == SOURCE


@pytest.mark.parametrize("change", ["invented_citation", "duplicate_ids", "removed_guideline"])
def test_invalid_book_is_never_saved(book, repository, change):
    request = replacement(book)
    if change == "invented_citation":
        request.rules[0].source_sentence = "Invented text."
    elif change == "duplicate_ids":
        request.rules.append(request.rules[0])
    else:
        request.guideline_text = "Other guidelines."
    with pytest.raises(ValidationError):
        save_rulebook_review("book", request, repository=repository)
    repository.save_rulebook_review.assert_not_called()


@pytest.mark.parametrize("change", ["retained_origin", "new_extracted_rule"])
def test_rule_origins_cannot_be_forged(book, repository, change):
    request = replacement(book)
    if change == "retained_origin":
        request.rules[0].origin = RuleOrigin.REVIEWER
        request.rules[0].source_sentence = None
    else:
        request.rules.append(book.rules[0].model_copy(update={"rule_id": "new"}))
    with pytest.raises(RulebookReviewError) as caught:
        save_rulebook_review("book", request, repository=repository)
    assert caught.value.reason == "invalid_rule_origin"
    repository.save_rulebook_review.assert_not_called()


def test_confirmation_is_explicit_and_preserves_unsupported_requirements(book, repository):
    book.rules[0].confirmation_state = ConfirmationState.CONFIRMED
    book.rules[0].check_name = None
    result = confirm_rulebook("book", action(), repository=repository)
    assert result.version == 2 and result.confirmation_state is ConfirmationState.CONFIRMED
    assert result.rules == book.rules and result.rules[0].check_name is None
    assert book.confirmation_state is ConfirmationState.DRAFT
    repository.save_rulebook_review.assert_called_once_with(
        result, expected_version=1, reviewer_id="reviewer_1", action=RulebookReviewAction.CONFIRM,
    )


@pytest.mark.parametrize("rule_state", ["draft", "rejected", "empty", "mixed"])
def test_unready_books_cannot_be_confirmed(book, repository, rule_state):
    if rule_state == "empty":
        book.rules.clear()
    elif rule_state == "mixed":
        book.rules[0].confirmation_state = ConfirmationState.CONFIRMED
        book.rules.append(Rule(rule_id="other", description="Review me too.", origin="reviewer"))
    else:
        book.rules[0].confirmation_state = ConfirmationState(rule_state)
    with pytest.raises(RulebookReviewError) as caught:
        confirm_rulebook("book", action(), repository=repository)
    assert caught.value.reason == "rulebook_not_ready"
    repository.save_rulebook_review.assert_not_called()


def test_rejection_preserves_individual_decisions(book, repository):
    book.rules[0].confirmation_state = ConfirmationState.CONFIRMED
    result = reject_rulebook("book", action(), repository=repository)
    assert result.version == 2 and result.confirmation_state is ConfirmationState.REJECTED
    assert result.rules == book.rules
    assert book.confirmation_state is ConfirmationState.DRAFT
    assert repository.save_rulebook_review.call_args.kwargs["action"] is RulebookReviewAction.REJECT


@pytest.mark.parametrize("state", [ConfirmationState.CONFIRMED, ConfirmationState.REJECTED])
def test_final_books_need_revision_before_edit_confirm_or_reject(book, repository, state):
    book.rules[0].confirmation_state = ConfirmationState.CONFIRMED
    book.confirmation_state = state
    for operation, request in (
        (save_rulebook_review, replacement(book)), (confirm_rulebook, action()), (reject_rulebook, action()),
    ):
        with pytest.raises(RulebookReviewError) as caught:
            operation("book", request, repository=repository)
        assert caught.value.reason == "invalid_rulebook_state"
    repository.save_rulebook_review.assert_not_called()


@pytest.mark.parametrize("state", [ConfirmationState.CONFIRMED, ConfirmationState.REJECTED])
def test_revision_resets_all_rules_and_keeps_original_snapshot(book, repository, state):
    book.rules[0].confirmation_state = ConfirmationState.CONFIRMED
    book.rules.append(Rule(rule_id="manual", description="Review posture.", origin="reviewer",
                          confirmation_state=state))
    book.confirmation_state = state
    original = book.model_dump()
    result = revise_rulebook("book", action(), repository=repository)
    assert result.version == 2 and result.confirmation_state is ConfirmationState.DRAFT
    assert all(rule.confirmation_state is ConfirmationState.DRAFT for rule in result.rules)
    assert [rule.rule_id for rule in result.rules] == ["label", "manual"]
    assert result.rules[0].origin is RuleOrigin.EXTRACTED
    assert result.guideline_text == SOURCE and book.model_dump() == original
    assert repository.save_rulebook_review.call_args.kwargs["action"] is RulebookReviewAction.REVISE


def test_draft_cannot_start_another_revision(book, repository):
    with pytest.raises(RulebookReviewError, match="already editable"):
        revise_rulebook("book", action(), repository=repository)
    repository.save_rulebook_review.assert_not_called()


@pytest.mark.parametrize("operation", [save_rulebook_review, confirm_rulebook, reject_rulebook, revise_rulebook])
def test_stale_requests_are_rejected_before_applying_decisions(book, repository, operation):
    request = replacement(book) if operation is save_rulebook_review else action()
    book.version = 2
    with pytest.raises(RulebookVersionConflictError) as caught:
        operation("book", request, repository=repository)
    assert caught.value.expected_version == 1 and caught.value.actual_version == 2
    repository.save_rulebook_review.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"expected_version": True}, {"expected_version": 0}, {"expected_version": "1"},
    {"reviewer_id": " "}, {"unexpected": "value"},
])
def test_review_requests_are_strict(changes):
    with pytest.raises(ValidationError):
        RulebookAction(**{**action().model_dump(), **changes})


def test_mutated_nested_request_is_revalidated_before_loading(book, repository):
    request = replacement(book)
    request.rules[0].description = " "
    with pytest.raises(ValidationError):
        save_rulebook_review("book", request, repository=repository)
    repository.get_rulebook.assert_not_called()


def test_mutated_action_identity_is_revalidated_before_loading(repository):
    request = action()
    request.reviewer_id = " "
    with pytest.raises(ValidationError):
        reject_rulebook("book", request, repository=repository)
    repository.get_rulebook.assert_not_called()


def test_missing_book_error_propagates(repository):
    error = RulebookNotFoundError("missing", None)
    repository.get_rulebook.side_effect = error
    with pytest.raises(RulebookNotFoundError) as caught:
        reject_rulebook("missing", action(), repository=repository)
    assert caught.value is error
    repository.save_rulebook_review.assert_not_called()


@pytest.mark.parametrize("error", [
    RulebookStorageError("Write failed."), RulebookVersionConflictError("book", 1, 2),
])
def test_failed_write_is_not_retried_or_reported_as_success(book, repository, error):
    repository.save_rulebook_review.side_effect = error
    with pytest.raises(type(error)) as caught:
        save_rulebook_review("book", replacement(book), repository=repository)
    assert caught.value is error and repository.save_rulebook_review.call_count == 1
    assert book.version == 1
