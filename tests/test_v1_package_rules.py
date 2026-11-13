"""Rules that hold for every module of the package, read from the syntax tree.

Nothing here names a person or a forest.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import memory_forest
from memory_forest import search


PACKAGE = Path(memory_forest.__file__).resolve().parent


def modules() -> list[tuple[str, ast.Module]]:
    """Every module of the package, by its path under the package."""
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        found.append((str(path.relative_to(PACKAGE)), ast.parse(path.read_text(encoding="utf-8"))))
    return found


class PackageRuleTests(unittest.TestCase):
    def test_the_walk_reads_every_module(self) -> None:
        names = [name for name, _tree in modules()]
        self.assertIn("search.py", names)
        self.assertIn("adapters/jsonl.py", names)
        self.assertGreater(len(names), 40)

    def test_no_object_is_kept_beside_another_by_its_address(self) -> None:
        """A flag or a record keyed by ``id()`` outlives the object and is found again by a stranger."""
        calls = [
            f"{name}:{node.lineno}"
            for name, tree in modules()
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "id"
        ]
        self.assertEqual(calls, [])

    def test_no_default_argument_names_the_first_index_file(self) -> None:
        """The current index file is read from the pointer every time. A default would freeze the first name."""
        first = {search.INDEX_FILENAME, search.INDEX_RELATIVE}
        found = []
        for name, tree in modules():
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    continue
                for default in [*node.args.defaults, *node.args.kw_defaults]:
                    if default is None:
                        continue
                    literal = isinstance(default, ast.Constant) and default.value in first
                    named = isinstance(default, (ast.Name, ast.Attribute)) and (
                        getattr(default, "id", None) in {"INDEX_FILENAME", "INDEX_RELATIVE"}
                        or getattr(default, "attr", None) in {"INDEX_FILENAME", "INDEX_RELATIVE"}
                    )
                    if literal or named:
                        found.append(f"{name}:{default.lineno}")
        self.assertEqual(found, [])


class WaitChildTests(unittest.TestCase):
    """A failed wait ends a child that is still alive before it reads the child's stderr."""

    def test_a_wait_on_a_live_child_that_writes_nothing_ends_at_its_deadline(self) -> None:
        from wait_child import read_fifo

        with tempfile.TemporaryDirectory() as scratch:
            fifo = Path(scratch) / "never-written"
            os.mkfifo(fifo)
            # The child blocks on its own stdin: it neither exits nor writes, until it is ended.
            child = subprocess.Popen(
                [sys.executable, "-c", "import sys; sys.stdin.read()"],
                stdin=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            outcome: list[BaseException] = []
            done = threading.Event()

            def wait() -> None:
                try:
                    read_fifo(child, str(fifo), what="a handshake nobody sends", seconds=0.2)
                except BaseException as exc:
                    outcome.append(exc)
                finally:
                    done.set()

            threading.Thread(target=wait, daemon=True).start()
            try:
                self.assertTrue(done.wait(20), "the failed wait read a live child's stderr and did not end")
                self.assertIsInstance(outcome[0], AssertionError)
                self.assertIn("a handshake nobody sends", str(outcome[0]))
                self.assertIsNotNone(child.poll())
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=30)
                if child.stdin is not None:
                    child.stdin.close()
                if child.stderr is not None:
                    child.stderr.close()


if __name__ == "__main__":
    unittest.main()
