"""Synthetic HTTP values; never a production dependency or runtime proof."""
from datetime import UTC, datetime

from backend.app.core.dataset_http import DatasetHttpSettings
from backend.app.core.dataset_import import ImportSettings
from backend.app.models.dataset import Dataset, DatasetImportStarted, DatasetPage, SamplePage, source_issue
from backend.app.models.dataset_import import ImportPolicy
from backend.app.models.job import Job, JobKind
from backend.app.storage.dataset_repository import (
    DatasetNotFoundError, DatasetRepository, DatasetRevisionNotFoundError, DatasetSampleNotFoundError,
)
from backend.tests.dataset_fixtures import prepared_sample


def http_settings(tmp_path, *, max_archive_bytes=256):
    return DatasetHttpSettings(imports=ImportSettings(root=tmp_path / "imports", asset_root=tmp_path / "assets",
                              policy=ImportPolicy(max_archive_bytes=max_archive_bytes)))


def dataset(*, status="importing", dataset_id="dataset_a"):
    return Dataset(dataset_id=dataset_id, name="Synthetic", format="penn_fudan", status=status,
                   sample_count=1 if status == "ready" else None, object_count=None,
                   import_job_id="job_dataset_a", created_at=datetime(2026, 10, 20, tzinfo=UTC),
                   issues=[source_issue("import_failed")] if status in {"failed", "invalid"} else [])


def started():
    value = dataset()
    return DatasetImportStarted(dataset=value, job=Job.queued(job_id=value.import_job_id,
                                kind=JobKind.DATASET_IMPORT, total=None, now=value.created_at))


class UploadService:
    def __init__(self, failure=None):
        self.calls, self.handles, self.failure = [], [], failure
    def submit(self, stream, *, name, format):
        self.handles.append(stream)
        self.calls.append((stream.read(1024), name, format))
        if self.failure is not None:
            raise self.failure
        return started()


class Catalogue:
    def __init__(self):
        self.calls = []
        self.value = dataset()
    def list_datasets(self, *, limit, offset):
        self.calls.append(("list", limit, offset))
        items = [self.value] if offset == 0 else []
        return DatasetPage(items=items, limit=limit, offset=offset, total=1, next_offset=None)
    def get_dataset(self, dataset_id):
        self.calls.append(("get", dataset_id))
        if dataset_id != "dataset_a":
            raise DatasetNotFoundError(dataset_id)
        return self.value
    def list_samples(self, dataset_id, *, limit, offset):
        self.get_dataset(dataset_id)
        sample = DatasetRepository._sample(prepared_sample())
        return SamplePage(items=[sample] if offset == 0 else [], limit=limit, offset=offset, total=1, next_offset=None)
    def get_sample(self, dataset_id, sample_id, *, revision=None):
        self.get_dataset(dataset_id)
        self.calls.append(("sample", sample_id, revision))
        if sample_id != "sample_a":
            raise DatasetSampleNotFoundError(dataset_id, sample_id)
        if revision not in (None, 1, 2):
            raise DatasetRevisionNotFoundError(dataset_id, sample_id, revision)
        return DatasetRepository._sample(prepared_sample(revision=2 if revision is None else revision))
