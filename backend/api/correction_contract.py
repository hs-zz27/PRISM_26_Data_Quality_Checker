"""Promote exactly four existing declarations using actual implemented transports."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json

from .common import BASE_PATH, copy_live_schemas
from backend.app.models.correction import AnnotationRevision, CorrectionRequest, MaskUpload, RevisionPage, RecheckRequest
from backend.app.models.audit_submission import AuditStarted
from backend.app.models.job import Job, JobStatus

OPERATIONS={
    ('/datasets/{dataset_id}/samples/{sample_id}/mask-uploads','post'):'uploadCorrectionMask',
    ('/datasets/{dataset_id}/samples/{sample_id}/revisions','post'):'correctAnnotations',
    ('/datasets/{dataset_id}/samples/{sample_id}/revisions','get'):'listAnnotationRevisions',
    ('/audits/{audit_id}/rechecks','post'):'recheckSamples',
}


def correction_examples(existing):
    now=datetime(2026,10,21,tzinfo=UTC)
    obj={'object_id':1,'label':'pedestrian','bbox':[1,1,4,3]}
    request=CorrectionRequest.model_validate_json(json.dumps({'expected_revision':1,'reviewer_id':'reviewer_demo',
        'reason':'Correct the complete object list.','objects':[obj],'mask_upload_id':'mask_'+'a'*32,'source_audit_id':'audit_demo'}))
    revision=AnnotationRevision.model_validate_json(json.dumps({'dataset_id':'dataset_demo','sample_id':'sample_001','revision':2,
        'parent_revision':1,'objects':[obj],'mask_changed':True,'reviewer_id':'reviewer_demo','reason':request.reason,
        'source_audit_id':'audit_demo','created_at':now.isoformat()}))
    original=AnnotationRevision.model_validate_json(json.dumps({**revision.model_dump(mode='json'),'revision':1,
        'parent_revision':None,'mask_changed':False,'reviewer_id':None,'reason':None,'source_audit_id':None}))
    mask=MaskUpload(mask_upload_id='mask_'+'a'*32,dataset_id='dataset_demo',sample_id='sample_001',base_revision=1,
        image_size=(4,3),mask_ids=[0,1],expires_at=now+timedelta(hours=1))
    page=RevisionPage(items=[revision,original],limit=20,offset=0,total=2,next_offset=None)
    recheck=RecheckRequest(sample_ids=['sample_001'],sample_revisions={'sample_001':2},reviewer_id='reviewer_demo')
    started=deepcopy(existing['recheckSamples']['response']['body'])
    started['job']['kind']='audit'
    started=AuditStarted.model_validate_json(json.dumps(started))
    return {'uploadCorrectionMask':mask.model_dump(mode='json'),'correctAnnotations':revision.model_dump(mode='json'),
        'listAnnotationRevisions':page.model_dump(mode='json'),'recheckSamples':started.model_dump(mode='json'),
        'correction_request':request.model_dump(mode='json'),'recheck_request':recheck.model_dump(mode='json')}


def align_correction_contract(contract,live,operations):
    templates={item['operation_id']:item for item in operations}
    examples=correction_examples(templates)
    for (path,method),identifier in OPERATIONS.items():
        old=contract['paths'][path][method]
        if old['operationId']!=identifier or sum(item['operation_id']==identifier for item in operations)!=1:
            raise ValueError('Correction declarations changed; inspect before promotion.')
        operation=deepcopy(live['paths'][BASE_PATH+path][method])
        if operation['operationId']!=identifier:
            raise ValueError('Correction live operation ID differs.')
        operation.update(summary=old['summary'],description=(
            'Immutable correction revisions preserve old audit/review/export evidence. '
            'Refresh on stale revision; uploads expire and are consumed once. '
            'Rechecks pin selected current heads through enqueue, preserve the parent rulebook, '
            'and create fresh pending reviews. Recheck jobs intentionally use kind=audit and the existing audit worker. '
            'Use include_rechecks=true on the original audit sample/queue/findings reads to show attempts in one workspace. '
            'Overlapping active rechecks of the same image return RECHECK_ALREADY_RUNNING (409). '
            'No correction or machine success implies human acceptance. '
            'Original provenance is nullable; unknown historical mask_changed is null. '
            'Object IDs are 1..65535; supported mask PNGs are grayscale 8/16-bit or palette indices 1/2/4/8-bit.'),
            **{'x-implementation-status':'implemented','x-backing-service-status':'Immutable correction repository/service and linked AUDIT enqueue.'})
        for parameter in operation.get('parameters',[]):
            name=parameter['name']
            parameter['example']= {'dataset_id':'dataset_demo','sample_id':'sample_001','audit_id':'audit_demo','limit':20,'offset':0}[name]
        entry=templates[identifier]
        body=operation.get('requestBody')
        if body:
            media_type='multipart/form-data' if identifier=='uploadCorrectionMask' else 'application/json'
            media=body['content'][media_type]
            schema={'$ref':'#/components/schemas/MaskUploadRequest'} if identifier=='uploadCorrectionMask' else media['schema']
            media['schema']=schema
            example=({'mask':'<binary integer-ID PNG>','expected_revision':1} if identifier=='uploadCorrectionMask' else
                examples['correction_request' if identifier=='correctAnnotations' else 'recheck_request'])
            media['example']=example
            entry['request']['body']=deepcopy(example)
        codes={
            '404':{'NOT_FOUND':'The requested resource does not exist.'},
            '413':{'UPLOAD_TOO_LARGE':'The correction request exceeds configured bounds.'},
            '415':{'UNSUPPORTED_MEDIA_TYPE':'Use the documented content representation.'},
            '422':{'VALIDATION_ERROR':'One or more fields are invalid.'},
            '500':{'INTERNAL_ERROR':'The request could not be completed.'},
            '503':{'DEPENDENCY_UNAVAILABLE':'Storage or durable publication is unavailable.'},
            '409':{'STALE_SAMPLE_REVISION':'Refresh the selected revision before resubmitting.'},
        }
        if identifier in {'correctAnnotations','uploadCorrectionMask'}:
            codes['422']['SOURCE_NOT_CORRECTABLE']='A verified PNG image and resulting integer-ID mask are required.'
        if identifier=='correctAnnotations':
            codes['409'].update(MASK_UPLOAD_UNAVAILABLE='The mask upload cannot be used.',SOURCE_AUDIT_MISMATCH='The source audit does not pin this base.')
        if identifier=='recheckSamples':
            codes['409'].update(RECHECK_ALREADY_RUNNING='Wait for the active recheck of this image to finish.',AUDIT_NOT_READY='Wait for a terminal parent.',AUDIT_NOT_RECHECKABLE='The parent lacks supported immutable lineage.',RULEBOOK_NOT_CONFIRMED='The recorded book must be confirmed.')
        for status,response in operation['responses'].items():
            for media_type,media in response.get('content',{}).items():
                if media_type!='application/json':
                    continue
                if status.startswith('2'):
                    media.pop('examples',None);media['example']=deepcopy(examples[identifier])
                else:
                    media.pop('example',None)
                    media['examples']={code:{'value':{'error':{'code':code,'message':message,'details':[],'request_id':'request_demo'}}} for code,message in codes[status].items()}
        contract['paths'][path][method]=operation
        entry.update(implementation='implemented',summary=operation['summary'],backing_service=operation['x-backing-service-status'])
        entry['response']['body']=deepcopy(examples[identifier])
    # All four models and dependent definitions are already Pydantic-derived in
    # build_schemas(). Keep live lossless definitions for the full response graph.
    copy_live_schemas(contract, live, OPERATIONS, keep={'MaskUploadRequest'})


def recheck_job_scenarios():
    """Delivered lifecycle snapshots use the existing executable AUDIT kind."""
    from .fixtures import job
    states={}
    for status in JobStatus:
        value=job('audit','audit','audit_recheck_demo',completed=status is JobStatus.COMPLETED)
        value['job_id']='job_recheck_demo'
        value['status']=status.value
        value['progress']['phase']=status.value
        if status is JobStatus.FAILED:
            value['error']={'code':'AUDIT_FAILED','message':'The audit could not be completed.',
                            'details':[],'request_id':'request_demo'}
        states[status.value]=Job.model_validate_json(json.dumps(value)).model_dump(mode='json')
    return states
