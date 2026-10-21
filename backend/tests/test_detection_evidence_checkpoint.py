"""Expected-checkpoint verification in the detection evidence service."""

import hashlib

import pytest

from backend.app.inference.person_detector import MODEL_NAME
from backend.app.models.detection_evidence import EvidenceStatus, UnavailableReason
from backend.app.services import detection_evidence
from backend.app.services.detection_evidence import DetectionEvidenceService
from PIL import Image

CONTENT = b"pretend weights"
GOOD_HASH = hashlib.sha256(CONTENT).hexdigest()


class Detector:
    def detect(self, image, min_score):
        raise AssertionError("not needed")


@pytest.fixture
def weights(tmp_path):
    path = tmp_path / "weights.pth"
    path.write_bytes(CONTENT)
    return path


def collect(service):
    return service.collect(Image.new("RGB", (20, 20)), [], min_confidence=0.5, min_iou=0.5)


def test_a_matching_expected_hash_loads_the_checkpoint(weights, monkeypatch):
    built = []
    monkeypatch.setattr(detection_evidence, "PersonDetector", lambda path: built.append(path) or Detector())
    service = DetectionEvidenceService(
        person_labels=["person"], checkpoint_path=weights, expected_checkpoint_sha256=GOOD_HASH,
    )
    assert built == [weights] and service.setup_failure is None


def test_a_different_checkpoint_is_refused_before_loading(weights, monkeypatch):
    def refuse(path):
        raise AssertionError("must not load a checkpoint that differs from the expected one")

    monkeypatch.setattr(detection_evidence, "PersonDetector", refuse)
    service = DetectionEvidenceService(
        person_labels=["person"], checkpoint_path=weights, expected_checkpoint_sha256="b" * 64,
    )
    evidence = collect(service)
    assert evidence.status == EvidenceStatus.DETECTOR_UNAVAILABLE
    assert evidence.reason == UnavailableReason.CHECKPOINT_MISMATCH
    identity = evidence.detector_identity
    assert (identity.architecture, identity.checkpoint_sha256, identity.expected_checkpoint_sha256) == (
        MODEL_NAME, GOOD_HASH, "b" * 64,
    )
    assert service.setup_failure is not None


def test_a_missing_file_with_an_expected_hash_is_missing_not_mismatched(tmp_path):
    service = DetectionEvidenceService(
        person_labels=["person"], checkpoint_path=tmp_path / "missing.pth", expected_checkpoint_sha256=GOOD_HASH,
    )
    evidence = collect(service)
    assert evidence.reason == UnavailableReason.WEIGHTS_MISSING
    assert evidence.detector_identity.checkpoint_sha256 is None
    assert evidence.detector_identity.expected_checkpoint_sha256 == GOOD_HASH


def test_the_expected_hash_applies_only_to_a_local_checkpoint():
    with pytest.raises(ValueError, match="expected_checkpoint_sha256"):
        DetectionEvidenceService(person_labels=["person"], detector=Detector(), expected_checkpoint_sha256=GOOD_HASH)


@pytest.mark.parametrize("bad", ["", "abc", "A" * 64, "g" * 64, 5])
def test_the_expected_hash_must_be_lowercase_sha256(weights, bad):
    with pytest.raises(ValueError, match="expected_checkpoint_sha256"):
        DetectionEvidenceService(person_labels=["person"], checkpoint_path=weights, expected_checkpoint_sha256=bad)


def test_without_an_expected_hash_behavior_is_unchanged(weights, monkeypatch):
    built = []
    monkeypatch.setattr(detection_evidence, "PersonDetector", lambda path: built.append(path) or Detector())
    DetectionEvidenceService(person_labels=["person"], checkpoint_path=weights)
    assert built == [weights]
