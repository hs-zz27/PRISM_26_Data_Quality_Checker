"""Offline regression checks for target geometry and paired diagnostic execution."""

from base64 import b64decode
from io import BytesIO
import json

import httpx
from PIL import Image
import pytest

from backend.experiments.box_grounding.context_probe import prepare_targets, run_probe
from backend.experiments.box_grounding.context_views import render_context
from backend.experiments.box_grounding.crops import render_box_crops
from backend.experiments.box_grounding.prompts import crop_request, load_prompt_texts
from backend.experiments.box_grounding.settings import ExecutionSettings
from backend.experiments.box_grounding.views import ViewLimits
from box_helpers import DIGEST, CaseSpec, build_corpus, read_results


def settings():
    return ExecutionSettings(model_digest=DIGEST, identity_schema="bound-v1")


def plan_for(scope):
    return {"schema": "context-probe-plan-v1", "scope_sha256": scope.sha256,
            "targets": [{"case_name": "case-02", "box_number": 1},
                        {"case_name": "case-02", "box_number": 2}]}


def write_plan(corpus, tmp_path):
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(plan_for(corpus.scope())))
    return path


@pytest.mark.parametrize("box", [(1, 1, 1, 1), (8, 6, 8, 6), (1, 1, 8, 6), (2, 2, 4, 5)])
def test_frame_preserves_all_target_pixels_including_source_edges(tmp_path, box):
    corpus = build_corpus(tmp_path/'source', (CaseSpec('edge', 'development', (8, 6), ((1, 'person', box),)),))
    case = corpus.scope().cases[0]
    crop = render_box_crops(case.image_bytes, case.objects, (1, 1), ViewLimits())[0]
    context = render_context(case, crop, ViewLimits())
    with Image.open(BytesIO(context.png)) as view, Image.open(BytesIO(crop.png)) as tight:
        assert view.crop(context.target_window).tobytes() == tight.tobytes()
        left, top, right, bottom = context.target_window
        assert view.getpixel((left-1, top)) == (255, 0, 0)
        assert view.getpixel((right, bottom-1)) == (255, 0, 0)
    assert context.source_window == (0, 0, 8, 6)


def test_context_uses_source_pixels_and_clips_only_padding(tmp_path):
    spec = CaseSpec('center', 'development', (300, 300), ((1, 'person', (101, 101, 200, 200)),))
    corpus = build_corpus(tmp_path/'source', (spec,))
    case = corpus.scope().cases[0]
    crop = render_box_crops(case.image_bytes, case.objects, (1, 1), ViewLimits())[0]
    context = render_context(case, crop, ViewLimits())
    assert context.source_window == (50, 50, 250, 250)
    assert context.target_window == (52, 52, 152, 152)
    assert context.size == (204, 204)
    with Image.open(BytesIO(context.png)) as view, Image.open(BytesIO(case.image_bytes)) as source:
        assert view.getpixel((3, 3)) == source.getpixel((51, 51))


def test_control_is_identical_and_candidate_has_no_original_geometry(corpus):
    scope = corpus.scope()
    targets = prepare_targets(scope, plan_for(scope), settings(), packet=corpus.packet)
    for target in targets:
        expected = crop_request(scope, target.case, target.crop, load_prompt_texts(corpus.packet), settings(), variant='box-crop-v2')
        assert target.requests['box-crop-v2'].body == expected.body
        candidate = target.requests['box-context-v1'].payload
        document = json.loads(candidate['messages'][1]['content'])
        assert set(document['box']) == {'box_number', 'object_id', 'label'}
        assert 'outside the frame does not make the target pass' in document['task']['instruction']
        assert b64decode(candidate['messages'][1]['images'][0]) == target.context.png
        for field in ['case_id', 'rule_id', 'box_number', 'object_id']:
            assert 'const' in candidate['format']['properties'][field]


@pytest.mark.parametrize('change', ['hash', 'duplicate', 'index', 'label'])
def test_invalid_plans_fail_before_any_request(corpus, change):
    scope = corpus.scope(); plan = plan_for(scope)
    if change == 'hash': plan['scope_sha256'] = '0'*64
    if change == 'duplicate': plan['targets'][1] = plan['targets'][0]
    if change == 'index': plan['targets'][0]['box_number'] = True
    if change == 'label': plan['targets'][0]['expected'] = 'pass'
    with pytest.raises(ValueError):
        prepare_targets(scope, plan, settings(), packet=corpus.packet)


def test_changed_system_prompt_is_rejected_before_network(corpus, tmp_path):
    plan = write_plan(corpus, tmp_path)
    (corpus.packet / 'system_prompt_v1.txt').write_text('Changed after scope preparation.')
    def forbidden(request):
        raise AssertionError('Prompt mismatch must stop before network contact')
    directory = tmp_path / 'mismatch'
    with pytest.raises(ValueError, match='System prompt differs from the source scope'):
        run_probe(corpus.scope_path, plan, directory, settings(), packet=corpus.packet,
                  transport=httpx.MockTransport(forbidden), say=lambda _: None)
    assert not directory.exists()


def test_dry_run_does_not_contact_model_or_create_run(corpus, tmp_path):
    def forbidden(request):
        raise AssertionError('Dry run contacted server')
    directory = tmp_path/'dry'
    summary = run_probe(corpus.scope_path, write_plan(corpus, tmp_path), directory, settings(),
                        dry_run=True, transport=httpx.MockTransport(forbidden), packet=corpus.packet, say=lambda _: None)
    assert summary == {'stage': 'dry-run', 'planned_requests': 4}
    assert not directory.exists()


def test_paired_runner_retains_order_provenance_and_identity_failures(corpus, fake, tmp_path, monkeypatch):
    from pathlib import Path
    original_read = Path.read_bytes
    def no_labels(path):
        assert path != corpus.labels_path, 'Inference must not read labels'
        return original_read(path)
    monkeypatch.setattr(Path, 'read_bytes', no_labels)
    def wrong_identity(count, reply, request):
        if count == 2:
            content = json.loads(reply['message']['content']); content['box_number'] = 8
            reply['message']['content'] = json.dumps(content)
        return reply
    fake.behavior = wrong_identity
    directory = tmp_path/'run'
    summary = run_probe(corpus.scope_path, write_plan(corpus, tmp_path), directory, settings(),
                        transport=fake.transport(), packet=corpus.packet, say=lambda _: None)
    records = read_results(directory)
    assert summary['completed_requests'] == fake.chat_count == 4
    assert [(r['box_number'],r['arm']) for r in records] == [
        (1,'box-crop-v2'), (1,'box-context-v1'), (2,'box-context-v1'), (2,'box-crop-v2')]
    assert records[1]['outcome'] == 'unavailable'
    assert records[1]['error'] == 'identity_mismatch'
    assert records[1]['prediction'] is None
    assert records[0]['object_id'] == records[3]['object_id']  # repeated ID, distinct boxes
    assert all((directory/r['image_path']).exists() for r in records)
    assert fake.max_in_flight == 1


def test_infrastructure_failures_stop_and_existing_run_is_preserved(corpus, fake, tmp_path):
    from backend.experiments.box_grounding.run_files import RunFilesError
    def timeout(count, reply, request):
        raise httpx.ReadTimeout('slow', request=request)
    fake.behavior = timeout
    directory = tmp_path/'run'; plan = write_plan(corpus, tmp_path)
    result = run_probe(corpus.scope_path, plan, directory, settings(), transport=fake.transport(), packet=corpus.packet, say=lambda _: None)
    assert result['stage'] == 'aborted' and result['completed_requests'] == 3
    count = fake.chat_count
    with pytest.raises(RunFilesError):
        run_probe(corpus.scope_path, plan, directory, settings(), transport=fake.transport(), packet=corpus.packet)
    assert fake.chat_count == count


def test_validation_plan_runs_all_boxes_beyond_probe_limit_with_identical_requests(tmp_path, fake):
    specs = tuple(CaseSpec(f'validation-{i:02d}', 'held_out', (8, 6),
                          ((1, 'person', (2, 2, 5, 5)),)) for i in range(1, 10))
    corpus = build_corpus(tmp_path / 'corpus', specs)
    scope = corpus.scope()
    plan = dict(schema='context-validation-plan-v1', scope_sha256=scope.sha256,
                targets=[dict(case_name=c.case_name, box_number=1) for c in scope.cases])
    prepared = prepare_targets(scope, plan, settings(), packet=corpus.packet)
    assert len(prepared) == 9
    for item, target in zip(plan['targets'], prepared):
        probe = {**plan, 'schema': 'context-probe-plan-v1', 'targets': [item]}
        original = prepare_targets(scope, probe, settings(), packet=corpus.packet)[0]
        assert {a:r.body for a,r in target.requests.items()} == {a:r.body for a,r in original.requests.items()}
    with pytest.raises(ValueError, match='one to 8'):
        prepare_targets(scope, {**plan, 'schema': 'context-probe-plan-v1'}, settings(), packet=corpus.packet)
    plan_path = tmp_path / 'validation.json'
    plan_path.write_text(json.dumps(plan))
    directory = tmp_path / 'validation-run'
    summary = run_probe(corpus.scope_path, plan_path, directory, settings(), packet=corpus.packet,
                        transport=fake.transport(), say=lambda _: None)
    assert summary['stage'] == 'completed'
    assert summary['completed_requests'] == fake.chat_count == 18
    records = read_results(directory)
    assert {(r['case_name'],r['box_number'],r['arm']) for r in records} == {
        (c.case_name,1,a) for c in scope.cases for a in ['box-crop-v2','box-context-v1']}
    assert json.loads((directory/'run.json').read_text())['evaluation_role'] == 'full-scope validation'


def test_validation_rejects_omitted_boxes_before_network(corpus, fake, tmp_path):
    plan = {**plan_for(corpus.scope()), 'schema': 'context-validation-plan-v1'}
    plan_path = tmp_path / 'incomplete.json'
    plan_path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match='include every box'):
        run_probe(corpus.scope_path, plan_path, tmp_path/'run', settings(), packet=corpus.packet,
                  transport=fake.transport(), say=lambda _: None)
    assert not fake.calls
