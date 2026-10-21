"""Promote existing export declarations in place using lossless live HTTP shapes."""
from copy import deepcopy

from .common import BASE_PATH
from .export_examples import export_examples

EXPECTED = {
    ('/exports', 'post'): 'startExport',
    ('/exports', 'get'): 'listExports',
    ('/exports/{export_id}', 'get'): 'getExport',
    ('/exports/{export_id}/download', 'get'): 'downloadExport',
    ('/exports/{export_id}/manifest', 'get'): 'getExportManifest',
}


def align_export_contract(contract, live, operations):
    examples = export_examples()
    responses = {
        'startExport': examples['states'][0], 'listExports': examples['page'],
        'getExport': examples['states'][3]['export'], 'getExportManifest': examples['manifest'],
    }
    summaries = {
        'startExport': 'Prepare reviewed output and manifest', 'listExports': 'List reviewed exports',
        'getExport': 'Read export status and download URLs',
        'downloadExport': 'Download reviewed dataset archive',
        'getExportManifest': 'Read exported and excluded sample decisions',
    }
    for (path, method), operation_id in EXPECTED.items():
        previous = contract['paths'].get(path, {}).get(method)
        if operation_id != 'listExports' and (previous is None or previous['operationId'] != operation_id):
            raise ValueError('The planned export declaration changed; inspect before promotion.')
        operation = deepcopy(live['paths'][BASE_PATH + path][method])
        if operation['operationId'] != operation_id:
            raise ValueError('Export operation IDs do not match the live routes.')
        operation['summary'] = summaries[operation_id]
        operation['description'] = (
            'Exports preserve exact historical audit pins and frozen human decisions; '
            'they do not implement annotation corrections or rechecks. '
            'accepted_only includes accepted; all_reviewed includes accepted and rejected diagnostically. '
            'Pending/reopened and needs_correction samples are excluded. '
            'Counts and artifact URLs stay null until completed. Active audits and legacy audits are not exportable. '
            'After a lost POST response, reconcile list/detail/job reads before considering resubmission. '
            'Download and manifest return EXPORT_NOT_READY for every noncompleted state.'
        )
        operation['x-implementation-status'] = 'implemented'
        operation['x-backing-service-status'] = 'ExportRepository, bounded explicit export worker and verified download service.'
        for parameter in operation.get('parameters', []):
            parameter['example'] = (examples['states'][0]['export']['export_id']
                                    if parameter['name'] == 'export_id'
                                    else 20 if parameter['name'] == 'limit' else 0)
        if method == 'post':
            operation['requestBody']['content']['application/json']['example'] = examples['request']
        for status, response in operation['responses'].items():
            for media_type, media in response.get('content', {}).items():
                if media_type != 'application/json':
                    continue
                if status.startswith('2'):
                    media['example'] = deepcopy(responses[operation_id])
                else:
                    codes = {
                        '404': {'NOT_FOUND': 'The requested export or source audit does not exist.'},
                        '422': {'VALIDATION_ERROR': 'One or more request fields are invalid.'},
                        '500': {'INTERNAL_ERROR': 'The request could not be completed.'},
                        '503': {'DEPENDENCY_UNAVAILABLE': 'Export storage or durable acceptance is unavailable.'},
                        '409': ({'AUDIT_NOT_READY': 'Wait for a terminal audit.',
                                 'AUDIT_NOT_EXPORTABLE': 'The source audit has no immutable snapshot.'}
                                if operation_id == 'startExport' else
                                {'EXPORT_NOT_READY': 'The export has not completed.'}),
                    }[status]
                    media['examples'] = {code: {'value': {'error': {'code': code, 'message': message,
                                                                  'details': [], 'request_id': 'request_demo'}}}
                                         for code, message in codes.items()}
        contract['paths'].setdefault(path, {})[method] = operation
        matches = [entry for entry in operations if entry['operation_id'] == operation_id]
        if operation_id == 'listExports':
            if matches:
                raise ValueError('Duplicate listExports declaration.')
            entry = {'operation_id': operation_id, 'method': method.upper(), 'path': BASE_PATH + path,
                     'tag': 'Exports', 'request': {'path_parameters': {}, 'query': {'limit': 20, 'offset': 0},
                                                  'content_type': None, 'body': None},
                     'response': {'status': 200, 'content_type': 'application/json', 'body': examples['page']}}
            operations.append(entry)
        else:
            if len(matches) != 1:
                raise ValueError('Export template declarations must be unique.')
            entry = matches[0]
        entry.update(summary=operation['summary'], implementation='implemented',
                     backing_service=operation['x-backing-service-status'])
        if operation_id in responses:
            entry['response']['body'] = deepcopy(responses[operation_id])
        if operation_id == 'startExport':
            entry['request']['body'] = deepcopy(examples['request'])
        if 'export_id' in entry['request']['path_parameters']:
            entry['request']['path_parameters']['export_id'] = examples['states'][0]['export']['export_id']
    # Use the live lossless definitions only for the export graph; never replace
    # unrelated planned or implemented schemas by broad dictionary overwrite.
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
    # Kept in examples.json by the bounded build_contract patch.
    contract['x-export-scenarios'] = examples
