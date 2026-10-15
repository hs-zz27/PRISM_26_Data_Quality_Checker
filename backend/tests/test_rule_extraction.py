from unittest.mock import Mock
from uuid import UUID

from pydantic import ValidationError
import pytest

from backend.app.core.config import load_ollama_settings
from backend.app.inference.ollama_client import OllamaClientError
from backend.app.models.rule import ConfirmationState, Rulebook, RuleOrigin, RuleProposal
from backend.app.services import rule_extraction
from backend.app.storage.repository import (
    RulebookRepository,
    RulebookStorageError,
    RulebookVersionConflictError,
)


SOURCE = "Allowed labels are person and vehicle."


@pytest.fixture
def proposal():
    return RuleProposal(
        rule_id="LBL-1",
        description="Use an allowed label.",
        check_name="allowed_labels",
        parameters={"allowed_labels": ["person", "vehicle"]},
        source_sentence=SOURCE,
    )


@pytest.fixture
def inference(monkeypatch, proposal):
    client = Mock(return_value=[proposal])
    monkeypatch.setattr(rule_extraction, "propose_rules", client)
    return client


def test_extracts_new_draft_and_preserves_exact_text(inference):
    text = f"\n  {SOURCE}\nKeep these class names exactly.\n"
    book = rule_extraction.extract_rulebook(text)
    inference.assert_called_once_with(text, settings=None)
    assert book.guideline_text == text
    assert book.version == 1
    assert book.confirmation_state is ConfirmationState.DRAFT
    assert UUID(book.rulebook_id.removeprefix("rulebook_")).version == 4
    assert book.rules[0].origin is RuleOrigin.EXTRACTED
    assert book.rules[0].confirmation_state is ConfirmationState.DRAFT
    assert book.rules[0].source_sentence == SOURCE
    assert Rulebook.model_validate_json(book.model_dump_json()) == book


def test_each_extraction_creates_an_independent_rulebook(inference):
    first = rule_extraction.extract_rulebook(SOURCE)
    second = rule_extraction.extract_rulebook(SOURCE)
    assert first.rulebook_id != second.rulebook_id
    assert first.version == second.version == 1


def test_explicit_settings_are_forwarded(inference):
    settings = load_ollama_settings()
    rule_extraction.extract_rulebook(SOURCE, settings=settings)
    inference.assert_called_once_with(SOURCE, settings=settings)
    assert inference.call_args.kwargs["settings"] is settings


@pytest.mark.parametrize("text", [None, "", " \n\t", 123])
def test_bad_input_is_rejected_before_inference(inference, text):
    with pytest.raises(ValueError, match="non-empty plain text"):
        rule_extraction.extract_rulebook(text)
    inference.assert_not_called()


@pytest.mark.parametrize("error", [
    OllamaClientError("server_unavailable", "Local server is unavailable."),
    OllamaClientError("invalid_rule_proposals", "Invalid model response."),
    ValueError("Invalid model configuration."),
    FileNotFoundError("Configuration file is missing."),
])
def test_client_errors_propagate_without_an_empty_fallback(inference, error):
    inference.side_effect = error
    with pytest.raises(type(error)) as caught:
        rule_extraction.extract_rulebook(SOURCE)
    assert caught.value is error


def test_no_proposals_returns_an_empty_unconfirmed_book(inference):
    inference.return_value = []
    book = rule_extraction.extract_rulebook(SOURCE)
    assert book.rules == [] and book.confirmation_state is ConfirmationState.DRAFT


@pytest.mark.parametrize("check_name", [None, "future_custom_check"])
def test_unsupported_requirements_and_parameters_are_retained(inference, proposal, check_name):
    proposal.check_name = check_name
    book = rule_extraction.extract_rulebook(SOURCE)
    assert book.rules[0].check_name == check_name
    assert book.rules[0].parameters == proposal.parameters


@pytest.mark.parametrize("citation", [None, "Invented guideline text."])
def test_invalid_citations_are_rejected(inference, proposal, citation):
    proposal.source_sentence = citation
    with pytest.raises(ValidationError):
        rule_extraction.extract_rulebook(SOURCE)


def test_duplicate_rule_ids_are_rejected(inference, proposal):
    inference.return_value = [proposal, proposal]
    with pytest.raises(ValidationError, match="unique"):
        rule_extraction.extract_rulebook(SOURCE)


def test_invalid_proposal_fields_are_rejected_when_creating_rules(inference, proposal):
    proposal.rule_id = ""
    with pytest.raises(ValidationError):
        rule_extraction.extract_rulebook(SOURCE)


def test_returned_rules_do_not_share_mutable_data_with_client(inference, proposal):
    book = rule_extraction.extract_rulebook(SOURCE)
    proposal.parameters["allowed_labels"].append("other")
    assert book.rules[0].parameters["allowed_labels"] == ["person", "vehicle"]
    book.rules[0].parameters["allowed_labels"].append("walker")
    assert "walker" not in proposal.parameters["allowed_labels"]


@pytest.fixture
def repository():
    return Mock(spec_set=RulebookRepository)


def test_extract_and_save_returns_committed_snapshot_after_extraction(inference, proposal, repository):
    text = f"\n  {SOURCE}\n"
    settings = load_ollama_settings()
    committed = []

    def propose(*args, **kwargs):
        repository.save_rulebook.assert_not_called()
        return [proposal]

    def save(draft, *, expected_version):
        snapshot = draft.model_copy(deep=True)
        committed.append(snapshot)
        return snapshot

    inference.side_effect = propose
    repository.save_rulebook.side_effect = save
    result = rule_extraction.extract_and_save_rulebook(text, repository=repository, settings=settings)

    inference.assert_called_once_with(text, settings=settings)
    draft = repository.save_rulebook.call_args.args[0]
    repository.save_rulebook.assert_called_once_with(draft, expected_version=0)
    assert result is committed[0] and result is not draft
    assert result == draft and result.guideline_text == text
    assert result.version == 1 and result.confirmation_state is ConfirmationState.DRAFT


def test_extract_and_save_rejects_invalid_input_without_model_or_storage(inference, repository):
    with pytest.raises(ValueError, match="non-empty plain text"):
        rule_extraction.extract_and_save_rulebook(" \n", repository=repository)
    inference.assert_not_called()
    repository.save_rulebook.assert_not_called()


@pytest.mark.parametrize("error", [
    OllamaClientError("server_unavailable", "Local server is unavailable."),
    ValueError("Invalid model configuration."),
])
def test_extract_and_save_does_not_write_on_inference_failure(inference, repository, error):
    inference.side_effect = error
    with pytest.raises(type(error)) as caught:
        rule_extraction.extract_and_save_rulebook(SOURCE, repository=repository)
    assert caught.value is error
    inference.assert_called_once_with(SOURCE, settings=None)
    repository.save_rulebook.assert_not_called()


def test_extract_and_save_does_not_store_invalid_proposals(inference, proposal, repository):
    proposal.source_sentence = "Invented citation."
    with pytest.raises(ValidationError):
        rule_extraction.extract_and_save_rulebook(SOURCE, repository=repository)
    repository.save_rulebook.assert_not_called()


@pytest.mark.parametrize("error", [
    RulebookStorageError("Database is unavailable."),
    RulebookVersionConflictError("rulebook_demo", expected_version=0, actual_version=1),
])
def test_extract_and_save_propagates_storage_failure_without_retry(inference, repository, error):
    repository.save_rulebook.side_effect = error
    with pytest.raises(type(error)) as caught:
        rule_extraction.extract_and_save_rulebook(SOURCE, repository=repository)
    assert caught.value is error
    inference.assert_called_once_with(SOURCE, settings=None)
    assert repository.save_rulebook.call_count == 1
