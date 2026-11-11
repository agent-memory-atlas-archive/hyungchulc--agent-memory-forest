"""``forest check [--sync]``: the derived lists brought up to the pages, hand descriptions kept."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v1_move import difference, snapshot  # noqa: E402
from v1_support import ForestTestCase  # noqa: E402

from memory_forest import maps  # noqa: E402

BOARD = "03 mtm/bakery/deck-oven.md"


class SyncListTests(ForestTestCase):
    def test_sync_list_keeps_descriptions_drops_duplicates_and_stale_lines_and_appends_missing(self) -> None:
        text = ("# board\n\n## Leaves\n\n- `a.md` — kept by hand\n- `gone.md` — no page\n- `a.md` — twice\n\n"
                "## Links\n\n- up: [[x|x]]\n")
        wanted = {"a.md": maps.Wanted("a.md", "made", "2051-03-02"), "b.md": maps.Wanted("b.md", "new page")}
        new, change = maps.sync_list(text, "Leaves", wanted, key=lambda target: target, removable=lambda _t: True)
        self.assertEqual(
            new,
            "# board\n\n## Leaves\n\n- `a.md` — kept by hand (updated 2051-03-02)\n- `b.md` — new page\n\n"
            "## Links\n\n- up: [[x|x]]\n",
        )
        self.assertEqual(change.added, ["- `b.md` — new page"])
        self.assertEqual(change.removed, ["- `gone.md` — no page", "- `a.md` — twice"])
        again, nothing = maps.sync_list(new, "Leaves", wanted, key=lambda target: target, removable=lambda _t: True)
        self.assertEqual((again, nothing), (new, None))

    def test_a_missing_section_is_made_before_the_named_one(self) -> None:
        text = "# board\n\nAbout it.\n\n## Links\n\n- up: [[x|x]]\n"
        new, _ = maps.sync_list(text, "Leaves", {"a.md": maps.Wanted("a.md", "one")}, key=lambda target: target,
                                removable=lambda _t: True, before=("Links",))
        self.assertEqual(new, "# board\n\nAbout it.\n\n## Leaves\n\n- `a.md` — one\n\n## Links\n\n- up: [[x|x]]\n")


class CheckTests(ForestTestCase):
    def test_check_lists_what_differs_and_sync_brings_it_up_once(self) -> None:
        root = self.make_forest()
        stale = maps.check(root)
        self.assertIn((BOARD, "Leaves"), [(change.path, change.section) for change in stale])
        before = snapshot(root)
        maps.apply(root)
        changed = difference(before, snapshot(root))
        self.assertEqual(changed["created"] + changed["removed"], [])
        self.assertEqual(sorted({change.path for change in stale}), changed["changed"])
        self.assertIn("- `repairs.md` — deck-oven (updated 2051-03-02)", (root / BOARD).read_text(encoding="utf-8"))
        self.assertEqual(maps.check(root), [])
        settled = snapshot(root)
        maps.apply(root)
        self.assertEqual(snapshot(root), settled)

    def test_hand_written_lines_survive_and_lines_without_a_page_go(self) -> None:
        root = self.make_forest()
        maps.apply(root)
        board = root / BOARD
        text = board.read_text(encoding="utf-8")
        text = text.replace("`repairs.md` — deck-oven", "`repairs.md` — every fix since it came")
        text = text.replace("- `repairs.md`", "- `old.md` — a page that is gone\n- `repairs.md`")
        board.write_text(text, encoding="utf-8")
        found = maps.check(root)
        self.assertEqual([(change.path, change.removed) for change in found],
                         [(BOARD, ["- `old.md` — a page that is gone"])])
        maps.apply(root)
        new = board.read_text(encoding="utf-8")
        self.assertIn("- `repairs.md` — every fix since it came (updated 2051-03-02)", new)
        self.assertNotIn("old.md", new)

    def test_a_dry_run_writes_nothing(self) -> None:
        root = self.make_forest()
        before = snapshot(root)
        result = maps.apply(root, dry_run=True)
        self.assertTrue(result["result"])
        self.assertEqual(snapshot(root), before)

    def test_root_lists_follow_trees_and_agents(self) -> None:
        root = self.make_forest()
        index = root / "01 xltm/XLTM.md"
        index.write_text(index.read_text(encoding="utf-8").replace("- `people` — the people Ilse knows.\n", ""),
                         encoding="utf-8")
        maps.apply(root)
        text = index.read_text(encoding="utf-8")
        trees = text.split("## Trees", 1)[1].split("## Agents", 1)[0]
        self.assertIn("- `bakery` — the bakery: its ovens, recipes and suppliers.", trees)
        self.assertIn("- `people`", trees)
        self.assertNotIn("front-desk", trees)
        self.assertIn("- `front-desk` — Front Desk · orchestrator", text.split("## Agents", 1)[1])
