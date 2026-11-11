"""A current-state line that states one collection entry cites ``<page>#<id>``: its date, its moves and the
one-time ``forest collections cite-entries`` upkeep, on made-up forests."""

from __future__ import annotations

import gzip
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v1_collections import COLLECTION, CollectionTestCase, entry  # noqa: E402
from test_v1_move import TODAY, snapshot  # noqa: E402

from memory_forest import archive, collections, lint, move, movelog  # noqa: E402
from memory_forest.model import CurrentStateLine  # noqa: E402

BOARD = "03 mtm/people/regulars.md"
TAMSIN = f"{COLLECTION}#tamsin-orrow"
BRAM = f"{COLLECTION}#bram-eskell"
LATER_LINE = "- 2051-03-20 15:00 Pacific/Auckland — Bram Eskell: brought a second crate.\n"


def with_state(root: Path, *lines: str) -> None:
    """The regulars board with these lines under ``## Current state``."""
    board = root / BOARD
    text = board.read_text(encoding="utf-8")
    block = "## Current state\n\n" + "".join(line + "\n" for line in lines) + "\n"
    if "## Current state" in text:
        head, _, rest = text.partition("## Current state")
        rest = rest[rest.index("\n## ") + 1:] if "\n## " in rest else ""
        text = head + block + rest
    else:
        text = text.replace("## Leaves", block + "## Leaves", 1) if "## Leaves" in text else text + "\n" + block
    board.write_text(text, encoding="utf-8")


def add_timeline(root: Path, line: str, updated: str) -> None:
    page = root / COLLECTION
    text = page.read_text(encoding="utf-8")
    head, _, rest = text.partition("## Timeline\n\n")
    first, _, after = rest.partition("\n\n")
    text = head + "## Timeline\n\n" + first + "\n" + line.rstrip("\n") + "\n\n" + after
    text = text.replace("updated_at: 2051-03-02", f"updated_at: {updated}", 1)
    page.write_text(text, encoding="utf-8")


def stale(root: Path) -> list[str]:
    report = lint.lint(root, codes=["current_state_stale"])
    return [hit.message for hit in report.hits if hit.code == "current_state_stale"]


class EntrySourceLintTests(CollectionTestCase):
    def test_an_entry_source_is_dated_by_its_entry_and_a_bare_page_by_the_page(self) -> None:
        root = self.forest()
        with_state(root, f"- Tamsin Orrow wants rye on Fridays (as of 2051-03-02; source `{TAMSIN}`)",
                   f"- Regulars came in for bread (as of 2051-03-02; source `{COLLECTION}`)")
        self.assertEqual(stale(root), [])
        add_timeline(root, LATER_LINE, "2051-03-20")
        found = stale(root)
        self.assertEqual(len(found), 1, found)
        self.assertIn(f"`{COLLECTION}` changed 2051-03-20", found[0])
        add_timeline(root, "- 2051-03-25 09:00 Pacific/Auckland — Tamsin Orrow: asked again.\n", "2051-03-25")
        self.assertTrue(any(f"`{TAMSIN}` changed 2051-03-25" in message for message in stale(root)))

    def test_an_entry_with_no_dated_line_goes_by_its_page(self) -> None:
        root = self.forest()
        with_state(root, f"- Bram Eskell delivers apples (as of 2051-03-02; source `{BRAM}`)")
        self.assertEqual(stale(root), [])
        add_timeline(root, "- 2051-03-09 09:00 Pacific/Auckland — Tamsin Orrow: asked again.\n", "2051-03-09")
        self.assertEqual(len(stale(root)), 1)

    def test_a_moved_entry_is_named_with_its_place_now_and_a_missing_one_is_gone(self) -> None:
        root = self.forest()
        page = root / COLLECTION
        text = page.read_text(encoding="utf-8")
        section = text[text.index("## Bram Eskell"):text.index("## Timeline")]
        page.write_text(text.replace(section, ""), encoding="utf-8")
        other = "04 stm/people/regulars/seen-once-2051-03.md"
        (root / other).write_text(text.replace("(2051-q1)", "(2051-03)").replace(
            text[text.index("## Tamsin Orrow"):text.index("## Bram Eskell")], ""), encoding="utf-8")
        movelog.append(root, [movelog.record("recut", BRAM, f"{other}#bram-eskell", subject="bram-eskell")])
        with_state(root, f"- Bram Eskell delivers apples (as of 2051-03-02; source `{BRAM}`)",
                   f"- Nobody here (as of 2051-03-02; source `{COLLECTION}#nobody-here`)")
        found = stale(root)
        self.assertIn(f"the source `{BRAM}` moved to `{other}#bram-eskell`", found)
        self.assertIn(f"the source `{COLLECTION}#nobody-here` is not there", found)

    def test_a_line_naming_several_entries_is_stale_when_one_of_them_changed(self) -> None:
        line = f"- Both came in (as of 2051-03-02; sources `{TAMSIN}`, `{BRAM}`)"
        self.assertEqual(CurrentStateLine.parse(line).sources, (TAMSIN, BRAM))
        self.assertEqual(CurrentStateLine.parse(line).render(), line)
        self.assertIsNone(CurrentStateLine.match(f"- Both (as of 2051-03-02; source `{TAMSIN}`, `{BRAM}`)"))
        root = self.forest()
        with_state(root, line)
        self.assertEqual(stale(root), [])
        add_timeline(root, LATER_LINE, "2051-03-20")
        self.assertEqual(len(stale(root)), 1)


class EntryMoveTests(CollectionTestCase):
    def test_resolve_follows_an_entry_through_an_admit_and_a_later_move(self) -> None:
        root = self.forest()
        collections.execute(root, "admit", "tamsin-orrow", today=TODAY)
        leaf = "04 stm/people/regulars/tamsin-orrow.md"
        self.assertResolves(root, TAMSIN, leaf)
        moved = "04 stm/people/regulars/tamsin-orrow-profile.md"
        move.execute(root, "leaf", today=TODAY, source=leaf, target=moved)
        self.assertResolves(root, TAMSIN, moved)

    def test_an_entry_not_on_its_page_and_never_logged_resolves_nowhere(self) -> None:
        root = self.forest()
        self.assertResolves(root, TAMSIN, TAMSIN)
        found = movelog.resolve(root, f"{COLLECTION}#nobody-here")
        self.assertNotEqual(found.status, "current")

    def test_admit_rewrites_a_board_line_that_cites_the_entry(self) -> None:
        root = self.forest()
        with_state(root, f"- Tamsin Orrow wants rye on Fridays (as of 2051-03-02; source `{TAMSIN}`)",
                   f"- Bram Eskell delivers apples (as of 2051-03-02; source `{BRAM}`)")
        collections.execute(root, "admit", "tamsin-orrow", today=TODAY)
        board = self.text(root, BOARD)
        self.assertIn("source `04 stm/people/regulars/tamsin-orrow.md`)", board)
        self.assertIn(f"source `{BRAM}`)", board)

    def test_a_quarter_cut_rewrites_entry_sources_to_their_month_pages(self) -> None:
        root = self.forest()
        settings = root / "forest.toml"
        settings.write_text(settings.read_text(encoding="utf-8")
                            + "\n[limits]\nleaf_cap_bytes = 1200\nentry_admit_margin_bytes = 100\n", encoding="utf-8")
        page = root / COLLECTION
        extra = (entry("Corin Vale", "corin-vale", "2051-01-15", 20)
                 + entry("Odile Marsh", "odile-marsh", "2051-02-09", 20))
        page.write_text(page.read_text(encoding="utf-8").replace("## Timeline", extra + "## Timeline"),
                        encoding="utf-8")
        with_state(root, f"- Corin Vale buys bread (as of 2051-01-15; source `{COLLECTION}#corin-vale`)")
        collections.execute(root, "recut", COLLECTION, today=TODAY)
        found = movelog.resolve(root, f"{COLLECTION}#corin-vale")
        self.assertEqual(found.status, "current")
        self.assertIn(f"source `{found.path}`)", self.text(root, BOARD))
        self.assertTrue(found.path.endswith("#corin-vale") and found.path != f"{COLLECTION}#corin-vale", found)

    def test_demote_makes_a_line_citing_the_leaf_cite_the_entry(self) -> None:
        root = self.forest()
        board = root / "03 mtm/bakery/suppliers.md"
        board.write_text(board.read_text(encoding="utf-8").replace(
            "## Leaves", "## Current state\n\n- Millstone delivers flour (as of 2051-03-02; source "
            "`04 stm/bakery/suppliers/millstone-flour.md`)\n\n## Leaves"), encoding="utf-8")
        target = collections.execute(root, "demote", "millstone-flour", today=TODAY)
        self.assertTrue(target["written"])
        self.assertIn("source `04 stm/bakery/suppliers/seen-once-2051-q1.md#millstone-flour`)", board.read_text(
            encoding="utf-8"))
        self.assertEqual(stale(root), [])


class CiteEntriesTests(CollectionTestCase):
    LINES = (
        f"- Tamsin Orrow wants rye on Fridays (as of 2051-03-20; source `{COLLECTION}`)",
        f"- Tamsin Orrow and Bram Eskell came in (as of 2051-03-20; source `{COLLECTION}`)",
        f"- Regulars came in for bread (as of 2051-03-20; source `{COLLECTION}`)",
        f"- Tam asked first (as of 2051-02-01; source `{COLLECTION}`)",
    )

    def prepared(self) -> Path:
        root = self.forest()
        add_timeline(root, LATER_LINE, "2051-03-20")
        with_state(root, *self.LINES)
        return root

    def test_a_dry_run_counts_and_writes_nothing(self) -> None:
        root = self.prepared()
        before = snapshot(root)
        report = collections.cite_entries(root, dry_run=True)
        self.assertEqual(snapshot(root), before)
        self.assertEqual(report["outcomes"], {"one": 2, "several": 1, "none": 1})
        self.assertEqual(report["references_rewritten"], 3)
        self.assertEqual(report["dates_corrected"], 1)
        self.assertEqual(report["lines_left_stale"], 1)
        self.assertEqual(report["pages"], [BOARD])
        self.assertFalse(report["written"])

    def test_cite_entries_rewrites_only_the_tags_and_a_second_run_changes_nothing(self) -> None:
        root = self.prepared()
        before = (root / BOARD).read_bytes()
        report = collections.cite_entries(root)
        self.assertTrue(report["written"])
        after = (root / BOARD).read_bytes()
        board = after.decode("utf-8")
        self.assertIn(f"- Tamsin Orrow wants rye on Fridays (as of 2051-03-02; source `{TAMSIN}`)", board)
        self.assertIn(f"- Tamsin Orrow and Bram Eskell came in (as of 2051-03-20; sources `{TAMSIN}`, `{BRAM}`)",
                      board)
        self.assertIn(self.LINES[2], board)
        self.assertIn(f"- Tam asked first (as of 2051-02-01; source `{TAMSIN}`)", board)
        old_lines, new_lines = before.decode("utf-8").split("\n"), board.split("\n")
        self.assertEqual(len(old_lines), len(new_lines))
        for old, new in zip(old_lines, new_lines):
            if old != new:
                self.assertEqual(old[: old.index(" (as of ")], new[: new.index(" (as of ")])
        self.assertNotIn("## Update record", board)
        copy = archive.latest(root, BOARD)
        self.assertIsNotNone(copy)
        self.assertEqual(archive.read_copy(root, copy), before)
        self.assertEqual(gzip.decompress((root / ".forest/archive" / copy.archive_file).read_bytes()), before)
        stale_now = stale(root)
        self.assertEqual(len(stale_now), 1, stale_now)  # the truly stale line only
        again = collections.cite_entries(root)
        self.assertFalse(again["written"])
        self.assertEqual((root / BOARD).read_bytes(), after)
        self.assertEqual(again["references_rewritten"], 0)
