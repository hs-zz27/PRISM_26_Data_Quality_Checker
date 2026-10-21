"""Authored strict-model/PNG/serialization regressions; NOT RUN in handoff."""
import json
from io import BytesIO
from unittest.mock import Mock

import numpy as np
from pydantic import ValidationError
import pytest

from backend.app.core.correction import CorrectionError, CorrectionPolicy
from backend.app.models.correction import CorrectionObject, CorrectionRequest, RecheckRequest
from backend.app.services.corrections import annotation_bytes, decode_png
from backend.app.datasets.penn_fudan import parse_annotations, parse_annotation_metadata
from backend.tests.correction_fixtures import png


@pytest.mark.parametrize('changes',[{'object_id':True},{'object_id':0},{'object_id':65536},{'object_id':'1'},
    {'label':''},{'label':' '},{'label':'a"b'},{'label':'a\nb'},{'label':'a\x00b'},{'label':'a\u2028b'},
    {'bbox':[0,1,2,2]},{'bbox':[1,2,1,1]},{'bbox':[True,1,2,2]},{'extra':1}])
def test_correction_objects_reject_invalid_structure(changes):
    with pytest.raises(ValidationError):
        CorrectionObject.model_validate_json(json.dumps({'object_id':1,'label':'person','bbox':[1,1,4,3]}|changes))


@pytest.mark.parametrize('label',['person','歩行者','piéton',' pedestrian ','δ'])
def test_utf8_labels_and_order_round_trip(label):
    objects=[CorrectionObject(object_id=65535,label=label,bbox=(4,3,4,3)),CorrectionObject(object_id=7,label='other',bbox=(1,1,1,1))]
    data=annotation_bytes(objects,(4,3),4,8192)
    assert parse_annotations(data.decode())==[o.model_dump() for o in objects]
    assert parse_annotation_metadata(data.decode())['image_size']==(4,3,4)


def test_zero_object_declaration_and_image_bounds():
    assert parse_annotations(annotation_bytes([],(4,3),3,1024).decode())==[]
    with pytest.raises(CorrectionError):
        annotation_bytes([CorrectionObject(object_id=1,label='x',bbox=(1,1,5,3))],(4,3),3,1024)


@pytest.mark.parametrize('mode',['RGB','RGBA','1'])
def test_mask_rejects_preview_or_unsupported_depth(mode):
    with pytest.raises(CorrectionError):
        decode_png(png(np.ones((3,4),dtype=np.uint8),mode=mode),CorrectionPolicy(),mask=True)


def test_palette_and_16bit_values_are_actual_ids():
    raw=np.array([[0,256,65535,1]]*3,dtype=np.uint16)
    assert decode_png(png(raw),CorrectionPolicy(),mask=True)==((4,3),[0,1,256,65535])
    assert decode_png(png(np.array([[0,7,254,255]]*3,dtype=np.uint8),mode='P'),CorrectionPolicy(),mask=True)[1]==[0,7,254,255]


def test_corrupt_png_and_decoded_pixel_budget():
    for raw in (b'not PNG',png()[:-20],png()):
        policy=CorrectionPolicy(max_pixels=1)
        with pytest.raises(CorrectionError):
            decode_png(raw,policy,mask=False)


@pytest.mark.parametrize('changes',[{'expected_revision':True},{'objects':[{'object_id':1,'label':'x','bbox':[1,1,2,2]}]*2},
    {'reason':'a\x00b'},{'expected_revision':2147483648},{'selected_revision':2}])
def test_request_is_strict(changes):
    with pytest.raises(ValidationError):
        CorrectionRequest.model_validate_json(json.dumps({'expected_revision':1,'reviewer_id':'r','reason':'edit','objects':[]}|changes))


@pytest.mark.parametrize('changes',[{'sample_ids':[]},{'sample_ids':['a','a']},{'sample_revisions':{'b':1}},
    {'sample_revisions':{'a':True}},{'sample_revisions':{}},{'feedback_policy_id':'x'}])
def test_recheck_exact_unique_nonempty_selection(changes):
    with pytest.raises(ValidationError):
        RecheckRequest.model_validate({'sample_ids':['a'],'sample_revisions':{'a':1},'reviewer_id':'r'}|changes)


def test_incoherent_limits_and_relative_storage_roots_are_rejected():
    from backend.app.core.correction import CorrectionPolicy,CorrectionSettings
    from pathlib import Path
    with pytest.raises(ValidationError):
        CorrectionPolicy(max_asset_bytes=1024)
    with pytest.raises(ValidationError):
        CorrectionSettings(asset_root=Path('relative'))


@pytest.mark.parametrize('revision,mask_changed',[(1,None),(1,True),(2,False),(2,True)])
def test_unattributed_history_cannot_invent_mask_provenance(revision,mask_changed):
    from datetime import datetime,timezone
    from backend.app.models.correction import AnnotationRevision
    with pytest.raises(ValidationError):
        AnnotationRevision(dataset_id='dataset_a',sample_id='sample_a',revision=revision,
            parent_revision=None,objects=[],mask_changed=mask_changed,reviewer_id=None,
            reason=None,source_audit_id=None,created_at=datetime.now(timezone.utc))


@pytest.mark.parametrize('revision,mask_changed',[(1,False),(2,None)])
def test_import_and_unknown_later_history_remain_distinct(revision,mask_changed):
    from datetime import datetime,timezone
    from backend.app.models.correction import AnnotationRevision
    value=AnnotationRevision(dataset_id='dataset_a',sample_id='sample_a',revision=revision,
        parent_revision=None,objects=[],mask_changed=mask_changed,reviewer_id=None,
        reason=None,source_audit_id=None,created_at=datetime.now(timezone.utc))
    assert value.mask_changed is mask_changed


@pytest.mark.parametrize('operation',['upload','correct'])
def test_mutated_service_policy_is_refused_before_repository_or_file_work(tmp_path,operation):
    from backend.app.core.correction import CorrectionSettings,CorrectionStorageError
    from backend.app.services.corrections import CorrectionService
    repository=Mock()
    service=CorrectionService(repository,CorrectionSettings(asset_root=tmp_path))
    service.settings.policy.max_mask_bytes=0
    with pytest.raises(CorrectionStorageError):
        if operation=='upload':
            service.upload('dataset_a','sample_a',1,BytesIO(b'not read'))
        else:
            service.correct('dataset_a','sample_a',CorrectionRequest(expected_revision=1,
                reviewer_id='r',reason='Edit.',objects=[]))
    assert repository.mock_calls==[]
    assert list(tmp_path.iterdir())==[]


def test_mask_upload_schema_matches_successor_revision_limit():
    from backend.app.models.correction import MaskUploadRequest
    assert MaskUploadRequest.model_json_schema()['properties']['expected_revision']['maximum']==2147483646
    with pytest.raises(ValidationError):
        MaskUploadRequest(mask='binary',expected_revision=2147483647)


def test_unknown_private_asset_role_cannot_silently_become_a_mask(tmp_path):
    from backend.app.core.correction import CorrectionSettings
    from backend.app.services.correction_files import write_asset
    with pytest.raises(ValueError):
        write_asset(CorrectionSettings(asset_root=tmp_path),b'bytes',role='thumbnail')
    assert list(tmp_path.iterdir())==[]


def test_service_operation_snapshot_is_independent_of_later_policy_mutation(tmp_path):
    from backend.app.core.correction import CorrectionSettings
    from backend.app.services.corrections import CorrectionService
    service=CorrectionService(Mock(),CorrectionSettings(asset_root=tmp_path))
    snapshot=service._settings_snapshot()
    expected=snapshot.policy.max_mask_bytes
    service.settings.policy.max_mask_bytes=1
    assert snapshot.policy.max_mask_bytes==expected
    assert snapshot.policy is not service.settings.policy
