"""Single-attempt parent supervision; no database access in the spawned child.

One atomic bounded message file is acknowledged by unlinking. The parent drains
while the child runs. No pipe frame reads, unbounded queues or join-before-read.
"""

import json
import multiprocessing
import os
from pathlib import Path
import shutil
import stat
import threading
import time
from uuid import uuid4

from ..core.dataset_import import (
    ERROR_MESSAGES, ImportInputError, ImportOutcomeUnresolved, ImportResourceError,
    ImportSettings, ImportStorageError,
)
from ..models.dataset_import import ImportClaim, ImportRequest, SamplePublication
from ..storage.dataset_repository import (
    DatasetCommitOutcomeUnknownError, DatasetImportDeadlineError, DatasetStorageError,
)
from ..storage.workflow_repository import JobCancellationError, JobClaimLostError
from .dataset_upload import private_directory


def _emit(directory: Path, payload: dict, limit: int):
    encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > limit:
        raise ImportResourceError()
    temporary, message = directory / "message.part", directory / "message.json"
    with temporary.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, message)
    while message.exists():
        time.sleep(0.02)  # Parent/watchdog can terminate this process at every boundary.


def _child(request_json: str, settings_json: str, directory_string: str, parent_pid: int, deadline_end: float):
    # Process termination isolation, not a security sandbox. No DB object is passed.
    for key in tuple(os.environ):
        if key == "DATABASE_URL" or key == "PRISM_TEST_DATABASE_URL" or key.startswith(("POSTGRES_", "PG")):
            os.environ.pop(key, None)
    def orphan_bound():
        while True:
            if os.getppid() != parent_pid or time.monotonic() >= deadline_end:
                os._exit(124)
            time.sleep(0.05)
    threading.Thread(target=orphan_bound, name="dataset-import-child-bound", daemon=True).start()
    request = ImportRequest.model_validate_json(request_json)
    settings = ImportSettings.model_validate_json(settings_json)
    directory = Path(directory_string)
    try:
        # Local import keeps worker module import free from PNG/NumPy startup cost.
        if request.format == "penn_fudan":
            from ..datasets.penn_fudan_zip import prepare_sample, unpack_archive
        else:
            from ..datasets.json_zip import prepare_sample, unpack_archive
        source = directory / "extracted"
        ids, unsupported = unpack_archive(settings.root / request.archive_key, source, request)
        _emit(directory, {"kind": "discovery", "total": len(ids), "unsupported": unsupported}, request.policy.max_result_bytes)
        for ordinal, sample_id in enumerate(ids):
            prepared = prepare_sample(request.dataset_id, sample_id, ordinal, source_root=source,
                                      asset_root=settings.asset_root, policy=request.policy)
            _emit(directory, {"kind": "sample", "publication": prepared.model_dump(mode="json")},
                  request.policy.max_result_bytes)
        _emit(directory, {"kind": "done"}, request.policy.max_result_bytes)
    except BaseException as error:
        # Deliberately never serialize exception strings, source paths or identities.
        if isinstance(error, ImportInputError):
            code = error.code
        elif isinstance(error, ImportResourceError):
            code = "IMPORT_RESOURCE_LIMIT"
        elif isinstance(error, (ImportStorageError, OSError)) or type(error).__name__ in {
            "AssetSnapshotStorageError", "AssetIntegrityError", "AssetReadLimitError", "AssetStorageError",
        }:
            code = "IMPORT_STORAGE_FAILED"
        else:
            code = "IMPORT_FAILED"
        (directory / "message.part").unlink(missing_ok=True)
        _emit(directory, {"kind": "error", "code": code}, request.policy.max_result_bytes)


def _read_message(directory: Path, limit: int):
    path = directory / "message.json"
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > limit:
            raise ImportResourceError()
        content = handle.read(limit + 1)
    if len(content) > limit:
        raise ImportResourceError()
    try:
        def unique_pairs(pairs):
            output = {}
            for key, value in pairs:
                if key in output:
                    raise ValueError("Duplicate result key.")
                output[key] = value
            return output
        def reject_constant(value):
            raise ValueError("Non-finite result.")
        result = json.loads(content, object_pairs_hook=unique_pairs, parse_constant=reject_constant)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError("Invalid worker result document.") from error
    if type(result) is not dict:
        raise ValueError("Invalid worker result envelope.")
    return result


def _stop(process, lock):
    with lock:
        if process.is_alive():
            process.terminate()
            process.join(timeout=1)
        if process.is_alive():
            process.kill()
            process.join(timeout=1)


class _Watchdog:
    """Independent local bound, including while the parent waits for PostgreSQL."""
    def __init__(self, process, *, lease_end, deadline_end):
        self.process, self.lease_end, self.deadline_end = process, lease_end, deadline_end
        self.lock, self.stop_event, self.fired = threading.Lock(), threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self._run, name="dataset-import-watchdog", daemon=True)

    def _run(self):
        while not self.stop_event.wait(0.02):
            if time.monotonic() >= min(self.lease_end, self.deadline_end):
                self.fired.set()
                _stop(self.process, self.lock)
                return

    def acknowledge(self, began, acknowledgement):
        # Base on call START: DB/commit latency must never overextend local permission.
        with self.lock:
            self.lease_end = began + acknowledgement.lease_remaining
            self.deadline_end = min(self.deadline_end, began + acknowledgement.deadline_remaining)

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=3)
        _stop(self.process, self.lock)


def run_claimed_import(repository, claim: ImportClaim, *, settings: ImportSettings,
                       stop_event=None, context=None):
    """Run exactly one persisted attempt. No automatic computation/SQL replay."""
    claim = ImportClaim.model_validate(claim.model_dump())
    settings = ImportSettings.model_validate(settings.model_dump())
    # The ledger's policy, not a changed process configuration, governs accepted work.
    policy = claim.request.policy
    if stop_event is None:
        stop_event = threading.Event()
    context = multiprocessing.get_context("spawn") if context is None else context
    directory, process, watchdog = None, None, None
    try:
        # A fresh acknowledged heartbeat precedes any child start, including after
        # slow claim commit or process startup. Conservatively account for latency.
        began = time.monotonic()
        acknowledgement = repository.heartbeat(claim)
        if acknowledgement.job.status.value not in {"running", "cancelling"}:
            return acknowledgement.job
        lease_end = began + acknowledgement.lease_remaining
        deadline_end = began + acknowledgement.deadline_remaining
        private_directory(settings.root)
        private_directory(settings.root / "runs")
        private_directory(settings.asset_root)
        directory = settings.root / "runs" / uuid4().hex
        directory.mkdir(mode=0o700)
        process = context.Process(target=_child, args=(claim.request.model_dump_json(),
                                                     settings.model_dump_json(), str(directory), os.getpid(), deadline_end))
        process.start()
        watchdog = _Watchdog(process, lease_end=lease_end, deadline_end=deadline_end)
        watchdog.thread.start()
        next_renew, discovered, published, done, error_code = began + policy.renew_seconds, None, 0, False, None
        while True:
            now = time.monotonic()
            if stop_event.is_set():
                error_code = "IMPORT_INTERRUPTED"
                break
            if watchdog.fired.is_set() or now >= min(watchdog.lease_end, watchdog.deadline_end):
                error_code = "IMPORT_TIMEOUT" if now >= watchdog.deadline_end else "IMPORT_INTERRUPTED"
                break
            if now >= next_renew:
                began = time.monotonic()
                acknowledgement = repository.heartbeat(claim)
                if acknowledgement.job.status.value not in {"running", "cancelling"}:
                    return acknowledgement.job
                watchdog.acknowledge(began, acknowledgement)
                next_renew = began + policy.renew_seconds
            message = _read_message(directory, policy.max_result_bytes)
            if message is not None:
                kind = message.get("kind")
                if done or error_code is not None:
                    raise ValueError("Result after terminal message.")
                if kind == "discovery" and set(message) == {"kind", "total", "unsupported"} and discovered is None:
                    if type(message["total"]) is not int or not 1 <= message["total"] <= policy.max_samples or type(message["unsupported"]) is not bool:
                        raise ValueError("Invalid discovery result.")
                    repository.discover(claim, message["total"], unsupported=message["unsupported"])
                    discovered = message["total"]
                elif kind == "sample" and set(message) == {"kind", "publication"} and discovered is not None and published < discovered:
                    prepared = SamplePublication.model_validate_json(json.dumps(message["publication"], allow_nan=False))
                    if prepared.ordinal != published or prepared.sample.dataset_id != claim.request.dataset_id:
                        raise ValueError("Out-of-order or foreign publication.")
                    repository._publish_import_sample(claim, prepared)
                    repository.record_progress(claim)
                    published += 1
                elif kind == "done" and set(message) == {"kind"} and discovered is not None and published == discovered:
                    done = True
                elif kind == "error" and set(message) == {"kind", "code"} and message["code"] in ERROR_MESSAGES:
                    error_code = message["code"]
                else:
                    raise ValueError("Invalid import result sequence.")
                (directory / "message.json").unlink()  # Ack only AFTER acknowledged SQL boundary.
                if error_code is not None:
                    break
            if not process.is_alive():
                # A message could have arrived between the read and exit check.
                if (directory / "message.json").exists():
                    continue
                if not done or process.exitcode != 0:
                    error_code = "IMPORT_INTERRUPTED"
                break
            time.sleep(policy.poll_seconds)
        _stop(process, watchdog.lock)
        return repository.finish(claim, code=error_code)
    except DatasetCommitOutcomeUnknownError as error:
        raise ImportOutcomeUnresolved("Import commit requires operator reconciliation; do not replay.") from error
    except JobClaimLostError:
        return None  # A foreign/expired owner never terminalizes; stale recovery owns it.
    except (JobCancellationError, DatasetImportDeadlineError):
        # finish() locks and rechecks: cancellation takes precedence over timeout.
        if process is not None and watchdog is not None:
            _stop(process, watchdog.lock)
        try:
            return repository.finish(claim, code="IMPORT_TIMEOUT")
        except DatasetCommitOutcomeUnknownError as error:
            raise ImportOutcomeUnresolved("Import terminal commit requires reconciliation.") from error
        except JobClaimLostError:
            return None
    except DatasetStorageError:
        raise  # Stop; no blind replay or speculative terminal transaction after DB loss.
    except Exception as error:
        if process is not None:
            _stop(process, watchdog.lock if watchdog else threading.Lock())
        code = "IMPORT_RESOURCE_LIMIT" if isinstance(error, ImportResourceError) else "IMPORT_STORAGE_FAILED" if isinstance(error, OSError) else "IMPORT_FAILED"
        try:
            return repository.finish(claim, code=code)
        except DatasetCommitOutcomeUnknownError as unresolved:
            raise ImportOutcomeUnresolved("Import terminal commit requires reconciliation.") from unresolved
        except JobClaimLostError:
            return None
    finally:
        if watchdog is not None:
            watchdog.close()
        elif process is not None and process.pid is not None:
            _stop(process, threading.Lock())
        # Only this generated scratch directory. NEVER archives, registered files,
        # .locks, snapshots, or another attempt. Unregistered snapshot orphans remain.
        if directory is not None and (process is None or not process.is_alive()):
            try:
                shutil.rmtree(directory)
            except OSError:
                pass
