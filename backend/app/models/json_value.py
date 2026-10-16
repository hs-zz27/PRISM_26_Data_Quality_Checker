"""JSON values that can be stored without non-finite numeric extensions."""

from math import isfinite
from typing import Annotated

from pydantic import AfterValidator, JsonValue


def _require_finite_numbers(value: JsonValue) -> JsonValue:
    """Inspect nested values without normalizing or replacing source data."""
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("JSON numbers must be finite; NaN and infinity are not supported.")
    if isinstance(value, dict):
        for child in value.values():
            _require_finite_numbers(child)
    elif isinstance(value, list):
        for child in value:
            _require_finite_numbers(child)
    return value


# The after-validator also covers JSON input, where JsonValue trusts the parser.
FiniteJsonValue = Annotated[JsonValue, AfterValidator(_require_finite_numbers)]
