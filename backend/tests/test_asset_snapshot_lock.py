"""The sample capture lock on POSIX and Windows. Both code paths run on every platform via fakes."""

import errno
import multiprocessing
import time
from pathlib import Path

import pytest

from backend.app.services import asset_snapshot as snapshot

BUSY = getattr(errno, "EDEADLOCK", errno.EDEADLK)


class FakeStream:
    def __init__(self):
        self.events = []

    def fileno(self):
        return 7

    def seek(self, position):
        self.events.append(("seek", position))


class FakeFcntl:
    LOCK_EX = 2

    def __init__(self):
        self.calls = []

    def flock(self, descriptor, operation):
        self.calls.append((descriptor, operation))


class FakeMsvcrt:
    LK_LOCK, LK_UNLCK = 1, 0

    def __init__(self, failures=()):
        self.failures = list(failures)
        self.calls = []

    def locking(self, descriptor, mode, length):
        self.calls.append((descriptor, mode, length))
        if mode == self.LK_LOCK and self.failures:
            raise OSError(self.failures.pop(0), "lock failure")


# --- POSIX path: behavior is unchanged -----------------------------------------------------------

def test_posix_takes_an_exclusive_flock_and_needs_no_explicit_unlock(monkeypatch):
    fake = FakeFcntl()
    monkeypatch.setattr(snapshot, "fcntl", fake)
    monkeypatch.setattr(snapshot, "msvcrt", None)
    stream = FakeStream()
    snapshot._lock_exclusively(stream)
    snapshot._unlock(stream)
    assert fake.calls == [(7, 2)] and stream.events == []


# --- Windows path ----------------------------------------------------------------------------------

@pytest.fixture
def windows(monkeypatch):
    def install(failures=()):
        fake = FakeMsvcrt(failures)
        monkeypatch.setattr(snapshot, "fcntl", None)
        monkeypatch.setattr(snapshot, "msvcrt", fake)
        return fake

    return install


def test_windows_keeps_waiting_while_another_process_holds_the_lock(windows):
    fake = windows([BUSY, BUSY])
    stream = FakeStream()
    snapshot._lock_exclusively(stream)
    assert fake.calls == [(7, 1, 1)] * 3  # two busy answers, then the lock
    assert stream.events == [("seek", 0)] * 3  # always the same first byte


@pytest.mark.parametrize("code", [errno.EBADF, errno.EINVAL, errno.EACCES])
def test_windows_does_not_retry_real_failures(windows, code):
    fake = windows([code])
    with pytest.raises(OSError) as info:
        snapshot._lock_exclusively(FakeStream())
    assert info.value.errno == code and len(fake.calls) == 1


def test_windows_releases_the_lock_promptly_and_tolerates_a_failed_release(windows):
    fake = windows()
    stream = FakeStream()
    snapshot._unlock(stream)
    assert fake.calls == [(7, 0, 1)]

    class Failing(FakeMsvcrt):
        def locking(self, descriptor, mode, length):
            raise OSError(errno.EACCES, "already unlocked")

    snapshot.msvcrt = Failing()
    snapshot._unlock(FakeStream())  # must not raise; closing the file releases the lock anyway


# --- the context manager ------------------------------------------------------------------------------

def test_a_failed_acquisition_is_reported_as_a_storage_error(tmp_path, monkeypatch):
    def fail(stream):
        raise OSError(errno.EBADF, "bad")

    monkeypatch.setattr(snapshot, "_lock_exclusively", fail)
    with pytest.raises(snapshot.AssetSnapshotStorageError, match="could not be acquired"):
        with snapshot._capture_lock(tmp_path, "dataset", "sample", 1):
            pytest.fail("the body must not run without the lock")


def test_the_lock_is_released_even_when_the_body_raises(tmp_path, monkeypatch):
    released = []
    monkeypatch.setattr(snapshot, "_lock_exclusively", lambda stream: None)
    monkeypatch.setattr(snapshot, "_unlock", lambda stream: released.append(True))
    with pytest.raises(ValueError, match="body failed"):
        with snapshot._capture_lock(tmp_path, "dataset", "sample", 1):
            raise ValueError("body failed")
    assert released == [True]


# --- the real lock between two real processes -------------------------------------------------------------

def _hold_lock(root, ready, seconds):
    from backend.app.services import asset_snapshot

    with asset_snapshot._capture_lock(Path(root), "dataset", "sample", 1):
        Path(ready).write_text("locked", encoding="utf-8")
        time.sleep(seconds)


def test_a_second_process_waits_for_the_first_to_release_the_lock(tmp_path):
    """Uses the real operating-system lock; this is the check that matters on each platform."""
    ready = tmp_path / "ready"
    process = multiprocessing.get_context("spawn").Process(
        target=_hold_lock, args=(str(tmp_path), str(ready), 2.0), daemon=True,
    )
    process.start()
    try:
        deadline = time.monotonic() + 60
        while not ready.exists():
            assert time.monotonic() < deadline, "the first process never took the lock"
            time.sleep(0.05)
        started = time.monotonic()
        with snapshot._capture_lock(tmp_path, "dataset", "sample", 1):
            waited = time.monotonic() - started
    finally:
        process.join(timeout=30)
    assert waited >= 1.0, f"the second process did not wait for the lock (waited {waited:.2f}s)"
