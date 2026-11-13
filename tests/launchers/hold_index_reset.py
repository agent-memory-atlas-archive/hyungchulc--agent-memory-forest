"""Run ``search.reset`` and stand after the lock is held, before the pointer moves.

The pause replaces ``_reset_held`` in this process only. A parent that disappears hits the alarm.
"""

from __future__ import annotations

import json
import os
import signal
import sys

from memory_forest import search


def _expire(_signum: int, _frame: object) -> None:
    raise SystemExit("timed out standing inside the index reset")


def main() -> None:
    root, held, release = sys.argv[1], sys.argv[2], sys.argv[3]
    original = search._reset_held

    def hold(forest: object, *, dry_run: bool = False) -> dict[str, object]:
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
        return original(forest, dry_run=dry_run)

    search._reset_held = hold  # type: ignore[assignment]
    json.dump(search.reset(root), sys.stdout)
    sys.stdout.write("\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
