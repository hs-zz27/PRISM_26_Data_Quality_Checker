"""Authored thin-route, parser and safe-error coverage; no TestClient run here."""
from datetime import UTC,datetime,timedelta
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from backend.app.main import create_app
from backend.app.core.correction import CorrectionSettings,CorrectionError
from backend.app.models.correction import MaskUpload
from backend.app.routers.corrections import get_correction_service
from backend.app.routers.rechecks import get_recheck_repository

BASE='/api/v1/datasets/d/samples/s'


@pytest.fixture
def client(tmp_path):
    config=CorrectionSettings(asset_root=tmp_path/'assets')
    app=create_app(correction_settings_factory=lambda:config)
    service=Mock();service.settings=config
    app.dependency_overrides[get_correction_service]=lambda:service
    rechecks=Mock();app.dependency_overrides[get_recheck_repository]=lambda:rechecks
    with TestClient(app,raise_server_exceptions=False) as http:
        yield http,service,rechecks


def test_upload_parser_has_exact_fields_and_closes_handle(client):
    http,service,_=client
    service.upload.return_value=MaskUpload(mask_upload_id='mask_'+'a'*32,dataset_id='d',sample_id='s',base_revision=1,
        image_size=(4,3),mask_ids=[0,1],expires_at=datetime.now(UTC)+timedelta(hours=1))
    response=http.post(BASE+'/mask-uploads',data={'expected_revision':'1'},files={'mask':('mask.png',b'PNG','image/png')})
    assert response.status_code==201
    handle=service.upload.call_args.args[3]
    assert handle.closed and service.upload.call_args.args[:3]==('d','s',1)
    for revision in ('true','0','2147483647','1.0'):
        response=http.post(BASE+'/mask-uploads',data={'expected_revision':revision},files={'mask':('x',b'x')})
        assert response.status_code==422


@pytest.mark.parametrize('code',['STALE_SAMPLE_REVISION','MASK_UPLOAD_UNAVAILABLE','SOURCE_AUDIT_MISMATCH','SOURCE_NOT_CORRECTABLE','DEPENDENCY_UNAVAILABLE'])
def test_safe_narrow_error_mapping(client,code):
    http,service,_=client
    service.correct.side_effect=CorrectionError(code)
    response=http.post(BASE+'/revisions',json={'expected_revision':1,'reviewer_id':'r','reason':'edit','objects':[]})
    assert response.status_code==CorrectionError.CODES[code][0]
    error=response.json()['error'];assert error['code']==code and error['request_id']
    assert 'storage_key' not in response.text and 'Traceback' not in response.text


def test_strict_json_duplicate_query_and_recheck_selection(client):
    http,service,rechecks=client
    response=http.post(BASE+'/revisions',json={'expected_revision':True,'reviewer_id':'r','reason':'edit','objects':[]})
    assert response.status_code==422;service.correct.assert_not_called()
    response=http.post('/api/v1/audits/a/rechecks',json={'sample_ids':['s'],'sample_revisions':{'other':1},'reviewer_id':'r'})
    assert response.status_code==422;rechecks.start.assert_not_called()
    response=http.post(BASE+'/revisions?x=1',json={'expected_revision':1,'reviewer_id':'r','reason':'edit','objects':[]})
    assert response.status_code==422


def test_actual_ingress_and_unsupported_encoding(client):
    http,service,_=client
    response=http.post(BASE+'/mask-uploads',content=b'',headers={'Content-Length':str(100*1024**2)})
    assert response.status_code==413;service.upload.assert_not_called()
    response=http.post(BASE+'/revisions',content=b'{}',headers={'Content-Type':'application/json','Content-Encoding':'gzip'})
    assert response.status_code==415


def test_valid_json_box_array_reaches_service_without_integer_coercion(client):
    http,service,_=client
    service.correct.side_effect=CorrectionError('STALE_SAMPLE_REVISION')
    body={'expected_revision':1,'reviewer_id':'r','reason':'edit',
          'objects':[{'object_id':1,'label':'piéton','bbox':[1,1,4,3]}]}
    assert http.post(BASE+'/revisions',json=body).status_code==409
    assert service.correct.call_args.args[2].objects[0].bbox==(1,1,4,3)
    service.correct.reset_mock()
    body['objects'][0]['bbox'][0]=True
    assert http.post(BASE+'/revisions',json=body).status_code==422
    service.correct.assert_not_called()


@pytest.mark.parametrize('suffix,content_type',[('/revisions','application/json'),('/mask-uploads','multipart/form-data; boundary=abc')])
def test_measured_chunked_body_limit_survives_json_and_multipart_parsing(tmp_path,suffix,content_type):
    from backend.app.core.correction import CorrectionPolicy
    config=CorrectionSettings(asset_root=tmp_path/'assets',policy=CorrectionPolicy(
        max_mask_bytes=1024,max_asset_bytes=1024,max_annotation_bytes=1024))
    app=create_app(correction_settings_factory=lambda:config)
    service=Mock();service.settings=config
    app.dependency_overrides[get_correction_service]=lambda:service
    def chunks():
        if suffix=='/mask-uploads':
            yield b'--abc\r\nContent-Disposition: form-data; name="mask"; filename="x.png"\r\n\r\n'
        for _ in range(80):yield b' '*1024
    with TestClient(app,raise_server_exceptions=False) as http:
        response=http.post(BASE+suffix,content=chunks(),headers={'Content-Type':content_type})
    assert response.status_code==413
    assert response.json()['error']['code']=='UPLOAD_TOO_LARGE'
    assert response.json()['error']['request_id']
    service.correct.assert_not_called();service.upload.assert_not_called()


def test_mutated_ingress_configuration_returns_safe_dependency_error(tmp_path):
    config=CorrectionSettings(asset_root=tmp_path/'assets')
    config.policy.max_mask_bytes=0
    app=create_app(correction_settings_factory=lambda:config)
    service=Mock()
    app.dependency_overrides[get_correction_service]=lambda:service
    with TestClient(app,raise_server_exceptions=False) as http:
        response=http.post(BASE+'/revisions',json={'expected_revision':1,'reviewer_id':'r','reason':'edit','objects':[]})
    assert response.status_code==503
    assert response.json()['error']['code']=='DEPENDENCY_UNAVAILABLE'
    assert 'max_mask_bytes' not in response.text and str(tmp_path) not in response.text
    service.correct.assert_not_called()
