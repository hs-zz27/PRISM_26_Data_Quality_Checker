"""Verify selected official dataset files and prepare label-free local inputs.

Run from the checkout root with --dataset-root or --archive. There is no network
access, model execution, database work or modification of source files here.
"""

import argparse
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

from PIL import Image

from backend.app.datasets.penn_fudan import parse_annotation_metadata, parse_annotations

PACKET = Path(__file__).resolve().parent
MAX_IMAGE_BYTES = 8 * 1024**2
MAX_ANNOTATION_BYTES = 131072
MAX_PIXELS = 16_000_000


class PreparationError(ValueError):
    """A source differs from the published corpus; do not guess a replacement."""


def write_json(path: Path, value: dict) -> None:
    """Use UTF-8/LF on Windows as well, keeping output fingerprints portable."""
    path.write_bytes((json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode())


@contextmanager
def source_reader(*, dataset_root: Path | None, archive_path: Path | None):
    if (dataset_root is None) == (archive_path is None):
        raise PreparationError("Choose exactly one dataset directory or ZIP archive.")
    if dataset_root is not None:
        def read(relative, limit):
            with (dataset_root / relative).open("rb") as stream:
                result = stream.read(limit + 1)
            if len(result) > limit:
                raise PreparationError(f"Source byte limit exceeded: {relative}")
            return result
        yield read
        return
    with ZipFile(archive_path) as archive:
        def read(relative, limit):
            # Read only exact expected members. Never extract the archive tree.
            candidates = [info for info in archive.infolist()
                          if info.filename in (relative, "PennFudanPed/" + relative)]
            if len(candidates) != 1:
                raise PreparationError(f"ZIP member missing or duplicated: {relative}")
            info = candidates[0]
            if info.file_size > limit:
                raise PreparationError(f"Source byte limit exceeded: {relative}")
            with archive.open(info) as stream:
                result = stream.read(limit + 1)
            if len(result) > limit:
                raise PreparationError(f"Source byte limit exceeded: {relative}")
            return result
        yield read


def prepare(output: Path, *, dataset_root: Path | None = None,
            archive_path: Path | None = None, packet: Path = PACKET) -> dict:
    if output.exists():
        raise PreparationError("Output already exists. Preserve it; choose a new output directory.")
    manifest_bytes = (packet / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest["schema_version"] != 1:
        raise PreparationError("Unsupported corpus manifest version.")
    names = [case["case_name"] for case in manifest["cases"]]
    if not names or len(set(names)) != len(names):
        raise PreparationError("Case names must be present and unique.")
    verified = []
    with source_reader(dataset_root=dataset_root, archive_path=archive_path) as read:
        for case in manifest["cases"]:
            name = case["case_name"]
            image = read(case["image"], MAX_IMAGE_BYTES)
            annotation = read(case["original_annotation"], MAX_ANNOTATION_BYTES)
            if sha256(image).hexdigest() != case["image_sha256"]:
                raise PreparationError(f"Source image hash differs: {name}")
            if sha256(annotation).hexdigest() != case["original_annotation_sha256"]:
                raise PreparationError(f"Original annotation hash differs: {name}")
            if case["annotation_override"] is not None:
                annotation = (packet / case["annotation_override"]).read_bytes()
            if len(annotation) > MAX_ANNOTATION_BYTES or sha256(annotation).hexdigest() != case["evaluation_annotation_sha256"]:
                raise PreparationError(f"Evaluation annotation hash differs: {name}")
            with Image.open(BytesIO(image)) as opened:
                if opened.format != "PNG" or getattr(opened, "n_frames", 1) != 1 or opened.width * opened.height > MAX_PIXELS:
                    raise PreparationError(f"Unsupported source image: {name}")
                dimensions = opened.size
                opened.verify()
            text = annotation.decode("utf-8", errors="strict")
            objects = parse_annotations(text)
            metadata = parse_annotation_metadata(text)
            if (list(dimensions) != case["image_size"] or metadata["errors"]
                    or metadata["image_size"] is None
                    or metadata["image_size"][:2] != dimensions
                    or metadata["coordinate_origin"] != (1, 1)
                    or metadata["object_count"] != len(objects)
                    or json.loads(json.dumps(objects)) != case["objects"]):
                raise PreparationError(f"Source metadata or object facts differ: {name}")
            verified.append((case, image, annotation, objects))
    # No output is published until every source case has verified successfully.
    output.mkdir(parents=True)
    cases = []
    for case, image, annotation, objects in verified:
        case_root = output / case["case_name"]
        case_root.mkdir()
        (case_root / "image.png").write_bytes(image)
        (case_root / "annotation.txt").write_bytes(annotation)
        identity = dict(case_name=case["case_name"], image_sha256=case["image_sha256"],
                        annotation_sha256=case["evaluation_annotation_sha256"], guideline=manifest["guideline"])
        case_id = sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        cases.append(dict(case_name=case["case_name"], case_id=case_id,
            partition=case["partition"], image=case["case_name"] + "/image.png",
            annotation=case["case_name"] + "/annotation.txt", inputs=dict(
                image_size=case["image_size"], image_sha256=case["image_sha256"],
                annotation_sha256=case["evaluation_annotation_sha256"],
                annotation_text=annotation.decode("utf-8"), objects=objects)))
    result = dict(schema_version=1, corpus_version=manifest["corpus_version"],
                  manifest_sha256=sha256(manifest_bytes).hexdigest(),
                  system_prompt_sha256=sha256((packet / "system_prompt_v1.txt").read_bytes()).hexdigest(),
                  guideline=manifest["guideline"], rule_id="VISIBLE-PEDESTRIAN", cases=cases)
    write_json(output / "scope.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--dataset-root", type=Path)
    source.add_argument("--archive", type=Path)
    parser.add_argument("--output", type=Path, default=Path(".cache/box-grounding/inputs"))
    args = parser.parse_args()
    result = prepare(args.output, dataset_root=args.dataset_root, archive_path=args.archive)
    print(f"Prepared {len(result['cases'])} verified cases in {args.output}")
    print("No model requests ran. Expected labels are kept outside the inference scope.")


if __name__ == "__main__":
    main()
