"""Detection evidence service with a fake detector; no weights, datasets or downloads."""

import copy
import hashlib
import json
import subprocess
import sys
from math import inf, nan
from pathlib import Path

import pytest
import torch
from PIL import Image
from pydantic import ValidationError

from backend.app.inference.person_detector import MODEL_NAME, InferenceError, WeightsLoadError
from backend.app.models.detection import DetectionResult, PersonDetection
from backend.app.models.detection_evidence import (
    DetectionEvidence,
    EvidenceStatus,
    ExcludedAnnotation,
    ModelIdentity,
    UnavailableReason,
)
from backend.app.services import detection_evidence
from backend.app.services.detection_evidence import DetectionEvidenceService

LABELS = ("person",)


class FakeDetector:
    """Returns fixed detections like the real detector and records each score cutoff."""

    def __init__(self, detections=(), *, error=None, model_name="fake-architecture"):
        self.detections = list(detections)
        self.error = error
        self.model_name = model_name
        self.cutoffs = []

    def detect(self, image, min_score):
        self.cutoffs.append(min_score)
        if self.error:
            raise self.error
        width, height = image.size
        return DetectionResult(
            image_width=width, image_height=height, min_score=float(min_score),
            model_name=self.model_name, weights_file="fake.pth",
            detections=[d for d in self.detections if d.score >= min_score],
        )


def person(box, score=0.9):
    x_min, y_min, x_max, y_max = box
    return PersonDetection(
        x_min=float(x_min), y_min=float(y_min), x_max=float(x_max), y_max=float(y_max), score=score,
    )


def ann(object_id, bbox, label="person"):
    return {"object_id": object_id, "label": label, "bbox": bbox}


def image(size=(100, 100)):
    return Image.new("RGB", size, "white")


def service(detections=(), *, labels=LABELS, **detector_options):
    return DetectionEvidenceService(person_labels=labels, detector=FakeDetector(detections, **detector_options))


def collect(svc, annotations, *, picture=None, min_confidence=0.5, min_iou=0.5):
    return svc.collect(
        image() if picture is None else picture, annotations,
        min_confidence=min_confidence, min_iou=min_iou,
    )


# --- hand-off to the existing detector and matcher --------------------------

def test_detections_and_annotations_reach_the_matcher():
    detector = FakeDetector([person((50, 50, 60, 60)), person((0, 0, 10, 10))])
    svc = DetectionEvidenceService(person_labels=LABELS, detector=detector)
    evidence = collect(svc, [ann(1, (1, 1, 10, 10))])
    assert evidence.status == EvidenceStatus.COMPARED
    assert evidence.reason is None
    [pair] = evidence.matching.matches
    # Detection references are positions in the detector's output list.
    assert (pair.detection.index, pair.annotation.index, pair.iou) == (1, 0, 1.0)
    assert evidence.detector_output.detections[pair.detection.index].x_max == 10.0
    assert [d.index for d in evidence.matching.unmatched_detections] == [0]
    assert detector.cutoffs == [0.0]
    assert (evidence.image_width, evidence.image_height) == (100, 100)
    assert (evidence.min_confidence, evidence.min_iou) == (0.5, 0.5)
    assert evidence.person_labels == ["person"]


def test_fractional_detector_coordinates_are_preserved_in_original_image_pixels():
    svc = service([person((0.5, 0.5, 10.5, 10.5))])
    evidence = collect(svc, [ann(1, (1, 1, 10, 10))], picture=image((20, 20)), min_iou=0.8)
    box = evidence.detector_output.detections[0]
    assert (box.x_min, box.y_min, box.x_max, box.y_max) == (0.5, 0.5, 10.5, 10.5)
    assert evidence.matching.matches[0].iou == pytest.approx(90.25 / 109.75)


def test_one_pixel_annotation_boxes_are_converted_before_matching():
    # [1, 1, 1, 1] becomes [0, 0, 1, 1], so the unit detector box matches exactly.
    svc = service([person((0, 0, 1, 1))])
    evidence = collect(svc, [ann(1, (1, 1, 1, 1))], picture=image((10, 10)), min_iou=1)
    assert evidence.matching.matches[0].iou == 1.0


def test_iou_boundary_is_inclusive():
    # The annotation becomes (0, 0, 1, 2), so its IoU with the detection is exactly 0.5.
    svc = service([person((0, 0, 2, 2))])
    annotations = [ann(1, (1, 1, 1, 2))]
    assert len(collect(svc, annotations, min_iou=0.5).matching.matches) == 1
    above = collect(svc, annotations, min_iou=0.5000001)
    assert above.matching.matches == [] and len(above.matching.unmatched_detections) == 1


def test_confidence_is_applied_once_by_the_matcher_and_filtered_detections_stay_visible():
    svc = service([person((0, 0, 10, 10), 0.75), person((50, 50, 60, 60), 0.25)])
    evidence = collect(svc, [ann(1, (1, 1, 10, 10))], min_confidence=0.75)
    assert [m.detection.index for m in evidence.matching.matches] == [0]
    assert [d.index for d in evidence.matching.confidence_filtered_detections] == [1]
    assert evidence.matching.unmatched_detections == []
    # The detector itself discarded nothing, so both candidates are in its output.
    assert len(evidence.detector_output.detections) == 2


# --- empty, zero-object and unavailable evidence ----------------------------

def test_successful_inference_with_no_people_is_valid_evidence():
    evidence = collect(service([]), [ann(1, (1, 1, 10, 10))])
    assert evidence.status == EvidenceStatus.COMPARED
    assert evidence.detector_output.detections == []
    assert [a.index for a in evidence.matching.unmatched_annotations] == [0]


def test_an_empty_annotation_list_is_a_real_zero_object_sample():
    evidence = collect(service([person((0, 0, 10, 10))]), [])
    assert evidence.status == EvidenceStatus.COMPARED
    assert evidence.annotations_available is True and evidence.annotation_count == 0
    assert [d.index for d in evidence.matching.unmatched_detections] == [0]


def test_unavailable_annotations_keep_detections_without_calling_them_unmatched():
    evidence = collect(service([person((0, 0, 10, 10))]), None)
    assert evidence.status == EvidenceStatus.COMPARISON_UNAVAILABLE
    assert evidence.reason == UnavailableReason.ANNOTATIONS_UNAVAILABLE
    assert evidence.matching is None
    assert evidence.annotations_available is False and evidence.annotation_count is None
    assert len(evidence.detector_output.detections) == 1


def test_missing_weights_are_reported_not_raised(tmp_path):
    svc = DetectionEvidenceService(person_labels=LABELS, checkpoint_path=tmp_path / "missing.pth")
    evidence = collect(svc, [ann(1, (1, 1, 10, 10))])
    assert evidence.status == EvidenceStatus.DETECTOR_UNAVAILABLE
    assert evidence.reason == UnavailableReason.WEIGHTS_MISSING
    assert evidence.detector_output is None and evidence.matching is None
    identity = evidence.detector_identity
    assert (identity.architecture, identity.checkpoint_source, identity.checkpoint_sha256) == (
        MODEL_NAME, "local_checkpoint", None,
    )
    assert isinstance(svc.setup_failure, WeightsLoadError)
    assert evidence.annotation_count == 1  # provided, but nothing was compared


def test_corrupt_weights_are_unusable_and_still_identified(tmp_path):
    path = tmp_path / "corrupt.pth"
    content = b"this is not a checkpoint"
    path.write_bytes(content)
    evidence = collect(DetectionEvidenceService(person_labels=LABELS, checkpoint_path=path), [])
    assert evidence.status == EvidenceStatus.DETECTOR_UNAVAILABLE
    assert evidence.reason == UnavailableReason.WEIGHTS_UNUSABLE
    assert evidence.detector_identity.checkpoint_sha256 == hashlib.sha256(content).hexdigest()


def test_inference_failure_is_reported_without_internal_details():
    secret = "C:/Users/someone/secret.pth exploded"
    evidence = collect(service(error=InferenceError(secret)), [ann(1, (1, 1, 10, 10))])
    assert evidence.status == EvidenceStatus.INFERENCE_FAILED
    assert evidence.reason == UnavailableReason.INFERENCE_FAILED
    assert evidence.detector_output is None and evidence.matching is None
    assert "someone" not in json.dumps(evidence.model_dump(mode="json"))


def test_weights_failing_at_detection_time_are_reported_as_unavailable():
    evidence = collect(service(error=WeightsLoadError("lazy load failed")), [])
    assert evidence.status == EvidenceStatus.DETECTOR_UNAVAILABLE
    assert evidence.reason == UnavailableReason.WEIGHTS_UNUSABLE


def test_unexpected_errors_are_not_converted_into_evidence():
    with pytest.raises(RuntimeError, match="programming bug"):
        collect(service(error=RuntimeError("programming bug")), [])


# --- annotation records, identity and label scope -----------------------------

def test_invalid_annotation_records_make_the_comparison_incomplete():
    evidence = collect(service([person((0, 0, 10, 10))]), ["junk", ann(1, (1, 1, 10, 10))])
    assert evidence.status == EvidenceStatus.COMPARED_INCOMPLETE
    assert evidence.reason is None
    assert [(r.record_type, r.index, r.reason) for r in evidence.matching.invalid_records] == [
        ("annotation", 0, "malformed_record"),
    ]
    assert [(m.detection.index, m.annotation.index) for m in evidence.matching.matches] == [(0, 1)]


def test_duplicate_object_ids_remain_separate_records():
    svc = service([person((0, 0, 10, 10)), person((20, 20, 30, 30))])
    evidence = collect(svc, [ann(7, (1, 1, 10, 10)), ann(7, (21, 21, 30, 30))])
    assert [(m.annotation.index, m.annotation.object_id) for m in evidence.matching.matches] == [(0, 7), (1, 7)]


def test_ambiguity_is_left_for_review_whatever_the_confidence():
    svc = service([person((0, 0, 10, 10), 0.99), person((0, 0, 10, 10), 0.6)])
    evidence = collect(svc, [ann(1, (1, 1, 10, 10))])
    assert evidence.status == EvidenceStatus.COMPARED
    assert evidence.matching.matches == []
    [group] = evidence.matching.ambiguous_groups
    assert [d.index for d in group.detections] == [0, 1]


def test_annotations_outside_the_label_scope_are_excluded_not_unmatched():
    svc = service([person((0, 0, 10, 10)), person((20, 20, 30, 30))])
    annotations = [ann(1, (1, 1, 10, 10)), ann(2, (11, 11, 20, 20), "car"), ann(3, (21, 21, 30, 30))]
    evidence = collect(svc, annotations)
    assert [(e.index, e.object_id, e.label) for e in evidence.excluded_annotations] == [(1, 2, "car")]
    assert [(m.detection.index, m.annotation.index) for m in evidence.matching.matches] == [(0, 0), (1, 2)]
    assert evidence.matching.unmatched_annotations == []
    assert evidence.annotation_count == 3


def test_labels_are_matched_exactly_and_never_treated_as_interchangeable():
    annotations = [ann(1, (1, 1, 10, 10), "PASpersonWalking")]
    narrow = collect(service([person((0, 0, 10, 10))], labels=["person"]), annotations)
    assert [e.label for e in narrow.excluded_annotations] == ["PASpersonWalking"]
    assert narrow.matching.matches == []
    wide = collect(service([person((0, 0, 10, 10))], labels=["person", "PASpersonWalking"]), annotations)
    assert wide.excluded_annotations == [] and len(wide.matching.matches) == 1


def test_every_reference_maps_back_to_the_original_annotation_positions():
    svc = service([person((0, 0, 10, 10)), person((50, 50, 60, 60)), person((50, 50, 60, 60))])
    annotations = [
        ann(10, (1, 1, 10, 10), "car"),   # 0: outside the label scope
        ann(11, (1, 1, 10, 10)),          # 1: matches detection 0
        "junk",                           # 2: invalid
        ann(12, (51, 51, 60, 60)),        # 3: ambiguous with detections 1 and 2
        ann(13, (81, 81, 90, 90)),        # 4: unmatched
    ]
    evidence = collect(svc, annotations)
    matching = evidence.matching
    assert [(m.detection.index, m.annotation.index, m.annotation.object_id) for m in matching.matches] == [(0, 1, 11)]
    [group] = matching.ambiguous_groups
    assert ([d.index for d in group.detections], [a.index for a in group.annotations]) == ([1, 2], [3])
    assert [(p.detection.index, p.annotation.index) for p in group.pairs] == [(1, 3), (2, 3)]
    assert [(a.index, a.object_id) for a in matching.unmatched_annotations] == [(4, 13)]
    assert [(r.record_type, r.index) for r in matching.invalid_records] == [("annotation", 2)]
    assert [e.index for e in evidence.excluded_annotations] == [0]


# --- configuration and caller errors --------------------------------------------

@pytest.mark.parametrize("labels", [[], (), "person", b"person", [""], [1], [None], None, 5])
def test_person_labels_must_be_explicit(labels):
    with pytest.raises(ValueError, match="person_labels"):
        DetectionEvidenceService(person_labels=labels, detector=FakeDetector())


def test_person_labels_have_no_default():
    with pytest.raises(TypeError):
        DetectionEvidenceService(detector=FakeDetector())


def test_exactly_one_detector_source_is_required(tmp_path):
    with pytest.raises(ValueError, match="exactly one"):
        DetectionEvidenceService(person_labels=LABELS)
    with pytest.raises(ValueError, match="exactly one"):
        DetectionEvidenceService(
            person_labels=LABELS, detector=FakeDetector(), checkpoint_path=tmp_path / "weights.pth",
        )


@pytest.mark.parametrize("bad", [nan, inf, -0.1, 1.1, True, "0.5", None])
def test_invalid_min_confidence_is_rejected_before_detection(bad):
    detector = FakeDetector()
    svc = DetectionEvidenceService(person_labels=LABELS, detector=detector)
    with pytest.raises(ValueError, match="min_confidence"):
        svc.collect(image(), [], min_confidence=bad, min_iou=0.5)
    assert detector.cutoffs == []


@pytest.mark.parametrize("bad", [0, 0.0, -0.5, nan, inf, 1.1, True, "0.5", None])
def test_invalid_min_iou_is_rejected_before_detection(bad):
    detector = FakeDetector()
    svc = DetectionEvidenceService(person_labels=LABELS, detector=detector)
    with pytest.raises(ValueError, match="min_iou"):
        svc.collect(image(), [], min_confidence=0.5, min_iou=bad)
    assert detector.cutoffs == []


def test_both_thresholds_are_required():
    svc = service()
    with pytest.raises(TypeError):
        svc.collect(image(), [])
    with pytest.raises(TypeError):
        svc.collect(image(), [], min_confidence=0.5)


def test_wrong_argument_types_are_caller_errors():
    svc = service()
    with pytest.raises(TypeError, match="image"):
        svc.collect("photo.png", [], min_confidence=0.5, min_iou=0.5)
    for bad in ("boxes", b"boxes", {"object_id": 1}):
        with pytest.raises(TypeError, match="annotations"):
            svc.collect(image(), bad, min_confidence=0.5, min_iou=0.5)


# --- lifecycle, preservation and serialization ---------------------------------

def test_one_detector_instance_serves_repeated_calls():
    detector = FakeDetector([person((0, 0, 10, 10))])
    svc = DetectionEvidenceService(person_labels=LABELS, detector=detector)
    first, second = collect(svc, []), collect(svc, [])
    assert detector.cutoffs == [0.0, 0.0]
    assert first == second


def test_checkpoint_is_hashed_and_loaded_once_per_service(monkeypatch, tmp_path):
    path = tmp_path / "weights.pth"
    content = b"pretend weights"
    path.write_bytes(content)
    built, hashed = [], []
    real_hash = detection_evidence.file_sha256

    def factory(weights_path):
        built.append(weights_path)
        return FakeDetector([person((0, 0, 10, 10))])

    def counting_hash(target):
        hashed.append(target)
        return real_hash(target)

    monkeypatch.setattr(detection_evidence, "PersonDetector", factory)
    monkeypatch.setattr(detection_evidence, "file_sha256", counting_hash)
    svc = DetectionEvidenceService(person_labels=LABELS, checkpoint_path=path)
    hashes_at_setup = len(hashed)
    results = [collect(svc, []) for _ in range(3)]
    assert len(built) == 1 and len(hashed) == hashes_at_setup
    identity = results[0].detector_identity
    assert identity.checkpoint_sha256 == hashlib.sha256(content).hexdigest()
    assert (identity.checkpoint_source, identity.architecture) == ("local_checkpoint", MODEL_NAME)
    assert {r.detector_identity for r in results} == {identity}


def test_a_checkpoint_that_changes_during_loading_is_unusable(monkeypatch, tmp_path):
    path = tmp_path / "weights.pth"
    path.write_bytes(b"before")

    def factory(weights_path):
        Path(weights_path).write_bytes(b"after")
        return FakeDetector()

    monkeypatch.setattr(detection_evidence, "PersonDetector", factory)
    evidence = collect(DetectionEvidenceService(person_labels=LABELS, checkpoint_path=path), [])
    assert evidence.status == EvidenceStatus.DETECTOR_UNAVAILABLE
    assert evidence.reason == UnavailableReason.WEIGHTS_UNUSABLE


def test_no_download_is_attempted(monkeypatch, tmp_path):
    def refuse(*args, **kwargs):
        raise AssertionError("tried to download weights")

    monkeypatch.setattr(torch.hub, "load_state_dict_from_url", refuse)
    svc = DetectionEvidenceService(person_labels=LABELS, checkpoint_path=tmp_path / "missing.pth")
    assert collect(svc, []).reason == UnavailableReason.WEIGHTS_MISSING


def test_importing_the_service_does_no_model_work():
    code = (
        "import torch\n"
        "def refuse(*args, **kwargs): raise AssertionError('model work at import time')\n"
        "torch.load = refuse\n"
        "torch.hub.load_state_dict_from_url = refuse\n"
        "threads = torch.get_num_threads()\n"
        "import backend.app.services.detection_evidence\n"
        "assert torch.get_num_threads() == threads\n"
    )
    root = Path(__file__).resolve().parents[2]
    done = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_inputs_are_not_modified():
    picture = Image.effect_noise((40, 30), 50).convert("RGB")
    annotations = [ann(1, (1, 1, 10, 10)), ann(2, (1, 1, 5, 5), "car"), "junk"]
    before_image = (picture.mode, picture.size, picture.tobytes())
    before_annotations = copy.deepcopy(annotations)
    collect(service([person((0, 0, 10, 10))]), annotations, picture=picture)
    assert (picture.mode, picture.size, picture.tobytes()) == before_image
    assert annotations == before_annotations


def test_evidence_is_finite_json_without_private_paths(tmp_path):
    private = tmp_path / "private-user-folder"
    private.mkdir()
    missing = private / "missing.pth"
    results = [
        collect(DetectionEvidenceService(person_labels=LABELS, checkpoint_path=missing), [ann(1, (1, 1, 10, 10))]),
        collect(service([person((0, 0, 10, 10))]), [ann(1, (1, 1, 10, 10)), "junk"]),
        collect(service(error=InferenceError(str(missing))), None),
    ]
    for evidence in results:
        text = json.dumps(evidence.model_dump(mode="json"), allow_nan=False)
        assert str(tmp_path) not in text and "private-user-folder" not in text
        assert "Traceback" not in text


# --- model consistency -------------------------------------------------------------

def evidence_fields(**overrides):
    fields = {
        "status": EvidenceStatus.DETECTOR_UNAVAILABLE,
        "reason": UnavailableReason.WEIGHTS_MISSING,
        "image_width": 10, "image_height": 10,
        "min_confidence": 0.5, "min_iou": 0.5,
        "person_labels": ["person"],
        "detector_identity": ModelIdentity(architecture=MODEL_NAME, checkpoint_source="local_checkpoint"),
        "annotations_available": False,
        "excluded_annotations": [],
    }
    fields.update(overrides)
    return fields


def test_a_consistent_unavailable_result_is_accepted():
    assert DetectionEvidence(**evidence_fields()).detector_output is None


@pytest.mark.parametrize("overrides", [
    {"reason": None},
    {"reason": UnavailableReason.INFERENCE_FAILED},
    {"status": EvidenceStatus.INFERENCE_FAILED},
    {"status": EvidenceStatus.COMPARED},
    {"status": EvidenceStatus.COMPARISON_UNAVAILABLE, "reason": UnavailableReason.ANNOTATIONS_UNAVAILABLE},
    {"annotations_available": True},
    {"annotation_count": 3},
    {"excluded_annotations": [ExcludedAnnotation(index=0, label="car")]},
    {"min_iou": 0.0},
    {"min_confidence": nan},
    {"person_labels": []},
])
def test_inconsistent_evidence_is_rejected(overrides):
    with pytest.raises(ValidationError):
        DetectionEvidence(**evidence_fields(**overrides))


def test_model_identity_never_invents_a_checkpoint_hash():
    with pytest.raises(ValidationError):
        ModelIdentity(checkpoint_source="injected_detector", checkpoint_sha256="a" * 64)
    with pytest.raises(ValidationError):
        ModelIdentity(checkpoint_source="local_checkpoint", checkpoint_sha256="not-a-hash")
    assert ModelIdentity(checkpoint_source="local_checkpoint", checkpoint_sha256="a" * 64).architecture is None
