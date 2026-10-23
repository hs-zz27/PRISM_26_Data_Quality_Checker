"""Audit submission freezes the detector settings. Needs POSIX file locking via the audit engine."""

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fcntl", reason="the audit engine imports POSIX file locking, so these run on Linux, macOS or WSL")

from backend.app.core.person_detection import DetectionConfigurationError  # noqa: E402
from backend.app.models.audit_submission import AuditRequest  # noqa: E402
from backend.app.models.detection_evidence import DetectorConfiguration  # noqa: E402
from backend.tests.test_audit_submission import submission  # noqa: E402,F401 - reuse its fixture
from backend.tests.test_detection_configuration import (  # noqa: E402,F401 - shared helpers and fixture
    FULL_SETTINGS,
    clean_environment,
    enabled_configuration,
)


def test_submission_freezes_the_configuration_given_at_acceptance(submission):  # noqa: F811
    service, _, _, audits, _ = submission
    configuration = enabled_configuration()
    service.detector_configuration = lambda: configuration
    service.start(AuditRequest(dataset_id="dataset_a", rulebook_id="book", rulebook_version=1))
    assert audits.start_audit.call_args.args[0].detector == configuration


def test_submission_reads_the_environment_when_each_audit_is_accepted(submission, clean_environment):  # noqa: F811
    service, _, _, audits, _ = submission
    clean_environment.setenv("DETECTION_ENABLED", "false")
    service.start(AuditRequest(dataset_id="dataset_a", rulebook_id="book", rulebook_version=1))
    first = audits.start_audit.call_args.args[0]
    assert first.detector == DetectorConfiguration(enabled=False)

    for name, value in FULL_SETTINGS.items():
        clean_environment.setenv(name, value)
    service.start(AuditRequest(dataset_id="dataset_a", rulebook_id="book", rulebook_version=1))
    second = audits.start_audit.call_args.args[0]
    assert second.detector.enabled is True and second.detector.person_labels == ["PASperson", "person"]
    # The audit accepted earlier keeps what it froze.
    assert first.detector == DetectorConfiguration(enabled=False)


def test_invalid_settings_stop_an_audit_before_anything_is_queued(submission, clean_environment):  # noqa: F811
    service, _, _, audits, _ = submission
    clean_environment.setenv("DETECTION_ENABLED", "true")
    with pytest.raises(DetectionConfigurationError):
        service.start(AuditRequest(dataset_id="dataset_a", rulebook_id="book", rulebook_version=1))
    audits.start_audit.assert_not_called()


def test_importing_audit_submission_does_not_import_torch():
    code = (
        "import sys\n"
        "import backend.app.services.audit_submission\n"
        "import backend.app.services.batch_audit\n"
        "assert 'torch' not in sys.modules, 'torch was imported by the audit API modules'\n"
    )
    root = Path(__file__).resolve().parents[2]
    done = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
