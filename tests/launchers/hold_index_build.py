"""Run ``search.build`` and stand inside the write, after the rows are staged and before commit.

The pause wraps ``_set_meta`` in this process only, on the ``built_at`` write that sits immediately
before the commit. A parent that disappears hits the alarm.
"""

from __future__ import annotations

import os
import signal
import sys

from memory_forest import search


def _expire(_signum: int, _frame: object) -> None:
    raise SystemExit("timed out standing inside the index build")


def main() -> int:
    root, held, release = sys.argv[1], sys.argv[2], sys.argv[3]
    real_set_meta = search._set_meta
    paused = {"done": False}

    def set_meta(connection: object, key: str, value: str) -> None:
        real_set_meta(connection, key, value)
        if paused["done"] or key != "built_at":
            return
        paused["done"] = True
        signal.signal(signal.SIGALRM, _expire)
        signal.alarm(120)
        descriptor = os.open(held, os.O_WRONLY)
        try:
            os.write(descriptor, b"held\n")
        finally:
            os.close(descriptor)
        signal.alarm(120)
        descriptor = os.open(release, os.O_RDONLY)
        try:
            os.read(descriptor, 64)
        finally:
            os.close(descriptor)
        signal.alarm(0)

    search._set_meta = set_meta  # type: ignore[assignment]
    result = search.build(root, wait_seconds=30)
    return 0 if not result.get("busy") else 1


if __name__ == "__main__":
    raise SystemExit(main())
