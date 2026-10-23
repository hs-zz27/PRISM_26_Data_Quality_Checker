"""Opt-in PostgreSQL regressions; model responses are synthetic and no inference runs."""

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import AssetStorageSettings
from backend.app.main import create_app
from backend.app.models.audit_submission import AuditRequest
from backend.app.models.correction import RecheckRequest
from backend.app.models.job import JobKind
from backend.app.routers.audits import get_audit_read_repository
from backend.app.services.audit_submission import AuditSubmissionService
from backend.app.services.batch_audit import BatchAuditService
from backend.app.storage.audit_read_repository import AuditReadRepository
from backend.app.storage.recheck_repository import RecheckRepository
from backend.app.storage.repository import RulebookRepository
from backend.app.storage.workflow_repository import AuditRepository, SampleAssetManifestRepository
from backend.tests.correction_fixtures import fixture
from backend.tests.dataset_fixtures import (
    dataset_database_settings,
    empty_dataset_database_settings,
)
from backend.tests.test_audit_submission import rulebook
from backend.tests.test_correction_repository import request as correction_request
from backend.tests.test_enclosure_integration import ENABLED, FakeOllama, rule


def setup_audit(settings, tmp_path, monkeypatch, *, enabled):
    for key, value in ENABLED.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ENCLOSURE_ENABLED", "true" if enabled else "false")
    monkeypatch.setenv("AUDIT_SAMPLE_MAX_SECONDS", "900")
    catalogue, corrections, _ = fixture(settings, tmp_path)
    books = RulebookRepository(settings=settings)
    books.save_rulebook(rulebook(rules=[rule()]))
    audits = AuditRepository(settings=settings)
    manifests = SampleAssetManifestRepository(settings=settings,
        asset_settings=AssetStorageSettings(root=corrections.settings.asset_root))
    worker = BatchAuditService(rulebook_repository=books, audit_repository=audits,
        manifest_repository=manifests, sample_max_seconds=900, isolate_sample_computation=False)
    accepted = AuditSubmissionService(catalogue=catalogue, batch=worker).start(
        AuditRequest(dataset_id="dataset_a", rulebook_id="book", rulebook_version=1))
    return accepted, audits, worker, corrections


@pytest.mark.parametrize("enabled", [False, True])
def test_recheck_preserves_parent_pin_and_disabled_choice(dataset_database_settings, tmp_path, monkeypatch, enabled):
    accepted, audits, _, corrections = setup_audit(dataset_database_settings, tmp_path, monkeypatch, enabled=enabled)
    original = audits.get_request(accepted.audit.audit_id)
    assert (original.enclosure_runtime is not None) is enabled
    audits.cancel_job(accepted.job.job_id)
    corrections.correct("dataset_a", "sample_a", correction_request())
    # Opposite configuration must not alter a recheck's execution choice.
    monkeypatch.setenv("ENCLOSURE_ENABLED", "false" if enabled else "true")
    monkeypatch.setenv("ENCLOSURE_MODEL_DIGEST", "sha256:" + "11" * 32)
    linked = RecheckRepository(settings=dataset_database_settings).start(accepted.audit.audit_id,
        RecheckRequest(sample_ids=["sample_a"], sample_revisions={"sample_a": 2}, reviewer_id="human"))
    child = audits.get_request(linked.audit.audit_id)
    assert child.enclosure_runtime == original.enclosure_runtime
    assert child.sample_revisions == {"sample_a": 2}
    assert audits.get_request(accepted.audit.audit_id).sample_revisions == {"sample_a": 1}


def test_model_proposal_is_stored_and_visible_in_sample_and_findings_api(dataset_database_settings, tmp_path, monkeypatch):
    from backend.app.inference.enclosure_client import EnclosureClient
    from backend.app.services import enclosure
    accepted, audits, worker, _ = setup_audit(dataset_database_settings, tmp_path, monkeypatch, enabled=True)
    fake = FakeOllama(decide=lambda _: ("fail", "The framed target contains background only."))
    monkeypatch.setattr(enclosure, "EnclosureClient", lambda pin, **kwargs:
        EnclosureClient(pin, clock=kwargs["clock"], transport=fake.transport()))
    claim = audits.jobs.claim_next_job(worker_id="synthetic", kind=JobKind.AUDIT, lease_seconds=300)
    assert worker.run_claimed_job(claim).status.value == "completed"
    reads = AuditReadRepository(settings=dataset_database_settings)
    saved = reads.get_sample(accepted.audit.audit_id, "sample_a")
    assert len(saved.findings) == 1
    finding = saved.findings[0]
    assert finding.outcome.value == "fail" and finding.review.resolution.value == "pending"
    evidence = finding.evidence.measurements["enclosure"]
    assert evidence["boxes_intended"] == 1 and evidence["boxes"][0]["model_verified"]
    assert evidence["execution"] == audits.get_request(accepted.audit.audit_id).enclosure_runtime.execution.model_dump(mode="json")
    app = create_app()
    app.dependency_overrides[get_audit_read_repository] = lambda: reads
    with TestClient(app) as client:
        sample = client.get(f"/api/v1/audits/{accepted.audit.audit_id}/samples/sample_a")
        listing = client.get(f"/api/v1/audits/{accepted.audit.audit_id}/findings")
    assert sample.status_code == listing.status_code == 200
    assert sample.json()["findings"][0]["evidence"]["measurements"]["enclosure"] == evidence
    assert listing.json()["items"][0]["finding_id"] == finding.finding_id
