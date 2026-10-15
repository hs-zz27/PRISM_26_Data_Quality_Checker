"""Build reviewable draft rulebooks from plain text guidelines."""

from uuid import uuid4

from ..core.config import OllamaSettings
from ..inference.ollama_client import propose_rules
from ..models.rule import ConfirmationState, Rule, Rulebook, RuleOrigin


def extract_rulebook(
    guideline_text: str,
    *,
    settings: OllamaSettings | None = None,
) -> Rulebook:
    """Extract a new version-1 draft, preserving the supplied guideline text.

    The Ollama client loads default settings unless an explicit configuration
    is supplied. Inference and configuration errors propagate unchanged.
    Returned rules are copied and revalidated because existing model instances
    are mutable. Rulebook validation checks citations and unique rule IDs.

    An empty proposal list produces an empty draft; unknown check names remain
    available for review. This service does not confirm or persist rulebooks,
    start jobs, or run an audit. Citation presence does not establish that the
    model interpreted the guideline correctly.
    """
    if not isinstance(guideline_text, str) or not guideline_text.strip():
        raise ValueError("Guidelines must be non-empty plain text.")

    proposals = propose_rules(guideline_text, settings=settings)
    rules = [Rule.model_validate(proposal.model_dump()) for proposal in proposals]
    if any(
        rule.origin is not RuleOrigin.EXTRACTED
        or rule.confirmation_state is not ConfirmationState.DRAFT
        for rule in rules
    ):
        raise ValueError("Extracted proposals must have extracted origin and remain in draft.")

    return Rulebook(
        rulebook_id=f"rulebook_{uuid4().hex}",
        version=1,
        guideline_text=guideline_text,
        rules=rules,
        confirmation_state=ConfirmationState.DRAFT,
    )
