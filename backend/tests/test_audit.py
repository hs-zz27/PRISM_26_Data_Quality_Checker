import json

import numpy as np
import pytest
from PIL import Image

from backend.app.services.audit import audit_sample


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


@pytest.mark.parametrize("file", ["PNGImages/sample.png", "Annotation/sample.txt", "PedMasks/sample_mask.png"])
def test_missing_files_report_loading_failure(sample_root, file):
    (sample_root / file).unlink()
    report = audit_sample(sample_root, "sample", ["person"])
    assert report["status"] == "incomplete" and report["errors"][0]["stage"] == "load"
    assert all(check["reason"] == "sample_load_failed" for check in report["checks"].values())
    assert report["review_status"] == "pending_review"


def test_corrupt_image_is_reported(sample_root):
    (sample_root / "PNGImages/sample.png").write_bytes(b"broken image")
    report = audit_sample(sample_root, "sample", ["person"])
    assert report["errors"][0]["reason"] == "sample_load_failed"


def test_malformed_annotation_is_reported(sample_root):
    (sample_root / "Annotation/sample.txt").write_text("Bounding box for object broken\n")
    report = audit_sample(sample_root, "sample", ["person"])
    assert report["status"] == "incomplete" and report["annotation_text"]
    assert report["errors"][0]["reason"] == "annotation_parse_failed"


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
    (sample_root / "Annotation/sample.txt").write_text("")
    report = audit_sample(sample_root, "sample", ["person"])
    assert report["objects"] == [] and report["status"] == "completed"
    assert report["checks"]["mask_ids"]["result"]["missing_annotations"] == [1, 2]


@pytest.mark.parametrize("allowed, connectivity", [(None, 8), ([], 8), (["person"], 6), (["person"], 8.0)])
def test_bad_caller_configuration_raises(sample_root, allowed, connectivity):
    with pytest.raises(ValueError):
        audit_sample(sample_root, "sample", allowed, connectivity)
