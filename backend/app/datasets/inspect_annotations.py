"""Local read-only CLI for COCO/LabelMe structural inspection. Not an AI audit."""

import argparse
from dataclasses import asdict
import json
import sys

from .annotation_reader import AnnotationReadError
from .annotation_validation import inspect_document
from .coco import read_coco
from .labelme import read_labelme


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("format", choices=("coco", "labelme"))
    parser.add_argument("annotations", help="Path to one annotation JSON file")
    parser.add_argument("--image-root", required=True, help="Allowed root directory containing image files")
    parser.add_argument("--reference-base", help="Override directory for relative image references")
    args = parser.parse_args(argv)
    try:
        document = (read_coco if args.format == "coco" else read_labelme)(args.annotations)
        report = inspect_document(document, args.image_root, reference_base=args.reference_base)
    except (AnnotationReadError, ValueError, OSError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2
    payload = asdict(report)
    payload["inspection_only"] = True
    payload["diagnostic_count"] = len(report.all_issues)
    print(json.dumps(payload, indent=2, ensure_ascii=True, allow_nan=False))
    # Nonzero means diagnostics or an incomplete check, never automatic rejection.
    return 1 if report.all_issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
