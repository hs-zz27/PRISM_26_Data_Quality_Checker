"""Opt-in PostgreSQL coverage for immutable sample-asset manifests."""

from hashlib import sha256
import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr
import pytest

from backend.app.core.config import AssetStorageSettings, DatabaseSettings
from backend.app.models.asset_manifest import AssetAvailability, AssetManifestEntry, SampleAssetManifest
from backend.app.storage.database import database_connection, initialize_database
from backend.app.storage.workflow_repository import (
    AssetIntegrityError,
    AssetUnavailableError,
    SampleAssetManifestConflictError,
    SampleAssetManifestNotFoundError,
    SampleAssetManifestRepository,
)


@pytest.fixture
def database_settings():
    conninfo = os.environ.get("PRISM_TEST_DATABASE_URL")
    if not conninfo:
        pytest.skip("Set PRISM_TEST_DATABASE_URL to run PostgreSQL integration tests.")
    schema = f"prism_asset_manifest_{uuid4().hex}"
    with psycopg.connect(conninfo, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        settings = DatabaseSettings(conninfo=SecretStr(make_conninfo(conninfo, options=f"-c search_path={schema}")))
        initialize_database(settings)
        yield settings
    finally:
        with psycopg.connect(conninfo, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def entry(key: str, content: bytes, content_type: str) -> AssetManifestEntry:
    return AssetManifestEntry(
        availability=AssetAvailability.AVAILABLE,
        storage_key=key,
        content_type=content_type,
        sha256=sha256(content).hexdigest(),
        size_bytes=len(content),
    )


def manifest(*, dataset_id="dataset_1", sample_id="sample_1", revision=2) -> SampleAssetManifest:
    return SampleAssetManifest(
        dataset_id=dataset_id, sample_id=sample_id, sample_revision=revision,
        original_image=entry(f"{dataset_id}/{sample_id}/{revision}/image", b"image", "image/png"),
        raw_mask=entry(f"{dataset_id}/{sample_id}/{revision}/mask", b"mask", "image/png"),
        annotation_text=entry(f"{dataset_id}/{sample_id}/{revision}/annotation", b"annotation", "text/plain"),
    )


def test_manifest_round_trip_is_exact_and_immutable(database_settings, tmp_path):
    storage = AssetStorageSettings(root=tmp_path)
    image = tmp_path / "dataset_1/sample_1/2/image"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")
    repository = SampleAssetManifestRepository(settings=database_settings, asset_settings=storage)
    original = manifest()

    assert repository.save_manifest(original) == original
    assert SampleAssetManifestRepository(
        settings=database_settings, asset_settings=storage,
    ).get_manifest("dataset_1", "sample_1", 2) == original
    assert repository.resolve_asset_path("dataset_1", "sample_1", 2, "original_image") == image

    replay = repository.save_manifest(original)
    assert replay == original and replay is not original

    replacement = original.model_copy(update={
        "original_image": entry("dataset_1/sample_1/2/replacement", b"replacement", "image/png"),
    })
    with pytest.raises(SampleAssetManifestConflictError):
        repository.save_manifest(replacement)
    assert repository.get_manifest("dataset_1", "sample_1", 2) == original


def test_manifest_scope_revision_and_missing_resources_are_distinct(database_settings, tmp_path):
    repository = SampleAssetManifestRepository(
        settings=database_settings, asset_settings=AssetStorageSettings(root=tmp_path),
    )
    first = manifest(revision=1)
    second = manifest(revision=2)
    other_dataset = manifest(dataset_id="dataset_2")
    repository.save_manifest(first)
    repository.save_manifest(second)
    repository.save_manifest(other_dataset)

    assert repository.get_manifest("dataset_1", "sample_1", 1) == first
    assert repository.get_manifest("dataset_1", "sample_1", 2) == second
    assert repository.get_manifest("dataset_2", "sample_1", 2) == other_dataset
    with pytest.raises(SampleAssetManifestNotFoundError):
        repository.get_manifest("dataset_1", "sample_1", 3)


def test_unavailable_and_changed_files_do_not_look_like_valid_assets(database_settings, tmp_path):
    storage = AssetStorageSettings(root=tmp_path)
    repository = SampleAssetManifestRepository(settings=database_settings, asset_settings=storage)
    unavailable = manifest(revision=3).model_copy(update={
        "raw_mask": AssetManifestEntry(availability="missing"),
    })
    repository.save_manifest(unavailable)
    with pytest.raises(AssetUnavailableError):
        repository.resolve_asset_path("dataset_1", "sample_1", 3, "raw_mask")

    image = tmp_path / "dataset_1/sample_1/2/image"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"changed")
    repository.save_manifest(manifest())
    with pytest.raises(AssetIntegrityError):
        repository.resolve_asset_path("dataset_1", "sample_1", 2, "original_image")

    with pytest.raises(AssetIntegrityError):
        repository.resolve_asset_path("dataset_1", "sample_1", 2, "raw_mask")


def test_manifest_migration_does_not_backfill_old_records(database_settings):
    with database_connection(database_settings, readonly=True) as connection:
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()[-1] == {"version": 8}
        assert connection.execute("SELECT COUNT(*) AS total FROM sample_asset_manifests").fetchone() == {"total": 0}
