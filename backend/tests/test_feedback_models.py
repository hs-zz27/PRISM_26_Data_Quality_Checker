"""Strict snapshot, execution and example-selection regressions."""
from copy import deepcopy
import json
import pytest
from pydantic import ValidationError
from backend.app.models.feedback import ExecutionPin, FeedbackPolicyCreate, GroundTruthCreate, CaseReference, CaseSnapshot, EvaluationInput, FeedbackExamplePage, checked, case_view, fingerprint
from backend.tests.feedback_fixtures import case, inputs

@pytest.mark.parametrize('field,value', [('temperature', True), ('schema_version', True), ('schema_version', 1.0), ('temperature', 0.0), ('num_ctx', True), ('num_predict', 0), ('model_digest', 'mutable-tag'), ('max_pixels', float('inf'))])
def test_execution_rejects_invalid_original_values(field, value):
    with pytest.raises(ValidationError):
        ExecutionPin.model_validate(dict(model='vision', model_digest='sha256:' + 'a' * 64) | {field: value})


@pytest.mark.parametrize('value', [True, 1.0, '10', 0, -1, 1025])
def test_execution_rejects_invalid_cpu_threads(value):
    with pytest.raises(ValidationError):
        ExecutionPin(model='vision', model_digest='sha256:' + 'a' * 64, num_thread=value)


def test_legacy_evaluation_fingerprints_and_json_survive_optional_threads(tmp_path):
    original = inputs(tmp_path)
    legacy = original.model_dump(mode='json')
    legacy['runtime'].pop('num_thread', None)
    restored = EvaluationInput.model_validate_json(json.dumps(legacy))
    assert restored.runtime.num_thread is None
    assert restored.model_dump(mode='json') == legacy
    assert fingerprint(restored) == fingerprint(legacy)
    # Explicit automatic selection also serializes as the historical shape.
    legacy['runtime']['num_thread'] = None
    assert fingerprint(EvaluationInput.model_validate_json(json.dumps(legacy))) == fingerprint(original)
    tuned = EvaluationInput.model_validate(original.model_dump())
    tuned.runtime.num_thread = 10
    assert fingerprint(tuned) != fingerprint(original)
    assert EvaluationInput.model_validate_json(tuned.model_dump_json()).runtime.num_thread == 10


def test_new_execution_pins_keep_the_accepted_thread_count(monkeypatch):
    from backend.app.core import feedback
    from backend.app.core.config import OllamaSettings

    settings = OllamaSettings(
        model='vision', host='http://127.0.0.1:11434', timeout_seconds=120.0,
        context_length=16384, max_output_tokens=512, cpu_threads=10,
    )
    monkeypatch.setattr(feedback, 'load_ollama_settings', lambda: settings)
    monkeypatch.setenv('FEEDBACK_MODEL_DIGEST', 'sha256:' + 'a' * 64)
    accepted = feedback.load_execution_pin()
    settings = settings.model_copy(update={'cpu_threads': 4})
    assert feedback.load_execution_pin().num_thread == 4
    assert accepted.num_thread == 10

@pytest.mark.parametrize('revision', [True, 0, '1', 2 ** 31])
def test_case_reference_rejects_boolean_or_invalid_revision(revision):
    with pytest.raises(ValidationError):
        CaseReference(audit_id='audit', sample_id='sample', sample_revision=revision, rule_id='rule')

def test_mutated_nested_input_revalidated_before_serialization(tmp_path):
    request = inputs(tmp_path)
    request.truth.cases[0].manifest.__dict__['sample_revision'] = True
    with pytest.raises(ValidationError):
        checked(EvaluationInput, request)

def test_frozen_feedback_json_preserves_nested_aware_timestamps(tmp_path):
    original = inputs(tmp_path)
    restored = EvaluationInput.model_validate_json(original.model_dump_json())
    assert restored == original
    assert restored.policy.created_at.tzinfo is not None
    assert restored.policy.cases[0].finding_review.updated_at.tzinfo is not None
    assert restored.truth.cases[0].reviewed_at.tzinfo is not None

@pytest.mark.parametrize('field,value', [
    ('temperature', True), ('temperature', 0.0),
    ('schema_version', True), ('schema_version', 1.0),
])
def test_execution_json_rejects_noninteger_literal_options(field, value):
    document = dict(model='vision', model_digest='sha256:' + 'a' * 64)
    document[field] = value
    with pytest.raises(ValidationError):
        ExecutionPin.model_validate_json(json.dumps(document))

@pytest.mark.parametrize('name', ['Invalid\x00name', 'Invalid\ud800name'])
def test_feedback_text_json_rejects_nul_and_invalid_unicode(name):
    document = dict(name=name, reviewer_id='human', example_ids=['example_ZmluZGluZw.1'])
    with pytest.raises(ValidationError):
        FeedbackPolicyCreate.model_validate_json(json.dumps(document))

def test_only_explicit_binary_adjudications_are_truth(tmp_path):
    reference = case(tmp_path).reference.model_dump()
    for value in ['unsure', 'unavailable', 'accepted', None]:
        with pytest.raises(ValidationError):
            GroundTruthCreate(reviewer_id='human', cases=[reference | dict(expected=value, reason='Checked.')])
    with pytest.raises(ValidationError):
        GroundTruthCreate(reviewer_id='human', cases=[])
    with pytest.raises(ValidationError):
        GroundTruthCreate(reviewer_id='human', cases=[reference])

@pytest.mark.parametrize('kind', ['same_revision', 'new_revision', 'image_alias'])
def test_leakage_detects_logical_revision_and_identical_content(tmp_path, kind):
    source = inputs(tmp_path)
    train = source.policy.cases[0]
    document = source.model_dump(mode='python')
    candidate = deepcopy(document['truth']['cases'][0])
    if kind in {'same_revision', 'new_revision'}:
        candidate['reference']['sample_id'] = train.reference.sample_id
        candidate['manifest']['sample_id'] = train.reference.sample_id
        if kind == 'new_revision':
            candidate['reference']['sample_revision'] = 2
            candidate['manifest']['sample_revision'] = 2
    else:
        candidate['manifest']['original_image'] = train.manifest.original_image.model_dump()
    document['truth']['cases'] = [candidate]
    with pytest.raises(ValidationError):
        EvaluationInput.model_validate(document)

def test_examples_resolvable_and_review_revision_distinct(tmp_path):
    approved = case(tmp_path, revision=3, approved=True)
    view = case_view(approved)
    assert view.review_revision == 1 and view.reference.sample_revision == 3
    assert view.example_id.endswith('.1') and 'finding_' not in view.example_id
    assert 'revision=3' in view.image_url
    assert 'storage_key' not in view.model_dump_json() and '/feedback-assets/' not in view.model_dump_json()
    before = fingerprint(approved)
    approved.finding_review.comment = 'Later in-memory edit.'
    assert fingerprint(approved) != before

def test_policy_budget_and_unknown_fields_rejected():
    example = 'example_ZmluZGluZw.1'
    for values in [[], [example] * 2, [example + str(i) for i in range(5)]]:
        with pytest.raises(ValidationError):
            FeedbackPolicyCreate(name='Policy', reviewer_id='human', example_ids=values)
    with pytest.raises(ValidationError):
        FeedbackPolicyCreate(name='Policy', reviewer_id='human', example_ids=[example], target_check_names=['person_posture'])

def test_page_count_and_empty_past_end_are_coherent():
    page = FeedbackExamplePage(items=[], limit=20, offset=99, total=1, next_offset=None)
    assert page.items == []
    with pytest.raises(ValidationError):
        FeedbackExamplePage(items=[], limit=20, offset=0, total=1, next_offset=None)


def test_rule_semantic_fingerprint_is_verified_on_read(tmp_path):
    from backend.app.models.feedback import RulePin
    source = case(tmp_path).target
    changed = source.model_dump(mode='python')
    changed['rule']['description'] = 'Different rule semantics.'
    with pytest.raises(ValidationError):
        RulePin.model_validate(changed)


def test_unverified_execution_cannot_be_a_binary_proposal(tmp_path):
    from backend.app.models.feedback import RecordedPrediction
    from backend.tests.feedback_fixtures import record
    value = case(tmp_path)
    document = record(value.manifest, value.target).model_dump(mode='python')
    document['model_verified'] = False
    with pytest.raises(ValidationError):
        RecordedPrediction.model_validate(document)
    document['prediction']['outcome'] = 'unavailable'
    assert RecordedPrediction.model_validate(document).prediction.outcome == 'unavailable'
