"""Opt-in isolated PostgreSQL/HTTP scenarios; authored, NEVER run for this handoff."""
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend.app.main import create_app
from backend.app.core.config import AssetStorageSettings
from backend.app.routers import assets as asset_routes, datasets as routes, jobs as job_routes
from backend.app.services.asset_retrieval import SampleAssetService
from backend.app.services.dataset_import_jobs import run_claimed_import
from backend.app.storage.dataset_import_repository import DatasetImportRepository
from backend.app.storage.dataset_repository import DatasetRepository, DatasetCommitOutcomeUnknownError
from backend.app.storage.workflow_repository import AuditRepository, JobRepository, SampleAssetManifestRepository
from backend.tests.dataset_http_fakes import http_settings
from backend.tests.dataset_import_fakes import entries, zip_bytes
from backend.tests.dataset_fixtures import (
    dataset_database_settings, empty_dataset_database_settings, prepared_sample,
    seed_historical_revision, seed_import, read_sql,
)


@pytest.fixture
def persisted_api(dataset_database_settings, tmp_path):
    effective = http_settings(tmp_path, max_archive_bytes=1048576)
    repository = DatasetImportRepository(settings=dataset_database_settings)
    manifests = SampleAssetManifestRepository(settings=dataset_database_settings,
                    asset_settings=AssetStorageSettings(root=effective.imports.asset_root))
    app = create_app(dataset_upload_settings_factory=lambda: effective)
    app.dependency_overrides[routes.get_dataset_repository] = lambda: repository
    app.dependency_overrides[routes.get_dataset_import_repository] = lambda: repository
    app.dependency_overrides[job_routes.get_job_repository] = lambda: JobRepository(settings=dataset_database_settings)
    app.dependency_overrides[job_routes.get_audit_repository] = lambda: AuditRepository(settings=dataset_database_settings)
    app.dependency_overrides[asset_routes.get_sample_asset_service] = lambda: SampleAssetService(manifests)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, repository, effective, manifests


def upload(client, content=None):
    return client.post('/api/v1/datasets', data={'name': 'Synthetic source', 'format': 'penn_fudan'},
                       files={'archive': ('ignored.zip', zip_bytes(entries()) if content is None else content,
                                          'application/zip')})


def test_202_is_restart_stable_queued_and_does_not_start_worker(persisted_api, dataset_database_settings):
    client, repository, settings, _ = persisted_api
    content = zip_bytes(entries())
    response = upload(client, content)
    assert response.status_code == 202
    pair = response.json()
    fresh = DatasetImportRepository(settings=dataset_database_settings)
    assert fresh.get_dataset(pair['dataset']['dataset_id']).status.value == 'importing'
    assert client.get('/api/v1/jobs/' + pair['job']['job_id']).json()['status'] == 'queued'
    row = read_sql(dataset_database_settings, 'SELECT * FROM dataset_import_requests WHERE job_id=%s',
                   (pair['job']['job_id'],))[0]
    assert row['started_at'] is None
    assert (settings.imports.root / row['archive_key']).read_bytes() == content
    assert client.get(f"/api/v1/datasets/{pair['dataset']['dataset_id']}/samples").json()['total'] == 0
    assert not (settings.imports.root / 'runs').exists()


def test_shared_queued_and_running_cancel_keep_dataset_and_job_coherent(persisted_api):
    client, repository, _, _ = persisted_api
    for running in (False, True):
        pair = upload(client).json()
        claimed = repository.claim_next(worker_id='http-test') if running else None
        url = '/api/v1/jobs/' + pair['job']['job_id'] + '/cancel'
        response = client.post(url)
        assert response.status_code == 202
        expected = 'cancelling' if running else 'cancelled'
        assert response.json()['status'] == expected
        if running:
            assert client.post(url).json()['status'] == 'cancelling'
            assert client.get('/api/v1/datasets/' + pair['dataset']['dataset_id']).json()['status'] == 'importing'
            assert repository.finish(claimed).status.value == 'cancelled'
        assert client.get('/api/v1/datasets/' + pair['dataset']['dataset_id']).json()['status'] == 'cancelled'
        assert client.post(url).status_code == 409


def test_explicit_worker_then_revision_pinned_raw_and_preview_delivery(persisted_api):
    client, repository, settings, _ = persisted_api
    content = zip_bytes(entries())
    pair = upload(client, content).json()
    claim = repository.claim_next(worker_id='http-assets')
    result = run_claimed_import(repository, claim, settings=settings.imports)
    assert result.status.value == 'completed'
    assert result.result.href == '/api/v1/datasets/' + pair['dataset']['dataset_id']
    dataset = client.get(result.result.href).json()
    assert dataset['status'] == 'ready' and dataset['sample_count'] == 1 and dataset['object_count'] == 0
    sample = client.get(result.result.href + '/samples/a').json()
    expected = dict(entries())
    for kind, entry in [('image', 'PNGImages/a.png'), ('mask', 'PedMasks/a_mask.png')]:
        link = sample['assets'][kind]
        assert link['revision'] == 1 and link['url'].endswith('?revision=1')
        raw = client.get(link['url'])
        assert raw.status_code == 200 and raw.content == expected[entry]
        assert raw.headers['content-type'] == 'image/png' and raw.headers['x-content-type-options'] == 'nosniff'
    for kind in ('thumbnail', 'mask_preview'):
        preview = client.get(sample['assets'][kind]['url'])
        assert preview.status_code == 200 and preview.content.startswith(b'\x89PNG\r\n\x1a\n')


def test_catalogue_reads_do_not_touch_files_or_decode_and_historical_heads_are_explicit(
    persisted_api, dataset_database_settings, monkeypatch,
):
    client, repository, _, manifests = persisted_api
    _, _, claim = seed_import(dataset_database_settings)
    first = prepared_sample()
    repository._publish_import_sample(claim, first)
    seed_historical_revision(dataset_database_settings, first, revision=2)
    def forbidden(*args, **kwargs):
        pytest.fail('Metadata reads must never open or decode source assets.')
    monkeypatch.setattr(manifests, 'read_asset_bytes', forbidden)
    from PIL import Image
    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(Path, 'open', forbidden)
    for path in ('/api/v1/datasets', '/api/v1/datasets/dataset_a', '/api/v1/datasets/dataset_a/samples'):
        assert client.get(path).status_code == 200
    root = '/api/v1/datasets/dataset_a/samples/sample_a'
    for suffix, revision in [('', 2), ('?revision=1', 1), ('?revision=2', 2)]:
        response = client.get(root + suffix)
        assert response.status_code == 200 and response.json()['revision'] == revision
        assert response.json()['assets']['image']['url'].endswith(f'?revision={revision}')
    assert client.get(root + '?revision=3').status_code == 404
    assert client.get('/api/v1/datasets/dataset_a/samples/absent').status_code == 404


def test_empty_known_parent_vs_missing_and_stable_sample_pages(persisted_api, dataset_database_settings):
    client, repository, _, _ = persisted_api
    _, _, claim = seed_import(dataset_database_settings)
    root = '/api/v1/datasets/dataset_a/samples'
    assert client.get(root).json()['items'] == []
    assert client.get('/api/v1/datasets/absent/samples').status_code == 404
    for ordinal, name in enumerate(('a', 'b', 'c')):
        repository._publish_import_sample(claim, prepared_sample(sample_id=name, ordinal=ordinal))
    ids = []
    for offset in range(4):
        response = client.get(root, params={'limit': 1, 'offset': offset})
        assert response.status_code == 200
        page = response.json()
        assert page['total'] == 3 and page['offset'] == offset
        ids.extend(sample['sample_id'] for sample in page['items'])
        assert page['next_offset'] == (offset + 1 if offset < 2 else None)
    assert ids == ['a', 'b', 'c']


def test_corrupt_readable_source_retains_raw_bytes_and_safe_preview_error(persisted_api):
    client, repository, settings, _ = persisted_api
    parts = [('PNGImages/a.png', b'corrupt-readable-source'), ('Annotation/a.txt', b'bad annotation')]
    pair = upload(client, zip_bytes(parts)).json()
    result = run_claimed_import(repository, repository.claim_next(worker_id='dirty'), settings=settings.imports)
    assert result.status.value == 'completed'
    dataset_url = '/api/v1/datasets/' + pair['dataset']['dataset_id']
    assert client.get(dataset_url).json()['object_count'] is None
    sample = client.get(dataset_url + '/samples/a').json()
    assert sample['integrity_status'] == 'invalid' and sample['issues'] and sample['objects'] == []
    assert sample['assets']['mask'] is None
    assert client.get(sample['assets']['image']['url']).content == b'corrupt-readable-source'
    preview = client.get(sample['assets']['thumbnail']['url'])
    assert preview.status_code == 422 and preview.json()['error']['code'] == 'ASSET_NOT_RENDERABLE'


def test_lost_acceptance_ack_is_not_202_or_replayed_and_retains_archive(persisted_api, monkeypatch):
    client, repository, settings, _ = persisted_api
    enqueue = repository.enqueue
    calls = []
    def lost_ack(*args, **kwargs):
        calls.append(args[0])
        enqueue(*args, **kwargs)  # Committed but HTTP layer cannot know acknowledgement.
        raise DatasetCommitOutcomeUnknownError('PRIVATE_ACK')
    monkeypatch.setattr(repository, 'enqueue', lost_ack)
    response = upload(client)
    assert response.status_code == 503 and response.json()['error']['code'] == 'DEPENDENCY_UNAVAILABLE'
    assert len(calls) == 1 and 'PRIVATE_ACK' not in response.text
    assert (settings.imports.root / calls[0].archive_key).is_file()
    assert repository.get_dataset(calls[0].dataset_id).status.value == 'importing'
