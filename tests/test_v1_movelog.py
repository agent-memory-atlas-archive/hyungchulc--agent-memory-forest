from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v1_support import ForestTestCase  # noqa: E402

from memory_forest import archive, movelog  # noqa: E402
from memory_forest.errors import MemoryForestError  # noqa: E402
from memory_forest.locking import writer_lock  # noqa: E402

LEAF = "04 stm/bakery/deck-oven/repairs.md"


class MoveLogTests(ForestTestCase):
    def move(self, root: Path, old: str, new: str) -> None:
        source, target = root / old, root / new
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)

    def test_records_are_appended_as_one_json_line_each(self) -> None:
        root = self.make_forest()
        first = movelog.record("move", LEAF, "04 stm/bakery/deck-oven/door-repairs.md", subject="deck-oven", reason="a  clearer\nname")
        second = movelog.record("archive", "04 stm/bakery/deck-oven/old.md", None, reason="folded")
        self.assertEqual(movelog.append(root, [first, second]), 2)
        lines = (root / ".forest" / "moves.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(
            sorted(json.loads(lines[0])),
            ["from", "operation", "reason", "subject", "time", "to"],
        )
        self.assertEqual(json.loads(lines[0])["reason"], "a clearer name")
        self.assertEqual(movelog.read(root), [first, second])

    def test_records_are_checked(self) -> None:
        for args in (
            ("Move", "a", "b"),
            ("move", "../a", "b"),
            ("move", "/a", "b"),
            ("move", "04 stm/a.md", "04 stm/a.md"),
        ):
            with self.subTest(args=args):
                with self.assertRaises(MemoryForestError):
                    movelog.record(*args)

    def test_append_takes_the_writer_lock_and_re_enters_with_a_token(self) -> None:
        root = self.make_forest()
        item = movelog.record("move", "04 stm/a/b/c.md", "04 stm/a/b/d.md")
        with writer_lock(root) as token:
            with self.assertRaises(MemoryForestError):
                movelog.append(root, [item])
            movelog.append(root, [item], lock=token)
        self.assertEqual(len(movelog.read(root)), 1)

    def test_resolve_follows_a_chain_to_the_present_place(self) -> None:
        root = self.make_forest()
        middle = "04 stm/bakery/deck-oven/door-repairs.md"
        final = "04 stm/bakery/ovens/deck-oven-repairs.md"
        self.move(root, LEAF, middle)
        movelog.append(root, [movelog.record("rename", LEAF, middle, subject="deck-oven")])
        self.move(root, middle, final)
        movelog.append(root, [movelog.record("move", middle, final, subject="deck-oven")])
        found = movelog.resolve(root, LEAF)
        self.assertEqual((found.status, found.path), ("current", final))
        self.assertEqual([item.operation for item in found.chain], ["rename", "move"])
        self.assertEqual(movelog.resolve(root, middle).path, final)

    def test_a_page_moved_away_and_back_resolves_to_where_it_is_now(self) -> None:
        root = self.make_forest()
        other = "04 stm/bakery/deck-oven/elsewhere.md"
        self.move(root, LEAF, other)
        movelog.append(root, [movelog.record("move", LEAF, other)])
        self.move(root, other, LEAF)
        movelog.append(root, [movelog.record("move", other, LEAF)])
        found = movelog.resolve(root, LEAF)
        self.assertEqual((found.status, found.path), ("current", LEAF))
        self.assertEqual(movelog.resolve(root, other).path, LEAF)

    def test_a_moved_folder_carries_its_pages(self) -> None:
        root = self.make_forest()
        self.move(root, "04 stm/bakery/deck-oven", "04 stm/ovens/deck-oven")
        movelog.append(root, [movelog.record("move", "04 stm/bakery/deck-oven", "04 stm/ovens/deck-oven")])
        found = movelog.resolve(root, LEAF)
        self.assertEqual((found.status, found.path), ("current", "04 stm/ovens/deck-oven/repairs.md"))

    def test_entry_and_registry_addresses(self) -> None:
        root = self.make_forest()
        page = "04 stm/people/regulars/seen-once-2051-q1.md"
        leaf = "04 stm/people/regulars/bram-eskell.md"
        (root / leaf).write_text("admitted\n", encoding="utf-8")
        movelog.append(root, [movelog.record("admit", f"{page}#bram-eskell", leaf, subject="bram-eskell")])
        self.assertEqual(movelog.resolve(root, f"{page}#bram-eskell").path, leaf)
        # an entry whose page moved follows the page
        new_page = "04 stm/people/regulars/seen-once-2051-03.md"
        self.move(root, page, new_page)
        movelog.append(root, [movelog.record("recut", page, new_page)])
        found = movelog.resolve(root, f"{page}#tamsin-orrow")
        self.assertEqual((found.status, found.path), ("current", f"{new_page}#tamsin-orrow"))
        # a registry line moved from one index to another
        movelog.append(
            root,
            [
                movelog.record(
                    "move-branch",
                    "03 mtm/bakery/bakery_MTM.md#deck-oven",
                    "03 mtm/people/people_MTM.md#deck-oven",
                    subject="deck-oven",
                )
            ],
        )
        self.assertEqual(
            movelog.resolve(root, "03 mtm/bakery/bakery_MTM.md#deck-oven").path,
            "03 mtm/people/people_MTM.md#deck-oven",
        )

    def test_split_lines_are_passed_over(self) -> None:
        root = self.make_forest()
        movelog.append(root, [movelog.record("split", LEAF, "04 stm/bakery/deck-oven/door.md")])
        found = movelog.resolve(root, LEAF)
        self.assertEqual((found.status, found.path, found.chain), ("current", LEAF, []))
        self.assertEqual(len(movelog.read(root, include_non_moving=True)), 1)

    def test_an_archived_page_resolves_to_its_archive_entry_and_successor(self) -> None:
        root = self.make_forest()
        successor = "04 stm/bakery/deck-oven/maintenance.md"
        (root / successor).write_text("maintenance\n", encoding="utf-8")
        archive.put(root, LEAF, reason="merged into maintenance", successor=successor)
        movelog.append(root, [movelog.record("archive", LEAF, None, reason="merged")])
        found = movelog.resolve(root, LEAF)
        self.assertEqual(found.status, "archived")
        self.assertEqual(found.successor, successor)
        self.assertEqual(found.archive["path"], LEAF)
        # the successor itself moved later: resolve names where it is now
        moved = "04 stm/bakery/ovens/maintenance.md"
        self.move(root, successor, moved)
        movelog.append(root, [movelog.record("move", successor, moved)])
        self.assertEqual(movelog.resolve(root, LEAF).successor, moved)

    def test_unknown_paths_and_broken_logs(self) -> None:
        root = self.make_forest()
        self.assertEqual(movelog.resolve(root, "04 stm/never/was/here.md").status, "unknown")
        movelog.append(root, [movelog.record("move", "04 stm/a/b/x.md", "04 stm/a/b/y.md")])
        movelog.append(root, [movelog.record("move", "04 stm/a/b/y.md", "04 stm/a/b/x.md")])
        with open(root / ".forest" / "moves.jsonl", "a", encoding="utf-8") as handle:
            handle.write("not json\n{\"from\": 3}\n")
        found = movelog.resolve(root, "04 stm/a/b/x.md")
        self.assertEqual(found.status, "unknown")
        self.assertEqual(found.path, "04 stm/a/b/x.md")
        self.assertEqual(len(found.chain), 2)


if __name__ == "__main__":
    unittest.main()
