"""The one door to the file system (``memory_forest.system``): only it touches the disk, it answers by the
system's own error, and what it checked is what SQLite reads.

The bakery and the bots are invented. Nothing here is a real person, shop or transcript.
"""

from __future__ import annotations

import ast
import builtins
import contextlib
import errno
import io
import json
import os
import shutil
import sqlite3
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import memory_forest
from memory_forest import cli, cli_find, derived, mcp, search, system
from memory_forest.errors import MemoryForestError

import door_rules
from test_v1_find import FILES
from v1_support import ForestTestCase

PACKAGE = door_rules.PACKAGE
READER = "till-bot"
PAGE = "04 stm/bakery/ovens/floor.md"


def agent_commands() -> list[tuple[str, ...]]:
    """Every command of ``forest`` an agent calls: those with the reader's ``--as``. ``mcp`` serves the tools."""
    found = []
    for command in cli.FOREST_COMMANDS:
        flags = {flag for argument in command.arguments for flag in argument.flags}
        dests = {argument.options.get("dest") for argument in command.arguments}
        if "--as" in flags and "reader" in dests and command.words != ("mcp",):
            found.append(command.words)
    return found


def agent_tools() -> list[str]:
    """Every tool of the MCP server's table."""
    return [tool["name"] for tool in mcp._TOOLS]


# The arguments each command and tool of an agent is called with here. The list of commands and tools is
# the parser's and the tool table's; a new one fails ``test_every_agent_call_has_its_arguments``.
COMMAND_ARGUMENTS: dict[tuple[str, ...], list[str]] = {
    ("search",): ["search", "trays"],
    ("read",): ["read", PAGE],
    ("timeline", "query"): ["timeline", "query", "2051-03-02"],
    ("timeline", "subject"): ["timeline", "subject", "till-bot"],
    ("navigate",): ["navigate"],
    ("log",): ["log"],
}
TOOL_ARGUMENTS: dict[str, dict[str, object]] = {
    "memory_search": {"words": "trays"},
    "memory_read": {"path": PAGE},
    "memory_timeline": {"phrase": "2051-03-02"},
    "memory_navigate": {},
    "memory_log": {"text": "the till counted the trays"},
}


def run_command(root: Path, words: tuple[str, ...], *, as_json: bool = True) -> tuple[int, str, str]:
    """``forest <command> --as till-bot --root <root>`` in this process; the turn of ``log`` on stdin."""
    argv = [*COMMAND_ARGUMENTS[words], "--as", READER, "--root", str(root)] + (["--json"] if as_json else [])
    out, err = io.StringIO(), io.StringIO()
    stdin = io.TextIOWrapper(io.BytesIO(b"the till counted the trays\n"), encoding="utf-8")
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.object(sys, "stdin", stdin):
        try:
            code = cli.forest_main(argv)
        except SystemExit as exc:
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


def run_tool(root: Path, name: str) -> dict[str, object]:
    reply = mcp._call(root, READER, {"name": name, "arguments": dict(TOOL_ARGUMENTS[name])}, "stdio")
    return {"isError": reply["isError"], "body": json.loads(reply["content"][0]["text"])}


class DoorSyntaxTests(unittest.TestCase):
    """By the syntax tree: outside the door no module touches the disk itself."""

    def test_no_module_outside_the_list_touches_the_disk_but_the_door(self) -> None:
        found = {
            name: calls
            for name, tree in door_rules.modules()
            if name != door_rules.DOOR and name not in door_rules.NOT_YET
            for calls in [door_rules.forbidden_calls(tree)]
            if calls
        }
        self.assertEqual(found, {})

    def test_the_list_holds_only_modules_that_still_touch_the_disk(self) -> None:
        trees = dict(door_rules.modules())
        for name, owner in door_rules.NOT_YET.items():
            self.assertIn(name, trees)
            self.assertTrue(owner)
            self.assertTrue(door_rules.forbidden_calls(trees[name]), f"{name} is through the door: take it off the list")

    def test_the_rules_catch_every_kind_of_call_and_leave_the_package_own(self) -> None:
        caught = '''
import os, shutil, tempfile, glob, sqlite3, io
from os import path as osp
from pathlib import Path
open("x")
os.stat("x")
os.path.exists("x")
osp.isdir("x")
shutil.copy("a", "b")
tempfile.mkdtemp()
glob.glob("*")
io.open("x")
sqlite3.connect("index.sqlite")
Path("x").exists()
here.read_text()
here.iterdir()
here.mkdir(parents=True)
here.unlink()
here.resolve()
here.replace(there)
'''
        found = door_rules.forbidden_calls(ast.parse(caught))
        self.assertEqual(len(found), 16, found)
        left = '''
import os, sqlite3
from . import layout, system
plan.exists(page)
text.replace("a", "b")
layout.resolve(root, page)
system.look(path)
sqlite3.connect(":memory:")
os.path.join("a", "b")
Plan.open(root)
match.group()
'''
        self.assertEqual(door_rules.forbidden_calls(ast.parse(left)), [])

    def test_the_door_reads_no_setting_and_no_environment(self) -> None:
        tree = ast.parse((PACKAGE / door_rules.DOOR).read_text(encoding="utf-8"))
        names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        self.assertNotIn("environ", names)
        self.assertNotIn("getenv", names)
        self.assertIsNone(system._WATCH)
        self.assertEqual(system._POINTS, {})


class DoorRuntimeTests(ForestTestCase):
    """By running: while each command and tool of an agent runs, only the door touches the disk."""

    WRAPPED = (
        "open", "stat", "lstat", "fstat", "scandir", "listdir", "mkdir", "makedirs", "remove", "unlink",
        "rmdir", "rename", "replace", "chmod", "access", "utime", "readlink", "symlink", "link", "truncate",
        "ftruncate", "read", "write", "close", "lseek", "fsync", "mkfifo", "getcwd",
    )

    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_forest(dict(FILES))
        search.build(self.root)

    def test_every_agent_call_has_its_arguments(self) -> None:
        self.assertEqual(sorted(agent_commands()), sorted(COMMAND_ARGUMENTS))
        self.assertEqual(sorted(agent_tools()), sorted(TOOL_ARGUMENTS))

    def _caller(self, frame) -> str | None:
        """The package module a file call comes from, read from the stack; None for the standard library's own."""
        package = str(PACKAGE) + os.sep
        while frame is not None:
            filename = frame.f_code.co_filename
            if filename.startswith(package):
                return filename[len(package):]
            module = frame.f_globals.get("__name__", "")
            if module.split(".")[0] in {"importlib", "zipimport", "_frozen_importlib", "_frozen_importlib_external"}:
                return None
            if not (module in {"os", "posixpath", "genericpath", "io", "_pyio", "shutil", "tempfile", "glob",
                               "fnmatch", "contextlib", "codecs", "builtins"}
                    or module.startswith(("pathlib", "encodings"))):
                return None
            frame = frame.f_back
        return None

    @contextlib.contextmanager
    def _watched(self):
        calls: list[tuple[str, str]] = []
        ran: set[str] = set()
        package = str(PACKAGE) + os.sep

        def wrap(owner, name):
            real = getattr(owner, name)

            def wrapper(*args, **kwargs):
                where = self._caller(sys._getframe(1))
                if where is not None:
                    calls.append((where, name))
                return real(*args, **kwargs)

            return real, wrapper

        patches = []
        for name in self.WRAPPED:
            if hasattr(os, name):
                real, wrapper = wrap(os, name)
                patches.append(mock.patch.object(os, name, wrapper))
        for owner in (builtins, io):
            real, wrapper = wrap(owner, "open")
            patches.append(mock.patch.object(owner, "open", wrapper))

        def profile(frame, event, _arg):
            if event == "call" and frame.f_code.co_filename.startswith(package):
                ran.add(frame.f_code.co_filename[len(package):])

        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            sys.setprofile(profile)
            threading.setprofile(profile)
            try:
                yield calls, ran
            finally:
                sys.setprofile(None)
                threading.setprofile(None)

    def _check(self, label: str, calls, ran) -> None:
        outside = sorted({where for where, _name in calls} - {door_rules.DOOR} - door_rules.INTAKE)
        self.assertEqual(outside, [], f"{label}: a module other than the door touched the disk")
        listed = sorted((set(door_rules.NOT_YET) - door_rules.INTAKE) & ran)
        self.assertEqual(listed, [], f"{label}: a module listed as reached by no agent call ran")

    def test_only_the_door_touches_the_disk_while_an_agent_calls(self) -> None:
        for words in agent_commands():
            with self.subTest(command=" ".join(words)):
                with self._watched() as (calls, ran):
                    code, out, err = run_command(self.root, words, as_json=False)
                self.assertEqual(code, 0, out + err)
                self._check(" ".join(words), calls, ran)
        for name in agent_tools():
            with self.subTest(tool=name):
                with self._watched() as (calls, ran):
                    answer = run_tool(self.root, name)
                self.assertFalse(answer["isError"], answer)
                self._check(name, calls, ran)

    def test_the_wrapping_sees_a_call_that_does_not_go_through_the_door(self) -> None:
        # Code whose frames say it is a module of the package (as ``index.py`` of the list, which opens files
        # itself) is seen by the wrapping, through ``os`` and through a path's own methods alike.
        code = compile("import os\nos.lstat(root)\nroot.exists()\n", str(PACKAGE / "index.py"), "exec")
        with self._watched() as (calls, _ran):
            exec(code, {"root": self.root})
        self.assertEqual(calls, [("index.py", "lstat"), ("index.py", "stat")])
        with self.assertRaises(AssertionError):
            self._check("a call outside the door", [("search.py", "lstat")], set())


class DoorAnswerTests(ForestTestCase):
    """The door answers by the system's own error, follows no link and waits on nothing it opens."""

    def setUp(self) -> None:
        super().setUp()
        if os.geteuid() == 0:
            self.skipTest("the superuser enters and reads every folder")
        self.folder = self.base / "folder"
        self.folder.mkdir()
        self.file = self.folder / "page.md"
        self.file.write_text("the trays\n", encoding="utf-8")

    def test_a_folder_that_cannot_be_entered_is_denied_never_absent(self) -> None:
        os.chmod(self.folder, 0o600)
        self.addCleanup(os.chmod, self.folder, 0o700)
        for call in (system.look, system.read_bytes, system.look_or_none, system.kind):
            with self.subTest(call=call.__name__), self.assertRaises(system.Denied) as denied:
                call(self.file)
            self.assertEqual(denied.exception.access, "enter")
            self.assertEqual(denied.exception.where(), str(self.folder))

    def test_a_file_that_cannot_be_read_is_denied_for_reading(self) -> None:
        os.chmod(self.file, 0o200)
        with self.assertRaises(system.Denied) as denied:
            system.read_bytes(self.file)
        self.assertEqual((denied.exception.access, denied.exception.where()), ("read", str(self.file)))
        os.chmod(self.folder, 0o300)
        self.addCleanup(os.chmod, self.folder, 0o700)
        with self.assertRaises(system.Denied) as unlisted:
            system.listing(self.folder)
        self.assertEqual(unlisted.exception.access, "list")

    def test_a_link_is_never_followed(self) -> None:
        link = self.folder / "link.md"
        link.symlink_to(self.file)
        folder_link = self.base / "folder-link"
        folder_link.symlink_to(self.folder)
        for call in (system.read_bytes, system.open_file):
            with self.subTest(call=call.__name__), self.assertRaises(system.Link):
                call(link)
        with self.assertRaises(system.Link):
            system.listing(folder_link)
        self.assertEqual(system.kind(link), "link")
        with self.assertRaises(system.Link):
            system.write_whole(link, b"x")
        self.assertEqual(self.file.read_text(encoding="utf-8"), "the trays\n")

    def test_a_fifo_is_not_a_file_and_nothing_waits_on_it(self) -> None:
        fifo = self.folder / "fifo.md"
        os.mkfifo(fifo)
        started = time.monotonic()
        for call in (system.read_bytes, system.open_file):
            with self.subTest(call=call.__name__), self.assertRaises(system.NotAFile) as refused:
                call(fifo)
            self.assertEqual(refused.exception.found, "fifo")
        with self.assertRaises(system.NotAFile):
            system.append(fifo, b"x")
        self.assertLess(time.monotonic() - started, 5)

    def test_making_a_folder_another_made_is_success_and_a_file_there_is_not(self) -> None:
        self.assertTrue(system.make_folder(self.folder / "new"))
        self.assertFalse(system.make_folder(self.folder / "new"))
        with self.assertRaises(system.NotAFolder):
            system.make_folder(self.file)

    def test_a_whole_write_keeps_the_mode_it_is_given(self) -> None:
        target = self.folder / "new.json"
        system.write_whole(target, b"{}", mode=0o640)
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)
        system.write_whole(target, b"[]")
        self.assertEqual((target.read_bytes(), target.stat().st_mode & 0o777), (b"[]", 0o640))
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), ["new.json", "page.md"])

    def test_the_door_counts_and_its_nth_call_answers_what_a_test_asks(self) -> None:
        with system.watching(fail_calls={2: "denied"}) as watch:
            self.assertEqual(system.read_bytes(self.file), b"the trays\n")
            with self.assertRaises(system.Denied):
                system.read_bytes(self.file)
        self.assertEqual(watch.bytes_read, len(b"the trays\n"))
        self.assertEqual([name for name, _path in watch.calls], ["read", "read"])
        self.assertEqual(watch.injected, [(2, "read", "denied")])
        for kind in system.KINDS:
            with self.subTest(kind=kind), system.watching(fail_calls={1: kind}):
                with self.assertRaises(system.BY_KIND[kind]):
                    system.look(self.file)
        self.assertIsNone(system._WATCH)


class DoorClassifyTests(ForestTestCase):
    """The system's error number decides the outcome, and for a denial the door finds which access was missing
    and where; a test's injected outcome has the shape the call would give it."""

    def setUp(self) -> None:
        super().setUp()
        if os.geteuid() == 0:
            self.skipTest("the superuser enters every folder")
        self.folder = self.base / "shop"
        self.folder.mkdir()
        self.file = self.folder / "tray.md"
        self.file.write_text("the trays\n", encoding="utf-8")
        self.locked = self.base / "locked"
        self.locked.mkdir()
        (self.locked / "inner.md").write_text("the dough\n", encoding="utf-8")
        os.chmod(self.locked, 0o600)
        self.addCleanup(os.chmod, self.locked, 0o700)
        self.addCleanup(os.chmod, self.folder, 0o700)

    def _classified(self, number: int | None, path: Path, access: str = "read") -> system.Outcome:
        error = OSError(number, os.strerror(number)) if number is not None else OSError("no number")
        return system.outcome_of(error, str(path), access)

    def test_each_error_number_is_its_outcome(self) -> None:
        rows = [
            (errno.ENOENT, system.Absent), (errno.ELOOP, system.Link), (errno.ENOTDIR, system.NotAFolder),
            (errno.EISDIR, system.NotAFile), (errno.EAGAIN, system.Busy), (errno.EBUSY, system.Busy),
            (errno.EIO, system.Other), (errno.ENOSPC, system.Other),
        ]
        for number, kind in rows:
            with self.subTest(error=errno.errorcode[number]):
                outcome = self._classified(number, self.file)
                self.assertIs(type(outcome), kind)
                self.assertEqual((outcome.errno, outcome.error, outcome.path), (number, errno.errorcode[number],
                                                                                str(self.file)))
        self.assertEqual(self._classified(errno.EISDIR, self.file).found, "folder")
        unnamed = self._classified(None, self.file)
        self.assertEqual((type(unnamed), unnamed.errno, unnamed.error), (system.Other, errno.EIO, "EIO"))
        made = system.Absent(self.file)
        self.assertIs(system.outcome_of(made), made)
        self.assertEqual(system.Busy(self.file, note="deadline").error, "deadline")
        self.assertEqual(system.Busy(self.file).error, errno.errorcode[errno.EAGAIN])

    def test_a_denial_says_which_access_was_missing_and_where(self) -> None:
        def denied(number: int, path: Path, access: str) -> tuple[type, str, str]:
            outcome = self._classified(number, path, access)
            return type(outcome), outcome.access, outcome.where()

        rows = [
            ("a read-only file system", (errno.EROFS, self.file, "write"),
             (system.Denied, "write", str(self.folder))),
            ("a file that cannot be read", (errno.EACCES, self.file, "read"), (system.Denied, "read", str(self.file))),
            ("a folder that cannot be listed", (errno.EACCES, self.folder, "list"),
             (system.Denied, "list", str(self.folder))),
            ("a file in a folder that cannot be entered", (errno.EACCES, self.locked / "inner.md", "read"),
             (system.Denied, "enter", str(self.locked))),
            ("a new file two folders below one that cannot be entered",
             (errno.EACCES, self.locked / "batch" / "new.md", "write"), (system.Denied, "enter", str(self.locked))),
            ("a new file in a folder that can be looked at, not entered", (errno.EACCES, self.locked / "new.md", "write"),
             (system.Denied, "enter", str(self.locked))),
            ("a folder where a file was to be written", (errno.EPERM, self.folder, "write"),
             (system.NotAFile, "", str(self.folder))),
            ("a file whose folder can be written", (errno.EPERM, self.file, "write"),
             (system.Denied, "write", str(self.file))),
            ("a file whose folder can be written, again", (errno.EACCES, self.file, "write"),
             (system.Denied, "write", str(self.file))),
            ("a path below one that cannot be entered", (errno.EACCES, self.locked / "batch" / "tray.md", "enter"),
             (system.Denied, "enter", str(self.locked))),
            ("a path two folders below one that cannot be entered",
             (errno.EACCES, self.locked / "batch" / "deeper" / "tray.md", "enter"),
             (system.Denied, "enter", str(self.locked))),
        ]
        for label, (number, path, access), expected in rows:
            with self.subTest(row=label):
                self.assertEqual(denied(number, path, access), expected)
        os.chmod(self.folder, 0o500)
        self.assertEqual(denied(errno.EACCES, self.folder / "new.md", "write"),
                         (system.Denied, "write", str(self.folder)))
        self.assertEqual(denied(errno.EACCES, self.file, "write"), (system.Denied, "write", str(self.folder)))

    def test_an_injected_outcome_has_the_shape_the_call_gives_it(self) -> None:
        def injected(kind: str, call) -> system.Outcome:
            with system.watching(fail_calls={1: kind}), self.assertRaises(system.Outcome) as caught:
                call()
            return caught.exception

        looked = injected("denied", lambda: system.look(self.file))
        self.assertEqual((looked.access, looked.where()), ("enter", str(self.folder)))
        read = injected("denied", lambda: system.read_bytes(self.file))
        self.assertEqual((read.access, read.where()), ("read", str(self.file)))
        asked = injected("denied", lambda: system.may(self.file, "read"))
        self.assertEqual((asked.access, asked.where()), ("read", str(self.file)))
        found = injected("not_a_file", lambda: system.read_bytes(self.file))
        self.assertEqual((type(found), found.found), (system.NotAFile, "folder"))
        other = injected("other", lambda: system.read_bytes(self.file))
        self.assertEqual((type(other), other.errno, other.error), (system.Other, errno.EIO, "EIO"))
        names = {"absent": "ENOENT", "link": "ELOOP", "busy": errno.errorcode[errno.EAGAIN], "changed": "ESTALE",
                 "not_a_folder": "ENOTDIR"}
        for kind, name in names.items():
            with self.subTest(kind=kind):
                outcome = injected(kind, lambda: system.read_bytes(self.file))
                self.assertIs(type(outcome), system.BY_KIND[kind])
                self.assertEqual((outcome.path, outcome.error, outcome.found, outcome.access, outcome.folder),
                                 (str(self.file), name, "", "", ""))

    def test_a_watch_and_a_point_are_one_at_a_time(self) -> None:
        with system.watching():
            with self.assertRaises(RuntimeError):
                with system.watching():
                    pass
        with system.at(system.POINT_INDEX_OPEN, lambda **info: None):
            with self.assertRaises(RuntimeError):
                with system.at(system.POINT_INDEX_OPEN, lambda **info: None):
                    pass
        self.assertIsNone(system._WATCH)
        self.assertEqual(system._POINTS, {})

    def test_an_open_past_its_deadline_is_busy_and_names_the_deadline(self) -> None:
        index = self.folder / "index.sqlite"
        sqlite3.connect(index).close()
        gate = threading.Event()
        self.addCleanup(gate.set)
        with self.assertRaises(system.Busy) as caught:
            system.open_index(index, immutable=False, first=lambda connection: gate.wait(10), deadline_seconds=0.2)
        gate.set()
        self.assertEqual((caught.exception.kind, caught.exception.error), ("busy", "deadline"))


    def test_the_compare_after_the_read_sees_each_change_of_the_index_file(self) -> None:
        index = self.folder / "index.sqlite"
        made = sqlite3.connect(index)
        made.execute("CREATE TABLE trays (number INTEGER)")
        made.commit()
        made.close()

        def opened(immutable: bool) -> system.IndexFile:
            handle, _result = system.open_index(index, immutable=immutable)
            self.addCleanup(handle.close)
            return handle

        for immutable in (False, True):
            with self.subTest(immutable=immutable):
                handle = opened(immutable)
                self.assertTrue(handle.same())
                with system.watching(fail_calls={1: "changed"}):
                    self.assertFalse(handle.same())
                wal = Path(f"{index}-wal")
                wal.write_bytes(b"")
                # Only immutable mode reads past a write-ahead log; in WAL mode SQLite keeps one there itself.
                self.assertEqual(handle.same(), not immutable)
                wal.unlink()
                with index.open("ab") as grown:
                    grown.write(b"\0" * 512)
                # A file that grew under the same name is still the file opened; immutable mode read it whole.
                self.assertEqual(handle.same(), not immutable)
        handle = opened(False)
        swapped = self.folder / "swapped.sqlite"
        shutil.copyfile(index, swapped)
        os.replace(swapped, index)
        self.assertFalse(handle.same())
        index.unlink()
        self.assertFalse(handle.same())

@unittest.skipIf(os.geteuid() == 0, "the superuser writes a read-only folder")
class IndexSwapTests(ForestTestCase):
    """At the door's named point, between its look and SQLite's open, something else takes the index's place."""

    SWAPS = ("fifo", "folder", "link", "another index", "nothing")

    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_forest(dict(FILES))
        search.build(self.root)
        self.index = self.root / ".forest" / "index.sqlite"
        self.kept = self.index.read_bytes()
        other = self.make_forest(dict(FILES), name="other")
        search.build(other)
        self.other_index = other / ".forest" / "index.sqlite"
        self.folder = self.root / ".forest"
        self.addCleanup(os.chmod, self.folder, 0o700)
        self.released: list[Path] = []
        self.addCleanup(self._release)

    def _release(self) -> None:
        """Open the write end of a fifo SQLite may still wait on, so its abandoned thread ends."""
        for fifo in self.released:
            try:
                descriptor = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
            except OSError:
                continue
            os.close(descriptor)

    def _swap(self, kind: str):
        def at_point(*, path: Path) -> None:
            path = Path(path)
            if path.name != self.index.name:
                return
            mode = os.stat(self.folder).st_mode & 0o777
            os.chmod(self.folder, 0o700)
            try:
                path.unlink()
                if kind == "fifo":
                    os.mkfifo(path)
                    self.released.append(path)
                elif kind == "folder":
                    path.mkdir()
                elif kind == "link":
                    path.symlink_to(self.other_index)
                elif kind == "another index":
                    shutil.copyfile(self.other_index, path)
            finally:
                os.chmod(self.folder, mode)

        return system.at(system.POINT_INDEX_OPEN, at_point)

    def _restore(self) -> None:
        os.chmod(self.folder, 0o700)
        if self.index.is_symlink() or self.index.is_file():
            self.index.unlink()
        elif self.index.is_dir():
            self.index.rmdir()
        elif self.index.exists():
            self._release()
            self.index.unlink()
        self.index.write_bytes(self.kept)

    def _answers(self, kind: str, writable: bool) -> list[dict[str, object]]:
        answers = []
        calls = (
            ("index status", lambda: search.status(self.root)),
            ("search", lambda: search.query(self.root, "trays", reader=READER)),
            ("memory_search", lambda: run_tool(self.root, "memory_search")),
        )
        for label, call in calls:
            self._restore()
            if not writable:
                os.chmod(self.folder, 0o500)
            started = time.monotonic()
            with mock.patch.object(system, "OPEN_DEADLINE_SECONDS", 1.0), self._swap(kind):
                try:
                    answer = call()
                except MemoryForestError as exc:
                    answer = {"isError": True, "body": exc.as_dict()}
            took = time.monotonic() - started
            self.assertLess(took, 10.0, f"{label} after a {kind} waited {took:.1f} s")
            body = answer["body"] if "body" in answer else answer
            answers.append({"label": label, **body})
        return answers

    def test_every_swap_in_both_kinds_of_folder_is_the_same_true_busy_answer(self) -> None:
        for writable in (True, False):
            for kind in self.SWAPS:
                with self.subTest(swap=kind, writable=writable):
                    answers = self._answers(kind, writable)
                    for answer in answers:
                        self.assertEqual(answer["code"], "index_busy", answer)
                        self.assertEqual(answer["next"], "retry", answer)
                        self.assertEqual(answer["details"]["state"], "busy", answer)
                        self.assertEqual(
                            answer["message"],
                            " ".join(derived.CLAIMS[key].sentence for key in ("index_changed", "ask_later")),
                            answer,
                        )
                    self.assertEqual(len({(item["code"], item["message"]) for item in answers}), 1, answers)
        self._restore()
        self.assertEqual(search.status(self.root)["state"], "sound")


if __name__ == "__main__":
    unittest.main()
