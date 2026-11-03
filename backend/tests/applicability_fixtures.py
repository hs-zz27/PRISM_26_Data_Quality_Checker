"""Fixtures for requirement-applicability tests. Every image and annotation here is SYNTHETIC."""
import json
import os

import httpx
import pytest

from backend.app.models.applicability import ScopeImage
from backend.app.models.feedback import ExecutionPin
from backend.app.models.rule import Rule, Rulebook


# The managed importer needs POSIX file APIs (the repo's other importer tests skip the same way).
requires_importer = pytest.mark.skipif(not hasattr(os, 'O_NOFOLLOW'), reason='the managed importer needs POSIX file APIs')


def imported(*args, **kwargs):
    """Lazy: the shared importer fixture pulls in OpenCV through the audit modules."""
    from backend.tests.test_json_dataset_workflow import imported as real
    return real(*args, **kwargs)


def v5_pin(**changes):
    changes.setdefault('num_predict', 2048)
    return ExecutionPin(model='test-vision', model_digest='sha256:' + 'a' * 64,
                        prompt_version='annotation-guideline-v5',
                        preprocessing='original-and-annotation-overlay-v1', **changes)


def v6_pin(**changes):
    changes.setdefault('num_predict', 2048)
    return ExecutionPin(model='test-vision', model_digest='sha256:' + 'a' * 64,
                        prompt_version='annotation-guideline-v6',
                        preprocessing='target-focus-overlay-v1', **changes)


def v3_pin(**changes):
    changes.setdefault('num_predict', 2048)
    return ExecutionPin(model='test-vision', model_digest='sha256:' + 'a' * 64,
                        prompt_version='annotation-guideline-v3',
                        preprocessing='original-and-annotation-overlay-v1', **changes)


def visual_rule(rule_id, text, *, applicability=None, context=()):
    extra = {} if applicability is None else {'applicability': applicability}
    return Rule(rule_id=rule_id, description=text, check_name='annotation_guideline', parameters={},
                origin='reviewer', source_sentence=None, confirmation_state='confirmed',
                context_sentences=list(context), **extra)


def rulebook(*rules):
    lines = []
    for rule in rules:
        lines += [rule.description, *rule.context_sentences]
    text = '\n'.join(dict.fromkeys(lines))
    return Rulebook(rulebook_id='applicability_book', version=1, guideline_text=text,
                    rules=list(rules), confirmation_state='confirmed')


def labelme_document(*shapes):
    return {'imagePath': 'person.jpg', 'imageData': None, 'imageWidth': 20, 'imageHeight': 16,
            'flags': {}, 'shapes': list(shapes)}


def polygon_shape(points, label='person'):
    return {'label': label, 'shape_type': 'polygon', 'group_id': None, 'points': points, 'flags': {}}


def rectangle_shape(points, label='person'):
    return {'label': label, 'shape_type': 'rectangle', 'group_id': None, 'points': points, 'flags': {}}


MAN = [[2, 2], [8, 2], [8, 14], [2, 14]]
WOMAN = [[11, 3], [17, 3], [17, 15], [11, 15]]


def two_polygons():
    """The reproduction shape: two person polygons and NO supplied bounding boxes."""
    return labelme_document(polygon_shape(MAN), polygon_shape(WOMAN))


IMAGE = ScopeImage(sample_id='s', sample_revision=1, image_key='k', image_sha256='1' * 64, annotation_sha256='2' * 64)


class FakeOllama:
    """A mock local server. ``answer(document)`` returns the model's JSON fields for a request."""

    def __init__(self, answer):
        self.answer = answer
        self.requests = []
        self.chats = []

    def handler(self, request):
        self.requests.append(request.url.path)
        if request.url.path == '/api/version':
            return httpx.Response(200, json={'version': '0.33.3'})
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': 'test-vision', 'digest': 'a' * 64}]})
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'capabilities': ['vision']})
        payload = json.loads(request.content)
        self.chats.append(payload)
        document = json.loads(payload['messages'][-1]['content'])
        body = {'case_id': document['case_id'], 'rule_id': document['rule_id'],
                'evidence_references': ['image', 'annotations'], **self.answer(document)}
        return httpx.Response(200, json={'model': 'test-vision', 'done': True, 'done_reason': 'stop',
                                         'prompt_eval_count': 1000,
                                         'message': {'role': 'assistant', 'content': json.dumps(body)}})

    def transport(self):
        return httpx.MockTransport(self.handler)

    @property
    def chat_count(self):
        return len(self.chats)


def assessment(key, outcome='pass', *, geometry='polygon', condition='applies', reason='Synthetic assessment.'):
    return {'object_key': key, 'outcome': outcome, 'geometry': geometry, 'condition': condition, 'reason': reason}
