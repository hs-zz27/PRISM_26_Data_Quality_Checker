"""Authored synthetic archive and dirty-evidence regressions. NOT executed."""
from io import BytesIO
import stat
import struct
import zipfile

from PIL import Image
import pytest

from backend.app.core.dataset_import import ImportInputError, ImportResourceError, ImportStorageError
from backend.app.datasets.penn_fudan_zip import prepare_sample, unpack_archive
from backend.app.models.dataset_import import ImportPolicy
from backend.app.storage.workflow_repository import read_manifest_asset_file
from backend.app.core.config import AssetStorageSettings
from backend.tests.dataset_import_fakes import annotation, entries, png, staged, zip_bytes


@pytest.mark.parametrize("prefix", ["", "PennFudanPed/"])
@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_root_or_one_wrapper_without_directory_entries(tmp_path, prefix, compression):
    path, request, settings = staged(tmp_path, zip_bytes(entries(prefix), compression=compression))
    source = tmp_path / "extracted"
    assert unpack_archive(path, source, request) == (["a"], False)
    sample = prepare_sample(request.dataset_id, "a", 0, source_root=source,
                            asset_root=settings.asset_root, policy=request.policy)
    assert sample.sample.integrity_status.value == "valid"
    assert sample.sample.image_size == (3, 2) and sample.annotation_object_count == 0
    assert sample.sample.objects == []
    for role, expected in (("raw_mask", entries()[2][1]), ("annotation_text", annotation())):
        _, actual = read_manifest_asset_file(sample.manifest, role, AssetStorageSettings(root=settings.asset_root))
        assert actual == expected  # 16-bit IDs and CRLF are not normalized.
    assert all(value is None for value in sample.sample.assets.model_dump().values())


def test_union_of_roles_and_unknown_annotation_count(tmp_path):
    path, request, settings = staged(tmp_path, zip_bytes([
        ("Annotation/a.txt", b"not an annotation"), ("PedMasks/b_mask.png", png("P")),
        ("PNGImages/c.png", b"corrupt but readable PNG"),
    ]))
    source = tmp_path / "extracted"
    ids, _ = unpack_archive(path, source, request)
    assert ids == ["a", "b", "c"]
    results = [prepare_sample(request.dataset_id, item, index, source_root=source,
                              asset_root=settings.asset_root, policy=request.policy)
               for index, item in enumerate(ids)]
    assert all(item.annotation_object_count is None for item in results)
    assert all(item.sample.issues for item in results)
    assert results[2].manifest.original_image.availability.value == "available"
    assert results[2].sample.image_size is None


@pytest.mark.parametrize("text", [b"", b"\xff\xfe", b"Objects with ground truth : 1 {}", annotation(count=2),
                                    annotation(boxes='Bounding box for object broken\n')])
def test_bad_annotation_is_evidence_not_container_failure(tmp_path, text):
    path, request, settings = staged(tmp_path, zip_bytes([(n, text if n.endswith(".txt") else c) for n, c in entries()]))
    source = tmp_path / "extracted"
    unpack_archive(path, source, request)
    result = prepare_sample(request.dataset_id, "a", 0, source_root=source,
                            asset_root=settings.asset_root, policy=request.policy)
    assert result.annotation_object_count is None
    assert result.sample.integrity_status.value == "invalid"
    assert result.manifest.annotation_text.availability.value == "available"


def test_duplicate_objects_invalid_coordinates_preserve_order(tmp_path):
    line = 'Bounding box for object 1 "person" (Xmin, Ymin) - (Xmax, Ymax) : (-2, 1) - (0, 0)\r\n'
    path, request, settings = staged(tmp_path, zip_bytes([(n, annotation(count=2, boxes=line*2) if n.endswith(".txt") else c) for n, c in entries()]))
    source = tmp_path / "extracted"
    unpack_archive(path, source, request)
    result = prepare_sample(request.dataset_id, "a", 0, source_root=source,
                            asset_root=settings.asset_root, policy=request.policy)
    assert result.annotation_object_count == 2
    assert [item.object_id for item in result.sample.objects] == [1, 1]
    assert result.sample.objects[0].bbox == (-2, 1, 0, 0)
    assert result.sample.integrity_status.value == "invalid"


@pytest.mark.parametrize("name", ["../evil", "/evil", "C:/evil", "\\\\server\\file", "a\\b", "a/../b",
                                   "a//b", "a/./b", "CON.txt", "folder/trailing. ", "bad\x00name"])
def test_reject_unsafe_identity_even_for_ignored_entries(tmp_path, name):
    # zipfile truncates NUL on writing; patch raw ZIP name to preserve attack bytes.
    content = zip_bytes(entries() + [("badXname" if "\x00" in name else name, b"ignored")])
    if "\x00" in name:
        content = content.replace(b"badXname", b"bad\x00name")
    path, request, _ = staged(tmp_path, content)
    with pytest.raises(ImportInputError):
        unpack_archive(path, tmp_path / "out", request)


@pytest.mark.parametrize("extra", [
    [("PNGImages/a.png", b"duplicate")], [("pngimages/A.png", b"alias")],
    [("Annotation/A.txt", annotation())], [("other/PNGImages/b.png", png())],
    [("outer/inner/PNGImages/b.png", png())], [("folder", b"file"), ("folder/child", b"child")],
    [("PNGImages/e\u0301.png", png()), ("PNGImages/é.png", png())],
])
def test_ambiguous_or_colliding_layout_rejected(tmp_path, extra):
    path, request, _ = staged(tmp_path, zip_bytes(entries() + extra))
    with pytest.raises(ImportInputError):
        unpack_archive(path, tmp_path / "out", request)


def test_special_file_and_unsupported_compression(tmp_path):
    info = zipfile.ZipInfo("__MACOSX/link")
    info.create_system, info.external_attr = 3, (stat.S_IFLNK | 0o777) << 16
    path, request, _ = staged(tmp_path, zip_bytes(entries() + [(info, b"target")]))
    with pytest.raises(ImportInputError):
        unpack_archive(path, tmp_path / "out", request)
    content = zip_bytes(entries(), compression=zipfile.ZIP_BZIP2)
    path, request, _ = staged(tmp_path, content)
    with pytest.raises(ImportInputError):
        unpack_archive(path, tmp_path / "out2", request)


def test_encrypted_and_crc_failure_are_container_failures(tmp_path):
    raw = bytearray(zip_bytes(entries()))
    central = raw.index(b"PK\x01\x02")
    struct.pack_into("<H", raw, 6, 1)
    struct.pack_into("<H", raw, central + 8, 1)
    path, request, _ = staged(tmp_path, bytes(raw))
    with pytest.raises(ImportInputError):
        unpack_archive(path, tmp_path / "encrypted", request)
    raw = bytearray(zip_bytes(entries()))
    name_length, extra = struct.unpack_from("<HH", raw, 26)
    raw[30 + name_length + extra] ^= 1
    path, request, _ = staged(tmp_path, bytes(raw))
    with pytest.raises(ImportInputError):
        unpack_archive(path, tmp_path / "crc", request)


@pytest.mark.parametrize("updates", [
    {"max_entries": 2}, {"max_samples": 1},
    {"max_file_bytes": 50, "max_annotation_bytes": 50},
    {"max_expanded_bytes": 150, "max_file_bytes": 150, "max_annotation_bytes": 100},
    {"max_annotation_bytes": 10},
])
def test_metadata_and_actual_resource_budgets(tmp_path, updates):
    content = zip_bytes(entries() + [("PNGImages/b.png", png())])
    policy = ImportPolicy(**updates)
    path, request, _ = staged(tmp_path, content, policy=policy)
    with pytest.raises(ImportResourceError):
        unpack_archive(path, tmp_path / "out", request)


def test_pixel_and_object_budgets(tmp_path):
    policy = ImportPolicy(max_image_pixels=5)
    path, request, settings = staged(tmp_path, zip_bytes(entries()), policy=policy)
    source = tmp_path / "out"
    unpack_archive(path, source, request)
    with pytest.raises(ImportResourceError):
        prepare_sample(request.dataset_id, "a", 0, source_root=source,
                       asset_root=settings.asset_root, policy=policy)


def test_ignored_metadata_crc_counts_and_unsupported_extra_warning(tmp_path):
    path, request, _ = staged(tmp_path, zip_bytes(entries() + [
        ("__MACOSX/junk", b"metadata"), (".DS_Store", b"metadata"),
        ("README.md", b"docs"), ("PNGImages/note.bin", b"extra"),
    ]))
    assert unpack_archive(path, tmp_path / "out", request) == (["a"], True)


def test_staged_identity_mismatch_is_storage_failure(tmp_path):
    path, request, _ = staged(tmp_path, zip_bytes(entries()))
    path.write_bytes(b"changed after acceptance")
    with pytest.raises(ImportStorageError):
        unpack_archive(path, tmp_path / "out", request)


def test_parsed_object_budget_is_enforced_without_label_policy(tmp_path):
    line = 'Bounding box for object 1 "anything" (Xmin, Ymin) - (Xmax, Ymax) : (1, 1) - (2, 2)\n'
    content = zip_bytes([(name, annotation(count=2, boxes=line*2) if name.endswith(".txt") else data)
                         for name, data in entries()])
    path, request, settings = staged(tmp_path, content, policy=ImportPolicy(max_objects_per_sample=1))
    source = tmp_path / "out"
    unpack_archive(path, source, request)
    with pytest.raises(ImportResourceError):
        prepare_sample(request.dataset_id, "a", 0, source_root=source,
                       asset_root=settings.asset_root, policy=request.policy)
