"""HTTP dependency overrides, safe errors and preheader integrity boundaries."""
import asyncio
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from backend.api.export_examples import export_examples
from backend.app.core.export import (
    AuditNotExportable, AuditNotReady, ExportDependencyError, ExportNotFound, ExportNotReady,
)
from backend.app.main import create_app
from backend.app.models.export import Artifact, ExportPage, ExportStarted, PublishedArtifacts
from backend.app.routers import exports
from backend.app.services.export_download import ExportDownloadService
from backend.app.services.export_files import create_attempt
from backend.tests.export_support import synthetic
from backend.tests.dataset_fixtures import dataset_database_settings, empty_dataset_database_settings


@pytest.fixture
def api(tmp_path):
    app = create_app()
    frozen, settings, _ = synthetic(tmp_path)
    examples = export_examples()
    repository = Mock()
    repository.enqueue.return_value = ExportStarted.model_validate_json(__import__('json').dumps(examples['states'][0]))
    repository.get_export.return_value = repository.enqueue.return_value.export
    repository.list_exports.return_value = ExportPage(items=[], limit=20, offset=0, total=0, next_offset=None)
    service = Mock()
    app.dependency_overrides[exports.get_export_repository] = lambda: repository
    app.dependency_overrides[exports.get_export_settings] = lambda: settings
    app.dependency_overrides[exports.get_export_download_service] = lambda: service
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, repository, service, examples
    finally:
        app.dependency_overrides.clear()


def test_post_is_durable_service_delegation_and_public_only(api):
    client, repository, service, examples = api
    response = client.post('/api/v1/exports', json=examples['request'])
    assert response.status_code == 202
    assert repository.enqueue.call_count == 1
    assert 'samples' not in response.text and 'storage_key' not in response.text and 'claim_token' not in response.text
    assert response.json()['export']['included_count'] is None
    assert not service.method_calls


@pytest.mark.parametrize('body', [
    {'include_images': 1}, {'audit_id': '\x00'}, {'selection': 'unsure'},
    {'format': 'unsupported_format'}, {'max_samples': 1}, {'output_path': '/private'},
])
def test_invalid_public_body_has_no_enqueue(api, body):
    client, repository, _, examples = api
    response = client.post('/api/v1/exports', json={**examples['request'], **body})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'VALIDATION_ERROR'
    repository.enqueue.assert_not_called()


@pytest.mark.parametrize('exception,status,code', [
    (AuditNotReady(), 409, 'AUDIT_NOT_READY'),
    (AuditNotExportable(), 409, 'AUDIT_NOT_EXPORTABLE'),
    (ExportDependencyError('/private password SQL'), 503, 'DEPENDENCY_UNAVAILABLE'),
    (ValueError('/private password SQL'), 500, 'INTERNAL_ERROR'),
])
def test_safe_post_errors_do_not_misclassify_internal_failures(api, exception, status, code):
    client, repository, _, examples = api
    repository.enqueue.side_effect = exception
    response = client.post('/api/v1/exports', json=examples['request'])
    assert response.status_code == status and response.json()['error']['code'] == code
    assert '/private' not in response.text and 'password' not in response.text and 'SQL' not in response.text


@pytest.mark.parametrize('query', ['?limit=true', '?offset=-1', '?offset=9223372036854775808',
                                    '?limit=101', '?limit=1&limit=2', '?unknown=1'])
def test_invalid_collection_query_is_rejected(api, query):
    client, repository, _, _ = api
    assert client.get('/api/v1/exports' + query).status_code == 422
    repository.list_exports.assert_not_called()


def test_empty_collection_and_metadata_reads_never_start_work(api):
    client, repository, service, examples = api
    assert client.get('/api/v1/exports').json()['items'] == []
    identifier = examples['states'][0]['export']['export_id']
    assert client.get('/api/v1/exports/' + identifier).status_code == 200
    assert not service.method_calls


@pytest.mark.parametrize('method', ['get_manifest', 'download'])
def test_noncompleted_artifact_reads_are_conflicts(api, method):
    client, repository, service, examples = api
    getattr(service, method).side_effect = ExportNotReady()
    identifier = examples['states'][0]['export']['export_id']
    suffix = 'manifest' if method == 'get_manifest' else 'download'
    response = client.get(f'/api/v1/exports/{identifier}/{suffix}')
    assert response.status_code == 409 and response.json()['error']['code'] == 'EXPORT_NOT_READY'


def test_same_verified_descriptor_is_streamed_and_closed(tmp_path):
    frozen, settings, _ = synthetic(tmp_path)
    token = 'd' * 32
    directory = create_attempt(settings.root, token)
    content = b'not a real ZIP: descriptor identity fixture'
    path = directory / 'archive.zip'
    path.write_bytes(content)
    artifact = Artifact(key=token + '/archive.zip', size_bytes=len(content), sha256=sha256(content).hexdigest())
    metadata = PublishedArtifacts(archive=artifact, manifest=Artifact(key=token + '/manifest.json', size_bytes=1, sha256='a' * 64))
    repository = SimpleNamespace(completed_artifacts=lambda export_id: (frozen, metadata))
    response = ExportDownloadService(repository, settings).download(frozen.manifest.export_id)
    # Replace the pathname after verification: the response must use its old FD.
    path.rename(directory / 'original.zip')
    path.write_bytes(b'replacement')
    sent = []
    async def receive():
        return {'type': 'http.disconnect'}
    async def send(message):
        sent.append(message)
    asyncio.run(response({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send))
    assert b''.join(message.get('body', b'') for message in sent) == content
    assert response._handle.closed
    assert response.headers['x-content-type-options'] == 'nosniff'


def test_descriptor_closes_when_response_send_is_cancelled(tmp_path):
    frozen, settings, _ = synthetic(tmp_path)
    token = 'e' * 32
    directory = create_attempt(settings.root, token)
    content = b'archive'
    (directory / 'archive.zip').write_bytes(content)
    metadata = PublishedArtifacts(
        archive=Artifact(key=token + '/archive.zip', size_bytes=len(content), sha256=sha256(content).hexdigest()),
        manifest=Artifact(key=token + '/manifest.json', size_bytes=1, sha256='a' * 64))
    response = ExportDownloadService(SimpleNamespace(completed_artifacts=lambda _: (frozen, metadata)), settings).download('ignored')
    async def receive():
        return {'type': 'http.disconnect'}
    async def send(message):
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(response({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send))
    assert response._handle.closed


def test_corrupt_artifact_is_rejected_before_response_creation(tmp_path):
    frozen, settings, _ = synthetic(tmp_path)
    token = 'f' * 32
    directory = create_attempt(settings.root, token)
    (directory / 'archive.zip').write_bytes(b'bad')
    metadata = PublishedArtifacts(
        archive=Artifact(key=token + '/archive.zip', size_bytes=3, sha256='0' * 64),
        manifest=Artifact(key=token + '/manifest.json', size_bytes=1, sha256='0' * 64))
    service = ExportDownloadService(SimpleNamespace(completed_artifacts=lambda _: (frozen, metadata)), settings)
    with pytest.raises(ExportDependencyError):
        service.download('ignored')


@pytest.mark.parametrize('selection,images,included', [
    ('accepted_only', False, 1), ('all_reviewed', True, 2),
])
def test_opt_in_postgresql_accept_worker_manifest_and_download(
    dataset_database_settings, tmp_path, selection, images, included,
):
    # Authored only. The imported fixture opts in using PRISM_TEST_DATABASE_URL
    # and an isolated schema. Nothing in this function ran during the handoff.
    from io import BytesIO
    import json
    from threading import Event
    from zipfile import ZipFile
    from backend.app.services.export_jobs import run_claimed_export
    from backend.app.storage.export_repository import ExportRepository
    from backend.app.storage.workflow_repository import JobRepository
    from backend.tests.test_export_repository import seed
    decisions = ('accepted', 'rejected', 'pending_review')
    audit, _ = seed(dataset_database_settings, tmp_path, decisions=decisions)
    _, settings, expected = synthetic(tmp_path, decisions=decisions, include_images=images,
                                      selection=selection)
    repository = ExportRepository(settings=dataset_database_settings)
    app = create_app()
    app.dependency_overrides[exports.get_export_repository] = lambda: repository
    app.dependency_overrides[exports.get_export_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            response = client.post('/api/v1/exports', json={
                'audit_id': audit.audit_id, 'format': 'penn_fudan',
                'selection': selection, 'include_images': images,
            })
            assert response.status_code == 202
            started = response.json()
            identity, job_id = started['export']['export_id'], started['job']['job_id']
            base = f'/api/v1/exports/{identity}'
            assert client.get(base + '/manifest').status_code == 409
            assert client.get(base + '/download').status_code == 409
            claim = repository.claim_next(worker_id='export_integration_worker')
            assert claim.job.job_id == job_id
            job = run_claimed_export(repository, claim, settings=settings, stop_event=Event())
            assert job.status.value == 'completed'
            # Existing public Job model/read boundary, not a new export job API.
            assert JobRepository(settings=dataset_database_settings).get_job(job_id) == job
            metadata = client.get(base)
            assert metadata.status_code == 200
            assert metadata.json()['included_count'] == included
            assert metadata.json()['excluded_count'] == len(decisions) - included
            assert metadata.json()['download_url'] == base + '/download'
            manifest_response = client.get(base + '/manifest')
            assert manifest_response.status_code == 200
            manifest = manifest_response.json()
            assert len(manifest['entries']) == len(decisions)
            downloaded = client.get(base + '/download')
            assert downloaded.status_code == 200
            assert downloaded.headers['content-type'] == 'application/zip'
            assert downloaded.headers['x-content-type-options'] == 'nosniff'
            assert int(downloaded.headers['content-length']) == len(downloaded.content)
            with ZipFile(BytesIO(downloaded.content)) as archive:
                assert json.loads(archive.read('manifest.json')) == manifest
                for entry in manifest['entries']:
                    sample_id = entry['sample_id']
                    roles = [('annotation_text', f'Annotation/{sample_id}.txt'),
                             ('raw_mask', f'PedMasks/{sample_id}_mask.png')]
                    if images:
                        roles.append(('original_image', f'PNGImages/{sample_id}.png'))
                    for role, name in roles:
                        if entry['included']:
                            assert archive.read(name) == expected[(sample_id, role)]
                        else:
                            assert name not in archive.namelist()
                if not images:
                    assert not any(name.startswith('PNGImages/') for name in archive.namelist())
                if selection == 'all_reviewed':
                    assert manifest['entries'][1]['review_decision'] == 'rejected'
                    assert manifest['entries'][1]['included']
    finally:
        app.dependency_overrides.clear()
