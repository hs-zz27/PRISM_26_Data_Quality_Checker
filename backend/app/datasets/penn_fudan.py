import re
from pathlib import Path
from PIL import Image
import numpy as np


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


def load_sample(dataset_root, image_id):
    dataset_root = Path(dataset_root)

    image_path = dataset_root / "PNGImages" / f"{image_id}.png"
    annotation_path = dataset_root / "Annotation" / f"{image_id}.txt"
    mask_path = dataset_root / "PedMasks" / f"{image_id}_mask.png"

    with Image.open(image_path) as file:
        image = file.convert("RGB")

    annotation_text = annotation_path.read_text()

    with Image.open(mask_path) as file:
        mask = np.array(file)

    return image, annotation_text, mask


def parse_annotations(annotation_text):
    """Extract each person's ID, label and box, keeping source coordinates."""
    objects = []

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

    return objects


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
