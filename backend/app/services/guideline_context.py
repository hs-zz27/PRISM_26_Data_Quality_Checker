"""How a rule's applicable guideline context accompanies it into visual-check requests.

The context itself is part of the rule (``context_sentences``), so it is pinned,
fingerprinted, persisted and reviewed with the rule. Requests built from a rule
that has no context are unchanged byte for byte, keeping earlier pinned prompts
and evaluations comparable.
"""

from collections.abc import Mapping
from typing import Any

CONTEXT_USE = (
    "guideline.context_sentences are lines copied from the same confirmed guideline: scope, "
    "definitions, exceptions, conditions or how unclear evidence must be reported. They qualify "
    "guideline.description. Apply them when judging it; do not judge them as separate requirements. "
    "Scope and class definitions apply to what the image shows, even when an annotation label disagrees. "
    "If they say how unclear evidence must be reported, return unsure when the evidence is unclear."
)


def context_fields(rule: Mapping[str, Any]) -> dict[str, str]:
    """Extra request fields for a serialized rule; empty when it carries no context."""
    return {"guideline_context_use": CONTEXT_USE} if rule.get("context_sentences") else {}
