"""Verify bounded immutable inputs, without model execution or database access."""
from io import BytesIO
from hashlib import sha256
import warnings
from PIL import Image
from ..core.feedback import FeedbackError
from ..models.feedback import EvidenceLimits, ExecutionPin, checked
from ..models.asset_manifest import AssetKind
from ..storage.workflow_repository import read_manifest_asset_file, AssetIntegrityError, AssetReadLimitError, AssetUnavailableError
from ..datasets.penn_fudan import parse_annotations, parse_annotation_metadata

def _evidence(manifest, execution: EvidenceLimits | ExecutionPin, asset_settings, *, compact=False, native_table=False):
    if isinstance(execution,ExecutionPin):
        execution=checked(ExecutionPin,execution)
        execution=EvidenceLimits(max_image_bytes=execution.max_image_bytes,max_annotation_bytes=execution.max_annotation_bytes,max_pixels=execution.max_pixels)
    else:execution=checked(EvidenceLimits,execution)
    try:
        _, image = read_manifest_asset_file(manifest, AssetKind.ORIGINAL_IMAGE, asset_settings, max_bytes=execution.max_image_bytes)
        _, annotation = read_manifest_asset_file(manifest, AssetKind.ANNOTATION_TEXT, asset_settings, max_bytes=execution.max_annotation_bytes)
        text = annotation.decode('utf-8', errors='strict')
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(image)) as picture:
                if picture.format not in {'PNG', 'JPEG'} or getattr(picture, 'n_frames', 1) != 1 or picture.width * picture.height > execution.max_pixels:
                    raise ValueError('Unsupported image.')
                dimensions = picture.size
                picture.verify()
            with Image.open(BytesIO(image)) as picture:
                picture.load()
                if manifest.annotation_format != 'penn_fudan' and picture.getexif().get(274, 1) != 1:
                    raise ValueError('Unsupported image orientation.')
        if manifest.annotation_format != 'penn_fudan':
            import json
            from ..datasets.json_sample import read_annotation, display_objects
            document, record = read_annotation(annotation, manifest.annotation_format, manifest.image_key)
            if document.issues or record.issues or document.unassigned_annotations or any(obj.issues for obj in record.annotations):
                raise ValueError('Incomplete or unsupported annotations.')
            if (record.width, record.height) != dimensions:
                raise ValueError('Annotation dimensions disagree.')
            objects = [obj.model_dump(mode='json') for obj in display_objects(record)]
            if compact:
                # Native geometry appears exactly once. Normalized labels and keys
                # supply the mapping that raw COCO category IDs cannot convey.
                inputs = {'annotation_format': document.format,
                    'coordinate_system': 'zero_based_continuous', 'image_size': list(dimensions),
                    'bbox_encoding': 'xywh' if document.format == 'coco' else 'native_shape_points',
                    'image': record.source,
                    'annotation_columns': ['object_key', 'object_id', 'label', 'source'],
                    'annotations': [[obj.key, view['object_id'], view['label'], obj.source]
                                    for obj, view in zip(record.annotations, objects)]}
                if native_table:
                    # Share repeated native field names without dropping any
                    # attribute or conflating an absent field with a null value.
                    sources = [obj.source for obj in record.annotations]
                    columns = [key for key in sources[0] if all(key in source for source in sources)] if sources else []
                    inputs['source_columns'] = columns
                    inputs['annotation_columns'] = ['object_key', 'object_id', 'label', 'source_values', 'source_extra']
                    inputs['annotations'] = [[obj.key, view['object_id'], view['label'],
                        [obj.source[key] for key in columns],
                        {key: value for key, value in obj.source.items() if key not in columns}]
                        for obj, view in zip(record.annotations, objects)]
                return image, inputs, objects
            text = json.dumps({'format': document.format, 'coordinate_system': 'zero_based_continuous',
                               'image': record.source, 'annotations': [obj.source for obj in record.annotations]}, allow_nan=False)
            return (image, {'annotation_text': text, 'objects': objects, 'image_size': list(dimensions),
                'annotation_format': document.format, 'image_sha256': sha256(image).hexdigest(),
                'annotation_sha256': sha256(annotation).hexdigest()}, objects)
        objects = parse_annotations(text)
        metadata = parse_annotation_metadata(text)
        if metadata['errors'] or metadata['object_count'] != len(objects):
            raise ValueError('Incomplete annotations.')
        if metadata['image_size'] is None or tuple(metadata['image_size'][:2]) != dimensions:
            raise ValueError('Annotation dimensions disagree.')
        if compact:
            return image, {'annotation_format': 'penn_fudan', 'coordinate_system': 'one_based_inclusive',
                           'bbox_encoding': 'xyxy', 'image_size': list(dimensions), 'objects': objects}, objects
        return (image, {'annotation_text': text, 'objects': objects, 'image_size': list(dimensions), 'image_sha256': sha256(image).hexdigest(), 'annotation_sha256': sha256(annotation).hexdigest()}, objects)
    except (AssetIntegrityError, AssetReadLimitError, AssetUnavailableError, OSError, ValueError, UnicodeError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise FeedbackError('FEEDBACK_NOT_READY') from error


def evidence(manifest, execution: EvidenceLimits | ExecutionPin, asset_settings, *, compact=False):
    image, inputs, _ = _evidence(manifest, execution, asset_settings, compact=compact)
    return image, inputs


def visual_evidence(manifest, execution, asset_settings):
    """Use the same verified source and normalized objects for the text and overlay."""
    from .annotation_overlay import annotation_overlay
    image, inputs, objects = _evidence(manifest, execution, asset_settings, compact=True, native_table=True)
    try:
        overlay = annotation_overlay(image, objects, manifest.annotation_format)
    except (OSError, ValueError) as error:
        raise FeedbackError('FEEDBACK_NOT_READY') from error
    if len(overlay) > execution.max_image_bytes:
        raise FeedbackError('CONTEXT_NOT_REPRESENTABLE')
    return image, inputs, overlay
