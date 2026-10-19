import json

import numpy as np
import pytest
from PIL import Image

from backend.app.models.finding import FindingOutcome
from backend.app.models.rule import ConfirmationState, Rule, Rulebook
from backend.app.services.audit import audit_sample
from backend.app.services.evaluation import evaluate_rules


@pytest.fixture
def sample_root(tmp_path):
    for folder in ("PNGImages", "Annotation", "PedMasks"):
        (tmp_path / folder).mkdir()
    Image.new("RGB", (4, 3)).save(tmp_path / "PNGImages/sample.png")
    mask = np.zeros((3, 4), dtype=np.uint8)
    mask[:2, :2] = 1
    mask[2, 3] = 2
    Image.fromarray(mask).save(tmp_path / "PedMasks/sample_mask.png")
    (tmp_path / "Annotation/sample.txt").write_text(
        'Image size (X x Y x C) : 4 x 3 x 3\n'
        'Objects with ground truth : 2 { "person" "person" }\n'
        '# Top left pixel co-ordinates : (1, 1)\n'
        'Bounding box for object 1 "person" (Xmin, Ymin) - (Xmax, Ymax) : (1, 1) - (2, 2)\n'
        'Bounding box for object 2 "person" (Xmin, Ymin) - (Xmax, Ymax) : (4, 3) - (4, 3)\n'
    )
    return tmp_path


def test_complete_sample_keeps_all_objects_pending_review(sample_root):
    report = audit_sample(sample_root, "sample", (label for label in ["person"]))
    assert report["status"] == "completed" and report["review_status"] == "pending_review"
    assert report["errors"] == [] and len(report["objects"]) == 2
    assert report["checks"]["mask_ids"]["result"]["matches"]
    assert report["checks"]["box_overlaps"]["result"]["pairs"][0]["iou"] == 0
    for obj in report["objects"]:
        checks = obj["checks"]
        assert all(check["status"] == "completed" for check in checks.values())
        assert checks["box_mask_alignment"]["result"]["max_edge_difference"] == 0
        assert checks["mask_box_fill"]["result"]["fill_fraction"] == 1
    json.dumps(report)


def test_actual_check_failure_isolated_from_independent_label_check(sample_root, monkeypatch):
    def fail_dimensions(*args, **kwargs):
        raise ValueError("synthetic dimensions implementation failure")

    monkeypatch.setattr("backend.app.services.audit.check_mask_dimensions", fail_dimensions)
    report = audit_sample(sample_root, "sample", ["person"])

    assert report["status"] == "incomplete"
    assert report["checks"]["mask_dimensions"] == {
        "status": "unavailable", "reason": "check_execution_failed",
        "detail": None, "result": None,
    }
    assert report["checks"]["labels"]["status"] == "completed"
    findings = evaluate_rules(
        Rulebook(
            rulebook_id="book_actual_check", version=1, guideline_text="checks",
            confirmation_state=ConfirmationState.CONFIRMED,
            rules=[
                Rule(
                    rule_id="dimensions", description="Dimensions match.",
                    check_name="mask_dimensions", origin="reviewer",
                    confirmation_state=ConfirmationState.CONFIRMED,
                ),
                Rule(
                    rule_id="labels", description="Labels are allowed.",
                    check_name="allowed_labels", parameters={"allowed_labels": ["person"]},
                    origin="reviewer", confirmation_state=ConfirmationState.CONFIRMED,
                ),
            ],
        ),
        report, audit_id="audit_actual_check", sample_id="sample", sample_revision=1,
    )
    assert findings[0].outcome is FindingOutcome.UNAVAILABLE
    assert all(finding.outcome is FindingOutcome.PASS for finding in findings[1:])
    assert findings[0].reason == "check_execution_failed"


def test_collection_without_shared_labels_keeps_geometry_measurements(sample_root):
    report = audit_sample(sample_root, "sample", None)
    assert report["status"] == "completed"
    assert "labels" not in report["checks"]
    assert all("label" not in obj["checks"] for obj in report["objects"])
    assert report["checks"]["box_overlaps"]["status"] == "completed"


@pytest.mark.parametrize("error", [MemoryError("out of memory"), RuntimeError("unsafe shared state"), OSError("I/O failure")])
def test_resource_and_unclassified_check_failures_are_fatal(sample_root, monkeypatch, error):
    def fail_dimensions(*args, **kwargs):
        raise error

    monkeypatch.setattr("backend.app.services.audit.check_mask_dimensions", fail_dimensions)
    with pytest.raises(type(error)):
        audit_sample(sample_root, "sample", ["person"])


def test_failed_check_cannot_mutate_annotations_used_by_independent_checks(sample_root, monkeypatch):
    def mutate_annotations(objects, mask):
        objects[0]["object_id"] = 999
        objects[0]["bbox"] = (100, 100, 200, 200)
        raise ValueError("check failed after modifying its private input")

    monkeypatch.setattr("backend.app.services.audit.check_mask_ids", mutate_annotations)
    report = audit_sample(sample_root, "sample", ["person"])

    assert report["checks"]["mask_ids"]["reason"] == "check_execution_failed"
    assert report["checks"]["annotation_ids"]["result"]["valid"] is True
    assert report["objects"][0]["annotation"]["object_id"] == 1
    assert report["objects"][0]["checks"]["box_validity"]["result"]["valid"] is True


def test_failed_check_cannot_modify_shared_mask_pixels(sample_root, monkeypatch):
    def mutate_mask(image, mask):
        mask[:] = 0

    monkeypatch.setattr("backend.app.services.audit.check_mask_dimensions", mutate_mask)
    report = audit_sample(sample_root, "sample", ["person"])

    assert report["checks"]["mask_dimensions"]["reason"] == "check_execution_failed"
    assert report["checks"]["mask_ids"]["result"]["matches"] is True
    assert report["objects"][0]["checks"]["mask_components"]["result"]["component_count"] == 1


@pytest.mark.parametrize("file,reason,independent_checks", [
    ("PNGImages/sample.png", "image_load_failed", ["labels", "annotation_ids", "mask_ids"]),
    ("Annotation/sample.txt", "annotation_load_failed", ["mask_dimensions"]),
    ("PedMasks/sample_mask.png", "mask_load_failed", ["labels", "box_overlaps", "annotation_metadata"]),
])
def test_missing_files_preserve_independent_checks(sample_root, file, reason, independent_checks, caplog):
    (sample_root / file).unlink()
    report = audit_sample(sample_root, "sample", ["person"])
    assert report["status"] == "incomplete" and report["errors"][0]["stage"] == "load"
    assert report["errors"][0]["reason"] == reason
    assert report["errors"][0]["detail"] == "The source asset could not be loaded."
    assert str(sample_root) not in json.dumps(report)
    assert str(sample_root / file) in caplog.text
    for name in independent_checks:
        assert report["checks"][name]["status"] == "completed"
    assert report["checks"]["mask_dimensions"]["status"] == (
        "completed" if reason == "annotation_load_failed" else "unavailable"
    )
    if reason == "mask_load_failed":
        for row in report["objects"]:
            assert row["checks"]["box_validity"]["result"]["valid"]
            assert row["checks"]["mask_components"]["reason"] == reason
    if reason == "image_load_failed":
        assert report["objects"][0]["checks"]["mask_components"]["status"] == "completed"
        assert report["objects"][0]["checks"]["box_validity"]["reason"] == reason
    assert report["review_status"] == "pending_review"


def test_corrupt_image_is_reported(sample_root):
    (sample_root / "PNGImages/sample.png").write_bytes(b"broken image")
    report = audit_sample(sample_root, "sample", ["person"])
    assert report["errors"][0]["reason"] == "image_load_failed"
    assert report["checks"]["labels"]["status"] == "completed"


def test_malformed_annotation_is_reported(sample_root):
    (sample_root / "Annotation/sample.txt").write_text("Bounding box for object broken\n")
    report = audit_sample(sample_root, "sample", ["person"])
    assert report["status"] == "incomplete" and report["annotation_text"]
    assert report["errors"][0]["reason"] == "annotation_parse_failed"
    assert report["checks"]["mask_dimensions"]["result"]["matches"]


@pytest.mark.parametrize("text", [
    "", "not an annotation", "Objects with ground truth : 2 {}",
    "Objects with ground truth : 0 {}\nObjects with ground truth : 0 {}",
])
def test_unusable_annotation_cannot_pass_a_label_only_audit(sample_root, text):
    (sample_root / "Annotation/sample.txt").write_text(text)
    report = audit_sample(sample_root, "sample", None)
    book = Rulebook(
        rulebook_id="book_labels", version=1, guideline_text="Use allowed labels.",
        confirmation_state="confirmed",
        rules=[Rule(
            rule_id="labels", description="Use allowed labels.", check_name="allowed_labels",
            parameters={"allowed_labels": ["person"]}, origin="reviewer",
            confirmation_state="confirmed",
        )],
    )

    findings = evaluate_rules(book, report, audit_id="audit", sample_id="sample", sample_revision=1)

    assert report["status"] == "incomplete"
    assert report["checks"]["mask_dimensions"]["result"]["matches"]
    assert report["errors"][0]["reason"] == "annotation_parse_failed"
    assert len(findings) == 1 and findings[0].outcome is FindingOutcome.UNAVAILABLE
    assert findings[0].review.resolution == "pending"


def test_dimension_mismatch_blocks_only_dependent_comparisons(sample_root):
    Image.fromarray(np.ones((2, 2), dtype=np.uint8)).save(sample_root / "PedMasks/sample_mask.png")
    report = audit_sample(sample_root, "sample", ["person"])
    assert not report["checks"]["mask_dimensions"]["result"]["matches"]
    assert report["checks"]["labels"]["status"] == "completed"
    for obj in report["objects"]:
        checks = obj["checks"]
        for name in ("box_mask_alignment", "mask_outside_box", "mask_box_fill"):
            assert checks[name]["status"] == "unavailable"
            assert checks[name]["reason"] == "dimensions_mismatch"
    assert report["objects"][0]["checks"]["mask_components"]["status"] == "completed"


def test_rgb_mask_does_not_block_labels_and_box_geometry(sample_root):
    Image.new("RGB", (4, 3)).save(sample_root / "PedMasks/sample_mask.png")
    report = audit_sample(sample_root, "sample", ["person"])
    assert report["checks"]["mask_dimensions"]["reason"] == "invalid_mask"
    assert report["checks"]["box_overlaps"]["status"] == "completed"
    for obj in report["objects"]:
        assert obj["checks"]["label"]["status"] == "completed"
        assert obj["checks"]["mask_components"]["reason"] == "invalid_mask"


def test_missing_instance_and_extra_mask_id_remain_visible(sample_root):
    mask = np.zeros((3, 4), dtype=np.uint8)
    mask[:2, :2] = 1
    mask[2, 3] = 3
    Image.fromarray(mask).save(sample_root / "PedMasks/sample_mask.png")
    report = audit_sample(sample_root, "sample", ["person"])
    ids = report["checks"]["mask_ids"]["result"]
    assert ids["missing_masks"] == [2] and ids["missing_annotations"] == [3]
    assert report["objects"][0]["checks"]["mask_box_fill"]["status"] == "completed"
    assert report["objects"][1]["checks"]["mask_box_fill"]["reason"] == "missing_mask"


@pytest.mark.parametrize("box", ["(2, 1) - (1, 2)", "(0, 1) - (2, 2)"])
def test_invalid_box_retains_other_objects_and_independent_checks(sample_root, box):
    path = sample_root / "Annotation/sample.txt"
    path.write_text(path.read_text().replace("(1, 1) - (2, 2)", box))
    report = audit_sample(sample_root, "sample", ["person"])
    checks = report["objects"][0]["checks"]
    assert not checks["box_validity"]["result"]["valid"]
    assert checks["mask_box_fill"]["status"] == "unavailable"
    assert checks["mask_components"]["status"] == "completed"
    assert report["objects"][1]["checks"]["mask_box_fill"]["status"] == "completed"


@pytest.mark.parametrize("replacement, reason", [("1", "duplicate_object_id"), ("0", "invalid_object_id")])
def test_ambiguous_or_background_annotation_id_blocks_mask_measurements(sample_root, replacement, reason):
    path = sample_root / "Annotation/sample.txt"
    path.write_text(path.read_text().replace("object 2", f"object {replacement}"))
    report = audit_sample(sample_root, "sample", ["person"])
    assert not report["checks"]["annotation_ids"]["result"]["valid"]
    assert report["objects"][1]["checks"]["mask_components"]["reason"] == reason


def test_label_mismatch_remains_a_completed_check_not_a_final_rejection(sample_root):
    report = audit_sample(sample_root, "sample", ["vehicle"])
    assert not report["checks"]["labels"]["result"]["matches"]
    assert report["checks"]["labels"]["status"] == "completed"
    assert report["review_status"] == "pending_review"


def test_no_annotations_still_reports_unannotated_mask_ids(sample_root):
    (sample_root / "Annotation/sample.txt").write_text("Objects with ground truth : 0 {}")
    report = audit_sample(sample_root, "sample", ["person"])
    assert report["objects"] == [] and report["status"] == "completed"
    assert report["checks"]["mask_ids"]["result"]["missing_annotations"] == [1, 2]


@pytest.mark.parametrize("allowed, connectivity", [([], 8), (["person"], 6), (["person"], 8.0)])
def test_bad_caller_configuration_raises(sample_root, allowed, connectivity):
    with pytest.raises(ValueError):
        audit_sample(sample_root, "sample", allowed, connectivity)


def test_metadata_checks_declared_size_count_and_origin(sample_root):
    report = audit_sample(sample_root, "sample", ["person"])
    metadata = report["checks"]["annotation_metadata"]
    assert metadata["status"] == "completed"
    assert metadata["result"]["matches"] and metadata["result"]["issues"] == {}


@pytest.mark.parametrize("old,new,field,reason", [
    ("4 x 3 x 3", "5 x 3 x 3", "image_size", "image_size_mismatch"),
    ("ground truth : 2", "ground truth : 7", "object_count", "object_count_mismatch"),
    ("ground truth : 2", "ground truth : broken", "object_count", "malformed_metadata"),
])
def test_metadata_errors_preserve_valid_annotations(sample_root, old, new, field, reason):
    path = sample_root / "Annotation/sample.txt"
    path.write_text(path.read_text().replace(old, new))
    report = audit_sample(sample_root, "sample", ["person"])
    assert len(report["objects"]) == 2
    assert report["checks"]["annotation_metadata"]["result"]["issues"][field] == reason
    assert report["checks"]["labels"]["result"]["matches"]
    assert report["checks"]["mask_ids"]["result"]["matches"]


@pytest.mark.parametrize("header,reason", [
    ("", "coordinate_origin_unavailable"),
    ("# Top left pixel co-ordinates : invalid", "coordinate_origin_unavailable"),
    ("# Top left pixel co-ordinates : (0, 0)", "unsupported_coordinate_origin"),
])
def test_unusable_origin_blocks_only_coordinate_checks(sample_root, header, reason):
    path = sample_root / "Annotation/sample.txt"
    path.write_text(path.read_text().replace("# Top left pixel co-ordinates : (1, 1)", header))
    report = audit_sample(sample_root, "sample", ["person"])
    assert report["checks"]["box_overlaps"]["reason"] == reason
    assert report["checks"]["mask_dimensions"]["result"]["matches"]
    for row in report["objects"]:
        for name in ("box_validity", "box_mask_alignment", "mask_outside_box", "mask_box_fill"):
            assert row["checks"][name]["reason"] == reason
        assert row["checks"]["mask_components"]["status"] == "completed"
        assert row["checks"]["label"]["status"] == "completed"


def test_metadata_compares_original_image_channels(sample_root):
    Image.new("L", (4, 3)).save(sample_root / "PNGImages/sample.png")
    result = audit_sample(sample_root, "sample", ["person"])["checks"]["annotation_metadata"]["result"]
    assert result["actual_image_size"] == (4, 3, 1)
    assert result["issues"]["image_size"] == "image_size_mismatch"


def test_corrupt_mask_keeps_annotation_and_image_checks(sample_root):
    (sample_root / "PedMasks/sample_mask.png").write_bytes(b"broken mask")
    report = audit_sample(sample_root, "sample", ["person"])
    assert report["status"] == "incomplete"
    assert report["checks"]["labels"]["result"]["matches"]
    assert report["checks"]["box_overlaps"]["status"] == "completed"
    assert report["checks"]["mask_ids"]["reason"] == "mask_load_failed"


def test_all_missing_inputs_return_all_loading_errors(tmp_path):
    report = audit_sample(tmp_path, "missing", ["person"])
    assert report["status"] == "incomplete" and report["objects"] == []
    assert {error["reason"] for error in report["errors"]} == {
        "image_load_failed", "annotation_load_failed", "mask_load_failed",
    }
    assert all(check["status"] == "unavailable" for check in report["checks"].values())
