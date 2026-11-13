"""The commit journal: a commit completes, or the forest stays as it was, even when its process is killed.

A child process runs one move on a made-up forest and exits hard at an
injected point; the parent then takes the writer lock, which rolls the
journal back, and the forest's files and the move log are compared byte for
byte with what they were before.
"""

from __future__ import annotations

import bounds  # every invocation has a deadline

import contextlib
import io
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v1_move import MILLSTONE, REPAIRS, TODAY, MoveTestCase  # noqa: E402
from v1_support import REPOSITORY_ROOT  # noqa: E402

from memory_forest import journal, move, movelog  # noqa: E402
from memory_forest.checks import run_checks  # noqa: E402
from memory_forest.config import load_config  # noqa: E402
from memory_forest.errors import MemoryForestError  # noqa: E402
from memory_forest.locking import writer_lock  # noqa: E402

# The child: count page writes and exit hard (no clean-up, no finally) at the named point.
CHILD = textwrap.dedent("""
    import os, sys
    from memory_forest import move, movelog, plan
    root, point = sys.argv[1], sys.argv[2]
    writes = {"n": 0}
    original_write, original_append = plan.atomic_write, movelog.append
    def write(path, data, **options):
        original_write(path, data, **options)
        writes["n"] += 1
        if point == "first-write" and writes["n"] == 1:
            os._exit(9)
    def append(*arguments, **options):
        if point == "last-write":
            os._exit(9)
        count = original_append(*arguments, **options)
        if point == "move-log":
            os._exit(9)
        return count
    plan.atomic_write, movelog.append = write, append
    move.execute(root, "promote", subject="millstone-flour", today="2051-03-05")
""")


def files(root: Path) -> dict[str, bytes]:
    """Every file of the forest with its bytes, the move log included; locks, journal and registry aside."""
    found = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_file() and not relative.startswith((".forest/locks", ".forest/journal", ".forest/registry")):
            found[relative] = path.read_bytes()
    return found


def folders(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*")
                  if path.is_dir() and ".forest" not in path.relative_to(root).parts)


class JournalTests(MoveTestCase):
    def forest_with_history(self) -> Path:
        root = self.forest()
        move.execute(root, "leaf", source=REPAIRS, target="04 stm/bakery/deck-oven/fixes.md", today=TODAY)
        return root

    def kill_at(self, root: Path, point: str) -> None:
        result = subprocess.run([sys.executable, "-c", CHILD, str(root), point], capture_output=True,
                                env={**os.environ, "PYTHONPATH": str(REPOSITORY_ROOT / "src")})
        self.assertEqual(result.returncode, 9, result.stderr)

    def test_a_kill_at_each_point_leaves_the_forest_as_it_was(self) -> None:
        for point in ("first-write", "last-write", "move-log"):
            with self.subTest(point=point):
                root = self.forest_with_history()
                before, before_folders = files(root), folders(root)
                self.kill_at(root, point)
                self.assertTrue(Path(journal.journal_path(root)).is_file())
                self.assertNotEqual(files(root), before)
                problems = [problem.code for problem in run_checks(root, load_config(root), ["journal"])[1]]
                self.assertEqual(problems, [journal.STANDING_CODE])
                said = io.StringIO()
                with contextlib.redirect_stderr(said), writer_lock(root, label="the next writer"):
                    pass
                self.assertIn("rolled back an unfinished commit", said.getvalue())
                self.assertFalse(Path(journal.journal_path(root)).exists())
                self.assertEqual(files(root), before)
                self.assertEqual(folders(root), before_folders)
                self.assertEqual(movelog.resolve(root, MILLSTONE).path, MILLSTONE)
                os.rename(root, root.with_name(f"done-{point}"))

    def test_an_interrupted_roll_back_runs_again(self) -> None:
        root = self.forest_with_history()
        before = files(root)
        self.kill_at(root, "move-log")
        writes = {"n": 0}
        original = journal.atomic_write

        def stop_after_one(path, data, **options):
            if writes["n"] == 1:
                raise KeyboardInterrupt
            writes["n"] += 1
            original(path, data, **options)

        with mock.patch.object(journal, "atomic_write", stop_after_one):
            with self.assertRaises(KeyboardInterrupt), contextlib.redirect_stderr(io.StringIO()):
                with writer_lock(root):
                    pass
        self.assertTrue(Path(journal.journal_path(root)).is_file())
        result = subprocess.run(
            [sys.executable, "-c", "from memory_forest.cli import forest_main; raise SystemExit(forest_main())",
             "recover", "--root", str(root)], capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(REPOSITORY_ROOT / "src")})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rolled back an unfinished commit", result.stderr)
        self.assertEqual(files(root), before)

    def test_a_commit_that_succeeds_leaves_no_journal(self) -> None:
        root = self.forest()
        move.execute(root, "promote", subject="millstone-flour", today=TODAY)
        self.assertFalse(Path(journal.journal_path(root)).exists())
        self.assertEqual(run_checks(root, load_config(root), ["journal"])[1], [])

    def test_a_failing_move_log_append_puts_every_page_back(self) -> None:
        root = self.forest_with_history()
        before, before_folders = files(root), folders(root)

        def broken(*_arguments, **_options):
            raise OSError("the disk is full")

        with mock.patch.object(movelog, "append", broken):
            with self.assertRaises(OSError):
                move.execute(root, "promote", subject="millstone-flour", today=TODAY)
        self.assertFalse(Path(journal.journal_path(root)).exists())
        self.assertEqual(files(root), before)
        self.assertEqual(folders(root), before_folders)

    def test_an_unreadable_journal_is_refused_and_kept(self) -> None:
        root = self.forest()
        Path(journal.journal_path(root)).parent.mkdir(parents=True, exist_ok=True)
        Path(journal.journal_path(root)).write_bytes(b"not json")
        with self.assertRaises(MemoryForestError):
            with writer_lock(root):
                pass
        self.assertTrue(Path(journal.journal_path(root)).is_file())

    def test_a_journal_of_an_unknown_schema_stops_the_next_writer_before_any_write(self) -> None:
        root = self.forest_with_history()
        path = Path(journal.journal_path(root))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"schema": "memory-forest-journal/99", "pages": {}}', encoding="ascii")
        before = files(root)
        with self.assertRaises(MemoryForestError) as caught:
            move.execute(root, "promote", subject="millstone-flour", today=TODAY)
        self.assertEqual(caught.exception.code, "journal_invalid")
        self.assertEqual(files(root), before)
        self.assertTrue(path.is_file())

    def test_a_copied_forest_with_a_standing_journal_rolls_back_only_itself(self) -> None:
        root = self.forest_with_history()
        before = files(root)
        self.kill_at(root, "move-log")
        half = files(root)
        copy = root.with_name("copy")
        shutil.copytree(root, copy)
        with contextlib.redirect_stderr(io.StringIO()), writer_lock(copy, label="the copy's writer"):
            pass
        self.assertFalse(Path(journal.journal_path(copy)).exists())
        self.assertEqual(files(copy), before)
        self.assertTrue(Path(journal.journal_path(root)).is_file())  # the original keeps its journal and its pages
        self.assertEqual(files(root), half)

