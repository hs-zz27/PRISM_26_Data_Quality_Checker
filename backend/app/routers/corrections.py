"""Three correction/history endpoints. Decoding and storage run off the event loop."""
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Query
from starlette.concurrency import run_in_threadpool

from ..core.correction import CorrectionError, CorrectionSettings, load_correction_settings
from ..models.correction import AnnotationRevision, CorrectionRequest, MaskUpload, MaskUploadRequest, RevisionPage
from ..models.audit_read import ErrorResponse
from ..services.corrections import CorrectionService
from ..storage.correction_repository import CorrectionRepository
from .correction_ingress import SCOPE_KEY, CorrectionBodyLimit
from .multipart import parsed_mask_upload
from .datasets import Identifier, _query

router=APIRouter(prefix='/datasets/{dataset_id}/samples/{sample_id}',tags=['Corrections'],responses={
    status:{'model':ErrorResponse,'description':description} for status,description in {
        404:'Dataset or sample not found.',409:'Stale revision, unavailable mask or incompatible source audit.',
        422:'Invalid correction inputs or unusable source.',500:'Unexpected failure.',503:'Storage or durable publication unavailable.'}.items()})


def get_correction_repository():
    return CorrectionRepository()


def get_correction_settings(request: Request):
    saved=request.scope.get(SCOPE_KEY)
    return load_correction_settings() if saved is None else CorrectionSettings.model_validate(saved.model_dump())


def get_correction_service(repository: Annotated[CorrectionRepository,Depends(get_correction_repository)],
                           settings: Annotated[CorrectionSettings,Depends(get_correction_settings)]):
    return CorrectionService(repository,settings)


Service=Annotated[CorrectionService,Depends(get_correction_service)]
Repository=Annotated[CorrectionRepository,Depends(get_correction_repository)]


@router.post('/mask-uploads',response_model=MaskUpload,status_code=201,operation_id='uploadCorrectionMask',
    responses={413:{'model':ErrorResponse,'description':'Upload byte limit exceeded.'},415:{'model':ErrorResponse,'description':'Unsupported multipart representation.'}},
    openapi_extra={'requestBody':{'required':True,'content':{'multipart/form-data':{'schema':MaskUploadRequest.model_json_schema()}}}})
async def upload_mask(dataset_id: Identifier,sample_id: Identifier,request: Request,service: Service,
                      settings: Annotated[CorrectionSettings,Depends(get_correction_settings)]):
    _query(request,set())
    scoped=request.scope.get(SCOPE_KEY)
    if scoped is None or settings!=scoped or service.settings!=scoped:
        raise CorrectionError('DEPENDENCY_UNAVAILABLE')
    async with parsed_mask_upload(request,settings) as (mask,revision):
        return await run_in_threadpool(service.upload,dataset_id,sample_id,revision,mask.file)


@router.post('/revisions',response_model=AnnotationRevision,status_code=201,operation_id='correctAnnotations',
             responses={413:{'model':ErrorResponse,'description':'Actual request or annotation size exceeds policy.'},415:{'model':ErrorResponse,'description':'Unsupported content encoding.'}})
def correct(dataset_id: Identifier,sample_id: Identifier,request: Request,body: CorrectionRequest,service: Service):
    _query(request,set())
    return service.correct(dataset_id,sample_id,body)


@router.get('/revisions',response_model=RevisionPage,operation_id='listAnnotationRevisions')
def history(dataset_id: Identifier,sample_id: Identifier,request: Request,repository: Repository,
            limit: Annotated[int,Query(ge=1,le=100)]=20,
            offset: Annotated[int,Query(ge=0,le=9223372036854775807)]=0):
    _query(request,{'limit','offset'})
    return repository.history(dataset_id,sample_id,limit=limit,offset=offset)


def register_correction_errors(app):
    from .errors import _response
    async def handler(request,error):
        return _response(error.status,error.code,str(error))
    async def body_limit_handler(request,error):
        failure=CorrectionError('UPLOAD_TOO_LARGE')
        return _response(failure.status,failure.code,str(failure))
    app.add_exception_handler(CorrectionError,handler)
    app.add_exception_handler(CorrectionBodyLimit,body_limit_handler)
