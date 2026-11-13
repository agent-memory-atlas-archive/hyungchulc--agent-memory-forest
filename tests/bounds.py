"""A test that never returns fails in every unittest entry point, requesting thread stacks first."""
from __future__ import annotations

import atexit
import faulthandler
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

DEFAULT_SECONDS = 90
_original = unittest.TestCase.run


# The native traceback writer can itself stall on a hot frame. An independent interpreter owns the
# final deadline, so that diagnostic failure cannot turn a failing test into an infinite test run.
_WATCHDOG = Path(__file__).with_name("bounds_watchdog.py")
_WATCH = None
_WATCH_PARENT = None
_pid = os.getpid
_now = time.monotonic
_popen = subprocess.Popen
DUMP_GRACE_SECONDS = 1.0


def _send(command):
    global _WATCH, _WATCH_PARENT
    if _WATCH is None or _WATCH_PARENT != _pid():
        _WATCH_PARENT = _pid()
        _WATCH = _popen([sys.executable, str(_WATCHDOG), str(_WATCH_PARENT)],
                        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True)
    _WATCH.stdin.write(command + "\n")
    _WATCH.stdin.flush()


def _arm(seconds):
    _send(str(_now() + seconds + DUMP_GRACE_SECONDS))
    faulthandler.dump_traceback_later(seconds, exit=True)


def _disarm():
    faulthandler.cancel_dump_traceback_later()
    _send("clear")


def _close_watch():
    if _WATCH is None or _WATCH_PARENT != _pid():
        return
    try:
        _WATCH.stdin.close()
    except BrokenPipeError:
        pass
    try:
        _WATCH.wait(timeout=2)
    except subprocess.TimeoutExpired:
        _WATCH.kill()
        _WATCH.wait(timeout=2)


atexit.register(_close_watch)


def _run(self, result=None):
    seconds = float(os.environ.get("FOREST_TEST_BOUND", getattr(self, "bound_seconds", DEFAULT_SECONDS)))
    sys.stderr.flush()
    _arm(seconds)
    try:
        return _original(self, result)
    finally:
        _disarm()


if not getattr(unittest.TestCase.run, "forest_bound", False):
    _run.forest_bound = True
    unittest.TestCase.run = _run

_stop = unittest.TestResult.stopTestRun


def _finish(self):
    skipped = sum("superuser" in reason.casefold() for _, reason in getattr(self, "skipped", []))
    sys.stderr.write(f"\nsuperuser denial rows skipped: {skipped}\n")
    if skipped and os.environ.get("FOREST_TESTS_ALLOW_SUPERUSER") != "1":
        self.addFailure(unittest.FunctionTestCase(lambda: None, description="ordinary account required"),
                        (AssertionError, AssertionError(
                            f"{skipped} denial rows skipped; run as an ordinary account, or explicitly set "
                            "FOREST_TESTS_ALLOW_SUPERUSER=1"), None))
    return _stop(self)


unittest.TestResult.stopTestRun = _finish

_setup = unittest.TestSuite._handleClassSetUp


def _class_setup(self, test, result):
    seconds = float(os.environ.get("FOREST_TEST_BOUND", getattr(test, "bound_seconds", DEFAULT_SECONDS)))
    _arm(seconds)
    try:
        return _setup(self, test, result)
    finally:
        _disarm()


unittest.TestSuite._handleClassSetUp = _class_setup
