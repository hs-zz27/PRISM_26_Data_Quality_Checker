"""Audit acceptance coverage, including opt-in isolated PostgreSQL integration."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

from pydantic import ValidationError
import pytest

from backend.app.models.audit import AuditRun, AuditSourceMode, BatchAuditRequest
from backend.app.models.audit_submission import AuditRequest
from backend.app.models.dataset_import import CatalogueRevision
from backend.app.models.job import Job, JobKind
from backend.app.models.rule import Rule, Rulebook
from backend.app.services.audit_submission import AuditRuleParametersError, AuditSubmissionService
from backend.app.services.batch_audit import BatchAuditError, BatchAuditService, RulebookNotConfirmedError
from backend.app.storage.database import database_connection
from backend.app.storage.dataset_repository import (
    DatasetNotReadyError, DatasetRepository, DatasetSampleNotFoundError, DatasetStorageError,
)
from backend.app.storage.repository import RulebookRepository
from backend.app.storage.workflow_repository import AuditRepository, WorkflowStorageError
from backend.tests.dataset_fixtures import (
    dataset_database_settings, empty_dataset_database_settings, prepared_sample,
    read_sql, seed_historical_revision, seed_import, write_sql,
)


def rulebook(**changes):
    values = dict(
        rulebook_id="book", version=1, guideline_text="Person labels only.",
        confirmation_state="confirmed", rules=[Rule(
            rule_id="labels", description="Person labels only.", check_name="allowed_labels",
            parameters={"allowed_labels": ["person"]}, origin="reviewer", confirmation_state="confirmed",
        )],
    )
    return Rulebook(**(values | changes))


@pytest.fixture
def submission():
    publication = prepared_sample()
    catalogue = Mock(spec=DatasetRepository)
    catalogue.resolve_audit_samples.return_value = {"sample_a": CatalogueRevision(
        dataset_id="dataset_a", sample_id="sample_a", revision=1, manifest=publication.manifest,
    )}
    books = Mock(spec=RulebookRepository)
    books.get_rulebook.return_value = rulebook()
    audits = Mock(spec=AuditRepository)
    now = datetime(2026, 10, 21, tzinfo=UTC)
    job = Job.queued(job_id="job", kind=JobKind.AUDIT, total=1, now=now)
    audits.start_audit.return_value = (AuditRun(
        audit_id="audit", job_id="job", dataset_id="dataset_a", rulebook_id="book",
        rulebook_version=1, source_mode="snapshot", status="queued", sample_count=1,
        processed_count=0, failed_count=0, created_at=now, updated_at=now,
    ), job)
    capturer = Mock(side_effect=AssertionError("Imported snapshots must never be recaptured."))
    batch = BatchAuditService(
        rulebook_repository=books, audit_repository=audits, snapshot_capturer=capturer,
        sample_max_seconds=30,
    )
    service = AuditSubmissionService(catalogue=catalogue, batch=batch)
    return service, catalogue, books, audits, capturer


def test_submission_uses_manifest_only_pins_without_running_or_recapturing(submission):
    service, catalogue, books, audits, capturer = submission
    result = service.start(AuditRequest(dataset_id="dataset_a", rulebook_id="book", rulebook_version=1))
    catalogue.resolve_audit_samples.assert_called_once_with("dataset_a", None)
    books.get_rulebook.assert_called_once_with("book", version=1)
    saved = audits.start_audit.call_args.args[0]
    assert saved.source_mode is AuditSourceMode.SNAPSHOT
    assert saved.dataset_root == "" and saved.allowed_labels is None
    assert saved.sample_ids == ["sample_a"] and saved.sample_revisions == {"sample_a": 1}
    assert audits.start_audit.call_args.kwargs["sample_manifests"]["sample_a"].sample_revision == 1
    assert result.audit.job_id == result.job.job_id and result.job.status.value == "queued"
    assert result.parent_audit_id is None and result.feedback_policy_id is None
    capturer.assert_not_called()
    audits.claim_audit.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"sample_ids": []}, {"sample_ids": ["a", "a"]}, {"sample_ids": [" "]},
    {"rulebook_version": True}, {"rulebook_version": "1"}, {"rulebook_version": 0},
    {"rulebook_version": 2147483648}, {"dataset_id": "a\x00b"}, {"dataset_id": "a" * 256},
    {"dataset_root": "/private/path"}, {"source_mode": "legacy"},
    {"sample_revisions": {"a": 1}}, {"feedback_policy_id": "unsupported"},
])
def test_public_request_rejects_invalid_or_private_inputs(changes):
    with pytest.raises(ValidationError):
        AuditRequest.model_validate(dict(dataset_id="dataset_a", rulebook_id="book", rulebook_version=1) | changes)


def test_mutable_request_is_revalidated_before_catalogue_access(submission):
    service, catalogue, _, audits, _ = submission
    request = AuditRequest(dataset_id="dataset_a", rulebook_id="book", rulebook_version=1)
    request.sample_ids = ["a", "a"]
    with pytest.raises(ValidationError):
        service.start(request)
    catalogue.resolve_audit_samples.assert_not_called()
    audits.start_audit.assert_not_called()


@pytest.mark.parametrize("state", ["draft", "rejected"])
def test_unconfirmed_book_never_queues_work(submission, state):
    service, _, books, audits, _ = submission
    books.get_rulebook.return_value = rulebook(confirmation_state=state)
    with pytest.raises(RulebookNotConfirmedError):
        service.start(AuditRequest(dataset_id="dataset_a", rulebook_id="book", rulebook_version=1))
    audits.start_audit.assert_not_called()


def test_invalid_known_parameters_fail_but_unsupported_rules_remain_auditable(submission):
    service, _, books, audits, _ = submission
    book = rulebook()
    book.rules[0].parameters = {"allowed_labels": []}
    books.get_rulebook.return_value = book
    request = AuditRequest(dataset_id="dataset_a", rulebook_id="book", rulebook_version=1)
    with pytest.raises(AuditRuleParametersError):
        service.start(request)
    audits.start_audit.assert_not_called()
    book.rules[0].check_name = "future_check"
    service.start(request)
    audits.start_audit.assert_called_once()


@pytest.mark.parametrize("mode", ["missing", "different_revision", "legacy"])
def test_explicit_manifest_seam_rejects_unpinned_or_mismatched_inputs(submission, mode):
    service, _, _, audits, capturer = submission
    manifest = prepared_sample().manifest
    request = BatchAuditRequest(
        dataset_id="dataset_a", dataset_root="", rulebook_id="book", rulebook_version=1,
        sample_ids=["sample_a"], sample_revisions={"sample_a": 2 if mode == "different_revision" else 1},
        source_mode="legacy" if mode == "legacy" else "snapshot",
    )
    with pytest.raises(BatchAuditError):
        service.batch.start(request, sample_manifests={} if mode == "missing" else {"sample_a": manifest})
    audits.start_audit.assert_not_called()
    capturer.assert_not_called()


@pytest.fixture
def ready_catalogue(dataset_database_settings):
    settings = dataset_database_settings
    catalogue = DatasetRepository(settings=settings)
    _, _, claim = seed_import(settings)
    first = prepared_sample(sample_id="z", ordinal=0)
    second = prepared_sample(sample_id="a", ordinal=1)
    for publication in (first, second):
        catalogue._publish_import_sample(claim, publication)
    # Test-only completion; the production import worker owns this transition.
    write_sql(settings, "UPDATE datasets SET status='ready',sample_count=2 WHERE dataset_id='dataset_a'")
    books = RulebookRepository(settings=settings)
    books.save_rulebook(rulebook(), expected_version=0)
    audits = AuditRepository(settings=settings)
    service = AuditSubmissionService(catalogue=catalogue, batch=BatchAuditService(
        rulebook_repository=books, audit_repository=audits, sample_max_seconds=30,
    ))
    return settings, catalogue, books, audits, service, first, second


def test_catalogue_heads_are_pinned_and_higher_unselected_revisions_are_ignored(ready_catalogue):
    settings, catalogue, books, audits, service, first, _ = ready_catalogue
    seed_historical_revision(settings, first, revision=2)
    seed_historical_revision(settings, first, revision=3, advance_head=False)
    result = service.start(AuditRequest(dataset_id="dataset_a", rulebook_id="book", rulebook_version=1))
    stored = audits.get_request(result.audit.audit_id)
    assert stored.sample_ids == ["z", "a"]
    assert stored.sample_revisions == {"z": 2, "a": 1}
    assert stored.source_mode is AuditSourceMode.SNAPSHOT and stored.dataset_root == ""
    assert audits.get_job(result.audit.audit_id) == result.job
    rows = read_sql(settings, "SELECT sample_id,sample_revision,manifest_dataset_id FROM audit_samples WHERE audit_id=%s ORDER BY ordinal", (result.audit.audit_id,))
    assert rows == [
        {"sample_id": "z", "sample_revision": 2, "manifest_dataset_id": "dataset_a"},
        {"sample_id": "a", "sample_revision": 1, "manifest_dataset_id": "dataset_a"},
    ]
    seed_historical_revision(settings, first, revision=4)
    books.save_rulebook(rulebook(version=2, confirmation_state="draft"), expected_version=1)
    assert catalogue.resolve_revision("dataset_a", "z").revision == 4
    assert audits.get_request(result.audit.audit_id) == stored
    # Exact confirmed versions remain usable even if the latest version is draft.
    subset = service.start(AuditRequest(dataset_id="dataset_a", rulebook_id="book", rulebook_version=1, sample_ids=["a", "z"]))
    assert audits.get_request(subset.audit.audit_id).sample_ids == ["a", "z"]
    assert audits.get_request(subset.audit.audit_id).sample_revisions == {"a": 1, "z": 4}
    assert all(not read_sql(settings, f"SELECT 1 FROM {table}") for table in ("findings", "audit_measurements"))


def test_missing_sample_or_nonready_dataset_creates_no_audit(ready_catalogue):
    settings, _, _, _, service, _, _ = ready_catalogue
    request = AuditRequest(dataset_id="dataset_a", rulebook_id="book", rulebook_version=1, sample_ids=["absent"])
    with pytest.raises(DatasetSampleNotFoundError):
        service.start(request)
    write_sql(settings, "UPDATE datasets SET status='importing' WHERE dataset_id='dataset_a'")
    with pytest.raises(DatasetNotReadyError):
        service.start(request)
    assert read_sql(settings, "SELECT 1 FROM audit_runs") == []
    assert read_sql(settings, "SELECT 1 FROM jobs WHERE kind='audit'") == []


def test_ready_state_and_heads_use_one_read_snapshot(ready_catalogue, monkeypatch):
    settings, catalogue, _, _, _, first, _ = ready_catalogue
    read_dataset = catalogue._dataset

    def advance_after_dataset_read(connection, dataset_id):
        dataset = read_dataset(connection, dataset_id)
        # Commit a newer head on another connection between the two reads.
        seed_historical_revision(settings, first, revision=2)
        return dataset

    with monkeypatch.context() as patch:
        patch.setattr(catalogue, "_dataset", advance_after_dataset_read)
        pins = catalogue.resolve_audit_samples("dataset_a")
    assert pins["z"].revision == 1
    assert catalogue.resolve_revision("dataset_a", "z").revision == 2


def test_broken_catalogue_head_fails_closed_without_recapture(ready_catalogue):
    settings, catalogue, _, audits, service, _, _ = ready_catalogue
    # Deliberate corruption in this isolated test schema, bypassing the guard.
    with database_connection(settings) as connection:
        connection.execute("ALTER TABLE dataset_sample_heads DISABLE TRIGGER dataset_head_monotonic")
        connection.execute("DELETE FROM dataset_sample_heads WHERE dataset_id='dataset_a' AND sample_id='z'")
        connection.execute("ALTER TABLE dataset_sample_heads ENABLE TRIGGER dataset_head_monotonic")
    with pytest.raises(DatasetStorageError):
        catalogue.resolve_audit_samples("dataset_a")
    with pytest.raises(DatasetStorageError):
        service.start(AuditRequest(dataset_id="dataset_a", rulebook_id="book", rulebook_version=1))
    assert audits.list_audits().total == 0


def test_audit_job_and_samples_roll_back_together_on_missing_manifest(ready_catalogue):
    settings, _, _, audits, service, _, _ = ready_catalogue
    # Force an invalid pin at the internal seam: the FK must roll back all rows.
    request = BatchAuditRequest(dataset_id="dataset_a", dataset_root="", rulebook_id="book", rulebook_version=1,
                                sample_ids=["absent"], sample_revisions={"absent": 1}, source_mode="snapshot")
    with pytest.raises(WorkflowStorageError):
        service.batch.start(request, sample_manifests={"absent": prepared_sample(sample_id="absent").manifest})
    assert audits.list_audits().total == 0
    assert read_sql(settings, "SELECT 1 FROM jobs WHERE kind='audit'") == []


def test_listing_is_stable_and_public_and_worker_claims_only_audits(ready_catalogue):
    settings, _, _, audits, _, _, _ = ready_catalogue
    request = BatchAuditRequest(dataset_id="legacy", dataset_root="/private/source", rulebook_id="book", rulebook_version=1, sample_ids=["x"])
    now = datetime(2026, 10, 21, tzinfo=UTC)
    for name in ("a", "b", "c"):
        audits.start_audit(request, audit_id=name, job_id=f"audit_job_{name}", now=now)
    audits.request_cancellation("b", now=now)
    page = audits.list_audits(limit=2)
    assert [item.audit_id for item in page.items] == ["c", "b"]
    assert page.total == 3 and page.next_offset == 2
    assert page.items[1].status.value == "cancelled"
    assert [item.audit_id for item in audits.list_audits(limit=2, offset=2).items] == ["a"]
    assert audits.list_audits(offset=99).items == []
    assert "dataset_root" not in page.model_dump_json() and "/private/source" not in page.model_dump_json()
    other = Job.queued(job_id="export", kind=JobKind.EXPORT, total=1, now=now - timedelta(seconds=60))
    audits.jobs.create_job(other)
    claim = audits.jobs.claim_next_job(worker_id="worker", kind=JobKind.AUDIT, now=now)
    assert claim is not None and claim.job.kind is JobKind.AUDIT
    assert audits.jobs.get_job("export").status.value == "queued"
