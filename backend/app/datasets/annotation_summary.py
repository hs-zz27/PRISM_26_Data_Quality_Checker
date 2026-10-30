"""Per-object statement of which annotation types exist and where each came from.

This answers, without judging quality:

* ``supplied``            the source contained this annotation type and it parsed.
* ``derived``             this tool computed it from other geometry (never from source).
* ``not_supplied``        the source never contained this type. Not an error.
* ``malformed``           the source contained this type but it could not be parsed;
                          the original value stays in ``AnnotationRecord.source``.
* ``retained_not_decoded`` supplied in an encoding this tool keeps but does not decode
                          (COCO RLE, LabelMe embedded mask).
* ``referenced_missing``  the source points at a mask file that is not there. This is
                          different from ``not_supplied`` and is a reader-level fact only
                          when a file-existence check is given.

Nothing here modifies a record. A summary is derived evidence about the reader output,
not an audit outcome.
"""

from dataclasses import dataclass
from typing import Any, Callable, Literal

from ..models.annotation_document import AnnotationRecord

State = Literal["supplied", "derived", "not_supplied", "malformed",
                "retained_not_decoded", "referenced_missing"]


@dataclass(frozen=True)
class TypeStatus:
    state: State
    detail: str | None = None


@dataclass(frozen=True)
class GeometrySummary:
    box: TypeStatus
    polygon: TypeStatus
    mask: TypeStatus
    polygon_parts: int = 0
    polygon_vertex_counts: tuple[int, ...] = ()

    @property
    def types_present(self) -> list[str]:
        """Names of types that exist in the output, original or derived."""
        found = []
        for name in ("box", "polygon", "mask"):
            if getattr(self, name).state in ("supplied", "derived", "retained_not_decoded",
                                             "referenced_missing"):
                found.append(name if getattr(self, name).state != "derived" else name + " (derived)")
        return found


def _source_mapping(obj: AnnotationRecord) -> dict:
    return obj.source if isinstance(obj.source, dict) else {}


def _box_status(obj: AnnotationRecord, source: dict) -> TypeStatus:
    if obj.bbox is not None and obj.bbox_origin == "supplied":
        return TypeStatus("supplied")
    if obj.bbox is not None and obj.bbox_origin == "derived_from_polygon":
        return TypeStatus("derived", f"computed from {len(obj.polygons)} polygon part(s); "
                                     "the source has no box")
    # A box field or rectangle shape exists in the source but produced no box.
    if "bbox" in source or source.get("shape_type") == "rectangle":
        return TypeStatus("malformed", "source box could not be parsed; original is retained")
    return TypeStatus("not_supplied")


def _polygon_status(obj: AnnotationRecord, source: dict) -> TypeStatus:
    rejected = sum(1 for issue in obj.issues if issue.code == "invalid_polygon")
    if obj.polygons:
        detail = f"{rejected} further part(s) could not be parsed" if rejected else None
        return TypeStatus("supplied", detail)
    segmentation = source.get("segmentation")
    expects_polygon = source.get("shape_type") == "polygon" or (
        isinstance(segmentation, list) and len(segmentation) > 0)
    if expects_polygon or rejected:
        return TypeStatus("malformed", "source polygon could not be parsed; original is retained")
    if isinstance(segmentation, list):
        return TypeStatus("not_supplied", "source has an empty segmentation list")
    return TypeStatus("not_supplied")


def _mask_status(obj: AnnotationRecord, mask_file_exists: Callable[[Any], bool] | None) -> TypeStatus:
    mask = obj.mask
    if mask is None:
        return TypeStatus("not_supplied")
    if mask.encoding in ("coco_rle", "labelme_png"):
        return TypeStatus("retained_not_decoded", f"{mask.encoding} is kept as supplied, not decoded")
    if mask_file_exists is None:
        return TypeStatus("supplied", "mask file reference; existence was not checked")
    if mask_file_exists(mask.value):
        return TypeStatus("supplied")
    return TypeStatus("referenced_missing", "the source references a mask file that cannot be found")


def summarize_annotation(obj: AnnotationRecord, *,
                         mask_file_exists: Callable[[Any], bool] | None = None) -> GeometrySummary:
    """Describe one record. ``mask_file_exists`` is only used for file-referenced masks."""
    source = _source_mapping(obj)
    return GeometrySummary(
        box=_box_status(obj, source),
        polygon=_polygon_status(obj, source),
        mask=_mask_status(obj, mask_file_exists),
        polygon_parts=len(obj.polygons),
        polygon_vertex_counts=tuple(len(part) for part in obj.polygons),
    )
