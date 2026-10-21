"""Liveness, capabilities and local readiness. Reads only; nothing is started or downloaded."""

from typing import Annotated

from fastapi import APIRouter, Depends

from ..models.audit_read import ErrorResponse
from ..models.system import Capabilities, Health, SystemStatus
from ..services.system_status import SystemStatusService, build_capabilities

router = APIRouter(tags=["System"], responses={
    500: {"model": ErrorResponse, "description": "Unexpected server error."},
})


def get_system_status_service() -> SystemStatusService:
    """Resolve settings at request time; importing this module reads no configuration."""
    return SystemStatusService()


@router.get("/health", response_model=Health, operation_id="getHealth")
def get_health() -> Health:
    """Liveness only: no database, Ollama, checkpoint or inference is touched."""
    return Health(status="ok", api_version="v1")


@router.get("/capabilities", response_model=Capabilities, operation_id="getCapabilities")
def get_capabilities() -> Capabilities:
    """Executable checks come from the real registry; evidence features are listed separately."""
    return build_capabilities()


@router.get("/system/status", response_model=SystemStatus, operation_id="getSystemStatus")
def get_system_status(
    service: Annotated[SystemStatusService, Depends(get_system_status_service)],
) -> SystemStatus:
    """Bounded read-only probes. Sync, so FastAPI runs the blocking work off the event loop."""
    return service.status()
