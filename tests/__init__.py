"""Keep direct unittest module/file selection on the same bounded test setup as discovery."""
from __future__ import annotations

import sys
from pathlib import PurePath

_test_folder = str(PurePath(__file__).parent)
if _test_folder not in sys.path:
    sys.path.insert(0, _test_folder)

import bounds  # noqa: E402,F401
