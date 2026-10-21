"""Detection evidence inside audit collection: synthetic samples, fake detectors, no weights."""

import copy
import hashlib
import json
import pickle
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from backend.app.inference.person_detector import MODEL_NAME
from backend.app.models.detection import DetectionResult, PersonDetection
from backend.app.models.detection_evidence import (
    DetectorConfiguration,
    PreprocessingIdentity,
)
from backend.app.models.detection_matching import MATCHING_POLICY
from backend.app.services import detection_audit, detection_evidence
from backend.app.services.audit import audit_sample
from backend.app.services.detection_audit import (
    DetectionAwareCollector,
    add_detection_evidence,
    clear_detector_cache,
    collect_detection_evidence,
)
from backend.app.services.detection_evidence import DetectionEvidenceService
from backend.app.storage.workflow_repository import _validate_measurement_document

LABEL = "PASperson"
SHA = "a" * 64
BOX = (10.0, 10.0, 50.0, 70.0)  # equals the annotation below after its one-based conversion


class FakeDetector:
    def __init__(self, detections=None, error=None):
        self.detections = [PersonDetection(
            x_min=BOX[0], y_min=BOX[1], x_max=BOX[2], y_max=BOX[3], score=0.9,
        )] if detections is None else detections
        self.error = error
        self.calls = 0

    def detect(self, image, min_score):
        self.calls += 1
        if self.error:
            raise self.error
        width, height = image.size
        return DetectionResult(
            image_width=width, image_height=height, min_score=float(min_score), model_name="fake",
            weights_file="fake.pth", detections=[d for d in self.detections if d.score >= min_score],
        )


def configuration(**changes):
    values = dict(
        enabled=True, architecture=MODEL_NAME, checkpoint_sha256=SHA, min_confidence=0.5, min_iou=0.5,
        person_labels=[LABEL], matching_policy=MATCHING_POLICY, evidence_schema_version=1,
        preprocessing=PreprocessingIdentity(),
    )
    return DetectorConfiguration(**(values | changes))


def fake_service(detector=None):
    """A `service_for` that drives the real evidence service with a fake detector."""
    detector = FakeDetector() if detector is None else detector
    return lambda config: DetectionEvidenceService(person_labels=config.person_labels, detector=detector)


def write_sample(root, image_id="s1", *, annotation="valid", mask=True, image=True, origin="(1, 1)"):
    root = Path(root)
    for directory in ("PNGImages", "PedMasks", "Annotation"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    if image:
        Image.new("RGB", (100, 80), "white").save(root / "PNGImages" / f"{image_id}.png")
    if mask:
        pixels = np.zeros((80, 100), dtype=np.uint16)
        pixels[10:70, 10:50] = 1
        Image.fromarray(pixels).save(root / "PedMasks" / f"{image_id}_mask.png")
    if annotation == "valid":
        text = (
            "Image size (X x Y x C) : 100 x 80 x 3\n"
            f'Objects with ground truth : 1 {{ "{LABEL}" }}\n'
            f"Top left pixel co-ordinates : {origin}\n"
            f'Bounding box for object 1 "{LABEL}" (Xmin, Ymin) - (Xmax, Ymax) : (11, 11) - (50, 70)\n'
        )
        (root / "Annotation" / f"{image_id}.txt").write_text(text, encoding="utf-8")
    elif annotation == "garbage":
        (root / "Annotation" / f"{image_id}.txt").write_text("not an annotation file", encoding="utf-8")
    return root


def measurements(image_id="s1", **changes):
    document = {
        "image_id": image_id, "image_size": [100, 80], "annotation_text": "text", "allowed_labels": [],
        "status": "completed", "review_status": "pending_review", "errors": [], "checks": {}, "objects": [],
    }
    return document | changes


@pytest.fixture(autouse=True)
def fresh_detector_cache(monkeypatch):
    for name in ("DETECTION_CHECKPOINT_PATH", "DETECTION_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    clear_detector_cache()
    yield
    clear_detector_cache()


# --- evidence for one materialized sample ---------------------------------------------------------

def test_evidence_is_added_and_other_measurements_are_untouched(tmp_path):
    root = write_sample(tmp_path)
    base = measurements()
    before = copy.deepcopy(base)
    result = add_detection_evidence(base, root, "s1", configuration(), service_for=fake_service())
    assert base == before  # the collector's own result is never modified
    assert {key: value for key, value in result.items() if key != "detection_evidence"} == before
    evidence = result["detection_evidence"]
    assert evidence["status"] == "compared" and evidence["matching"]["comparison_complete"] is True
    [pair] = evidence["matching"]["matches"]
    assert (pair["annotation"]["object_id"], pair["annotation"]["label"], pair["iou"]) == (1, LABEL, 1.0)
    assert evidence["evidence_schema_version"] == 1 and evidence["person_labels"] == [LABEL]


def test_detection_still_runs_when_annotations_are_missing(tmp_path):
    root = write_sample(tmp_path, annotation=None)
    result = add_detection_evidence(measurements(), root, "s1", configuration(), service_for=fake_service())
    evidence = result["detection_evidence"]
    assert evidence["status"] == "comparison_unavailable" and evidence["reason"] == "annotations_unavailable"
    assert len(evidence["detector_output"]["detections"]) == 1 and evidence["matching"] is None
    assert result["status"] == "incomplete"
    assert result["errors"][-1]["stage"] == "detection"
    assert result["errors"][-1]["reason"] == "detection_annotations_unavailable"


def test_detection_still_runs_when_annotations_cannot_be_parsed(tmp_path):
    root = write_sample(tmp_path, annotation="garbage")
    evidence = collect_detection_evidence(root, "s1", configuration(), service_for=fake_service())
    assert evidence.status == "comparison_unavailable" and len(evidence.detector_output.detections) == 1


def test_an_unsupported_coordinate_origin_blocks_comparison_only(tmp_path):
    root = write_sample(tmp_path, origin="(0, 0)")
    evidence = collect_detection_evidence(root, "s1", configuration(), service_for=fake_service())
    assert evidence.status == "comparison_unavailable"
    assert len(evidence.detector_output.detections) == 1


def test_a_missing_mask_does_not_block_detection_or_matching(tmp_path):
    root = write_sample(tmp_path, mask=False)
    evidence = collect_detection_evidence(root, "s1", configuration(), service_for=fake_service())
    assert evidence.status == "compared" and len(evidence.matching.matches) == 1


def test_a_valid_empty_annotation_is_not_unavailable(tmp_path):
    root = write_sample(tmp_path, annotation=None)
    (root / "Annotation" / "s1.txt").write_text(
        "Image size (X x Y x C) : 100 x 80 x 3\n"
        'Objects with ground truth : 0 { "PASperson" }\n'
        "Top left pixel co-ordinates : (1, 1)\n", encoding="utf-8",
    )
    evidence = collect_detection_evidence(root, "s1", configuration(), service_for=fake_service())
    assert evidence.status == "compared" and evidence.annotation_count == 0
    assert [d.index for d in evidence.matching.unmatched_detections] == [0]


def test_an_unreadable_image_is_recorded_without_raising(tmp_path):
    root = write_sample(tmp_path, image=False)
    evidence = collect_detection_evidence(root, "s1", configuration(), service_for=fake_service())
    assert (evidence.status, evidence.configuration.enabled) == ("image_unavailable", True)
    result = add_detection_evidence(measurements(), root, "s1", configuration(), service_for=fake_service())
    assert result["detection_evidence"]["status"] == "image_unavailable"
    assert result["status"] == "completed"  # the collector's own load error already marks the sample


def test_disabled_detection_records_that_it_was_switched_off_and_reads_nothing(tmp_path):
    def refuse(config):
        raise AssertionError("a disabled configuration must not build a detector")

    result = add_detection_evidence(
        measurements(), tmp_path / "does-not-exist", "s1", DetectorConfiguration(enabled=False), service_for=refuse,
    )
    assert result["detection_evidence"]["status"] == "disabled"
    assert result["status"] == "completed" and result["errors"] == []


def test_inference_failure_makes_the_sample_incomplete_without_losing_other_measurements(tmp_path):
    from backend.app.inference.person_detector import InferenceError

    root = write_sample(tmp_path)
    service = fake_service(FakeDetector(error=InferenceError("secret /private/path")))
    base = measurements(checks={"labels": {"status": "completed"}})
    result = add_detection_evidence(base, root, "s1", configuration(), service_for=service)
    assert result["detection_evidence"]["status"] == "inference_failed"
    assert result["status"] == "incomplete" and result["checks"] == base["checks"]
    assert result["errors"][-1]["reason"] == "detection_inference_failed"
    assert "/private" not in json.dumps(result)


def test_unexpected_programming_errors_are_not_hidden(tmp_path):
    root = write_sample(tmp_path)
    with pytest.raises(RuntimeError, match="bug"):
        add_detection_evidence(
            measurements(), root, "s1", configuration(), service_for=fake_service(FakeDetector(error=RuntimeError("bug"))),
        )


# --- worker-local checkpoint resolution and verification ------------------------------------------------------

def test_an_unset_checkpoint_location_is_reported_as_a_missing_checkpoint(tmp_path):
    root = write_sample(tmp_path)
    result = add_detection_evidence(measurements(), root, "s1", configuration())
    assert result["detection_evidence"]["status"] == "detector_unavailable"
    assert result["detection_evidence"]["reason"] == "weights_missing"
    assert result["status"] == "incomplete"
    assert result["errors"][-1]["reason"] == "detection_weights_missing"


def test_a_different_checkpoint_than_the_frozen_one_is_never_loaded(tmp_path, monkeypatch):
    root = write_sample(tmp_path)
    weights = tmp_path / "other.pth"
    weights.write_bytes(b"some other weights")

    def refuse(path):
        raise AssertionError("a mismatching checkpoint must not be loaded")

    monkeypatch.setenv("DETECTION_CHECKPOINT_PATH", str(weights))
    monkeypatch.setattr(detection_evidence, "PersonDetector", refuse)
    result = add_detection_evidence(measurements(), root, "s1", configuration())
    evidence = result["detection_evidence"]
    assert evidence["status"] == "detector_unavailable" and evidence["reason"] == "checkpoint_mismatch"
    assert evidence["detector_identity"]["checkpoint_sha256"] == hashlib.sha256(b"some other weights").hexdigest()
    assert evidence["detector_identity"]["expected_checkpoint_sha256"] == SHA
    assert result["status"] == "incomplete"
    text = json.dumps(result)
    assert str(tmp_path) not in text and weights.name not in text  # JSON escapes Windows backslashes


def test_a_matching_checkpoint_is_loaded_once_and_reused_across_samples(tmp_path, monkeypatch):
    weights = tmp_path / "weights.pth"
    weights.write_bytes(b"pretend weights")
    frozen = configuration(checkpoint_sha256=hashlib.sha256(b"pretend weights").hexdigest())
    built = []
    detector = FakeDetector()

    def factory(path):
        built.append(path)
        return detector

    monkeypatch.setenv("DETECTION_CHECKPOINT_PATH", str(weights))
    monkeypatch.setattr(detection_evidence, "PersonDetector", factory)
    results = []
    for image_id in ("s1", "s2", "s3"):
        root = write_sample(tmp_path / image_id, image_id)
        results.append(collect_detection_evidence(root, image_id, frozen))
    assert len(built) == 1 and detector.calls == 3
    assert {r.detector_identity.checkpoint_sha256 for r in results} == {frozen.checkpoint_sha256}
    assert all(r.status == "compared" for r in results)


# --- spawn safety ------------------------------------------------------------------

def test_the_collector_pickles_with_only_plain_configuration():
    collector = DetectionAwareCollector(configuration())
    restored = pickle.loads(pickle.dumps(collector))
    assert restored.configuration == collector.configuration and restored.base_collector is audit_sample
    assert set(vars(restored)) == {"configuration", "base_collector"}


# --- storage and read path compatibility -------------------------------------------------------------------------------

def test_stored_measurement_documents_accept_the_evidence_key_and_older_documents(tmp_path):
    root = write_sample(tmp_path)
    document = add_detection_evidence(measurements(), root, "s1", configuration(), service_for=fake_service())
    assert _validate_measurement_document(document, "s1")["detection_evidence"] == document["detection_evidence"]
    older = measurements()
    assert "detection_evidence" not in _validate_measurement_document(older, "s1")


def test_evidence_documents_are_finite_json_without_private_paths(tmp_path, monkeypatch):
    private = tmp_path / "private-user-folder"
    private.mkdir()
    monkeypatch.setenv("DETECTION_CHECKPOINT_PATH", str(private / "missing.pth"))
    root = write_sample(tmp_path / "sample")
    documents = [
        add_detection_evidence(measurements(), root, "s1", configuration()),
        add_detection_evidence(measurements(), root, "s1", configuration(), service_for=fake_service()),
        add_detection_evidence(measurements(), root, "s1", DetectorConfiguration(enabled=False)),
    ]
    for document in documents:
        text = json.dumps(document, allow_nan=False)
        assert str(tmp_path) not in text and "private-user-folder" not in text


def test_reading_stored_evidence_needs_no_detector(tmp_path):
    root = write_sample(tmp_path)
    document = add_detection_evidence(measurements(), root, "s1", configuration(), service_for=fake_service())
    stored = json.loads(json.dumps(document))
    # The read path only validates and returns the stored document; nothing is built or run.
    assert _validate_measurement_document(stored, "s1") == stored
    assert detection_audit._services == {}
