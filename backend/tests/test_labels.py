import pytest

from backend.app.checks.labels import check_allowed_labels


def test_label_check_uses_supplied_values_and_preserves_all_results():
    objects = [
        {"object_id": 1, "label": "vehicle"},
        {"object_id": 2, "label": "Vehicle"},
        {"object_id": 3, "label": "vehicle "},
        {"object_id": 4},
        {"object_id": 5, "label": []},
    ]
    result = check_allowed_labels(objects, ["vehicle", "animal"])
    assert not result["matches"]
    assert result["invalid_object_ids"] == [2, 3, 4, 5]
    assert [row["reason"] for row in result["object_results"]] == [
        None, "label_not_allowed", "label_not_allowed", "invalid_label", "invalid_label"
    ]
    assert objects[2]["label"] == "vehicle "


def test_matching_and_empty_annotation_collections():
    assert check_allowed_labels([{"object_id": 1, "label": "animal"}], ["animal"])["matches"]
    result = check_allowed_labels([], ["animal"])
    assert result["matches"] and result["object_results"] == []


@pytest.mark.parametrize("allowed", ["animal", None, [], [""], [1], [["animal"]]])
def test_invalid_allowed_label_configuration(allowed):
    with pytest.raises(ValueError):
        check_allowed_labels([], allowed)
