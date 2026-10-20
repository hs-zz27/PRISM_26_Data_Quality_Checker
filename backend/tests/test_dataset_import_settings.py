"""Authored settings validation/no-startup-effects regressions. NOT executed."""
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.core.dataset_import import ImportConfigurationError, ImportSettings, load_import_settings
from backend.app.models.dataset_import import ImportPolicy


def test_root_env_precedence_and_loading_creates_nothing(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("DATASET_IMPORT_") or key == "ASSET_STORAGE_ROOT":
            monkeypatch.delenv(key)
    env = tmp_path / ".env"
    root, assets = tmp_path / "private-imports", tmp_path / "private-assets"
    env.write_text(f"DATASET_IMPORT_ROOT={root}\nASSET_STORAGE_ROOT={assets}\nDATASET_IMPORT_MAX_ENTRIES=15\n")
    monkeypatch.setenv("DATASET_IMPORT_MAX_ENTRIES", "17")
    settings = load_import_settings(env_path=env)
    assert settings.root == root and settings.policy.max_entries == 17
    assert not root.exists() and not assets.exists()


@pytest.mark.parametrize("value", ["", "NaN", "0", "false", "1.5"])
def test_invalid_integer_settings_are_safe_errors(tmp_path, monkeypatch, value):
    monkeypatch.setenv("DATASET_IMPORT_MAX_ENTRIES", value)
    with pytest.raises(ImportConfigurationError) as caught:
        load_import_settings(env_path=tmp_path / "missing.env")
    assert str(tmp_path) not in str(caught.value)


@pytest.mark.parametrize("overrides", [{"max_samples": True}, {"renew_seconds": float("nan")},
                                       {"renew_seconds": 16.0}, {"max_annotation_bytes": 100_000_000}])
def test_strict_finite_policy(overrides):
    with pytest.raises(ValidationError):
        ImportPolicy(**overrides)


def test_roots_must_be_absolute_and_disjoint(tmp_path):
    for root, assets in [(Path("relative"), tmp_path), (tmp_path, tmp_path), (tmp_path, tmp_path / "assets")]:
        with pytest.raises(ValidationError):
            ImportSettings(root=root, asset_root=assets, policy=ImportPolicy())
