#!/usr/bin/env python3
r"""Turn each guard of a function round, one at a time, and report which change the tests notice.

A mutation list chosen by hand tests only the guards its writer remembered. This tool reads the
syntax tree of each function it is given (its body, nested functions and lambdas included), or of
a module-level assignment such as a table of lambdas, and makes one changed copy, a mutant, for
every guard in it:

  condition    the test of an if, elif, while, assert or conditional expression, a comprehension
               filter or a case guard, a lambda's body or a return's value that is an and/or, a not
               or a comparison, and each and/or operand inside one: turned round, not (...)
  comparison   one operator turned round: == and !=, < and >=, > and <=, in and not in, is and
               is not (each operator of a chained comparison on its own)
  return       a return that is not the last statement of its function: replaced by pass
  boolean      the value of a return True or return False turned round, the last one included
  raise        a raise: replaced by pass
  loop         a for or while loop, its else included: replaced by pass, so its body never runs
  continue     replaced by pass
  break        replaced by pass

Only the text of the changed node is replaced, so every other line of the module stays as it is.
Each mutant is written into a private copy of the repository (the files git lists, tracked or
not ignored; the working tree is never touched) and the tests named for the function run there,
in a niced child process, one at a time:

  killed       the tests failed or errored: they notice the change
  timeout      the tests ran past their deadline, ten times as long as the unchanged tests took
               (at least 30 s, at most --timeout): counted with killed
  survived     the tests passed: nothing notices this guard
  invalid      the changed module does not compile or import: listed apart, not counted

The unchanged tests run first; if they fail, nothing else runs. Results are kept in a JSON state
file under each mutant's id and the SHA-256 of the target, its function's source and the test
ids, so a second run skips what is recorded and Ctrl-C stops after saving what is done. This is
not part of `make check`: one test class can take minutes, and it runs once per mutant.

Run from the repository root:

  python3 scripts/mutate_guards.py --list \
      --target src/memory_forest/search.py:_probe --tests test_v1_index_state.CatalogueTests
  nice -n 15 python3 scripts/mutate_guards.py --limit 3 \
      --target src/memory_forest/search.py:_probe --tests test_v1_index_state.CatalogueTests
  python3 scripts/mutate_guards.py --plan guards.json
  python3 scripts/mutate_guards.py --plan guards.json --survivors
  python3 scripts/mutate_guards.py --report

A target is PATH:QUALNAME: a .py file under src/ or tests/, relative to the repository root, and
the name of a function (Class.method for a method) or of a module-level assignment. Test ids are
unittest names importable from tests/ (a leading "tests." is dropped). A plan file is a JSON list
of {"target": "...", "tests": ["...", ...]}. The tables go to standard output as Markdown;
progress goes to standard error. The children run in the copy, with its src/ and tests/ on
PYTHONPATH, so a test that finds files beside itself or starts its own Python finds the copy.

Limits: a recorded result is tied to the target's source and the test ids only, so after
changing the tests or the code the target calls, run again with --fresh. Git must be installed:
it lists the files that are copied.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import warnings
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
PLACES = ("src", "tests")  # the folders a target may be in; the copy puts both on PYTHONPATH
STATE_FORMAT = 1
KINDS = ("condition", "comparison", "return", "boolean", "raise", "loop", "continue", "break")
RESULTS = ("killed", "timeout", "survived", "invalid")
DEADLINE_FACTOR = 10  # a mutant's tests may run this many times as long as the unchanged tests took
DEADLINE_LEAST = 30.0  # seconds: a deadline is never shorter, so a slow start is not a kill
EXCERPT = 60  # characters of a node's source kept in its id and shown in the table
NICENESS = 10  # a child runs at least this nice
TEST_ID = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
TURNED = {
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.GtE, ast.GtE: ast.Lt, ast.Gt: ast.LtE,
    ast.LtE: ast.Gt, ast.In: ast.NotIn, ast.NotIn: ast.In, ast.Is: ast.IsNot, ast.IsNot: ast.Is,
}
SYMBOLS = {
    ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=",
    ast.In: "in", ast.NotIn: "not in", ast.Is: "is", ast.IsNot: "is not",
}
IMPORT_CHECK = "import importlib, sys; print(importlib.import_module(sys.argv[1]).__file__)"
Function = ast.FunctionDef | ast.AsyncFunctionDef
Assignment = ast.Assign | ast.AnnAssign
Loop = ast.For | ast.AsyncFor | ast.While


class Stop(Exception):
    """A reason to stop, said to the user."""


@dataclass(frozen=True)
class Mutant:
    """One changed copy of a function: the bytes ``start:end`` of its module replaced by ``text``."""

    target: str
    kind: str
    line: int
    column: int
    excerpt: str
    change: str
    start: int
    end: int
    text: bytes
    ident: str = ""


@dataclass(frozen=True)
class Group:
    """A target with the tests that guard it: its module, its mutants and the key its results are kept under."""

    target: str
    module: str
    tests: tuple[str, ...]
    source: bytes
    key: str
    mutants: tuple[Mutant, ...]


# ─────────────────────────── targets and their mutants ───────────────────────────


def parse_target(text: str) -> tuple[str, str]:
    """``PATH:QUALNAME`` as (the file's path relative to the repository root, dotted name), checked."""
    module, _, qualname = text.rpartition(":")
    path = PurePosixPath(module)
    if (
        not module or path.is_absolute() or ".." in path.parts or path.suffix != ".py" or len(path.parts) < 2
        or path.parts[0] not in PLACES
    ):
        raise Stop(f"{text!r}: a target is PATH:QUALNAME, PATH a .py file under src/ or tests/ relative to the "
                   "repository root")
    names = [name for name in qualname.split(".") if name != "<locals>"]
    if not names or not all(name.isidentifier() for name in names):
        raise Stop(f"{text!r}: {qualname!r} is not a dotted name")
    return path.as_posix(), ".".join(names)


def normalize_tests(ids: list[str]) -> tuple[str, ...]:
    """The test ids checked, without a leading ``tests.``, sorted and without repeats."""
    names = {name.removeprefix("tests.") for name in ids}
    for name in names:
        if not TEST_ID.fullmatch(name):
            raise Stop(f"{name!r} is not a unittest id such as module.Class or module.Class.test_name")
    if not names:
        raise Stop("a target needs at least one test id")
    return tuple(sorted(names))


def read_module(module: str) -> bytes:
    path = ROOT / module
    place = (ROOT / PurePosixPath(module).parts[0]).resolve()
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(place):
        raise Stop(f"{module}: no such file in the repository")
    return path.read_bytes()


def find_target(tree: ast.Module, qualname: str, target: str) -> Function | Assignment:
    """The definition a dotted name leads to through classes and functions (the last, if a name repeats).

    The last name may also be one a module-level or class-level assignment gives (``NAME = ...`` or
    ``NAME: type = ...``): its value is then the code that is mutated.
    """
    node: ast.AST = tree
    names = qualname.split(".")
    for number, name in enumerate(names, 1):
        found: list[ast.AST] = [child for child in _definitions(node) if child.name == name]
        if not found and number == len(names) and isinstance(node, (ast.Module, ast.ClassDef)):
            found = [child for child in _assignments(node) if name in _assigned_names(child)]
        if not found:
            raise Stop(f"{target}: no definition named {name!r}")
        node = found[-1]
    if isinstance(node, ast.ClassDef):
        raise Stop(f"{target}: names a class, not a function")
    return node  # type: ignore[return-value]


def _definitions(node: ast.AST) -> Iterator[Function | ast.ClassDef]:
    """The functions and classes a scope defines, looking through if, try, with, loops and match."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield child
        elif isinstance(child, (ast.stmt, ast.excepthandler, ast.match_case)):
            yield from _definitions(child)


def _assignments(node: ast.Module | ast.ClassDef) -> Iterator[Assignment]:
    """The assignments with a value written directly in a module's or a class's body."""
    for child in node.body:
        if isinstance(child, ast.Assign) or (isinstance(child, ast.AnnAssign) and child.value is not None):
            yield child


def _assigned_names(node: Assignment) -> set[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return {target.id for target in targets if isinstance(target, ast.Name)}


def mutation_points(definition: Function | Assignment) -> Iterator[tuple[str, ast.AST, int]]:
    """Every (kind, node, operator index) in a function's body or an assignment's value, nested code included."""
    seen: set[int] = set()

    def condition(part: ast.expr) -> Iterator[tuple[str, ast.AST, int]]:
        if id(part) not in seen:  # a condition is also an operand only when written twice
            seen.add(id(part))
            yield "condition", part, 0

    def walk(node: ast.AST, last: ast.stmt | None) -> Iterator[tuple[str, ast.AST, int]]:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            last = node.body[-1]
        for test in _conditions(node):
            for part in _condition_parts(test):
                yield from condition(part)
        if isinstance(node, ast.Compare):
            for index in range(len(node.ops)):
                yield "comparison", node, index
        elif isinstance(node, ast.Return):
            if node is not last:
                yield "return", node, 0
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, bool):
                yield "boolean", node.value, 0
        elif isinstance(node, (ast.Raise, ast.Continue, ast.Break)):
            yield type(node).__name__.lower(), node, 0
        elif isinstance(node, Loop):
            yield "loop", node, 0
        for child in ast.iter_child_nodes(node):
            yield from walk(child, last)

    if isinstance(definition, Function):
        for statement in definition.body:
            yield from walk(statement, definition.body[-1])
    else:
        yield from walk(definition.value, None)


def _conditions(node: ast.AST) -> list[ast.expr]:
    """The tests a node holds: if, elif, while, assert, conditional expression, comprehension filter, case guard,
    and a lambda's body or a return's value that is a test itself (an and/or, a not or a comparison)."""
    if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
        return [node.test]
    if isinstance(node, ast.comprehension):
        return list(node.ifs)
    if isinstance(node, ast.match_case) and node.guard is not None:
        return [node.guard]
    value = node.body if isinstance(node, ast.Lambda) else node.value if isinstance(node, ast.Return) else None
    negation = isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.Not)
    if isinstance(value, (ast.BoolOp, ast.Compare)) or negation:
        return [value]
    return []


def _condition_parts(test: ast.expr) -> Iterator[ast.expr]:
    """A condition, then each and/or operand inside it, looking through ``not``."""
    yield test
    inner = test
    while isinstance(inner, ast.UnaryOp) and isinstance(inner.op, ast.Not):
        inner = inner.operand
    if isinstance(inner, ast.BoolOp):
        for operand in inner.values:
            yield from _condition_parts(operand)


def replacement(kind: str, node: ast.AST, index: int, original: bytes) -> tuple[bytes, str]:
    """The text that takes the node's place, and the change as the table shows it."""
    if kind == "condition":
        return b"(not (" + original + b"))", "not (...)"
    if kind == "comparison":
        assert isinstance(node, ast.Compare)
        ops = list(node.ops)
        before = type(ops[index])
        ops[index] = TURNED[before]()
        turned = ast.unparse(ast.Compare(left=node.left, ops=ops, comparators=node.comparators))
        change = f"{SYMBOLS[before]} -> {SYMBOLS[TURNED[before]]}"
        if len(ops) > 1:
            change += f" (operator {index + 1})"
        return f"({turned})".encode(), change
    if kind == "boolean":
        assert isinstance(node, ast.Constant)
        return repr(not node.value).encode(), f"{node.value!r} -> {not node.value!r}"
    return b"pass", "pass"


def line_starts(source: bytes) -> list[int]:
    """The byte offset where each line begins (index 0 is line 1), and one past the end."""
    starts = [0]
    for line in source.split(b"\n"):
        starts.append(starts[-1] + len(line) + 1)
    return starts


def make_mutants(
    target: str, source: bytes, starts: list[int], definition: Function | Assignment, first: int
) -> tuple[Mutant, ...]:
    """One mutant per mutation point, in source order, each with its stable id (``first``: the definition's
    first line, its decorators included)."""
    mutants = []
    for kind, node, index in mutation_points(definition):
        start = starts[node.lineno - 1] + node.col_offset  # col_offset counts UTF-8 bytes
        end = starts[node.end_lineno - 1] + node.end_col_offset
        text, change = replacement(kind, node, index, source[start:end])
        excerpt = " ".join(ast.unparse(node).split())[:EXCERPT]
        mutants.append(Mutant(target, kind, node.lineno, node.col_offset, excerpt, change, start, end, text))
    mutants.sort(key=lambda mutant: (mutant.line, mutant.column, KINDS.index(mutant.kind), -mutant.end))
    return tuple(_with_ids(mutants, first))


def _with_ids(mutants: list[Mutant], first: int) -> Iterator[Mutant]:
    """Each mutant with its id: target, kind, line, column, excerpt (and the operator turned); a repeat gets #2.

    The line is counted from the definition's first line, so an edit elsewhere in the module keeps every id; an
    edit of the definition changes the group's key instead.
    """
    seen: dict[str, int] = {}
    for mutant in mutants:
        parts = [mutant.target, mutant.kind, f"+{mutant.line - first}", str(mutant.column), mutant.excerpt]
        if mutant.kind == "comparison":
            parts.append(mutant.change)
        ident = "|".join(parts)
        seen[ident] = seen.get(ident, 0) + 1
        yield replace(mutant, ident=ident if seen[ident] == 1 else f"{ident}#{seen[ident]}")


def group_key(target: str, function_text: bytes, tests: tuple[str, ...]) -> str:
    """The SHA-256 of the target, its function's source and the test ids."""
    digest = hashlib.sha256()
    for part in (target.encode(), function_text, "\n".join(tests).encode()):
        digest.update(len(part).to_bytes(8, "big") + part)
    return digest.hexdigest()


def load_group(target_text: str, tests: list[str]) -> Group:
    module, qualname = parse_target(target_text)
    target = f"{module}:{qualname}"
    source = read_module(module)
    try:
        tree = ast.parse(source, filename=module)
    except (SyntaxError, ValueError) as exc:
        raise Stop(f"{module}: does not parse ({exc})") from None
    definition = find_target(tree, qualname, target)
    starts = line_starts(source)
    decorators = definition.decorator_list if isinstance(definition, Function) else []
    first = min([definition.lineno, *(decorator.lineno for decorator in decorators)])
    ids = normalize_tests(tests)
    key = group_key(target, source[starts[first - 1] : starts[definition.end_lineno]], ids)  # type: ignore[index]
    return Group(target, module, ids, source, key, make_mutants(target, source, starts, definition, first))


def load_groups(items: list[tuple[str, list[str]]]) -> list[Group]:
    groups: dict[str, Group] = {}
    for target, tests in items:
        group = load_group(target, tests)
        groups.setdefault(group.key, group)
    return list(groups.values())


def read_plan(path: Path) -> list[tuple[str, list[str]]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Stop(f"{path}: cannot read the plan ({exc})") from None
    entries = data if isinstance(data, list) else [None]
    if not all(isinstance(entry, dict) and _plan_entry(entry) for entry in entries):
        raise Stop(f'{path}: a plan is a JSON list of {{"target": "MODULE:QUALNAME", "tests": ["ID", ...]}}')
    return [(entry["target"], entry["tests"]) for entry in entries]


def _plan_entry(entry: dict[str, Any]) -> bool:
    tests = entry.get("tests")
    return isinstance(entry.get("target"), str) and isinstance(tests, list) and all(isinstance(t, str) for t in tests)


# ─────────────────────────── the state file ───────────────────────────


def empty_state() -> dict[str, Any]:
    return {"format": STATE_FORMAT, "groups": {}}


def load_state(path: Path, fresh: bool) -> dict[str, Any]:
    """The recorded results: empty when there is no file, or, with --fresh, when the file is unreadable."""
    if not path.exists():
        return empty_state()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        problem = "" if _is_state(state) else "not a state file of this tool"
    except (OSError, ValueError) as exc:
        state, problem = None, f"cannot read it ({exc})"
    if not problem:
        return state
    if fresh:
        return empty_state()
    raise Stop(f"{path}: {problem}; --fresh starts a new one")


def _is_state(state: Any) -> bool:
    if not isinstance(state, dict) or state.get("format") != STATE_FORMAT or not isinstance(state.get("groups"), dict):
        return False
    return all(
        isinstance(group, dict) and isinstance(group.get("target"), str) and isinstance(group.get("tests"), list)
        and isinstance(group.get("results"), dict)
        for group in state["groups"].values()
    )


def save_state(path: Path, state: dict[str, Any]) -> None:
    """Write the whole state through a temporary file beside it, so a stop never leaves half a file."""
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(state, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def recorded(state: dict[str, Any], group: Group) -> dict[str, Any]:
    return state["groups"].get(group.key, {}).get("results", {})


def group_record(state: dict[str, Any], group: Group, forget: list[Mutant]) -> dict[str, Any]:
    """The group's record in the state, made when missing, without the results to ``forget``.

    A record of an older source of the same target and tests goes: its mutants no longer exist.
    """
    groups = state["groups"]
    for key, record in list(groups.items()):
        if key != group.key and record.get("target") == group.target and tuple(record.get("tests", ())) == group.tests:
            del groups[key]
    record = groups.setdefault(group.key, {"target": group.target, "tests": list(group.tests), "results": {}})
    record["mutants"] = len(group.mutants)
    current = {mutant.ident for mutant in group.mutants}
    for ident in [ident for ident in record["results"] if ident not in current]:
        del record["results"][ident]  # a mutant this version of the tool no longer makes
    for mutant in forget:
        record["results"].pop(mutant.ident, None)
    return record


def view(state: dict[str, Any], group: Group) -> dict[str, Any]:
    """What the report shows for a group of this run: the results of its mutants, each at its line in the module
    as it is now."""
    results = recorded(state, group)
    shown = {
        mutant.ident: {**results[mutant.ident], "line": mutant.line, "order": order}
        for order, mutant in enumerate(group.mutants)
        if mutant.ident in results
    }
    return {"target": group.target, "tests": list(group.tests), "mutants": len(group.mutants), "results": shown}


# ─────────────────────────── running the tests ───────────────────────────


def repository_files() -> list[str]:
    """The files git lists in the working tree, tracked or untracked and not ignored, relative to the root."""
    try:
        found = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
            capture_output=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Stop(f"git could not list the repository's files ({exc})") from None
    return sorted({name for name in found.decode("utf-8", "surrogateescape").split("\0") if name})


class Workspace:
    """A private copy of the repository in a folder made for this run, removed on every way out."""

    def __enter__(self) -> Workspace:
        self.folder = Path(tempfile.mkdtemp(prefix="mutate-guards-"))
        self.tree = self.folder / "tree"
        try:
            for name in repository_files():
                source = ROOT / name
                if not source.is_symlink() and not source.is_file():
                    continue  # listed by git but deleted in the working tree
                copy = self.tree / name
                copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, copy, follow_symlinks=False)
        except BaseException:
            shutil.rmtree(self.folder)
            raise
        self.log = self.folder / "child.log"
        # No bytecode is written, so a stale .pyc can never stand in for a mutant.
        path = os.pathsep.join(str(self.tree / place) for place in PLACES)
        self.env = dict(os.environ, PYTHONPATH=path, PYTHONDONTWRITEBYTECODE="1")
        return self

    def __exit__(self, *exc_info: object) -> None:
        try:
            shutil.rmtree(self.folder)
        except OSError as exc:
            say(f"warning: the private copy {self.folder} could not be removed ({exc})")

    def module(self, relative: str) -> Path:
        return self.tree / relative

    def run(self, command: list[str], timeout: float) -> tuple[str, float]:
        """One child in a process group of its own: ``passed``, ``failed`` or ``timeout``, and its seconds."""
        started = time.monotonic()
        with open(self.log, "wb") as log:
            child = subprocess.Popen(
                command, cwd=self.tree, env=self.env, stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, start_new_session=True,
            )
            try:
                code = child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_group(child)
                return "timeout", time.monotonic() - started
            except BaseException:
                _kill_group(child)
                raise
        return ("passed" if code == 0 else "failed"), time.monotonic() - started

    def output(self) -> str:
        return self.log.read_text(encoding="utf-8", errors="replace")

    def tail(self, lines: int) -> str:
        """The last lines the child wrote, the private folder's name hidden."""
        text = self.output()
        for name in {str(self.folder.resolve()), str(self.folder)}:
            text = text.replace(name, "<copy>")
        return "\n".join(text.strip().splitlines()[-lines:])


def _kill_group(child: subprocess.Popen[bytes]) -> None:
    """Kill the child and everything it started; it is not yet reaped, so its group id is still its own."""
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait()


def test_command(tests: tuple[str, ...]) -> list[str]:
    # -f: the first failure decides killed, so the rest need not run.
    return [sys.executable, "-m", "unittest", "-f", *tests]


def import_command(module: str) -> list[str]:
    parts = list(PurePosixPath(module).with_suffix("").parts)[1:]  # src/ and tests/ are on PYTHONPATH
    if parts[-1] == "__init__":
        parts.pop()
    return [sys.executable, "-c", IMPORT_CHECK, ".".join(parts)]


def check_module(workspace: Workspace, module: str, timeout: float) -> None:
    """Stop unless the module imports, unchanged, from the private copy: else no mutant would reach the tests."""
    outcome, _seconds = workspace.run(import_command(module), timeout)
    found = workspace.output().strip().splitlines()[-1:] if outcome == "passed" else []
    if not found or Path(found[0]).resolve() != workspace.module(module).resolve():
        raise Stop(f"{module} does not import from the private copy:\n{workspace.tail(15)}")


def check_baseline(workspace: Workspace, tests: tuple[str, ...], timeout: float) -> float:
    """Run the tests on the unchanged copy; stop when they fail or run no test. Returns their seconds."""
    say(f"unchanged: {' '.join(tests)}")
    outcome, seconds = workspace.run(test_command(tests), timeout)
    ran = re.findall(r"^Ran (\d+) tests? in ", workspace.tail(10), re.MULTILINE)
    if ran and int(ran[-1]) == 0:
        raise Stop(f"the tests named run no test: {' '.join(tests)}")
    if outcome != "passed" or not ran:
        verb = f"do not finish within {timeout:g} s" if outcome == "timeout" else "fail"
        raise Stop(f"the unchanged tests {verb}, so a failure could not be told from a kill:\n{workspace.tail(25)}")
    say(f"  {ran[-1]} {'test passes' if ran[-1] == '1' else 'tests pass'} in {duration(seconds)}")
    if seconds > timeout / 2:
        say(f"  warning: over half of --timeout {timeout:g} s; a mutant slowed by load may be recorded as timeout")
    return seconds


def try_mutant(workspace: Workspace, group: Group, mutant: Mutant, timeout: float) -> dict[str, Any]:
    """Run the group's tests against one mutant, then put the module back: what happened."""
    changed = group.source[: mutant.start] + mutant.text + group.source[mutant.end :]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            compile(changed, group.module, "exec", dont_inherit=True)
    except (SyntaxError, ValueError) as exc:
        return {"result": "invalid", "seconds": 0.0, "note": f"does not compile: {exc}"}
    path = workspace.module(group.module)
    path.write_bytes(changed)
    try:
        outcome, seconds = workspace.run(test_command(group.tests), timeout)
        if outcome == "failed" and workspace.run(import_command(group.module), timeout)[0] != "passed":
            return {"result": "invalid", "seconds": round(seconds, 1), "note": f"does not import: {workspace.tail(1)}"}
        result = {"failed": "killed", "passed": "survived", "timeout": "timeout"}[outcome]
        return {"result": result, "seconds": round(seconds, 1)}
    finally:
        path.write_bytes(group.source)


def chosen(group: Group, only: str | None) -> list[Mutant]:
    return [mutant for mutant in group.mutants if not only or mutant.kind == only]


Todo = list[tuple[Group, Mutant]]


def schedule(groups: list[Group], state: dict[str, Any], args: argparse.Namespace) -> tuple[Todo, int]:
    """The mutants to run now, in order, and how many are skipped as already recorded."""
    todo, skipped = [], 0
    for group in groups:
        done = {} if args.fresh else recorded(state, group)
        for mutant in chosen(group, args.only):
            again = args.survivors and done.get(mutant.ident, {}).get("result") == "survived"
            if mutant.ident in done and not again:
                skipped += 1
            else:
                todo.append((group, mutant))
    return (todo if args.limit is None else todo[: args.limit]), skipped


def lower_priority() -> None:
    """Run (and so start every child) at least NICENESS nice; a tool started with nice keeps its own.

    The niceness is read with getpriority: on macOS a process at niceness 20 cannot call nice() at all,
    not even nice(0), since the C library aims every call at 19, a raise in priority.
    """
    current = os.getpriority(os.PRIO_PROCESS, 0)
    if current < NICENESS:
        try:
            os.nice(NICENESS - current)
        except OSError as exc:
            say(f"warning: the priority stays as it is ({exc}); start the tool with nice")


def _interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def run(groups: list[Group], state: dict[str, Any], args: argparse.Namespace) -> int:
    started = time.monotonic()
    todo, skipped = schedule(groups, state, args)
    if skipped:
        say(f"{mutants(skipped)} already recorded for the same source and tests: skipped")
    say(f"{mutants(len(todo))} to run" + (f" (--limit {args.limit})" if args.limit is not None else ""))
    if not todo:
        print(report([view(state, group) for group in groups]))
        return 0
    if not args.state.resolve().parent.is_dir():
        raise Stop(f"{args.state}: the folder for the state file does not exist")
    for name in ("SIGTERM", "SIGHUP"):
        signal.signal(getattr(signal, name), _interrupt)
    lower_priority()
    count, records = 0, None
    try:
        with Workspace() as workspace:
            running = list({group.key: group for group, _mutant in todo}.values())
            for group in running:
                workspace.module(group.module).write_bytes(group.source)  # the bytes the mutants were made from
            for module in dict.fromkeys(group.module for group in running):
                check_module(workspace, module, args.timeout)
            baselines: dict[tuple[str, ...], float] = {}
            for group in running:
                if group.tests not in baselines:
                    baselines[group.tests] = check_baseline(workspace, group.tests, args.timeout)
            deadlines = {
                tests: min(args.timeout, max(DEADLINE_LEAST, DEADLINE_FACTOR * seconds))
                for tests, seconds in baselines.items()
            }
            estimate = sum(baselines[group.tests] for group, _mutant in todo)
            say(f"{mutants(len(todo))}: about {duration(estimate)} if each runs as long")
            # --fresh forgets every chosen mutant of a group it reaches, so a later run goes on from a clean start.
            records = {g.key: group_record(state, g, chosen(g, args.only) if args.fresh else []) for g in running}
            for number, (group, mutant) in enumerate(todo, 1):
                say(f"[{number}/{len(todo)}] {mutant.kind} line {mutant.line}: {mutant.excerpt} ({mutant.change})")
                outcome = try_mutant(workspace, group, mutant, deadlines[group.tests])
                records[group.key]["results"][mutant.ident] = {
                    "kind": mutant.kind, "line": mutant.line, "column": mutant.column, "excerpt": mutant.excerpt,
                    "change": mutant.change, "order": group.mutants.index(mutant), **outcome,
                }
                save_state(args.state, state)
                count += 1
                say(f"  {outcome['result']} in {duration(outcome['seconds'])}")
    except KeyboardInterrupt:
        if records is not None:
            save_state(args.state, state)
        say(f"stopped: {mutants(count)} saved in {args.state} by this run; the same command goes on from there")
        print(report([view(state, group) for group in groups]))
        return 130
    print(report([view(state, group) for group in groups]))
    print(f"\nTime spent: {duration(time.monotonic() - started)} for {mutants(count)} run now, "
          f"the unchanged tests ({duration(sum(baselines.values()))}) included.")
    return 0


# ─────────────────────────── tables ───────────────────────────


def say(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def mutants(count: int) -> str:
    return f"{count} mutant" if count == 1 else f"{count} mutants"


def duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, rest = divmod(round(seconds), 60)
    if minutes < 60:
        return f"{minutes} min {rest:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


def code(text: str) -> str:
    """Source text as a Markdown code span, its pipes escaped for a table cell."""
    text = text.replace("|", "\\|")
    return f"`` {text} ``" if "`" in text else f"`{text}`"


def table(headers: list[str], rows: list[list[str]]) -> str:
    """A Markdown pipe table, padded so it reads as well in a terminal."""
    widths = [max(len(row[column]) for row in [headers, *rows]) for column in range(len(headers))]

    def line(cells: list[str]) -> str:
        return "| " + " | ".join(cell.ljust(width) for cell, width in zip(cells, widths)) + " |"

    return "\n".join([line(headers), "|" + "|".join("-" * (width + 2) for width in widths) + "|", *map(line, rows)])


def _ordered(results: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Results in the order --list shows their mutants: by place, a whole condition before its operands."""
    return sorted(results.items(), key=lambda item: (item[1]["line"], item[1].get("order", 0), item[0]))


def listing(groups: list[Group], state: dict[str, Any], only: str | None) -> str:
    rows, lines = [], []
    for group in groups:
        done = recorded(state, group)
        listed = chosen(group, only)
        for mutant in listed:
            result = done.get(mutant.ident, {}).get("result", "not run")
            rows.append([group.target, mutant.kind, str(mutant.line), code(mutant.excerpt), code(mutant.change),
                         result])
        counts = Counter(mutant.kind for mutant in group.mutants)
        kinds = ", ".join(f"{kind} {counts[kind]}" for kind in KINDS if counts[kind])
        recorded_here = sum(mutant.ident in done for mutant in listed)
        lines.append(f"{group.target}: {mutants(len(group.mutants))} ({kinds or 'none'}); "
                     f"{len(listed)} listed, {recorded_here} of them recorded.")
    return table(["target", "kind", "line", "excerpt", "change", "result"], rows) + "\n\n" + "\n".join(lines)


def report(records: list[dict[str, Any]]) -> str:
    """The rows recorded, then totals per target; invalid mutants are listed apart and not counted."""
    rows, totals, invalid = [], [], []
    for record in sorted(records, key=lambda record: (record["target"], record["tests"])):
        results = _ordered(record["results"])
        for _ident, result in results:
            rows.append([record["target"], result["kind"], str(result["line"]), code(result["excerpt"]),
                         code(result["change"]), result["result"]])
            if result["result"] == "invalid":
                invalid.append(f"- {record['target']} line {result['line']} {result['kind']}: {result.get('note', '')}")
        totals.append(_totals(record, [result for _ident, result in results]))
    parts = [
        table(["target", "kind", "line", "excerpt", "change", "result"], rows) if rows else "No mutant recorded yet.",
        "",
        table(["target", "tests", *RESULTS, "not run", "noticed", "test time"], totals),
    ]
    if invalid:
        parts += ["", "Invalid mutants (not counted):", *invalid]
    return "\n".join(parts)


def _totals(record: dict[str, Any], results: list[dict[str, Any]]) -> list[str]:
    count = {name: sum(result["result"] == name for result in results) for name in RESULTS}
    noticed, counted = count["killed"] + count["timeout"], count["killed"] + count["timeout"] + count["survived"]
    not_run = max(record.get("mutants", len(results)) - len(results), 0)
    return [
        record["target"], " ".join(record["tests"]), *(str(count[name]) for name in count), str(not_run),
        f"{noticed}/{counted}" if counted else "-", duration(sum(result.get("seconds", 0.0) for result in results)),
    ]


# ─────────────────────────── command line ───────────────────────────


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def _seconds(text: str) -> float:
    value = float(text)
    if not value > 0:
        raise argparse.ArgumentTypeError("must be more than 0")
    return value


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Turn each guard of the given functions round, one at a time, and report which change "
        "the tests notice.",
        epilog="Example: %(prog)s --limit 3 --target src/memory_forest/search.py:_probe "
        "--tests test_v1_index_state.CatalogueTests",
    )
    parser.add_argument("--target", action="append", default=[], metavar="PATH:QUALNAME",
                        help="a function or a module-level assignment, PATH a .py file under src/ or tests/ "
                        "(repeatable, each with its --tests)")
    parser.add_argument("--tests", action="append", default=[], nargs="+", metavar="ID",
                        help="the unittest ids that guard the --target before it")
    parser.add_argument("--plan", type=Path, metavar="FILE", help='a JSON list of {"target": ..., "tests": [...]}')
    parser.add_argument("--state", type=Path, default=Path(".mutate-guards.json"), metavar="FILE",
                        help="where results are kept (default: %(default)s in the current folder)")
    parser.add_argument("--fresh", action="store_true",
                        help="forget what is recorded for the chosen mutants (see --only) and run them again")
    parser.add_argument("--survivors", action="store_true",
                        help="run again the mutants recorded as survived and keep the rest (after a test was added; "
                        "a test that is added can only kill more)")
    shown = parser.add_mutually_exclusive_group()
    shown.add_argument("--list", action="store_true", help="print the mutants without running them")
    shown.add_argument("--report", action="store_true", help="print the table from the state file and run nothing")
    parser.add_argument("--only", choices=KINDS, metavar="KIND", help=f"only one kind: {', '.join(KINDS)}")
    parser.add_argument("--limit", type=_positive, metavar="N", help="stop after N new mutants")
    parser.add_argument("--timeout", type=_seconds, default=300.0, metavar="SECONDS",
                        help="the longest deadline for one test run (a mutant's is ten times its unchanged tests' "
                        "time, at least 30 s); a mutant past it is stopped and recorded as timeout, "
                        "which counts as killed (default: %(default)g)")
    args = parser.parse_args(argv)
    if len(args.target) != len(args.tests):
        parser.error("give each --target its own --tests")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        state = load_state(args.state, args.fresh)
        items = list(zip(args.target, args.tests)) + (read_plan(args.plan) if args.plan else [])
        if not items:
            if not args.report:
                raise Stop("name a --target with its --tests, a --plan, or ask for --report")
            print(report(list(state["groups"].values())))
            return 0
        groups = load_groups(items)
        if args.list:
            print(listing(groups, state, args.only))
            return 0
        if args.report:
            print(report([view(state, group) for group in groups]))
            return 0
        return run(groups, state, args)
    except Stop as stop:
        say(f"mutate_guards: {stop}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
