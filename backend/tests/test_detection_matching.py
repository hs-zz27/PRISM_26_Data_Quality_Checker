"""Detection-to-annotation matching on synthetic boxes; no images, weights or dataset."""

import copy
import json
from math import inf, nan

import numpy as np
import pytest

from backend.app.models.detection import PersonDetection
from backend.app.models.detection_matching import (
    MATCHING_COORDINATE_CONVENTION,
    MATCHING_POLICY,
)
from backend.app.services.detection_matching import match_detections_to_annotations

SIZE = (100, 100)


def det(box, score=0.9):
    return {"bbox": box, "score": score}


def ann(object_id, bbox, label="person"):
    return {"object_id": object_id, "label": label, "bbox": bbox}


def match(detections, annotations, *, size=SIZE, min_confidence=0.5, min_iou=0.5):
    return match_detections_to_annotations(
        detections, annotations, size, min_confidence=min_confidence, min_iou=min_iou,
    )


def matched_pairs(result):
    return [(pair.detection.index, pair.annotation.index) for pair in result.matches]


def assert_nothing_reported(result, *, except_for=()):
    lists = {
        "matches", "ambiguous_groups", "unmatched_detections", "unmatched_annotations",
        "confidence_filtered_detections", "invalid_records",
    }
    for name in lists - set(except_for):
        assert getattr(result, name) == [], name


# --- geometry -------------------------------------------------------------

def test_identical_boxes_have_iou_one():
    result = match([det((10, 10, 50, 60))], [ann(1, (11, 11, 50, 60))])
    assert matched_pairs(result) == [(0, 0)]
    assert result.matches[0].iou == 1.0


def test_disjoint_boxes_are_not_eligible():
    result = match([det((0, 0, 10, 10))], [ann(1, (21, 21, 30, 30))], min_iou=0.01)
    assert result.matches == []
    assert [d.index for d in result.unmatched_detections] == [0]
    assert [a.index for a in result.unmatched_annotations] == [0]


def test_edge_touching_boxes_have_zero_intersection():
    # The annotation becomes (10, 0, 20, 10); it shares only the line x = 10.
    result = match([det((0, 0, 10, 10))], [ann(1, (11, 1, 20, 10))], min_iou=0.01)
    assert result.matches == [] and result.ambiguous_groups == []
    assert len(result.unmatched_detections) == 1 and len(result.unmatched_annotations) == 1


def test_partial_overlap_iou():
    # The annotation becomes (5, 0, 15, 10): intersection 50, union 150.
    result = match([det((0, 0, 10, 10))], [ann(1, (6, 1, 15, 10))], min_iou=0.3)
    assert result.matches[0].iou == pytest.approx(1 / 3)


def test_contained_box_iou():
    # The annotation becomes (0, 0, 5, 5): intersection 25, union 100.
    result = match([det((0, 0, 10, 10))], [ann(1, (1, 1, 5, 5))], min_iou=0.2)
    assert result.matches[0].iou == 0.25


def test_fractional_detector_coordinates_are_preserved():
    result = match([det((0.5, 0.5, 10.5, 10.5))], [ann(1, (1, 1, 10, 10))], min_iou=0.8)
    assert result.matches[0].iou == pytest.approx(90.25 / 109.75)


def test_one_pixel_annotation_converts_to_a_unit_box():
    # [1, 1, 1, 1] becomes [0, 0, 1, 1].
    exact = match([det((0, 0, 1, 1))], [ann(1, (1, 1, 1, 1))], size=(10, 10), min_iou=1)
    assert exact.matches[0].iou == 1.0
    # The neighbouring pixel [1, 1, 2, 2] only touches that unit box.
    neighbour = match([det((1, 1, 2, 2))], [ann(1, (1, 1, 1, 1))], size=(10, 10), min_iou=0.01)
    assert neighbour.matches == []


def test_two_pixel_annotation_converts_to_a_two_unit_box():
    result = match([det((0, 0, 2, 2))], [ann(1, (1, 1, 2, 2))], size=(10, 10), min_iou=1)
    assert matched_pairs(result) == [(0, 0)]


def test_boxes_may_span_the_whole_image():
    result = match([det((0, 0, 10, 10))], [ann(1, (1, 1, 10, 10))], size=(10, 10))
    assert result.matches[0].iou == 1.0
    assert result.invalid_records == []


# --- thresholds -----------------------------------------------------------

def test_iou_exactly_at_the_threshold_is_eligible():
    # The annotation becomes (0, 0, 1, 2): intersection 2, union 4, IoU 0.5.
    boxes = ([det((0, 0, 2, 2))], [ann(1, (1, 1, 1, 2))])
    assert matched_pairs(match(*boxes, min_iou=0.5)) == [(0, 0)]
    above = match(*boxes, min_iou=0.5000001)
    assert above.matches == [] and len(above.unmatched_detections) == 1


def test_confidence_exactly_at_the_threshold_is_considered():
    boxes = [ann(1, (1, 1, 10, 10))]
    kept = match([det((0, 0, 10, 10), 0.75)], boxes, min_confidence=0.75)
    assert matched_pairs(kept) == [(0, 0)] and kept.confidence_filtered_detections == []
    dropped = match([det((0, 0, 10, 10), 0.75)], boxes, min_confidence=0.76)
    assert [d.index for d in dropped.confidence_filtered_detections] == [0]


def test_min_iou_of_one_accepts_only_identical_boxes():
    result = match([det((0, 0, 10, 9))], [ann(1, (1, 1, 10, 10))], min_iou=1)
    assert result.matches == [] and result.min_iou == 1.0


# --- empty and filtered inputs --------------------------------------------

def test_both_lists_empty():
    result = match([], [])
    assert_nothing_reported(result)
    assert result.comparison_complete is True


def test_only_detections():
    result = match([det((0, 0, 10, 10)), det((20, 20, 30, 30))], [])
    assert [d.index for d in result.unmatched_detections] == [0, 1]
    assert_nothing_reported(result, except_for=["unmatched_detections"])


def test_only_annotations():
    result = match([], [ann(1, (1, 1, 10, 10)), ann(2, (21, 21, 30, 30))])
    assert [a.index for a in result.unmatched_annotations] == [0, 1]
    assert_nothing_reported(result, except_for=["unmatched_annotations"])


def test_all_detections_excluded_by_confidence():
    result = match(
        [det((0, 0, 10, 10), 0.2), det((20, 20, 30, 30), 0.3)], [ann(1, (1, 1, 10, 10))],
    )
    assert [d.index for d in result.confidence_filtered_detections] == [0, 1]
    # Excluded detections are not unmatched detections, and they are not compared.
    assert result.unmatched_detections == [] and result.matches == []
    assert [a.index for a in result.unmatched_annotations] == [0]


# --- ambiguity ------------------------------------------------------------

def test_one_detection_eligible_for_two_annotations_is_ambiguous():
    # The second annotation becomes (0, 0, 10, 5): IoU 0.5 with the detection.
    result = match([det((0, 0, 10, 10))], [ann(1, (1, 1, 10, 10)), ann(2, (1, 1, 10, 5))])
    assert result.matches == []
    [group] = result.ambiguous_groups
    assert [d.index for d in group.detections] == [0]
    assert [a.index for a in group.annotations] == [0, 1]
    assert [(p.detection.index, p.annotation.index) for p in group.pairs] == [(0, 0), (0, 1)]
    assert [p.iou for p in group.pairs] == [1.0, 0.5]
    assert result.unmatched_detections == [] and result.unmatched_annotations == []


def test_two_detections_eligible_for_one_annotation_is_ambiguous():
    result = match([det((0, 0, 10, 10)), det((0, 0, 10, 5))], [ann(1, (1, 1, 10, 10))])
    assert result.matches == []
    [group] = result.ambiguous_groups
    assert [d.index for d in group.detections] == [0, 1]
    assert [a.index for a in group.annotations] == [0]
    assert [(p.detection.index, p.annotation.index) for p in group.pairs] == [(0, 0), (1, 0)]


def test_two_by_two_component_is_one_ambiguous_group_whatever_the_confidence():
    result = match(
        [det((0, 0, 10, 10), 0.99), det((0, 0, 10, 9), 0.6)],
        [ann(1, (1, 1, 10, 10)), ann(2, (1, 1, 10, 9))],
    )
    assert result.matches == []
    [group] = result.ambiguous_groups
    assert [d.index for d in group.detections] == [0, 1]
    assert [a.index for a in group.annotations] == [0, 1]
    assert [(p.detection.index, p.annotation.index) for p in group.pairs] == [(0, 0), (0, 1), (1, 0), (1, 1)]
    assert [p.iou for p in group.pairs] == pytest.approx([1.0, 0.9, 0.9, 1.0])


def test_duplicate_detections_are_kept_and_make_the_group_ambiguous():
    result = match(
        [det((0, 0, 10, 10), 0.9), det((0, 0, 10, 10), 0.8)], [ann(1, (1, 1, 10, 10))],
    )
    assert result.matches == []
    [group] = result.ambiguous_groups
    assert [(d.index, d.score) for d in group.detections] == [(0, 0.9), (1, 0.8)]


def test_duplicate_annotation_object_ids_stay_separate_records():
    result = match(
        [det((0, 0, 10, 10)), det((20, 20, 30, 30))],
        [ann(7, (1, 1, 10, 10)), ann(7, (21, 21, 30, 30))],
    )
    assert matched_pairs(result) == [(0, 0), (1, 1)]
    assert [(m.annotation.index, m.annotation.object_id) for m in result.matches] == [(0, 7), (1, 7)]


def test_annotation_labels_are_preserved():
    result = match([det((0, 0, 10, 10))], [ann(1, (1, 1, 10, 10), label="PASpersonWalking")])
    assert result.matches[0].annotation.label == "PASpersonWalking"


# --- invalid records ------------------------------------------------------

VALID_BOX = (0, 0, 10, 10)


@pytest.mark.parametrize("record,reason", [
    ("not a record", "malformed_record"),
    (None, "malformed_record"),
    ({"score": 0.9}, "invalid_box"),
    (det(None), "invalid_box"),
    (det((1, 2, 3)), "invalid_box"),
    (det("abcd"), "invalid_box"),
    (det((50, 0, 20, 10)), "invalid_box"),
    (det((0, 50, 10, 20)), "invalid_box"),
    (det((0, 0, nan, 10)), "invalid_box"),
    (det((0, 0, inf, 10)), "invalid_box"),
    (det((0, 0, True, 10)), "invalid_box"),
    (det(("0", 0, 10, 10)), "invalid_box"),
    (det((5, 0, 5, 10)), "zero_area_box"),
    (det((0, 5, 10, 5)), "zero_area_box"),
    (det((0, 0, 101, 10)), "box_outside_image"),
    (det((-1, 0, 10, 10)), "box_outside_image"),
    (det((0, 0, 10, 100.5)), "box_outside_image"),
    ({"bbox": VALID_BOX}, "invalid_score"),
    (det(VALID_BOX, nan), "invalid_score"),
    (det(VALID_BOX, inf), "invalid_score"),
    (det(VALID_BOX, -0.1), "invalid_score"),
    (det(VALID_BOX, 1.5), "invalid_score"),
    (det(VALID_BOX, None), "invalid_score"),
    (det(VALID_BOX, True), "invalid_score"),
    (det(VALID_BOX, "0.9"), "invalid_score"),
])
def test_invalid_detection_is_reported_not_compared(record, reason):
    result = match([record], [ann(1, (1, 1, 10, 10))])
    [problem] = result.invalid_records
    assert (problem.record_type, problem.index, problem.reason) == ("detection", 0, reason)
    assert problem.detail
    assert result.comparison_complete is False
    # The invalid record never becomes a zero-IoU measurement or any other category.
    assert result.matches == [] and result.ambiguous_groups == []
    assert result.unmatched_detections == [] and result.confidence_filtered_detections == []
    assert [a.index for a in result.unmatched_annotations] == [0]


GOOD_BOX = (1, 1, 10, 10)


@pytest.mark.parametrize("record,reason,object_id", [
    ("junk", "malformed_record", None),
    (None, "malformed_record", None),
    ({"label": "p", "bbox": GOOD_BOX}, "invalid_object_id", None),
    (ann(True, GOOD_BOX), "invalid_object_id", None),
    (ann("1", GOOD_BOX), "invalid_object_id", None),
    (ann(4, GOOD_BOX, label=5), "invalid_label", 4),
    (ann(4, GOOD_BOX, label=None), "invalid_label", 4),
    ({"object_id": 4, "label": "p"}, "invalid_box", 4),
    (ann(4, None), "invalid_box", 4),
    (ann(4, (1, 1, 2)), "invalid_box", 4),
    (ann(4, (1.0, 1, 2, 2)), "invalid_box", 4),
    (ann(4, (True, 1, 2, 2)), "invalid_box", 4),
    (ann(4, (3, 1, 2, 2)), "invalid_box", 4),
    (ann(4, (0, 1, 2, 2)), "box_outside_image", 4),
    (ann(4, (1, 1, 101, 2)), "box_outside_image", 4),
])
def test_invalid_annotation_is_reported_not_compared(record, reason, object_id):
    result = match([det(VALID_BOX)], [record])
    [problem] = result.invalid_records
    assert (problem.record_type, problem.index, problem.reason) == ("annotation", 0, reason)
    assert problem.object_id == object_id
    assert result.comparison_complete is False
    assert result.matches == [] and result.unmatched_annotations == []
    assert [d.index for d in result.unmatched_detections] == [0]


def test_valid_records_are_still_compared_when_others_are_invalid():
    result = match([det(VALID_BOX), det(None)], [ann(1, GOOD_BOX), "junk"])
    assert matched_pairs(result) == [(0, 0)]
    assert [(p.record_type, p.index) for p in result.invalid_records] == [("detection", 1), ("annotation", 1)]
    assert result.comparison_complete is False


def test_a_low_confidence_record_with_an_invalid_box_is_invalid_not_filtered():
    result = match([det((0, 0, 200, 10), 0.1)], [])
    assert [p.reason for p in result.invalid_records] == ["box_outside_image"]
    assert result.confidence_filtered_detections == []


def test_person_detection_objects_are_accepted():
    detection = PersonDetection(x_min=0.5, y_min=0.5, x_max=10.5, y_max=10.5, score=0.9)
    result = match([detection], [ann(1, (1, 1, 10, 10))], size=(20, 20), min_iou=0.8)
    assert result.matches[0].iou == pytest.approx(90.25 / 109.75)


def test_person_detection_outside_this_image_is_invalid_here():
    detection = PersonDetection(x_min=0, y_min=0, x_max=21, y_max=10, score=0.9)
    result = match([detection], [], size=(20, 20))
    assert [p.reason for p in result.invalid_records] == ["box_outside_image"]


# --- caller errors --------------------------------------------------------

@pytest.mark.parametrize("bad", [nan, inf, -inf, -0.1, 1.1, True, "0.5", None])
def test_invalid_min_confidence_is_rejected(bad):
    with pytest.raises(ValueError, match="min_confidence"):
        match([], [], min_confidence=bad)


@pytest.mark.parametrize("bad", [0, 0.0, -0.5, nan, inf, 1.1, True, "0.5", None])
def test_invalid_min_iou_is_rejected(bad):
    with pytest.raises(ValueError, match="min_iou"):
        match([], [], min_iou=bad)


@pytest.mark.parametrize("bad", [
    (0, 10), (10, 0), (10, -1), (True, 10), (10.0, 10), (10,), (10, 10, 10), "ab", None, 10,
])
def test_invalid_image_size_is_rejected(bad):
    with pytest.raises(ValueError, match="image"):
        match([], [], size=bad)


@pytest.mark.parametrize("bad", [None, "boxes", b"boxes", {"bbox": 1}, iter([])])
def test_record_lists_must_be_sequences(bad):
    with pytest.raises(TypeError, match="detections"):
        match(bad, [])
    with pytest.raises(TypeError, match="annotations"):
        match([], bad)


def test_both_thresholds_are_required():
    with pytest.raises(TypeError):
        match_detections_to_annotations([], [], SIZE)
    with pytest.raises(TypeError):
        match_detections_to_annotations([], [], SIZE, min_iou=0.5)


# --- result properties ----------------------------------------------------

def mixed_inputs():
    detections = [
        det((0, 0, 10, 10)),                      # 0: matches annotation 0
        det((50, 50, 60, 60)),                    # 1: nothing nearby
        det((20, 20, 30, 30)),                    # 2: eligible for annotations 1 and 2
        det((70, 70, 80, 80), 0.3),               # 3: below the confidence minimum
        det(None),                                # 4: invalid
    ]
    annotations = [
        ann(1, (1, 1, 10, 10)),                   # 0
        ann(2, (21, 21, 30, 30)),                 # 1
        ann(3, (21, 21, 30, 25)),                 # 2: IoU 0.5 with detection 2
        ann(4, (81, 81, 90, 90)),                 # 3: only a filtered detection is near
        "junk",                                   # 4: invalid
    ]
    return detections, annotations


def test_every_record_lands_in_exactly_one_category():
    detections, annotations = mixed_inputs()
    result = match(detections, annotations)

    detection_groups = {
        "matched": [m.detection.index for m in result.matches],
        "ambiguous": [d.index for g in result.ambiguous_groups for d in g.detections],
        "unmatched": [d.index for d in result.unmatched_detections],
        "filtered": [d.index for d in result.confidence_filtered_detections],
        "invalid": [p.index for p in result.invalid_records if p.record_type == "detection"],
    }
    assert detection_groups == {
        "matched": [0], "ambiguous": [2], "unmatched": [1], "filtered": [3], "invalid": [4],
    }
    annotation_groups = {
        "matched": [m.annotation.index for m in result.matches],
        "ambiguous": [a.index for g in result.ambiguous_groups for a in g.annotations],
        "unmatched": [a.index for a in result.unmatched_annotations],
        "invalid": [p.index for p in result.invalid_records if p.record_type == "annotation"],
    }
    assert annotation_groups == {"matched": [0], "ambiguous": [1, 2], "unmatched": [3], "invalid": [4]}

    every_detection = sorted(i for indexes in detection_groups.values() for i in indexes)
    every_annotation = sorted(i for indexes in annotation_groups.values() for i in indexes)
    assert every_detection == list(range(len(detections)))
    assert every_annotation == list(range(len(annotations)))


def test_result_reports_the_conventions_and_thresholds_used():
    result = match([], [], size=(640, 480), min_confidence=0.4, min_iou=0.3)
    assert result.coordinate_convention == MATCHING_COORDINATE_CONVENTION
    assert result.policy == MATCHING_POLICY
    assert (result.image_width, result.image_height) == (640, 480)
    assert (result.min_confidence, result.min_iou) == (0.4, 0.3)
    assert "unmatched" in result.interpretation.lower()


def test_ordering_is_deterministic_and_follows_input_order():
    detections = [det((0, 0, 10, 10)), det((20, 0, 30, 10)), det((40, 0, 50, 10))]
    annotations = [ann(1, (41, 1, 50, 10)), ann(2, (21, 1, 30, 10)), ann(3, (1, 1, 10, 10))]
    first = match(detections, annotations)
    assert matched_pairs(first) == [(0, 2), (1, 1), (2, 0)]
    assert match(detections, annotations) == first


def test_ambiguous_groups_are_ordered_by_their_lowest_detection_index():
    detections = [det((50, 50, 60, 60)), det((0, 0, 10, 10)), det((50, 50, 60, 60)), det((0, 0, 10, 10))]
    annotations = [ann(1, (1, 1, 10, 10)), ann(2, (51, 51, 60, 60))]
    result = match(detections, annotations)
    assert [[d.index for d in g.detections] for g in result.ambiguous_groups] == [[0, 2], [1, 3]]
    assert [[a.index for a in g.annotations] for g in result.ambiguous_groups] == [[1], [0]]


def test_inputs_are_not_modified():
    detections, annotations = mixed_inputs()
    detections.append(PersonDetection(x_min=1, y_min=1, x_max=5, y_max=5, score=0.7))
    before = copy.deepcopy((detections, annotations))
    match(detections, annotations)
    assert (detections, annotations) == before


def test_result_is_finite_json_with_plain_python_values():
    detections, annotations = mixed_inputs()
    result = match(detections, annotations)
    data = result.model_dump(mode="json")
    assert json.loads(json.dumps(data, allow_nan=False)) == data


def test_numpy_inputs_become_plain_python_values():
    detection = det((np.float32(0), np.float32(0), np.float64(10), np.float64(10)), np.float32(0.75))
    annotation = ann(np.int64(3), (np.int64(1), np.int64(1), np.int64(10), np.int64(10)))
    result = match([detection], [annotation], size=(np.int64(100), np.int64(100)))
    [pair] = result.matches
    assert type(pair.iou) is float and type(pair.detection.score) is float
    assert type(pair.annotation.object_id) is int
    assert type(result.image_width) is int