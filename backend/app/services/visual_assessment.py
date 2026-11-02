"""Ground object references in source geometry and aggregate without a model vote.

This validates evidence references, not visual truth. Applicability, class identity
and boundary fit still need the model and reviewer; no keyword-based guideline
interpretation or annotation-label filter silently decides those questions.
"""
from ..models.feedback import Prediction


def object_inventory(objects, annotation_format):
    rows = []
    for obj in objects:
        geometry = ['label']
        supplied_box = obj.get('bbox') is not None and (
            annotation_format == 'penn_fudan' or obj.get('bbox_origin') == 'supplied')
        if supplied_box:
            geometry.append('box')
        if obj.get('polygons'):
            geometry.append('polygon')
        # Raw masks are not attached by this visual protocol. The deterministic
        # mask checks remain separate; their existence cannot justify visual PASS.
        rows.append({'object_key': obj.get('source_key', f"penn_fudan:{obj['object_id']}"),
                     'object_id': obj['object_id'], 'label': obj['label'],
                     'available_geometry': geometry,
                     'bbox_origin': 'supplied' if annotation_format == 'penn_fudan' else obj.get('bbox_origin')})
    if len({row['object_key'] for row in rows}) != len(rows):
        raise ValueError('Ambiguous annotation identities.')
    return rows


def grounded_prediction(assessment, inventory, image_size):
    """Require every source object exactly once; never invent a missing verdict."""
    sources = {row['object_key']: row for row in inventory}
    keys = [observation.object_key for observation in assessment.observations]
    if len(keys) != len(set(keys)) or set(keys) != set(sources):
        raise ValueError('object_coverage_mismatch')
    width, height = image_size
    for observation in assessment.observations:
        available = sources[observation.object_key]['available_geometry']
        if observation.outcome in {'pass', 'fail'} and not set(observation.geometry) <= set(available):
            raise ValueError('unsupported_visual_geometry')
        if observation.region is not None:
            left, top, right, bottom = observation.region
            if not (0 <= left < right <= width and 0 <= top < bottom <= height):
                raise ValueError('review_region_out_of_bounds')

    applicable = [item for item in assessment.observations if item.outcome != 'not_applicable']
    # A demonstrated violation survives other uncertain objects. Passing requires
    # every applicable object to pass. Nothing applicable is not a passing audit.
    outcome = assessment.aggregate_outcome()
    relevant = [item for item in applicable if item.outcome == outcome]
    if not applicable:
        reason = 'No applicable annotation was established. Review the guideline scope and object observations.'
    else:
        prefix = f'{len(relevant)}/{len(applicable)} applicable objects: {outcome}.'
        explanations = [f"Object #{sources[item.object_key]['object_id']}: {item.reason}" for item in relevant]
        reason = ' '.join([prefix, *explanations])[:1024]
    return Prediction(case_id=assessment.case_id, rule_id=assessment.rule_id,
                      outcome=outcome, reason=reason,
                      evidence_references=['image', 'annotations', 'guideline'])
