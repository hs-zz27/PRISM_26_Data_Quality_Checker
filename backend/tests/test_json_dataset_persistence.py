"""Opt-in PostgreSQL tests, always scoped to disposable schemas."""
from copy import deepcopy

import pytest

from backend.app.core.correction import CorrectionSettings, CorrectionError
from backend.app.models.correction import CorrectionRequest
from backend.app.services.corrections import CorrectionService
from backend.app.services.dataset_import_jobs import run_claimed_import
from backend.app.services.dataset_upload import submit_archive
from backend.app.storage.dataset_import_repository import DatasetImportRepository
from backend.app.storage.correction_repository import CorrectionRepository
from backend.app.storage.workflow_repository import SampleAssetManifestRepository
from backend.tests.dataset_fixtures import dataset_database_settings, empty_dataset_database_settings, read_sql
from backend.tests.test_json_dataset_workflow import imported


@pytest.mark.parametrize('format', ['coco', 'labelme'])
def test_spawned_import_and_native_correction_are_durable(dataset_database_settings, tmp_path, format):
    _, settings, _, _ = imported(tmp_path, format)
    repo = DatasetImportRepository(settings=dataset_database_settings)
    started = submit_archive([(settings.root/'input.zip').read_bytes()], name='JSON examples',
                              format=format, repository=repo, settings=settings)
    claim = repo.claim_next(worker_id='json_test')
    assert claim.request.format == format
    job = run_claimed_import(repo, claim, settings=settings)
    assert job.status.value == 'completed', job.error
    dataset_id = started.dataset.dataset_id
    dataset = repo.get_dataset(dataset_id)
    assert dataset.format.value == format and dataset.status.value == 'ready'
    sample = repo.list_samples(dataset_id).items[0]
    assert sample.assets.image.content_type == 'image/jpeg'
    assert sample.assets.mask is None
    sample_id = sample.sample_id
    pin = repo.resolve_revision(dataset_id, sample_id)
    assert pin.manifest.annotation_format == format
    saved = SampleAssetManifestRepository(settings=dataset_database_settings).get_manifest(dataset_id, sample_id, 1)
    assert saved == pin.manifest
    objects = [obj.model_dump(mode='json') for obj in sample.objects]
    if format == 'labelme':
        objects[0]['label'] = 'pedestrian'
    else:
        objects[0]['bbox'][0] = 0.5
    request = CorrectionRequest.model_validate({'expected_revision': 1, 'reviewer_id': 'student',
                                               'reason': 'Reviewer correction', 'objects': objects})
    corrections = CorrectionRepository(settings=dataset_database_settings)
    service = CorrectionService(corrections, CorrectionSettings(asset_root=settings.asset_root))
    revision = service.correct(dataset_id, sample_id, request)
    assert revision.revision == 2
    assert repo.get_sample(dataset_id, sample_id).revision == 2
    assert repo.get_sample(dataset_id, sample_id, revision=1).objects == sample.objects
    assert [item.revision for item in corrections.history(dataset_id, sample_id).items] == [2, 1]
    with pytest.raises(CorrectionError) as error:
        service.correct(dataset_id, sample_id, request)
    assert error.value.code == 'STALE_SAMPLE_REVISION'


def test_legacy_manifest_serialization_keeps_feedback_hash_identity():
    from backend.tests.test_asset_manifest import manifest
    value = manifest()
    serialized = value.model_dump(mode='json')
    assert set(serialized) == {'dataset_id', 'sample_id', 'sample_revision', 'original_image', 'raw_mask', 'annotation_text'}
    assert value.annotation_format == 'penn_fudan'
