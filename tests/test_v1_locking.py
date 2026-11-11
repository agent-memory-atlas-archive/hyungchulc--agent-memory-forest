from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v1_support import REPOSITORY_ROOT, ForestTestCase  # noqa: E402

from memory_forest.errors import MemoryForestError  # noqa: E402
from memory_forest.locking import (  # noqa: E402
    append_lines,
    atomic_write,
    hold_lock,
    holds,
    lock_status,
    stage_lock,
    writer_lock,
)

CHILD = textwrap.dedent(
    """
    import sys
    from memory_forest.errors import MemoryForestError
    from memory_forest.locking import writer_lock
    root, token = sys.argv[1], sys.argv[2]
    try:
        with writer_lock(root, token=token or None):
            print("inside")
    except MemoryForestError as exc:
        print(exc.code)
    """
)


def run_child(root: Path, token: str) -> str:
    environment = dict(os.environ, PYTHONPATH=str(REPOSITORY_ROOT / "src"))
    completed = subprocess.run(
        [sys.executable, "-c", CHILD, str(root), token],
        capture_output=True,
        text=True,
        env=environment,
        check=True,
    )
    return completed.stdout.strip()


class WriterLockTests(ForestTestCase):
    def test_one_writer_at_a_time(self) -> None:
        root = self.make_forest()
        with writer_lock(root, label="first") as token:
            self.assertEqual(holds(root), token)
            self.assertEqual(run_child(root, ""), "lock_busy")
            status = lock_status(root)
            self.assertTrue(status.held)
            self.assertFalse(status.stale)
            self.assertEqual(status.owner["pid"], os.getpid())
            self.assertEqual(status.owner["label"], "first")
            self.assertNotIn("token", status.owner)
        self.assertIsNone(holds(root))
        self.assertEqual(run_child(root, ""), "inside")

    def test_a_holder_re_enters_only_with_its_token(self) -> None:
        root = self.make_forest()
        with writer_lock(root) as token:
            with writer_lock(root, token=token) as inner:
                self.assertEqual(inner, token)
            with writer_lock(root, token=token.value):
                pass
            self.assertEqual(holds(root), token)  # the inner block released nothing
            with self.assertRaises(MemoryForestError) as caught:
                with writer_lock(root):
                    pass
            self.assertEqual(caught.exception.code, "lock_already_held")
            with self.assertRaises(MemoryForestError) as caught:
                with writer_lock(root, token="0" * 32):
                    pass
            self.assertEqual(caught.exception.code, "lock_token_invalid")

    def test_a_child_process_works_under_its_parents_hold(self) -> None:
        root = self.make_forest()
        with writer_lock(root) as token:
            self.assertEqual(run_child(root, token.value), "inside")
            self.assertEqual(run_child(root, "f" * 32), "lock_token_invalid")
        self.assertEqual(run_child(root, token.value), "lock_token_invalid")

    def test_a_token_dies_with_its_hold(self) -> None:
        root = self.make_forest()
        with writer_lock(root) as token:
            pass
        with self.assertRaises(MemoryForestError):
            with writer_lock(root, token=token):
                pass

    def test_a_lock_left_by_a_dead_process_is_stale_and_taken(self) -> None:
        root = self.make_forest()
        with writer_lock(root):
            pass
        lock_file = root / ".forest" / "locks" / "writer.lock"
        dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
        lock_file.write_text(
            json.dumps({"host": socket.gethostname(), "label": "crashed", "pid": int(dead.stdout), "token": "a" * 32}),
            encoding="utf-8",
        )
        status = lock_status(root)
        self.assertFalse(status.held)
        self.assertTrue(status.stale)
        with writer_lock(root, label="after") as token:
            self.assertEqual(lock_status(root).owner["label"], "after")
            self.assertNotEqual(token.value, "a" * 32)
        self.assertFalse(lock_status(root).stale)

    def test_stage_locks_are_separate_from_the_writer_lock(self) -> None:
        root = self.make_forest()
        with writer_lock(root):
            with stage_lock(root, "daily") as token:
                self.assertEqual(token.name, "stage-daily")
                with self.assertRaises(MemoryForestError):
                    with stage_lock(root, "daily"):
                        pass
            with stage_lock(root, "filing"):
                pass
        with self.assertRaises(MemoryForestError):
            with hold_lock(root, "Not A Slug"):
                pass

    def test_the_lock_folder_cannot_be_a_symlink(self) -> None:
        root = self.make_forest()
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        (root / ".forest").mkdir()
        os.symlink(elsewhere, root / ".forest" / "locks")
        with self.assertRaises(MemoryForestError):
            with writer_lock(root):
                pass


class WriteHelperTests(ForestTestCase):
    def test_atomic_write_replaces_whole_and_keeps_the_mode(self) -> None:
        target = self.base / "page.md"
        atomic_write(target, "first\n")
        self.assertEqual(target.read_text(encoding="utf-8"), "first\n")
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        target.chmod(0o640)
        atomic_write(target, b"second\n")
        self.assertEqual(target.read_bytes(), b"second\n")
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)
        self.assertEqual(sorted(path.name for path in self.base.iterdir()), ["page.md"])

    def test_atomic_write_refuses_a_symlink(self) -> None:
        real = self.base / "real.md"
        real.write_text("keep\n", encoding="utf-8")
        link = self.base / "link.md"
        link.symlink_to(real)
        with self.assertRaises(MemoryForestError):
            atomic_write(link, "replaced\n")
        self.assertEqual(real.read_text(encoding="utf-8"), "keep\n")

    def test_append_lines_adds_whole_lines(self) -> None:
        target = self.base / "log.jsonl"
        append_lines(target, ['{"a": 1}', '{"b": 2}\n'])
        append_lines(target, [])
        append_lines(target, ['{"c": 3}'])
        self.assertEqual(target.read_text(encoding="utf-8"), '{"a": 1}\n{"b": 2}\n{"c": 3}\n')


if __name__ == "__main__":
    unittest.main()
