"""Extract draft rulebooks and coordinate their persistence."""

from uuid import uuid4

from ..core.config import OllamaSettings
from ..inference.ollama_client import propose_rules
from ..models.rule import ConfirmationState, Rule, Rulebook, RuleOrigin
from ..storage.repository import RulebookRepository


def extract_rulebook(
    guideline_text: str,
    *,
    settings: OllamaSettings | None = None,
) -> Rulebook:
    """Extract a new version-1 draft, preserving the supplied guideline text.

    The Ollama client loads default settings unless an explicit configuration
    is supplied. Inference and configuration errors propagate unchanged.
    The service assigns extracted origin and draft state to each proposal.
    Constructing the rulebook validates citations and unique rule IDs.

    An empty proposal list produces an empty draft; unknown check names remain
    available for review. This service does not confirm or persist rulebooks,
    start jobs, or run an audit. Citation presence does not establish that the
    model interpreted the guideline correctly.
    """
    if not isinstance(guideline_text, str) or not guideline_text.strip():
        raise ValueError("Guidelines must be non-empty plain text.")

    proposals = propose_rules(guideline_text, settings=settings)
    rules = [
        Rule(
            **proposal.model_dump(),
            origin=RuleOrigin.EXTRACTED,
            confirmation_state=ConfirmationState.DRAFT,
        )
        for proposal in proposals
    ]

    return Rulebook(
        rulebook_id=f"rulebook_{uuid4().hex}",
        version=1,
        guideline_text=guideline_text,
        rules=rules,
        confirmation_state=ConfirmationState.DRAFT,
    )


def extract_and_save_rulebook(
    guideline_text: str,
    *,
    repository: RulebookRepository,
    settings: OllamaSettings | None = None,
) -> Rulebook:
    """Create and persist a version-1 draft through the supplied repository.

    Extraction and validation finish before storage opens its transaction.
    Return the repository's saved snapshot only after the write commits.
    Extraction, validation and storage errors propagate unchanged; a failed
    save never returns an unsaved draft as a successful result.

    Each call creates a new rulebook. Calls are not automatically retried and
    do not confirm rules, create jobs or initialize the database schema.
    """
    draft = extract_rulebook(guideline_text, settings=settings)
    return repository.save_rulebook(draft, expected_version=0)
