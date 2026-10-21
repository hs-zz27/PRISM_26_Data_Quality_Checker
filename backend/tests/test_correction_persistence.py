"""Phase 1 metadata-only transactions; no Phase 2 imports or file decoding."""
from copy import deepcopy
from hashlib import sha256

import pytest

from backend.app.core.correction import CorrectionError
from backend.app.models.correction import CorrectionRequest, PreparedCorrection
from backend.app.models.dataset_import import SamplePublication
from backend.app.storage.correction_repository import CorrectionRepository
from backend.app.storage.dataset_repository import DatasetRepository
from backend.tests.dataset_fixtures import (
    dataset_database_settings, empty_dataset_database_settings, prepared_sample,
    read_sql, seed_import, write_sql,
)


def prepared(settings):
    original=prepared_sample()
    raw=original.model_dump()
    # Metadata-only synthetic roles. Byte correctness is covered in Phase 2.
    raw['manifest']['raw_mask']=deepcopy(raw['manifest']['original_image'])
    raw['sample'].update(image_size=(4,3),integrity_status='valid',issues=[],
                         objects=[{'object_id':1,'label':'person','bbox':(1,1,4,3)}])
    raw['annotation_object_count']=1
    base=SamplePublication.model_validate(raw)
    _,_,claim=seed_import(settings)
    DatasetRepository(settings=settings)._publish_import_sample(claim,base)
    write_sql(settings,"UPDATE datasets SET status='ready',sample_count=1,object_count=1 WHERE dataset_id='dataset_a'")
    body=CorrectionRequest(expected_revision=1,reviewer_id='r',reason='Edit label.',
        objects=[{'object_id':1,'label':'piéton','bbox':(1,1,4,3)}])
    new=base.model_dump()
    new['manifest']['sample_revision']=2
    annotation=b'Independent metadata-only corrected annotation identity.\n'
    new['manifest']['annotation_text'].update(storage_key='corrections/v1/metadata/annotation.txt',
        content_type='text/plain',size_bytes=len(annotation),sha256=sha256(annotation).hexdigest())
    new['sample'].update(revision=2,objects=[obj.model_dump() for obj in body.objects])
    return CorrectionRepository(settings=settings),PreparedCorrection(request=body,base=base,revised=SamplePublication.model_validate(new))


def test_phase1_publication_history_and_stale_cas(dataset_database_settings):
    repo,value=prepared(dataset_database_settings)
    saved=repo.publish(value)
    assert saved.revision==2 and saved.parent_revision==1
    assert repo.get_sample('dataset_a','sample_a').objects[0].label=='piéton'
    assert repo.history('dataset_a','sample_a').total==2
    assert repo.history('dataset_a','sample_a',offset=1).items[0].reviewer_id is None
    with pytest.raises(CorrectionError) as error:repo.publish(value)
    assert error.value.code=='STALE_SAMPLE_REVISION'


def test_phase1_transaction_rolls_back_provenance_and_head(dataset_database_settings,monkeypatch):
    repo,value=prepared(dataset_database_settings)
    def fail(*args):raise RuntimeError('Synthetic response construction failure')
    monkeypatch.setattr(repo,'_history_item',fail)
    with pytest.raises(RuntimeError):repo.publish(value)
    assert repo.get_sample('dataset_a','sample_a').revision==1
    assert read_sql(repo.settings,'SELECT count(*) AS n FROM annotation_revision_provenance')==[{'n':0}]


def test_reused_annotation_registration_is_rejected_before_publication(dataset_database_settings):
    from pydantic import ValidationError
    repo,value=prepared(dataset_database_settings)
    raw=value.model_dump()
    raw['revised']['manifest']['annotation_text']=raw['base']['manifest']['annotation_text']
    with pytest.raises(ValidationError):
        value.revised=SamplePublication.model_validate(raw['revised'])
        repo.publish(value)
    assert repo.get_sample('dataset_a','sample_a').revision==1
    assert read_sql(repo.settings,'SELECT count(*) AS n FROM annotation_revision_provenance')==[{'n':0}]


def test_mask_lookup_revalidates_mutated_nested_base_before_connection(dataset_database_settings,monkeypatch):
    from unittest.mock import Mock
    from pydantic import ValidationError
    repo,value=prepared(dataset_database_settings)
    value.base.sample.revision=True
    connection=Mock(side_effect=AssertionError('No connection allowed'))
    monkeypatch.setattr(repo,'_connection',connection)
    with pytest.raises(ValidationError):
        repo.mask('mask_'+'a'*32,value.base)
    connection.assert_not_called()
