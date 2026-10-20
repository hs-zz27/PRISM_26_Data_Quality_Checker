"""Authored strict-model regressions. Not executed in this handoff."""

from datetime import UTC, datetime, timedelta
import json

from pydantic import ValidationError
import pytest

from backend.app.models.dataset import (
    Dataset, DatasetImportStarted, DatasetPage, ImportedAnnotationObject, Sample, SamplePage, source_issue,
)
from backend.app.models.dataset_import import ImportClaim, ImportPolicy, ImportRequest, SamplePublication
from backend.app.models.job import Job, JobKind
from backend.tests.dataset_fixtures import prepared_sample

NOW = datetime(2026, 10, 20, tzinfo=UTC)


def dataset(**updates):
    return Dataset.model_validate({
        "dataset_id": "dataset_a", "name": "Synthetic", "format": "penn_fudan",
        "status": "importing", "sample_count": None, "object_count": None,
        "import_job_id": "job_dataset_a", "created_at": NOW, **updates,
    })


def request(**updates):
    return ImportRequest.model_validate({
        "dataset_id": "dataset_a", "job_id": "job_dataset_a",
        "archive_key": "uploads/owned/archive.zip", "archive_sha256": "0" * 64,
        "archive_size_bytes": 4, "policy": ImportPolicy().model_dump(), **updates,
    })


@pytest.mark.parametrize("field,value", [
    ("sample_count", True), ("object_count", False), ("sample_count", -1),
    ("sample_count", 1.0), ("object_count", float("nan")),
    ("object_count", float("inf")), ("sample_count", 2**63),
    ("name", " \n"), ("unexpected", 1), ("format", "coco"),
    ("created_at", datetime(2026, 10, 20)), ("status", "completed"),
])
def test_dataset_rejects_invalid_primitive_or_extra(field, value):
    with pytest.raises(ValidationError):
        dataset(**{field: value})


def test_count_and_ready_semantics():
    assert dataset().object_count is None
    for status in ("invalid", "failed"):
        with pytest.raises(ValidationError):
            dataset(status=status)
        assert dataset(status=status, issues=[source_issue("import_failed")]).status.value == status
    assert dataset(status="ready", sample_count=1).object_count is None
    with pytest.raises(ValidationError):
        dataset(object_count=0)
    with pytest.raises(ValidationError):
        dataset(status="ready", sample_count=0, object_count=0)
    with pytest.raises(ValidationError):
        dataset(sample_count=0, object_count=1)


def test_source_objects_preserve_invalid_values_and_duplicates():
    prepared = prepared_sample()
    assert [obj.object_id for obj in prepared.sample.objects] == [-1, -1]
    assert prepared.sample.objects[0].bbox == (-3, 5, -8, 0)
    assert prepared.sample.objects[0].label == ""
    for bad in [True, 1.5, "1", float("inf")]:
        with pytest.raises(ValidationError):
            ImportedAnnotationObject(object_id=bad, label="person", bbox=(1, 2, 3, 4))
    with pytest.raises(ValidationError):
        ImportedAnnotationObject(object_id=1, label="person", bbox=(1, 2, True, 4))


def test_json_round_trip_preserves_tuple_constraints_and_strict_primitives():
    sample = prepared_sample().sample
    assert Sample.model_validate_json(sample.model_dump_json()) == sample
    raw = json.loads(sample.model_dump_json())
    raw["revision"] = True
    with pytest.raises(ValidationError):
        Sample.model_validate_json(json.dumps(raw))
    raw["revision"] = 1
    raw["objects"][0]["bbox"][0] = "1"
    with pytest.raises(ValidationError):
        Sample.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("revision", [False, 0, -1, 1.0, "1", 2**31])
def test_sample_revision_is_a_strict_supported_integer(revision):
    raw = prepared_sample().sample.model_dump()
    raw["revision"] = revision
    with pytest.raises(ValidationError):
        Sample.model_validate(raw)


def test_integrity_and_safe_issue_invariants():
    raw = prepared_sample().sample.model_dump()
    raw["issues"] = []
    with pytest.raises(ValidationError):
        Sample.model_validate(raw)
    raw["integrity_status"] = "valid"
    with pytest.raises(ValidationError):
        Sample.model_validate(raw)
    with pytest.raises(ValidationError):
        dataset(issues=[{"field": "archive", "message": "/private/staging/secret.zip"}])
    with pytest.raises(ValueError):
        source_issue("invalid_image", field="/private/file")


def test_pages_include_empty_offset_past_end_and_reject_inconsistent_totals():
    assert DatasetPage(items=[], limit=20, offset=0, total=0, next_offset=None).items == []
    assert SamplePage(items=[], limit=20, offset=50, total=3, next_offset=None).items == []
    with pytest.raises(ValidationError):
        DatasetPage(items=[], limit=20, offset=0, total=1, next_offset=None)
    with pytest.raises(ValidationError):
        SamplePage(items=[], limit=True, offset=0, total=0, next_offset=None)


@pytest.mark.parametrize("key", ["/abs.zip", "../a.zip", "a/../b", "a//b", "C:/x", "a\\b", "a/./b", "a/", "a\x00b"])
def test_private_request_rejects_unsafe_storage_keys(key):
    with pytest.raises(ValidationError):
        request(archive_key=key)


@pytest.mark.parametrize("updates", [
    {"max_seconds": True}, {"renew_seconds": float("inf")}, {"poll_seconds": float("nan")},
    {"lease_seconds": 2, "renew_seconds": 1.0}, {"max_annotation_bytes": 100, "max_file_bytes": 99},
    {"max_expanded_bytes": 1}, {"max_objects_per_sample": 10001},
])
def test_invalid_policy_is_rejected(updates):
    with pytest.raises(ValidationError):
        ImportPolicy(**updates)


def test_private_deadline_and_archive_identity():
    with pytest.raises(ValidationError):
        request(started_at=NOW)
    with pytest.raises(ValidationError):
        request(started_at=NOW, deadline=NOW + timedelta(seconds=1))
    with pytest.raises(ValidationError):
        request(archive_size_bytes=0)
    with pytest.raises(ValidationError):
        request(archive_sha256="A" * 64)
    assert "archive_key" not in dataset().model_dump()


def test_publication_revalidates_mutated_nested_models_and_rejects_wrong_manifest():
    prepared = prepared_sample()
    object.__setattr__(prepared.manifest, "dataset_id", "foreign")
    with pytest.raises(ValidationError):
        SamplePublication.model_validate(prepared.model_dump())
    prepared = prepared_sample()
    prepared.sample.revision = True
    with pytest.raises(ValidationError):
        SamplePublication.model_validate(prepared.model_dump())
    prepared = prepared_sample()
    prepared.sample.objects[0].object_id = False
    with pytest.raises(ValidationError):
        SamplePublication.model_validate(prepared.model_dump())


def test_complete_count_is_not_inferred_from_empty_or_partial_objects():
    prepared = prepared_sample()
    raw = prepared.model_dump()
    raw["sample"]["objects"] = []
    assert SamplePublication.model_validate(raw).annotation_object_count is None
    raw["annotation_object_count"] = 0
    assert SamplePublication.model_validate(raw).annotation_object_count == 0
    raw["annotation_object_count"] = 1
    with pytest.raises(ValidationError):
        SamplePublication.model_validate(raw)


def test_acceptance_must_be_exactly_one_queued_import():
    job = Job.queued(job_id="job_dataset_a", kind=JobKind.DATASET_IMPORT, total=None, now=NOW)
    assert DatasetImportStarted(dataset=dataset(), job=job).job == job
    job.job_id = "foreign"
    with pytest.raises(ValidationError):
        DatasetImportStarted(dataset=dataset(), job=job)
    wrong = Job.queued(job_id="job_dataset_a", kind=JobKind.AUDIT, total=None, now=NOW)
    with pytest.raises(ValidationError):
        DatasetImportStarted(dataset=dataset(), job=wrong)


def test_claim_requires_persisted_start_and_matching_dataset_job_kind():
    job = Job.queued(job_id="job_dataset_a", kind=JobKind.DATASET_IMPORT, total=None, now=NOW)
    with pytest.raises(ValidationError):
        ImportClaim(job=job, request=request(), worker_id="worker", claim_token="token",
                    lease_until=NOW + timedelta(seconds=30))


def test_asset_identity_and_mutated_shared_models_are_revalidated():
    from backend.app.models.audit_read import AssetReference, SampleAssets
    sample = prepared_sample().sample
    assets = SampleAssets(image=AssetReference(
        url="/api/v1/datasets/dataset_a/samples/sample_a/assets/image?revision=1",
        content_type="image/png", revision=1,
    ))
    raw = sample.model_dump()
    raw["assets"] = assets
    assert Sample.model_validate(raw).assets.image.revision == 1
    assets.image.url = "https://untrusted.invalid/"
    with pytest.raises(ValidationError):
        Sample.model_validate(raw)
    assets.image.url = "/api/v1/datasets/dataset_a/samples/sample_a/assets/image?revision=1"
    assets.image.revision = True
    with pytest.raises(ValidationError):
        Sample.model_validate(raw)
    with pytest.raises(ValidationError):
        dataset(dataset_id="a\x00b")
