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

def evidence(manifest, execution: EvidenceLimits | ExecutionPin, asset_settings):
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
            text = json.dumps({'format': document.format, 'coordinate_system': 'zero_based_continuous',
                               'image': record.source, 'annotations': [obj.source for obj in record.annotations]}, allow_nan=False)
            return (image, {'annotation_text': text, 'objects': objects, 'image_size': list(dimensions),
                'annotation_format': document.format, 'image_sha256': sha256(image).hexdigest(),
                'annotation_sha256': sha256(annotation).hexdigest()})
        objects = parse_annotations(text)
        metadata = parse_annotation_metadata(text)
        if metadata['errors'] or metadata['object_count'] != len(objects):
            raise ValueError('Incomplete annotations.')
        if metadata['image_size'] is None or tuple(metadata['image_size'][:2]) != dimensions:
            raise ValueError('Annotation dimensions disagree.')
        return (image, {'annotation_text': text, 'objects': objects, 'image_size': list(dimensions), 'image_sha256': sha256(image).hexdigest(), 'annotation_sha256': sha256(annotation).hexdigest()})
    except (AssetIntegrityError, AssetReadLimitError, AssetUnavailableError, OSError, ValueError, UnicodeError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise FeedbackError('FEEDBACK_NOT_READY') from error
