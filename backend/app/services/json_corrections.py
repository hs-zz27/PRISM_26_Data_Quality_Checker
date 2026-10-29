"""Revise native geometry without replacing originals or dropping source metadata."""
from copy import deepcopy
import json

from ..core.config import AssetStorageSettings
from ..core.correction import CorrectionError
from ..datasets.annotation_reader import parse_document_bytes
from ..datasets.annotation_validation import _decode_metadata, _geometry_issues
from ..datasets.json_sample import read_annotation, display_objects, publication
from ..datasets.coco import parse_coco
from ..datasets.labelme import parse_labelme
from ..models.annotation_validation import ValidationLimits
from ..models.asset_manifest import SampleAssetManifest
from ..models.correction import JsonCorrectionObject, PreparedCorrection
from ..models.dataset_import import ImportPolicy
from .correction_files import read_asset, write_asset


def prepare_json_correction(base, request, settings):
    if request.mask_upload_id or any(not isinstance(obj, JsonCorrectionObject) for obj in request.objects):
        raise CorrectionError('VALIDATION_ERROR')
    before = base.manifest
    original = read_asset(settings, before.annotation_text)
    image = read_asset(settings, before.original_image)
    limits = ValidationLimits(max_image_bytes=settings.policy.max_asset_bytes, max_image_pixels=settings.policy.max_pixels)
    try:
        width, height, _, orientation = _decode_metadata(image, limits)
        if orientation != 1:
            raise ValueError('Unsupported orientation.')
        document, record = read_annotation(original, before.annotation_format, before.image_key)
        prior = display_objects(record)
        if len(prior) != len(request.objects):
            raise ValueError('Adding or deleting source records is not supported by this editor.')
        updated = deepcopy(document.source)
        for old, source, new in zip(prior, record.annotations, request.objects):
            if any(getattr(old, field) != getattr(new, field) for field in (
                'object_id', 'source_key', 'source_id', 'group_id', 'bbox_origin', 'coordinate_system',
            )):
                raise ValueError('Source identities must remain unchanged.')
            if source.mask is not None or any(i.severity == 'unsupported' for i in source.issues):
                raise ValueError('Unsupported annotation encoding.')
            index = int(source.key.rsplit('/', 1)[1])
            if document.format == 'coco':
                target = updated['annotations'][index]
                categories = [c for c in updated['categories'] if c.get('name') == new.label]
                if len(categories) != 1:
                    raise ValueError('Select one existing unambiguous category.')
                target['category_id'] = categories[0]['id']
                if source.bbox_origin == 'supplied':
                    if new.bbox is None:
                        raise ValueError('A supplied box cannot be removed.')
                    x1, y1, x2, y2 = new.bbox
                    target['bbox'] = [x1, y1, x2-x1, y2-y1]
                if new.polygons != old.polygons:
                    if not new.polygons or not old.polygons:
                        raise ValueError('Keep the source geometry type.')
                    target['segmentation'] = [[v for point in part for v in point] for part in new.polygons]
            else:
                target = updated['shapes'][index]
                target['label'] = new.label
                if target['shape_type'] == 'rectangle':
                    if new.bbox is None or new.polygons:
                        raise ValueError('A rectangle needs a box.')
                    x1, y1, x2, y2 = new.bbox
                    target['points'] = [[x1, y1], [x2, y2]]
                elif target['shape_type'] == 'polygon':
                    if len(new.polygons) != 1:
                        raise ValueError('A LabelMe polygon shape needs exactly one polygon.')
                    target['points'] = [list(point) for point in new.polygons[0]]
                else:
                    raise ValueError('Unsupported shape type.')
        parser = parse_coco if document.format == 'coco' else parse_labelme
        checked_document = parser(updated)
        checked_record = next(item for item in checked_document.images if item.key == before.image_key)
        # Validate before calling native polygon rasterization (bounded, in-image vertices).
        errors = [*checked_document.issues, *checked_record.issues]
        for obj in checked_record.annotations:
            errors.extend(obj.issues)
            errors.extend(_geometry_issues(obj, (width, height), limits))
        if errors or checked_document.unassigned_annotations:
            raise ValueError('The resulting sample still contains structural/unsupported issues.')
        if [obj.model_dump() for obj in display_objects(checked_record)] != [obj.model_dump() for obj in request.objects]:
            raise ValueError('Derived bounds must match the edited polygon geometry.')
        if document.format == 'coco':
            from pycocotools import mask as coco_mask
            for old, new, source in zip(prior, request.objects, checked_record.annotations):
                target = updated['annotations'][int(source.key.rsplit('/', 1)[1])]
                if new.polygons != old.polygons and 'area' in target:
                    encoded = coco_mask.frPyObjects(target['segmentation'], height, width)
                    target['area'] = int(coco_mask.area(coco_mask.merge(encoded)))
                elif not new.polygons and new.bbox != old.bbox and 'area' in target:
                    target['area'] = (new.bbox[2]-new.bbox[0])*(new.bbox[3]-new.bbox[1])
        content = (json.dumps(updated, ensure_ascii=True, allow_nan=False) + '\n').encode()
    except (ValueError, TypeError, KeyError, StopIteration, IndexError) as error:
        raise CorrectionError('JSON_SOURCE_NOT_CORRECTABLE') from error
    asset = write_asset(settings, content, role='annotation', annotation_format=before.annotation_format)
    manifest = SampleAssetManifest.model_validate({**before.model_dump(),
        'sample_revision': before.sample_revision + 1, 'annotation_text': asset})
    policy = ImportPolicy(max_annotation_bytes=settings.policy.max_annotation_bytes,
        max_file_bytes=settings.policy.max_asset_bytes, max_image_pixels=settings.policy.max_pixels)
    revised = publication(manifest, base.ordinal, AssetStorageSettings(root=settings.asset_root), policy)
    if revised.sample.integrity_status.value != 'valid':
        raise CorrectionError('JSON_SOURCE_NOT_CORRECTABLE')
    return PreparedCorrection(request=request, base=base, revised=revised)
