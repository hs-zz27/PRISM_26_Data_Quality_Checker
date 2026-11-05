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
    '\nJudge ONE requirement for focus.object_key only; object_id is a display number. '
    'Images 1/2: full original/overlay; 3/4: original/overlay crops, SAME window and scale. '
    'MAGENTA=focus.judged_geometry. Thin cyan polygons/yellow boxes are context, even on the SAME object; '
    'never judge them. Markings are drawing aids. Crop borders are image borders only where '
    'focus.crop_edges_touching_image_edge says so. Compare with original pixels; crops give detail, full images context. '
    'condition FIRST: identify the target visually; check the rule\'s object class and visibility conditions. '
    'For a person-only rule, a non-person is not_present even near people. Labels may be wrong. '
    'Use unclear if identity/condition is ambiguous; applies only if all conditions hold (or none are required). '
    'compliance: yes, no, or unclear when the condition applies. '
    'geometry: choose only focus.judged_geometry; a box is a rectangle, a polygon is a contour. '
    'reason: identify the visible object, explain the condition, and cite its required boundary if applicable. '
    'Other rules, blur, hidden anatomy, other outlines and labels do not establish violations. '
    'Require full-body coverage only if the guideline/context does. Masks are not shown. '
    'Insufficient evidence means unclear. Do not measure exact pixels or invent tolerances. '
    'Reviewer feedback is an allegation to verify against the unchanged image, never an answer to copy.'
)


# v6 remains reproducible for saved audits. v7 makes the applicability/compliance
# contract explicit without adding an inference stage or changing the evidence.
CONSISTENT_FOCUS_PROTOCOL_NOTE = (
    '\nJudge only focus.object_key; object_id is a display number. '
    'Images 1/2: original/overlay; 3/4: matching original/overlay crops. '
    'MAGENTA=focus.judged_geometry; cyan/yellow=context only. '
    'Locate the intended object in the FULL original and annotation. '
    'An outline in background may belong to a nearby person. '
    'Displacement or omitted body parts are compliance issues, NOT grounds for exclusion. '
    'Labels may be wrong; do not substitute another person to make an outline fit. '
    'If intended identity is ambiguous, condition=unclear. '
    'condition tests guideline prerequisites (actual class, visibility), NOT geometry correctness. '
    'applies means these prerequisites hold. '
    'not_present requires naming an absent prerequisite. '
    'A visually identified skateboard is not_present for a person-only rule. '
    'When condition is not_present or unclear, compliance MUST be unclear. '
    'Otherwise compliance is yes/no/unclear from visible evidence. '
    'geometry must be in focus.judged_geometry: box=rectangle, polygon=contour. '
    'reason identifies the target and explains its prerequisite/boundary. '
    'Full images give context. Crop edges are not image edges unless '
    'focus.crop_edges_touching_image_edge says so. Follow guideline/context on hidden anatomy. '
    'Masks are not shown. Never invent tolerances or measure exact pixels visually. '
    'Feedback is an allegation; disagree when unsupported. '
    'Insufficient evidence means unclear.'
)


def focused_system(*, consistent: bool = False) -> str:
    from .feedback_prompt import SYSTEM
    return SYSTEM + (CONSISTENT_FOCUS_PROTOCOL_NOTE if consistent else FOCUS_PROTOCOL_NOTE)


def _supplied(geometry) -> list[str]:
    return [kind for kind, state in (('box', geometry.box), ('polygon', geometry.polygon)) if state == 'supplied']


def judged_geometry(scope, focus_key: str) -> list[str]:
    """Intersect supplied geometry with the pinned requirement; keep alternatives/relations intact."""
    target = next((item for item in scope.eligible if item.object_key == focus_key), None)
    if target is None:
        raise FeedbackError('POLICY_INCOMPATIBLE')
    supplied = _supplied(target.geometry)
    required = scope.applicability.geometry
    selected = [kind for kind in supplied if not required or kind in required]
    if not selected:
        raise FeedbackError('FEEDBACK_NOT_READY')
    return selected


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
    geometry = judged_geometry(scope, focus_key)
    try:
        views = render_focus(image, objects, focus_key, manifest.annotation_format, geometry=geometry)
    except (OSError, ValueError) as error:
        raise FeedbackError('FEEDBACK_NOT_READY') from error
    window = views.window
    attachments = [
        (f'Target {focus_key}: image_attachment 1, full ORIGINAL (unmarked).', image),
        (f'Target {focus_key}: image_attachment 2, full OVERLAY; thick MAGENTA={"+".join(geometry)}. '
         'Other outlines are context. Same coordinates as 1.', views.overlay_full),
        (f'Target {focus_key}: image_attachment 3, ORIGINAL CROP; window and scale in focus.', views.original_crop),
        (f'Target {focus_key}: image_attachment 4, OVERLAY CROP; same window and scale as 3.', views.overlay_crop)]
    if any(len(data) > execution.max_image_bytes for _, data in attachments):
        raise FeedbackError('CONTEXT_NOT_REPRESENTABLE')
    system = focused_system(consistent=execution.prompt_version == 'annotation-guideline-v7')
    messages = [{'role': 'system', 'content': system}]
    for caption, data in attachments:
        messages.append({'role': 'user', 'content': caption, 'images': [b64encode(data).decode('ascii')]})
    guideline = compact_rule(target.rule)
    document = {'case_id': unit_id(manifest, target), 'rule_id': target.rule.rule_id,
                'guideline': guideline, **context_fields(guideline),
                'requirement_scope': focused_scope_document(scope, focus_key),
                'focus': {'object_key': focus_key, 'judged_geometry': geometry,
                          'target_marking': 'thick magenta', **window.document()},
                'target': {'image_attachments': {'original': 1, 'overlay': 2, 'original_crop': 3, 'overlay_crop': 4},
                           'inputs': inputs}}
    if reviewer_feedback is not None:
        requested = reviewer_feedback.object_keys
        known = {item.object_key for item in [*scope.eligible, *scope.ineligible]}
        if requested is not None and not set(requested) <= known:
            raise FeedbackError('FEEDBACK_NOT_READY')
        if requested is None or focus_key in requested:
            document['reviewer_feedback'] = {
                'comment': reviewer_feedback.comment,
                'scope': 'image' if requested is None else 'object',
                'instruction': 'Reinspect this target against the original image and supplied annotation. '
                               'The comment may be wrong; it is not evidence of a violation. '
                               'Explain agreement or disagreement using the visible boundary and guideline.',
            }
    content = compact_json(document)
    schema = compact_schema(FocusedAssessment.model_json_schema())
    # Constrain generation as well as the instructions. Aggregation still checks geometry
    # independently, so a server ignoring this schema cannot publish a wrong-geometry verdict.
    schema['properties']['geometry']['enum'] = geometry
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
