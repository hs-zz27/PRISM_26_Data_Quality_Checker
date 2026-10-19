"""PostgreSQL workflow tests for durable audit progress and recovery.

These tests are intentionally opt-in with the rest of the PostgreSQL suite. A
local unit-test run does not need a database; CI or a developer can provide
``PRISM_TEST_DATABASE_URL`` to exercise the transaction boundaries.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import os
from time import sleep
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr
from PIL import Image
import pytest

from backend.app.core.config import AssetStorageSettings, DatabaseSettings
from backend.app.models.audit import AuditSampleClaim, AuditSourceMode, BatchAuditRequest
from backend.app.models.finding import FindingResolution
from backend.app.models.rule import ConfirmationState, Rule, Rulebook
from backend.app.services.batch_audit import BatchAuditService, ResolvedSampleRevision
from backend.app.services.asset_snapshot import capture_sample
from backend.app.services.evaluation import evaluate_rules
from backend.app.services.evaluation import CheckExecutionError
from backend.app.storage.database import database_connection, initialize_database
from backend.app.storage.repository import RulebookRepository
from backend.app.storage.workflow_repository import (
    AuditRepository, FindingRepository, SampleAssetManifestRepository,
)


NOW = datetime.now(UTC) - timedelta(seconds=1)
SOURCE = "Every object uses one of the allowed labels."


@pytest.fixture
def workflow_settings():
    conninfo = os.environ.get("PRISM_TEST_DATABASE_URL")
    if not conninfo:
        pytest.skip("Set PRISM_TEST_DATABASE_URL to run PostgreSQL integration tests.")

    schema = f"prism_workflow_{uuid4().hex}"
    with psycopg.connect(conninfo, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        settings = DatabaseSettings(conninfo=SecretStr(make_conninfo(
            conninfo, options=f"-c search_path={schema}",
        )))
        initialize_database(settings)
        yield settings
    finally:
        with psycopg.connect(conninfo, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def clean_rulebook() -> Rulebook:
    return Rulebook(
        rulebook_id="book_workflow",
        version=1,
        guideline_text=SOURCE,
        rules=[Rule(
            rule_id="labels",
            description="Use only the configured labels.",
            check_name="allowed_labels",
            parameters={"allowed_labels": ["person"]},
            origin="reviewer",
            source_sentence=None,
            confirmation_state=ConfirmationState.CONFIRMED,
        )],
        confirmation_state=ConfirmationState.CONFIRMED,
    )


def measurement_report(
    dataset_root: str,
    sample_id: str,
    allowed_labels: list[str] | None,
    connectivity: int,
) -> dict:
    return {
        "image_id": sample_id,
        "image_size": (2, 2),
        "annotation_text": SOURCE,
        "allowed_labels": ["person"],
        "status": "completed",
        "review_status": "pending_review",
        "errors": [],
        "checks": {},
        "objects": [{
            "annotation": {"object_id": 1, "label": "person", "bbox": (1, 1, 2, 2)},
            "checks": {"label": {
                "status": "completed", "reason": None, "detail": None,
                "result": {"object_id": 1, "label": "person", "matches": True, "reason": None},
            }},
        }],
    }


def source_failure_collector(dataset_root, sample_id, allowed_labels, connectivity):
    if sample_id == "bad":
        raise OSError("synthetic source-data failure")
    return measurement_report(dataset_root, sample_id, allowed_labels, connectivity)


def programming_failure_collector(dataset_root, sample_id, allowed_labels, connectivity):
    raise RuntimeError("synthetic programming failure")


def slow_collector(dataset_root, sample_id, allowed_labels, connectivity):
    sleep(1.5)
    return measurement_report(dataset_root, sample_id, allowed_labels, connectivity)


def hanging_collector(dataset_root, sample_id, allowed_labels, connectivity):
    sleep(10)
    return measurement_report(dataset_root, sample_id, allowed_labels, connectivity)


def large_result_collector(dataset_root, sample_id, allowed_labels, connectivity):
    report = measurement_report(dataset_root, sample_id, allowed_labels, connectivity)
    report["large_payload"] = "x" * 200_000
    return report


def incomplete_measurement_collector(dataset_root, sample_id, allowed_labels, connectivity):
    report = measurement_report(dataset_root, sample_id, allowed_labels, connectivity)
    report["status"] = "incomplete"
    report["errors"] = [{
        "stage": "load", "reason": "mask_load_failed", "detail": "synthetic missing mask",
    }]
    return report


def hanging_evaluator(*args, **kwargs):
    sleep(10)
    raise RuntimeError("synthetic evaluator hang ended unexpectedly")


def credential_observing_collector(dataset_root, sample_id, allowed_labels, connectivity):
    import os

    names = ("DATABASE_URL", "POSTGRES_PASSWORD", "PGPASSWORD")
    with open(f"{dataset_root}/child_credentials.log", "w", encoding="utf-8") as marker:
        marker.write("present" if any(name in os.environ for name in names) else "absent")
    return measurement_report(dataset_root, sample_id, allowed_labels, connectivity)


def transient_collector(dataset_root, sample_id, allowed_labels, connectivity):
    from backend.app.services.sample_worker import TransientSampleError

    with open(f"{dataset_root}/attempts.log", "a", encoding="utf-8") as marker:
        marker.write(f"{sample_id}\n")
    raise TransientSampleError("synthetic transient failure")


def make_service(
    settings,
    collector=measurement_report,
    lease_seconds=5,
    *,
    rule_evaluator=None,
    isolate_sample_computation=False,
    sample_max_seconds=900,
):
    rulebooks = RulebookRepository(settings=settings)
    audits = AuditRepository(settings=settings)
    rulebooks.save_rulebook(clean_rulebook())
    return BatchAuditService(
        rulebook_repository=rulebooks,
        audit_repository=audits,
        measurement_collector=collector,
        **({} if rule_evaluator is None else {"rule_evaluator": rule_evaluator}),
        lease_seconds=lease_seconds,
        isolate_sample_computation=isolate_sample_computation,
        sample_max_seconds=sample_max_seconds,
    ), audits


def test_snapshot_audit_persists_manifest_association_on_each_claim(workflow_settings, tmp_path):
    source_root = tmp_path / "source"
    for directory in ("PNGImages", "PedMasks", "Annotation"):
        (source_root / directory).mkdir(parents=True)
    (source_root / "PNGImages/sample.png").write_bytes(b"image")
    (source_root / "PedMasks/sample_mask.png").write_bytes(b"mask")
    (source_root / "Annotation/sample.txt").write_bytes(b"annotation")
    asset_settings = AssetStorageSettings(root=tmp_path / "managed")
    manifest_repository = SampleAssetManifestRepository(
        settings=workflow_settings, asset_settings=asset_settings,
    )
    manifest = capture_sample(
        dataset_id="dataset_1", sample_id="sample", sample_revision=2,
        source_root=source_root, repository=manifest_repository,
    )
    audits = AuditRepository(settings=workflow_settings)
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="legacy-root", rulebook_id="book_workflow",
        rulebook_version=1, sample_ids=["sample"], sample_revisions={"sample": 2},
        source_mode=AuditSourceMode.SNAPSHOT,
    )
    audits.start_audit(
        request, sample_manifests={"sample": manifest}, audit_id="snapshot_audit", job_id="snapshot_job",
    )
    job_claim = audits.claim_audit("snapshot_audit", worker_id="snapshot_worker")
    sample_claim = audits.claim_next_sample(job_claim)

    assert sample_claim is not None
    assert sample_claim.source_mode is AuditSourceMode.SNAPSHOT
    assert sample_claim.manifest_dataset_id == "dataset_1"
    with database_connection(workflow_settings, readonly=True) as connection:
        row = connection.execute(
            "SELECT source_mode FROM audit_runs WHERE audit_id = 'snapshot_audit'"
        ).fetchone()
        sample = connection.execute(
            """SELECT manifest_dataset_id, sample_revision FROM audit_samples
               WHERE audit_id = 'snapshot_audit' AND sample_id = 'sample'"""
        ).fetchone()
    assert row == {"source_mode": "snapshot"}
    assert sample == {"manifest_dataset_id": "dataset_1", "sample_revision": 2}


def start_snapshot_audit(settings, tmp_path, *, isolated):
    source = tmp_path / "source"
    for directory in ("PNGImages", "PedMasks", "Annotation"):
        (source / directory).mkdir(parents=True)
    Image.new("RGB", (2, 2)).save(source / "PNGImages/sample.png")
    Image.new("L", (2, 2), 1).save(source / "PedMasks/sample_mask.png")
    (source / "Annotation/sample.txt").write_text(
        'Image size (X x Y x C) : 2 x 2 x 3\n'
        'Objects with ground truth : 1 { "person" }\n'
        'Top left pixel co-ordinates : (1, 1)\n'
        'Bounding box for object 1 "person" (Xmin, Ymin) - (Xmax, Ymax) : (1, 1) - (2, 2)\n',
    )

    class Resolver:
        def resolve_samples(self, **kwargs):
            return {"sample": ResolvedSampleRevision(2, source)}

    rulebooks = RulebookRepository(settings=settings)
    rulebooks.save_rulebook(clean_rulebook())
    audits = AuditRepository(settings=settings)
    service = BatchAuditService(
        rulebook_repository=rulebooks, audit_repository=audits,
        sample_revision_resolver=Resolver(),
        manifest_repository=SampleAssetManifestRepository(
            settings=settings, asset_settings=AssetStorageSettings(root=tmp_path / "managed"),
        ),
        isolate_sample_computation=isolated,
    )
    audit, _ = service.start(BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="unused-legacy-root", rulebook_id="book_workflow",
        rulebook_version=1, sample_ids=["sample"], source_mode="snapshot",
    ))
    return service, audits, audit, source


@pytest.mark.parametrize("isolated", [False, True])
def test_snapshot_audit_persists_results_from_original_bytes(workflow_settings, tmp_path, isolated):
    service, audits, audit, source = start_snapshot_audit(workflow_settings, tmp_path, isolated=isolated)
    Image.new("RGB", (8, 8)).save(source / "PNGImages/sample.png")
    (source / "PedMasks/sample_mask.png").unlink()
    (source / "Annotation/sample.txt").write_text("changed source annotation")

    job = service.run_audit(audit.audit_id, worker_id="snapshot_worker")

    assert job.status.value == "completed"
    measurements = audits.get_measurements(audit.audit_id, "sample", 2)
    assert measurements["image_size"] == [2, 2]
    assert measurements["objects"][0]["annotation"]["label"] == "person"
    findings = FindingRepository(settings=workflow_settings).list_findings(audit.audit_id)
    assert len(findings) == 1
    assert findings[0].outcome.value == "pass"
    assert findings[0].sample_revision == 2
    assert findings[0].review.resolution is FindingResolution.PENDING
    assert audits.get_failures(audit.audit_id) == []


@pytest.mark.parametrize("isolated", [False, True])
@pytest.mark.parametrize("failure", ["temporary_storage", "snapshot_integrity"])
def test_snapshot_storage_failures_fail_the_job(
    workflow_settings, tmp_path, monkeypatch, isolated, failure,
):
    service, audits, audit, _ = start_snapshot_audit(workflow_settings, tmp_path, isolated=isolated)
    if failure == "temporary_storage":
        def disk_full(*args, **kwargs):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("backend.app.services.asset_snapshot.TemporaryDirectory", disk_full)
    else:
        repository = service.manifest_repository
        manifest = repository.get_manifest("dataset_1", "sample", 2)
        (repository.asset_settings.root / manifest.raw_mask.storage_key).write_bytes(b"tampered")

    job = service.run_audit(audit.audit_id, worker_id="snapshot_worker")

    assert job.status.value == "failed"
    assert job.error.code == "AUDIT_FAILED"
    assert audits.get_measurements(audit.audit_id, "sample", 2) is None
    assert all(failure.reason != "source_data_unavailable" for failure in audits.get_failures(audit.audit_id))


def test_retry_rebuilds_the_pydantic_sample_claim_with_validation(monkeypatch):
    class RetryRepository:
        def increment_sample_attempt(self, claim, *, operation_id):
            assert isinstance(claim, AuditSampleClaim)
            assert operation_id.startswith("sample_attempt\x1f")
            return claim.attempt_count + 1

        def checkpoint_sample_claim(self, claim, *, renew, lease_seconds):
            assert isinstance(claim, AuditSampleClaim)

    service = BatchAuditService(
        rulebook_repository=object(), audit_repository=RetryRepository(),
        sample_max_seconds=900,
    )
    now = datetime.now(UTC)
    claim = AuditSampleClaim(
        audit_id="audit_retry_model", job_id="job_retry_model", sample_id="sample",
        sample_revision=1, worker_id="worker", claim_token="token",
        lease_until=now + timedelta(seconds=60),
        processing_deadline=now + timedelta(seconds=900), attempt_count=1,
    )
    monkeypatch.setattr(service, "_retry_delay", lambda retry_number: 0.0)

    updated, should_retry = service._prepare_retry(claim, RuntimeError("transient"))

    assert should_retry is True
    assert isinstance(updated, AuditSampleClaim)
    assert updated.attempt_count == 2
    assert updated.lease_until > claim.lease_until
    assert updated.lease_until <= updated.processing_deadline


@pytest.mark.parametrize("entry_point", ["audit_id", "job_id", "queue_claim"])
def test_batch_audit_commits_measurements_findings_and_progress_per_sample(
    workflow_settings, entry_point,
):
    service, audits = make_service(workflow_settings)
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="not-validated-here",
        rulebook_id="book_workflow", rulebook_version=1,
        sample_ids=["one", "two"], sample_revisions={"two": 2},
        allowed_labels=["person"],
    )

    audit, queued_job = service.start(request, audit_id="audit_workflow", job_id="job_workflow", now=NOW)
    if entry_point == "queue_claim":
        claim = audits.jobs.claim_next_job(worker_id="worker_1")
        assert claim is not None and claim.job.job_id == queued_job.job_id
        completed = service.run_claimed_job(claim)
    elif entry_point == "job_id":
        completed = service.run_job(queued_job.job_id, worker_id="worker_1")
    else:
        completed = service.run_audit(audit.audit_id, worker_id="worker_1")

    assert queued_job.status.value == "queued"
    assert completed.status.value == "completed"
    assert completed.progress.processed == 2
    assert completed.progress.fraction == 1.0
    assert audits.get_audit(audit.audit_id).processed_count == 2
    assert audits.get_measurements(audit.audit_id, "two", 2)["image_id"] == "two"

    findings = FindingRepository(settings=workflow_settings).list_findings(audit.audit_id)
    assert len(findings) == 2
    assert all(finding.review.resolution is FindingResolution.PENDING for finding in findings)
    assert {finding.rulebook_version for finding in findings} == {1}


@pytest.mark.parametrize("replace_stale_claim", [False, True])
def test_cancellation_before_queue_dispatch_does_not_collect_samples(
    workflow_settings, replace_stale_claim,
):
    def collector(*args):
        pytest.fail("A cancelled queue claim must not collect a sample.")

    service, audits = make_service(workflow_settings, collector)
    past = datetime.now(UTC) - timedelta(minutes=2)
    audit, job = service.start(BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="root", rulebook_id="book_workflow",
        rulebook_version=1, sample_ids=["one"],
    ), now=past)
    if replace_stale_claim:
        stale = audits.claim_audit(audit.audit_id, worker_id="old_worker", now=past)
        assert stale is not None
        assert audits.claim_next_sample(stale, now=past) is not None
    claim = audits.jobs.claim_next_job(worker_id="worker_1")
    assert claim is not None
    assert service.cancel_job(job.job_id).status.value == "cancelling"

    cancelled = service.run_claimed_job(claim)

    assert cancelled.status.value == "cancelled"
    assert cancelled.progress.processed == 0
    assert audits.get_audit(audit.audit_id).status.value == "cancelled"
    assert audits.get_measurements(audit.audit_id, "one", 1) is None
    assert FindingRepository(settings=workflow_settings).list_findings(audit.audit_id) == []


def test_queue_takeover_resumes_unfinished_samples_and_preserves_results(workflow_settings):
    collected_samples = []

    def collector(dataset_root, sample_id, allowed_labels, connectivity):
        collected_samples.append(sample_id)
        assert audits.get_audit(audit.audit_id).status.value == "running"
        return measurement_report(dataset_root, sample_id, allowed_labels, connectivity)

    service, audits = make_service(workflow_settings, collector)
    past = datetime.now(UTC) - timedelta(minutes=2)
    audit, _ = service.start(BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="root", rulebook_id="book_workflow",
        rulebook_version=1, sample_ids=["done", "unfinished"],
    ), now=past)
    stale = audits.claim_audit(audit.audit_id, worker_id="old_worker", now=past)
    assert stale is not None
    done_claim = audits.claim_next_sample(stale, now=past)
    assert done_claim is not None and done_claim.sample_id == "done"
    measurements = measurement_report("root", "done", None, 8)
    findings = evaluate_rules(
        clean_rulebook(), measurements, audit_id=audit.audit_id,
        sample_id="done", sample_revision=1,
    )
    audits.persist_sample_success(done_claim, measurements=measurements, findings=findings, now=past)
    unfinished_claim = audits.claim_next_sample(stale, now=past)
    assert unfinished_claim is not None and unfinished_claim.sample_id == "unfinished"
    saved_measurements = audits.get_measurements(audit.audit_id, "done", 1)
    saved_findings = FindingRepository(settings=workflow_settings).list_findings(audit.audit_id)
    replacement = audits.jobs.claim_next_job(worker_id="new_worker")
    assert replacement is not None and replacement.claim_token != stale.claim_token

    completed = service.run_claimed_job(replacement)

    assert completed.status.value == "completed"
    assert completed.progress.processed == 2
    assert collected_samples == ["unfinished"]
    assert audits.get_audit(audit.audit_id).processed_count == 2
    assert audits.get_measurements(audit.audit_id, "done", 1) == saved_measurements
    all_findings = FindingRepository(settings=workflow_settings).list_findings(audit.audit_id)
    assert [finding for finding in all_findings if finding.sample_id == "done"] == saved_findings
    assert len(all_findings) == 2


@pytest.mark.parametrize("include_label_rules, shared_labels", [
    pytest.param(False, None, id="geometry-without-shared-labels"),
    pytest.param(True, None, id="per-rule-labels-without-shared-labels"),
    pytest.param(True, ["vehicle"], id="shared-labels-do-not-override-rule-parameters"),
])
def test_batch_audit_runs_with_optional_shared_labels(
    workflow_settings, tmp_path, include_label_rules, shared_labels,
):
    for folder in ("PNGImages", "Annotation", "PedMasks"):
        (tmp_path / folder).mkdir()
    Image.new("RGB", (2, 2)).save(tmp_path / "PNGImages/sample.png")
    Image.new("L", (2, 2), color=1).save(tmp_path / "PedMasks/sample_mask.png")
    (tmp_path / "Annotation/sample.txt").write_text(
        'Image size (X x Y x C) : 2 x 2 x 3\n'
        'Objects with ground truth : 1 { "person" }\n'
        '# Top left pixel co-ordinates : (1, 1)\n'
        'Bounding box for object 1 "person" (Xmin, Ymin) - (Xmax, Ymax) : (1, 1) - (2, 2)\n',
        encoding="utf-8",
    )
    rules = [Rule(
        rule_id="box", description="Boxes are valid.", check_name="box_validity",
        origin="reviewer", confirmation_state=ConfirmationState.CONFIRMED,
    )]
    if include_label_rules:
        rules.extend(Rule(
            rule_id=f"{label}_labels", description=f"Only {label} labels are allowed.",
            check_name="allowed_labels", parameters={"allowed_labels": [label]},
            origin="reviewer", confirmation_state=ConfirmationState.CONFIRMED,
        ) for label in ("person", "vehicle"))
    rulebook = Rulebook(
        rulebook_id="book_optional_labels", version=1,
        guideline_text="\n".join(rule.description for rule in rules), rules=rules,
        confirmation_state=ConfirmationState.CONFIRMED,
    )
    rulebooks = RulebookRepository(settings=workflow_settings)
    rulebooks.save_rulebook(rulebook)
    audits = AuditRepository(settings=workflow_settings)
    service = BatchAuditService(rulebook_repository=rulebooks, audit_repository=audits)
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root=str(tmp_path),
        rulebook_id=rulebook.rulebook_id, rulebook_version=rulebook.version,
        sample_ids=["sample"], allowed_labels=shared_labels,
    )

    audit, _ = service.start(request)
    completed = service.run_audit(audit.audit_id, worker_id="worker_1")

    assert completed.status.value == "completed"
    assert completed.progress.processed == 1
    assert completed.progress.fraction == 1.0
    stored_audit = audits.get_audit(audit.audit_id)
    assert stored_audit.status.value == "completed"
    assert stored_audit.processed_count == 1
    assert stored_audit.failed_count == 0
    assert audits.get_request(audit.audit_id).allowed_labels == shared_labels
    assert audits.get_failures(audit.audit_id) == []
    measurements = audits.get_measurements(audit.audit_id, "sample", 1)
    assert measurements is not None
    assert measurements["status"] == "completed"
    assert measurements["errors"] == []
    object_checks = measurements["objects"][0]["checks"]
    assert object_checks["box_validity"]["result"]["valid"] is True
    if shared_labels is None:
        assert "labels" not in measurements["checks"]
        assert "label" not in object_checks
    else:
        assert object_checks["label"]["result"]["matches"] is False

    findings = FindingRepository(settings=workflow_settings).list_findings(audit.audit_id)
    assert len(findings) == len(rules)
    expected_outcomes = {"box": "pass"}
    if include_label_rules:
        expected_outcomes.update(person_labels="pass", vehicle_labels="fail")
        for finding in findings:
            if finding.check_name == "allowed_labels":
                assert finding.evidence.measurements["actual_label"] == "person"
                assert finding.evidence.measurements["allowed_labels"] == finding.parameters["allowed_labels"]
    assert {finding.rule_id: finding.outcome.value for finding in findings} == expected_outcomes
    assert all(finding.review.resolution is FindingResolution.PENDING for finding in findings)


def test_sample_failures_are_durable_and_do_not_hide_successful_samples(workflow_settings):
    service, audits = make_service(workflow_settings, source_failure_collector)
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="root", rulebook_id="book_workflow",
        rulebook_version=1, sample_ids=["good", "bad"], allowed_labels=["person"],
    )
    audit, _ = service.start(request, audit_id="audit_failures", job_id="job_failures", now=NOW)

    completed = service.run_audit(audit.audit_id, worker_id="worker_1")

    assert completed.status.value == "completed"
    assert completed.progress.processed == 2
    assert completed.progress.fraction == 1.0
    assert audits.get_audit(audit.audit_id).failed_count == 1
    failures = audits.get_failures(audit.audit_id)
    assert len(failures) == 1 and failures[0].sample_id == "bad"
    assert audits.get_measurements(audit.audit_id, "good", 1) is not None


def test_isolated_check_failure_persists_incomplete_measurements_and_audit(
    workflow_settings, monkeypatch,
):
    def fail_check(*args, **kwargs):
        raise CheckExecutionError("synthetic check implementation failure")

    monkeypatch.setattr("backend.app.services.evaluation._evaluate_object_rule", fail_check)
    service, audits = make_service(workflow_settings)
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="root", rulebook_id="book_workflow",
        rulebook_version=1, sample_ids=["one"], allowed_labels=["person"],
    )
    audit, _ = service.start(request, audit_id="audit_incomplete", job_id="job_incomplete")

    completed = service.run_audit(audit.audit_id, worker_id="worker_1")

    assert completed.status.value == "completed"
    stored_audit = audits.get_audit(audit.audit_id)
    assert stored_audit.incomplete_count == 1
    assert audits.get_measurements(audit.audit_id, "one", 1)["status"] == "completed"
    assert FindingRepository(settings=workflow_settings).list_findings(audit.audit_id)[0].reason == (
        "check_execution_failed"
    )


def test_programming_errors_fail_the_job_instead_of_becoming_sample_failures(workflow_settings):
    service, audits = make_service(workflow_settings, programming_failure_collector)
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="root", rulebook_id="book_workflow",
        rulebook_version=1, sample_ids=["one"], allowed_labels=["person"],
    )
    audit, _ = service.start(request, audit_id="audit_programming", job_id="job_programming", now=NOW)

    failed = service.run_audit(audit.audit_id, worker_id="worker_1")

    assert failed.status.value == "failed"
    assert audits.get_audit(audit.audit_id).status.value == "failed"
    assert failed.error is not None and failed.error.code == "AUDIT_FAILED"


def test_unserializable_isolated_collector_is_a_visible_computation_failure(workflow_settings):
    def local_collector(dataset_root, sample_id, allowed_labels, connectivity):
        return measurement_report(dataset_root, sample_id, allowed_labels, connectivity)

    service, audits = make_service(
        workflow_settings, local_collector, isolate_sample_computation=True,
    )
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="root", rulebook_id="book_workflow",
        rulebook_version=1, sample_ids=["one"], allowed_labels=["person"],
    )
    audit, _ = service.start(request, audit_id="audit_unserializable", job_id="job_unserializable")

    failed = service.run_audit(audit.audit_id, worker_id="worker_1")

    assert failed.status.value == "failed"
    assert audits.get_audit(audit.audit_id).status.value == "failed"
    assert audits.get_failures(audit.audit_id)[0].reason == "audit_worker_failed"


def test_heartbeat_keeps_a_long_sample_claim_alive(workflow_settings):
    service, _ = make_service(
        workflow_settings, slow_collector, lease_seconds=1,
        isolate_sample_computation=True,
    )
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="root", rulebook_id="book_workflow",
        rulebook_version=1, sample_ids=["one"], allowed_labels=["person"],
    )
    audit, _ = service.start(request, audit_id="audit_slow", job_id="job_slow", now=NOW)

    completed = service.run_audit(audit.audit_id, worker_id="worker_1")

    assert completed.status.value == "completed"


def test_hard_sample_deadline_terminates_isolated_collection_and_is_durable(workflow_settings):
    service, audits = make_service(
        workflow_settings, hanging_collector, lease_seconds=1,
        isolate_sample_computation=True, sample_max_seconds=1,
    )
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="root", rulebook_id="book_workflow",
        rulebook_version=1, sample_ids=["one"], allowed_labels=["person"],
    )
    audit, _ = service.start(request, audit_id="audit_timeout", job_id="job_timeout")

    completed = service.run_audit(audit.audit_id, worker_id="worker_1")

    assert completed.status.value == "completed"
    failures = audits.get_failures(audit.audit_id)
    assert len(failures) == 1
    assert failures[0].reason == "processing_timeout"
    assert audits.get_audit(audit.audit_id).failed_count == 1


def test_hard_sample_deadline_interrupts_isolated_evaluation(workflow_settings):
    service, audits = make_service(
        workflow_settings,
        rule_evaluator=hanging_evaluator,
        isolate_sample_computation=True,
        sample_max_seconds=1,
    )
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="root", rulebook_id="book_workflow",
        rulebook_version=1, sample_ids=["one"], allowed_labels=["person"],
    )
    audit, _ = service.start(
        request, audit_id="audit_eval_timeout", job_id="job_eval_timeout",
    )

    completed = service.run_audit(audit.audit_id, worker_id="worker_1")

    assert completed.status.value == "completed"
    assert audits.get_failures(audit.audit_id)[0].reason == "processing_timeout"


def test_isolated_child_does_not_receive_database_environment(workflow_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://secret@example.invalid/db")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("PGPASSWORD", "secret")
    service, _ = make_service(
        workflow_settings, credential_observing_collector,
        isolate_sample_computation=True,
    )
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root=str(tmp_path), rulebook_id="book_workflow",
        rulebook_version=1, sample_ids=["one"], allowed_labels=["person"],
    )
    audit, _ = service.start(
        request, audit_id="audit_child_credentials", job_id="job_child_credentials",
    )

    completed = service.run_audit(audit.audit_id, worker_id="worker_1")

    assert completed.status.value == "completed"
    assert (tmp_path / "child_credentials.log").read_text(encoding="utf-8") == "absent"
    assert os.environ["POSTGRES_PASSWORD"] == "secret"


def test_large_isolated_result_is_drained_without_ipc_deadlock(workflow_settings):
    service, audits = make_service(
        workflow_settings, large_result_collector, isolate_sample_computation=True,
    )
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="root", rulebook_id="book_workflow",
        rulebook_version=1, sample_ids=["one"], allowed_labels=["person"],
    )
    audit, _ = service.start(request, audit_id="audit_large_result", job_id="job_large_result")

    completed = service.run_audit(audit.audit_id, worker_id="worker_1")

    assert completed.status.value == "completed"
    assert len(audits.get_measurements(audit.audit_id, "one", 1)["large_payload"]) == 200_000


def test_source_incompleteness_is_preserved_and_counted(workflow_settings):
    service, audits = make_service(workflow_settings, incomplete_measurement_collector)
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="root", rulebook_id="book_workflow",
        rulebook_version=1, sample_ids=["one"], allowed_labels=["person"],
    )
    audit, _ = service.start(request, audit_id="audit_source_incomplete", job_id="job_source_incomplete")

    completed = service.run_audit(audit.audit_id, worker_id="worker_1")

    assert completed.status.value == "completed"
    assert audits.get_measurements(audit.audit_id, "one", 1)["status"] == "incomplete"
    assert audits.get_audit(audit.audit_id).incomplete_count == 1


def test_transient_sample_failures_stop_after_three_attempts(workflow_settings, tmp_path):
    service, audits = make_service(
        workflow_settings, transient_collector, isolate_sample_computation=True,
    )
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root=str(tmp_path), rulebook_id="book_workflow",
        rulebook_version=1, sample_ids=["one"], allowed_labels=["person"],
    )
    audit, _ = service.start(request, audit_id="audit_retries", job_id="job_retries")

    completed = service.run_audit(audit.audit_id, worker_id="worker_1")

    assert completed.status.value == "completed"
    assert audits.get_failures(audit.audit_id)[0].reason == "retry_exhausted"
    assert (tmp_path / "attempts.log").read_text(encoding="utf-8").splitlines() == [
        "one", "one", "one",
    ]
    with database_connection(workflow_settings, readonly=True) as connection:
        assert connection.execute(
            "SELECT attempt_count FROM audit_samples WHERE audit_id = %s",
            (audit.audit_id,),
        ).fetchone() == {"attempt_count": 3}


def test_cancellation_during_isolated_collection_wins_at_checkpoint(workflow_settings):
    service, audits = make_service(
        workflow_settings, hanging_collector, lease_seconds=1,
        isolate_sample_computation=True, sample_max_seconds=30,
    )
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="root", rulebook_id="book_workflow",
        rulebook_version=1, sample_ids=["one"], allowed_labels=["person"],
    )
    audit, job = service.start(request, audit_id="audit_cancel_child", job_id="job_cancel_child")

    with ThreadPoolExecutor(max_workers=1) as workers:
        future = workers.submit(service.run_audit, audit.audit_id, worker_id="worker_1")
        sleep(0.6)
        requested = service.cancel_job(job.job_id)
        completed = future.result(timeout=10)

    assert requested.status.value == "cancelling"
    assert completed.status.value == "cancelled"
    assert audits.get_measurements(audit.audit_id, "one", 1) is None


def test_concurrent_claims_are_exclusive_and_stale_work_can_recover(workflow_settings):
    service, audits = make_service(workflow_settings)
    request = BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="root", rulebook_id="book_workflow",
        rulebook_version=1, sample_ids=["one"], allowed_labels=["person"],
    )
    audit, _ = service.start(request, audit_id="audit_claims", job_id="job_claims", now=NOW)

    def claim(worker_id):
        return audits.claim_audit(audit.audit_id, worker_id=worker_id, lease_seconds=1, now=NOW)

    with ThreadPoolExecutor(max_workers=2) as workers:
        claims = list(workers.map(claim, ["worker_a", "worker_b"]))

    assert sum(claim is not None for claim in claims) == 1
    sample_claim = audits.claim_next_sample(next(claim for claim in claims if claim is not None), now=NOW)
    assert sample_claim is not None

    recovered = audits.recover_stale_jobs(now=NOW + timedelta(seconds=2))
    assert [job.status.value for job in recovered] == ["queued"]
    assert audits.get_audit(audit.audit_id).status.value == "queued"
