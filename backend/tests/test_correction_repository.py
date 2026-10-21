"""Opt-in PostgreSQL/file regressions. Authored only; no suite run by handoff."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from io import BytesIO
from threading import Barrier

import numpy as np
import pytest
from pydantic import ValidationError

from backend.app.core.correction import CorrectionError, CorrectionCommitUnknown, CorrectionStorageError
from backend.app.models.correction import CorrectionRequest
from backend.app.storage import correction_repository as module
from backend.app.storage.database import database_connection, CommitOutcomeUnknownError
from backend.tests.correction_fixtures import fixture,png
from backend.tests.dataset_fixtures import dataset_database_settings,empty_dataset_database_settings,read_sql,write_sql


def request(*,revision=1,objects=None,**changes):
    if objects is None:
        objects=[{'object_id':1,'label':'piéton','bbox':(1,1,4,3)}]
    return CorrectionRequest(expected_revision=revision,reviewer_id='reviewer',reason='Correct actual annotations.',objects=objects,**changes)


def test_real_revision_history_immutable_original_and_head_count(dataset_database_settings,tmp_path):
    repo,service,original=fixture(dataset_database_settings,tmp_path)
    before=original.manifest.model_dump_json()
    revised=service.correct('dataset_a','sample_a',request())
    assert revised.revision==2 and revised.parent_revision==1 and revised.mask_changed is False
    assert repo.get_sample('dataset_a','sample_a').objects[0].label=='piéton'
    assert repo.resolve_revision('dataset_a','sample_a',revision=1).manifest.model_dump_json()==before
    page=repo.history('dataset_a','sample_a',limit=1)
    assert page.total==2 and page.items[0].revision==2 and page.next_offset==1
    original_history=repo.history('dataset_a','sample_a',offset=1).items[0]
    assert original_history.reviewer_id is None and original_history.reason is None
    assert original_history.parent_revision is None and original_history.mask_changed is False
    assert repo.history('dataset_a','sample_a',offset=99).items==[]
    assert repo.get_dataset('dataset_a').object_count==1


@pytest.mark.parametrize('unknown',[False,True])
def test_zero_objects_and_count_delta_with_once_only_mask(dataset_database_settings,tmp_path,unknown):
    repo,service,original=fixture(dataset_database_settings,tmp_path,unknown_count=unknown)
    upload=service.upload('dataset_a','sample_a',1,BytesIO(png(np.zeros((3,4),dtype=np.uint16))))
    corrected=service.correct('dataset_a','sample_a',request(objects=[],mask_upload_id=upload.mask_upload_id))
    assert corrected.objects==[] and corrected.mask_changed is True
    assert repo.get_dataset('dataset_a').object_count==(None if unknown else 0)
    assert read_sql(repo.settings,'SELECT consumed_revision FROM correction_mask_uploads')[0]['consumed_revision']==2
    with pytest.raises(CorrectionError):
        service.correct('dataset_a','sample_a',request(revision=2,objects=[],mask_upload_id=upload.mask_upload_id))


def test_damaged_annotation_can_be_replaced_but_mask_ids_cannot_be_invented(dataset_database_settings,tmp_path):
    repo,service,_=fixture(dataset_database_settings,tmp_path,broken_annotation=True)
    with pytest.raises(CorrectionError) as error:
        service.correct('dataset_a','sample_a',request(objects=[]))
    assert error.value.code=='VALIDATION_ERROR'
    assert service.correct('dataset_a','sample_a',request()).revision==2
    assert repo.get_dataset('dataset_a').object_count is None


def test_two_competing_saves_one_success_one_conflict(dataset_database_settings,tmp_path):
    repo,service,_=fixture(dataset_database_settings,tmp_path)
    barrier=Barrier(2)
    original=repo.publish
    def publish(prepared):
        barrier.wait(timeout=10)
        return original(prepared)
    repo.publish=publish
    def run(_):
        try:
            return service.correct('dataset_a','sample_a',request()).revision
        except CorrectionError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(run,[0,1]))
    assert sorted(map(str,results))==['2','STALE_SAMPLE_REVISION']
    assert repo.history('dataset_a','sample_a').total==2


def test_upload_expiry_scope_and_tampering(dataset_database_settings,tmp_path):
    repo,service,_=fixture(dataset_database_settings,tmp_path)
    public=service.upload('dataset_a','sample_a',1,BytesIO(png(np.ones((3,4),dtype=np.uint16))))
    base=repo.base('dataset_a','sample_a',1)
    staged=repo.mask(public.mask_upload_id,base)
    path=service.settings.asset_root/staged.asset.storage_key
    path.write_bytes(b'tampered')
    with pytest.raises(CorrectionStorageError):
        service.correct('dataset_a','sample_a',request(mask_upload_id=public.mask_upload_id))
    with pytest.raises(CorrectionError) as error:
        repo.mask('mask_'+'0'*32,base)
    assert error.value.code=='MASK_UPLOAD_UNAVAILABLE'
    # Expiry is immutable in SQL; stage a valid but already-expired row via test-only
    # insertion with matching document timestamps, rather than disabling guards.
    from datetime import timedelta
    from psycopg.types.json import Jsonb
    old=staged.model_dump(mode='json');old['public']['mask_upload_id']='mask_'+'b'*32
    with database_connection(repo.settings) as connection:
        now=connection.execute('SELECT clock_timestamp() AS now').fetchone()['now']
        old['created_at']=(now-timedelta(hours=2)).isoformat()
        old['public']['expires_at']=(now-timedelta(hours=1)).isoformat()
        connection.execute('''INSERT INTO correction_mask_uploads(mask_upload_id,dataset_id,sample_id,base_revision,document,created_at,expires_at)
            VALUES(%s,'dataset_a','sample_a',1,%s,%s,%s)''',(old['public']['mask_upload_id'],Jsonb(old),now-timedelta(hours=2),now-timedelta(hours=1)))
    with pytest.raises(CorrectionError) as error:
        repo.mask(old['public']['mask_upload_id'],base)
    assert error.value.code=='MASK_UPLOAD_UNAVAILABLE'


def test_full_rollback_after_head_write_retains_only_unpublished_orphans(dataset_database_settings,tmp_path,monkeypatch):
    repo,service,_=fixture(dataset_database_settings,tmp_path)
    def fail(*args):
        raise RuntimeError('Synthetic post-write failure')
    monkeypatch.setattr(repo,'_history_item',fail)
    with pytest.raises(RuntimeError):
        service.correct('dataset_a','sample_a',request())
    assert repo.get_sample('dataset_a','sample_a').revision==1
    assert read_sql(repo.settings,'SELECT count(*) AS n FROM annotation_revision_provenance')==[{'n':0}]
    assert read_sql(repo.settings,'SELECT count(*) AS n FROM dataset_sample_revisions')==[{'n':1}]
    assert list((service.settings.asset_root/'corrections').rglob('annotation.txt'))


def test_unknown_commit_retains_possible_published_bytes(dataset_database_settings,tmp_path,monkeypatch):
    repo,service,_=fixture(dataset_database_settings,tmp_path)
    real=module.database_connection
    @contextmanager
    def unknown(settings,*,readonly=False):
        with real(settings,readonly=readonly) as connection:
            yield connection
        if not readonly:
            raise CommitOutcomeUnknownError('Synthetic lost acknowledgement')
    monkeypatch.setattr(module,'database_connection',unknown)
    with pytest.raises(CorrectionCommitUnknown):
        service.correct('dataset_a','sample_a',request())
    monkeypatch.setattr(module,'database_connection',real)
    assert repo.get_sample('dataset_a','sample_a').revision==2
    manifest=repo.resolve_revision('dataset_a','sample_a').manifest
    assert (service.settings.asset_root/manifest.annotation_text.storage_key).is_file()


def test_mutated_nested_request_is_revalidated(dataset_database_settings,tmp_path):
    repo,service,_=fixture(dataset_database_settings,tmp_path)
    body=request();body.objects[0].bbox=(0,0,0,0)
    with pytest.raises(ValidationError):
        service.correct('dataset_a','sample_a',body)
    assert repo.get_sample('dataset_a','sample_a').revision==1


def test_missing_image_and_revision_overflow_refuse_without_publication(dataset_database_settings,tmp_path,monkeypatch):
    from backend.tests.dataset_fixtures import seed_historical_revision
    repo,service,original=fixture(dataset_database_settings,tmp_path)
    (service.settings.asset_root/original.manifest.original_image.storage_key).unlink()
    with pytest.raises(CorrectionStorageError):
        service.correct('dataset_a','sample_a',request())
    assert repo.get_sample('dataset_a','sample_a').revision==1
    seed_historical_revision(repo.settings,original,revision=2147483647)
    with pytest.raises(CorrectionError) as error:
        repo.base('dataset_a','sample_a',2147483647)
    assert error.value.code=='VALIDATION_ERROR'


def test_upload_expiring_at_final_consumption_rolls_back_all_rows(dataset_database_settings,tmp_path,monkeypatch):
    from datetime import timedelta
    from backend.app.storage.database import database_connection
    repo,service,original=fixture(dataset_database_settings,tmp_path)
    public=service.upload('dataset_a','sample_a',1,BytesIO(png(np.ones((3,4),dtype=np.uint16))))
    # Move time forward only in the final SQL expression via a connection proxy.
    # This executes a real SQL UPDATE predicate when the test is authorized.
    real=module.database_connection
    class Proxy:
        def __init__(self,connection):self.connection=connection
        def execute(self,query,params=()):
            if 'AND expires_at>clock_timestamp()' in query:
                query=query.replace('expires_at>clock_timestamp()',"expires_at>clock_timestamp()+interval '2 hours'")
            return self.connection.execute(query,params)
        def __getattr__(self,name):return getattr(self.connection,name)
    @contextmanager
    def forward(settings,*,readonly=False):
        with real(settings,readonly=readonly) as connection:yield Proxy(connection)
    monkeypatch.setattr(module,'database_connection',forward)
    with pytest.raises(CorrectionError) as error:
        service.correct('dataset_a','sample_a',request(mask_upload_id=public.mask_upload_id))
    assert error.value.code=='MASK_UPLOAD_UNAVAILABLE'
    assert repo.get_sample('dataset_a','sample_a').revision==1
    assert read_sql(repo.settings,'SELECT consumed_revision FROM correction_mask_uploads')[0]['consumed_revision'] is None
