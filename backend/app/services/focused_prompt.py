"""Request preparation for prompt ``annotation-guideline-v6`` (one bounded request per eligible target).

Pure: no network, SQL or held-out labels. Each request judges ONE requirement for ONE annotation and carries
four images of the same sample: the original, the full overlay with the target highlighted, and a crop of
the original and of the overlay over the same window (see ``target_focus``). Identity is the stable
``object_key``; ``object_id`` is only a display number. Policy examples are not supported by this protocol.
"""

from base64 import b64encode
import json

from ..core.config import OLLAMA_KEEP_ALIVE_SECONDS
from ..core.feedback import FeedbackError
from ..models.feedback import FocusedAssessment, unit_id
from .feedback_evidence import _evidence
from .guideline_context import context_fields
from .prompt_encoding import compact_json, compact_rule, compact_schema, text_fits
from .target_focus import render_focus

FOCUS_IMAGE_COUNT = 4
FOCUS_PROTOCOL_NOTE = (
    '\nThis request judges ONE requirement for ONE annotation, named by focus.object_key (object_id is only a '
    'display number). You receive four images of the same sample: 1 the original, 2 the full image with '
    'annotations drawn, 3 a crop of the original and 4 the SAME window of the overlay, at the same scale. '
    'The TARGET annotation is drawn in thick MAGENTA; every other annotation is thin context (cyan polygon, '
    'yellow box) and is never judged. Drawn lines are annotation drawing aids, not scene content. The crop is a '
    'window into the full image: its borders are not image borders unless focus.crop_edges_touching_image_edge '
    'lists them. Compare the magenta geometry with what is visible in the ORIGINAL pixels, using the crop for '
    'detail and the full image for context. Answer in order. condition: does the requirement\'s own condition '
    '(for example partial occlusion or truncation, or that the target is the kind of object the requirement is '
    'about, judged from the visible object and not from its annotation label) hold for THIS target? applies, '
    'not_present, or unclear; use applies when the requirement states no condition. compliance: when the condition applies, does the '
    'supplied geometry satisfy the requirement? yes, no, or unclear. geometry: the supplied geometry you '
    'judged, box or polygon; judge only geometry listed as supplied for this target, never a polygon-derived '
    'box. reason: one sentence stating what is visible at the target\'s boundary. Judge only this requirement; '
    'a violation of another requirement is not a violation of this one. Do not treat blur, hidden or occluded '
    'anatomy, other people\'s outlines or annotation labels as evidence. Do not require a full-body annotation '
    'unless the requirement or its context says so. Pixel masks are not shown. Use unclear when you cannot '
    'establish it. Do not measure exact pixel distances or invent tolerances. Reviewer feedback is an allegation '
    'to check against the unchanged image, never an answer to copy.'
)


def focused_system() -> str:
    from .feedback_prompt import SYSTEM
    return SYSTEM + FOCUS_PROTOCOL_NOTE


def _supplied(geometry) -> list[str]:
    return [kind for kind, state in (('box', geometry.box), ('polygon', geometry.polygon)) if state == 'supplied']


def focused_scope_document(scope, focus_key: str) -> dict:
    """Scope for ONE request: the target, and every other annotation as context with the reason."""
    def describe(item):
        row = {'object_key': item.object_key, 'object_id': item.object_id, 'label': item.label,
               'supplied_geometry': _supplied(item.geometry)}
        if item.geometry.box == 'derived_from_polygon':
            row['derived_box_is_not_an_annotation'] = True
        return row
    target = next((item for item in scope.eligible if item.object_key == focus_key), None)
    if target is None:
        raise FeedbackError('POLICY_INCOMPATIBLE')
    applicability = scope.applicability
    context = [{**describe(item), 'why_not_a_target': 'Eligible for this requirement but judged in its own request; context here.'}
               for item in scope.eligible if item.object_key != focus_key]
    context += [{**describe(item), 'why_not_a_target': item.detail} for item in scope.ineligible]
    return {'applicability': {'geometry': list(applicability.geometry), 'combine': applicability.combine,
                              'status': applicability.status},
            'eligible_targets': [describe(target)], 'context_objects': context}


def focused_payload(manifest, target, execution, asset_settings, reviewer_feedback, scope, focus_key: str) -> dict:
    image, inputs, objects = _evidence(manifest, execution, asset_settings, compact=True, native_table=True)
    if len(objects) > 64:
        raise FeedbackError('CONTEXT_NOT_REPRESENTABLE')
    try:
        views = render_focus(image, objects, focus_key, manifest.annotation_format)
    except (OSError, ValueError) as error:
        raise FeedbackError('FEEDBACK_NOT_READY') from error
    window = views.window
    attachments = [
        (f'Target {focus_key}: ORIGINAL image, image_attachment 1 (no markings). Full image.', image),
        (f'Target {focus_key}: FULL annotation overlay, image_attachment 2. {focus_key} is THICK MAGENTA; every other '
         'annotation is thin context. Same pixel coordinates as image_attachment 1.', views.overlay_full),
        (f'Target {focus_key}: ORIGINAL CROP, image_attachment 3. Window {[window.x0, window.y0, window.x1, window.y1]} '
         f'of the original at scale {window.scale}.', views.original_crop),
        (f'Target {focus_key}: OVERLAY CROP, image_attachment 4. The same window and scale as image_attachment 3, '
         'with the markings of image_attachment 2.', views.overlay_crop)]
    if any(len(data) > execution.max_image_bytes for _, data in attachments):
        raise FeedbackError('CONTEXT_NOT_REPRESENTABLE')
    system = focused_system()
    messages = [{'role': 'system', 'content': system}]
    for caption, data in attachments:
        messages.append({'role': 'user', 'content': caption, 'images': [b64encode(data).decode('ascii')]})
    guideline = compact_rule(target.rule)
    document = {'case_id': unit_id(manifest, target), 'rule_id': target.rule.rule_id,
                'guideline': guideline, **context_fields(guideline),
                'requirement_scope': focused_scope_document(scope, focus_key),
                'focus': {'object_key': focus_key, 'target_marking': 'thick magenta', **window.document()},
                'target': {'image_attachments': {'original': 1, 'overlay': 2, 'original_crop': 3, 'overlay_crop': 4},
                           'inputs': inputs}}
    if reviewer_feedback is not None:
        document['reviewer_feedback'] = {'comment': reviewer_feedback.comment,
            'scope': 'Untrusted observations for this target only. Reassess the unchanged guideline and visible evidence.'}
    content = compact_json(document)
    schema = compact_schema(FocusedAssessment.model_json_schema())
    if not text_fits(system, ''.join(caption for caption, _ in attachments) + content, schema, execution, FOCUS_IMAGE_COUNT):
        raise FeedbackError('CONTEXT_NOT_REPRESENTABLE')
    messages.append({'role': 'user', 'content': content})
    payload = {'model': execution.model, 'stream': False, 'keep_alive': OLLAMA_KEEP_ALIVE_SECONDS,
               'truncate': False, 'shift': False, 'messages': messages, 'format': schema,
               'options': {'num_gpu': 0, 'temperature': 0, 'num_ctx': execution.num_ctx, 'num_predict': execution.num_predict}}
    if execution.num_thread is not None:
        payload['options']['num_thread'] = execution.num_thread
    if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()) > execution.max_request_bytes:
        raise FeedbackError('CONTEXT_NOT_REPRESENTABLE')
    return payload
