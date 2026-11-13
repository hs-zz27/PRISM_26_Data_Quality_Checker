"""A review never changes the stored photo or the stored annotation file.

Problems are shown beside the finding (drawn in the browser, or in images made for the model), never drawn into
or saved over the original. These tests hash every stored file before and after the work a review involves.
"""
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path

from PIL import Image
import pytest

from backend.app.core.config import AssetStorageSettings
from backend.app.models.asset_manifest import AssetKind
from backend.app.models.correction import RecheckRequest
from backend.app.models.job import JobKind
from backend.app.models.review import FindingReviewRequest, SampleReviewRequest
from backend.app.services.annotation_overlay import annotation_overlay
from backend.app.services.evaluation import evaluate_rules
from backend.app.services.json_audit import audit_json_sample
from backend.app.services.review import review_finding, review_sample
from backend.app.services.target_focus import render_focus
from backend.app.storage.audit_read_repository import AuditReadRepository
from backend.app.storage.recheck_repository import RecheckRepository
from backend.app.storage.workflow_repository import (
    FindingRepository, SampleReviewRepository, read_manifest_asset_file,
)
from backend.tests.dataset_fixtures import (  # noqa: F401
    dataset_database_settings, empty_dataset_database_settings, read_sql,
)
from backend.tests.test_evaluation import confirmed_book, label_rule
from backend.tests.test_json_dataset_workflow import imported
from backend.tests.test_labelme_person_polygons import people
from backend.tests.test_rechecks import prepared


def stored_files(root: Path) -> dict[str, str]:
    """{relative path: SHA-256} for every file under the asset store."""
    return {path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


def test_reviewing_findings_and_the_image_changes_no_stored_file(dataset_database_settings, tmp_path):
    settings = dataset_database_settings
    repo, corrections, parent, audits, worker = prepared(settings, tmp_path)
    audit_id = parent.audit.audit_id
    root = Path(corrections.settings.asset_root)
    manifests = worker.manifest_repository

    def stored(kind):
        return sha256(manifests.read_asset_bytes("dataset_a", "sample_a", 1, kind)).hexdigest()

    files_before = stored_files(root)
    image_before, annotation_before = stored(AssetKind.ORIGINAL_IMAGE), stored(AssetKind.ANNOTATION_TEXT)
    manifest_before = read_sql(settings, "SELECT * FROM sample_asset_manifests ORDER BY sample_id, sample_revision")
    assert files_before and len(set(files_before.values())) == 3  # photo, mask and annotation are stored

    # The review: read the evidence, decide every finding, decide the image twice, then recheck with feedback.
    reader = AuditReadRepository(settings=settings)
    detail = reader.get_sample(audit_id, "sample_a")
    findings = reader.list_audit_findings(audit_id, limit=100, offset=0).items
    assert findings
    finding_reviews = FindingRepository(settings=settings)
    for index, finding in enumerate(findings):
        review_finding(finding.finding_id, FindingReviewRequest(
            expected_review_version=0, resolution="accepted" if index % 2 == 0 else "rejected",
            reviewer_id="reviewer", comment="Looked at the image."), repository=finding_reviews)
    sample_reviews = SampleReviewRepository(settings=settings)
    for version, decision in enumerate(("needs_correction", "rejected", "accepted")):
        review_sample(audit_id, "sample_a", SampleReviewRequest(
            expected_sample_revision=1, expected_review_version=version, decision=decision,
            reviewer_id="reviewer", comment="The head is outside the annotation."), repository=sample_reviews)
    child = RecheckRepository(settings=settings).start(audit_id, RecheckRequest(
        sample_ids=["sample_a"], sample_revisions={"sample_a": 1}, reviewer_id="reviewer",
        feedback="The head is outside the annotation.", propose_correction=True))
    claim = audits.jobs.claim_next_job(worker_id="synthetic", kind=JobKind.AUDIT, lease_seconds=300)
    assert worker.run_claimed_job(claim).status.value == "completed"
    assert reader.get_sample(child.audit.audit_id, "sample_a").summary.sample_revision == 1
    assert detail.summary.sample_revision == 1

    assert sample_reviews.get_sample_review(audit_id, "sample_a", 1).decision.value == "accepted"
    assert stored(AssetKind.ORIGINAL_IMAGE) == image_before
    assert stored(AssetKind.ANNOTATION_TEXT) == annotation_before
    # No stored file was changed, replaced, added or removed, and no manifest row was rewritten.
    assert stored_files(root) == files_before
    assert read_sql(settings, "SELECT * FROM sample_asset_manifests ORDER BY sample_id, sample_revision") == manifest_before
    assert repo.get_sample("dataset_a", "sample_a").revision == 1


def test_drawing_a_labelme_problem_for_display_leaves_the_stored_photo_and_file_identical(tmp_path):
    data = people(3)
    publications, settings, raw, image = imported(tmp_path, "labelme", data=data)
    publication = publications[0]
    assets = AssetStorageSettings(root=settings.asset_root)
    root = Path(settings.asset_root)

    def stored(role):
        return sha256(read_manifest_asset_file(publication.manifest, role, assets)[1]).hexdigest()

    files_before = stored_files(root)
    image_before, annotation_before = stored("original_image"), stored("annotation_text")
    assert image_before == sha256(image).hexdigest() and annotation_before == sha256(raw).hexdigest()

    # Everything a review shows: the evidence, the findings, the outlines drawn over the photo and a close-up.
    report = audit_json_sample(publication.manifest, assets)
    findings = evaluate_rules(confirmed_book(label_rule()), report, audit_id="audit",
                              sample_id=publication.sample.sample_id, sample_revision=1)
    assert findings
    objects = [obj.model_dump(mode="json") for obj in publication.sample.objects]
    photo = read_manifest_asset_file(publication.manifest, "original_image", assets)[1]
    overlay = annotation_overlay(photo, objects, "labelme")
    views = render_focus(photo, objects, "/shapes/1", "labelme")

    # The drawings are new pictures: they differ from the photo, which proves something was drawn, elsewhere.
    def pixels(content):
        with Image.open(BytesIO(content)) as picture:
            return picture.convert("RGB").tobytes()
    assert pixels(overlay) != pixels(photo) and pixels(views.overlay_full) != pixels(photo)
    assert objects == [obj.model_dump(mode="json") for obj in publication.sample.objects]

    assert stored("original_image") == image_before
    assert stored("annotation_text") == annotation_before
    assert stored_files(root) == files_before
    assert json.loads(read_manifest_asset_file(publication.manifest, "annotation_text", assets)[1]) == data


@pytest.mark.parametrize("decision", ["accepted", "rejected", "needs_correction"])
def test_each_decision_alone_leaves_the_files_identical(dataset_database_settings, tmp_path, decision):
    settings = dataset_database_settings
    _, corrections, parent, _, _ = prepared(settings, tmp_path)
    root = Path(corrections.settings.asset_root)
    before = stored_files(root)
    review_sample(parent.audit.audit_id, "sample_a", SampleReviewRequest(
        expected_sample_revision=1, expected_review_version=0, decision=decision,
        reviewer_id="reviewer", comment="Decided after looking at the close-up."),
        repository=SampleReviewRepository(settings=settings))
    assert stored_files(root) == before
