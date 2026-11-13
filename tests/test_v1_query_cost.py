"""What one query costs at the door, counted at three sizes of a generated forest (``forest_sizes``).

The counts are pinned, not the time: after a build with nothing changed, a search and a timeline read cost a
listing per folder of the daily pages and the lanes and nothing per daily page or turn, but they compile the
registry from the trees, so they read every leaf and grow by a fixed number of door calls per leaf and per
branch; ``index status`` looks at every page and reads none. ``docs/tools.md`` says the same in words, with
these numbers. ``scripts/query_cost.py`` prints the counts and the times at larger sizes.

The bakery and its trays are invented. Nothing here is a real person, shop or transcript.
"""

from __future__ import annotations

import json
import tempfile
import unittest

import bounds  # installs a deadline for every unittest invocation
from pathlib import Path

from memory_forest import search

from forest_sizes import BRANCH, INDEX, branches, costs, generated, make_forest

SIZES = (BRANCH, 4 * BRANCH, 16 * BRANCH)
assert SIZES[-1] < search.IN_CHUNK  # every size's matched rows are asked in one statement
# Door calls a search or a timeline read adds for one more leaf: it reads the leaf (1), resolves and looks at
# its home as a registered subject, and checks its names against the masking's ledger (37 looks, 8 real paths).
QUERY_PER_LEAF = 46
QUERY_PER_BRANCH = 1  # the listing of the branch's folder in 04 stm
# ``index status``: a look at the leaf (with the parts of its path) and a real path, per leaf; the same for
# the branch's board, and a listing of the branch's folder.
STATUS_PER_LEAF = 7
STATUS_PER_BRANCH = 7


def _bytes(leaves: int) -> int:
    """The bytes of the generated leaves and of the bakery's index that lists them."""
    files = generated(leaves)
    return sum(len(text.encode()) for relative, text in files.items()
               if relative.startswith("04 stm/bakery/batch-")) + len(files[INDEX].encode())


class QueryCostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        base = Path(cls._temporary.name).resolve()
        cls.roots = {leaves: make_forest(base / f"forest-{leaves}", leaves) for leaves in SIZES}
        cls.costs = {leaves: costs(root) for leaves, root in cls.roots.items()}

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def _grown(self, name: str, leaves: int) -> int:
        return self.costs[leaves][name].total - self.costs[SIZES[0]][name].total

    def test_a_search_and_a_timeline_read_grow_with_the_leaves_of_the_trees(self) -> None:
        for name in ("search", "timeline"):
            for leaves in SIZES[1:]:
                with self.subTest(call=name, leaves=leaves):
                    added = leaves - SIZES[0], branches(leaves) - branches(SIZES[0])
                    self.assertEqual(self._grown(name, leaves), QUERY_PER_LEAF * added[0] + QUERY_PER_BRANCH * added[1])
                    self.assertEqual(self.costs[leaves][name].calls["read"]
                                     - self.costs[SIZES[0]][name].calls["read"], added[0])
                    # Every leaf is read whole, with the index that registers it; nothing else grows. The
                    # statements stay the same while a search's matched rows fit one ``IN (...)`` of
                    # ``search.IN_CHUNK``; past that, each 500 more add one (``scripts/query_cost.py`` shows it).
                    self.assertEqual(self.costs[leaves][name].bytes_read - _bytes(leaves),
                                     self.costs[SIZES[0]][name].bytes_read - _bytes(SIZES[0]))
                    self.assertEqual(self.costs[leaves][name].statements, self.costs[SIZES[0]][name].statements)

    def test_index_status_looks_at_every_page_and_reads_none(self) -> None:
        for leaves in SIZES[1:]:
            with self.subTest(leaves=leaves):
                added = leaves - SIZES[0], branches(leaves) - branches(SIZES[0])
                self.assertEqual(self._grown("index status", leaves),
                                 STATUS_PER_LEAF * added[0] + STATUS_PER_BRANCH * added[1])
                for field in ("bytes_read", "statements"):
                    self.assertEqual(getattr(self.costs[leaves]["index status"], field),
                                     getattr(self.costs[SIZES[0]]["index status"], field), field)

    def test_daily_pages_and_turns_cost_nothing_each(self) -> None:
        """Thirty more daily pages and thirty more lane files, in the folders that are there: the same calls."""
        root = self.roots[SIZES[0]]
        before = costs(root)
        added = []
        for day in range(1, 31):
            added.append(root / "05 daily" / f"2051-04-{day:02d}.md")
            added[-1].write_text(f"# 2051-04-{day:02d}\n\nThe proofing room was aired.\n", encoding="utf-8")
            added.append(root / "06 istm" / "oven-bot" / f"2051-04-{day:02d}.jsonl")
            added[-1].write_text(json.dumps({
                "at": f"2051-04-{day:02d}T08:00:00+00:00", "id": f"oven-bot:shift:{day}", "origin": "interactive",
                "role": "agent", "session": "shift", "source": {"adapter": "jsonl", "place": "1", "transcript": "shift"},
                "text": "the trays went in",
            }) + "\n", encoding="utf-8")
        try:
            self.assertEqual(search.build(root)["busy"], False)
            after = costs(root)
            for name in ("search", "timeline"):
                with self.subTest(call=name):
                    self.assertEqual((after[name].calls, after[name].bytes_read),
                                     (before[name].calls, before[name].bytes_read))
        finally:
            for path in added:
                path.unlink()
            search.build(root)


if __name__ == "__main__":
    unittest.main()
