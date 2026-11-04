"""Batch reads of permanent display names for IDs held by other responses."""

from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse

from ..models.audit_read import ErrorResponse
from ..models.display_names import DisplayNameLookup
from ..storage.display_name_repository import (
    MAX_LOOKUP_IDS, DisplayNameRepository, DisplayNameStorageError, DisplayTitleConflictError,
)

router = APIRouter(prefix="/display-names", tags=["Display names"], responses={
    422: {"model": ErrorResponse, "description": "Invalid request fields."},
    500: {"model": ErrorResponse, "description": "Unexpected server error."},
    503: {"model": ErrorResponse, "description": "Name storage unavailable."},
})


def get_display_name_repository() -> DisplayNameRepository:
    """Resolve configuration per request; never connect or migrate on import."""
    return DisplayNameRepository()


def names_beside(repository) -> DisplayNameRepository:
    """Read names from the same database as the records they label."""
    return DisplayNameRepository(settings_from=lambda: repository.settings)


DisplayNames = Annotated[DisplayNameRepository, Depends(get_display_name_repository)]
LookupIds = Annotated[list[Annotated[str, Query(min_length=1, pattern=r"\S")]], Query(max_length=MAX_LOOKUP_IDS)]

TITLE_CONFLICT_RESPONSE = {409: {
    "model": ErrorResponse, "description": "The title changed after it was read.",
    "content": {"application/json": {"examples": {"VERSION_CONFLICT": {"value": {"error": {
        "code": "VERSION_CONFLICT", "message": "The title was changed by someone else. Refresh before saving.",
        "details": [], "request_id": "request_demo",
    }}}}}},
}}


@router.get("", response_model=DisplayNameLookup, operation_id="lookupDisplayNames")
def lookup_display_names(
    names: DisplayNames, audit_id: LookupIds = [], rulebook_id: LookupIds = [],
) -> DisplayNameLookup:
    """Read labels for up to 100 audit and 100 rulebook IDs. Unknown IDs are omitted; nothing is allocated."""
    return names.lookup(audit_ids=audit_id, rulebook_ids=rulebook_id)


def register_display_name_errors(app: FastAPI) -> None:
    from .errors import _response, logger

    async def title_conflict(request: Request, error: DisplayTitleConflictError) -> JSONResponse:
        return _response(409, "VERSION_CONFLICT", "The title was changed by someone else. Refresh before saving.")

    async def names_unavailable(request: Request, error: DisplayNameStorageError) -> JSONResponse:
        logger.exception("Display name storage unavailable", exc_info=error)
        return _response(503, "DEPENDENCY_UNAVAILABLE", "Display name storage is unavailable.")

    app.add_exception_handler(DisplayTitleConflictError, title_conflict)
    app.add_exception_handler(DisplayNameStorageError, names_unavailable)
