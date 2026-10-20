"""Authored catalogue/ownership regressions; PostgreSQL tests are opt-in and unrun."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event

from psycopg.types.json import Jsonb
from pydantic import ValidationError
import pytest

from backend.app.models.dataset import source_issue
from backend.app.models.dataset_import import SamplePublication
from backend.app.storage import dataset_repository as module
from backend.app.storage.database import (
    CommitOutcomeUnknownError, DatabaseError, database_connection,
)
from backend.app.storage.dataset_repository import (
    DatasetCommitOutcomeUnknownError, DatasetImportDeadlineError, DatasetNotFoundError,
    DatasetPublicationConflictError, DatasetRepository, DatasetRevisionNotFoundError,
    DatasetSampleNotFoundError, DatasetStorageError,
)
from backend.app.storage.workflow_repository import (
    JobCancellationError, JobClaimLostError, SampleAssetManifestRepository,
)
from backend.tests.dataset_fixtures import (
    dataset_database_settings, empty_dataset_database_settings, prepared_sample,
    read_sql, seed_historical_revision, seed_import, write_sql,
)


@pytest.fixture
def repository(dataset_database_settings):
    return DatasetRepository(settings=dataset_database_settings)


def counts(settings):
    return {table: read_sql(settings, f"SELECT count(*) AS n FROM {table}")[0]["n"]
            for table in ("sample_asset_manifests", "dataset_samples",
                          "dataset_sample_revisions", "dataset_sample_heads")}


def test_empty_pages_and_precise_missing_resource_errors(repository):
    assert repository.list_datasets().items == []
    with pytest.raises(DatasetNotFoundError):
        repository.list_samples("missing")
    seed_import(repository.settings)
    assert repository.list_samples("dataset_a").total == 0
    with pytest.raises(DatasetSampleNotFoundError):
        repository.get_sample("dataset_a", "missing", revision=5)
    with pytest.raises(DatasetNotFoundError):
        repository.get_sample("missing", "missing")
    with pytest.raises(DatasetSampleNotFoundError):
        repository.resolve_revision("dataset_a", "missing")


def test_null_counts_damaged_objects_and_revision_pinned_links(repository, monkeypatch):
    _, _, claim = seed_import(repository.settings)
    prepared = prepared_sample()
    repository._publish_import_sample(claim, prepared)
    assert repository.get_dataset("dataset_a").sample_count is None
    assert repository.get_dataset("dataset_a").object_count is None
    # Even an attempted read through the asset service must fail this test.
    def forbidden(*args, **kwargs):
        pytest.fail("Catalogue reads must not open or decode managed/source files.")
    monkeypatch.setattr(SampleAssetManifestRepository, "read_asset_bytes", forbidden)
    monkeypatch.setattr(SampleAssetManifestRepository, "resolve_asset_path", forbidden)
    sample = repository.get_sample("dataset_a", "sample_a")
    assert sample.integrity_status.value == "invalid"
    assert [obj.object_id for obj in sample.objects] == [-1, -1]
    assert sample.objects[0].bbox == (-3, 5, -8, 0)
    assert sample.assets.image.url.endswith("/assets/image?revision=1")
    assert sample.assets.thumbnail.url.endswith("/assets/thumbnail?revision=1")
    assert sample.assets.mask is None and sample.assets.mask_preview is None
    assert sample.image_size is None and sample.issues
    assert repository.list_samples("dataset_a").items == [sample]
    pin = repository.resolve_revision("dataset_a", "sample_a")
    assert pin.manifest == prepared.manifest
    assert "source_root" not in pin.model_dump()
    public = sample.model_dump_json()
    assert prepared.manifest.original_image.storage_key not in public
    assert prepared.manifest.original_image.sha256 not in public
    assert claim.claim_token not in public and claim.request.archive_key not in public


def test_stable_order_pagination_totals_and_offset_beyond_end(repository):
    _, _, first = seed_import(repository.settings, "dataset_b")
    timestamp = repository.get_dataset("dataset_b").created_at
    _, _, second = seed_import(repository.settings, "dataset_a", now=timestamp)
    for index, name in enumerate(["z", "a", "m"]):
        repository._publish_import_sample(second, prepared_sample(sample_id=name, ordinal=index))
    page = repository.list_datasets(limit=1)
    assert [item.dataset_id for item in page.items] == ["dataset_a"]
    assert page.total == 2 and page.next_offset == 1
    assert repository.list_datasets(limit=1, offset=1).items[0].dataset_id == "dataset_b"
    samples = repository.list_samples("dataset_a", limit=2)
    assert [item.sample_id for item in samples.items] == ["z", "a"]
    assert samples.total == 3 and samples.next_offset == 2
    last = repository.list_samples("dataset_a", limit=2, offset=2)
    assert [item.sample_id for item in last.items] == ["m"] and last.next_offset is None
    assert repository.list_samples("dataset_a", offset=99).items == []


def test_current_authority_is_not_highest_manifest_or_highest_revision(repository):
    _, _, claim = seed_import(repository.settings)
    original = prepared_sample()
    repository._publish_import_sample(claim, original)
    seed_historical_revision(repository.settings, original, revision=2)
    seed_historical_revision(repository.settings, original, revision=3, advance_head=False)
    # An unrelated/legacy manifest is also deliberately larger than any head.
    extra = original.manifest.model_dump()
    extra["sample_revision"] = 99
    with database_connection(repository.settings) as connection:
        repository._insert_manifest(connection, module.SampleAssetManifest.model_validate(extra))
    assert repository.get_sample("dataset_a", "sample_a").revision == 2
    assert repository.list_samples("dataset_a").items[0].revision == 2
    assert repository.get_sample("dataset_a", "sample_a", revision=1).revision == 1
    assert repository.get_sample("dataset_a", "sample_a", revision=3).revision == 3
    with pytest.raises(DatasetRevisionNotFoundError):
        repository.get_sample("dataset_a", "sample_a", revision=99)
    with pytest.raises(DatasetRevisionNotFoundError):
        repository.resolve_revision("dataset_a", "sample_a", revision=100)


def test_identical_replay_returns_independent_evidence_and_conflicts_do_not_overwrite(repository):
    _, _, claim = seed_import(repository.settings)
    original = prepared_sample()
    first = repository._publish_import_sample(claim, original)
    assert repository._publish_import_sample(claim, original) == first
    assert counts(repository.settings) == dict.fromkeys(counts(repository.settings), 1)
    first.objects.clear()
    assert len(repository.get_sample("dataset_a", "sample_a").objects) == 2
    altered = original.model_dump()
    altered["sample"]["objects"][0]["label"] = "different"
    with pytest.raises(DatasetPublicationConflictError):
        repository._publish_import_sample(claim, SamplePublication.model_validate(altered))
    altered = original.model_dump()
    altered["manifest"]["original_image"]["sha256"] = "f" * 64
    with pytest.raises(DatasetPublicationConflictError):
        repository._publish_import_sample(claim, SamplePublication.model_validate(altered))
    with pytest.raises(DatasetPublicationConflictError):
        repository._publish_import_sample(claim, prepared_sample(sample_id="other", ordinal=0))
    assert repository.get_sample("dataset_a", "sample_a").objects[0].label == ""


def test_unowned_even_identical_manifest_is_not_adopted(repository):
    _, _, claim = seed_import(repository.settings)
    original = prepared_sample()
    with database_connection(repository.settings) as connection:
        repository._insert_manifest(connection, original.manifest)
    with pytest.raises(DatasetPublicationConflictError):
        repository._publish_import_sample(claim, original)
    assert counts(repository.settings) == {"sample_asset_manifests": 1, "dataset_samples": 0,
                                           "dataset_sample_revisions": 0, "dataset_sample_heads": 0}


def test_invalid_or_mutated_publication_is_rejected_before_database_access(repository, monkeypatch):
    _, _, claim = seed_import(repository.settings)
    def forbid(**kwargs):
        pytest.fail("Invalid publication must not open a connection.")
    monkeypatch.setattr(repository, "_connection", forbid)
    bad = prepared_sample()
    bad.sample.revision = True
    with pytest.raises(ValidationError):
        repository._publish_import_sample(claim, bad)
    with pytest.raises(ValueError):
        repository._publish_import_sample(claim, prepared_sample(dataset_id="foreign"))
    bad = prepared_sample()
    bad.manifest = bad.manifest.model_copy(update={"sample_id": "foreign"})
    with pytest.raises(ValidationError):
        repository._publish_import_sample(claim, bad)


@pytest.mark.parametrize("change", ["token", "worker", "attempt", "kind", "expired", "cancel", "dataset"])
def test_locked_stored_ownership_is_required(repository, change):
    _, _, claim = seed_import(repository.settings)
    statements = {
        "token": "UPDATE jobs SET claim_token='replacement'",
        "worker": "UPDATE jobs SET claimed_by='foreign'",
        "attempt": "UPDATE jobs SET attempt=2",
        "kind": "UPDATE jobs SET kind='audit'",
        "expired": "UPDATE jobs SET lease_until=clock_timestamp()-interval '1 second'",
        "cancel": "UPDATE jobs SET status='cancelling'",
        "dataset": "UPDATE datasets SET status='cancelled'",
    }
    write_sql(repository.settings, statements[change])
    expected = JobCancellationError if change == "cancel" else JobClaimLostError
    with pytest.raises(expected):
        repository._publish_import_sample(claim, prepared_sample())
    assert all(value == 0 for value in counts(repository.settings).values())


def test_deadline_after_all_publication_writes_rolls_everything_back(repository, monkeypatch):
    _, _, claim = seed_import(repository.settings)
    authorize = repository._authorize
    calls = 0
    def expires(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise DatasetImportDeadlineError("Simulated deadline after write locks.")
        return authorize(*args)
    monkeypatch.setattr(repository, "_authorize", expires)
    with pytest.raises(DatasetImportDeadlineError):
        repository._publish_import_sample(claim, prepared_sample())
    assert calls == 2 and all(value == 0 for value in counts(repository.settings).values())


def test_post_job_lock_time_is_authoritative(repository):
    _, _, claim = seed_import(repository.settings)
    entered = Event()
    def publish():
        entered.set()
        return DatasetRepository(settings=repository.settings)._publish_import_sample(claim, prepared_sample())
    with ThreadPoolExecutor(max_workers=1) as pool:
        with database_connection(repository.settings) as blocker:
            blocker.execute("SELECT * FROM jobs WHERE job_id=%s FOR UPDATE", (claim.job.job_id,))
            future = pool.submit(publish)
            assert entered.wait(timeout=5)
            # The caller's lease snapshot remains in the future, but the committed
            # locked row becomes expired before the waiting publisher can proceed.
            blocker.execute("UPDATE jobs SET lease_until=clock_timestamp()-interval '1 second'")
        with pytest.raises(JobClaimLostError):
            future.result(timeout=10)
    assert all(value == 0 for value in counts(repository.settings).values())


def test_known_failure_rolls_back_manifest_revision_and_head(repository, monkeypatch):
    _, _, claim = seed_import(repository.settings)
    actual = repository._insert_manifest
    def fails(connection, manifest):
        actual(connection, manifest)
        raise DatabaseError("Injected storage error containing private driver information.")
    monkeypatch.setattr(repository, "_insert_manifest", fails)
    with pytest.raises(DatasetStorageError) as error:
        repository._publish_import_sample(claim, prepared_sample())
    assert "private driver" not in str(error.value)
    assert all(value == 0 for value in counts(repository.settings).values())


def test_unknown_acknowledgement_is_a_distinct_error_not_success_or_automatic_replay(repository, monkeypatch):
    _, _, claim = seed_import(repository.settings)
    actual = module.database_connection
    @contextmanager
    def lost_ack(settings, *, readonly=False):
        with actual(settings, readonly=readonly) as connection:
            yield connection
        if not readonly:
            raise CommitOutcomeUnknownError("Injected lost acknowledgement after commit.")
    with monkeypatch.context() as patch:
        patch.setattr(module, "database_connection", lost_ack)
        with pytest.raises(DatasetCommitOutcomeUnknownError) as error:
            repository._publish_import_sample(claim, prepared_sample())
        assert error.value.commit_outcome_unknown
    # The simulation committed: explicit readback observes one immutable row, no retry.
    assert repository.get_sample("dataset_a", "sample_a").revision == 1
    assert all(value == 1 for value in counts(repository.settings).values())


@pytest.mark.parametrize("status", ["importing", "failed", "cancelled", "invalid"])
def test_partial_catalogue_remains_browsable_without_becoming_ready(repository, status):
    _, _, claim = seed_import(repository.settings)
    repository._publish_import_sample(claim, prepared_sample())
    write_sql(repository.settings, "UPDATE datasets SET status=%s,issues=%s",
              (status, Jsonb([source_issue("import_failed").model_dump()] if status == "failed"
                             else [source_issue("invalid_layout").model_dump()] if status == "invalid" else [])))
    assert repository.get_dataset("dataset_a").status.value == status
    assert repository.list_samples("dataset_a").total == 1
    assert repository.get_sample("dataset_a", "sample_a").revision == 1


def test_manifest_content_drift_fails_closed_without_recapture(repository):
    _, _, claim = seed_import(repository.settings)
    repository._publish_import_sample(claim, prepared_sample())
    # Legacy table allows SQL mutation; catalogue compares its immutable identity copy.
    write_sql(repository.settings, "UPDATE sample_asset_manifests SET original_image_sha256=%s", ("e" * 64,))
    with pytest.raises(DatasetStorageError):
        repository.get_sample("dataset_a", "sample_a")
    with pytest.raises(DatasetStorageError):
        repository.list_samples("dataset_a")


def test_page_uses_constant_query_count_and_fetches_only_requested_documents(repository, monkeypatch):
    _, _, claim = seed_import(repository.settings)
    for index in range(4):
        repository._publish_import_sample(claim, prepared_sample(sample_id=f"s{index}", ordinal=index))
    actual = repository._connection
    statements = []
    @contextmanager
    def observe(*, write=False):
        with actual(write=write) as connection:
            class Proxy:
                def execute(self, statement, parameters=()):
                    statements.append(statement)
                    return connection.execute(statement, parameters)
            yield Proxy()
    monkeypatch.setattr(repository, "_connection", observe)
    page = repository.list_samples("dataset_a", limit=2, offset=1)
    assert len(page.items) == 2 and page.total == 4
    assert len(statements) == 3
    assert "AS MATERIALIZED" in statements[-1] and "LIMIT %s OFFSET %s" in statements[-1]


@pytest.mark.parametrize("kwargs", [{"limit": True}, {"limit": 101}, {"offset": -1}, {"offset": 1.0}])
def test_invalid_pagination_does_not_open_database(repository, monkeypatch, kwargs):
    def forbid(**options):
        pytest.fail("Invalid pagination must not open a connection.")
    monkeypatch.setattr(repository, "_connection", forbid)
    with pytest.raises(ValueError):
        repository.list_samples("dataset_a", **kwargs)


def test_url_quoting_matches_existing_audit_link_conventions(repository):
    _, _, claim = seed_import(repository.settings, "dataset /é")
    prepared = prepared_sample(dataset_id="dataset /é", sample_id="sample /é")
    saved = repository._publish_import_sample(claim, prepared)
    assert saved.assets.image.url == (
        "/api/v1/datasets/dataset%20%2F%C3%A9/samples/sample%20%2F%C3%A9/assets/image?revision=1"
    )


def test_publisher_policy_budget_checks_precede_database_access(repository, monkeypatch):
    _, _, claim = seed_import(repository.settings)
    def forbid(**options):
        pytest.fail("Oversized prepared metadata must not open a connection.")
    monkeypatch.setattr(repository, "_connection", forbid)
    bad = prepared_sample().model_dump()
    bad["manifest"]["original_image"]["size_bytes"] = claim.request.policy.max_file_bytes + 1
    with pytest.raises(ValueError):
        repository._publish_import_sample(claim, SamplePublication.model_validate(bad))


def test_escaped_nul_in_source_label_remains_round_trippable_evidence(repository):
    _, _, claim = seed_import(repository.settings)
    raw = prepared_sample().model_dump()
    raw["sample"]["objects"][0]["label"] = "person\x00source"
    prepared = SamplePublication.model_validate(raw)
    repository._publish_import_sample(claim, prepared)
    saved = repository.get_sample("dataset_a", "sample_a")
    assert saved.objects[0].label == "person\x00source"
    assert "\\u0000" in saved.model_dump_json()


def test_two_publishers_of_one_identical_boundary_serialize_to_one_revision(repository):
    from threading import Barrier
    _, _, claim = seed_import(repository.settings)
    prepared = prepared_sample()
    barrier = Barrier(2)
    def publish(_):
        barrier.wait(timeout=5)
        return DatasetRepository(settings=repository.settings)._publish_import_sample(claim, prepared)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, range(2)))
    assert results[0] == results[1]
    assert all(value == 1 for value in counts(repository.settings).values())


def test_missing_or_malformed_registered_manifest_fails_closed():
    prepared = prepared_sample()
    row = {"registered_manifest": None, "manifest_document": prepared.manifest.model_dump(mode="json"),
           "sample_document": prepared.sample.model_dump_json(), "ordinal": 0,
           "annotation_object_count": None, "dataset_id": "dataset_a", "sample_id": "sample_a",
           "sample_revision": 1}
    with pytest.raises(DatasetStorageError):
        DatasetRepository._publication(row)


def test_explicit_settings_constructor_has_no_database_or_filesystem_effects(monkeypatch):
    from backend.app.core.config import DatabaseSettings
    from pydantic import SecretStr
    def forbid(*args, **kwargs):
        pytest.fail("Constructor must not open a connection.")
    monkeypatch.setattr(module, "database_connection", forbid)
    settings = DatabaseSettings(conninfo=SecretStr("dbname=not_contacted"))
    assert DatasetRepository(settings=settings).settings is settings
