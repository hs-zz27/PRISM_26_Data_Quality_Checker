"""Correction-boundary validation without PostgreSQL, files or model inference."""

import json
from unittest.mock import Mock

import pytest
from pydantic import SecretStr, ValidationError

from backend.app.core.config import DatabaseSettings
from backend.app.models.correction import CorrectionRequest, RecheckRequest
from backend.app.models.dataset_import import SamplePublication
from backend.app.storage.correction_repository import CorrectionRepository, checked
from backend.tests.dataset_fixtures import prepared_sample


@pytest.mark.parametrize("field", [
    "sample_revision", "manifest_revision", "ordinal", "asset_size", "box_coordinate",
])
def test_mutated_publication_is_rejected_before_mask_storage(field, monkeypatch):
    base = prepared_sample()
    if field == "sample_revision":
        base.sample.revision = True
    elif field == "manifest_revision":
        base.manifest = base.manifest.model_copy(update={"sample_revision": True})
    elif field == "ordinal":
        base.ordinal = True
    elif field == "asset_size":
        entry = base.manifest.original_image.model_copy(update={"size_bytes": True})
        base.manifest = base.manifest.model_copy(update={"original_image": entry})
    else:
        base.sample.objects[0].bbox = (True, 5, -8, 0)

    repository = CorrectionRepository(
        settings=DatabaseSettings(conninfo=SecretStr("dbname=unused")),
    )
    connection = Mock(side_effect=AssertionError("Invalid inputs must not open storage."))
    monkeypatch.setattr(repository, "_connection", connection)

    with pytest.raises(ValidationError):
        repository.mask("mask_" + "a" * 32, base)

    connection.assert_not_called()


def test_checked_live_publication_detaches_nested_models_and_containers():
    original = prepared_sample()

    snapshot = checked(SamplePublication, original)

    assert snapshot == original
    assert snapshot is not original
    assert snapshot.sample is not original.sample
    assert snapshot.sample.objects[0] is not original.sample.objects[0]
    assert snapshot.manifest is not original.manifest
    assert snapshot.manifest.original_image is not original.manifest.original_image
    original.sample.objects[0].label = "changed after validation"
    assert snapshot.sample.objects[0].label == ""


def test_checked_stored_publication_still_decodes_json_box_arrays():
    original = prepared_sample()
    document = json.loads(original.model_dump_json())

    snapshot = checked(SamplePublication, document)

    assert snapshot == original
    assert type(snapshot.sample.objects[0].bbox) is tuple
    document["sample"]["revision"] = True
    with pytest.raises(ValidationError):
        checked(SamplePublication, document)


@pytest.mark.parametrize("field", ["expected_revision", "object_id", "box_coordinate"])
def test_checked_live_correction_rejects_mutations_without_serialization(field):
    request = CorrectionRequest(
        expected_revision=1, reviewer_id="reviewer", reason="Correct annotations.",
        objects=[{"object_id": 1, "label": "person", "bbox": (1, 1, 4, 3)}],
    )
    if field == "expected_revision":
        request.expected_revision = True
    elif field == "object_id":
        request.objects[0].object_id = True
    else:
        request.objects[0].bbox = (True, 1, 4, 3)

    with pytest.raises(ValidationError):
        checked(CorrectionRequest, request)


def test_checked_recheck_rejects_boolean_revision_in_mutated_mapping():
    request = RecheckRequest(
        sample_ids=["sample"], sample_revisions={"sample": 1}, reviewer_id="reviewer",
    )
    request.sample_revisions["sample"] = True

    with pytest.raises(ValidationError):
        checked(RecheckRequest, request)
