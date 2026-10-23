"""Exercise portable preparation without datasets, models or database fixtures."""

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import warnings
from zipfile import ZipFile

from PIL import Image
import pytest

from backend.experiments.box_grounding.prepare import (
    PACKET,
    PreparationError,
    prepare,
    write_json,
)


def annotation(bbox=(1, 1, 4, 3)):
    xmin, ymin, xmax, ymax = bbox
    return (
        "Image size (X x Y x C) : 4 x 3 x 3\n"
        'Objects with ground truth : 1 { "person" }\n'
        "Top left pixel co-ordinates : (1, 1)\n"
        'Bounding box for object 1 "person" (Xmin, Ymin) - (Xmax, Ymax) : '
        f"({xmin}, {ymin}) - ({xmax}, {ymax})\n"
    ).encode()


@pytest.fixture
def corpus(tmp_path):
    source = tmp_path / "dataset"
    packet = tmp_path / "packet"
    (source / "PNGImages").mkdir(parents=True)
    (source / "Annotation").mkdir()
    packet.mkdir()
    image = BytesIO()
    Image.new("RGB", (4, 3), "blue").save(image, format="PNG")
    data = image.getvalue()
    (source / "PNGImages/a.png").write_bytes(data)
    (source / "Annotation/a.txt").write_bytes(annotation())
    manifest = dict(
        schema_version=1,
        corpus_version="synthetic-v1",
        guideline="Each existing box must enclose a person.",
        cases=[dict(
            case_name="case-02", partition="development",
            image="PNGImages/a.png", image_sha256=sha256(data).hexdigest(),
            image_size=[4, 3], original_annotation="Annotation/a.txt",
            original_annotation_sha256=sha256(annotation()).hexdigest(),
            evaluation_annotation_sha256=sha256(annotation()).hexdigest(),
            annotation_override=None,
            objects=[dict(object_id=1, label="person", bbox=[1, 1, 4, 3])],
            controlled_perturbation=False,
        )],
    )
    write_json(packet / "manifest.json", manifest)
    (packet / "system_prompt_v1.txt").write_bytes(b"Synthetic system prompt.")
    return source, packet, manifest


@pytest.mark.parametrize("prefix", ["", "PennFudanPed/"])
def test_zip_and_directory_produce_identical_label_free_scope(corpus, tmp_path, prefix):
    source, packet, _ = corpus
    archive = tmp_path / "dataset.zip"
    with ZipFile(archive, "w") as zipped:
        for relative in ("PNGImages/a.png", "Annotation/a.txt"):
            zipped.write(source / relative, prefix + relative)
        zipped.writestr("../../outside.txt", "ignored: not a selected member")
    first = tmp_path / "from-directory"
    second = tmp_path / "from-zip"
    scope = prepare(first, dataset_root=source, packet=packet)
    assert prepare(second, archive_path=archive, packet=packet) == scope
    assert (first / "scope.json").read_bytes() == (second / "scope.json").read_bytes()
    assert (first / "case-02/image.png").read_bytes() == (source / "PNGImages/a.png").read_bytes()
    assert (first / "case-02/annotation.txt").read_bytes() == annotation()
    assert scope["cases"][0]["inputs"]["objects"][0]["bbox"] == (1, 1, 4, 3)
    assert scope["cases"][0]["image"] == "case-02/image.png"
    assert not (tmp_path / "outside.txt").exists()
    serialized = json.dumps(scope)
    for forbidden in ("expected", "decision", "controlled_perturbation", "reviewer_id"):
        assert forbidden not in serialized


def test_declared_annotation_override_is_verified_and_source_is_unchanged(corpus, tmp_path):
    source, packet, manifest = corpus
    replacement = annotation((2, 2, 4, 3))
    (packet / "replacement.txt").write_bytes(replacement)
    case = manifest["cases"][0]
    case.update(annotation_override="replacement.txt",
                evaluation_annotation_sha256=sha256(replacement).hexdigest(),
                objects=[dict(object_id=1, label="person", bbox=[2, 2, 4, 3])])
    write_json(packet / "manifest.json", manifest)
    target = tmp_path / "output"
    prepare(target, dataset_root=source, packet=packet)
    assert (target / "case-02/annotation.txt").read_bytes() == replacement
    assert (source / "Annotation/a.txt").read_bytes() == annotation()


@pytest.mark.parametrize("changed", ["image", "annotation", "override", "object_facts"])
def test_mismatches_publish_no_output(corpus, tmp_path, changed):
    source, packet, manifest = corpus
    case = manifest["cases"][0]
    if changed == "image":
        (source / "PNGImages/a.png").write_bytes(b"changed")
    elif changed == "annotation":
        (source / "Annotation/a.txt").write_bytes(annotation() + b"changed")
    elif changed == "override":
        (packet / "replacement.txt").write_bytes(b"changed")
        case["annotation_override"] = "replacement.txt"
    else:
        case["objects"][0]["bbox"] = [1, 1, 3, 2]
    write_json(packet / "manifest.json", manifest)
    target = tmp_path / "output"
    with pytest.raises(PreparationError):
        prepare(target, dataset_root=source, packet=packet)
    assert not target.exists()


def test_duplicate_zip_member_is_rejected(corpus, tmp_path):
    source, packet, _ = corpus
    archive = tmp_path / "duplicate.zip"
    with ZipFile(archive, "w") as zipped:
        zipped.write(source / "PNGImages/a.png", "PNGImages/a.png")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            zipped.write(source / "PNGImages/a.png", "PNGImages/a.png")
    with pytest.raises(PreparationError, match="missing or duplicated"):
        prepare(tmp_path / "output", archive_path=archive, packet=packet)


def test_preparation_does_not_overwrite_an_existing_run(corpus, tmp_path):
    source, packet, _ = corpus
    target = tmp_path / "output"
    prepare(target, dataset_root=source, packet=packet)
    before = (target / "scope.json").read_bytes()
    with pytest.raises(PreparationError, match="already exists"):
        prepare(target, dataset_root=source, packet=packet)
    assert (target / "scope.json").read_bytes() == before


def test_published_cases_labels_references_and_replacements_are_consistent():
    manifest = json.loads((PACKET / "manifest.json").read_bytes())
    labels = json.loads((PACKET / "reviewed_labels.json").read_bytes())["cases"]
    results = json.loads((PACKET / "reference_results/reviewed_comparison.json").read_bytes())
    cases = {case["case_name"]: case for case in manifest["cases"]}
    assert len(cases) == 11
    assert "case-01" not in cases
    assert sum(case["partition"] == "training" for case in cases.values()) == 3
    assert sum(case["partition"] == "development" for case in cases.values()) == 8
    assert {label["case_id"] for label in labels} == set(cases)
    assert len({case["image_sha256"] for case in cases.values()}) == 11
    for label in labels:
        assert label["partition"] == cases[label["case_id"]]["partition"]
    for case in cases.values():
        if case["annotation_override"]:
            replacement = PACKET / case["annotation_override"]
            assert sha256(replacement.read_bytes()).hexdigest() == case["evaluation_annotation_sha256"]
    expected = {label["case_id"]: label["decision"] for label in labels}
    assert {row["case_name"] for row in results["results"]} == {
        name for name, case in cases.items() if case["partition"] == "development"
    }
    for row in results["results"]:
        assert row["expected"] == expected[row["case_name"]]
