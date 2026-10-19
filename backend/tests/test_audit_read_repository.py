"""PostgreSQL audit reads across processing outcomes, review changes and pins."""

import os
from copy import deepcopy
from io import BytesIO
from urllib.parse import quote
from uuid import uuid4

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
from pydantic import SecretStr
import pytest

from backend.app.core.config import AssetStorageSettings, DatabaseSettings
from backend.app.main import create_app
from backend.app.models.asset_manifest import AssetManifestEntry, SampleAssetManifest
from backend.app.models.audit import AuditFailure, AuditSourceMode, BatchAuditRequest
from backend.app.models.finding import Finding
from backend.app.models.review import FindingReviewRequest, SampleReviewRequest
from backend.app.routers.audits import get_audit_read_repository
from backend.app.routers.assets import get_sample_asset_service
from backend.app.routers.finding_reviews import get_finding_repository
from backend.app.routers.sample_reviews import get_sample_review_repository
from backend.app.services.review import review_finding, review_sample
from backend.app.services.asset_retrieval import SampleAssetService
from backend.app.services.asset_snapshot import capture_sample
from backend.app.storage import AuditRepository, FindingRepository, SampleReviewRepository
from backend.app.storage.audit_read_repository import AuditReadRepository
from backend.app.storage.database import database_connection, initialize_database
from backend.app.storage.workflow_repository import (
    AuditNotFoundError,
    AuditSampleNotFoundError,
    SampleAssetManifestRepository,
)


@pytest.fixture
def settings():
    conninfo = os.environ.get("PRISM_TEST_DATABASE_URL")
    if not conninfo:
        pytest.skip("Set PRISM_TEST_DATABASE_URL to run PostgreSQL integration tests.")
    schema = f"prism_audit_read_{uuid4().hex}"
    with psycopg.connect(conninfo, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        settings = DatabaseSettings(conninfo=SecretStr(make_conninfo(conninfo, options=f"-c search_path={schema}")))
        initialize_database(settings)
        yield settings
    finally:
        with psycopg.connect(conninfo, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


SAMPLE_IDS = [
    "passing", "flagged", "uncertain", "unsupported", "no_findings", "incomplete",
    "mixed", "failed", "running", "queued", "cancelled",
]


def _asset_entry(availability: str, storage_key: str) -> AssetManifestEntry:
    if availability != "available":
        return AssetManifestEntry(availability=availability)
    return AssetManifestEntry(
        availability=availability, storage_key=storage_key, content_type="image/png",
        sha256="0" * 64, size_bytes=1,
    )


def _manifest(
    dataset_id: str, sample_id: str, revision: int, *,
    image: str = "available", mask: str = "available",
) -> SampleAssetManifest:
    prefix = f"{dataset_id}/{sample_id}/{revision}"
    return SampleAssetManifest(
        dataset_id=dataset_id, sample_id=sample_id, sample_revision=revision,
        original_image=_asset_entry(image, f"{prefix}/image.png"),
        raw_mask=_asset_entry(mask, f"{prefix}/mask.png"),
        annotation_text=AssetManifestEntry(availability="missing"),
    )


def _start_snapshot_audit(
    audits: AuditRepository, *, audit_id: str, job_id: str, dataset_id: str,
    manifests: list[SampleAssetManifest],
) -> None:
    audits.start_audit(
        BatchAuditRequest(
            dataset_id=dataset_id, dataset_root="unused-root", rulebook_id="book_1",
            rulebook_version=1, source_mode=AuditSourceMode.SNAPSHOT,
            sample_ids=[manifest.sample_id for manifest in manifests],
            sample_revisions={manifest.sample_id: manifest.sample_revision for manifest in manifests},
        ),
        sample_manifests={manifest.sample_id: manifest for manifest in manifests},
        audit_id=audit_id, job_id=job_id,
    )


@pytest.fixture
def populated(settings):
    audits = AuditRepository(settings=settings)
    audits.start_audit(BatchAuditRequest(
        dataset_id="dataset_1", dataset_root="unused-root", rulebook_id="book_1", rulebook_version=1,
        sample_ids=SAMPLE_IDS, sample_revisions={name: 2 for name in SAMPLE_IDS},
    ), audit_id="audit_1", job_id="job_1")
    job = audits.claim_audit("audit_1", worker_id="worker_1")
    outcomes = [["pass"], ["fail"], ["unsure"], ["unavailable"], [], ["pass"], ["fail", "unavailable"]]
    for sample_id, sample_outcomes in zip(SAMPLE_IDS, outcomes):
        claim = audits.claim_next_sample(job)
        assert claim.sample_id == sample_id
        findings = [Finding(
            finding_id=f"{sample_id}_{index}", audit_id="audit_1", sample_id=sample_id, sample_revision=2,
            rulebook_id="book_1", rulebook_version=1, rule_id=f"rule_{index}", description="Check labels.",
            check_name="allowed_labels", outcome=outcome, origin="reviewer",
            evidence={"measurements": {"label": "person"}},
        ) for index, outcome in enumerate(sample_outcomes)]
        audits.persist_sample_success(claim, measurements={
            "image_id": sample_id, "review_status": "pending_review",
            "status": "incomplete" if sample_id == "incomplete" else "completed",
        }, findings=findings)
    failed = audits.claim_next_sample(job)
    audits.persist_sample_failure(failed, failure=AuditFailure(
        failure_id="failure_1", audit_id="audit_1", sample_id="failed", sample_revision=2,
        stage="load", reason="missing_input", detail="Input could not be loaded.",
        created_at=job.job.created_at,
    ))
    assert audits.claim_next_sample(job).sample_id == "running"
    with database_connection(settings) as connection:
        connection.execute("UPDATE audit_samples SET status = 'cancelled' WHERE sample_id = 'cancelled'")
    return AuditReadRepository(settings=settings)


@pytest.fixture
def snapshot_populated(settings, tmp_path):
    manifest_repository = SampleAssetManifestRepository(
        settings=settings, asset_settings=AssetStorageSettings(root=tmp_path / "assets"),
    )
    manifests = [
        _manifest("dataset_snapshot_1", "sample_all", 2),
        _manifest("dataset_snapshot_1", "image_missing", 3, image="missing"),
        _manifest("dataset_snapshot_1", "mask_unreadable", 4, mask="unreadable"),
        _manifest("dataset_snapshot_1", "sample special%?#", 5),
        _manifest("dataset_snapshot_1", "shared", 2),
        _manifest("dataset_snapshot_1", "shared", 9),
        _manifest("dataset_snapshot_2", "shared", 2),
        _manifest("dataset special%?#", "sample special%?#", 5),
    ]
    for manifest in manifests:
        manifest_repository.save_manifest(manifest)

    audits = AuditRepository(settings=settings)
    _start_snapshot_audit(
        audits, audit_id="audit_snapshot_1", job_id="job_snapshot_1",
        dataset_id="dataset_snapshot_1", manifests=manifests[:5],
    )
    _start_snapshot_audit(
        audits, audit_id="audit_snapshot_2", job_id="job_snapshot_2",
        dataset_id="dataset_snapshot_2", manifests=[manifests[6]],
    )
    _start_snapshot_audit(
        audits, audit_id="audit_snapshot_revision", job_id="job_snapshot_revision",
        dataset_id="dataset_snapshot_1", manifests=[manifests[5]],
    )
    _start_snapshot_audit(
        audits, audit_id="audit_snapshot_encoded", job_id="job_snapshot_encoded",
        dataset_id="dataset special%?#", manifests=[manifests[7]],
    )
    return AuditReadRepository(settings=settings)


@pytest.fixture
def captured_asset_api(settings, tmp_path):
    repository = SampleAssetManifestRepository(
        settings=settings, asset_settings=AssetStorageSettings(root=tmp_path / "assets"),
    )
    source = tmp_path / "source"
    for folder in ("PNGImages", "PedMasks", "Annotation"):
        (source / folder).mkdir(parents=True)
    dataset_id, sample_id = "dataset spécial%?#", "sample spécial%?#"
    image_path = source / "PNGImages" / f"{sample_id}.png"
    mask_path = source / "PedMasks" / f"{sample_id}_mask.png"
    Image.new("RGB", (6, 4), "red").save(image_path)
    mask = np.array([[0, 1, 257, 65535, 257, 1]] * 4, dtype=np.uint16)
    Image.fromarray(mask).save(mask_path)
    originals = {"image": image_path.read_bytes(), "mask": mask_path.read_bytes()}
    manifest = capture_sample(
        dataset_id=dataset_id, sample_id=sample_id, sample_revision=3,
        source_root=source, repository=repository,
    )
    _start_snapshot_audit(
        AuditRepository(settings=settings), audit_id="audit_captured", job_id="job_captured",
        dataset_id=dataset_id, manifests=[manifest],
    )

    # A newer capture and another dataset must not change the audit's old pin.
    Image.new("RGB", (6, 4), "blue").save(image_path)
    Image.new("L", (6, 4), 0).save(mask_path)
    capture_sample(
        dataset_id=dataset_id, sample_id=sample_id, sample_revision=4,
        source_root=source, repository=repository,
    )
    capture_sample(
        dataset_id="other_dataset", sample_id=sample_id, sample_revision=3,
        source_root=source, repository=repository,
    )
    image_path.unlink()
    mask_path.unlink()

    app = create_app()
    reads = AuditReadRepository(settings=settings)
    service = SampleAssetService(repository)
    app.dependency_overrides[get_audit_read_repository] = lambda: reads
    app.dependency_overrides[get_sample_asset_service] = lambda: service
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, repository, manifest, originals


def _captured_detail(client):
    page = client.get("/api/v1/audits/audit_captured/samples")
    assert page.status_code == 200
    sample_id = page.json()["items"][0]["sample_id"]
    url = f"/api/v1/audits/audit_captured/samples/{quote(sample_id, safe='')}"
    response = client.get(url)
    assert response.status_code == 200
    return url, response.json(), page.json()


def test_http_audit_links_deliver_managed_bytes_for_the_exact_pin(captured_asset_api):
    client, repository, manifest, originals = captured_asset_api
    detail_url, detail, page = _captured_detail(client)
    assert page["items"][0]["thumbnail_url"] == detail["assets"]["thumbnail"]["url"]
    responses = {}
    for kind, reference in detail["assets"].items():
        assert reference["revision"] == 3
        assert reference["url"].endswith("?revision=3")
        response = client.get(reference["url"])
        assert response.status_code == 200
        assert response.headers["content-type"] == reference["content_type"] == "image/png"
        assert response.headers["x-content-type-options"] == "nosniff"
        responses[kind] = response.content

    assert responses["image"] == originals["image"]
    assert responses["mask"] == originals["mask"]
    with Image.open(BytesIO(responses["mask"])) as raw:
        assert np.asarray(raw)[0].tolist() == [0, 1, 257, 65535, 257, 1]
    with Image.open(BytesIO(responses["mask_preview"])) as preview:
        pixels = np.asarray(preview)
        assert preview.size == (6, 4)
        assert pixels[0, 0, 3] == 0
        assert pixels[0, 1:, 3].tolist() == [255] * 5
        assert len({tuple(pixel) for pixel in pixels[0, 1:]}) == 3
    with Image.open(BytesIO(responses["thumbnail"])) as thumbnail:
        assert thumbnail.size == (6, 4)
        assert thumbnail.getpixel((0, 0)) == (255, 0, 0, 255)

    absent_revision = detail["assets"]["image"]["url"].replace("revision=3", "revision=999")
    absent = client.get(absent_revision)
    assert absent.status_code == 404
    assert absent.json()["error"]["code"] == "NOT_FOUND"
    assert client.get(detail_url).json() == detail
    assert client.get("/api/v1/audits/audit_captured/samples").json() == page
    for public_kind, source_kind in (("image", "original_image"), ("mask", "raw_mask")):
        stored = repository.asset_settings.root / getattr(manifest, source_kind).storage_key
        assert stored.read_bytes() == originals[public_kind]


@pytest.mark.parametrize("kind", ["image", "mask", "thumbnail", "mask_preview"])
def test_http_asset_integrity_failure_never_serves_replaced_bytes(captured_asset_api, kind):
    client, repository, manifest, _ = captured_asset_api
    _, detail, _ = _captured_detail(client)
    source_kind = "original_image" if kind in ("image", "thumbnail") else "raw_mask"
    entry = getattr(manifest, source_kind)
    stored = repository.asset_settings.root / entry.storage_key
    stored.write_bytes(b"changed after capture")

    response = client.get(detail["assets"][kind]["url"])

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert response.json()["error"]["request_id"]
    assert str(stored) not in response.text
    assert entry.sha256 not in response.text


def test_http_read_limit_is_consistent_for_raw_and_derived_assets(captured_asset_api, monkeypatch):
    client, _, _, _ = captured_asset_api
    _, detail, _ = _captured_detail(client)
    monkeypatch.setattr("backend.app.services.asset_retrieval.MAX_PREVIEW_SOURCE_BYTES", 1)
    monkeypatch.setattr("backend.app.services.asset_preview.MAX_PREVIEW_SOURCE_BYTES", 1)

    for reference in detail["assets"].values():
        response = client.get(reference["url"])
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "ASSET_TOO_LARGE"
        assert response.json()["error"]["request_id"]


def decide(settings, sample_id, decision, version=0):
    return review_sample("audit_1", sample_id, SampleReviewRequest(
        expected_sample_revision=2, expected_review_version=version,
        decision=decision, reviewer_id="reviewer_1", comment="Inspected evidence.",
    ), repository=SampleReviewRepository(settings=settings))


def test_queue_includes_every_sample_and_distinguishes_machine_states(populated):
    page = populated.list_samples("audit_1")
    assert [item.sample_id for item in page.items] == SAMPLE_IDS
    assert [item.tool_status for item in page.items] == [
        "no_issue", "flagged", "unsure", "unavailable", "unavailable", "unavailable",
        "flagged", "unavailable", "pending", "pending", "unavailable",
    ]
    assert page.total == len(SAMPLE_IDS) and page.next_offset is None
    assert all(item.review_status == "pending_review" for item in page.items)
    assert all(item.thumbnail_url is None for item in page.items)
    assert page.items[5].incomplete


def test_snapshot_queue_and_detail_links_match_the_pinned_manifest(snapshot_populated):
    page = snapshot_populated.list_samples("audit_snapshot_1")
    queue = {item.sample_id: item for item in page.items}
    detail = snapshot_populated.get_sample("audit_snapshot_1", "sample_all")
    base = "/api/v1/datasets/dataset_snapshot_1/samples/sample_all/assets"

    assert page.total == 5
    assert queue["sample_all"].thumbnail_url == detail.summary.thumbnail_url
    assert detail.summary.thumbnail_url == f"{base}/thumbnail?revision=2"
    assert detail.assets.model_dump(mode="json") == {
        kind: {
            "url": f"{base}/{kind}?revision=2",
            "content_type": "image/png",
            "revision": 2,
        }
        for kind in ("image", "mask", "mask_preview", "thumbnail")
    }


def test_audit_reads_use_manifest_metadata_without_opening_asset_files(snapshot_populated, monkeypatch):
    def fail_if_asset_read(*args, **kwargs):
        raise AssertionError("audit reads must not open managed asset files")

    monkeypatch.setattr(SampleAssetManifestRepository, "read_asset_bytes", fail_if_asset_read)
    monkeypatch.setattr(SampleAssetManifestRepository, "resolve_asset_path", fail_if_asset_read)

    page = snapshot_populated.list_samples("audit_snapshot_1")
    detail = snapshot_populated.get_sample("audit_snapshot_1", "sample_all")

    assert page.items[0].thumbnail_url is not None
    assert detail.assets.thumbnail is not None


def test_partial_or_unreadable_snapshot_assets_advertise_only_captured_roles(snapshot_populated):
    image_missing = snapshot_populated.get_sample("audit_snapshot_1", "image_missing")
    assert image_missing.assets.image is None
    assert image_missing.assets.thumbnail is None
    assert image_missing.assets.mask is not None
    assert image_missing.assets.mask_preview is not None
    assert image_missing.summary.thumbnail_url is None

    mask_unreadable = snapshot_populated.get_sample("audit_snapshot_1", "mask_unreadable")
    assert mask_unreadable.assets.image is not None
    assert mask_unreadable.assets.thumbnail is not None
    assert mask_unreadable.assets.mask is None
    assert mask_unreadable.assets.mask_preview is None


def test_asset_links_encode_identifiers_and_preserve_exact_dataset_and_revision(snapshot_populated):
    special = snapshot_populated.get_sample("audit_snapshot_encoded", "sample special%?#")
    assert special.assets.thumbnail is not None
    assert special.assets.thumbnail.url == (
        "/api/v1/datasets/dataset%20special%25%3F%23/samples/"
        "sample%20special%25%3F%23/assets/thumbnail?revision=5"
    )

    first = snapshot_populated.get_sample("audit_snapshot_1", "shared")
    second = snapshot_populated.get_sample("audit_snapshot_2", "shared")
    revision = snapshot_populated.get_sample("audit_snapshot_revision", "shared")
    assert first.assets.image is not None
    assert second.assets.image is not None
    assert revision.assets.image is not None
    assert first.assets.image.url == (
        "/api/v1/datasets/dataset_snapshot_1/samples/shared/assets/image?revision=2"
    )
    assert second.assets.image.url == (
        "/api/v1/datasets/dataset_snapshot_2/samples/shared/assets/image?revision=2"
    )
    assert revision.assets.image.url == (
        "/api/v1/datasets/dataset_snapshot_1/samples/shared/assets/image?revision=9"
    )
    assert first.assets.image.revision == second.assets.image.revision == 2
    assert revision.assets.image.revision == 9


def test_snapshot_asset_join_preserves_queue_pagination(snapshot_populated):
    first = snapshot_populated.list_samples("audit_snapshot_1", limit=2)
    second = snapshot_populated.list_samples(
        "audit_snapshot_1", limit=2, offset=first.next_offset,
    )
    third = snapshot_populated.list_samples(
        "audit_snapshot_1", limit=2, offset=second.next_offset,
    )
    assert [item.sample_id for item in first.items + second.items] == [
        "sample_all", "image_missing", "mask_unreadable", "sample special%?#",
    ]
    assert [item.sample_id for item in third.items] == ["shared"]
    assert first.total == second.total == third.total == 5
    assert first.next_offset == 2 and second.next_offset == 4 and third.next_offset is None


def test_pagination_and_filters_apply_before_counting_and_paging(populated):
    first = populated.list_samples("audit_1", limit=2)
    second = populated.list_samples("audit_1", limit=2, offset=first.next_offset)
    assert [item.sample_id for item in first.items + second.items] == SAMPLE_IDS[:4]
    assert first.total == second.total == len(SAMPLE_IDS)
    flagged = populated.list_samples("audit_1", tool_status="flagged", limit=1)
    assert flagged.total == 2 and flagged.next_offset == 1
    assert flagged.items[0].sample_id == "flagged"
    decide(populated.settings, "flagged", "accepted")
    decide(populated.settings, "flagged", "rejected", version=1)
    assert populated.list_samples("audit_1", review_status="accepted").total == 0
    filtered = populated.list_samples("audit_1", tool_status="flagged", review_status="rejected")
    assert filtered.total == 1 and filtered.items[0].sample_id == "flagged"
    empty = populated.list_samples("audit_1", offset=100)
    assert empty.items == [] and empty.total == len(SAMPLE_IDS) and empty.next_offset is None


def test_detail_composes_current_reviews_without_rewriting_original_evidence(populated):
    before = populated.get_sample("audit_1", "flagged")
    decide(populated.settings, "flagged", "accepted")
    review_finding("flagged_0", FindingReviewRequest(
        expected_review_version=0, resolution="rejected", reviewer_id="reviewer_1", comment="Not a violation.",
    ), repository=FindingRepository(settings=populated.settings))
    after = populated.get_sample("audit_1", "flagged")
    assert after.measurements == before.measurements
    assert after.summary.tool_status == "flagged" and after.summary.finding_counts.fail == 1
    assert after.review.revision == 1 and after.review.decision == "accepted"
    assert after.summary.review_status == after.review.decision
    assert after.findings[0].review.resolution == "rejected"
    assert after.findings[0].outcome == before.findings[0].outcome
    assert after.findings[0].evidence == before.findings[0].evidence
    assert after.summary.sample_revision == after.review.sample_revision == 2
    with database_connection(populated.settings, readonly=True) as connection:
        raw = connection.execute("SELECT document FROM findings WHERE finding_id = 'flagged_0'").fetchone()
        assert raw["document"]["review"]["resolution"] == "pending"


def test_http_hides_legacy_load_paths_without_rewriting_stored_measurements(populated):
    audits = AuditRepository(settings=populated.settings)
    original = audits.get_measurements("audit_1", "incomplete", 2)
    assert original is not None
    original["annotation_text"] = "Keep original annotation text for review."
    original["errors"] = [
        {
            "stage": "load", "reason": f"{asset}_load_failed",
            "detail": f"[Errno 2] No such file: '/private/managed/snapshot/{asset}'",
        }
        for asset in ("image", "mask", "annotation")
    ] + [{
        "stage": "parse", "reason": "annotation_parse_failed",
        "detail": "Malformed bounding box on line 4.",
    }]
    original["checks"] = {"mask_dimensions": {
        "status": "unavailable", "reason": "image_load_failed", "detail": None, "result": None,
    }}
    with database_connection(populated.settings) as connection:
        connection.execute(
            """UPDATE audit_measurements SET document = %s
               WHERE audit_id = %s AND sample_id = %s AND sample_revision = %s""",
            (Jsonb(original), "audit_1", "incomplete", 2),
        )
    expected = deepcopy(original)
    for error in expected["errors"][:3]:
        error["detail"] = "The source asset could not be loaded."
    app = create_app()
    app.dependency_overrides[get_audit_read_repository] = lambda: populated

    with TestClient(app) as client:
        response = client.get("/api/v1/audits/audit_1/samples/incomplete")

    assert response.status_code == 200
    assert response.json()["measurements"] == expected
    assert "/private/managed/snapshot" not in response.text
    assert response.json()["summary"]["tool_status"] == "unavailable"
    assert response.json()["review"]["decision"] == "pending_review"
    assert audits.get_measurements("audit_1", "incomplete", 2) == original


@pytest.mark.parametrize("sample_id", ["failed", "running", "queued", "cancelled"])
def test_missing_measurements_are_null_with_failures_retained(populated, sample_id):
    detail = populated.get_sample("audit_1", sample_id)
    assert detail.measurements is None and detail.findings == []
    assert detail.summary.processing_status == sample_id
    assert len(detail.failures) == (1 if sample_id == "failed" else 0)
    assert detail.summary.thumbnail_url is None
    assert all(value is None for value in detail.assets.model_dump().values())
    if detail.failures:
        assert detail.failures[0].sample_revision == 2
        assert detail.failures[0].reason == "missing_input"


def test_missing_resources_are_distinct_from_empty_filtered_queues(populated):
    with pytest.raises(AuditNotFoundError):
        populated.list_samples("unknown")
    with pytest.raises(AuditNotFoundError):
        populated.get_sample("unknown", "passing")
    with pytest.raises(AuditSampleNotFoundError):
        populated.get_sample("audit_1", "unknown")
    assert populated.list_samples("audit_1", review_status="accepted").items == []


def test_detail_uses_one_snapshot_even_when_review_changes_during_read(populated, monkeypatch):
    original = populated._get_audit

    def change_review_after_snapshot(connection, audit_id):
        row = original(connection, audit_id)
        decide(populated.settings, "passing", "accepted")
        return row

    with monkeypatch.context() as patch:
        patch.setattr(populated, "_get_audit", change_review_after_snapshot)
        detail = populated.get_sample("audit_1", "passing")
    assert detail.review.revision == 0
    assert detail.summary.review_status == "pending_review"
    assert populated.get_sample("audit_1", "passing").review.revision == 1


def test_same_sample_in_another_audit_does_not_leak_evidence_or_reviews(populated):
    audits = AuditRepository(settings=populated.settings)
    audits.start_audit(BatchAuditRequest(
        dataset_id="dataset_2", dataset_root="unused-root", rulebook_id="book_2", rulebook_version=1,
        sample_ids=["passing"], sample_revisions={"passing": 9},
    ), audit_id="audit_2", job_id="job_2")
    job = audits.claim_audit("audit_2", worker_id="worker_2")
    claim = audits.claim_next_sample(job)
    audits.persist_sample_failure(claim, failure=AuditFailure(
        failure_id="other_failure", audit_id="audit_2", sample_id="passing", sample_revision=9,
        stage="load", reason="missing_input", detail="Other revision failed.", created_at=job.job.created_at,
    ))
    review_sample("audit_2", "passing", SampleReviewRequest(
        expected_sample_revision=9, expected_review_version=0, decision="rejected",
        reviewer_id="reviewer_2", comment="Other audit decision.",
    ), repository=SampleReviewRepository(settings=populated.settings))
    original = populated.get_sample("audit_1", "passing")
    other = populated.get_sample("audit_2", "passing")
    assert original.summary.sample_revision == 2 and original.failures == []
    assert original.review.revision == 0 and len(original.findings) == 1
    assert other.dataset_id == "dataset_2" and other.summary.sample_revision == 9
    assert other.findings == [] and other.measurements is None
    assert other.failures[0].failure_id == "other_failure"
    assert other.review.decision == "rejected"


def test_http_reads_real_postgres_results(populated):
    app = create_app()
    app.dependency_overrides[get_audit_read_repository] = lambda: populated
    with TestClient(app) as client:
        response = client.get("/api/v1/audits/audit_1/samples?tool_status=flagged")
        assert response.status_code == 200 and response.json()["total"] == 2
        detail = client.get("/api/v1/audits/audit_1/samples/failed")
        assert detail.status_code == 200
        assert detail.json()["measurements"] is None
        assert detail.json()["failures"][0]["reason"] == "missing_input"


def test_http_review_workflow_round_trips_versions_and_preserves_scope(populated):
    finding_repository = FindingRepository(settings=populated.settings)
    sample_repository = SampleReviewRepository(settings=populated.settings)
    audits = AuditRepository(settings=populated.settings)
    app = create_app()
    app.dependency_overrides[get_audit_read_repository] = lambda: populated
    app.dependency_overrides[get_finding_repository] = lambda: finding_repository
    app.dependency_overrides[get_sample_review_repository] = lambda: sample_repository

    audit_before = audits.get_audit("audit_1")
    job_before = audits.get_job("audit_1")
    with TestClient(app, raise_server_exceptions=False) as client:
        initial_response = client.get("/api/v1/audits/audit_1/samples/flagged")
        assert initial_response.status_code == 200
        initial = initial_response.json()
        initial_finding = next(item for item in initial["findings"] if item["finding_id"] == "flagged_0")
        assert initial["review"]["revision"] == 0
        assert initial["review"]["decision"] == "pending_review"
        assert initial["summary"]["sample_revision"] == 2
        assert initial_finding["review"]["resolution"] == "pending"

        finding_write = client.put(
            "/api/v1/findings/flagged_0/review",
            json={
                "expected_review_version": 0,
                "resolution": "rejected",
                "reviewer_id": "finding_reviewer",
                "comment": "The machine finding is not a violation.",
            },
        )
        assert finding_write.status_code == 200
        assert finding_write.json()["revision"] == 1
        assert finding_write.json()["resolution"] == "rejected"

        finding_detail = client.get("/api/v1/audits/audit_1/samples/flagged")
        finding_history = client.get("/api/v1/findings/flagged_0/reviews")
        assert finding_detail.status_code == finding_history.status_code == 200
        assert next(item for item in finding_detail.json()["findings"]
                     if item["finding_id"] == "flagged_0")["review"]["resolution"] == "rejected"
        assert finding_history.json()["total"] == 1
        assert finding_history.json()["items"][0]["revision"] == 1

        stale_finding = client.put(
            "/api/v1/findings/flagged_0/review",
            json={
                "expected_review_version": 0,
                "resolution": "accepted",
                "reviewer_id": "stale_finding_reviewer",
                "comment": "This must not overwrite the saved decision.",
            },
        )
        assert stale_finding.status_code == 409
        assert stale_finding.json()["error"]["code"] == "VERSION_CONFLICT"
        assert client.get("/api/v1/findings/flagged_0/reviews").json()["total"] == 1

        sample_write = client.put(
            "/api/v1/audits/audit_1/samples/flagged/review",
            json={
                "expected_sample_revision": 2,
                "expected_review_version": 0,
                "decision": "accepted",
                "reviewer_id": "sample_reviewer",
                "comment": "The audited sample is usable.",
            },
        )
        assert sample_write.status_code == 200
        assert sample_write.json()["sample_revision"] == 2
        assert sample_write.json()["revision"] == 1
        assert sample_write.json()["decision"] == "accepted"

        accepted_detail = client.get("/api/v1/audits/audit_1/samples/flagged")
        accepted_queue = client.get(
            "/api/v1/audits/audit_1/samples", params={"review_status": "accepted"},
        )
        accepted_history = client.get(
            "/api/v1/audits/audit_1/samples/flagged/reviews",
            params={"sample_revision": 2},
        )
        assert accepted_detail.status_code == accepted_queue.status_code == accepted_history.status_code == 200
        assert accepted_detail.json()["review"]["revision"] == 1
        assert accepted_detail.json()["review"]["decision"] == "accepted"
        assert accepted_queue.json()["total"] == 1
        assert accepted_queue.json()["items"][0]["sample_id"] == "flagged"
        assert accepted_queue.json()["items"][0]["review_status"] == "accepted"
        assert accepted_history.json()["total"] == 1
        assert accepted_history.json()["items"][0]["revision"] == 1

        stale_sample = client.put(
            "/api/v1/audits/audit_1/samples/flagged/review",
            json={
                "expected_sample_revision": 2,
                "expected_review_version": 0,
                "decision": "rejected",
                "reviewer_id": "stale_sample_reviewer",
                "comment": "This must not add a second decision.",
            },
        )
        assert stale_sample.status_code == 409
        assert stale_sample.json()["error"]["code"] == "VERSION_CONFLICT"
        assert client.get(
            "/api/v1/audits/audit_1/samples/flagged/reviews",
            params={"sample_revision": 2},
        ).json()["total"] == 1

        reopened = client.put(
            "/api/v1/audits/audit_1/samples/flagged/review",
            json={
                "expected_sample_revision": 2,
                "expected_review_version": 1,
                "decision": "pending_review",
                "reviewer_id": "reopener",
                "comment": "Additional review is required.",
            },
        )
        assert reopened.status_code == 200
        assert reopened.json()["sample_revision"] == 2
        assert reopened.json()["revision"] == 2
        assert reopened.json()["decision"] == "pending_review"

        final_detail = client.get("/api/v1/audits/audit_1/samples/flagged")
        final_queue = client.get(
            "/api/v1/audits/audit_1/samples", params={"review_status": "pending_review"},
        )
        final_history = client.get(
            "/api/v1/audits/audit_1/samples/flagged/reviews",
            params={"sample_revision": 2},
        )
        assert final_detail.status_code == final_queue.status_code == final_history.status_code == 200
        final = final_detail.json()
        final_finding = next(item for item in final["findings"] if item["finding_id"] == "flagged_0")
        assert final["review"]["revision"] == 2
        assert final["review"]["decision"] == "pending_review"
        assert final_queue.json()["total"] == len(SAMPLE_IDS)
        assert next(item for item in final_queue.json()["items"] if item["sample_id"] == "flagged")["review_status"] == "pending_review"
        assert [item["revision"] for item in final_history.json()["items"]] == [1, 2]
        assert [item["decision"] for item in final_history.json()["items"]] == ["accepted", "pending_review"]

        assert final["measurements"] == initial["measurements"]
        assert {key: final_finding[key] for key in final_finding if key != "review"} == {
            key: initial_finding[key] for key in initial_finding if key != "review"
        }
        assert final["summary"]["tool_status"] == initial["summary"]["tool_status"] == "flagged"
        assert final["summary"]["finding_counts"] == initial["summary"]["finding_counts"]
        assert final_finding["review"]["revision"] == 1
        assert final_finding["review"]["resolution"] == "rejected"

        other_sample = client.get("/api/v1/audits/audit_1/samples/passing")
        other_history = client.get(
            "/api/v1/audits/audit_1/samples/passing/reviews",
            params={"sample_revision": 2},
        )
        assert other_sample.status_code == other_history.status_code == 200
        assert other_sample.json()["review"]["revision"] == 0
        assert other_sample.json()["review"]["decision"] == "pending_review"
        assert other_history.json()["total"] == 0

        wrong_pin = client.get(
            "/api/v1/audits/audit_1/samples/flagged/reviews",
            params={"sample_revision": 1},
        )
        assert wrong_pin.status_code == 409
        assert wrong_pin.json()["error"]["code"] == "STALE_SAMPLE_REVISION"

    assert audits.get_audit("audit_1") == audit_before
    assert audits.get_job("audit_1") == job_before
