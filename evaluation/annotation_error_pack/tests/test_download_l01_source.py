"""Offline synthetic archives exercise acquisition without network access."""
import hashlib
import importlib.util
import io
from pathlib import Path
import zipfile

import pytest

SPEC = importlib.util.spec_from_file_location('download_l01_source',
    Path(__file__).resolve().parents[1] / 'tools' / 'download_l01_source.py')
dl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dl)


def fixture_archive():
    files = {'a.jpg': b'SYNTHETIC IMAGE BYTES', 'a.json': b'{"synthetic":true}'}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, data in files.items():
            archive.writestr('source/' + name, data)
        archive.writestr('unselected.txt', 'Must not be extracted')
    data = buffer.getvalue()
    manifest = {'archive_sha256': hashlib.sha256(data).hexdigest(), 'files': [
        {'file': name, 'archive_member': 'source/' + name,
         'sha256': hashlib.sha256(content).hexdigest()} for name, content in files.items()]}
    return data, manifest, files


def test_only_pinned_members_are_saved_and_repeated_download_preserves_sources(tmp_path):
    data, manifest, expected = fixture_archive()
    outputs = dl.source_members(data, manifest)
    assert outputs == expected
    dl.save_sources(outputs, tmp_path)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    dl.save_sources(outputs, tmp_path)
    assert before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}


@pytest.mark.parametrize('target', ['archive', 'member'])
def test_hash_mismatch_refuses_source_bytes(target):
    data, manifest, _ = fixture_archive()
    if target == 'archive':
        manifest['archive_sha256'] = '0' * 64
    else:
        manifest['files'][0]['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='SHA-256'):
        dl.source_members(data, manifest)


def test_existing_different_file_prevents_writing_either_member(tmp_path):
    _, _, outputs = fixture_archive()
    (tmp_path / 'a.json').write_bytes(b'local change')
    with pytest.raises(ValueError, match='Refusing to replace'):
        dl.save_sources(outputs, tmp_path)
    assert not (tmp_path / 'a.jpg').exists()
    assert (tmp_path / 'a.json').read_bytes() == b'local change'


def test_manifest_cannot_escape_destination():
    data, manifest, _ = fixture_archive()
    manifest['files'][0]['file'] = '../outside.jpg'
    with pytest.raises(ValueError, match='plain filename'):
        dl.source_members(data, manifest)
