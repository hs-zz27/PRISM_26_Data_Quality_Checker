"""Authored strict-model and selection regressions; intentionally unexecuted."""
from itertools import product

from pydantic import ValidationError
import pytest

from backend.app.models.export import (
    CapturedAvailability, ExportRequest, ExportSelection, FrozenExport, exclusion,
)
from backend.app.models.review import SampleReviewDecision
from backend.tests.export_support import synthetic


@pytest.mark.parametrize('decision,selection,images', product(
    list(SampleReviewDecision), list(ExportSelection), [False, True]))
def test_human_decision_matrix(decision, selection, images):
    availability = CapturedAvailability(annotation_text='available', raw_mask='available', original_image='available')
    reason = exclusion(decision, selection, images, availability)
    eligible = decision.value == 'accepted' or (decision.value == 'rejected' and selection.value == 'all_reviewed')
    assert (reason is None) is eligible


@pytest.mark.parametrize('role,state,images', product(
    ['annotation_text', 'raw_mask', 'original_image'], ['missing', 'unreadable'], [False, True]))
def test_specific_missing_role_reasons(role, state, images):
    availability = CapturedAvailability(**{name: state if name == role else 'available'
                                           for name in CapturedAvailability.model_fields})
    reason = exclusion(SampleReviewDecision.ACCEPTED, ExportSelection.ACCEPTED_ONLY, images, availability)
    assert (None if reason is None else reason.value) == (None if role == 'original_image' and not images else role + '_' + state)


@pytest.mark.parametrize('overrides', [
    {'audit_id': ''}, {'audit_id': '   '}, {'audit_id': 'a\x00b'}, {'audit_id': 3},
    {'audit_id': 'a' * 256}, {'format': 'coco'}, {'include_images': 1}, {'include_images': 'false'},
    {'selection': 'unsure'}, {'selection': 'latest'}, {'output_path': '/private'},
    {'include_rechecks': 1}, {'include_rechecks': 'true'},
])
def test_request_rejects_invalid_or_private_fields(overrides):
    body = dict(audit_id='audit_demo', format='penn_fudan', selection='accepted_only', include_images=True)
    body.update(overrides)
    with pytest.raises(ValidationError):
        ExportRequest.model_validate(body)


def test_review_counter_and_sample_revision_are_distinct(tmp_path):
    frozen, _, _ = synthetic(tmp_path)
    assert frozen.samples[0].entry.sample_revision == 2
    assert frozen.samples[0].entry.review_revision == 7
    assert frozen.samples[0].entry.processing_state == 'failed'
    assert frozen.samples[0].entry.included


def test_mutated_nested_snapshot_is_revalidated(tmp_path):
    frozen, _, _ = synthetic(tmp_path)
    frozen.manifest.entries.clear()
    with pytest.raises(ValidationError):
        FrozenExport.model_validate_json(frozen.model_dump_json())


def test_latest_export_requires_exact_attempt_provenance(tmp_path):
    frozen, _, _ = synthetic(tmp_path)
    frozen.manifest.include_rechecks = True
    with pytest.raises(ValidationError, match='explicit result audit'):
        FrozenExport.model_validate_json(frozen.model_dump_json())


def test_reopened_review_does_not_fall_back_to_old_acceptance(tmp_path):
    frozen, _, _ = synthetic(tmp_path, decisions=('pending_review',))
    entry = frozen.manifest.entries[0]
    assert entry.review_revision == 7
    assert entry.reason.value == 'pending_review' and not entry.included


def test_all_reviewed_preserves_rejected_decision(tmp_path):
    frozen, _, _ = synthetic(tmp_path, decisions=('rejected',), selection='all_reviewed')
    assert frozen.manifest.entries[0].included
    assert frozen.manifest.entries[0].review_decision.value == 'rejected'


def test_frozen_manifest_contains_every_request_provenance_field(tmp_path):
    from backend.app.models.export import ExportRequest
    from backend.tests.export_support import synthetic
    frozen, _, _ = synthetic(tmp_path)
    body = ExportRequest.model_validate({
        name: getattr(frozen.manifest, name) for name in ExportRequest.model_fields
    })
    assert body.format == 'penn_fudan'
    assert body.audit_id == frozen.manifest.audit_id


@pytest.mark.parametrize('limit,offset,total,next_offset', [
    (20, 0, 1, 0), (20, 0, 1, None), (20, 10, 0, 10), (True, 0, 0, None),
])
def test_page_rejects_invalid_empty_continuation(limit, offset, total, next_offset):
    from backend.app.models.export import ExportPage
    with pytest.raises(ValidationError):
        ExportPage(items=[], limit=limit, offset=offset, total=total, next_offset=next_offset)
