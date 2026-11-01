#!/usr/bin/env python3
"""Download only the pinned L01 source pair; never run application code."""

import argparse
import hashlib
import io
import json
from pathlib import Path
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[3] / 'datasets' / 'labelme_l01_example'
MANIFEST = ROOT / 'download_manifest.json'
MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
MAX_MEMBER_BYTES = 2 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def source_members(archive, manifest):
    """Validate the whole download and both members before writing either file."""
    if len(archive) > MAX_ARCHIVE_BYTES or digest(archive) != manifest['archive_sha256']:
        raise ValueError('Downloaded archive differs from the pinned SHA-256 or exceeds the size limit.')
    outputs = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        names = source.namelist()
        for entry in manifest['files']:
            filename, member = entry['file'], entry['archive_member']
            if Path(filename).name != filename or '\\' in filename or filename in ('.', '..'):
                raise ValueError('Manifest output must be a plain filename.')
            if filename in outputs or names.count(member) != 1:
                raise ValueError('Duplicate output or missing/duplicate archive member.')
            info = source.getinfo(member)
            if info.is_dir() or info.file_size > MAX_MEMBER_BYTES:
                raise ValueError('Unexpected source member size/type.')
            data = source.read(info)
            if digest(data) != entry['sha256']:
                raise ValueError(f'{filename} differs from the pinned SHA-256.')
            outputs[filename] = data
    return outputs


def save_sources(outputs, destination):
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in outputs.items():
        path = destination / name
        if path.is_symlink() or (path.exists() and (not path.is_file() or path.read_bytes() != data)):
            raise ValueError(f'Refusing to replace different existing source: {path}')
    for name, data in outputs.items():
        path = destination / name
        if not path.exists():
            with path.open('xb') as handle:
                handle.write(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, help='Use an already downloaded pinned archive instead of the network.')
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
        if args.archive:
            with args.archive.open('rb') as handle:
                data = handle.read(MAX_ARCHIVE_BYTES + 1)
        else:
            print('Downloading the pinned LabelMe tutorial archive (about 18 MB)...', flush=True)
            with urlopen(manifest['archive_url'], timeout=60) as response:
                data = response.read(MAX_ARCHIVE_BYTES + 1)
        outputs = source_members(data, manifest)
        save_sources(outputs, ROOT)
        for name, data in outputs.items():
            print(f'{ROOT / name}: {digest(data)}')
        print('Original sources downloaded. Candidate checks, pack build and human review are separate steps.')
        return 0
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f'download_l01_source: {error}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
