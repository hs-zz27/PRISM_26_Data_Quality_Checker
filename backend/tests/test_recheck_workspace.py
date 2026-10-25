"""Rechecks update a workspace while preserving every historical execution.

Uses isolated PostgreSQL fixtures. No model is needed; the worker runs the
existing deterministic label rule on tiny generated images.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from backend.app.core.correction import CorrectionError
from backend.app.core.export import AuditNotReady
from backend.app.models.export import ExportPolicy, ExportRequest
from backend.app.models.job import JobKind
from backend.app.models.review import SampleReview
from backend.app.storage.audit_read_repository import AuditReadRepository
from backend.app.storage.export_repository import ExportRepository
from backend.app.storage.recheck_repository import RecheckRepository
from backend.app.storage.workflow_repository import SampleReviewRepository
from backend.tests.dataset_fixtures import (
    dataset_database_settings, empty_dataset_database_settings,
)
from backend.tests.test_correction_repository import request as correction_request
from backend.tests.test_rechecks import prepared, selection


def accept(settings, audit_id, revision, now):
    SampleReviewRepository(settings=settings).save_sample_review(
        SampleReview(audit_id=audit_id, sample_id='sample_a', sample_revision=revision,
                     revision=1, decision='accepted', reviewer_id='human',
                     comment='Reviewed this exact attempt.', updated_at=now),
        expected_review_version=0,
    )


def export_request(audit_id, *, latest=True):
    return ExportRequest(audit_id=audit_id, format='penn_fudan', selection='accepted_only',
                         include_images=True, include_rechecks=latest)


def test_recheck_changes_queue_evidence_and_export_without_overwriting_parent(dataset_database_settings, tmp_path):
    settings = dataset_database_settings
    _, corrections, parent, audits, worker = prepared(settings, tmp_path)
    root = parent.audit.audit_id
    reads = AuditReadRepository(settings=settings)
    exports = ExportRepository(settings=settings)
    accept(settings, root, 1, parent.audit.created_at)
    original = reads.get_sample(root, 'sample_a')
    corrections.correct('dataset_a', 'sample_a', correction_request())
    child = RecheckRepository(settings=settings).start(root, selection(2))

    queued = reads.get_sample(root, 'sample_a', include_rechecks=True)
    assert queued.audit_id == child.audit.audit_id
    assert queued.workspace_audit_id == root
    assert queued.summary.sample_revision == 2
    assert queued.summary.processing_status == 'queued'
    assert queued.review.decision.value == 'pending_review'
    assert queued.findings == []
    assert [a.audit_id for a in queued.attempts] == [child.audit.audit_id, root]
    assert reads.list_samples(root, review_status='accepted', include_rechecks=True).total == 0
    assert reads.list_samples(root, review_status='accepted').total == 1
    assert reads.list_audit_findings(root, include_rechecks=True).total == 0
    assert reads.list_audit_findings(root).total == len(original.findings)
    with pytest.raises(AuditNotReady):
        exports.enqueue(export_request(root), policy=ExportPolicy())

    claim = audits.jobs.claim_next_job(worker_id='workspace', kind=JobKind.AUDIT, lease_seconds=300)
    assert worker.run_claimed_job(claim).status.value == 'completed'
    latest = reads.get_sample(root, 'sample_a', include_rechecks=True)
    assert latest.summary.result_audit_id == child.audit.audit_id
    assert latest.measurements['objects'][0]['annotation']['label'] == 'piéton'
    findings = reads.list_audit_findings(root, include_rechecks=True)
    assert findings.total > 0
    assert all(f.audit_id == child.audit.audit_id for f in findings.items)
    assert latest.review.decision.value == 'pending_review'
    accept(settings, child.audit.audit_id, 2, child.audit.created_at)
    assert reads.list_samples(root, review_status='accepted', include_rechecks=True).total == 1

    started = exports.enqueue(export_request(root), policy=ExportPolicy())
    frozen = exports.read_frozen(started.export.export_id)
    entry = frozen.manifest.entries[0]
    assert frozen.manifest.include_rechecks is True
    assert (entry.result_audit_id, entry.sample_revision, entry.included) == (child.audit.audit_id, 2, True)
    assert frozen.samples[0].manifest.sample_revision == 2
    historical = exports.enqueue(export_request(root, latest=False), policy=ExportPolicy())
    assert exports.read_frozen(historical.export.export_id).manifest.entries[0].sample_revision == 1

    # Another attempt must not alter old evidence, reviews, or frozen exports.
    newer = RecheckRepository(settings=settings).start(child.audit.audit_id, selection(2))
    audits.cancel_job(newer.job.job_id)
    cancelled = reads.get_sample(root, 'sample_a', include_rechecks=True)
    assert cancelled.audit_id == newer.audit.audit_id
    assert cancelled.summary.processing_status == 'cancelled'
    assert cancelled.review.decision.value == 'pending_review'
    assert len(cancelled.attempts) == 3
    excluded = exports.enqueue(export_request(root), policy=ExportPolicy())
    assert not exports.read_frozen(excluded.export.export_id).manifest.entries[0].included
    assert exports.read_frozen(started.export.export_id) == frozen
    old = reads.get_sample(root, 'sample_a')
    assert old.measurements == original.measurements
    assert old.findings == original.findings
    assert old.review == original.review
    assert audits.list_audits().total == 3
    assert [a.audit_id for a in audits.list_audits(roots_only=True).items] == [root]
    assert audits.list_audits(roots_only=True, offset=1).items == []


def test_concurrent_rechecks_of_same_image_enqueue_once(dataset_database_settings, tmp_path):
    settings = dataset_database_settings
    _, _, parent, audits, _ = prepared(settings, tmp_path)
    barrier = Barrier(2)

    def submit():
        barrier.wait(timeout=10)
        try:
            return RecheckRepository(settings=settings).start(parent.audit.audit_id, selection(1))
        except CorrectionError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert sum(result == 'RECHECK_ALREADY_RUNNING' for result in results) == 1
    assert audits.list_audits().total == 2
    child = next(result for result in results if not isinstance(result, str))
    audits.cancel_job(child.job.job_id)
    assert RecheckRepository(settings=settings).start(parent.audit.audit_id, selection(1)).job.status.value == 'queued'


def test_partial_recheck_keeps_original_order_and_unaffected_reviews(dataset_database_settings, tmp_path):
    from backend.app.models.audit import BatchAuditRequest
    from backend.app.storage.workflow_repository import AuditRepository, SampleAssetManifestRepository
    from backend.tests.dataset_fixtures import write_sql
    from backend.tests.test_export_repository import seed

    settings = dataset_database_settings
    parent, _ = seed(settings, tmp_path, decisions=('accepted', 'accepted', 'rejected'))
    audits = AuditRepository(settings=settings)
    assets = SampleAssetManifestRepository(settings=settings)
    # Reverse the subset's execution order to ensure the workspace keeps root order.
    ids = ['sample_2', 'sample_0']
    manifests = {name: assets.get_manifest(parent.dataset_id, name, 2) for name in ids}
    child, job = audits.start_audit(BatchAuditRequest(
        dataset_id=parent.dataset_id, dataset_root='', rulebook_id=parent.rulebook_id,
        rulebook_version=parent.rulebook_version, source_mode='snapshot',
        sample_ids=ids, sample_revisions={name: 2 for name in ids},
    ), sample_manifests=manifests)
    write_sql(settings, '''INSERT INTO audit_recheck_lineage
        (child_audit_id,parent_audit_id,reviewer_id,created_at) VALUES (%s,%s,%s,%s)''',
        (child.audit_id, parent.audit_id, 'human', child.created_at))
    reads = AuditReadRepository(settings=settings)
    page = reads.list_samples(parent.audit_id, include_rechecks=True)
    assert [s.sample_id for s in page.items] == ['sample_0', 'sample_1', 'sample_2']
    assert [s.result_audit_id for s in page.items] == [child.audit_id, parent.audit_id, child.audit_id]
    assert [s.review_status.value for s in page.items] == ['pending_review', 'accepted', 'pending_review']
    filtered = reads.list_samples(parent.audit_id, include_rechecks=True, tool_status='pending', limit=1)
    assert filtered.total == 2 and filtered.next_offset == 1
    assert filtered.items[0].sample_id == 'sample_0'
    assert reads.list_samples(parent.audit_id, include_rechecks=True, tool_status='pending', offset=1).items[0].sample_id == 'sample_2'
    audits.cancel_job(job.job_id)
    exports = ExportRepository(settings=settings)
    started = exports.enqueue(export_request(parent.audit_id), policy=ExportPolicy())
    entries = exports.read_frozen(started.export.export_id).manifest.entries
    assert [e.sample_id for e in entries] == ['sample_0', 'sample_1', 'sample_2']
    assert [e.included for e in entries] == [False, True, False]
    assert [e.ordinal for e in entries] == [0, 1, 2]
