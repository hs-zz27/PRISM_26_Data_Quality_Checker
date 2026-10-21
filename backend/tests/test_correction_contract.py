"""Authored source/live contracts and frozen audit-kind compatibility adjustment."""
import json
from pathlib import Path

from fastapi.routing import APIRoute,iter_route_contexts

from backend.api.build_contract import build_contract,render_artifacts
from backend.api.schemas import build_schemas
from backend.api.common import replace_model_refs
from backend.api.correction_contract import OPERATIONS
from backend.app.main import create_app
from backend.app.models.correction import CorrectionRequest,CorrectionObject,AnnotationRevision,RevisionPage,MaskUpload,MaskUploadRequest,RecheckRequest
from backend.app.models.audit_submission import AuditStarted
from backend.app.models.job import Job,JobStatus
from backend.tests.test_rulebook_contract import assert_same_schema


def test_four_existing_operations_promoted_once_and_exact_inventory():
    app=create_app();live=app.openapi();generated=build_contract()
    ids=[c.original_route.operation_id for c in iter_route_contexts(app.routes) if isinstance(c.original_route,APIRoute) and c.original_route.include_in_schema]
    assert len(ids)==len(set(ids))==49
    operations=[op for methods in generated['paths'].values() for op in methods.values()]
    assert len(operations)==50 and sum(op['x-implementation-status']=='planned' for op in operations)==1
    for (path,method),identifier in OPERATIONS.items():
        one=generated['paths'][path][method];two=live['paths']['/api/v1'+path][method]
        assert one['operationId']==two['operationId']==identifier and ids.count(identifier)==1
        assert one['x-implementation-status']=='implemented' and set(one['responses'])==set(two['responses'])
        for status in one['responses']:
            for media_type,media in one['responses'][status].get('content',{}).items():
                assert_same_schema(media['schema'],two['responses'][status]['content'][media_type]['schema'],generated['components']['schemas'],live['components']['schemas'])
        first={(p['in'],p['name']):p for p in one.get('parameters',[])}
        second={(p['in'],p['name']):p for p in two.get('parameters',[])}
        assert set(first)==set(second)
        for key in first:
            assert first[key]['required']==second[key]['required']
            assert_same_schema(first[key]['schema'],second[key]['schema'],generated['components']['schemas'],live['components']['schemas'])
        if 'requestBody' in one:
            for media_type,media in one['requestBody']['content'].items():
                assert_same_schema(media['schema'],two['requestBody']['content'][media_type]['schema'],generated['components']['schemas'],live['components']['schemas'])


def test_public_models_no_later_handwritten_override():
    schemas=build_schemas()
    for model in (CorrectionRequest,CorrectionObject,AnnotationRevision,RevisionPage,MaskUpload,MaskUploadRequest,RecheckRequest):
        expected=model.model_json_schema();expected.pop('$defs',None)
        assert schemas[model.__name__]==replace_model_refs(expected)
    box=schemas['CorrectionBoundingBox']
    assert box['minItems']==box['maxItems']==4 and all(item['minimum']==1 for item in box['prefixItems'])
    assert schemas['CorrectionObject']['properties']['object_id']['maximum']==65535
    assert schemas['AnnotationRevision']['properties']['mask_changed']['anyOf']==[{'type':'boolean'},{'type':'null'}]


def test_recheck_fixtures_use_executable_audit_kind_and_declared_result_urls():
    document=build_contract()
    entry=document['paths']['/audits/{audit_id}/rechecks']['post']['responses']['202']['content']['application/json']['example']
    started=AuditStarted.model_validate_json(json.dumps(entry))
    assert started.job.kind.value=='audit' and started.parent_audit_id=='audit_demo'
    states=document['x-recheck-job-scenarios']
    assert set(states)=={state.value for state in JobStatus}
    for state,value in states.items():
        job=Job.model_validate_json(json.dumps(value));assert job.kind.value=='audit' and job.status.value==state
        if job.result is not None:
            assert job.result.href=='/api/v1/audits/'+job.result.id
    assert set(document['x-future-job-scenarios'])==set()


def test_generated_artifact_freshness_remains_a_required_gate():
    root=Path(__file__).resolve().parents[1]/'api'
    for name,expected in render_artifacts().items():
        assert (root/name).read_text(encoding='utf-8')==expected
