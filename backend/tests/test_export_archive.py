"""File-only archive regressions; no database, real dataset or model required."""
from hashlib import sha256
from io import BytesIO
import os
from threading import Event
import time
from zipfile import ZipFile

import pytest

from backend.app.core.export import ExportDependencyError, ExportLimitError, ExportNameError, ExportStopped
from backend.app.models.export import ExportPolicy, FrozenExport
from backend.app.services.export_archive import (
    REPORT_NAME, _BoundedOutput, build_archive, manifest_bytes, member_names, report_bytes,
)
from backend.app.services.export_files import create_attempt
from backend.tests.export_support import synthetic


def build(frozen, settings, *, token='b' * 32, stopped=None, progress=None):
    directory = create_attempt(settings.root, token)
    build_archive(frozen, settings=settings, directory=directory, stop_event=Event() if stopped is None else stopped,
                  deadline_end=time.monotonic() + 60, progress=lambda n: None if progress is None else progress(n))
    return directory


@pytest.mark.parametrize('images', [True, False])
def test_exact_bytes_order_and_manifest_agreement(tmp_path, images):
    frozen, settings, expected = synthetic(tmp_path, decisions=('accepted', 'rejected', 'pending_review'),
                                           selection='all_reviewed', include_images=images)
    recorded = []
    directory = build(frozen, settings, progress=recorded.append)
    with ZipFile(directory / 'archive.zip') as archive:
        expected_names = [name for _, _, name in member_names(frozen)] + ['manifest.json', 'README.txt', REPORT_NAME]
        assert archive.namelist() == expected_names
        for sample, role, name in member_names(frozen):
            assert archive.read(name) == expected[(sample.entry.sample_id, role)]
        assert archive.read('manifest.json') == (directory / 'manifest.json').read_bytes() == manifest_bytes(frozen)
        assert 'diagnostic' in archive.read('README.txt').decode()
        assert archive.read(REPORT_NAME) == report_bytes(frozen)
    assert recorded == [1, 2]
    assert not any('PNGImages/' in name for name in expected_names) if not images else True


def test_valid_high_valued_png_is_not_reencoded(tmp_path):
    from PIL import Image
    buffer = BytesIO()
    image = Image.new('I;16', (2, 1))
    image.putpixel((0, 0), 256)
    image.putpixel((1, 0), 65535)
    image.save(buffer, format='PNG')
    content = buffer.getvalue()
    frozen, settings, _ = synthetic(tmp_path)
    sample = frozen.samples[0]
    path = settings.asset_root / sample.manifest.raw_mask.storage_key
    path.write_bytes(content)
    document = frozen.model_dump(mode='json')
    document['samples'][0]['manifest']['raw_mask'].update(size_bytes=len(content), sha256=sha256(content).hexdigest())
    frozen = FrozenExport.model_validate_json(__import__('json').dumps(document))
    directory = build(frozen, settings)
    with ZipFile(directory / 'archive.zip') as archive:
        assert archive.read('accepted/PedMasks/sample_0_mask.png') == content


def test_zero_inclusions_produces_only_manifest_readme_and_report(tmp_path):
    frozen, settings, _ = synthetic(tmp_path, decisions=('pending_review', 'needs_correction'))
    directory = build(frozen, settings)
    with ZipFile(directory / 'archive.zip') as archive:
        assert archive.namelist() == ['manifest.json', 'README.txt', REPORT_NAME]
    assert len(frozen.manifest.entries) == 2


@pytest.mark.parametrize('ids', [['x', 'X'], ['CON'], ['com1.txt'], ['../x'], ['x/y'], ['x.'], ['x:y'], ['x\\y']])
def test_unsafe_or_alias_names_fail_whole_export(tmp_path, ids):
    frozen, settings, _ = synthetic(tmp_path, decisions=('accepted',) * len(ids), ids=ids)
    with pytest.raises(ExportNameError):
        build(frozen, settings)


@pytest.mark.parametrize('mutation', ['remove', 'digest', 'grow', 'symlink', 'hardlink'])
def test_available_source_failure_never_changes_membership(tmp_path, mutation):
    frozen, settings, _ = synthetic(tmp_path)
    path = settings.asset_root / frozen.samples[0].manifest.raw_mask.storage_key
    if mutation == 'remove':
        path.unlink()
    elif mutation == 'digest':
        content = path.read_bytes()
        path.write_bytes(bytes([content[0] ^ 1]) + content[1:])
    elif mutation == 'grow':
        path.write_bytes(path.read_bytes() + b'x')
    elif mutation == 'symlink':
        path.unlink()
        path.symlink_to(tmp_path / 'elsewhere')
    else:
        os.link(path, tmp_path / 'alias')
    with pytest.raises((ExportDependencyError, ExportLimitError)):
        build(frozen, settings)
    assert frozen.manifest.entries[0].included


@pytest.mark.parametrize('policy', [ExportPolicy(max_file_bytes=1), ExportPolicy(max_input_bytes=1),
                                   ExportPolicy(max_archive_bytes=1), ExportPolicy(max_metadata_bytes=1024)])
def test_measured_limits_fail_before_publication(tmp_path, policy):
    frozen, settings, _ = synthetic(tmp_path, decisions=('accepted',) * 4, policy=policy)
    with pytest.raises(ExportLimitError):
        build(frozen, settings)


def test_zip_output_bound_is_checked_before_write():
    output = BytesIO()
    sink = _BoundedOutput(output, 3, lambda: None)
    sink.write(b'abc')
    with pytest.raises(ExportLimitError):
        sink.write(b'd')
    assert output.getvalue() == b'abc'


def test_cancellation_during_copy_is_observed(tmp_path):
    frozen, settings, _ = synthetic(tmp_path, decisions=('accepted', 'accepted'))
    stopped = Event()
    with pytest.raises(ExportStopped):
        build(frozen, settings, stopped=stopped, progress=lambda n: stopped.set())


def test_repeat_build_has_stable_entry_metadata_and_bytes(tmp_path):
    frozen, settings, _ = synthetic(tmp_path)
    first = build(frozen, settings, token='b' * 32)
    second = build(frozen, settings, token='c' * 32)
    assert (first / 'archive.zip').read_bytes() == (second / 'archive.zip').read_bytes()


def test_cancellation_inside_source_read_aborts_the_current_member(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from backend.app.services import export_archive as module
    frozen, settings, _ = synthetic(tmp_path)
    stopped = Event()
    original = module.open_relative
    target = frozen.samples[0].manifest.annotation_text.storage_key
    reads = []
    @contextmanager
    def source(root, key):
        with original(root, key) as handle:
            if key != target:
                yield handle
                return
            class DuringRead:
                def fileno(self):
                    return handle.fileno()
                def read(self, count):
                    content = handle.read(count)
                    reads.append(len(content))
                    stopped.set()
                    return content
            yield DuringRead()
    monkeypatch.setattr(module, 'open_relative', source)
    with pytest.raises(ExportStopped):
        build(frozen, settings, stopped=stopped)
    assert reads and reads[0] > 0
