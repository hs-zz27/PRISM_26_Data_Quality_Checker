"""Real file-path integration for native JSON import, review evidence, edits and export."""
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
import json
from threading import Event
import time
from zipfile import ZipFile

from PIL import Image
import pytest

from backend.app.core.config import AssetStorageSettings
from backend.app.core.correction import CorrectionSettings, CorrectionError
from backend.app.core.dataset_import import ImportInputError, ImportResourceError
from backend.app.core.export import ExportSettings
from backend.app.datasets.json_zip import unpack_archive, prepare_sample
from backend.app.models.correction import CorrectionRequest
from backend.app.models.export import (CapturedAvailability, ExportManifest, ExportManifestEntry,
    ExportPolicy, FrozenExport, FrozenSample)
from backend.app.services.asset_preview import _decode_png
from backend.app.services.evaluation import evaluate_rules
from backend.app.services.export_archive import build_archive, native_annotation
from backend.app.services.export_files import create_attempt
from backend.app.services.json_audit import audit_json_sample
from backend.app.services.json_corrections import prepare_json_correction
from backend.app.storage.workflow_repository import read_manifest_asset_file
from backend.tests.dataset_import_fakes import staged, zip_bytes
from backend.tests.test_evaluation import confirmed_book, label_rule


def documents(format):
    if format == 'coco':
        return {'info': {'notice': 'retain'}, 'images': [{'id': 7, 'file_name': 'person.jpg', 'width': 20, 'height': 16}],
            'categories': [{'id': 3, 'name': 'person'}], 'annotations': [
                {'id': 90, 'image_id': 7, 'category_id': 3, 'bbox': [0.25, 1.5, 9.5, 10],
                 'segmentation': [[1, 2, 9, 2, 9, 10, 1, 10]], 'area': 64, 'iscrowd': 0, 'custom': 'kept'}]}
    return {'imagePath': 'person.jpg', 'imageData': None, 'imageWidth': 20, 'imageHeight': 16,
        'flags': {'retain': True}, 'shapes': [{'label': 'person', 'shape_type': 'polygon',
        'group_id': 42, 'points': [[1, 2], [9, 2], [9, 10], [1, 10]], 'flags': {'custom': True}}]}


def imported(tmp_path, format, *, data=None, extras=(), include_image=True):
    data = documents(format) if data is None else data
    output = BytesIO()
    Image.new('RGB', (20, 16), 'green').save(output, format='JPEG')
    image = output.getvalue()
    raw = (json.dumps(data, indent=2) + '\r\n').encode()
    annotation_name = 'annotations/instances.json' if format == 'coco' else 'data/person.json'
    image_name = 'images/person.jpg' if format == 'coco' else 'data/person.jpg'
    entries = [(annotation_name, raw), *([(image_name, image)] if include_image else []), *extras]
    path, request, settings = staged(tmp_path, zip_bytes(entries))
    request = request.model_copy(update={'format': format})
    source = tmp_path / 'extracted'
    samples, _ = unpack_archive(path, source, request)
    publications = [prepare_sample(request.dataset_id, item, i, source_root=source,
        asset_root=settings.asset_root, policy=request.policy) for i, item in enumerate(samples)]
    return publications, settings, raw, image


@pytest.mark.parametrize('format', ['coco', 'labelme'])
def test_import_preserves_originals_and_uses_native_coordinates(tmp_path, format):
    pubs, settings, raw, image = imported(tmp_path, format)
    publication = pubs[0]
    assets = AssetStorageSettings(root=settings.asset_root)
    assert publication.sample.annotation_format == format
    assert publication.sample.integrity_status.value == 'valid'
    assert publication.manifest.raw_mask.availability.value == 'not_supplied'
    assert read_manifest_asset_file(publication.manifest, 'annotation_text', assets)[1] == raw
    assert read_manifest_asset_file(publication.manifest, 'original_image', assets)[1] == image
    obj = publication.sample.objects[0]
    assert obj.coordinate_system == 'zero_based_continuous'
    assert obj.source_id == (90 if format == 'coco' else None)
    assert obj.group_id == (42 if format == 'labelme' else None)
    if format == 'coco':
        assert obj.bbox == (0.25, 1.5, 9.75, 11.5)
    with _decode_png(image, allow_jpeg=True) as picture:
        assert picture.size == (20, 16)
    report = audit_json_sample(publication.manifest, assets)
    assert report['checks']['mask_ids']['status'] == 'unavailable'
    assert report['checks']['annotation_ids']['status'] == ('completed' if format == 'coco' else 'unavailable')
    book = confirmed_book(label_rule(), label_rule(rule_id='polygon', check_name='polygon_validity', parameters={}))
    findings = evaluate_rules(book, report, audit_id='audit', sample_id=publication.sample.sample_id, sample_revision=1)
    assert [f.outcome.value for f in findings] == ['pass', 'pass']
    assert all(f.review.resolution.value == 'pending' for f in findings)


@pytest.mark.parametrize('format', ['coco', 'labelme'])
def test_revision_edits_preserve_metadata_ids_and_original_hashes(tmp_path, format):
    pubs, settings, original, _ = imported(tmp_path, format)
    base = pubs[0]
    objects = [obj.model_dump(mode='json') for obj in base.sample.objects]
    objects[0]['polygons'] = [[[2, 2], [8, 2], [8, 10], [2, 10]]]
    if format == 'labelme':
        objects[0]['bbox'] = [2, 2, 8, 10]
    request = CorrectionRequest.model_validate({'expected_revision': 1, 'reviewer_id': 'student',
        'reason': 'Adjusted visible outline', 'objects': objects})
    correction = prepare_json_correction(base, request, CorrectionSettings(asset_root=settings.asset_root))
    assert correction.revised.sample.revision == 2
    assert correction.revised.manifest.original_image == base.manifest.original_image
    assets = AssetStorageSettings(root=settings.asset_root)
    assert read_manifest_asset_file(base.manifest, 'annotation_text', assets)[1] == original
    changed = json.loads(read_manifest_asset_file(correction.revised.manifest, 'annotation_text', assets)[1])
    if format == 'coco':
        assert changed['annotations'][0]['area'] == 48
        assert changed['annotations'][0]['custom'] == 'kept'
        assert changed['annotations'][0]['id'] == 90
        assert changed['info'] == documents(format)['info']
    else:
        assert changed['shapes'][0]['group_id'] == 42
        assert changed['shapes'][0]['flags'] == {'custom': True}


def test_identity_edits_are_rejected_before_publication(tmp_path):
    pubs, settings, _, _ = imported(tmp_path, 'labelme')
    objects = [obj.model_dump(mode='json') for obj in pubs[0].sample.objects]
    objects[0]['source_key'] = '/shapes/88'
    request = CorrectionRequest.model_validate({'expected_revision': 1, 'reviewer_id': 'student', 'reason': 'edit', 'objects': objects})
    with pytest.raises(CorrectionError):
        prepare_json_correction(pubs[0], request, CorrectionSettings(asset_root=settings.asset_root))
    assert not (settings.asset_root / 'corrections').exists()


@pytest.mark.parametrize('format', ['coco', 'labelme'])
def test_native_export_roundtrips_to_import(tmp_path, format):
    pubs, settings, raw, original_image = imported(tmp_path, format)
    source = pubs[0].manifest
    now = datetime.now(UTC)
    entry = ExportManifestEntry(sample_id=source.sample_id, sample_revision=1, ordinal=0,
        processing_state='completed', included=True, review_decision='accepted', review_revision=1,
        reviewer_id='student', reviewed_at=now, availability=CapturedAvailability(
            annotation_text='available', raw_mask='not_supplied', original_image='available'), reason=None)
    document = ExportManifest(format=format, export_id='export_demo', audit_id='audit_demo',
        dataset_id=source.dataset_id, source_mode='snapshot', rulebook_id='book', rulebook_version=1,
        selection='accepted_only', include_images=True, selected_at=now, entries=[entry])
    frozen = FrozenExport(manifest=document, samples=[FrozenSample(entry=entry, manifest=source)], policy=ExportPolicy())
    export_settings = ExportSettings(root=tmp_path/'exports', asset_root=settings.asset_root,
                                    import_root=settings.root, policy=frozen.policy)
    directory = create_attempt(export_settings.root, 'a'*32)
    build_archive(frozen, settings=export_settings, directory=directory, stop_event=Event(),
                  deadline_end=time.monotonic()+30, progress=lambda _: None)
    with ZipFile(directory/'archive.zip') as archive:
        assert archive.read(f'data/{source.sample_id}/image.jpg') == original_image
        doc = json.loads(archive.read(f'data/{source.sample_id}/annotations.json'))
        assert (doc['images'][0]['file_name'] if format == 'coco' else doc['imagePath']) == 'image.jpg'
    next_root = tmp_path/'roundtrip'; next_root.mkdir()
    path, request, _ = staged(next_root, (directory/'archive.zip').read_bytes())
    request = request.model_copy(update={'format': format})
    samples, unsupported = unpack_archive(path, next_root/'extracted', request)
    assert len(samples) == 1 and not unsupported


@pytest.mark.parametrize('name', ['../escape.json', '/absolute.json', 'data/../escape.json', 'data\\escape.json'])
def test_json_archive_rejects_unsafe_entry_names(tmp_path, name):
    with pytest.raises(ImportInputError):
        imported(tmp_path, 'labelme', extras=[(name, b'{}')])


def test_ambiguous_coco_image_binding_rejected(tmp_path):
    image = BytesIO(); Image.new('RGB', (20, 16)).save(image, format='JPEG')
    with pytest.raises(ImportInputError):
        imported(tmp_path, 'coco', extras=[('annotations/person.jpg', image.getvalue())])


def test_broken_annotation_named_manifest_is_not_silently_ignored(tmp_path):
    with pytest.raises(ImportInputError):
        imported(tmp_path, 'labelme', extras=[('manifest.json', b'{broken')])


def test_unsupported_rle_is_retained_and_visible(tmp_path):
    data = documents('coco'); data['annotations'][0]['segmentation'] = {'size': [16, 20], 'counts': [320]}
    pubs, settings, raw, _ = imported(tmp_path, 'coco', data=data)
    report = audit_json_sample(pubs[0].manifest, AssetStorageSettings(root=settings.asset_root))
    assert any(i['severity'] == 'unsupported' for i in report['source_diagnostics'])
    assert report['objects'][0]['checks']['polygon_validity']['status'] == 'unavailable'
    assert json.loads(native_annotation(raw, pubs[0].manifest))['annotations'][0]['segmentation'] == data['annotations'][0]['segmentation']


def test_missing_image_preserves_annotation_for_review(tmp_path):
    pubs, settings, _, _ = imported(tmp_path, 'labelme', include_image=False)
    assert pubs[0].sample.integrity_status.value == 'invalid'
    report = audit_json_sample(pubs[0].manifest, AssetStorageSettings(root=settings.asset_root))
    assert report['status'] == 'incomplete'
    assert report['objects'][0]['checks']['polygon_validity']['status'] == 'unavailable'


def test_empty_annotation_can_pass_labels_without_fabricated_objects(tmp_path):
    data = documents('labelme'); data['shapes'] = []
    pubs, settings, _, _ = imported(tmp_path, 'labelme', data=data)
    report = audit_json_sample(pubs[0].manifest, AssetStorageSettings(root=settings.asset_root))
    findings = evaluate_rules(confirmed_book(label_rule()), report, audit_id='a', sample_id=pubs[0].sample.sample_id, sample_revision=1)
    assert len(findings) == 1 and findings[0].outcome.value == 'pass'


@pytest.mark.parametrize('format', ['coco', 'labelme'])
def test_one_fix_can_be_saved_while_other_annotation_errors_remain(tmp_path, format):
    data = documents(format)
    if format == 'coco':
        data['annotations'].append({'id': 91, 'image_id': 7, 'category_id': 3,
            'bbox': [1, 1, 2, 2], 'segmentation': {'size': [16, 20], 'counts': [320]}})
        other = deepcopy(data['annotations'][1])
    else:
        data['shapes'].append({'label': '', 'shape_type': 'polygon', 'points': [[1, 1], [3, 3], [5, 5]], 'group_id': None})
        other = deepcopy(data['shapes'][1])
    pubs, settings, original, _ = imported(tmp_path, format, data=data)
    base = pubs[0]
    objects = [obj.model_dump(mode='json') for obj in base.sample.objects]
    objects[0]['polygons'] = [[[2, 2], [8, 2], [8, 10], [2, 10]]]
    if format == 'labelme': objects[0]['bbox'] = [2, 2, 8, 10]
    request = CorrectionRequest.model_validate(dict(expected_revision=1, reviewer_id='reviewer', reason='Fix one outline', objects=objects))
    revised = prepare_json_correction(base, request, CorrectionSettings(asset_root=settings.asset_root)).revised
    assets = AssetStorageSettings(root=settings.asset_root)
    saved = json.loads(read_manifest_asset_file(revised.manifest, 'annotation_text', assets)[1])
    assert saved['annotations' if format == 'coco' else 'shapes'][1] == other
    assert read_manifest_asset_file(base.manifest, 'annotation_text', assets)[1] == original
    assert revised.sample.revision == 2
    assert audit_json_sample(revised.manifest, assets)['source_diagnostics']


def test_partial_fix_cannot_introduce_new_polygon_errors(tmp_path):
    pubs, settings, _, _ = imported(tmp_path, 'labelme')
    objects = [obj.model_dump(mode='json') for obj in pubs[0].sample.objects]
    objects[0]['polygons'] = [[[1, 1], [4, 4], [1, 4], [4, 1]]]
    objects[0]['bbox'] = [1, 1, 4, 4]
    request = CorrectionRequest.model_validate(dict(expected_revision=1, reviewer_id='reviewer', reason='Synthetic crossing', objects=objects))
    with pytest.raises(CorrectionError):
        prepare_json_correction(pubs[0], request, CorrectionSettings(asset_root=settings.asset_root))
