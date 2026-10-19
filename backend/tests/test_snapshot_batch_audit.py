"""Focused regression coverage for snapshot-backed batch-audit selection."""

from datetime import UTC, datetime, timedelta
from functools import partial
import multiprocessing
import os
from pathlib import Path
import time

import pytest

from backend.app.core.config import AssetStorageSettings
from backend.app.models.asset_manifest import AssetAvailability, AssetKind, SampleAssetManifest
from backend.app.models.audit import AuditSampleClaim, AuditSourceMode, BatchAuditRequest
from backend.app.models.rule import ConfirmationState, Rule, Rulebook
from backend.app.services.batch_audit import (
    BatchAuditError,
    BatchAuditService,
    ResolvedSampleRevision,
)
from backend.app.services.evaluation import RuleEvaluationResult
from backend.app.services.sample_worker import SampleComputationError, TransientSampleError
from backend.app.storage.workflow_repository import (
    AssetIntegrityError, JobCancellationError, JobClaimLostError,
    SampleAssetManifestConflictError, SampleAssetManifestNotFoundError,
    SampleProcessingTimeoutError,
)


def _rulebook() -> Rulebook:
    return Rulebook(
        rulebook_id="book_1",
        version=1,
        guideline_text="Use person labels.",
        rules=[Rule(
            rule_id="label",
            description="Use person labels.",
            check_name="allowed_labels",
            parameters={"allowed_labels": ["person"]},
            origin="reviewer",
            confirmation_state=ConfirmationState.CONFIRMED,
        )],
        confirmation_state=ConfirmationState.CONFIRMED,
    )


def _write_sample(root: Path, *, image: bytes, mask: bytes, annotation: bytes) -> None:
    for directory in ("PNGImages", "PedMasks", "Annotation"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "PNGImages/sample.png").write_bytes(image)
    (root / "PedMasks/sample_mask.png").write_bytes(mask)
    (root / "Annotation/sample.txt").write_bytes(annotation)


class ManifestStore:
    def __init__(self, root: Path):
        self.asset_settings = AssetStorageSettings(root=root)
        self.manifests = {}

    def save_manifest(self, manifest: SampleAssetManifest) -> SampleAssetManifest:
        key = (manifest.dataset_id, manifest.sample_id, manifest.sample_revision)
        current = self.manifests.get(key)
        if current is not None and current != manifest:
            raise SampleAssetManifestConflictError(*key)
        self.manifests.setdefault(key, manifest.model_copy(deep=True))
        return self.manifests[key].model_copy(deep=True)

    def get_manifest(self, dataset_id: str, sample_id: str, sample_revision: int) -> SampleAssetManifest:
        key = (dataset_id, sample_id, sample_revision)
        if key not in self.manifests:
            raise SampleAssetManifestNotFoundError(*key)
        return self.manifests[key].model_copy(deep=True)

    def read_asset_bytes(self, dataset_id: str, sample_id: str, sample_revision: int, asset_kind: AssetKind) -> bytes:
        manifest = self.get_manifest(dataset_id, sample_id, sample_revision)
        entry = getattr(manifest, asset_kind.value)
        if entry.availability is not AssetAvailability.AVAILABLE:
            return b""
        return (self.asset_settings.root / entry.storage_key).read_bytes()


class Rulebooks:
    def get_rulebook(self, rulebook_id, *, version):
        assert (rulebook_id, version) == ("book_1", 1)
        return _rulebook()


class RecordingAudits:
    def __init__(self):
        self.calls = []
        self.checkpoints = []
        self.attempts = []
        self.saved = []

    def start_audit(self, request, **kwargs):
        self.calls.append((request, kwargs))
        return request, kwargs

    def checkpoint_sample_claim(self, sample_claim, **kwargs):
        self.checkpoints.append(sample_claim)

    def increment_sample_attempt(self, sample_claim, **kwargs):
        self.attempts.append(sample_claim.attempt_count + 1)
        return self.attempts[-1]

    def persist_sample_success(self, sample_claim, **kwargs):
        self.saved.append((sample_claim, kwargs))
        return "saved"


class FixedResolver:
    def __init__(self, selected):
        self.selected = selected
        self.calls = []

    def resolve_samples(self, *, dataset_id, sample_ids, requested_revisions):
        self.calls.append((dataset_id, tuple(sample_ids), dict(requested_revisions)))
        return self.selected


def request(*, source_mode=AuditSourceMode.SNAPSHOT, revisions=None, dataset_root="mutable-root"):
    return BatchAuditRequest(
        dataset_id="dataset_1",
        dataset_root=dataset_root,
        rulebook_id="book_1",
        rulebook_version=1,
        sample_ids=["sample"],
        sample_revisions={} if revisions is None else revisions,
        source_mode=source_mode,
    )


def claim(*, revision=1, source_mode=AuditSourceMode.SNAPSHOT, manifest_dataset_id="dataset_1"):
    now = datetime.now(UTC)
    return AuditSampleClaim(
        audit_id="audit_1",
        job_id="job_1",
        sample_id="sample",
        sample_revision=revision,
        source_mode=source_mode,
        manifest_dataset_id=manifest_dataset_id,
        worker_id="worker",
        claim_token="token",
        lease_until=now + timedelta(minutes=5),
        processing_deadline=now + timedelta(minutes=10),
        attempt_count=1,
    )


def reading_collector(root, sample_id, allowed_labels, connectivity):
    return {
        "image": (Path(root) / "PNGImages" / f"{sample_id}.png").read_bytes(),
        "mask": (Path(root) / "PedMasks" / f"{sample_id}_mask.png").read_bytes()
        if (Path(root) / "PedMasks" / f"{sample_id}_mask.png").exists() else None,
        "annotation": (Path(root) / "Annotation" / f"{sample_id}.txt").read_bytes()
        if (Path(root) / "Annotation" / f"{sample_id}.txt").exists() else None,
    }


def empty_evaluation(*args, **kwargs):
    return RuleEvaluationResult(findings=[], internal_check_failures=0)


def retrying_collector(marker, source, root, sample_id, allowed_labels, connectivity):
    report = reading_collector(root, sample_id, allowed_labels, connectivity)
    with Path(marker).open("a") as log:
        log.write(f"{os.getpid()}:{report['image'].decode()}\n")
    if len(Path(marker).read_text().splitlines()) < 3:
        (Path(source) / "PNGImages/sample.png").write_bytes(b"changed-source")
        raise TransientSampleError("synthetic transient collection error")
    return report


def snapshot_child_without_database(*args, **kwargs):
    import psycopg
    from backend.app.services.sample_worker import run_sample_child

    def forbidden_connect(*args, **kwargs):
        raise AssertionError("The snapshot child must not open PostgreSQL")

    psycopg.connect = forbidden_connect
    run_sample_child(*args, **kwargs)


def blocked_snapshot_child(*args, **kwargs):
    from backend.app.services import sample_worker

    def blocked_materialization(manifest, *, asset_settings, target_root):
        (asset_settings.root / "preparation_started").write_text(f"{os.getpid()}\n{target_root}")
        time.sleep(20)
        raise AssertionError("Snapshot preparation should have been terminated")

    sample_worker.materialize_sample = blocked_materialization
    sample_worker.run_sample_child(*args, **kwargs)


def make_service(tmp_path, resolver, audits=None, *, collector=reading_collector):
    return BatchAuditService(
        rulebook_repository=Rulebooks(),
        audit_repository=RecordingAudits() if audits is None else audits,
        measurement_collector=collector,
        rule_evaluator=empty_evaluation,
        sample_revision_resolver=resolver,
        manifest_repository=ManifestStore(tmp_path / "managed"),
        isolate_sample_computation=False,
        sample_max_seconds=900,
    )


def test_snapshot_start_resolves_revision_captures_once_and_ignores_later_source_changes(tmp_path):
    source = tmp_path / "source"
    _write_sample(source, image=b"image-v1", mask=b"mask-v1", annotation=b"annotation-v1")
    resolver = FixedResolver({"sample": ResolvedSampleRevision(1, source)})
    audits = RecordingAudits()
    service = make_service(tmp_path, resolver, audits)

    service.start(request(), audit_id="audit_1", job_id="job_1")
    persisted_request, kwargs = audits.calls[0]
    assert persisted_request.sample_revisions == {"sample": 1}
    assert persisted_request.source_mode is AuditSourceMode.SNAPSHOT
    assert set(kwargs["sample_manifests"]) == {"sample"}
    assert resolver.calls == [("dataset_1", ("sample",), {})]

    source.joinpath("PNGImages/sample.png").write_bytes(b"image-v2")
    source.joinpath("PedMasks/sample_mask.png").unlink()
    source.joinpath("Annotation/sample.txt").write_bytes(b"annotation-v2")
    assert service._collect_sample(persisted_request, claim()) == {
        "image": b"image-v1", "mask": b"mask-v1", "annotation": b"annotation-v1",
    }


def test_snapshot_reuses_existing_manifest_for_a_new_audit(tmp_path):
    source = tmp_path / "source"
    _write_sample(source, image=b"image-v1", mask=b"mask-v1", annotation=b"annotation-v1")
    resolver = FixedResolver({"sample": ResolvedSampleRevision(1, source)})
    audits = RecordingAudits()
    service = make_service(tmp_path, resolver, audits)
    service.start(request(), audit_id="audit_1", job_id="job_1")
    source.joinpath("PNGImages/sample.png").write_bytes(b"changed-source")

    service.start(request(), audit_id="audit_2", job_id="job_2")
    assert len(audits.calls) == 2
    assert len(service.manifest_repository.manifests) == 1
    persisted_request = audits.calls[1][0]
    assert service._collect_sample(persisted_request, claim()) == {
        "image": b"image-v1", "mask": b"mask-v1", "annotation": b"annotation-v1",
    }


def test_snapshot_pinned_revision_mismatch_is_rejected_before_audit_creation(tmp_path):
    source = tmp_path / "source"
    _write_sample(source, image=b"image", mask=b"mask", annotation=b"annotation")
    resolver = FixedResolver({"sample": ResolvedSampleRevision(2, source)})
    audits = RecordingAudits()
    service = make_service(tmp_path, resolver, audits)

    with pytest.raises(BatchAuditError):
        service.start(request(revisions={"sample": 1}), audit_id="audit_1", job_id="job_1")
    assert audits.calls == []


def test_snapshot_bytes_are_verified_and_collected_in_a_real_child(tmp_path, monkeypatch):
    source = tmp_path / "source"
    _write_sample(source, image=b"image", mask=b"mask", annotation=b"annotation")
    resolver = FixedResolver({"sample": ResolvedSampleRevision(1, source)})
    service = make_service(tmp_path, resolver)
    service.start(request(), audit_id="audit_1", job_id="job_1")
    persisted_request = request(revisions={"sample": 1})
    source.joinpath("PNGImages/sample.png").write_bytes(b"changed-source")
    monkeypatch.setattr(
        "backend.app.services.batch_audit.run_sample_child", snapshot_child_without_database,
    )
    measurements, _ = service._compute_isolated(persisted_request, _rulebook(), claim())
    assert measurements == {"image": b"image", "mask": b"mask", "annotation": b"annotation"}


def test_actual_spawned_retries_keep_the_original_snapshot(tmp_path, monkeypatch):
    source = tmp_path / "source"
    marker = tmp_path / "attempts"
    _write_sample(source, image=b"image-v1", mask=b"mask-v1", annotation=b"annotation-v1")
    service = make_service(
        tmp_path, FixedResolver({"sample": ResolvedSampleRevision(1, source)}),
        collector=partial(retrying_collector, marker, source),
    )
    service.isolate_sample_computation = True
    monkeypatch.setattr(service, "_retry_delay", lambda _: 0)
    service.start(request())
    result = service._process_sample(request(revisions={"sample": 1}), _rulebook(), claim())
    attempts = [line.split(":") for line in marker.read_text().splitlines()]
    assert result == "saved"
    assert [image for _, image in attempts] == ["image-v1"] * 3
    assert all(int(pid) != os.getpid() for pid, _ in attempts)
    assert service.audit_repository.attempts == [2, 3]
    saved_claim, saved = service.audit_repository.saved[0]
    assert saved_claim.attempt_count == 3
    assert saved_claim.source_mode is AuditSourceMode.SNAPSHOT
    assert saved["measurements"]["image"] == b"image-v1"


@pytest.mark.parametrize("stop", ["timeout", "cancellation", "lease_loss"])
def test_blocked_snapshot_preparation_is_supervised_despite_cleanup_failure(
    tmp_path, monkeypatch, caplog, stop,
):
    source = tmp_path / "source"
    _write_sample(source, image=b"image", mask=b"mask", annotation=b"annotation")
    service = make_service(tmp_path, FixedResolver({"sample": ResolvedSampleRevision(1, source)}))
    service.start(request())
    service.lease_seconds = 1
    from backend.app.services import asset_snapshot

    original_temporary_directory = asset_snapshot.TemporaryDirectory

    class FailingCleanupDirectory:
        def __init__(self, **kwargs):
            self.temporary = original_temporary_directory(**kwargs)
            self.name = self.temporary.name

        def cleanup(self):
            self.temporary.cleanup()
            raise OSError("synthetic cleanup failure")

    monkeypatch.setattr(asset_snapshot, "TemporaryDirectory", FailingCleanupDirectory)
    marker = service.manifest_repository.asset_settings.root / "preparation_started"
    checkpoints_during_preparation = []

    def checkpoint(sample_claim, **kwargs):
        if marker.exists():
            checkpoints_during_preparation.append(sample_claim)
            if stop == "cancellation":
                raise JobCancellationError("cancelled while preparing snapshot")
            if stop == "lease_loss":
                raise JobClaimLostError("claim replaced while preparing snapshot")

    monkeypatch.setattr(service.audit_repository, "checkpoint_sample_claim", checkpoint)
    monkeypatch.setattr("backend.app.services.batch_audit.run_sample_child", blocked_snapshot_child)
    active_claim = claim().model_copy(update={
        "processing_deadline": datetime.now(UTC) + timedelta(seconds=4),
    })
    expected = {
        "timeout": SampleProcessingTimeoutError,
        "cancellation": JobCancellationError,
        "lease_loss": JobClaimLostError,
    }[stop]
    started = time.monotonic()
    with pytest.raises(expected):
        service._compute_isolated(request(revisions={"sample": 1}), _rulebook(), active_claim)
    assert time.monotonic() - started < 12
    child_pid, working_root = marker.read_text().splitlines()
    assert int(child_pid) not in {child.pid for child in multiprocessing.active_children()}
    assert not Path(working_root).exists()
    assert checkpoints_during_preparation
    assert not service.audit_repository.saved
    assert "Sample processing storage cleanup failed" in caplog.text


@pytest.mark.parametrize("isolated", [False, True])
def test_tampered_managed_asset_is_a_worker_storage_failure(tmp_path, isolated):
    source = tmp_path / "source"
    _write_sample(source, image=b"image", mask=b"mask", annotation=b"annotation")
    service = make_service(tmp_path, FixedResolver({"sample": ResolvedSampleRevision(1, source)}))
    service.start(request())
    manifest = service.manifest_repository.get_manifest("dataset_1", "sample", 1)
    (service.manifest_repository.asset_settings.root / manifest.original_image.storage_key).write_bytes(b"tampered")
    if isolated:
        with pytest.raises(SampleComputationError):
            service._compute_isolated(request(revisions={"sample": 1}), _rulebook(), claim())
    else:
        with pytest.raises(AssetIntegrityError):
            service._collect_sample(request(revisions={"sample": 1}), claim())


def test_legacy_mode_keeps_the_existing_root_and_does_not_need_manifests(tmp_path):
    audits = RecordingAudits()
    service = BatchAuditService(
        rulebook_repository=Rulebooks(),
        audit_repository=audits,
        measurement_collector=reading_collector,
        isolate_sample_computation=False,
        sample_max_seconds=900,
    )
    legacy = request(source_mode=AuditSourceMode.LEGACY, dataset_root="legacy-root")
    service.start(legacy, audit_id="audit_legacy", job_id="job_legacy")
    assert audits.calls[0][1] == {
        "audit_id": "audit_legacy", "job_id": "job_legacy", "now": None,
    }

    received = []

    def collector(root, sample_id, allowed_labels, connectivity):
        received.append(root)
        return {"image_id": sample_id}

    service.measurement_collector = collector
    service._collect_sample(legacy, claim(source_mode=AuditSourceMode.LEGACY, manifest_dataset_id=None))
    assert received == ["legacy-root"]
