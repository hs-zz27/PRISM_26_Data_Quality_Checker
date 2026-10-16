"""Compare declared Penn-Fudan metadata with the assets that could be read."""

from ..models.annotation import AnnotationMetadata
from ..models.measurements import MeasurementResult


class MetadataResult(MeasurementResult):
    matches: bool | None
    declared: AnnotationMetadata
    actual_image_size: tuple[int, int, int] | None
    actual_object_count: int | None
    issues: dict[str, str]


def check_annotation_metadata(
    metadata: AnnotationMetadata,
    image_size: tuple[int, int, int] | None,
    object_count: int | None,
) -> MetadataResult:
    """Keep known mismatches visible even when another comparison is unavailable."""
    issues = dict(metadata["errors"])
    comparisons: list[bool] = []
    for field, declared, actual, mismatch in (
        ("image_size", metadata["image_size"], image_size, "image_size_mismatch"),
        ("object_count", metadata["object_count"], object_count, "object_count_mismatch"),
        ("coordinate_origin", metadata["coordinate_origin"], (1, 1), "unsupported_coordinate_origin"),
    ):
        if declared is None:
            continue
        if actual is None:
            issues[field] = "source_unavailable"
        else:
            matches = declared == actual
            comparisons.append(matches)
            if not matches:
                issues[field] = mismatch
    available = len(comparisons) == 3
    return {
        "measurement_available": available,
        "reason": None if available else "metadata_comparison_unavailable",
        "matches": all(comparisons) if available else None,
        "declared": metadata,
        "actual_image_size": image_size,
        "actual_object_count": object_count,
        "issues": issues,
    }
