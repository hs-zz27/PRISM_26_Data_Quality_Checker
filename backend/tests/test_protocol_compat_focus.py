"""Protocols v3, v4 and v5, saved pins and shared rendering are unchanged by the focused protocol (v6).

The golden file holds platform-independent values captured from branch commit b4c14de BEFORE v6 existed.
Request bytes (which include rendered PNGs) are compared with a checksum-locked frozen copy of the request
builder on the same machine, as the earlier compatibility tests do. Do not refresh either file to make a failing
test pass.
"""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from backend.app.core.config import AssetStorageSettings
from backend.app.core.feedback import FeedbackError, load_execution_pin
from backend.app.models.feedback import (
    ExecutionPin, FocusedAssessment, ObjectAssessment, RulePin, TargetedPrediction, checked, fingerprint)
from backend.app.models.visual_assessment import VisualAssessment
from backend.app.services.feedback_prompt import prepare_payload
from backend.app.services.rule_applicability import derive_applicability
from backend.tests.applicability_fixtures import imported, requires_importer, visual_rule

GOLDEN_DIR = Path(__file__).parent / 'golden'
GOLDEN = json.loads((GOLDEN_DIR / 'protocols_before_focus.json').read_text())
FROZEN_PROMPT = GOLDEN_DIR / 'feedback_prompt_before_focus.py'
FROZEN_PROMPT_SHA256 = 'e5643e0758bea974bc64c6c5877dbb45d0f4adfc1ccd3bf90cb2776aa215d5ce'      # LF-normalised source of feedback_prompt.py at b4c14de
OVERLAY_SHA256 = '6bf24daf48efc5fb66d917b6ecdcbf4967abb54dd8700a4cb9939739b9890b91'           # LF-normalised source of the shared annotation_overlay.py
VERSIONS = ['annotation-guideline-v3', 'annotation-guideline-v4', 'annotation-guideline-v5']
SHARED = 'original-and-annotation-overlay-v1'


def lf(data: bytes) -> bytes:
    return data.replace(b'\r\n', b'\n')


def pin(version, preprocessing=SHARED):
    return ExecutionPin(model='test-vision', model_digest='sha256:' + 'a' * 64, prompt_version=version,
                        preprocessing=preprocessing, num_predict=2048)


def frozen_builder():
    assert hashlib.sha256(lf(FROZEN_PROMPT.read_bytes())).hexdigest() == FROZEN_PROMPT_SHA256
    spec = importlib.util.spec_from_file_location('backend.app.services._prompt_before_focus', FROZEN_PROMPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.prepare_payload


@pytest.mark.parametrize('version', VERSIONS)
def test_saved_execution_pins_serialize_and_fingerprint_exactly_as_before(version):
    current = pin(version)
    assert current.model_dump(mode='json') == GOLDEN[f'{version}:pin_dump']
    assert fingerprint(current) == GOLDEN[f'{version}:pin_sha256']
    assert checked(ExecutionPin, current) == current


@requires_importer
@pytest.mark.parametrize('format', ['coco', 'labelme'])
@pytest.mark.parametrize('version', VERSIONS)
def test_v3_v4_and_v5_requests_match_the_frozen_builder_field_for_field(tmp_path, format, version):
    pubs, settings, _, _ = imported(tmp_path, format)
    target = RulePin(rulebook_id='book', rulebook_version=1,
                     rule=visual_rule('R-1', 'The polygon follows the visible outline of the person.'))
    assets = AssetStorageSettings(root=settings.asset_root)
    assert prepare_payload(pubs[0].manifest, target, [], pin(version), assets) == \
        frozen_builder()(pubs[0].manifest, target, [], pin(version), assets)


def test_derived_v1_applicability_is_frozen_for_every_captured_requirement():
    for text, expected in GOLDEN['derive_v1'].items():
        assert derive_applicability(text).model_dump(mode='json') == expected
        assert derive_applicability(text, 'derived-v1').model_dump(mode='json') == expected


def test_v4_and_v5_response_schemas_keep_their_shape_and_v6_is_a_separate_schema():
    expected = GOLDEN['schema_sha256']
    digest = lambda model: hashlib.sha256(json.dumps(model.model_json_schema(), sort_keys=True).encode()).hexdigest()
    assert digest(VisualAssessment) == expected['VisualAssessment(v4)']
    assert digest(TargetedPrediction) == expected['TargetedPrediction(v5)']
    assert digest(ObjectAssessment) == expected['ObjectAssessment(v5)']
    focused = {'case_id': 'a' * 64, 'rule_id': 'R', 'object_key': '/shapes/0', 'condition': 'applies', 'compliance': 'yes',
               'geometry': 'polygon', 'reason': 'Fine.', 'evidence_references': ['image']}
    assert FocusedAssessment.model_validate(focused)
    for older in (VisualAssessment, TargetedPrediction):
        with pytest.raises(ValueError):
            older.model_validate_json(json.dumps(focused))
    v5_reply = {'case_id': 'a' * 64, 'rule_id': 'R', 'outcome': 'pass', 'reason': 'Fine.', 'evidence_references': ['image'],
                'assessments': [{'object_key': '/shapes/0', 'outcome': 'pass', 'geometry': 'polygon',
                                 'condition': 'applies', 'reason': 'Fine.'}]}
    with pytest.raises(ValueError):
        FocusedAssessment.model_validate_json(json.dumps(v5_reply))


@pytest.mark.parametrize('version', VERSIONS)
def test_older_versions_reject_the_focused_preprocessing_and_v6_requires_it(version):
    with pytest.raises(ValueError):
        pin(version, 'target-focus-overlay-v1')
    with pytest.raises(ValueError):
        pin('annotation-guideline-v6', SHARED)
    assert pin('annotation-guideline-v6', 'target-focus-overlay-v1').prompt_version == 'annotation-guideline-v6'


def test_new_audits_keep_defaulting_to_v5_and_v6_is_opt_in_with_its_own_preprocessing(monkeypatch):
    monkeypatch.setenv('FEEDBACK_MODEL_DIGEST', 'sha256:' + 'b' * 64)
    monkeypatch.delenv('FEEDBACK_PROMPT_VERSION', raising=False)
    assert load_execution_pin().prompt_version == 'annotation-guideline-v5'
    monkeypatch.setenv('FEEDBACK_PROMPT_VERSION', 'annotation-guideline-v6')
    selected = load_execution_pin()
    assert (selected.prompt_version, selected.preprocessing) == ('annotation-guideline-v6', 'target-focus-overlay-v1')
    for version in ('annotation-guideline-v3', 'annotation-guideline-v4'):
        monkeypatch.setenv('FEEDBACK_PROMPT_VERSION', version)
        assert load_execution_pin().preprocessing == SHARED
    monkeypatch.setenv('FEEDBACK_PROMPT_VERSION', 'annotation-guideline-v7')
    with pytest.raises(FeedbackError):
        load_execution_pin()


def test_the_shared_overlay_used_by_corrections_and_v3_to_v5_is_byte_for_byte_unchanged():
    source = Path(__file__).parents[1] / 'app' / 'services' / 'annotation_overlay.py'
    assert hashlib.sha256(lf(source.read_bytes())).hexdigest() == OVERLAY_SHA256
