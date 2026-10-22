"""Promote the three planned system declarations in place using the live HTTP shapes."""
from copy import deepcopy

from backend.app.models.system import DetectorStatus, Health, OllamaStatus, SystemStatus
from backend.app.services.system_status import build_capabilities

from .common import BASE_PATH
from .fixtures import EXAMPLE_MODEL

EXPECTED = {
    ('/health', 'get'): 'getHealth',
    ('/capabilities', 'get'): 'getCapabilities',
    ('/system/status', 'get'): 'getSystemStatus',
}
SUMMARIES = {
    'getHealth': 'Check HTTP server liveness',
    'getCapabilities': 'List supported checks and evidence features',
    'getSystemStatus': 'Inspect local model readiness',
}
DESCRIPTIONS = {
    'getHealth': (
        'Liveness only. Returns ok while the HTTP server is running. It never touches PostgreSQL, '
        'Ollama, the detector checkpoint or inference, so an offline optional service cannot make it fail.'
    ),
    'getCapabilities': (
        'Describes what this backend build supports: dataset and guideline input formats, the annotation '
        'coordinate convention, executable guideline checks taken from the real registry, and detection '
        'and matching evidence features, which are listed separately because they are not guideline '
        'checks. Installed files do not change it. It starts no service and runs no inference. Planned '
        'posture, mask or person-count checks and detection-mask coverage are not listed because they do '
        'not exist.'
    ),
    'getSystemStatus': (
        'Read-only readiness. Ollama is probed with one short model-list request, never text generation; '
        'an unreachable server or a missing model is a normal state, not an error. Detector status keeps '
        'four things apart: whether the packages are installed, whether detection is disabled, enabled or '
        'invalid for new audits, whether a file exists at the configured checkpoint location (it is not '
        'hashed or loaded), and validation, which is unknown unless a real validation has been recorded. '
        'Reading it loads no model, runs no inference, downloads nothing and uses no database. Paths and '
        'raw errors are never returned.'
    ),
}
BACKING = {
    'getHealth': 'FastAPI route; no dependencies.',
    'getCapabilities': 'CHECK_REGISTRY and a static evidence feature list; FastAPI route.',
    'getSystemStatus': (
        'SystemStatusService: bounded Ollama model-list probe, detection settings and a checkpoint '
        'file-existence check; FastAPI route.'
    ),
}


def system_examples():
    """Build examples from the real models so they always validate against the live schemas."""
    return {
        'getHealth': Health(status='ok', api_version='v1').model_dump(mode='json'),
        'getCapabilities': build_capabilities().model_dump(mode='json'),
        'getSystemStatus': SystemStatus(
            ollama=OllamaStatus(configuration='valid', reachable=True, model=EXAMPLE_MODEL, model_available=True),
            detector=DetectorStatus(
                integration_supported=True, configuration='enabled', checkpoint_file='present',
                validation='unknown', architecture='fasterrcnn_mobilenet_v3_large_320_fpn',
            ),
            execution_device='cpu',
        ).model_dump(mode='json'),
    }


def align_system_contract(contract, live, operations):
    examples = system_examples()
    for (path, method), operation_id in EXPECTED.items():
        previous = contract['paths'].get(path, {}).get(method)
        if (previous is None or previous['operationId'] != operation_id
                or previous['x-implementation-status'] != 'planned'):
            raise ValueError('The planned system declaration changed; inspect before promotion.')
        operation = deepcopy(live['paths'][BASE_PATH + path][method])
        if operation['operationId'] != operation_id:
            raise ValueError('System operation IDs do not match the live routes.')
        operation['summary'] = SUMMARIES[operation_id]
        operation['description'] = DESCRIPTIONS[operation_id]
        operation['x-implementation-status'] = 'implemented'
        operation['x-backing-service-status'] = BACKING[operation_id]
        for status, response in operation['responses'].items():
            for media_type, media in response.get('content', {}).items():
                if media_type != 'application/json':
                    continue
                if status.startswith('2'):
                    media['example'] = deepcopy(examples[operation_id])
                else:
                    media['example'] = {'error': {'code': 'INTERNAL_ERROR',
                                                  'message': 'The request could not be completed.',
                                                  'details': [], 'request_id': 'request_demo'}}
        contract['paths'][path][method] = operation
        matches = [entry for entry in operations if entry['operation_id'] == operation_id]
        if len(matches) != 1:
            raise ValueError('System template declarations must be unique.')
        entry = matches[0]
        entry.update(summary=operation['summary'], implementation='implemented',
                     backing_service=operation['x-backing-service-status'])
        entry['response']['body'] = deepcopy(examples[operation_id])
    # Use the live lossless definitions for the system graph only; never replace unrelated
    # schemas by a broad overwrite, and never let a handwritten version win over the models.
    schemas = live['components']['schemas']
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

    for path, method in EXPECTED:
        collect(contract['paths'][path][method])
    copied = set()
    while needed - copied:
        name = sorted(needed - copied)[0]
        definition = deepcopy(schemas[name])
        collect(definition)
        contract['components']['schemas'][name] = definition
        copied.add(name)
