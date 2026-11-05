"""Reviewed-export spreadsheet and decision folders. Offline unless PRISM_TEST_DATABASE_URL is set."""
import csv
from datetime import UTC, datetime
from io import StringIO
import json
from threading import Event
import time
from zipfile import ZipFile

from PIL import Image
import pytest
from pydantic import ValidationError

from backend.app.core.export import ExportDependencyError
from backend.app.datasets.json_zip import unpack_archive
from backend.app.models.export import FrozenExport, ReportCorrection, ReportIssue, SampleReport
from backend.app.models.review import SampleReviewDecision
from backend.app.services.export_archive import (
    REPORT_COLUMNS, REPORT_NAME, build_archive, decision_folder, member_names, report_bytes,
)
from backend.app.services.export_files import create_attempt
from backend.app.storage import export_repository as repository_module
from backend.tests.dataset_fixtures import (  # noqa: F401 - pytest fixtures used by the opt-in test
    dataset_database_settings, empty_dataset_database_settings,
)
from backend.tests.dataset_import_fakes import staged, zip_bytes
from backend.tests.export_support import synthetic


def rows(frozen):
    content = report_bytes(frozen)
    assert content.startswith('﻿'.encode('utf-8'))       # Excel detects UTF-8
    text = content.decode('utf-8')[1:]
    assert '\r\n' in text and '\n' not in text.replace('\r\n', '')  # CRLF rows only
    table = list(csv.reader(StringIO(text, newline='')))
    assert tuple(table[0]) == REPORT_COLUMNS
    return [dict(zip(REPORT_COLUMNS, row)) for row in table[1:]]


def with_reports(frozen, reports):
    samples = [s.model_copy(update={'report': r}) for s, r in zip(frozen.samples, reports)]
    return FrozenExport(manifest=frozen.manifest, samples=samples, policy=frozen.policy)


def report(*, issues=(), passed=0, omitted=0, comment='', correction=None):
    counts = {'fail': 0, 'unsure': 0, 'unavailable': 0}
    for issue in issues:
        counts[issue.outcome] += 1
    counts['fail'] += omitted  # omitted issues are failures in these fixtures
    return SampleReport(pass_count=passed, fail_count=counts['fail'], unsure_count=counts['unsure'],
                        unavailable_count=counts['unavailable'], issues=list(issues),
                        issues_omitted=omitted, review_comment=comment, correction=correction)


def test_folders_follow_only_the_frozen_human_decision(tmp_path):
    frozen, _, _ = synthetic(tmp_path, decisions=('accepted', 'rejected', 'pending_review', 'needs_correction'),
                             selection='all_reviewed')
    names = [(s.entry.sample_id, name) for s, _, name in member_names(frozen)]
    assert {n.split('/')[0] for _, n in names} == {'accepted', 'rejected'}
    assert all(n.startswith('accepted/') for sid, n in names if sid == 'sample_0')
    assert all(n.startswith('rejected/') for sid, n in names if sid == 'sample_1')
    assert not any(sid in {'sample_2', 'sample_3'} for sid, _ in names)
    assert ('sample_0', 'accepted/PNGImages/sample_0.png') in names
    assert ('sample_1', 'rejected/Annotation/sample_1.txt') in names


def test_accepted_only_never_creates_a_rejected_folder(tmp_path):
    frozen, _, _ = synthetic(tmp_path, decisions=('accepted', 'rejected'), selection='accepted_only')
    assert {name.split('/')[0] for _, _, name in member_names(frozen)} == {'accepted'}


def test_an_included_sample_without_a_final_decision_is_refused(tmp_path):
    frozen, _, _ = synthetic(tmp_path)
    # Unreachable through the selection policy; built without validation on purpose.
    undecided = frozen.samples[0].entry.model_copy(update={'review_decision': SampleReviewDecision.PENDING_REVIEW})
    with pytest.raises(ExportDependencyError):
        decision_folder(undecided)


def test_one_row_per_audited_sample_including_excluded_ones(tmp_path):
    frozen, _, _ = synthetic(tmp_path, decisions=('accepted', 'rejected', 'pending_review', 'needs_correction'),
                             selection='accepted_only')
    table = rows(frozen)
    assert [r['sample_id'] for r in table] == ['sample_0', 'sample_1', 'sample_2', 'sample_3']
    assert [r['review_decision'] for r in table] == ['accepted', 'rejected', 'pending_review', 'needs_correction']
    assert [r['exported'] for r in table] == ['yes', 'no', 'no', 'no']
    assert [r['export_folder'] for r in table] == ['accepted/', '', '', '']
    assert [r['exclusion_reason'] for r in table] == ['', 'rejected', 'pending_review', 'needs_correction']
    assert table[0]['result_audit_id'] == 'audit_demo'   # historical export: its own audit
    assert table[0]['reviewed_at'] == '2026-10-21T00:00:00+00:00'
    assert table[2]['reviewer_id'] == 'reviewer_demo'


def test_missing_report_details_are_stated_never_invented(tmp_path):
    frozen, _, _ = synthetic(tmp_path)
    (row,) = rows(frozen)
    assert row['findings_recorded'] == 'no'
    assert row['errors_found'] == 'Not recorded for this export.'
    assert all(row[c] == '' for c in ('pass', 'fail', 'unsure', 'unavailable', 'review_comment',
                                       'corrected_from_revision', 'correction_reviewer', 'correction_reason'))


def test_frozen_findings_comment_and_correction_are_reported(tmp_path):
    frozen, _, _ = synthetic(tmp_path, decisions=('accepted', 'rejected'), selection='all_reviewed')
    issues = [ReportIssue(rule_id='RULE-1', object_id=2, outcome='fail', resolution='accepted', reason='Box misses feet.'),
              ReportIssue(rule_id='RULE-2', object_id=None, outcome='unsure', resolution='pending')]
    frozen = with_reports(frozen, [
        report(issues=issues, passed=3, omitted=4, comment='Feet cut off; fixed in revision 2.',
               correction=ReportCorrection(parent_revision=1, reviewer_id='anna', reason='Extend box to feet')),
        report(passed=5),
    ])
    first, second = rows(frozen)
    assert (first['findings_recorded'], first['pass'], first['fail'], first['unsure'], first['unavailable']) == \
        ('yes', '3', '5', '1', '0')
    assert first['errors_found'] == ('RULE-1 object 2: fail (reviewer agreed) - Box misses feet. | '
                                     'RULE-2 whole sample: unsure (not reviewed) | +4 more not listed')
    assert first['review_comment'] == 'Feet cut off; fixed in revision 2.'
    assert (first['corrected_from_revision'], first['correction_reviewer'], first['correction_reason']) == \
        ('1', 'anna', 'Extend box to feet')
    assert second['export_folder'] == 'rejected/' and second['errors_found'] == '' and second['fail'] == '0'


@pytest.mark.parametrize('text', ['=HYPERLINK("http://x")', '+1', '-1+2', '@SUM(A1)', '\tx', '\rx'])
def test_formula_like_cells_are_neutralised(tmp_path, text):
    frozen, _, _ = synthetic(tmp_path)
    issue = ReportIssue(rule_id='@RULE', object_id=-1, outcome='fail', resolution='rejected', reason=None)
    frozen = with_reports(frozen, [report(issues=[issue], comment=text)])
    (row,) = rows(frozen)
    assert row['review_comment'] == "'" + text
    assert row['errors_found'] == "'@RULE object -1: fail (reviewer overrode)"


def test_report_is_byte_stable_and_unicode_safe(tmp_path):
    frozen, _, _ = synthetic(tmp_path)
    frozen = with_reports(frozen, [report(comment='Füße fehlen — 足', passed=1)])
    assert report_bytes(frozen) == report_bytes(frozen)
    assert rows(frozen)[0]['review_comment'] == 'Füße fehlen — 足'


def test_archive_contains_report_and_folders(tmp_path):
    frozen, settings, expected = synthetic(tmp_path, decisions=('accepted', 'rejected'), selection='all_reviewed')
    directory = create_attempt(settings.root, 'd' * 32)
    build_archive(frozen, settings=settings, directory=directory, stop_event=Event(),
                  deadline_end=time.monotonic() + 60, progress=lambda _: None)
    with ZipFile(directory / 'archive.zip') as archive:
        names = archive.namelist()
        assert names[-3:] == ['manifest.json', 'README.txt', REPORT_NAME]
        assert archive.read(REPORT_NAME) == report_bytes(frozen)
        assert archive.read('rejected/Annotation/sample_1.txt') == expected[('sample_1', 'annotation_text')]
        assert 'accepted/' in archive.read('README.txt').decode()
    assert not (directory / REPORT_NAME).exists()   # Only manifest.json is a separate artifact.


def test_sample_report_must_account_for_every_non_passing_finding():
    issue = ReportIssue(rule_id='R', outcome='fail', resolution='pending')
    with pytest.raises(ValidationError):
        SampleReport(pass_count=0, fail_count=2, unsure_count=0, unavailable_count=0,
                     issues=[issue], issues_omitted=0, review_comment='')
    with pytest.raises(ValidationError):
        SampleReport(pass_count=0, fail_count=6, unsure_count=0, unavailable_count=0,
                     issues=[issue] * 6, issues_omitted=0, review_comment='')
    with pytest.raises(ValidationError):
        ReportIssue(rule_id='R', outcome='pass', resolution='pending')


def test_frozen_inputs_saved_before_reports_still_load(tmp_path):
    frozen, _, _ = synthetic(tmp_path)
    document = frozen.model_dump(mode='json')
    for sample in document['samples']:
        sample.pop('report')
    loaded = FrozenExport.model_validate_json(json.dumps(document))
    assert loaded == frozen and loaded.samples[0].report is None


def test_clip_marks_every_shortening():
    clip = repository_module._clip
    assert clip(None, 10) is None
    assert clip('  a\n\tb  ', 10) == 'a b'
    assert clip('x' * 12, 10) == 'x' * 9 + '…' and len(clip('x' * 12, 10)) == 10
    assert clip('short', 10, cut=True) == 'short…'
    assert clip('x' * 10, 10, cut=True) == 'x' * 9 + '…'


class _Rows:
    def __init__(self, value):
        self.value = value

    def fetchall(self):
        return self.value


class _FindingsConnection:
    def __init__(self, findings):
        self.findings, self.calls = findings, []

    def execute(self, sql, parameters=()):
        assert 'FROM findings' in sql
        self.calls.append(parameters)
        return _Rows(self.findings)


def finding(audit, sample, outcome, rule='R', obj=None, resolution='pending', reason=None, cut=False, revision=2):
    return {'audit_id': audit, 'sample_id': sample, 'sample_revision': revision, 'rule_id': rule,
            'object_id': obj, 'outcome': outcome, 'resolution': resolution, 'reason': reason,
            'reason_cut': cut if reason is not None else None}


def test_reports_use_only_the_exact_result_pin_and_order_errors_first():
    pins = [{'audit_id': 'audit_child', 'sample_id': 's1', 'sample_revision': 2},
            {'audit_id': 'audit_root', 'sample_id': 's2', 'sample_revision': 2}]
    findings = [
        finding('audit_child', 's1', 'unavailable', rule='R0'),
        finding('audit_child', 's1', 'pass', rule='R1'),
        finding('audit_child', 's1', 'unsure', rule='R2', reason='Hidden  limb\nunclear'),
        *[finding('audit_child', 's1', 'fail', rule=f'R3', obj=i, resolution='accepted', reason='cut', cut=True)
          for i in range(5)],
        # Older family attempt of s1 and a different revision: never this export's results.
        finding('audit_root', 's1', 'fail', rule='OLD'),
        finding('audit_child', 's1', 'fail', rule='OLD', revision=1),
        finding('audit_root', 's2', 'pass'),
    ]
    connection = _FindingsConnection(findings)
    reviews = [repository_module._ReviewPin(audit_id='audit_child', sample_id='s1', sample_revision=2, revision=1,
                                            decision='rejected', reviewer_id='r', comment='Needs  another\nlook',
                                            updated_at=datetime(2026, 11, 5, tzinfo=UTC)), None]
    provenance = [{'parent_revision': 1, 'reviewer_id': 'anna', 'reason': 'Moved polygon back'}, None]
    first, second = repository_module._reports(connection, pins, reviews, provenance)
    assert connection.calls[0][2] == ['audit_child', 'audit_root']
    assert (first.pass_count, first.fail_count, first.unsure_count, first.unavailable_count) == (1, 5, 1, 1)
    assert [i.outcome for i in first.issues] == ['fail'] * 5
    assert [i.object_id for i in first.issues] == [0, 1, 2, 3, 4]
    assert first.issues[0].reason == 'cut…' and first.issues[0].resolution == 'accepted'
    assert first.issues_omitted == 2
    assert first.review_comment == 'Needs another look'
    assert first.correction == ReportCorrection(parent_revision=1, reviewer_id='anna', reason='Moved polygon back')
    assert (second.pass_count, second.issues, second.review_comment, second.correction) == (1, [], '', None)


def test_no_samples_makes_no_findings_query():
    connection = _FindingsConnection([])
    assert repository_module._reports(connection, [], [], []) == []
    assert connection.calls == []


def labelme_zip(*extras):
    from io import BytesIO
    from backend.tests.test_json_dataset_workflow import documents
    image = BytesIO()
    Image.new('RGB', (20, 16), 'green').save(image, format='JPEG')
    document = {**documents('labelme'), 'imagePath': 'image.jpg'}
    return zip_bytes([('accepted/data/s/annotations.json', json.dumps(document).encode()),
                      ('accepted/data/s/image.jpg', image.getvalue()), *extras])


@pytest.mark.parametrize('extra,flagged', [
    (('review_report.csv', b'a,b\r\n'), False),
    (('accepted/review_report.csv', b'a,b\r\n'), True),
    (('other.csv', b'a,b\r\n'), True),
])
def test_json_import_skips_only_the_exact_export_report(tmp_path, extra, flagged):
    path, request, _ = staged(tmp_path, labelme_zip(extra))
    request = request.model_copy(update={'format': 'labelme'})
    samples, unsupported = unpack_archive(path, tmp_path / 'extracted', request)
    assert len(samples) == 1 and unsupported is flagged


def test_opt_in_postgresql_report_is_frozen_with_the_decision(dataset_database_settings, tmp_path):
    """Authored for the owner's PostgreSQL run; skipped without PRISM_TEST_DATABASE_URL."""
    from backend.app.models.export import ExportPolicy
    from backend.app.models.finding import Finding, FindingReview
    from backend.app.storage import FindingRepository
    from backend.app.storage.export_repository import ExportRepository
    from backend.tests.test_export_repository import request, seed
    settings = dataset_database_settings
    audit, _ = seed(settings, tmp_path, decisions=('accepted', 'rejected'))
    findings = FindingRepository(settings=settings)
    common = dict(audit_id=audit.audit_id, sample_revision=2, rulebook_id='rulebook_demo', rulebook_version=3,
                  description='Box encloses the visible person.', origin='reviewer')
    saved = findings.save_findings([
        Finding(finding_id='f_fail', sample_id='sample_0', object_id=1, rule_id='RULE-1', outcome='fail',
                reason='The box misses the feet.', **common),
        Finding(finding_id='f_pass', sample_id='sample_0', object_id=2, rule_id='RULE-1', outcome='pass', **common),
        Finding(finding_id='f_unsure', sample_id='sample_1', object_id=1, rule_id='RULE-2', outcome='unsure', **common),
    ])
    assert len(saved) == 3
    now = datetime.now(UTC)
    findings.save_finding_review('f_fail', FindingReview(revision=1, resolution='accepted', reviewer_id='anna',
                                                         comment='Agree', updated_at=now), expected_review_version=0)
    repository = ExportRepository(settings=settings)
    started = repository.enqueue(request(audit, selection='all_reviewed'), policy=ExportPolicy())
    frozen = repository.read_frozen(started.export.export_id)
    first, second = (sample.report for sample in frozen.samples)
    assert (first.pass_count, first.fail_count, first.unsure_count, first.unavailable_count) == (1, 1, 0, 0)
    assert first.issues == [ReportIssue(rule_id='RULE-1', object_id=1, outcome='fail', resolution='accepted',
                                        reason='The box misses the feet.')]
    assert first.review_comment == 'Synthetic review.' and first.correction is None
    assert second.issues[0].resolution == 'pending' and second.unsure_count == 1
    # A later human decision never rewrites an existing export's frozen report.
    findings.save_finding_review('f_fail', FindingReview(revision=2, resolution='rejected', reviewer_id='anna',
                                                         comment='Changed mind', updated_at=now), expected_review_version=1)
    assert repository.read_frozen(started.export.export_id) == frozen
