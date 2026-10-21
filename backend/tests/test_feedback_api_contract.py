"""Authored HTTP and source/live contract regressions; no client run by handoff."""
from unittest.mock import Mock
import json
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute, iter_route_contexts
import pytest
from backend.app.main import create_app
from backend.app.routers.feedback import get_feedback_repository
from backend.app.core.feedback import FeedbackError
from backend.app.models.feedback import FeedbackPolicy, FeedbackPolicyCreate, GroundTruth, GroundTruthCreate, FeedbackEvaluation, FeedbackEvaluationCreate, FeedbackEvaluationStarted, FeedbackPolicyActivate, FeedbackExamplePage, FeedbackPolicyPage, FeedbackEvaluationPage
from backend.app.models.job import Job, JobStatus
from backend.api.feedback_contract import OPERATIONS, feedback_job_scenarios
from backend.api.feedback_examples import feedback_examples
from backend.api.schemas import build_schemas
from backend.api.common import replace_model_refs
from backend.api.build_contract import build_contract
from backend.tests.test_rulebook_contract import assert_same_schema

@pytest.fixture
def http_api():
    values = feedback_examples()
    repository = Mock()
    app = create_app()
    app.dependency_overrides[get_feedback_repository] = lambda: repository
    with TestClient(app, raise_server_exceptions=False) as client:
        yield (client, repository, values)

@pytest.mark.parametrize('path,method,identifier', [(p, m, i) for (p, m), i in OPERATIONS.items()])
def test_ten_routes_return_typed_synthetic_payloads(http_api, path, method, identifier):
    client, repository, values = http_api
    functions = {'listFeedbackExamples': 'examples', 'createFeedbackPolicy': 'create_policy', 'listFeedbackPolicies': 'policies', 'getFeedbackPolicy': 'get_policy', 'createGroundTruth': 'create_truth', 'getGroundTruth': 'get_truth', 'evaluateFeedbackPolicy': 'enqueue', 'getFeedbackEvaluation': 'get_evaluation', 'listFeedbackEvaluations': 'evaluations', 'activateFeedbackPolicy': 'activate'}
    response_models = {
        'listFeedbackExamples': FeedbackExamplePage,
        'createFeedbackPolicy': FeedbackPolicy,
        'listFeedbackPolicies': FeedbackPolicyPage,
        'getFeedbackPolicy': FeedbackPolicy,
        'createGroundTruth': GroundTruth,
        'getGroundTruth': GroundTruth,
        'evaluateFeedbackPolicy': FeedbackEvaluationStarted,
        'getFeedbackEvaluation': FeedbackEvaluation,
        'listFeedbackEvaluations': FeedbackEvaluationPage,
        'activateFeedbackPolicy': FeedbackPolicy,
    }
    getattr(repository, functions[identifier]).return_value = response_models[identifier].model_validate_json(
        json.dumps(values['responses'][identifier]),
    )
    url = '/api/v1' + path.format(policy_id='policy_demo', ground_truth_id='truth_demo', evaluation_id='evaluation_demo')
    response = client.request(method, url, json=values['requests'][identifier]) if identifier in values['requests'] else client.request(method, url)
    assert response.status_code == (202 if identifier == 'evaluateFeedbackPolicy' else 201 if identifier in {'createGroundTruth', 'createFeedbackPolicy'} else 200)
    assert response.json() == values['responses'][identifier]
    assert not any((secret in response.text for secret in ('storage_key', 'claim_token', 'lease_until', 'remote_host')))

@pytest.mark.parametrize('code', list(FeedbackError.messages))
def test_domain_errors_are_safe_envelopes(http_api, code):
    client, repository, _ = http_api
    repository.examples.side_effect = FeedbackError(code)
    response = client.get('/api/v1/feedback/examples')
    assert response.status_code == FeedbackError.messages[code][0]
    assert response.json()['error']['code'] == code and response.json()['error']['request_id']
    assert response.json()['error']['details'] == []

@pytest.mark.parametrize('query', ['?limit=0', '?offset=-1', '?offset=9223372036854775808', '?limit=2&limit=3', '?output_path=/private'])
def test_bad_collection_queries_never_reach_repository(http_api, query):
    client, repository, _ = http_api
    assert client.get('/api/v1/feedback/examples' + query).status_code == 422
    repository.examples.assert_not_called()

def test_mutation_rejects_private_override_before_enqueuing(http_api):
    client, repository, values = http_api
    body = values['requests']['evaluateFeedbackPolicy'] | {'model': 'replacement', 'lease_seconds': 999}
    assert client.post('/api/v1/feedback/evaluations', json=body).status_code == 422
    repository.enqueue.assert_not_called()

def test_programming_failure_is_500_not_client_error(http_api):
    client, repository, _ = http_api
    repository.examples.side_effect = RuntimeError('private SQL path')
    response = client.get('/api/v1/feedback/examples')
    assert response.status_code == 500 and 'private SQL path' not in response.text

def test_ten_promotions_exact_inventory_and_live_constraints():
    app = create_app()
    live = app.openapi()
    generated = build_contract()
    routes = [c.original_route.operation_id for c in iter_route_contexts(app.routes) if isinstance(c.original_route, APIRoute) and c.original_route.include_in_schema]
    operations = [op for methods in generated['paths'].values() for op in methods.values()]
    assert len(routes) == len(set(routes)) == 49 and len(operations) == 50
    assert sum((op['x-implementation-status'] == 'planned' for op in operations)) == 1
    for (path, method), identifier in OPERATIONS.items():
        left = generated['paths'][path][method]
        right = live['paths']['/api/v1' + path][method]
        assert left['operationId'] == right['operationId'] == identifier and routes.count(identifier) == 1
        assert left['x-implementation-status'] == 'implemented' and set(left['responses']) == set(right['responses'])
        for status in right['responses']:
            assert_same_schema(left['responses'][status]['content']['application/json']['schema'], right['responses'][status]['content']['application/json']['schema'], generated['components']['schemas'], live['components']['schemas'])
        for one, two in zip(left.get('parameters', []), right.get('parameters', []), strict=True):
            assert (one['name'], one['in'], one['required']) == (two['name'], two['in'], two['required'])
            assert_same_schema(one['schema'], two['schema'], generated['components']['schemas'], live['components']['schemas'])
        assert ('requestBody' in left) == ('requestBody' in right) == (method == 'post')
        if method == 'post':
            assert_same_schema(left['requestBody']['content']['application/json']['schema'], right['requestBody']['content']['application/json']['schema'], generated['components']['schemas'], live['components']['schemas'])

def test_models_not_overridden_by_handwritten_schemas_and_fixtures_validate():
    models = {'createFeedbackPolicy': (FeedbackPolicyCreate, FeedbackPolicy), 'createGroundTruth': (GroundTruthCreate, GroundTruth), 'evaluateFeedbackPolicy': (FeedbackEvaluationCreate, FeedbackEvaluationStarted), 'activateFeedbackPolicy': (FeedbackPolicyActivate, FeedbackPolicy)}
    values = feedback_examples()
    schemas = build_schemas()
    for identifier, (request, response) in models.items():
        for model, section in [(request, 'requests'), (response, 'responses')]:
            model.model_validate_json(json.dumps(values[section][identifier]))
            expected = model.model_json_schema()
            expected.pop('$defs', None)
            assert schemas[model.__name__] == replace_model_refs(expected)
    assert values['responses']['getFeedbackEvaluation']['metrics'] is None
    states = feedback_job_scenarios()
    assert set(states) == {s.value for s in JobStatus}
    for state, document in states.items():
        job = Job.model_validate_json(json.dumps(document))
        assert job.status.value == state
        if state == 'completed':
            assert job.result.href == '/api/v1/feedback/evaluations/evaluation_demo'
