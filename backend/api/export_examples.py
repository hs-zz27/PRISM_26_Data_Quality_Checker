"""Model-validated synthetic export fixtures; no filesystem/database operations."""
from datetime import UTC, datetime
import json

from backend.app.models.export import (
    CapturedAvailability, Export, ExportManifest, ExportManifestEntry, ExportPage,
    ExportRequest, ExportStarted,
)
from backend.app.models.job import ApiError, Job, Progress, ResultReference

TIME = datetime(2026, 10, 21, tzinfo=UTC)
EXPORT_ID = 'export_' + 'a' * 32
JOB_ID = 'job_export_demo'


def export_examples():
    request = ExportRequest(audit_id='audit_demo', format='penn_fudan',
                            selection='all_reviewed', include_images=False)
    scenarios = []
    for status in ('queued', 'running', 'cancelling', 'completed', 'failed', 'cancelled'):
        completed = status == 'completed'
        resource = Export(**request.model_dump(), export_id=EXPORT_ID, job_id=JOB_ID,
                          status=status, included_count=2 if completed else None,
                          excluded_count=2 if completed else None,
                          download_url=f'/api/v1/exports/{EXPORT_ID}/download' if completed else None,
                          manifest_url=f'/api/v1/exports/{EXPORT_ID}/manifest' if completed else None,
                          created_at=TIME)
        job = Job(job_id=JOB_ID, kind='export', status=status,
                  progress=Progress(phase=status, processed=2 if completed else 0, total=2,
                                    fraction=1.0 if completed else 0.0),
                  result=ResultReference(kind='export', id=EXPORT_ID, version=None,
                                         href=f'/api/v1/exports/{EXPORT_ID}') if completed else None,
                  error=ApiError(code='EXPORT_FAILED', message='Export processing could not be completed.',
                                 request_id='request_demo') if status == 'failed' else None,
                  created_at=TIME, updated_at=TIME)
        scenarios.append(ExportStarted(export=resource, job=job).model_dump(mode='json'))
    availability = CapturedAvailability(annotation_text='available', raw_mask='available', original_image='missing')
    entries = []
    for ordinal, (decision, reason) in enumerate([
        ('accepted', None), ('rejected', None), ('pending_review', 'pending_review'),
        ('accepted', 'raw_mask_unreadable'),
    ]):
        roles = availability if ordinal != 3 else CapturedAvailability(
            annotation_text='available', raw_mask='unreadable', original_image='missing')
        entries.append(ExportManifestEntry(
            sample_id=f'sample_{ordinal:03d}', sample_revision=2, ordinal=ordinal,
            processing_state='failed' if ordinal == 0 else 'completed', included=reason is None,
            review_decision=decision, review_revision=3, reviewer_id='reviewer_demo',
            reviewed_at=TIME, availability=roles, reason=reason,
        ))
    manifest = ExportManifest(export_id=EXPORT_ID, audit_id='audit_demo', dataset_id='dataset_demo',
                              source_mode='snapshot', rulebook_id='rulebook_demo', rulebook_version=3,
                              selection='all_reviewed', include_images=False, selected_at=TIME, entries=entries)
    empty = ExportManifest(**{**manifest.model_dump(), 'entries': []})
    return {'request': request.model_dump(mode='json'), 'states': scenarios,
            'manifest': manifest.model_dump(mode='json'), 'empty_manifest': empty.model_dump(mode='json'),
            'page': ExportPage(items=[Export.model_validate_json(json.dumps(scenarios[3]['export']))], limit=20,
                               offset=0, total=1, next_offset=None).model_dump(mode='json')}
