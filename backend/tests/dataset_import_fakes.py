"""Tiny authored ZIP/PNG builders; imported by tests, never at application startup."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import zipfile

from PIL import Image

from backend.app.core.dataset_import import ImportSettings
from backend.app.models.dataset_import import ImportPolicy, ImportRequest


def png(mode="RGB", color=0):
    output = BytesIO()
    Image.new(mode, (3, 2), color).save(output, format="PNG")
    return output.getvalue()


def annotation(*, count=0, boxes=""):
    return (f"Image size (X x Y x C) : 3 x 2 x 3\r\n"
            f"Objects with ground truth : {count} {{}}\r\n"
            "Top left pixel co-ordinates : (1, 1)\r\n" + boxes).encode("utf-8")


def entries(prefix=""):
    return [(prefix + "PNGImages/a.png", png()),
            (prefix + "Annotation/a.txt", annotation()),
            (prefix + "PedMasks/a_mask.png", png("I;16", 513))]


def zip_bytes(items, *, compression=zipfile.ZIP_STORED):
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, content in items:
            archive.writestr(name, content)
    return output.getvalue()


def staged(tmp_path: Path, content: bytes, *, policy=None):
    root, assets = tmp_path / "imports", tmp_path / "assets"
    root.mkdir(exist_ok=True)
    assets.mkdir(exist_ok=True)
    path = root / "input.zip"
    path.write_bytes(content)
    request = ImportRequest(job_id="job_zip", dataset_id="dataset_zip", archive_key="input.zip",
                            archive_sha256=sha256(content).hexdigest(), archive_size_bytes=len(content),
                            policy=ImportPolicy() if policy is None else policy)
    settings = ImportSettings(root=root, asset_root=assets, policy=request.policy)
    return path, request, settings
