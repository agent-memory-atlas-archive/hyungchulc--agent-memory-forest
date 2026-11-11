from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v1_support import SYNTHETIC_FOREST, ForestTestCase  # noqa: E402

from memory_forest import layout  # noqa: E402
from memory_forest.config import load_config, parse_config  # noqa: E402
from memory_forest.errors import MemoryForestError  # noqa: E402
from memory_forest.layout import PageRole  # noqa: E402


class PathTests(unittest.TestCase):
    def test_paths_follow_the_design(self) -> None:
        self.assertEqual(layout.ROOT_INDEX, "01 xltm/XLTM.md")
        self.assertEqual(layout.tree_page("bakery"), "02 ltm/bakery_LTM.md")
        self.assertEqual(layout.tree_index("bakery"), "03 mtm/bakery/bakery_MTM.md")
        self.assertEqual(layout.board("bakery", "deck-oven"), "03 mtm/bakery/deck-oven.md")
        self.assertEqual(layout.leaf_folder("bakery", "deck-oven"), "04 stm/bakery/deck-oven")
        self.assertEqual(layout.leaf("bakery", "deck-oven", "repairs"), "04 stm/bakery/deck-oven/repairs.md")
        self.assertEqual(
            layout.collection_page("people", "regulars", "2051-q1"),
            "04 stm/people/regulars/seen-once-2051-q1.md",
        )
        self.assertEqual(layout.daily_page("2051-03-02"), "05 daily/2051-03-02.md")
        self.assertEqual(layout.raw_lane("front-desk"), "06 istm/front-desk")
        self.assertEqual(layout.REGISTRY_FILE, ".forest/registry.json")
        self.assertEqual(layout.MOVES_FILE, ".forest/moves.jsonl")
        self.assertEqual(layout.ARCHIVE_DIR, ".forest/archive")
        with self.assertRaises(ValueError):
            layout.daily_page("2051-02-30")

    def test_each_path_has_its_role(self) -> None:
        cases = {
            "01 xltm/XLTM.md": (PageRole.ROOT_INDEX, None, None),
            "02 ltm/bakery_LTM.md": (PageRole.TREE_PAGE, "bakery", None),
            "03 mtm/bakery/bakery_MTM.md": (PageRole.TREE_INDEX, "bakery", None),
            "03 mtm/bakery/deck-oven.md": (PageRole.BOARD, "bakery", "deck-oven"),
            "04 stm/bakery/deck-oven/repairs.md": (PageRole.LEAF, "bakery", "deck-oven"),
            "04 stm/people/regulars/seen-once-2051-q1.md": (PageRole.COLLECTION_PAGE, "people", "regulars"),
            "04 stm/people/regulars/seen-once-2051-03-02-2.md": (PageRole.COLLECTION_PAGE, "people", "regulars"),
            "05 daily/2051-03-02.md": (PageRole.DAILY_PAGE, None, None),
            "06 istm/front-desk/2051-03-02.jsonl": (PageRole.RAW_FILE, "front-desk", None),
            "forest.toml": (PageRole.SETTINGS, None, None),
            ".forest/registry.json": (PageRole.STATE, None, None),
            "01 xltm/other.md": (PageRole.UNKNOWN, None, None),
            "04 stm/bakery/loose.md": (PageRole.UNKNOWN, None, None),
            "03 mtm/bakery/deck-oven.txt": (PageRole.UNKNOWN, None, None),
        }
        for path, (role, tree, branch) in cases.items():
            with self.subTest(path=path):
                info = layout.classify(path)
                self.assertEqual(info.role, role)
                self.assertEqual(info.tree, tree)
                self.assertEqual(info.branch, branch)
        self.assertTrue(layout.classify("04 stm/a/b/c.md").in_forest)
        self.assertFalse(layout.classify("05 daily/2051-03-02.md").in_forest)

    def test_parent_pages_follow_the_up_links(self) -> None:
        self.assertEqual(layout.parent_page("04 stm/bakery/deck-oven/repairs.md"), "03 mtm/bakery/deck-oven.md")
        self.assertEqual(layout.parent_page("03 mtm/bakery/deck-oven.md"), "02 ltm/bakery_LTM.md")
        self.assertEqual(layout.parent_page("03 mtm/bakery/bakery_MTM.md"), "02 ltm/bakery_LTM.md")
        self.assertEqual(layout.parent_page("02 ltm/bakery_LTM.md"), "01 xltm/XLTM.md")
        self.assertIsNone(layout.parent_page("01 xltm/XLTM.md"))

    def test_every_synthetic_page_has_a_forest_role(self) -> None:
        for page in layout.iter_pages(SYNTHETIC_FOREST):
            with self.subTest(page=page):
                self.assertTrue(layout.classify(page).in_forest)


class NameTests(unittest.TestCase):
    def test_slugs(self) -> None:
        self.assertTrue(layout.is_slug("deck-oven"))
        for bad in ("Deck-oven", "-oven", "deck oven", "deck_oven", ""):
            self.assertFalse(layout.is_slug(bad), bad)
        self.assertEqual(layout.slugify("Zoë O'Harra-Lind"), "zoe-oharra-lind")
        self.assertEqual(layout.slugify("Millstone  Flour, Ltd."), "millstone-flour-ltd")
        self.assertEqual(layout.slugify("Тамсин"), "")

    def test_periods_and_collection_names(self) -> None:
        self.assertEqual(layout.split_period("firings-2051-q2"), ("firings", "2051-q2"))
        self.assertEqual(layout.split_period("sessions-2051-04"), ("sessions", "2051-04"))
        self.assertEqual(layout.split_period("ledger-2050"), ("ledger", "2050"))
        self.assertEqual(layout.split_period("repairs"), ("repairs", None))
        # A day is a period only on a page of a month cut by days, beside its stream's other period pages.
        self.assertEqual(layout.split_period("firings-2051-04-09"), ("firings-2051-04-09", None))
        cut = ["firings-2051-04", "firings-2051-04-09", "firings-2051-04-09-2"]
        self.assertEqual(layout.split_period("firings-2051-04-09", cut), ("firings", "2051-04-09"))
        self.assertEqual(layout.split_period("firings-2051-04-09-2", cut), ("firings", "2051-04-09-2"))
        self.assertEqual(layout.split_period("glazes-2051-04-09", cut), ("glazes-2051-04-09", None))
        self.assertEqual(layout.period_grain("2051-04-09-2"), "day")
        self.assertIsNone(layout.period_grain("2051-04-09-1"))
        self.assertEqual(layout.collection_period("seen-once-2051-q1"), "2051-q1")
        self.assertEqual(layout.collection_period("seen-once-2051-03"), "2051-03")
        self.assertEqual(layout.collection_period("seen-once-2051-03-14"), "2051-03-14")
        self.assertEqual(layout.collection_period("seen-once-2051-03-14-3"), "2051-03-14-3")
        for bad in ("seen-once-2051-03-14-1", "seen-once-2051-13", "seen-once", "seen-once-2051"):
            self.assertIsNone(layout.collection_period(bad), bad)

    def test_name_rules(self) -> None:
        for good in ("repairs.md", "firings-2051-q2.md", "seen-once-2051-03-14-2.md", "cone-6-limits.md"):
            self.assertIsNone(layout.name_problem(good), good)
        for bad, word in (
            ("notes-7.md", "numbered"),
            ("visit-2051-03-14.md", "date"),
            ("trip-2051-summer.md", "date"),
            ("may-fault.md", "date word"),
            ("Repairs.md", "slug"),
        ):
            with self.subTest(name=bad):
                problem = layout.name_problem(bad)
                self.assertIsNotNone(problem)
                self.assertIn(word, problem)
        self.assertIsNotNone(layout.name_problem("firings-2051-q2.md", period_allowed=False))
        self.assertIsNone(layout.name_problem("firings-2051-04-09.md", siblings=["firings-2051-04.md"]))
        self.assertIn("date", layout.name_problem("firings-2051-04-09.md", siblings=["glazes-2051-04.md"]) or "")

    def test_date_words_are_a_setting(self) -> None:
        config = parse_config('[names]\ndate_words = ["lente"]\n')
        self.assertIsNotNone(layout.name_problem("trip-lente.md", config=config))
        self.assertIsNone(layout.name_problem("may-fault.md", config=config))


class SafePathTests(ForestTestCase):
    def test_resolve_refuses_escapes_and_symlinks(self) -> None:
        root = self.make_forest()
        self.assertEqual(layout.resolve(root, "04 stm/bakery"), root / "04 stm" / "bakery")
        for bad in ("../outside.md", "/etc/passwd", "04 stm/../../x", "04 stm/./a", "04 stm\\a", ""):
            with self.subTest(path=bad):
                with self.assertRaises(MemoryForestError):
                    layout.resolve(root, bad)
        outside = self.base / "outside"
        outside.mkdir()
        os.symlink(outside, root / "04 stm" / "linked")
        with self.assertRaises(MemoryForestError) as caught:
            layout.resolve(root, "04 stm/linked/page.md")
        self.assertEqual(caught.exception.code, "symlink_forbidden")
        with self.assertRaises(MemoryForestError):
            layout.iter_pages(root)

    def test_a_missing_path_is_allowed_unless_it_must_exist(self) -> None:
        root = self.make_forest()
        self.assertEqual(layout.resolve(root, "04 stm/new/branch/leaf.md").name, "leaf.md")
        with self.assertRaises(MemoryForestError):
            layout.resolve(root, "04 stm/new/branch/leaf.md", must_exist=True)

    def test_relative_to_root(self) -> None:
        root = self.make_forest()
        self.assertEqual(layout.relative_to_root(root, root / "04 stm" / "a.md"), "04 stm/a.md")
        with self.assertRaises(MemoryForestError):
            layout.relative_to_root(root, self.base / "elsewhere.md")

    def test_trees_and_pages_are_listed_in_order(self) -> None:
        root = self.make_forest()
        self.assertEqual(layout.trees(root), ["bakery", "front-desk", "people"])
        pages = layout.iter_pages(root, (layout.STM,))
        self.assertEqual(pages, sorted(pages))
        self.assertIn("04 stm/bakery/deck-oven/repairs.md", pages)


class InitTests(ForestTestCase):
    def test_init_writes_settings_layers_root_index_and_state(self) -> None:
        target = self.base / "fresh"
        result = layout.initialize(target)
        self.assertEqual(result["root"], str(target))
        for folder in (*layout.ALL_LAYERS, ".forest", ".forest/locks", ".forest/archive"):
            self.assertTrue((target / folder).is_dir(), folder)
        text = (target / layout.ROOT_INDEX).read_text(encoding="utf-8")
        for section in layout.ROOT_INDEX_SECTIONS:
            self.assertIn(f"## {section}\n", text)
        self.assertEqual(load_config(target).owner.id, "owner")
        self.assertEqual(oct((target / ".forest").stat().st_mode & 0o777), oct(0o700))

    def test_init_refuses_a_folder_that_holds_something(self) -> None:
        target = self.base / "used"
        target.mkdir()
        (target / "notes.md").write_text("x", encoding="utf-8")
        with self.assertRaises(MemoryForestError) as caught:
            layout.initialize(target)
        self.assertEqual(caught.exception.code, "root_not_empty")

    def test_init_accepts_an_empty_folder(self) -> None:
        target = self.base / "empty"
        target.mkdir()
        layout.initialize(target)
        self.assertTrue((target / "forest.toml").is_file())


if __name__ == "__main__":
    unittest.main()
