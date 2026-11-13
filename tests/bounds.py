"""A test that never returns fails in every unittest entry point, with every thread's stack."""
from __future__ import annotations

import faulthandler
import os
import sys
import unittest

DEFAULT_SECONDS = 90
_original = unittest.TestCase.run


def _run(self, result=None):
    seconds = float(os.environ.get("FOREST_TEST_BOUND", getattr(self, "bound_seconds", DEFAULT_SECONDS)))
    sys.stderr.flush()
    faulthandler.dump_traceback_later(seconds, exit=True)
    try:
        return _original(self, result)
    finally:
        faulthandler.cancel_dump_traceback_later()


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
    faulthandler.dump_traceback_later(seconds, exit=True)
    try:
        return _setup(self, test, result)
    finally:
        faulthandler.cancel_dump_traceback_later()


unittest.TestSuite._handleClassSetUp = _class_setup
