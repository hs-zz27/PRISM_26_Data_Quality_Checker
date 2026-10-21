"""Detection evidence through the batch engine and a spawned child. Needs POSIX file locking."""

import json
import multiprocessing
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fcntl", reason="the audit engine imports POSIX file locking, so these run on Linux, macOS or WSL")

from backend.app.models.audit import BatchAuditRequest  # noqa: E402
from backend.app.models.detection_evidence import DetectorConfiguration  # noqa: E402
from backend.app.services.audit import audit_sample  # noqa: E402
from backend.app.services.batch_audit import BatchAuditService  # noqa: E402
from backend.app.services.detection_audit import DetectionAwareCollector  # noqa: E402
from backend.app.services.sample_worker import run_collector_child  # noqa: E402
from backend.tests.test_detection_audit import (  # noqa: E402,F401 - shared helpers and autouse fixture
    configuration,
    fresh_detector_cache,
    write_sample,
)


def test_the_audit_api_modules_never_import_torch():
    code = (
        "import pickle, sys\n"
        "from backend.app.services.batch_audit import BatchAuditService\n"
        "from backend.app.services.detection_audit import DetectionAwareCollector\n"
        "from backend.app.models.detection_evidence import DetectorConfiguration\n"
        "pickle.loads(pickle.dumps(DetectionAwareCollector(DetectorConfiguration(enabled=False))))\n"
        "assert 'torch' not in sys.modules\n"
    )
    root = Path(__file__).resolve().parents[2]
    done = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def batch_service(**options):
    return BatchAuditService(rulebook_repository=None, audit_repository=None, sample_max_seconds=60, **options)


def request(**changes):
    values = dict(
        dataset_id="dataset_a", dataset_root="", rulebook_id="book", rulebook_version=1,
        sample_ids=["s1"], sample_revisions={"s1": 1}, source_mode="snapshot",
    )
    return BatchAuditRequest(**(values | changes))


def test_audits_without_a_frozen_configuration_keep_the_plain_collector():
    service = batch_service()
    assert service._collector_for(request()) is audit_sample


def test_an_audit_with_a_frozen_configuration_wraps_the_collector_it_was_given():
    injected = len  # any callable works as an injected collector
    service = batch_service(measurement_collector=injected)
    collector = service._collector_for(request(detector=configuration()))
    assert isinstance(collector, DetectionAwareCollector) and collector.base_collector is injected
    assert collector.configuration == configuration()


def test_a_disabled_frozen_configuration_still_goes_through_the_wrapper():
    collector = batch_service()._collector_for(request(detector=DetectorConfiguration(enabled=False)))
    assert isinstance(collector, DetectionAwareCollector) and collector.base_collector is audit_sample


def test_detection_runs_inside_a_spawned_child_and_returns_plain_measurements(tmp_path):
    root = write_sample(tmp_path)
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(
        target=run_collector_child,
        args=(send, DetectionAwareCollector(configuration()), str(root), "s1", None, 8),
        daemon=True,
    )
    process.start()
    send.close()
    chunks = []
    try:
        while receive.poll(120):
            chunk = receive.recv_bytes()
            if not chunk:
                break
            chunks.append(chunk)
        else:
            pytest.fail("the child process sent no result")
    finally:
        process.join(timeout=30)
        receive.close()
    message = pickle.loads(b"".join(chunks))
    assert message[0] == "success"
    document = message[1]
    # No checkpoint is configured in the child, so detection is honestly unavailable.
    assert document["detection_evidence"]["status"] == "detector_unavailable"
    assert document["status"] == "incomplete" and document["checks"]  # other measurements survive
    json.dumps(document, allow_nan=False)
