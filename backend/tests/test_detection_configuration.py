"""Frozen detector configuration, server settings and the saved-request seam."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.core.config import AuditConfigurationError
from backend.app.core.person_detection import (
    DETECTOR_ARCHITECTURE,
    DetectionConfigurationError,
    current_detector_configuration,
    freeze_detector_configuration,
    load_checkpoint_location,
    load_detection_settings,
)
from backend.app.inference.person_detector import MODEL_NAME
from backend.app.models.audit import BatchAuditRequest
from backend.app.models.audit_submission import AuditRequest
from backend.app.models.detection_evidence import (
    DetectionEvidence,
    DetectionNotRun,
    DetectorConfiguration,
    EvidenceStatus,
    ModelIdentity,
    PreprocessingIdentity,
    UnavailableReason,
)
from backend.app.models.detection_matching import MATCHING_POLICY
from backend.tests.test_audit_submission import submission  # noqa: F401 - reuse its fixture

SHA = "a" * 64
SETTING_NAMES = (
    "DETECTION_ENABLED", "DETECTION_CHECKPOINT_PATH", "DETECTION_CHECKPOINT_SHA256",
    "DETECTION_PERSON_LABELS", "DETECTION_MIN_CONFIDENCE", "DETECTION_MIN_IOU",
)


def enabled_configuration(**changes):
    values = dict(
        enabled=True, architecture=MODEL_NAME, checkpoint_sha256=SHA, min_confidence=0.5, min_iou=0.4,
        person_labels=["PASperson"], matching_policy=MATCHING_POLICY, evidence_schema_version=1,
        preprocessing=PreprocessingIdentity(),
    )
    return DetectorConfiguration(**(values | changes))


@pytest.fixture
def clean_environment(monkeypatch):
    for name in SETTING_NAMES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def write_env(tmp_path, **settings):
    path = tmp_path / ".env"
    path.write_text("\n".join(f"{name}={value}" for name, value in settings.items()), encoding="utf-8")
    return path


FULL_SETTINGS = dict(
    DETECTION_ENABLED="true", DETECTION_CHECKPOINT_PATH="/private/weights.pth",
    DETECTION_CHECKPOINT_SHA256=SHA, DETECTION_PERSON_LABELS="PASperson, person",
    DETECTION_MIN_CONFIDENCE="0.5", DETECTION_MIN_IOU="0.4",
)


# --- the frozen configuration model ---------------------------------------------------

def test_architecture_constant_matches_the_detector():
    assert DETECTOR_ARCHITECTURE == MODEL_NAME


def test_a_disabled_configuration_holds_no_other_settings():
    assert DetectorConfiguration(enabled=False).person_labels is None
    with pytest.raises(ValidationError):
        DetectorConfiguration(enabled=False, min_iou=0.5)


@pytest.mark.parametrize("missing", [
    "architecture", "checkpoint_sha256", "min_confidence", "min_iou", "person_labels",
    "matching_policy", "evidence_schema_version", "preprocessing",
])
def test_an_enabled_configuration_must_set_everything(missing):
    with pytest.raises(ValidationError):
        enabled_configuration(**{missing: None})


@pytest.mark.parametrize("changes", [
    {"checkpoint_sha256": "not-a-hash"}, {"checkpoint_sha256": "A" * 64},
    {"min_confidence": 1.5}, {"min_confidence": float("nan")},
    {"min_iou": 0.0}, {"min_iou": float("inf")},
    {"person_labels": []}, {"person_labels": [""]}, {"person_labels": ["a", "a"]},
    {"enabled": "yes"},
])
def test_invalid_configurations_are_rejected(changes):
    with pytest.raises(ValidationError):
        enabled_configuration(**changes)


def test_configuration_round_trips_through_json_and_python_dumps():
    configuration = enabled_configuration()
    assert DetectorConfiguration.model_validate_json(json.dumps(configuration.model_dump(mode="json"))) == configuration
    assert DetectorConfiguration.model_validate(configuration.model_dump()) == configuration


# --- server settings -----------------------------------------------------------------------

def test_detection_is_off_unless_explicitly_enabled(clean_environment, tmp_path):
    settings = load_detection_settings(env_path=tmp_path / "missing.env")
    assert settings.enabled is False
    assert freeze_detector_configuration(settings) == DetectorConfiguration(enabled=False)


def test_enabled_settings_are_read_and_frozen_without_the_checkpoint_path(clean_environment, tmp_path):
    configuration = current_detector_configuration(env_path=write_env(tmp_path, **FULL_SETTINGS))
    assert configuration.enabled is True
    assert configuration.person_labels == ["PASperson", "person"]
    assert (configuration.min_confidence, configuration.min_iou) == (0.5, 0.4)
    assert configuration.architecture == MODEL_NAME and configuration.matching_policy == MATCHING_POLICY
    assert "/private" not in json.dumps(configuration.model_dump(mode="json"))


def test_environment_overrides_the_env_file(clean_environment, tmp_path):
    clean_environment.setenv("DETECTION_ENABLED", "false")
    assert load_detection_settings(env_path=write_env(tmp_path, **FULL_SETTINGS)).enabled is False


def test_checkpoint_hash_is_normalized_to_lowercase(clean_environment, tmp_path):
    settings = FULL_SETTINGS | {"DETECTION_CHECKPOINT_SHA256": "A" * 64}
    assert current_detector_configuration(env_path=write_env(tmp_path, **settings)).checkpoint_sha256 == SHA


@pytest.mark.parametrize("name", [
    "DETECTION_CHECKPOINT_PATH", "DETECTION_CHECKPOINT_SHA256", "DETECTION_PERSON_LABELS",
    "DETECTION_MIN_CONFIDENCE", "DETECTION_MIN_IOU",
])
def test_enabled_without_a_required_setting_is_a_configuration_error(clean_environment, tmp_path, name):
    settings = {key: value for key, value in FULL_SETTINGS.items() if key != name}
    with pytest.raises(DetectionConfigurationError, match=name):
        load_detection_settings(env_path=write_env(tmp_path, **settings))


@pytest.mark.parametrize("changes", [
    {"DETECTION_ENABLED": "maybe"},
    {"DETECTION_MIN_CONFIDENCE": "high"},
    {"DETECTION_MIN_IOU": "abc"},
])
def test_unreadable_settings_are_configuration_errors(clean_environment, tmp_path, changes):
    with pytest.raises(DetectionConfigurationError):
        load_detection_settings(env_path=write_env(tmp_path, **(FULL_SETTINGS | changes)))


@pytest.mark.parametrize("changes", [
    {"DETECTION_MIN_CONFIDENCE": "1.5"}, {"DETECTION_MIN_IOU": "0"},
    {"DETECTION_MIN_CONFIDENCE": "nan"}, {"DETECTION_CHECKPOINT_SHA256": "xyz"},
])
def test_out_of_range_settings_are_rejected_when_frozen(clean_environment, tmp_path, changes):
    with pytest.raises(DetectionConfigurationError):
        current_detector_configuration(env_path=write_env(tmp_path, **(FULL_SETTINGS | changes)))


def test_configuration_errors_use_the_existing_safe_503_mapping():
    assert issubclass(DetectionConfigurationError, AuditConfigurationError)


def test_checkpoint_location_is_read_without_the_other_settings(clean_environment, tmp_path):
    path = write_env(tmp_path, DETECTION_CHECKPOINT_PATH="/private/weights.pth", DETECTION_MIN_IOU="junk")
    assert load_checkpoint_location(env_path=path) == Path("/private/weights.pth")
    assert load_checkpoint_location(env_path=tmp_path / "missing.env") is None


# --- the saved request and the recheck clone -----------------------------------------------------

def legacy_request(**changes):
    values = dict(
        dataset_id="dataset_a", dataset_root="", rulebook_id="book", rulebook_version=1,
        sample_ids=["s1"], sample_revisions={"s1": 1}, source_mode="snapshot",
    )
    return BatchAuditRequest(**(values | changes))


def test_requests_saved_before_the_feature_have_no_detector():
    old_document = legacy_request().model_dump(mode="json")
    old_document.pop("detector")
    assert BatchAuditRequest.model_validate(old_document).detector is None


def test_the_detector_configuration_survives_json_storage_and_revalidation():
    request = legacy_request(detector=enabled_configuration())
    stored = json.loads(json.dumps(request.model_dump(mode="json"), allow_nan=False))
    assert BatchAuditRequest.model_validate(stored).detector == request.detector
    assert BatchAuditRequest.model_validate(request.model_dump()).detector == request.detector


def test_a_recheck_clone_keeps_the_parents_frozen_configuration():
    parent = legacy_request(detector=enabled_configuration(min_iou=0.3))
    # The same construction the recheck repository uses for the child request.
    child = BatchAuditRequest.model_validate({
        **parent.model_dump(), "dataset_root": "", "sample_ids": ["s1"],
        "sample_revisions": {"s1": 2}, "source_mode": "snapshot",
    })
    assert child.detector == parent.detector and child.sample_revisions == {"s1": 2}


def test_a_recheck_of_a_legacy_parent_stays_legacy():
    parent = legacy_request()
    child = BatchAuditRequest.model_validate({**parent.model_dump(), "sample_revisions": {"s1": 2}})
    assert child.detector is None


def test_the_public_audit_request_rejects_detector_settings():
    with pytest.raises(ValidationError):
        AuditRequest.model_validate(dict(
            dataset_id="dataset_a", rulebook_id="book", rulebook_version=1,
            detector={"enabled": True},
        ))


# --- audit submission freezes the settings when the audit is accepted ----------------------------

def test_submission_freezes_the_configuration_given_at_acceptance(submission):  # noqa: F811
    service, _, _, audits, _ = submission
    configuration = enabled_configuration()
    service.detector_configuration = lambda: configuration
    service.start(AuditRequest(dataset_id="dataset_a", rulebook_id="book", rulebook_version=1))
    assert audits.start_audit.call_args.args[0].detector == configuration


def test_submission_reads_the_environment_when_each_audit_is_accepted(submission, clean_environment):  # noqa: F811
    service, _, _, audits, _ = submission
    clean_environment.setenv("DETECTION_ENABLED", "false")
    service.start(AuditRequest(dataset_id="dataset_a", rulebook_id="book", rulebook_version=1))
    first = audits.start_audit.call_args.args[0]
    assert first.detector == DetectorConfiguration(enabled=False)

    for name, value in FULL_SETTINGS.items():
        clean_environment.setenv(name, value)
    service.start(AuditRequest(dataset_id="dataset_a", rulebook_id="book", rulebook_version=1))
    second = audits.start_audit.call_args.args[0]
    assert second.detector.enabled is True and second.detector.person_labels == ["PASperson", "person"]
    # The audit accepted earlier keeps what it froze.
    assert first.detector == DetectorConfiguration(enabled=False)


def test_invalid_settings_stop_an_audit_before_anything_is_queued(submission, clean_environment):  # noqa: F811
    service, _, _, audits, _ = submission
    clean_environment.setenv("DETECTION_ENABLED", "true")
    with pytest.raises(DetectionConfigurationError):
        service.start(AuditRequest(dataset_id="dataset_a", rulebook_id="book", rulebook_version=1))
    audits.start_audit.assert_not_called()


# --- evidence models -----------------------------------------------------------------------------------

def test_not_run_evidence_matches_its_configuration():
    disabled = DetectionNotRun(status="disabled", configuration=DetectorConfiguration(enabled=False))
    assert disabled.evidence_schema_version == 1
    DetectionNotRun(status="image_unavailable", configuration=enabled_configuration())
    with pytest.raises(ValidationError):
        DetectionNotRun(status="disabled", configuration=enabled_configuration())
    with pytest.raises(ValidationError):
        DetectionNotRun(status="image_unavailable", configuration=DetectorConfiguration(enabled=False))


def test_evidence_records_its_schema_version_and_preprocessing():
    evidence = DetectionEvidence(
        status=EvidenceStatus.DETECTOR_UNAVAILABLE, reason=UnavailableReason.CHECKPOINT_MISMATCH,
        image_width=10, image_height=10, min_confidence=0.5, min_iou=0.5, person_labels=["person"],
        detector_identity=ModelIdentity(
            architecture=MODEL_NAME, checkpoint_source="local_checkpoint",
            checkpoint_sha256=SHA, expected_checkpoint_sha256="b" * 64,
        ),
        annotations_available=False, excluded_annotations=[],
    )
    dumped = evidence.model_dump(mode="json")
    assert dumped["evidence_schema_version"] == 1
    assert dumped["preprocessing"]["model_min_size"] == 320 and dumped["preprocessing"]["model_max_size"] == 640
    assert dumped["preprocessing"]["box_coordinates"] == "original image pixels"
    json.dumps(dumped, allow_nan=False)


def test_an_injected_detector_cannot_claim_an_expected_hash():
    with pytest.raises(ValidationError):
        ModelIdentity(checkpoint_source="injected_detector", expected_checkpoint_sha256=SHA)


# --- the API process stays light ------------------------------------------------------------------------------

def test_importing_audit_submission_does_not_import_torch():
    code = (
        "import sys\n"
        "import backend.app.services.audit_submission\n"
        "import backend.app.services.batch_audit\n"
        "assert 'torch' not in sys.modules, 'torch was imported by the audit API modules'\n"
    )
    root = Path(__file__).resolve().parents[2]
    done = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
