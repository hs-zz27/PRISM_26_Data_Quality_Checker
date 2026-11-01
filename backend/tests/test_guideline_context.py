"""Guideline extraction keeps requirements and their context together, through to audit payloads.

The model's output is mocked here: these tests check what the backend does with a
response (classification, context attachment, guards, coverage, persistence and
audit/recheck payloads). They cannot establish the real model's extraction quality.
"""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.app.inference.rule_proposals import expand_response, extraction_catalog, source_units
from backend.app.models.feedback import FeedbackRuntime, RulePin, fingerprint
from backend.app.models.review import RulebookAction, RulebookReplace
from backend.app.models.review_feedback import ImageReviewFeedback
from backend.app.models.rule import ConfirmationState, Rule, Rulebook, RuleOrigin
from backend.app.services.enclosure_prompt import build_request
from backend.app.services.evaluation import RuleEvaluationResult
from backend.app.services.feedback import augment_evaluation
from backend.app.services.feedback_prompt import prepare_payload
from backend.app.services.guideline_context import CONTEXT_USE
from backend.app.services.review import confirm_rulebook, save_rulebook_review
from backend.tests.feedback_fixtures import assets, case, inputs, record


def cid(name):
    return next(entry['check_id'] for entry in extraction_catalog() if entry['check_name'] == name)


VISUAL, VOCAB, BOXES = cid('annotation_guideline'), cid('allowed_labels'), cid('box_validity')


def respond(lines, rules):
    """lines: {source_id: role}; rules: (source_id, check_id, parameters, context_ids)."""
    return json.dumps({
        'lines': [{'source_id': i, 'role': role} for i, role in lines.items()],
        'rules': [{'source_id': s, 'context_ids': list(ctx), 'check_id': c, 'parameters': p}
                  for s, c, p, ctx in rules],
    })


def extract(text, lines, rules):
    return expand_response(respond(lines, rules), source_units(text))


def as_book(text, proposals):
    return Rulebook(rulebook_id='book_ctx', guideline_text=text, rules=[
        Rule(**p.model_dump(), origin=RuleOrigin.EXTRACTED) for p in proposals])


# A guideline in the same shape as the development file, but with different classes and wording.
ANIMALS = [
    "1. Scope: these rules cover dog and cat annotations, judged by what the image shows, even if a label says otherwise.",
    "2. A dog is a domestic canine; dogs in paintings or on screens are not dogs.",
    "3. A cat is a domestic feline.",
    "4. Every label must name the animal that is actually visible.",
    "5. Allowed labels are dog and cat.",
    "6. A fully visible animal's box must include its tail.",
    "7. For a partly hidden animal, annotate only the visible part.",
    "8. Boxes may extend at most 3 pixels beyond the visible animal; this tolerance needs reviewer approval.",
    "9. If the image is too unclear to decide, report the result as uncertain for a reviewer.",
]
ANIMAL_TEXT = "\n".join(ANIMALS)
ANIMAL_LINES = {1: 'context', 2: 'context', 3: 'context', 4: 'requirement', 5: 'requirement',
                6: 'requirement', 7: 'requirement_and_context', 8: 'requirement', 9: 'context'}


def animal_rules(overrides=None):
    rules = {4: (4, VISUAL, {}, []), 5: (5, VOCAB, {'allowed_labels': ['dog', 'cat']}, []),
             6: (6, VISUAL, {}, [7]), 7: (7, VISUAL, {}, []), 8: (8, 0, {}, [])}
    rules.update(overrides or {})
    return list(rules.values())


# --------------------------------------------------------------- definitions and context

def test_definition_only_lines_become_context_not_standalone_checks():
    proposals = extract(ANIMAL_TEXT, ANIMAL_LINES, animal_rules())
    sources = [p.source_sentence for p in proposals]
    for definition in (ANIMALS[0], ANIMALS[1], ANIMALS[2], ANIMALS[8]):
        assert definition not in sources                   # no separate pass/fail rule
    assert sorted(sources) == sorted(ANIMALS[i] for i in (3, 4, 5, 6, 7))   # every obligation kept


def test_visual_rules_receive_scope_definitions_exceptions_and_uncertainty_even_without_context_ids():
    proposals = {p.source_sentence: p for p in extract(ANIMAL_TEXT, ANIMAL_LINES, animal_rules())}
    label_rule = proposals[ANIMALS[3]]
    assert label_rule.check_name == 'annotation_guideline'
    # The model cited nothing for line 4; document context is attached deterministically.
    assert label_rule.context_sentences == [ANIMALS[0], ANIMALS[1], ANIMALS[2], ANIMALS[6], ANIMALS[8]]
    assert label_rule.description == ANIMALS[3]       # the requirement itself, not pasted context
    tail_rule = proposals[ANIMALS[5]]
    assert ANIMALS[6] in tail_rule.context_sentences   # occlusion: visible-only annotation
    assert ANIMALS[8] in tail_rule.context_sentences   # uncertainty policy reaches the check


def test_a_line_with_a_definition_and_an_obligation_is_both_a_rule_and_context():
    proposals = {p.source_sentence: p for p in extract(ANIMAL_TEXT, ANIMAL_LINES, animal_rules())}
    occlusion = proposals[ANIMALS[6]]
    assert occlusion.check_name == 'annotation_guideline'          # its own obligation is checked
    assert ANIMALS[6] not in occlusion.context_sentences           # never context for itself
    assert ANIMALS[6] in proposals[ANIMALS[3]].context_sentences   # and it qualifies other visual rules


def test_deterministic_checks_do_not_receive_document_context():
    proposals = {p.source_sentence: p for p in extract(ANIMAL_TEXT, ANIMAL_LINES, animal_rules())}
    vocabulary = proposals[ANIMALS[4]]
    assert vocabulary.check_name == 'allowed_labels' and vocabulary.context_sentences == []


def test_unsupported_requirements_keep_context_for_the_reviewer():
    proposals = {p.source_sentence: p for p in extract(ANIMAL_TEXT, ANIMAL_LINES, animal_rules())}
    tolerance = proposals[ANIMALS[7]]
    assert tolerance.check_name is None and ANIMALS[0] in tolerance.context_sentences


# --------------------------------------------------------------- input shapes and generality

def test_numbered_and_unnumbered_input_give_the_same_rules_with_exact_text():
    numbered = "1) A cat is a domestic feline.\n  2) Every label must name the visible animal.  "
    plain = "A cat is a domestic feline.\nEvery label must name the visible animal."
    for text in (numbered, plain):
        lines = source_units(text)
        [rule] = extract(text, {1: 'context', 2: 'requirement'}, [(2, VISUAL, {}, [])])
        assert rule.source_sentence == lines[1] and rule.source_sentence in text
        assert rule.context_sentences == [lines[0]]


def test_several_requirements_in_one_paragraph_each_become_a_rule():
    text = "Allowed labels are dog and cat, every label must name the visible animal, and boxes stay inside the image."
    rules = extract(text, {1: 'requirement'}, [(1, VOCAB, {'allowed_labels': ['dog', 'cat']}, []),
                                               (1, VISUAL, {}, []), (1, BOXES, {}, [])])
    assert [r.check_name for r in rules] == ['allowed_labels', 'annotation_guideline', 'box_validity']
    assert all(r.source_sentence == text and r.description == text for r in rules)


def test_reordered_guidelines_with_other_classes_attach_context_the_same_way():
    text = "\n".join([ANIMALS[3], ANIMALS[2], ANIMALS[0]])      # requirement first, definitions later
    [rule] = extract(text, {1: 'requirement', 2: 'context', 3: 'context'}, [(1, VISUAL, {}, [])])
    assert rule.context_sentences == [ANIMALS[2], ANIMALS[0]]   # document order, wherever they appear


# --------------------------------------------------------------- visual label vs vocabulary

def test_visual_label_matching_and_allowed_vocabulary_stay_separate_rules():
    proposals = {p.source_sentence: p for p in extract(ANIMAL_TEXT, ANIMAL_LINES, animal_rules())}
    assert proposals[ANIMALS[3]].check_name == 'annotation_guideline' and proposals[ANIMALS[3]].parameters == {}
    assert proposals[ANIMALS[4]].check_name == 'allowed_labels'
    assert proposals[ANIMALS[4]].parameters == {'allowed_labels': ['dog', 'cat']}


@pytest.mark.parametrize('text,labels', [
    ("Label each object as the animal it shows, for example dog or cat.", ['dog', 'cat']),   # examples only
    ("Allowed labels are dog and cat.", ['dog', 'cat', 'horse']),                             # invented label
])
def test_examples_and_invented_names_never_become_an_allowed_label_list(text, labels):
    [rule] = extract(text, {1: 'requirement'}, [(1, VOCAB, {'allowed_labels': labels}, [])])
    assert rule.check_name is None and rule.parameters == {}
    assert rule.source_sentence == text and 'allowed_labels was not accepted' in rule.extraction_notes[0]


# --------------------------------------------------------------- exact numeric requirements

@pytest.mark.parametrize('check,parameters', [
    (VISUAL, {}),                                                  # visual judgement cannot measure 3 px
    (cid('box_mask_alignment'), {'max_edge_difference': 3}),       # box-vs-mask is not box-vs-visible-animal
])
def test_exact_pixel_tolerance_stays_unsupported_and_visible(check, parameters):
    [rule] = extract(ANIMALS[7], {1: 'requirement'}, [(1, check, parameters, [])])
    assert rule.check_name is None and rule.parameters == {}
    assert rule.source_sentence == ANIMALS[7]                      # units and approval condition verbatim
    assert '3 pixels' in rule.source_sentence and 'reviewer approval' in rule.source_sentence
    assert rule.extraction_notes and 'kept unsupported' in rule.extraction_notes[0].lower()


def test_numeric_requirement_with_a_matching_measurement_is_accepted():
    text = "Each mask may have at most 2 connected components, and at most 5% of a mask may lie outside its box."
    rules = extract(text, {1: 'requirement'}, [(1, cid('mask_components'), {'max_component_count': 2}, []),
                                               (1, cid('mask_outside_box'), {'max_outside_fraction': 0.05}, [])])
    assert [(r.check_name, r.parameters) for r in rules] == [
        ('mask_components', {'max_component_count': 2}), ('mask_outside_box', {'max_outside_fraction': 0.05})]
    assert all(not r.extraction_notes for r in rules)


def test_a_threshold_not_stated_in_the_text_is_never_accepted():
    [rule] = extract("Masks should mostly stay inside their boxes.", {1: 'requirement'},
                     [(1, cid('mask_outside_box'), {'max_outside_fraction': 0.1}, [])])
    assert rule.check_name is None and 'not stated' in rule.extraction_notes[0]


# --------------------------------------------------------------- coverage and incomplete responses

def test_an_unclassified_unreferenced_line_is_kept_for_review_not_dropped():
    text = "Every label must name the visible animal.\nBoxes must include the tail."
    rules = extract(text, {1: 'requirement'}, [(1, VISUAL, {}, [])])
    assert [r.source_sentence for r in rules] == source_units(text)
    assert rules[1].check_name is None and 'did not classify' in rules[1].extraction_notes[0]


def test_a_requirement_without_any_mapping_is_kept_for_review():
    rules = extract("Boxes must include the tail.", {1: 'requirement'}, [])
    assert len(rules) == 1 and rules[0].check_name is None
    assert 'no check mapping' in rules[0].extraction_notes[0]


def test_an_empty_rule_list_is_not_presented_as_full_coverage():
    rules = extract("Boxes must include the tail.\nA cat is a feline.", {}, [])
    assert [r.check_name for r in rules] == [None, None] and all(r.extraction_notes for r in rules)


@pytest.mark.parametrize('content', [
    respond({1: 'context'}, [(1, VISUAL, {}, [])]),                       # rule cites a context-only line
    respond({1: 'requirement'}, [(1, VISUAL, {}, [])]).replace('"lines": [', '"lines": [{"source_id": 1, "role": "other"}, '),
    respond({3: 'requirement'}, []),                                        # unknown line
    respond({1: 'definition'}, []),                                         # unknown role
    respond({1: 'requirement'}, [(1, VISUAL, {}, [5])]),                    # invented context reference
    respond({1: 'requirement'}, [(1, cid('mask_outside_box'), {'max_outside_fraction': 1.5}, [])]),
    respond({1: 'requirement'}, [(1, 0, {'max_iou': 0.5}, [])]),            # parameters on an unmapped rule
    json.dumps({'rules': []}),                                              # classification missing
])
def test_invalid_or_contradictory_responses_are_rejected(content):
    with pytest.raises(ValueError):
        expand_response(content, ["Every label must name the visible animal.", "A cat is a feline."])


# --------------------------------------------------------------- persistence, review and confirmation

class MemoryRepository:
    def __init__(self, book):
        self.book = book

    def get_rulebook(self, rulebook_id, version=None):
        return self.book

    def save_rulebook_review(self, value, **kwargs):
        self.book = value.model_copy(deep=True)
        return self.book


def test_context_survives_saving_a_reviewer_edit_from_a_client_unaware_of_it_and_confirming():
    book = as_book(ANIMAL_TEXT, extract(ANIMAL_TEXT, ANIMAL_LINES, animal_rules()))
    repository = MemoryRepository(book)
    visual = next(r for r in book.rules if r.source_sentence == ANIMALS[3])
    # What today's review screen sends: the edited rule rebuilt without the new fields.
    edited = []
    for rule in book.rules:
        values = {k: v for k, v in rule.model_dump(mode='json').items()
                  if k not in ('context_sentences', 'extraction_notes')}
        values['confirmation_state'] = 'confirmed'
        if rule.rule_id == visual.rule_id:
            values['description'] = 'Labels must name the animal that is really visible.'
        edited.append(Rule.model_validate(values))
    saved = save_rulebook_review('book_ctx', RulebookReplace(
        expected_version=1, reviewer_id='reviewer', guideline_text=ANIMAL_TEXT, rules=edited), repository=repository)
    kept = next(r for r in saved.rules if r.rule_id == visual.rule_id)
    assert kept.description.startswith('Labels must name')
    assert kept.context_sentences == visual.context_sentences          # not silently erased
    tolerance = next(r for r in saved.rules if r.source_sentence == ANIMALS[7])
    assert tolerance.check_name is None and tolerance.context_sentences   # still unsupported, still reviewable
    confirmed = confirm_rulebook('book_ctx', RulebookAction(expected_version=2, reviewer_id='reviewer'),
                                 repository=repository)
    assert confirmed.confirmation_state is ConfirmationState.CONFIRMED
    pinned = RulePin(rulebook_id='book_ctx', rulebook_version=3,
                     rule=next(r for r in confirmed.rules if r.rule_id == visual.rule_id))
    assert pinned.rule.context_sentences == visual.context_sentences   # the audit target carries it


def test_an_explicit_empty_list_clears_context_on_purpose():
    book = as_book(ANIMAL_TEXT, extract(ANIMAL_TEXT, ANIMAL_LINES, animal_rules()))
    repository = MemoryRepository(book)
    rules = [rule.model_copy(update={'context_sentences': []}, deep=True) for rule in book.rules]
    rules = [Rule.model_validate({**r.model_dump(mode='json'), 'context_sentences': []}) for r in rules]
    saved = save_rulebook_review('book_ctx', RulebookReplace(
        expected_version=1, reviewer_id='reviewer', guideline_text=ANIMAL_TEXT, rules=rules), repository=repository)
    assert all(rule.context_sentences == [] for rule in saved.rules)


@pytest.mark.parametrize('context', [['Invented context.'], [ANIMALS[0], ANIMALS[0]], [ANIMALS[3]]])
def test_context_must_be_exact_distinct_guideline_text_and_not_the_rule_itself(context):
    with pytest.raises(ValidationError):
        Rulebook(rulebook_id='b', guideline_text=ANIMAL_TEXT, rules=[Rule(
            rule_id='R', description=ANIMALS[3], check_name='annotation_guideline', origin='extracted',
            source_sentence=ANIMALS[3], context_sentences=context)])


def test_rules_without_context_serialize_and_hash_exactly_as_before():
    rule = Rule(rule_id='R', description='d', check_name='annotation_guideline', origin='reviewer',
                confirmation_state='confirmed')
    legacy = {'rule_id': 'R', 'description': 'd', 'check_name': 'annotation_guideline', 'parameters': {},
              'origin': 'reviewer', 'source_sentence': None, 'confirmation_state': 'confirmed'}
    assert rule.model_dump(mode='json') == legacy
    assert RulePin(rulebook_id='b', rulebook_version=1, rule=rule).semantic_sha256 == fingerprint(legacy)


# --------------------------------------------------------------- actual audit and recheck payloads

def context_target(tmp_path):
    text = "\n".join([ANIMALS[0], ANIMALS[3], ANIMALS[8]])
    rule = Rule(rule_id='LABEL', description=ANIMALS[3], check_name='annotation_guideline', origin='extracted',
                source_sentence=ANIMALS[3], context_sentences=[ANIMALS[0], ANIMALS[8]], confirmation_state='confirmed')
    book = Rulebook(rulebook_id='book_ctx', guideline_text=text, rules=[rule], confirmation_state='confirmed')
    frozen = inputs(tmp_path)
    manifest = frozen.truth.cases[0].manifest
    return book, RulePin(rulebook_id='book_ctx', rulebook_version=1, rule=rule), manifest, frozen


def test_audit_payload_contains_the_rule_context_and_how_to_use_it(tmp_path):
    book, target, manifest, frozen = context_target(tmp_path)
    payload = prepare_payload(manifest, target, [], frozen.runtime, assets(tmp_path))
    content = json.loads(payload['messages'][1]['content'])
    assert content['guideline']['description'] == ANIMALS[3]
    assert content['guideline']['context_sentences'] == [ANIMALS[0], ANIMALS[8]]
    assert content['guideline_context_use'] == CONTEXT_USE


def test_recheck_payload_with_reviewer_feedback_keeps_the_context(tmp_path):
    book, target, manifest, frozen = context_target(tmp_path)
    feedback = ImageReviewFeedback(reviewer_id='reviewer', source_audit_id='audit_parent',
                                   comment='The animal behind the sofa may be a cat.')
    content = json.loads(prepare_payload(manifest, target, [], frozen.runtime, assets(tmp_path),
                                         reviewer_feedback=feedback)['messages'][1]['content'])
    assert content['guideline']['context_sentences'] == [ANIMALS[0], ANIMALS[8]]
    assert content['guideline_context_use'] == CONTEXT_USE and content['reviewer_feedback']['comment']


def test_requests_for_rules_without_context_are_unchanged(tmp_path):
    frozen = inputs(tmp_path)
    target = frozen.truth.cases[0]
    content = json.loads(prepare_payload(target.manifest, target.target, [], frozen.runtime,
                                         assets(tmp_path))['messages'][1]['content'])
    assert 'guideline_context_use' not in content and 'context_sentences' not in content['guideline']


def test_the_real_audit_path_sends_context_to_the_model_and_records_it(tmp_path, monkeypatch):
    import httpx
    from backend.app.inference.annotation_guideline import GuidelineClient
    book, target, manifest, frozen = context_target(tmp_path)
    sent = []

    def handler(request):
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': frozen.runtime.model, 'digest': 'a' * 64}]})
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'capabilities': ['vision']})
        sent.append(json.loads(request.content))
        prediction = record(manifest, target, 'unsure').prediction.model_dump(mode='json')
        return httpx.Response(200, json={'model': frozen.runtime.model, 'done': True, 'done_reason': 'stop',
                                         'prompt_eval_count': 1024,
                                         'message': {'role': 'assistant', 'content': json.dumps(prediction)}})

    monkeypatch.setattr('backend.app.services.feedback.GuidelineClient',
                        lambda pin: GuidelineClient(pin, transport=httpx.MockTransport(handler)))
    result = augment_evaluation(RuleEvaluationResult(findings=[], internal_check_failures=0), book, manifest,
                                FeedbackRuntime(execution=frozen.runtime), assets(tmp_path),
                                audit_id='audit_ctx', sample_id=manifest.sample_id, sample_revision=1)
    [chat] = sent
    content = json.loads(chat['messages'][1]['content'])
    assert content['guideline']['context_sentences'] == [ANIMALS[0], ANIMALS[8]]
    assert content['guideline_context_use'] == CONTEXT_USE
    [finding] = result.findings
    assert finding.outcome.value == 'unsure'
    assert finding.evidence.measurements['guideline_context'] == [ANIMALS[0], ANIMALS[8]]


def test_enclosure_requests_carry_the_rule_context():
    from backend.tests.test_enclosure_core import RULE, CASE_ID, box, picture, pin
    from backend.app.services.enclosure_views import ViewLimits, render_views
    data, _ = picture()
    [view] = render_views(data, [box((5, 5, 20, 20))], (1, 1), ViewLimits())
    rule = dict(RULE, context_sentences=['Pedestrians on posters are not pedestrians.'])
    content = json.loads(build_request(pin(), rule, CASE_ID, view).payload['messages'][1]['content'])
    assert content['guideline']['context_sentences'] == rule['context_sentences']
    assert content['guideline_context_use'] == CONTEXT_USE
    plain = json.loads(build_request(pin(), RULE, CASE_ID, view).payload['messages'][1]['content'])
    assert 'guideline_context_use' not in plain


def test_correction_proposal_requests_carry_every_rule_with_its_context(tmp_path):
    from backend.app.services.review_correction import propose_correction
    book, target, manifest, frozen = context_target(tmp_path)
    captured = []

    class Client:
        def __init__(self, execution): pass
        def verify(self): pass
        def close(self): pass
        def request(self, method, path, payload):
            captured.append(json.loads(payload['messages'][1]['content']))
            raise RuntimeError('stop after capturing the request')

    measurements = {'objects': [{'annotation': {'object_id': 1, 'label': 'cat', 'bbox': [1, 1, 4, 3]}}]}
    feedback = ImageReviewFeedback(reviewer_id='reviewer', source_audit_id='audit_parent',
                                   comment='Check the label.', propose_correction=True)
    with pytest.raises(RuntimeError):
        propose_correction(measurements, feedback, manifest, book, assets(tmp_path), frozen.runtime,
                           client_factory=Client)
    [context] = captured
    assert context['guidelines'][0]['context_sentences'] == [ANIMALS[0], ANIMALS[8]]


def test_the_full_development_guideline_shape_keeps_every_obligation(tmp_path):
    """The team's 12-line development file, with a response shaped like a correct extraction."""
    from pathlib import Path
    text = (Path(__file__).resolve().parents[2] / 'evaluation/annotation_error_pack/guidelines.txt').read_text(encoding='utf-8')
    lines = source_units(text)
    roles = {1: 'context', 2: 'context', 3: 'context', 4: 'requirement', 5: 'requirement', 6: 'requirement',
             7: 'requirement', 8: 'requirement_and_context', 9: 'requirement', 10: 'requirement',
             11: 'requirement', 12: 'context'}
    rules = [(i, VISUAL, {}, []) for i in (4, 5, 6, 7, 8, 10, 11)] + [(9, VISUAL, {}, [])]
    proposals = {p.source_sentence: p for p in expand_response(respond(roles, rules), lines)}
    assert set(proposals) == {lines[i - 1] for i in (4, 5, 6, 7, 8, 9, 10, 11)}     # no definition-only rule
    tolerance = proposals[lines[8]]
    assert tolerance.check_name is None and tolerance.extraction_notes            # 3 px kept unsupported
    for i in (4, 5, 6, 7, 10, 11):
        context = proposals[lines[i - 1]].context_sentences
        assert {lines[0], lines[1], lines[2], lines[7], lines[11]} <= set(context)  # scope, defs, occlusion, uncertainty


# --------------------------------------------------------------- real PostgreSQL and HTTP (opt-in)

from backend.tests.test_repository import database_settings, empty_database_settings  # noqa: E402,F401  (skip without a test DB)


def test_context_round_trips_through_postgres_and_the_http_review_api(database_settings):
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    from backend.app.routers.rulebooks import get_rulebook_repository
    from backend.app.storage.repository import RulebookRepository
    store = RulebookRepository(settings=database_settings)
    store.save_rulebook(as_book(ANIMAL_TEXT, extract(ANIMAL_TEXT, ANIMAL_LINES, animal_rules(
        {8: (8, VISUAL, {}, [])}))))       # the visual mapping of the pixel line gets a backend note
    app = create_app()
    app.dependency_overrides[get_rulebook_repository] = lambda: store
    base = '/api/v1/rulebooks/book_ctx'
    with TestClient(app, raise_server_exceptions=False) as http:
        v1 = http.get(base).json()
        by_source = {r['source_sentence']: r for r in v1['rules']}
        assert by_source[ANIMALS[3]]['context_sentences'][0] == ANIMALS[0]
        assert 'kept unsupported' in by_source[ANIMALS[7]]['extraction_notes'][0]
        assert 'context_sentences' not in by_source[ANIMALS[4]]              # empty fields are omitted
        # A review screen that does not know the new fields edits and confirms every rule.
        rules = [{k: v for k, v in r.items() if k not in ('context_sentences', 'extraction_notes')}
                 | {'confirmation_state': 'confirmed'} for r in v1['rules']]
        response = http.put(base, json={'expected_version': 1, 'reviewer_id': 'reviewer',
                                        'guideline_text': v1['guideline_text'], 'rules': rules})
        assert response.status_code == 200, response.text
        response = http.post(base + '/confirm', json={'expected_version': 2, 'reviewer_id': 'confirmer'})
        assert response.status_code == 200, response.text
    confirmed = store.get_rulebook('book_ctx')
    assert confirmed.confirmation_state is ConfirmationState.CONFIRMED and confirmed.version == 3
    saved = {r.source_sentence: r for r in confirmed.rules}
    assert saved[ANIMALS[3]].context_sentences == by_source[ANIMALS[3]]['context_sentences']
    assert saved[ANIMALS[7]].extraction_notes == by_source[ANIMALS[7]]['extraction_notes']
    pinned = RulePin(rulebook_id='book_ctx', rulebook_version=3, rule=saved[ANIMALS[3]])
    assert pinned.rule.context_sentences[-1] == ANIMALS[8]                # uncertainty reaches the audit target


# --------------------------------------------------------------- compact (v2) and overlay (v3) prompt versions

def context_rule():
    return Rule(rule_id='LABEL', description=ANIMALS[3], check_name='annotation_guideline', origin='extracted',
                source_sentence=ANIMALS[3], context_sentences=[ANIMALS[0], ANIMALS[8]], confirmation_state='confirmed')


def test_compact_rule_keeps_context_and_omits_it_when_absent():
    from backend.app.services.prompt_encoding import compact_rule
    assert compact_rule(context_rule())['context_sentences'] == [ANIMALS[0], ANIMALS[8]]
    plain = Rule(rule_id='R', description='d', check_name='annotation_guideline', origin='reviewer',
                 confirmation_state='confirmed')
    assert 'context_sentences' not in compact_rule(plain)


def test_compact_v2_audit_payload_contains_the_context(tmp_path):
    from backend.tests.test_compact_vision_prompts import pin as v2_pin
    frozen = inputs(tmp_path)
    manifest = frozen.truth.cases[0].manifest
    target = RulePin(rulebook_id='book_ctx', rulebook_version=1, rule=context_rule())
    payload = prepare_payload(manifest, target, [], v2_pin(num_predict=2048), assets(tmp_path))
    content = json.loads(payload['messages'][1]['content'])
    assert content['guideline']['context_sentences'] == [ANIMALS[0], ANIMALS[8]]
    assert content['guideline_context_use'] == CONTEXT_USE


@pytest.mark.parametrize('with_feedback', [False, True])
def test_overlay_v3_audit_and_recheck_payloads_contain_the_context(tmp_path, with_feedback):
    from backend.app.core.config import AssetStorageSettings
    from backend.tests.test_json_dataset_workflow import imported
    from backend.tests.test_visual_audit_prompts import visual_pin
    pubs, settings, _, _ = imported(tmp_path, 'coco')
    storage = AssetStorageSettings(root=settings.asset_root)
    target = RulePin(rulebook_id='book_ctx', rulebook_version=1, rule=context_rule())
    feedback = ImageReviewFeedback(reviewer_id='reviewer', source_audit_id='audit_parent',
                                   comment='The animal may be partly hidden.') if with_feedback else None
    payload = prepare_payload(pubs[0].manifest, target, [], visual_pin(num_predict=2048), storage,
                              **({'reviewer_feedback': feedback} if feedback else {}))
    content = json.loads(payload['messages'][-1]['content'])
    assert content['guideline']['context_sentences'] == [ANIMALS[0], ANIMALS[8]]
    assert content['guideline_context_use'] == CONTEXT_USE
    assert ('reviewer_feedback' in content) is with_feedback


def test_compact_v2_correction_request_carries_rule_context(tmp_path):
    from backend.app.core.config import AssetStorageSettings
    from backend.app.services.json_audit import audit_json_sample
    from backend.app.services.review_correction import propose_correction
    from backend.tests.test_compact_vision_prompts import pin as v2_pin
    from backend.tests.test_json_dataset_workflow import imported
    pubs, settings, _, _ = imported(tmp_path, 'labelme')
    manifest = pubs[0].manifest
    storage = AssetStorageSettings(root=settings.asset_root)
    book = Rulebook(rulebook_id='book_ctx', guideline_text="\n".join([ANIMALS[0], ANIMALS[3], ANIMALS[8]]),
                    rules=[context_rule()], confirmation_state='confirmed')
    captured = []

    class Client:
        def __init__(self, execution): pass
        def verify(self): pass
        def close(self): pass
        def request(self, method, path, payload):
            captured.append(json.loads(payload['messages'][1]['content']))
            raise RuntimeError('stop after capturing the request')

    feedback = ImageReviewFeedback(reviewer_id='reviewer', source_audit_id='audit_parent',
                                   comment='Check the label.', propose_correction=True)
    with pytest.raises(RuntimeError):
        propose_correction(audit_json_sample(manifest, storage), feedback, manifest, book, storage,
                           v2_pin(num_predict=2048), client_factory=Client)
    assert captured[0]['guidelines'][0]['context_sentences'] == [ANIMALS[0], ANIMALS[8]]


# --------------------------------------------------------------- further edge cases

def test_context_too_large_for_the_model_window_is_rejected_never_truncated(tmp_path):
    from backend.app.core.feedback import FeedbackError
    from backend.app.models.feedback import ExecutionPin
    long_context = 'Scope: ' + 'every annotated animal is in scope, judged by the image. ' * 120
    text = "\n".join([long_context, ANIMALS[3]])
    rule = Rule(rule_id='LABEL', description=ANIMALS[3], check_name='annotation_guideline', origin='extracted',
                source_sentence=ANIMALS[3], context_sentences=[long_context], confirmation_state='confirmed')
    Rulebook(rulebook_id='b', guideline_text=text, rules=[rule])           # valid as data
    frozen = inputs(tmp_path)
    small = ExecutionPin(model='test-vision', model_digest='sha256:' + 'a' * 64, num_ctx=2048, num_predict=128)
    target = RulePin(rulebook_id='b', rulebook_version=1, rule=rule)
    with pytest.raises(FeedbackError) as caught:
        prepare_payload(frozen.truth.cases[0].manifest, target, [], small, assets(tmp_path))
    assert caught.value.code == 'CONTEXT_NOT_REPRESENTABLE'


def test_a_repeated_guideline_sentence_is_cited_once_and_never_as_its_own_context():
    text = "A cat is a domestic feline.\nEvery label must name the visible animal.\nA cat is a domestic feline.\nEvery label must name the visible animal."
    rules = extract(text, {1: 'context', 2: 'requirement', 3: 'context', 4: 'context'}, [(2, VISUAL, {}, [])])
    assert len(rules) == 1
    assert rules[0].context_sentences == ['A cat is a domestic feline.']     # duplicate line, cited once
    as_book(text, rules)                                                     # and the rulebook validates


def test_revising_a_confirmed_rulebook_keeps_context_and_notes():
    from backend.app.services.review import revise_rulebook
    book = as_book(ANIMAL_TEXT, extract(ANIMAL_TEXT, ANIMAL_LINES, animal_rules({8: (8, VISUAL, {}, [])})))
    confirmed = book.model_copy(update={'confirmation_state': ConfirmationState.CONFIRMED, 'rules': [
        r.model_copy(update={'confirmation_state': ConfirmationState.CONFIRMED}) for r in book.rules]})
    repository = MemoryRepository(Rulebook.model_validate(confirmed.model_dump()))
    draft = revise_rulebook('book_ctx', RulebookAction(expected_version=1, reviewer_id='reviewer'),
                            repository=repository)
    assert draft.confirmation_state is ConfirmationState.DRAFT
    assert [r.context_sentences for r in draft.rules] == [r.context_sentences for r in book.rules]
    assert [r.extraction_notes for r in draft.rules] == [r.extraction_notes for r in book.rules]


def test_removing_a_context_line_from_the_guideline_text_is_rejected_not_kept_dangling():
    from backend.app.services.review import RulebookContentValidationError
    book = as_book(ANIMAL_TEXT, extract(ANIMAL_TEXT, ANIMAL_LINES, animal_rules()))
    repository = MemoryRepository(book)
    edited_text = ANIMAL_TEXT.replace(ANIMALS[8], '')          # reviewer deletes the uncertainty line
    rules = [Rule.model_validate({k: v for k, v in r.model_dump(mode='json').items()
                                  if k not in ('context_sentences', 'extraction_notes')}) for r in book.rules]
    with pytest.raises(RulebookContentValidationError):
        save_rulebook_review('book_ctx', RulebookReplace(expected_version=1, reviewer_id='reviewer',
                                                         guideline_text=edited_text, rules=rules),
                             repository=repository)
    assert repository.book is book                              # nothing was saved


def test_non_english_wording_fails_safe_for_keyword_guarded_checks():
    text = "Como máximo el 5% de cada máscara puede quedar fuera de su caja."   # 'mask' in Spanish
    [rule] = extract(text, {1: 'requirement'}, [(1, cid('mask_outside_box'), {'max_outside_fraction': 0.05}, [])])
    assert rule.check_name is None and rule.extraction_notes                 # kept visible, never accepted blindly
    [visual] = extract("Cada etiqueta debe nombrar el animal visible.", {1: 'requirement'}, [(1, VISUAL, {}, [])])
    assert visual.check_name == 'annotation_guideline'                       # unguarded visual mapping still works


def test_the_extraction_job_child_sends_context_and_notes_to_the_publishing_parent(monkeypatch):
    from backend.app.services import extraction_jobs as service
    from backend.tests.test_extraction_jobs import CapturePipe, model_settings
    book = as_book(ANIMAL_TEXT, extract(ANIMAL_TEXT, ANIMAL_LINES, animal_rules({8: (8, VISUAL, {}, [])})))
    monkeypatch.setattr(service, 'extract_rulebook', lambda text, settings: book)
    pipe = CapturePipe()
    reserved = 'rulebook_' + 'd' * 32
    service.extraction_child(pipe, ANIMAL_TEXT, model_settings().model_dump(), reserved)
    published = Rulebook.model_validate(pipe.payload['draft'])           # what the parent validates and saves
    assert published.rulebook_id == reserved
    assert [r.context_sentences for r in published.rules] == [r.context_sentences for r in book.rules]
    assert [r.extraction_notes for r in published.rules] == [r.extraction_notes for r in book.rules]
