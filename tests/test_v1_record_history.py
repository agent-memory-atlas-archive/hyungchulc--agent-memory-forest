"""The record history file: a trim's cost follows what moves, and a page holds one pointer for life."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v1_record import STAND_UP, change  # noqa: E402
from v1_checks_support import STAND, CheckForestTestCase, leaf  # noqa: E402

from memory_forest import archive, move, record  # noqa: E402
from memory_forest.config import DEFAULT_CONFIG  # noqa: E402
from memory_forest.model import RecordPointerLine, parse_page  # noqa: E402

FIXES = "04 stm/workshop/truing-stand/fixes.md"


class RecordHistoryTests(CheckForestTestCase):
    def page_with(self, lines: list[str]) -> Path:
        return self.workshop({STAND: leaf("truing-stand", STAND_UP, record="\n".join(lines))})

    def record_of(self, root: Path, path: str = STAND) -> list[str]:
        return record.record_lines(parse_page((root / path).read_text(encoding="utf-8")))

    def add_line(self, root: Path, line: str, path: str = STAND) -> None:
        text = (root / path).read_text(encoding="utf-8")
        (root / path).write_text(text.rstrip("\n") + "\n" + line + "\n", encoding="utf-8")

    def test_the_block_holds_the_moved_lines_and_the_page_the_kept_ones(self) -> None:
        lines = [change(f"2051-01-{day:02d}") for day in range(1, 29)]
        root = self.page_with(lines)
        before = (root / STAND).read_bytes()
        result = record.trim_page(root, STAND)
        kept = self.record_of(root)
        moved = list(result.plan.moved)
        self.assertEqual(sorted([*kept[1:], *moved]), sorted(lines))
        self.assertEqual(moved, [line for line in lines if line in moved])
        history = (root / result.archive_path).read_bytes()
        header = history.split(b"\n", 1)[0].decode()
        self.assertTrue(header.startswith("<!-- record lines moved "), header)
        self.assertIn(f"page sha256 {hashlib.sha256(before).hexdigest()}", header)
        self.assertIn(f"{len(moved)} lines · 2051-01-01 to {result.plan.last}", header)
        self.assertEqual(archive.block_lines(history), moved)
        [block] = archive.blocks(root, STAND)
        self.assertEqual((block.offset, block.length, block.lines), (0, len(history), len(moved)))
        self.assertTrue(result.archive_path.startswith(".forest/archive/record-history/04 stm/workshop/"))

    def test_an_interrupted_trim_does_not_append_its_block_again(self) -> None:
        root = self.page_with([change(f"2051-01-{day:02d}") for day in range(1, 29)])
        before = (root / STAND).read_bytes()

        def killed(*_arguments, **_options):
            raise KeyboardInterrupt

        with mock.patch.object(record, "atomic_write", killed):
            with self.assertRaises(KeyboardInterrupt):
                record.trim_page(root, STAND)
        self.assertEqual((root / STAND).read_bytes(), before)
        [history] = list((root / ".forest/archive/record-history").rglob("*.md"))
        once = history.read_bytes()
        result = record.trim_page(root, STAND)
        self.assertTrue(result.written)
        self.assertEqual(history.read_bytes(), once)
        self.assertEqual(len(archive.blocks(root, STAND)), 1)
        self.assertEqual(root / result.archive_path, history)

    def test_thirty_days_of_one_line_and_one_trim_a_day(self) -> None:
        root = self.page_with([change(f"2051-01-{day:02d}") for day in range(1, 29)])
        record.trim_page(root, STAND)
        budget = DEFAULT_CONFIG.limits.record_keep_bytes
        history = None
        for day in range(1, 31):
            self.add_line(root, change(f"2051-04-{day:02d}"))
            size_before = history.stat().st_size if history else None
            result = record.trim_page(root, STAND)
            history = root / result.archive_path
            kept = self.record_of(root)
            pointers = [line for line in kept if RecordPointerLine.match(line)]
            self.assertEqual(len(pointers), 1)
            self.assertLessEqual(sum(len(line.encode()) + 1 for line in kept if line not in pointers), budget)
            if size_before is not None:
                block = archive.blocks(root, STAND)[-1]
                self.assertEqual(history.stat().st_size, size_before + block.length)
                self.assertEqual(block.lines, len(result.plan.moved))
        self.assertEqual(len(list((root / ".forest/archive/record-history").rglob("*.md"))), 1)
        self.assertEqual(archive.entries(root, STAND), [])
        self.assertEqual(archive.verify(root), [])
        reads = []
        real = archive.read_history

        def counted(forest_root, name):
            reads.append(name)
            return real(forest_root, name)

        with mock.patch.object(archive, "read_history", counted):
            found = record.archived_record_lines(root, STAND)
        self.assertEqual(len(reads), 1)
        self.assertIn("2051-01-01", [line.date for line in found])

    def test_a_moved_page_keeps_its_pointer_and_its_file(self) -> None:
        root = self.page_with([change(f"2051-01-{day:02d}") for day in range(1, 29)])
        first = record.trim_page(root, STAND)
        move.execute(root, "leaf", source=STAND, target=FIXES, today="2051-05-01")
        self.assertEqual(self.record_of(root, FIXES)[0], first.pointer)
        self.assertIn("2051-01-01", [line.date for line in record.archived_record_lines(root, FIXES)])
        for day in range(1, 29):
            self.add_line(root, change(f"2051-06-{day:02d}"), FIXES)
        second = record.trim_page(root, FIXES)
        self.assertEqual(second.archive_path, first.archive_path)
        self.assertEqual([block.path for block in archive.blocks(root)], [STAND, FIXES])

    def test_a_name_another_page_left_takes_the_next_free_name(self) -> None:
        root = self.page_with([change(f"2051-01-{day:02d}") for day in range(1, 29)])
        taken = root / ".forest/archive/record-history" / STAND
        taken.parent.mkdir(parents=True)
        taken.write_bytes(b"<!-- an older page of this path -->\n")
        result = record.trim_page(root, STAND)
        self.assertTrue(result.archive_path.endswith("truing-stand/repairs-2.md"), result.archive_path)

    def test_the_archive_check_finds_a_changed_block(self) -> None:
        root = self.page_with([change(f"2051-01-{day:02d}") for day in range(1, 29)])
        result = record.trim_page(root, STAND)
        history = root / result.archive_path
        history.write_bytes(history.read_bytes().replace(b"clamp torque", b"clamp tork", 1))
        self.assertEqual([problem.code for problem in archive.verify(root)], ["archive_damaged"])

    def test_a_merged_page_keeps_the_pointer_and_a_trim_appends_to_its_file(self) -> None:
        from memory_forest.plan import Plan
        from memory_forest.record import Kind, classify

        root = self.page_with([change(f"2051-01-{day:02d}") for day in range(1, 29)])
        first = record.trim_page(root, STAND)
        (root / FIXES).write_text(leaf("truing-stand", STAND_UP, subtopic="fixes"), encoding="utf-8")
        plan = Plan.open(root, today="2051-05-01")
        move.merge_lines(plan, STAND, FIXES, why="one sub-topic")
        plan.commit()
        kept = self.record_of(root, FIXES)
        self.assertIn(first.pointer, kept)
        self.assertIs(classify(first.pointer, config=DEFAULT_CONFIG), Kind.PROVENANCE)
        for day in range(1, 29):
            self.add_line(root, change(f"2051-06-{day:02d}"), FIXES)
        second = record.trim_page(root, FIXES)
        self.assertEqual(second.archive_path, first.archive_path)
        self.assertIsNone(second.pointer)

    def test_a_page_merged_from_two_trimmed_pages_reads_both_files_once(self) -> None:
        from memory_forest.plan import Plan

        root = self.page_with([change(f"2051-01-{day:02d}") for day in range(1, 29)])
        (root / FIXES).write_text(leaf("truing-stand", STAND_UP, subtopic="fixes", record="\n".join(
            change(f"2051-02-{day:02d}") for day in range(1, 29))), encoding="utf-8")
        first = record.trim_page(root, STAND)
        second = record.trim_page(root, FIXES)
        self.assertNotEqual(first.archive_path, second.archive_path)
        moved = [*first.plan.moved, *second.plan.moved]
        plan = Plan.open(root, today="2051-05-01")
        move.merge_lines(plan, STAND, FIXES, why="one sub-topic")
        plan.commit()
        kept = self.record_of(root, FIXES)
        pointers = [line for line in kept if RecordPointerLine.match(line)]
        self.assertEqual(sorted(pointers), sorted([first.pointer, second.pointer]))
        found = [line.render() for line in record.archived_record_lines(root, FIXES)]
        self.assertEqual(sorted(found), sorted(moved))
        self.assertEqual(len(found), len(set(found)))
        sizes = {name: (root / name).stat().st_size for name in (first.archive_path, second.archive_path)}
        for day in range(1, 29):
            self.add_line(root, change(f"2051-06-{day:02d}"), FIXES)
        third = record.trim_page(root, FIXES)
        target = RecordPointerLine.match(pointers[0]).path
        self.assertEqual(third.archive_path, target)
        self.assertIsNone(third.pointer)
        other = next(name for name in sizes if name != target)
        self.assertEqual((root / other).stat().st_size, sizes[other])
        self.assertGreater((root / target).stat().st_size, sizes[target])
        self.assertEqual(archive.verify(root), [])

    def test_the_history_and_the_listing_from_the_command_line(self) -> None:
        import json
        import subprocess

        from v1_support import REPOSITORY_ROOT

        root = self.page_with([change(f"2051-01-{day:02d}") for day in range(1, 29)])
        trimmed = record.trim_page(root, STAND)
        archive.put(root, STAND, reason="a copy before a test edit", keep=True)

        def forest(*arguments: str) -> str:
            result = subprocess.run(
                [sys.executable, "-c", "from memory_forest.cli import forest_main; raise SystemExit(forest_main())",
                 *arguments, "--root", str(root)], capture_output=True, text=True,
                env={"PYTHONPATH": str(REPOSITORY_ROOT / "src"), "PATH": "/usr/bin:/bin"})
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

        self.assertEqual(forest("record", "history", STAND).splitlines(), list(trimmed.plan.moved))
        self.assertEqual(json.loads(forest("record", "history", STAND, "--json"))["lines"], list(trimmed.plan.moved))
        rows = json.loads(forest("archive", "list", "--json"))["entries"]
        self.assertEqual([row["kind"] for row in rows], ["record-history", "copy"])
        listed = forest("archive", "list").splitlines()
        self.assertIn(f"  record-history  {STAND}  {len(trimmed.plan.moved)} lines 2051-01-01 to ", listed[0])
        self.assertTrue(listed[0].endswith(trimmed.archive_path), listed[0])
        self.assertIn(f"  copy            {STAND}  ", listed[1])
