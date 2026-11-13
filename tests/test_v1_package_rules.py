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
from memory_forest import errors, search


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


# ─────────────────────────── the codes that can leave ───────────────────────────

# A raise whose code is not a literal and is not a parameter or a local the walk can follow. Each entry
# says the set of values the code can take there.
NON_LITERAL = {
    ("search.py", "index_error"): lambda: {code for code, _message in search._PERSON_MESSAGES.values()},
    ("search.py", "_open_regular"): lambda: {code for code, _message in search._PERSON_MESSAGES.values()},
}


class _Unresolved(Exception):
    pass


class _Package:
    """Every module's syntax tree, each node's function, and the imports that bring a name in."""

    def __init__(self) -> None:
        self.trees = dict(modules())
        self.owner: dict[tuple[str, int], ast.AST] = {}
        self.functions: dict[str, list[tuple[str, ast.FunctionDef]]] = {}
        self.imported: dict[str, set[tuple[str, str]]] = {}
        for name, tree in self.trees.items():
            imports: set[tuple[str, str]] = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.functions.setdefault(node.name, []).append((name, node))
                    for child in ast.walk(node):
                        self.owner[(name, id_of(child))] = node
                if isinstance(node, ast.ImportFrom) and node.module:
                    for alias in node.names:
                        imports.add((node.module.split(".")[-1], alias.asname or alias.name))
            self.imported[name] = imports

    def enclosing(self, module: str, node: ast.AST) -> ast.FunctionDef | None:
        return self.owner.get((module, id_of(node)))  # type: ignore[return-value]

    def values(self, module: str, node: ast.expr, depth: int = 0) -> set[str]:
        if depth > 5:
            raise _Unresolved("too deep")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.IfExp):
            return self.values(module, node.body, depth + 1) | self.values(module, node.orelse, depth + 1)
        if isinstance(node, ast.Name):
            function = self.enclosing(module, node)
            if function is None:
                raise _Unresolved(node.id)
            parameters = [*function.args.posonlyargs, *function.args.args]
            names = [item.arg for item in parameters]
            keyword_only = [item.arg for item in function.args.kwonlyargs]
            if node.id in names or node.id in keyword_only:
                return self._from_callers(module, function, node.id, depth)
            found: set[str] = set()
            assigned = False
            for child in ast.walk(function):
                if isinstance(child, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == node.id for target in child.targets
                ):
                    found |= self.values(module, child.value, depth + 1)
                    assigned = True
                elif isinstance(child, ast.Assign) and any(
                    isinstance(target, ast.Tuple) and any(isinstance(e, ast.Name) and e.id == node.id for e in target.elts)
                    for target in child.targets
                ):
                    raise _Unresolved(node.id)
            if not assigned:
                raise _Unresolved(node.id)
            return found
        raise _Unresolved(ast.unparse(node))

    def _from_callers(self, module: str, function: ast.FunctionDef, parameter: str, depth: int) -> set[str]:
        parameters = [item.arg for item in [*function.args.posonlyargs, *function.args.args]]
        found: set[str] = set()
        defaults = dict(zip(parameters[len(parameters) - len(function.args.defaults):], function.args.defaults))
        defaults.update(
            {item.arg: value for item, value in zip(function.args.kwonlyargs, function.args.kw_defaults) if value}
        )
        if parameter in defaults:
            found |= self.values(module, defaults[parameter], depth + 1)
        stem = module.rsplit("/", 1)[-1].removesuffix(".py")
        for caller_module, tree in self.trees.items():
            for call in ast.walk(tree):
                if not isinstance(call, ast.Call):
                    continue
                func = call.func
                same = isinstance(func, ast.Name) and func.id == function.name and (
                    caller_module == module or (stem, function.name) in self.imported[caller_module]
                )
                attribute = (
                    isinstance(func, ast.Attribute)
                    and func.attr == function.name
                    and isinstance(func.value, ast.Name)
                    and func.value.id == stem
                )
                if not (same or attribute):
                    continue
                argument = next((k.value for k in call.keywords if k.arg == parameter), None)
                if argument is None and parameter in parameters:
                    index = parameters.index(parameter)
                    if index < len(call.args):
                        argument = call.args[index]
                if argument is None:
                    continue
                found |= self.values(caller_module, argument, depth + 1)
        return found


def id_of(node: ast.AST) -> tuple[int, int, int, int]:
    """A node by where it stands, so no table is keyed by an object's address."""
    return (
        getattr(node, "lineno", -1), getattr(node, "col_offset", -1),
        getattr(node, "end_lineno", -1), getattr(node, "end_col_offset", -1),
    )


def raised_codes() -> tuple[dict[str, set[str]], dict[str, set[str]], list[str]]:
    """Every code a ``MemoryForestError`` can be made with, where, and every literal next move of a site."""
    package = _Package()
    codes: dict[str, set[str]] = {}
    nexts: dict[str, set[str]] = {}
    refused: list[str] = []
    for module, tree in package.trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if getattr(func, "id", getattr(func, "attr", None)) != "MemoryForestError":
                continue
            argument = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "code"), None)
            where = f"{module}:{node.lineno}"
            try:
                values = package.values(module, argument) if argument is not None else set()
            except _Unresolved:
                function = package.enclosing(module, node)
                key = (module, getattr(function, "name", ""))
                if key not in NON_LITERAL:
                    refused.append(where)
                    continue
                values = NON_LITERAL[key]()
            for value in values:
                codes.setdefault(value, set()).add(where)
            step = next((k.value for k in node.keywords if k.arg == "next_step"), None)
            if isinstance(step, ast.Constant) and step.value is not None:
                for value in values:
                    nexts.setdefault(value, set()).add(str(step.value))
    return codes, nexts, refused


def document_rows() -> dict[str, tuple[str, str]]:
    """``docs/refusals.md``: code, next move and command of each row."""
    rows: dict[str, tuple[str, str]] = {}
    text = (PACKAGE.parents[1] / "docs" / "refusals.md").read_text(encoding="utf-8")
    for line in text.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows[cells[0].strip("`")] = (cells[1].strip("`"), cells[2].strip("`"))
    return rows


class RefusalCodeTests(unittest.TestCase):
    """The codes that can leave are one closed set: the code, the table in the code and the document agree."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.codes, cls.nexts, cls.refused = raised_codes()

    def test_no_raise_has_a_code_the_walk_cannot_name(self) -> None:
        self.assertEqual(self.refused, [], "a non-literal code: make it literal, or list it with its values")

    def test_the_codes_raised_and_the_table_are_the_same_set(self) -> None:
        raised = set(self.codes) - set(search.INTERNAL_CODES)
        self.assertEqual(sorted(raised - set(errors.REFUSALS)), [], "raised but not in errors.REFUSALS")
        self.assertEqual(sorted(set(errors.REFUSALS) - raised), [], "in errors.REFUSALS but never raised")

    def test_the_document_is_the_table(self) -> None:
        rows = document_rows()
        self.assertEqual(sorted(set(rows) - set(errors.REFUSALS)), [])
        self.assertEqual(sorted(set(errors.REFUSALS) - set(rows)), [])
        for code, (nxt, command) in errors.REFUSALS.items():
            self.assertEqual(rows[code], (nxt, command), code)

    def test_every_code_has_a_next_move_and_a_site_never_says_another(self) -> None:
        for code, (nxt, command) in errors.REFUSALS.items():
            self.assertIn(nxt, errors.NEXT_MOVES, code)
            self.assertEqual(bool(command), nxt == "run", code)
        for code, said in self.nexts.items():
            if code in search.INTERNAL_CODES:
                continue
            self.assertEqual(said, {errors.REFUSALS[code][0]}, code)
        for code in errors.REFUSALS:
            made = errors.MemoryForestError(code, "x")
            self.assertEqual(made.as_dict()["next"], errors.REFUSALS[code][0], code)

    def test_an_internal_code_is_not_in_the_table(self) -> None:
        self.assertIn("index_protocol", self.codes)
        self.assertTrue(set(search.INTERNAL_CODES).isdisjoint(errors.REFUSALS))

    def test_the_index_table_of_the_tools_document_agrees(self) -> None:
        for code, nxt, command in search.INDEX_REFUSALS:
            self.assertEqual(errors.REFUSALS[code], (nxt, command), code)


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
