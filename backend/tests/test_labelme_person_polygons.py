"""LabelMe files with 1 to 10 person polygons: import, exact vertices, findings, and no silent repair."""
import json

import pytest

from backend.app.core.config import AssetStorageSettings
from backend.app.core.correction import CorrectionSettings
from backend.app.models.correction import CorrectionRequest
from backend.app.services.evaluation import evaluate_rules
from backend.app.services.export_archive import native_annotation
from backend.app.services.json_audit import audit_json_sample
from backend.app.services.json_corrections import prepare_json_correction
from backend.app.storage.workflow_repository import read_manifest_asset_file
from backend.tests.test_evaluation import confirmed_book, label_rule
from backend.tests.test_json_dataset_workflow import imported


def people(count):
    """A LabelMe file with `count` person polygons inside the 20 x 16 test photo; whole and fractional numbers."""
    shapes = []
    for index in range(count):
        left = 0.5 + index * 1.9
        shapes.append({'label': 'person', 'shape_type': 'polygon', 'group_id': None, 'flags': {},
                       'points': [[left, 2], [left + 1.25, 2.5], [left + 1, 13], [left, 12.125]]})
    return {'version': '5.4.1', 'flags': {}, 'shapes': shapes, 'imagePath': 'person.jpg', 'imageData': None,
            'imageWidth': 20, 'imageHeight': 16}


def shown(obj):
    return [[list(point) for point in part] for part in obj.polygons]


def kinds(points):
    return [[type(value) for value in point] for point in points]


@pytest.mark.parametrize('count', range(1, 11))
def test_file_with_one_to_ten_person_polygons_imports_with_exact_vertices_and_findings(tmp_path, count):
    data = people(count)
    publications, settings, raw, _ = imported(tmp_path, 'labelme', data=data)
    publication = publications[0]
    sample = publication.sample
    assets = AssetStorageSettings(root=settings.asset_root)

    assert sample.integrity_status.value == 'valid' and not sample.issues
    assert len(sample.objects) == count
    for index, (obj, shape) in enumerate(zip(sample.objects, data['shapes'])):
        assert obj.source_key == f'/shapes/{index}'  # the stable key; the display number is index + 1
        assert obj.object_id == index + 1 and obj.label == 'person'
        assert shown(obj) == [shape['points']]
        assert kinds(shown(obj)[0]) == kinds(shape['points'])  # 2 stays 2, 2.5 stays 2.5
        # A polygon has no box of its own: the bounds are marked as worked out, never as supplied.
        assert obj.bbox_origin == 'derived_from_polygon'
    # The stored annotation file is the uploaded file, byte for byte.
    assert read_manifest_asset_file(publication.manifest, 'annotation_text', assets)[1] == raw

    report = audit_json_sample(publication.manifest, assets)
    book = confirmed_book(label_rule(), label_rule(rule_id='polygon', check_name='polygon_validity', parameters={}))
    findings = evaluate_rules(book, report, audit_id='audit', sample_id=sample.sample_id, sample_revision=1)
    assert findings and {finding.outcome.value for finding in findings} == {'pass'}

    data['shapes'][0]['label'] = 'car'
    (tmp_path / 'wrong').mkdir()
    wrong, wrong_settings, _, _ = imported(tmp_path / 'wrong', 'labelme', data=data)
    report = audit_json_sample(wrong[0].manifest, AssetStorageSettings(root=wrong_settings.asset_root))
    findings = evaluate_rules(confirmed_book(label_rule()), report, audit_id='audit',
                              sample_id=wrong[0].sample.sample_id, sample_revision=1)
    failed = [finding for finding in findings if finding.outcome.value == 'fail']
    assert len(failed) == 1 and failed[0].object_id == 1


@pytest.mark.parametrize('name,points', [
    ('crosses itself', [[1, 2], [9, 10], [9, 2], [1, 10]]),
    ('leaves the image', [[-3.5, 2], [9, 2], [9, 30.25]]),
    ('repeats a vertex', [[1, 2], [9, 2], [9, 2], [9, 10], [1, 10]]),
    ('too few vertices', [[1, 2], [9, 2]]),
])
def test_problem_polygons_keep_their_source_points_and_are_reported_not_repaired(tmp_path, name, points):
    data = people(2)
    data['shapes'][1]['points'] = points
    publications, settings, raw, _ = imported(tmp_path, 'labelme', data=data)
    publication = publications[0]
    assets = AssetStorageSettings(root=settings.asset_root)

    # Nothing is clipped, reordered, closed, de-duplicated or dropped.
    assert len(publication.sample.objects) == 2
    assert shown(publication.sample.objects[0]) == [data['shapes'][0]['points']]
    assert shown(publication.sample.objects[1]) == [points]
    assert read_manifest_asset_file(publication.manifest, 'annotation_text', assets)[1] == raw
    # The problem is visible to the reviewer instead.
    report = audit_json_sample(publication.manifest, assets)
    assert publication.sample.integrity_status.value == 'invalid'
    assert any(issue['location'].startswith('/shapes/1') for issue in report['source_diagnostics'])
    assert not any(issue['location'].startswith('/shapes/0') for issue in report['source_diagnostics'])


def test_correcting_one_polygon_leaves_every_other_vertex_and_the_original_untouched(tmp_path):
    data = people(10)
    publications, settings, raw, _ = imported(tmp_path, 'labelme', data=data)
    base = publications[0]
    assets = AssetStorageSettings(root=settings.asset_root)
    objects = [obj.model_dump(mode='json') for obj in base.sample.objects]
    corrected = [[4.25, 1.5], [5.5, 2], [5.25, 14], [4.25, 13.5]]
    objects[2]['polygons'] = [corrected]
    objects[2]['bbox'] = [4.25, 1.5, 5.5, 14]  # a polygon's bounds are worked out from its points and must agree
    request = CorrectionRequest.model_validate(dict(expected_revision=1, reviewer_id='reviewer',
                                                    reason='Cover the head and feet', objects=objects))
    revised = prepare_json_correction(base, request, CorrectionSettings(asset_root=settings.asset_root)).revised

    saved = json.loads(read_manifest_asset_file(revised.manifest, 'annotation_text', assets)[1])
    assert saved['shapes'][2]['points'] == corrected
    for index, shape in enumerate(data['shapes']):
        if index != 2:
            assert saved['shapes'][index] == shape
    assert {key: value for key, value in saved.items() if key != 'shapes'} == \
        {key: value for key, value in data.items() if key != 'shapes'}
    assert revised.sample.revision == 2 and revised.sample.objects[2].source_key == '/shapes/2'
    # Revision 1 is still the uploaded file.
    assert read_manifest_asset_file(base.manifest, 'annotation_text', assets)[1] == raw
    # The native export of each revision carries that revision's exact points.
    for manifest, expected in ((base.manifest, data['shapes']), (revised.manifest, saved['shapes'])):
        content = read_manifest_asset_file(manifest, 'annotation_text', assets)[1]
        exported = json.loads(native_annotation(content, manifest))
        assert [shape['points'] for shape in exported['shapes']] == [shape['points'] for shape in expected]
