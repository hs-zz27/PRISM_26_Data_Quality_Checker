"""Promote ten existing feedback operations; preserve actual live schema constraints."""
from copy import deepcopy
from datetime import UTC, datetime
from backend.app.core.feedback import FeedbackError
from backend.app.models.job import Job, JobKind, JobStatus, ApiError, Progress, ResultReference
from .common import BASE_PATH
from .feedback_examples import feedback_examples
OPERATIONS = {('/feedback/examples', 'get'): 'listFeedbackExamples', ('/feedback/policies', 'post'): 'createFeedbackPolicy', ('/feedback/policies', 'get'): 'listFeedbackPolicies', ('/feedback/policies/{policy_id}', 'get'): 'getFeedbackPolicy', ('/feedback/ground-truth', 'post'): 'createGroundTruth', ('/feedback/ground-truth/{ground_truth_id}', 'get'): 'getGroundTruth', ('/feedback/evaluations', 'post'): 'evaluateFeedbackPolicy', ('/feedback/evaluations/{evaluation_id}', 'get'): 'getFeedbackEvaluation', ('/feedback/evaluations', 'get'): 'listFeedbackEvaluations', ('/feedback/policies/{policy_id}/activate', 'post'): 'activateFeedbackPolicy'}

def align_feedback_contract(contract, live, operations):
    """Require one declaration and handler for each operation before promoting it."""
    values = feedback_examples()
    templates = {entry['operation_id']: entry for entry in operations}
    codes = {str(status): {} for status in (404, 409, 422, 500, 503)}
    for code, (status, message) in FeedbackError.messages.items():
        codes[str(status)][code] = message
    codes['422']['VALIDATION_ERROR'] = 'One or more fields are invalid.'
    codes['500']['INTERNAL_ERROR'] = 'The request could not be completed.'
    for (path, method), identifier in OPERATIONS.items():
        old = contract['paths'][path][method]
        if old['operationId'] != identifier or sum((e['operation_id'] == identifier for e in operations)) != 1:
            raise ValueError('Feedback declaration identity changed.')
        operation = deepcopy(live['paths'][BASE_PATH + path][method])
        if operation['operationId'] != identifier:
            raise ValueError('Feedback handler identity changed.')
        operation.update(summary=old['summary'], description='Immutable rule-level reviewer evidence. Supports generic annotation_guideline feedback and single-box pedestrian_box_enclosure examples, including attributed binary overrides. Enclosure evaluation targets may contain multiple boxes; both arms retain per-box evidence and the same pinned marked-context method. Multi-box sample reviews never become inferred per-box training labels. New model outputs remain pending review. Metadata reads do not run inference. Evaluations use an explicit owned worker and fresh baseline/candidate inference over the same held-out cases. Null metrics mean not computed. Undefined ratios remain null. Activation requires an explicit reviewer decision, not a universal score threshold. Examples and model identities in fixtures are synthetic. Actual multimodal context fidelity, model capability and improvement require owner-run validation.', **{'x-implementation-status': 'implemented', 'x-backing-service-status': 'Feedback repositories, explicit comparison worker and thin HTTP routes.'})
        for parameter in operation.get('parameters', []):
            parameter['example'] = {'limit': 20, 'offset': 0, 'policy_id': 'policy_demo', 'ground_truth_id': 'truth_demo', 'evaluation_id': 'evaluation_demo'}[parameter['name']]
        entry = templates[identifier]
        if 'requestBody' in operation:
            media = operation['requestBody']['content']['application/json']
            media['example'] = deepcopy(values['requests'][identifier])
            entry['request']['body'] = deepcopy(media['example'])
        for status, response in operation['responses'].items():
            for media in response.get('content', {}).values():
                media.pop('example', None)
                media.pop('examples', None)
                if status.startswith('2'):
                    media['example'] = deepcopy(values['responses'][identifier])
                else:
                    media['examples'] = {code: {'value': {'error': {'code': code, 'message': message, 'details': [], 'request_id': 'request_demo'}}} for code, message in codes[status].items()}
        contract['paths'][path][method] = operation
        entry.update(implementation='implemented', summary=operation['summary'], backing_service=operation['x-backing-service-status'])
        entry['response']['body'] = deepcopy(values['responses'][identifier])
    needed = set()

    def collect(value):
        if isinstance(value, dict):
            reference = value.get('$ref', '')
            if reference.startswith('#/components/schemas/'):
                needed.add(reference.rsplit('/', 1)[-1])
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
    for path, method in OPERATIONS:
        collect(contract['paths'][path][method])
    copied = set()
    while needed - copied:
        name = sorted(needed - copied)[0]
        definition = deepcopy(live['components']['schemas'][name])
        collect(definition)
        contract['components']['schemas'][name] = definition
        copied.add(name)
    audit = contract['paths']['/audits']['post']
    audit['description'] += ' Explicit feedback_policy_id selects an active compatible immutable policy; null is baseline. Model identity is worker-verified, never probed during submission.'
    for code, (status, message) in FeedbackError.messages.items():
        if status != 409:
            continue
        audit['responses']['409']['content']['application/json'].setdefault('examples', {})[code] = {'value': {'error': {'code': code, 'message': message, 'details': [], 'request_id': 'request_demo'}}}

def feedback_job_scenarios():
    """Six synthetic lifecycle snapshots; these are not measured model results."""
    now = datetime(2026, 10, 21, tzinfo=UTC)
    states = {}
    for status in JobStatus:
        job = Job.queued(job_id='job_feedback_demo', kind=JobKind.FEEDBACK_EVALUATION, total=1, now=now)
        values = job.model_dump()
        values['status'] = status
        values['progress'] = Progress(phase=status.value, processed=1 if status is JobStatus.COMPLETED else 0, total=1, fraction=1.0 if status is JobStatus.COMPLETED else 0.0)
        if status is JobStatus.COMPLETED:
            values['result'] = ResultReference(kind='feedback_evaluation', id='evaluation_demo', href=BASE_PATH + '/feedback/evaluations/evaluation_demo')
        if status is JobStatus.FAILED:
            values['error'] = ApiError(code='FEEDBACK_EVALUATION_FAILED', message='The comparison could not be completed.', request_id='request_demo')
        states[status.value] = Job.model_validate(values).model_dump(mode='json')
    return states
