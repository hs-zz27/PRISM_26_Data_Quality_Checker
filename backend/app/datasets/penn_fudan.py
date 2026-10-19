"""Read Penn-Fudan assets and preserve annotation values for integrity checks."""

import re
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from ..models.annotation import AnnotationMetadata, AnnotationObject


BOUNDING_BOX_PATTERN = re.compile(
    r'''
    # Object ID and quoted class label
    Bounding\ box\ for\ object\s+(?P<object_id>\d+)\s+
    "(?P<label>[^"]+)"\s+

    # Coordinate headings and separator
    \(Xmin,\s*Ymin\)\s*-\s*\(Xmax,\s*Ymax\)\s*:\s*

    # Top-left corner
    \(\s*(?P<xmin>-?\d+),\s*(?P<ymin>-?\d+)\s*\)\s*-\s*

    # Bottom-right corner
    \(\s*(?P<xmax>-?\d+),\s*(?P<ymax>-?\d+)\s*\)
    ''',
    re.VERBOSE,
)


def load_image(dataset_root: str | Path, image_id: str) -> Image.Image:
    """Keep the source mode so metadata checks can inspect its channel count."""
    path = Path(dataset_root) / "PNGImages" / f"{image_id}.png"
    with Image.open(path) as file:
        return file.copy()


def load_annotation_text(dataset_root: str | Path, image_id: str) -> str:
    path = Path(dataset_root) / "Annotation" / f"{image_id}.txt"
    return path.read_text(encoding="utf-8")


def load_mask(dataset_root: str | Path, image_id: str) -> NDArray:
    path = Path(dataset_root) / "PedMasks" / f"{image_id}_mask.png"
    with Image.open(path) as file:
        return np.array(file)


def load_sample(dataset_root: str | Path, image_id: str) -> tuple[Image.Image, str, NDArray]:
    """Load a complete sample, raising if any asset cannot be read."""
    return (
        load_image(dataset_root, image_id).convert("RGB"),
        load_annotation_text(dataset_root, image_id),
        load_mask(dataset_root, image_id),
    )


def parse_annotations(annotation_text: str) -> list[AnnotationObject]:
    """Extract source boxes; an empty annotation must explicitly declare zero objects."""
    objects: list[AnnotationObject] = []

    for line_number, line in enumerate(annotation_text.splitlines(), start=1):
        line = line.strip()
        if not line.startswith("Bounding box for object"):
            continue

        match = BOUNDING_BOX_PATTERN.fullmatch(line)
        if match is None:
            raise ValueError(f"Malformed bounding box on line {line_number}: {line}")

        fields = match.groupdict()
        objects.append({
            "object_id": int(fields["object_id"]),
            "label": fields["label"],
            "bbox": (
                int(fields["xmin"]), int(fields["ymin"]),
                int(fields["xmax"]), int(fields["ymax"]),
            ),
        })

    if not objects and parse_annotation_metadata(annotation_text)["object_count"] != 0:
        raise ValueError("No bounding boxes were parsed and no valid zero-object declaration was found.")
    return objects


_METADATA_PATTERNS = {
    "image_size": (
        "Image size",
        re.compile(r"Image size\s*\(X x Y x C\)\s*:\s*(-?\d+)\s*x\s*(-?\d+)\s*x\s*(-?\d+)"),
    ),
    "object_count": (
        "Objects with ground truth",
        re.compile(r"Objects with ground truth\s*:\s*(-?\d+)\s*\{[^}]*\}"),
    ),
    "coordinate_origin": (
        "Top left pixel co-ordinates",
        re.compile(r"Top left pixel co-ordinates\s*:\s*\(\s*(-?\d+)\s*,\s*(-?\d+)\s*\)"),
    ),
}


def parse_annotation_metadata(annotation_text: str) -> AnnotationMetadata:
    """Read declared headers independently; retain errors per metadata field.

    Missing, malformed or repeated headers never acquire fabricated defaults.
    A damaged header does not discard valid bounding-box annotations.
    """
    lines = [line.strip().removeprefix("#").strip() for line in annotation_text.splitlines()]
    values: dict[str, tuple[int, ...] | None] = {}
    errors: dict[str, str] = {}
    for field, (prefix, pattern) in _METADATA_PATTERNS.items():
        candidates = [line for line in lines if line.startswith(prefix)]
        values[field] = None
        if not candidates:
            errors[field] = "missing_metadata"
        elif len(candidates) > 1:
            errors[field] = "duplicate_metadata"
        else:
            match = pattern.fullmatch(candidates[0])
            if match is None:
                errors[field] = "malformed_metadata"
            else:
                try:
                    values[field] = tuple(int(value) for value in match.groups())
                except ValueError:
                    errors[field] = "malformed_metadata"

    size = values["image_size"]
    count = values["object_count"]
    origin = values["coordinate_origin"]
    return {
        "image_size": (size[0], size[1], size[2]) if size is not None else None,
        "object_count": count[0] if count is not None else None,
        "coordinate_origin": (origin[0], origin[1]) if origin is not None else None,
        "errors": errors,
    }


if __name__ == "__main__":
    dataset_root = Path(__file__).resolve().parents[3] / "PennFudanPed"

    image, annotation_text, mask = load_sample(
        dataset_root, "FudanPed00001"
    )

    print("Image size:", image.size)
    print("Mask shape:", mask.shape)
    print("Mask values:", np.unique(mask))
    print(annotation_text)
    print("Parsed annotations:", parse_annotations(annotation_text))
