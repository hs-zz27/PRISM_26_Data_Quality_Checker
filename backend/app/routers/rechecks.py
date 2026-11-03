"""Durable linked audit submission; the existing audit worker executes rechecks."""
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from ..models.audit_submission import AuditStarted
from ..models.audit_read import ErrorResponse
from ..models.correction import RecheckRequest
from ..storage.recheck_repository import RecheckRepository
from .datasets import Identifier, _query
from ..storage.display_name_repository import DisplayNameRepository
from .display_names import names_beside

router=APIRouter(prefix='/audits',tags=['Audits'],responses={status:{'model':ErrorResponse,'description':description}
    for status,description in {404:'Parent audit not found.',409:'Parent not ready/recheckable or stale sample revision.',
    413:'Request byte limit exceeded.',415:'Unsupported content encoding.',422:'Invalid selection or rule parameters.',500:'Unexpected failure.',503:'Storage or enqueue unavailable.'}.items()})


def get_recheck_repository():
    return RecheckRepository()


def get_recheck_display_names(repository: Annotated[RecheckRepository,Depends(get_recheck_repository)]) -> DisplayNameRepository:
    return names_beside(repository)


@router.post('/{audit_id}/rechecks',response_model=AuditStarted,status_code=202,operation_id='recheckSamples')
def recheck(audit_id: Identifier,request: Request,body: RecheckRequest,
            repository: Annotated[RecheckRepository,Depends(get_recheck_repository)],
            names: Annotated[DisplayNameRepository,Depends(get_recheck_display_names)]):
    _query(request,set())
    started=repository.start(audit_id,body)
    return started.model_copy(update={'audit':names.with_audit_names([started.audit],after_write=True)[0]})
