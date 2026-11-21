"""Matching saved camera captures to OPS shots (shared by the in-process and child-process camera)."""

import threading
import time
from pathlib import Path

from openflight.camera.capture_runtime import CaptureInbox, SavedCameraCapture


def _saved(sequence, trigger):
    return SavedCameraCapture(sequence, trigger, trigger + 0.2, Path(f"/tmp/c{sequence}"), {})


def test_matches_capture_within_tolerance():
    inbox = CaptureInbox()
    inbox.add(_saved(1, 100.0))
    assert inbox.take(100.4, tolerance_s=0.75, timeout_s=0.0).sequence == 1
    assert inbox.take(100.4, tolerance_s=0.75, timeout_s=0.0) is None


def test_discards_stale_captures_before_the_match():
    inbox = CaptureInbox()
    inbox.add(_saved(1, 90.0))
    inbox.add(_saved(2, 100.0))
    assert inbox.take(100.1, tolerance_s=0.75, timeout_s=0.0).sequence == 2
    assert inbox.pending() == 0


def test_keeps_future_capture_for_a_later_shot():
    inbox = CaptureInbox()
    inbox.add(_saved(1, 110.0))
    assert inbox.take(100.0, tolerance_s=0.75, timeout_s=0.0) is None
    assert inbox.pending() == 1


def test_no_timestamp_takes_oldest():
    inbox = CaptureInbox()
    inbox.add(_saved(1, 90.0))
    inbox.add(_saved(2, 100.0))
    assert inbox.take(None, tolerance_s=0.75, timeout_s=0.0).sequence == 1


def test_waits_for_a_capture_that_arrives_later():
    inbox = CaptureInbox()
    threading.Timer(0.05, inbox.add, args=(_saved(3, 50.0),)).start()
    started = time.monotonic()
    assert inbox.take(50.0, tolerance_s=0.75, timeout_s=1.0).sequence == 3
    assert time.monotonic() - started < 0.9
