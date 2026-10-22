"""Authored lossless live/generated export contracts and inventory checks."""
import json
from pathlib import Path

from fastapi.routing import APIRoute, iter_route_contexts

from backend.api.build_contract import build_contract, render_artifacts
from backend.api.export_examples import export_examples
from backend.api.schemas import build_schemas
from backend.api.common import replace_model_refs
from backend.app.main import create_app
from backend.app.models.export import (
    Export, ExportManifest, ExportManifestEntry, ExportPage, ExportRequest, ExportStarted,
)
from backend.tests.test_rulebook_contract import assert_same_schema

API = Path(__file__).resolve().parents[1] / 'api'
EXPECTED = {
    ('/exports', 'post'): ('startExport', {'202', '404', '409', '422', '500', '503'}),
    ('/exports', 'get'): ('listExports', {'200', '422', '500', '503'}),
    ('/exports/{export_id}', 'get'): ('getExport', {'200', '404', '422', '500', '503'}),
    ('/exports/{export_id}/manifest', 'get'): ('getExportManifest', {'200', '404', '409', '422', '500', '503'}),
    ('/exports/{export_id}/download', 'get'): ('downloadExport', {'200', '404', '409', '422', '500', '503'}),
}


def test_all_five_export_operations_are_unique_and_live():
    live, generated = create_app().openapi(), build_contract()
    ids = [c.original_route.operation_id for c in iter_route_contexts(create_app().routes)
           if isinstance(c.original_route, APIRoute) and c.original_route.include_in_schema]
    assert len(ids) == len(set(ids)) == 50
    for (path, method), (identifier, statuses) in EXPECTED.items():
        one = generated['paths'][path][method]
        two = live['paths']['/api/v1' + path][method]
        assert one['operationId'] == two['operationId'] == identifier
        assert ids.count(identifier) == 1
        assert one['x-implementation-status'] == 'implemented'
        assert set(one['responses']) == set(two['responses']) == statuses
        assert ('requestBody' in two) is (method == 'post')
        first = {(p['in'], p['name']): p for p in one.get('parameters', [])}
        second = {(p['in'], p['name']): p for p in two.get('parameters', [])}
        assert set(first) == set(second)
        for key in first:
            assert first[key]['required'] == second[key]['required']
            assert_same_schema(first[key]['schema'], second[key]['schema'],
                               generated['components']['schemas'], live['components']['schemas'])
    operations = [op for methods in generated['paths'].values() for op in methods.values()]
    assert len(operations) == 50
    assert sum(op['x-implementation-status'] == 'planned' for op in operations) == 0


def test_export_models_are_not_overridden_by_handwritten_schemas():
    schemas = build_schemas()
    for model in (ExportRequest, Export, ExportStarted, ExportPage, ExportManifestEntry, ExportManifest):
        expected = model.model_json_schema()
        expected.pop('$defs', None)
        assert schemas[model.__name__] == replace_model_refs(expected)


def test_binary_and_pagination_shapes_are_lossless():
    live = create_app().openapi()
    download = live['paths']['/api/v1/exports/{export_id}/download']['get']
    assert set(download['responses']['200']['content']) == {'application/zip'}
    assert download['responses']['200']['content']['application/zip']['schema'] == {'type': 'string', 'format': 'binary'}
    assert 'X-Content-Type-Options' in download['responses']['200']['headers']
    for status, response in download['responses'].items():
        if status != '200':
            assert set(response['content']) == {'application/json'}
    query = {p['name']: p['schema'] for p in live['paths']['/api/v1/exports']['get']['parameters']}
    assert query['offset']['maximum'] == 9_223_372_036_854_775_807
    assert type(query['offset']['maximum']) is int
    assert query['limit']['default'] == 20
    assert query['limit']['minimum'] == 1 and query['limit']['maximum'] == 100


def test_synthetic_lifecycle_and_provenance_fixtures_validate():
    examples = export_examples()
    states = [ExportStarted.model_validate_json(json.dumps(pair)) for pair in examples['states']]
    assert {p.job.status.value for p in states} == {'queued','running','cancelling','completed','failed','cancelled'}
    for pair in states:
        assert (pair.export.download_url is not None) is (pair.job.status.value == 'completed')
    manifest = ExportManifest.model_validate_json(json.dumps(examples['manifest']))
    assert manifest.entries[1].included and manifest.entries[1].review_decision.value == 'rejected'
    assert manifest.entries[0].sample_revision != manifest.entries[0].review_revision
    assert not ExportManifest.model_validate_json(json.dumps(examples['empty_manifest'])).entries


def test_generated_artifacts_must_be_regenerated_together():
    for name, expected in render_artifacts().items():
        assert (API / name).read_text(encoding='utf-8') == expected
