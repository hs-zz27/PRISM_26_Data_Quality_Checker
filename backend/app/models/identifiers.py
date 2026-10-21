"""Shared validated scalar aliases without catalogue/audit import dependencies."""
from typing import Annotated
from pydantic import AfterValidator, Field, StringConstraints


def _no_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("Identifiers must not contain NUL characters.")
    return value


Identifier = Annotated[str, StringConstraints(min_length=1, max_length=255, pattern=r"\S"),
                       AfterValidator(_no_nul)]
Count = Annotated[int, Field(strict=True, ge=0, le=9_223_372_036_854_775_807)]
Revision = Annotated[int, Field(strict=True, ge=1, le=2_147_483_647)]
Dimension = Annotated[int, Field(strict=True, ge=1, le=2_147_483_647)]
