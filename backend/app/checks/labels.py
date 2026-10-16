"""Check label values against the confirmed project rulebook."""

from collections.abc import Iterable, Sequence
from typing import Any, TypedDict

from ..models.annotation import AnnotationObject


class LabelResult(TypedDict):
    object_id: int
    label: Any
    matches: bool
    reason: str | None


class AllowedLabelsResult(TypedDict):
    matches: bool
    allowed_labels: list[str]
    object_results: list[LabelResult]
    invalid_object_ids: list[int]


def check_allowed_labels(
    objects: Sequence[AnnotationObject], allowed_labels: Iterable[str],
) -> AllowedLabelsResult:
    """Check exact label membership using caller-supplied allowed values.

    No dataset-specific labels are built in, and labels are not corrected
    or normalized. Every annotation receives a result, including matches.
    Invalid allowed-label configuration raises ValueError.
    """
    if isinstance(allowed_labels, str):
        raise ValueError("Supply a collection of allowed labels, not one string.")
    try:
        labels = tuple(allowed_labels)
    except TypeError as error:
        raise ValueError("Supply a collection of allowed labels.") from error
    if not labels or any(not isinstance(label, str) or not label for label in labels):
        raise ValueError("Allowed labels must be a non-empty collection of non-empty strings.")

    allowed = set(labels)
    object_results: list[LabelResult] = []
    for obj in objects:
        label = obj.get("label")
        if not isinstance(label, str) or not label:
            reason = "invalid_label"
        elif label not in allowed:
            reason = "label_not_allowed"
        else:
            reason = None
        object_results.append({
            "object_id": obj["object_id"],
            "label": label,
            "matches": reason is None,
            "reason": reason,
        })

    return {
        "matches": all(result["matches"] for result in object_results),
        "allowed_labels": sorted(allowed),
        "object_results": object_results,
        "invalid_object_ids": [
            result["object_id"] for result in object_results if not result["matches"]
        ],
    }
