"""Read exact captured COCO/LabelMe evidence using its pinned image key."""
from ..models.annotation_document import ReaderIssue
from ..models.annotation_validation import ValidationLimits
from ..models.dataset import JsonAnnotationObject, Sample, source_issue
from ..models.dataset_import import SamplePublication
from ..storage.workflow_repository import read_manifest_asset_file
from .annotation_reader import parse_document_bytes
from .annotation_validation import _decode_metadata, _geometry_issues, ImageInputError
from .coco import parse_coco
from .labelme import parse_labelme


def read_annotation(content, format, image_key):
    parser = {"coco": parse_coco, "labelme": parse_labelme}[format]
    document = parse_document_bytes(content, parser)
    images = [image for image in document.images if image.key == image_key]
    if len(images) != 1:
        raise ValueError("The pinned image record is absent or ambiguous.")
    return document, images[0]


def display_objects(record):
    return [JsonAnnotationObject(
        coordinate_system="zero_based_continuous", object_id=index + 1,
        source_key=obj.key, source_id=obj.source_id, group_id=obj.group_id,
        label=obj.label or "", bbox=obj.bbox, bbox_origin=obj.bbox_origin,
        polygons=obj.polygons,
    ) for index, obj in enumerate(record.annotations)]


def inspect_sample(manifest, settings, limits, *, max_annotation_bytes):
    _, annotation = read_manifest_asset_file(manifest, "annotation_text", settings,
                                              max_bytes=max_annotation_bytes)
    document, record = read_annotation(annotation, manifest.annotation_format, manifest.image_key)
    issues = [*document.issues, *record.issues]
    if document.unassigned_annotations:
        issues.append(ReaderIssue("unassigned_annotations", "", "The source document contains annotations without an unambiguous image association."))
    for obj in record.annotations:
        issues.extend(obj.issues)
    size = None
    if manifest.original_image.availability.value != "available":
        issues.append(ReaderIssue("missing_image", record.key, "The referenced image is unavailable."))
    else:
        _, image = read_manifest_asset_file(manifest, "original_image", settings, max_bytes=limits.max_image_bytes)
        try:
            width, height, _, orientation = _decode_metadata(image, limits)
            if orientation != 1:
                issues.append(ReaderIssue("unsupported_image_orientation", record.key,
                                          "EXIF orientation needs an explicit coordinate mapping.", "unsupported"))
            else:
                size = (width, height)
                if (width, height) != (record.width, record.height):
                    issues.append(ReaderIssue("dimension_mismatch", record.key, "Declared and decoded image dimensions disagree."))
        except ImageInputError as error:
            issues.append(ReaderIssue(error.code, record.key, str(error)))
    for obj in record.annotations:
        issues.extend(_geometry_issues(obj, size, limits))
    return document, record, size, issues


def publication(manifest, ordinal, settings, policy):
    limits = ValidationLimits(max_image_bytes=policy.max_file_bytes, max_image_pixels=policy.max_image_pixels)
    document, record, size, issues = inspect_sample(manifest, settings, limits,
                                                   max_annotation_bytes=policy.max_annotation_bytes)
    if len(record.annotations) > policy.max_objects_per_sample:
        from ..core.dataset_import import ImportResourceError
        raise ImportResourceError()
    codes = set()
    if size is None:
        codes.add(("invalid_image", "image"))
    if any(issue.severity == "error" for issue in issues):
        codes.add(("invalid_annotation", "annotation"))
    if any(issue.severity == "unsupported" for issue in issues):
        codes.add(("unsupported_source", "annotation"))
    return SamplePublication(manifest=manifest, ordinal=ordinal,
        annotation_object_count=len(record.annotations), sample=Sample(
            dataset_id=manifest.dataset_id, sample_id=manifest.sample_id, revision=manifest.sample_revision,
            annotation_format=manifest.annotation_format,
            image_size=size, objects=display_objects(record), integrity_status="invalid" if codes else "valid",
            issues=[source_issue(code, field=field) for code, field in sorted(codes)]))
