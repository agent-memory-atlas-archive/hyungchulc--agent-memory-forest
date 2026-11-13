#!/usr/bin/env python3
"""Print what one query costs at three sizes of a generated forest: the door's calls and bytes, and the time.

For each size it writes a forest of that many generated leaves (the bakery of the tests, invented; see
tests/forest_sizes.py) into a private temporary folder, builds its index, and then, with nothing changed,
counts one `search`, one `timeline` read and one `index status` at the door and times each (the median of
--repeat runs). The counts are pinned by tests/test_v1_query_cost.py; the times are for the reader and
depend on the machine and its load. This is not part of `make check`.

Run from the repository root:

  nice -n 15 python3 scripts/query_cost.py
  nice -n 15 python3 scripts/query_cost.py --sizes 100 1000 5000 --repeat 5
"""

from __future__ import annotations

import argparse
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

import forest_sizes  # noqa: E402  (after the path is set)


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print the door's counts and the times of one query at three "
                                     "sizes of a generated forest.")
    parser.add_argument("--sizes", type=_positive, nargs=3, default=[100, 1000, 5000], metavar="LEAVES",
                        help="the leaves of the three forests (default: %(default)s)")
    parser.add_argument("--repeat", type=_positive, default=5, help="runs per call; the median is shown")
    args = parser.parse_args(argv)
    folder = Path(tempfile.mkdtemp(prefix="query-cost-"))
    rows = []
    try:
        for leaves in args.sizes:
            started = time.monotonic()
            root = forest_sizes.make_forest(folder / f"forest-{leaves}", leaves)
            built = time.monotonic() - started
            print(f"{leaves} leaves on {forest_sizes.branches(leaves)} branches: built in {built:.1f} s",
                  file=sys.stderr, flush=True)
            counted = forest_sizes.costs(root)
            for name, call in forest_sizes.calls(root).items():
                seconds = []
                for _ in range(args.repeat):
                    began = time.perf_counter()
                    call()
                    seconds.append(time.perf_counter() - began)
                cost = counted[name]
                rows.append([str(leaves), name, str(cost.total), str(cost.calls.get("read", 0)),
                             str(cost.bytes_read), str(cost.statements), f"{statistics.median(seconds) * 1000:.0f}"])
    finally:
        shutil.rmtree(folder)
    headers = ["leaves", "call", "door calls", "reads", "bytes read", "statements", "median ms"]
    widths = [max(len(row[column]) for row in [headers, *rows]) for column in range(len(headers))]
    print("| " + " | ".join(header.ljust(width) for header, width in zip(headers, widths)) + " |")
    print("|" + "|".join("-" * (width + 2) for width in widths) + "|")
    for row in rows:
        print("| " + " | ".join(cell.rjust(width) if index != 1 else cell.ljust(width)
                                for index, (cell, width) in enumerate(zip(row, widths))) + " |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
