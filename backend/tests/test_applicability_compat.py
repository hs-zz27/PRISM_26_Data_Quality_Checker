"""Historical rules, pins and request builders keep their documented behaviour.

Pins/rules and non-rendered requests retain their original golden hashes. Visual
requests compare to the frozen pre-PR builder in the same rendering environment:
PNG compression and default font rendering are not portable across Pillow builds.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from backend.app.core.config import AssetStorageSettings
from backend.app.models.applicability import RuleApplicability
from backend.app.models.feedback import ExecutionPin, RecordedPrediction, Prediction, RulePin, checked, fingerprint
from backend.app.models.review import RulebookReplace
from backend.app.models.rule import ConfirmationState, Rule, Rulebook
from backend.app.services.feedback_prompt import prepare_payload
from backend.app.services.review import save_rulebook_review
from backend.app.storage.repository import RulebookRepository
from backend.tests.applicability_fixtures import imported, requires_importer, v5_pin, visual_rule
from backend.tests.feedback_fixtures import book

GOLDEN = json.loads((Path(__file__).parent / 'golden' / 'guideline_requests_before_applicability.json').read_text())
VERSIONS = {'annotation-guideline-v1': 'original-png-no-transform-v1',
            'annotation-guideline-v2': 'original-png-no-transform-v1',
            'annotation-guideline-v3': 'original-and-annotation-overlay-v1'}
REQUEST_VERSIONS = {**VERSIONS, 'annotation-guideline-v4': 'original-and-annotation-overlay-v1'}
FROZEN_PROMPT = Path(__file__).parent / 'golden' / 'feedback_prompt_before_pr19.py'
FROZEN_PROMPT_SHA256 = 'ea3a4e1538c0504a9abaa374690067356d8f59586fadcfa3c935a8a749e6d9ec'


def pin(version):
    return ExecutionPin(model='test-vision', model_digest='sha256:' + 'a' * 64, prompt_version=version,
                        preprocessing=REQUEST_VERSIONS[version], num_predict=2048)


def digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def historical_builder():
    # Exact source from main 7aca2fed5e6216fa4bdc5decf9fa5efa104ae103, before PR #19.
    # Relative imports intentionally use the application's package; rendering occurs
    # on this machine for both sides, while the old prompt/assembly code is frozen.
    assert hashlib.sha256(FROZEN_PROMPT.read_bytes()).hexdigest() == FROZEN_PROMPT_SHA256
    spec = importlib.util.spec_from_file_location('backend.app.services._prompt_before_pr19', FROZEN_PROMPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.prepare_payload


@pytest.mark.parametrize('version', sorted(VERSIONS))
def test_historical_execution_pins_serialize_and_fingerprint_exactly_as_before(version):
    current = pin(version)
    assert current.model_dump(mode='json') == GOLDEN[f'{version}:pin_dump']
    assert fingerprint(current) == GOLDEN[f'{version}:pin_sha256']
    assert checked(ExecutionPin, current) == current


@requires_importer
@pytest.mark.parametrize('format', ['coco', 'labelme'])
@pytest.mark.parametrize('version', sorted(REQUEST_VERSIONS))
def test_historical_requests_match_the_frozen_builder_in_this_rendering_environment(tmp_path, format, version):
    pubs, settings, _, _ = imported(tmp_path, format)
    target = RulePin(rulebook_id=book().rulebook_id, rulebook_version=1, rule=book().rules[0])
    payload = prepare_payload(pubs[0].manifest, target, [], pin(version), AssetStorageSettings(root=settings.asset_root))
    expected = historical_builder()(pubs[0].manifest, target, [], pin(version),
                                    AssetStorageSettings(root=settings.asset_root))
    # Compare every field, including image bytes, rather than dropping the overlay
    # or updating Linux-generated PNG hashes to whatever this machine produces.
    assert payload == expected
    if version in {'annotation-guideline-v1', 'annotation-guideline-v2'}:
        assert digest(payload) == GOLDEN[f'{format}:{version}:payload_sha256']


def test_a_rule_saved_before_applicability_existed_keeps_its_json_and_semantic_hash():
    rule = book().rules[0]
    assert rule.model_dump(mode='json') == GOLDEN['rule_dump']
    assert 'applicability' not in rule.model_dump(mode='json')
    assert fingerprint(rule) == GOLDEN['rule_semantic_sha256']
    assert Rule.model_validate(GOLDEN['rule_dump']) == rule


def test_a_rule_with_applicability_pins_it_in_its_semantic_hash():
    base = book().rules[0]
    with_scope = base.model_copy(update={'applicability': RuleApplicability(
        geometry=['polygon'], source='reviewer', basis='Reviewer decided.')})
    assert with_scope.model_dump(mode='json')['applicability']['geometry'] == ['polygon']
    assert fingerprint(with_scope) != fingerprint(base)
    pinned = RulePin(rulebook_id='book', rulebook_version=1, rule=with_scope)
    assert pinned.rule.applicability == with_scope.applicability
    assert RulePin.model_validate(pinned.model_dump(mode='json')) == pinned


def test_a_recorded_prediction_from_before_has_no_scope_key_and_still_validates():
    recorded = RecordedPrediction(prediction=Prediction(case_id='a' * 64, rule_id='R', outcome='unavailable',
                                  reason='x', evidence_references=[]), duration_ms=1.0, model_verified=False)
    assert 'scope' not in recorded.model_dump(mode='json')
    assert checked(RecordedPrediction, recorded) == recorded


def test_v5_is_a_distinct_pin_with_the_same_evidence_views_and_v3_v4_stay_valid():
    assert v5_pin().prompt_version == 'annotation-guideline-v5'
    assert fingerprint(v5_pin()) != fingerprint(pin('annotation-guideline-v3'))
    v4 = ExecutionPin.model_validate({**v5_pin().model_dump(), 'prompt_version': 'annotation-guideline-v4'})
    assert fingerprint(v5_pin()) != fingerprint(v4)
    with pytest.raises(ValueError):
        ExecutionPin(model='m', model_digest='sha256:' + 'a' * 64, prompt_version='annotation-guideline-v5',
                     preprocessing='original-png-no-transform-v1')


@requires_importer
def test_v4_observation_protocol_and_v5_scope_protocol_remain_distinct(tmp_path):
    pubs, settings, _, _ = imported(tmp_path, 'labelme')
    target = RulePin(rulebook_id='book', rulebook_version=1,
                     rule=visual_rule('R-1', 'The polygon follows the visible person.'))
    assets = AssetStorageSettings(root=settings.asset_root)
    v4 = ExecutionPin.model_validate({**v5_pin().model_dump(), 'prompt_version': 'annotation-guideline-v4'})
    old = prepare_payload(pubs[0].manifest, target, [], v4, assets)
    new = prepare_payload(pubs[0].manifest, target, [], v5_pin(), assets)
    old_context = json.loads(old['messages'][-1]['content'])
    new_context = json.loads(new['messages'][-1]['content'])
    assert old['messages'][1:3] == new['messages'][1:3]  # same original and overlay
    assert 'observations' in old['format']['properties'] and 'assessments' not in old['format']['properties']
    assert 'assessments' in new['format']['properties'] and 'observations' not in new['format']['properties']
    assert 'object_inventory' in old_context['target']['inputs']
    assert 'requirement_scope' not in old_context
    assert 'object_inventory' not in new_context['target']['inputs']
    assert [row['object_key'] for row in new_context['requirement_scope']['eligible_targets']] == ['/shapes/0']


# ------------------------------------------------------------------ review preservation

def draft_with_applicability():
    rule = visual_rule('R-1', 'The polygon follows the visible person.', applicability=RuleApplicability(
        geometry=['polygon'], source='reviewer', basis='Reviewer decided.')).model_copy(
        update={'confirmation_state': ConfirmationState.DRAFT})
    return Rulebook(rulebook_id='book', version=1, guideline_text=rule.description, rules=[rule])


def repository(book_):
    repo = Mock(spec_set=RulebookRepository)
    repo.get_rulebook.return_value = book_
    repo.save_rulebook_review.side_effect = lambda value, **kwargs: value.model_copy(deep=True)
    return repo


def replace_request(book_, rule_document):
    return RulebookReplace.model_validate({'expected_version': 1, 'reviewer_id': 'reviewer_1',
                                           'guideline_text': book_.guideline_text, 'rules': [rule_document]})


def test_a_client_that_does_not_know_about_applicability_cannot_drop_it():
    book_ = draft_with_applicability()
    document = book_.rules[0].model_dump(mode='json')
    del document['applicability']
    result = save_rulebook_review('book', replace_request(book_, document), repository=repository(book_))
    assert result.rules[0].applicability == book_.rules[0].applicability


def test_a_reviewer_can_replace_or_explicitly_clear_applicability():
    book_ = draft_with_applicability()
    document = book_.rules[0].model_dump(mode='json')
    document['applicability'] = {'geometry': ['box'], 'combine': 'all', 'source': 'reviewer', 'status': 'clear',
                                 'basis': 'Reviewer changed this to boxes.'}
    changed = save_rulebook_review('book', replace_request(book_, document), repository=repository(book_))
    assert changed.rules[0].applicability.geometry == ['box'] and changed.rules[0].applicability.source == 'reviewer'
    document['applicability'] = None
    cleared = save_rulebook_review('book', replace_request(book_, document), repository=repository(book_))
    assert cleared.rules[0].applicability is None


def test_a_confirmed_rulebook_keeps_applicability_through_serialization():
    rule = visual_rule('R-1', 'The polygon follows the visible person.', applicability=RuleApplicability(
        geometry=['polygon'], source='reviewer', basis='Reviewer decided.'))
    confirmed = Rulebook(rulebook_id='book', version=3, guideline_text=rule.description, rules=[rule],
                         confirmation_state=ConfirmationState.CONFIRMED)
    restored = Rulebook.model_validate_json(confirmed.model_dump_json())
    assert restored == confirmed and restored.rules[0].applicability.geometry == ['polygon']
