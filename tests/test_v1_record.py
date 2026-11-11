from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v1_checks_support import STAND, CheckForestTestCase, leaf  # noqa: E402
from v1_support import REPOSITORY_ROOT, SYNTHETIC_FOREST  # noqa: E402

from memory_forest import archive, record  # noqa: E402
from memory_forest.archive import ArchiveProofError  # noqa: E402
from memory_forest.config import DEFAULT_CONFIG, parse_config  # noqa: E402
from memory_forest.model import RecordPointerLine, parse_page  # noqa: E402
from memory_forest.record import Kind, classify  # noqa: E402

STAND_UP = "03 mtm/workshop/truing-stand"
CONFIG = parse_config('[owner]\nid = "tobin-reel"\nname = "Tobin"\naliases = ["Tobin Reel"]\n')


def forest(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    environment = dict(os.environ, PYTHONPATH=str(REPOSITORY_ROOT / "src"))
    return subprocess.run(
        [sys.executable, "-c", "from memory_forest.cli import forest_main; raise SystemExit(forest_main())", *arguments],
        capture_output=True,
        env=environment,
        cwd=REPOSITORY_ROOT,
        check=False,
    )


def change(day: str, width: int = 60) -> str:
    return f'- {day} — clamp torque: was "{"a" * width}", now "b".'


class ClassifyTests(unittest.TestCase):
    def test_a_change_quotes_what_was_and_what_is(self) -> None:
        self.assertIs(classify(change("2051-03-02"), config=CONFIG), Kind.CHANGE)
        self.assertIs(classify('- 2051-03-02 — clamp: was "loose", now "tight". Why: worn. Source: `04 stm/x/y/z.md`.',
                               config=CONFIG), Kind.CHANGE)

    def test_the_owner_s_words_make_a_removal_or_correction(self) -> None:
        line = '- 2051-03-04 — the old rim taken out. Why: Tobin, in the shop chat: "that rim was never mine".'
        self.assertIs(classify(line, config=CONFIG), Kind.OWNER_WORDS)
        corrected = '- 2051-03-04 — wheel size: was "26", now "27.5". Why: Tobin Reel said “it is a 27.5”.'
        self.assertIs(classify(corrected, config=CONFIG), Kind.OWNER_WORDS)

    def test_quotes_of_the_page_s_own_statements_are_not_the_owner_s_words(self) -> None:
        line = '- 2051-03-04 — Tobin\'s clamp: was "loose", now "tight".'
        self.assertIs(classify(line, config=CONFIG), Kind.CHANGE)
        far = '- 2051-03-04 — Tobin asked about it. The note reads "old".'
        self.assertIsNone(classify(far, config=CONFIG))

    def test_the_owner_s_name_forms_and_quote_marks_are_settings(self) -> None:
        line = "- 2051-03-04 — the rim taken out at the boss's word: «not mine»."
        self.assertIsNone(classify(line, config=CONFIG))
        custom = parse_config('[record]\nowner_word_names = ["the boss"]\nquote_marks = ["«»"]\n')
        self.assertIs(classify(line, config=custom), Kind.OWNER_WORDS)

    def test_where_lines_came_from_or_went(self) -> None:
        self.assertIs(classify("- 2051-03-04 — the clamp lines moved to `04 stm/workshop/truing-stand/clamps.md`.",
                               config=CONFIG), Kind.PROVENANCE)
        self.assertIs(classify("- Earlier record lines (2051-01-01 – 2051-02-01, 4 lines) are kept in "
                               "`.forest/archive/2051-03-01/04 stm/a/b/c.md.gz`.", config=CONFIG), Kind.PROVENANCE)
        self.assertIs(classify("- 2051-03-04 — copied from `.forest/archive/2051-03-01/04 stm/a/b/c.md.gz`.",
                               config=CONFIG), Kind.PROVENANCE)

    def test_run_notes(self) -> None:
        for line in (
            "- 2051-03-04 — rechecked the page; nothing changed.",
            "- 2051-03-04 — 12 lines rewritten. Source: `05 daily/2051-03-04.md`.",
            "- set `kind:` on the page",
            "plain text",
        ):
            self.assertIsNone(classify(line, config=CONFIG), line)


class TrimTests(CheckForestTestCase):
    def page_with(self, lines: list[str]) -> Path:
        return self.workshop({STAND: leaf("truing-stand", STAND_UP, record="\n".join(lines))})

    def record_of(self, root: Path) -> list[str]:
        page = parse_page((root / STAND).read_text(encoding="utf-8"))
        return record.record_lines(page)

    def test_over_the_budget_the_newest_lines_and_the_owner_s_words_stay(self) -> None:
        owner = '- 2051-01-01 — the old rim taken out. Why: Tobin said "never mine".'
        old = [change(f"2051-01-{day:02d}") for day in range(2, 28)]
        new = [change(f"2051-02-{day:02d}") for day in range(1, 10)]
        note = "- 2051-02-10 — rechecked, unchanged."
        lines = [owner, *old, owner, *new, note]
        root = self.page_with(lines)
        before = (root / STAND).read_text(encoding="utf-8")
        result = record.trim_page(root, STAND)
        self.assertTrue(result.written)
        kept = self.record_of(root)
        pointer = RecordPointerLine.parse(kept[0])
        self.assertEqual(pointer.path, result.archive_path)
        self.assertEqual(kept[1], owner)
        budget = DEFAULT_CONFIG.limits.record_keep_bytes
        self.assertLessEqual(sum(len(line.encode()) + 1 for line in kept[2:]), budget)
        self.assertEqual(kept[-1], new[-1])
        self.assertNotIn(note, kept)
        moved = set(lines) - set(kept)
        self.assertIn(note, moved)
        self.assertIn(old[0], moved)
        self.assertEqual(pointer.count, len(lines) - len(kept) + 1)
        self.assertEqual((pointer.first, pointer.last), ("2051-01-01", "2051-02-10"))
        copy = archive.read_copy(root, archive.latest(root, STAND)).decode("utf-8")
        self.assertEqual(copy, before)
        self.assertEqual(parse_page((root / STAND).read_text(encoding="utf-8")).content_text(),
                         parse_page(before).content_text())

    def test_under_the_budget_only_run_notes_move(self) -> None:
        lines = [change("2051-03-01", 10), "- 2051-03-02 — rechecked, unchanged.", change("2051-03-03", 10)]
        root = self.page_with(lines)
        result = record.trim_page(root, STAND)
        self.assertEqual(result.plan.moved, (lines[1],))
        kept = self.record_of(root)
        self.assertEqual(kept[1:], [lines[0], lines[2]])

    def test_a_page_nothing_moves_on_is_left_untouched(self) -> None:
        root = self.page_with([change("2051-03-01", 10)])
        before = (root / STAND).read_bytes()
        result = record.trim_page(root, STAND)
        self.assertFalse(result.written)
        self.assertEqual((root / STAND).read_bytes(), before)
        self.assertEqual(archive.entries(root), [])

    def test_the_proof_fails_when_the_copy_lacks_a_moved_line(self) -> None:
        lines = [change("2051-03-01", 10), "- 2051-03-02 — rechecked, unchanged."]
        root = self.page_with(lines)
        before = (root / STAND).read_bytes()

        def lossy_put(forest_root, path, **options):
            data = before.decode("utf-8").replace(lines[1] + "\n", "").encode("utf-8")
            return archive.store(forest_root, path, data, reason=options["reason"], lock=options.get("lock"))

        with mock.patch.object(archive, "put", side_effect=lossy_put):
            with self.assertRaises(ArchiveProofError):
                record.trim_page(root, STAND)
        self.assertEqual((root / STAND).read_bytes(), before)

    def test_a_second_trim_adds_its_pointer_under_the_first(self) -> None:
        root = self.page_with([change(f"2051-01-{day:02d}") for day in range(1, 29)])
        first = record.trim_page(root, STAND)
        text = (root / STAND).read_text(encoding="utf-8")
        more = "\n".join(change(f"2051-02-{day:02d}") for day in range(1, 29))
        (root / STAND).write_text(text.rstrip("\n") + "\n" + more + "\n", encoding="utf-8")
        second = record.trim_page(root, STAND)
        self.assertTrue(second.written)
        kept = self.record_of(root)
        self.assertEqual(kept[0], first.pointer)
        self.assertEqual(kept[1], second.pointer)
        self.assertNotEqual(first.archive_path, second.archive_path)
        archived = record.archived_record_lines(root, STAND)
        days = [line.date for line in archived]
        self.assertEqual(days, sorted(days))
        self.assertIn("2051-01-01", days)
        self.assertIn("2051-02-01", days)
        self.assertFalse(set(kept) & {line.render() for line in archived})
        again = record.trim_page(root, STAND)
        self.assertFalse(again.written)

    def test_dry_run_writes_nothing(self) -> None:
        root = self.page_with([change(f"2051-01-{day:02d}") for day in range(1, 29)])
        before = (root / STAND).read_bytes()
        results = record.trim(root, [STAND], dry_run=True)
        self.assertTrue(results[0].plan.applies)
        self.assertEqual((root / STAND).read_bytes(), before)
        self.assertEqual(archive.entries(root), [])

    def test_size_and_kinds(self) -> None:
        root = self.page_with([change("2051-03-01", 10), "- 2051-03-02 — rechecked."])
        size = record.size(root, STAND)
        self.assertEqual(size["kinds"], {"change": 1, "owner-words": 0, "provenance": 0, "run-note": 1})
        self.assertEqual(size["due"], ["trim"])
        report = record.kinds(root, examples=1)
        self.assertEqual(report.totals["run-note"], 1)
        self.assertEqual(report.pages[STAND]["change"], 1)
        self.assertEqual(report.examples["run-note"][0][2], "- 2051-03-02 — rechecked.")


class RecordCommandTests(CheckForestTestCase):
    def test_the_synthetic_forest_has_no_run_note(self) -> None:
        result = forest("record", "kinds", "--root", str(SYNTHETIC_FOREST), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["totals"]["run-note"], 0)

    def test_trim_all_and_size(self) -> None:
        root = self.workshop({STAND: leaf("truing-stand", STAND_UP,
                                          record="\n".join(change(f"2051-01-{day:02d}") for day in range(1, 29)))})
        planned = forest("record", "trim", "--all", "--dry-run", "--root", str(root))
        self.assertEqual(planned.returncode, 0, planned.stderr)
        self.assertIn(b"would move", planned.stdout)
        done = forest("record", "trim", "--all", "--root", str(root), "--json")
        self.assertEqual(done.returncode, 0, done.stderr)
        pages = json.loads(done.stdout)["pages"]
        self.assertEqual([page["path"] for page in pages], [STAND])
        size = forest("record", "size", STAND, "--root", str(root))
        self.assertIn(b"due: nothing", size.stdout)
        refused = forest("record", "trim", "--root", str(root))
        self.assertEqual(refused.returncode, 2)


if __name__ == "__main__":
    unittest.main()
