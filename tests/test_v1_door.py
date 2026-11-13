"""The one door to the file system (``memory_forest.system``): only it touches the disk, it answers by the
system's own error, and what it checked is what SQLite reads.

The bakery and the bots are invented. Nothing here is a real person, shop or transcript.
"""

from __future__ import annotations

import ast
import builtins
import contextlib
import errno
import fcntl
import io
import json
import os
import shutil
import sqlite3
import stat
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

class DoorCallTests(ForestTestCase):
    """Each call of the door on a real disk: its value, and the outcome for what stands in its way."""

    def setUp(self) -> None:
        super().setUp()
        self.folder = self.base / "shop"
        self.folder.mkdir()
        self.page = self.folder / "tray.md"
        self.page.write_bytes(b"the rye trays\n")
        self.link = self.folder / "tray-link.md"
        self.link.symlink_to(self.page)
        self.fifo = self.folder / "proof.fifo"
        os.mkfifo(self.fifo)
        self.inner = self.folder / "racks"
        self.inner.mkdir()

    def test_a_listing_names_what_stands_there_and_follows_nothing(self) -> None:
        names = [("proof.fifo", "fifo"), ("racks", "folder"), ("tray-link.md", "link"), ("tray.md", "file")]
        listed = system.listing(self.folder)
        self.assertEqual([(entry.name, entry.kind) for entry in listed], names)
        self.assertEqual((listed[3].path, listed[3].look), (self.page, None))
        looked = system.listing(self.folder, looks=True)
        self.assertEqual([(entry.name, entry.kind) for entry in looked], names)
        self.assertEqual((looked[3].look.kind, looked[3].look.size), ("file", 14))
        with self.assertRaises(system.NotAFolder) as file_there:
            system.listing(self.page)
        self.assertEqual(file_there.exception.found, "file")
        (self.folder / "racks-link").symlink_to(self.inner)
        with self.assertRaises(system.Link):
            system.listing(self.folder / "racks-link")
        with self.assertRaises(system.Absent):
            system.listing(self.folder / "gone")

    def test_a_read_takes_a_regular_file_only(self) -> None:
        self.assertEqual(system.read_bytes(self.page), b"the rye trays\n")
        self.assertEqual(system.read_bytes(self.page, limit=7), b"the rye")
        self.assertEqual(system.read_bytes(self.page, limit=0), b"")
        self.assertEqual(system.read_text(self.page), "the rye trays\n")
        (self.folder / "odd.md").write_bytes(b"the \xff tray")
        self.assertEqual(system.read_text(self.folder / "odd.md", errors="replace"), "the \ufffd tray")
        with system.watching() as watch:
            system.read_bytes(self.page)
        self.assertEqual(watch.bytes_read, 14)
        for path, kind, found in ((self.inner, system.NotAFile, "folder"), (self.fifo, system.NotAFile, "fifo"),
                                  (self.link, system.Link, ""), (self.folder / "gone.md", system.Absent, "")):
            with self.subTest(path=path.name), self.assertRaises(kind) as caught:
                system.read_bytes(path)
            self.assertEqual(caught.exception.found, found)

    def test_a_file_is_read_in_pieces(self) -> None:
        with system.open_file(self.page) as reader:
            self.assertEqual((reader.look.kind, reader.look.size), ("file", 14))
            self.assertEqual(reader.read(3), b"the")
            self.assertEqual(reader.read(), b" rye trays\n")
            reader.seek(4)
            self.assertEqual(reader.read(-1), b"rye trays\n")
            reader.seek(0)
            self.assertEqual(reader.read(None), b"the rye trays\n")
            self.assertGreaterEqual(reader.fileno(), 0)
        self.assertEqual(reader.descriptor, -1)
        reader.close()
        with self.assertRaises(system.NotAFile):
            system.open_file(self.fifo)

    def test_a_folder_is_made_with_its_mode_or_found_made(self) -> None:
        made = self.folder / "proofing"
        self.assertTrue(system.make_folder(made, mode=0o770))
        self.assertEqual(stat.S_IMODE(made.stat().st_mode), 0o770)
        self.assertFalse(system.make_folder(made))
        with self.assertRaises(system.NotAFolder) as file_there:
            system.make_folder(self.page)
        self.assertEqual(file_there.exception.found, "file")
        with self.assertRaises(system.Link):
            system.make_folder(self.link)
        with self.assertRaises(system.Absent):
            system.make_folder(self.folder / "gone" / "deeper")
        deep = self.folder / "upper" / "middle" / "lower"
        system.make_folders(deep, mode=0o770)
        self.assertEqual([stat.S_IMODE(path.stat().st_mode) for path in (deep.parent.parent, deep.parent, deep)],
                         [0o770, 0o770, 0o770])
        os.chmod(self.inner, 0o755)
        system.make_folders(self.inner / "top")
        system.make_folders(self.inner)
        self.assertEqual(stat.S_IMODE(self.inner.stat().st_mode), 0o755)

    def test_a_whole_file_is_replaced_at_once_with_its_mode(self) -> None:
        target = self.folder / "count.md"
        system.write_whole(target, "first\n")
        self.assertEqual((target.read_text(), stat.S_IMODE(target.stat().st_mode)), ("first\n", 0o600))
        os.chmod(target, 0o640)
        system.write_whole(target, b"second\n", mode=0o600)
        self.assertEqual((target.read_bytes(), stat.S_IMODE(target.stat().st_mode)), (b"second\n", 0o640))
        system.write_whole(target, b"third\n", mode=0o644, keep_mode=False)
        self.assertEqual((target.read_bytes(), stat.S_IMODE(target.stat().st_mode)), (b"third\n", 0o644))
        system.write_whole(self.folder / "new.md", b"new", mode=0o640)
        self.assertEqual(stat.S_IMODE((self.folder / "new.md").stat().st_mode), 0o640)
        with self.assertRaises(system.Link):
            system.write_whole(self.link, b"x")
        with self.assertRaises(system.NotAFile) as folder_there:
            system.write_whole(self.inner, b"x")
        self.assertEqual(folder_there.exception.found, "folder")
        with self.assertRaises(system.Absent):
            system.write_whole(self.folder / "gone" / "count.md", b"x")
        self.assertEqual([path.name for path in self.folder.iterdir() if path.name.endswith(".tmp")], [])

    def test_a_new_file_is_made_once_with_its_mode(self) -> None:
        target = self.folder / "key"
        self.assertTrue(system.create_new(target, b"rye"))
        self.assertFalse(system.create_new(target, b"wheat"))
        self.assertEqual((target.read_bytes(), stat.S_IMODE(target.stat().st_mode)), (b"rye", 0o600))
        self.assertTrue(system.create_new(self.folder / "shared", b"oat", mode=0o640))
        self.assertEqual(stat.S_IMODE((self.folder / "shared").stat().st_mode), 0o640)

    def test_an_append_adds_waits_for_another_appender_and_a_cut_takes_back(self) -> None:
        log = self.folder / "turns.jsonl"
        system.append(log, b"")
        self.assertFalse(log.exists())
        system.append(log, b"one\n")
        system.append(log, b"two\n")
        self.assertEqual((log.read_bytes(), stat.S_IMODE(log.stat().st_mode)), (b"one\ntwo\n", 0o600))
        self.assertFalse(system.cut_back(log, 8))
        self.assertFalse(system.cut_back(log, 20))
        self.assertTrue(system.cut_back(log, 4))
        self.assertEqual(log.read_bytes(), b"one\n")
        self.assertFalse(system.cut_back(self.folder / "gone.jsonl", 0))
        with self.assertRaises(system.NotAFile) as fifo:
            system.append(self.fifo, b"x")  # a fifo with no reader refuses a writer that will not wait
        self.assertEqual(fifo.exception.found, "fifo")
        with self.assertRaises(system.Link):
            system.append(self.link, b"x")
        holder = os.open(log, os.O_RDWR)
        self.addCleanup(os.close, holder)
        fcntl.flock(holder, fcntl.LOCK_EX)
        started = time.monotonic()
        with mock.patch.object(system, "APPEND_WAIT_SECONDS", 0.2), self.assertRaises(system.Busy) as busy:
            system.append(log, b"three\n")
        self.assertGreaterEqual(time.monotonic() - started, 0.2)  # it waited its whole time first
        self.assertEqual(busy.exception.error, "deadline")
        fcntl.flock(holder, fcntl.LOCK_UN)
        system.append(log, b"three\n")
        self.assertEqual(log.read_bytes(), b"one\nthree\n")

    def test_a_file_or_an_empty_folder_is_removed(self) -> None:
        self.assertTrue(system.remove(self.link))
        self.assertTrue(self.page.exists())
        self.assertTrue(system.remove(self.page))
        self.assertFalse(system.remove(self.page))
        with self.assertRaises(system.NotAFile) as folder_there:
            system.remove(self.inner)
        self.assertEqual(folder_there.exception.found, "folder")
        system.remove_folder(self.inner)
        self.assertFalse(self.inner.exists())
        with self.assertRaises(system.Absent):
            system.remove_folder(self.inner)
        with self.assertRaises(system.NotAFolder):
            system.remove_folder(self.fifo)

    def test_a_mode_is_set_without_following_a_link(self) -> None:
        system.set_mode(self.page, 0o640)
        self.assertEqual(stat.S_IMODE(self.page.stat().st_mode), 0o640)
        try:
            system.set_mode(self.link, 0o600)
        except system.Link:
            pass  # a system that cannot change a link's own mode refuses it
        self.assertEqual(stat.S_IMODE(self.page.stat().st_mode), 0o640)
        with self.assertRaises(system.Absent):
            system.set_mode(self.folder / "gone", 0o600)

    def test_a_folder_is_synced_and_each_place_is_named(self) -> None:
        system.sync_folder(self.folder)
        with self.assertRaises(system.Absent):
            system.sync_folder(self.folder / "gone")
        self.assertEqual(system.absolute("~/crates"), Path(os.path.expanduser("~")) / "crates")
        self.assertEqual(system.absolute("crates"), Path(os.getcwd()) / "crates")
        self.assertEqual(system.real_path(self.link), self.page)
        self.assertEqual(system.working_folder(), Path(os.getcwd()))
        self.assertEqual(system.home_folder(), os.path.expanduser("~"))
        self.assertEqual(system.account_id(), os.getuid())
        gone = self.base / "gone"
        gone.mkdir()
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        os.chdir(gone)
        gone.rmdir()
        with self.assertRaises(system.Absent):
            system.working_folder()  # the working folder was taken away
        os.chdir(here)

    def test_what_the_system_answers_in_the_middle_of_a_call_is_its_outcome(self) -> None:
        """A name gone between the listing and its look is not listed; an error while listing, after an open or
        while reading is the call's outcome, never a part answer."""
        real_scandir = os.scandir

        class Gone:
            name = "gone.md"

            def stat(self, follow_symlinks: bool = True) -> os.stat_result:
                raise FileNotFoundError(errno.ENOENT, "gone")

            def is_symlink(self) -> bool:
                return False

            def is_dir(self, follow_symlinks: bool = True) -> bool:
                return False

            def is_file(self, follow_symlinks: bool = True) -> bool:
                return False

        @contextlib.contextmanager
        def with_a_name_gone(descriptor: int):
            with real_scandir(descriptor) as entries:
                yield [Gone(), *entries]

        for looks in (False, True):
            with self.subTest(looks=looks), mock.patch.object(system.os, "scandir", with_a_name_gone):
                self.assertNotIn("gone.md", [entry.name for entry in system.listing(self.folder, looks=looks)])

        @contextlib.contextmanager
        def failing(descriptor: int):
            def entries():
                raise OSError(errno.EIO, "the disk failed")
                yield  # pragma: no cover

            yield entries()

        with mock.patch.object(system.os, "scandir", failing), self.assertRaises(system.Other) as listed:
            system.listing(self.folder)
        self.assertEqual(listed.exception.error, "EIO")
        with self.assertRaises(system.NotAFolder):
            system.listing(self.page / "inner")  # a part of the path is a file
        with mock.patch.object(system.os, "fstat", side_effect=OSError(errno.EIO, "the disk failed")), \
                self.assertRaises(system.Other):
            system.read_bytes(self.page)
        with mock.patch.object(system.os, "read", side_effect=OSError(errno.EIO, "the disk failed")), \
                self.assertRaises(system.Other):
            system.read_bytes(self.page)

    def test_a_write_the_system_refuses_is_its_outcome(self) -> None:
        """What a write meets is told by the step that met it: a folder that cannot be made in, a mode that
        cannot be set, a cut that fails, a path under a file, a fifo where a whole file goes."""
        if os.geteuid() == 0:
            self.skipTest("the superuser writes in every folder")
        self.addCleanup(os.chmod, self.inner, 0o700)
        os.chmod(self.inner, 0o500)
        with self.assertRaises(system.Denied) as made:
            system.make_folder(self.inner / "new")
        self.assertEqual(made.exception.access, "write")
        os.chmod(self.inner, 0o700)
        with mock.patch.object(system.os, "chmod", side_effect=PermissionError(errno.EACCES, "refused")), \
                self.assertRaises(system.Denied):
            system.make_folder(self.inner / "new")
        log = self.folder / "turns.jsonl"
        log.write_bytes(b"one\ntwo\n")
        with mock.patch.object(system.os, "ftruncate", side_effect=OSError(errno.EIO, "the disk failed")), \
                self.assertRaises(system.Other):
            system.cut_back(log, 4)
        self.assertEqual(log.read_bytes(), b"one\ntwo\n")
        with self.assertRaises(system.NotAFolder):
            system.write_whole(self.page / "inner.md", b"x")
        with self.assertRaises(system.NotAFile) as fifo:
            system.write_whole(self.fifo, b"x")
        self.assertEqual(fifo.exception.found, "fifo")
        self.assertTrue(stat.S_ISFIFO(os.lstat(self.fifo).st_mode))

    def test_a_step_that_fails_partway_through_a_write_is_the_writes_outcome(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("the superuser writes in every folder")
        target = self.folder / "count.md"
        taken = self.folder / ".count.md.aaaaaaaa.tmp"
        taken.write_bytes(b"")
        with mock.patch.object(system.secrets, "choice", return_value="a"), self.assertRaises(system.Other) as names:
            system.write_whole(target, b"x")  # every temporary name it tries is taken
        self.assertEqual(names.exception.error, "EEXIST")
        taken.unlink()
        target.write_bytes(b"before")
        with mock.patch.object(system.os, "replace", side_effect=OSError(errno.EIO, "the disk failed")), \
                self.assertRaises(system.Other):
            system.write_whole(target, b"after")
        with mock.patch.object(system.os, "replace", side_effect=KeyboardInterrupt), \
                self.assertRaises(KeyboardInterrupt):
            system.write_whole(target, b"after")
        self.assertEqual(target.read_bytes(), b"before")
        self.assertEqual([path.name for path in self.folder.iterdir() if path.name.endswith(".tmp")], [])
        self.addCleanup(os.chmod, self.inner, 0o700)
        os.chmod(self.inner, 0o500)
        with self.assertRaises(system.Denied):
            system.create_new(self.inner / "key", b"rye")
        os.chmod(self.inner, 0o700)
        with mock.patch.object(system.os, "write", side_effect=OSError(errno.ENOSPC, "the disk is full")), \
                self.assertRaises(system.Other) as full:
            system.create_new(self.inner / "key", b"rye")
        self.assertEqual((full.exception.error, (self.inner / "key").exists()), ("ENOSPC", False))
        log = self.folder / "turns.jsonl"
        with mock.patch.object(system.os, "fsync", side_effect=OSError(errno.EIO, "the disk failed")), \
                self.assertRaises(system.Other):
            system.append(log, b"one\n")
        with mock.patch.object(system.fcntl, "flock", side_effect=OSError(errno.ENOLCK, "no locks")), \
                self.assertRaises(system.Other):
            system.append(log, b"two\n")
        self.addCleanup(os.chmod, self.folder, 0o700)
        os.chmod(self.folder, 0o500)
        with self.assertRaises(system.Denied) as kept:
            system.remove(self.page)
        self.assertEqual(kept.exception.access, "write")
        os.chmod(self.folder, 0o700)
        with mock.patch.object(system.os, "fsync", side_effect=OSError(errno.EIO, "the disk failed")), \
                self.assertRaises(system.Other):
            system.sync_folder(self.folder)
        with mock.patch.object(system.os.path, "realpath", side_effect=PermissionError(errno.EACCES, "denied")), \
                self.assertRaises(system.Denied):
            system.real_path(self.page)
        real_chmod = os.chmod

        def without_lchmod(path, mode, *, follow_symlinks=True):
            if not follow_symlinks:
                raise NotImplementedError("this system cannot change a link's own mode")
            return real_chmod(path, mode)

        with mock.patch.object(system.os, "chmod", without_lchmod):
            with self.assertRaises(system.Link):
                system.set_mode(self.link, 0o600)
            system.set_mode(self.page, 0o604)
        self.assertEqual(stat.S_IMODE(self.page.stat().st_mode), 0o604)

    def test_a_denial_in_a_working_folder_that_cannot_be_entered_names_it(self) -> None:
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        self.addCleanup(os.chmod, self.folder, 0o700)
        os.chdir(self.folder)
        os.chmod(self.folder, 0o000)
        if os.access(".", os.X_OK):
            self.skipTest("this account enters every folder")
        denied = system.outcome_of(OSError(errno.EACCES, "denied"), "tray.md", "read")
        self.assertEqual((type(denied), denied.access, denied.where()), (system.Denied, "enter", "."))

    def test_a_lock_is_taken_once_and_its_record_is_emptied_on_release(self) -> None:
        path = self.folder / "build.lock"
        self.assertTrue(system.lock_free(path))
        first = system.lock(path)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertTrue(first.take())
        first.record("held by the oven bot, a long record")
        first.record("held again")
        self.assertEqual(path.read_text(encoding="utf-8"), "held again")
        self.assertFalse(system.lock_free(path))
        second = system.lock(path)
        self.assertFalse(second.take())
        second.close()
        second.close()
        first.release()
        self.assertEqual((path.read_bytes(), first.descriptor), (b"", -1))
        self.assertTrue(system.lock_free(path))
        with self.assertRaises(system.Link):
            system.lock(self.link)
        with self.assertRaises(system.NotAFile):
            system.lock_free(self.inner)
        broken = system.lock(path)
        self.addCleanup(broken.close)
        with mock.patch.object(system.fcntl, "flock", side_effect=OSError(errno.ENOLCK, "no locks")):
            with self.assertRaises(system.Other):
                broken.take()
            with self.assertRaises(system.Other):
                system.lock_free(path)
        with mock.patch.object(system.os, "ftruncate", side_effect=OSError(errno.EIO, "the disk failed")), \
                self.assertRaises(system.Other):
            broken.record("held by the till bot")

    def test_sqlite_is_handed_the_descriptors_name_only_where_it_names_the_open_file(self) -> None:
        # What the system answers for /dev/fd/N is stood in for, so the choice is tried as on any system.
        with open(self.page, "rb") as opened:
            descriptor = opened.fileno()
            checked = os.fstat(descriptor)
            with mock.patch.object(system.os, "stat", return_value=checked):
                self.assertEqual(system._descriptor_path(descriptor, checked), f"/dev/fd/{descriptor}")
            with mock.patch.object(system.os, "stat", return_value=os.stat(self.inner)):
                self.assertIsNone(system._descriptor_path(descriptor, checked))
            with mock.patch.object(system.os, "stat", side_effect=OSError(errno.ENOENT, "no such file")):
                self.assertIsNone(system._descriptor_path(descriptor, checked))

    def _index(self) -> Path:
        path = self.folder / "trays.sqlite"
        made = sqlite3.connect(path)
        made.execute("CREATE TABLE trays (name TEXT)")
        made.commit()
        made.close()
        return path

    def test_an_index_is_read_without_writing_and_made_only_by_a_write(self) -> None:
        index = self._index()
        os.chmod(index, 0o400)
        self.addCleanup(os.chmod, index, 0o600)
        opened, rows = system.open_index(
            index, immutable=False,
            first=lambda connection: connection.execute("SELECT count(*) FROM trays").fetchone(),
        )
        opened.close()
        self.assertEqual(rows, (0,))
        absent = self.folder / "absent.sqlite"
        with self.assertRaises(system.Absent):
            system.open_index(absent, immutable=False)
        self.assertFalse(absent.exists())
        made, _ = system.open_index(absent, immutable=False, write=True)
        made.close()
        self.assertEqual(stat.S_IMODE(absent.stat().st_mode), 0o600)

    def test_a_denial_injected_into_an_open_names_the_access_the_open_asked_for(self) -> None:
        index = self._index()
        for write, access in ((False, "read"), (True, "write")):
            with self.subTest(write=write), system.watching(fail_calls={1: "denied"}), \
                    self.assertRaises(system.Denied) as denied:
                system.open_index(index, immutable=False, write=write)
            self.assertEqual(denied.exception.access, access)

    def test_a_write_connection_commits_each_statement_at_once(self) -> None:
        index = self._index()
        opened, _ = system.open_index(
            index, immutable=False, write=True,
            first=lambda connection: connection.execute("INSERT INTO trays VALUES ('rye')"),
        )
        self.addCleanup(opened.close)
        other = sqlite3.connect(index)
        self.addCleanup(other.close)
        self.assertEqual(other.execute("SELECT name FROM trays").fetchall(), [("rye",)])

    def test_what_stops_the_open_at_the_named_point_is_the_calls_answer(self) -> None:
        index = self._index()

        def stop(**info: object) -> None:
            raise system.Busy(str(info["path"]), note="a hook")

        with system.at(system.POINT_INDEX_OPEN, stop), self.assertRaises(system.Busy):
            system.open_index(index, immutable=False)

    def test_a_connection_whose_first_statement_fails_is_closed(self) -> None:
        index = self._index()
        made: list[sqlite3.Connection] = []

        def first(connection: sqlite3.Connection) -> None:
            made.append(connection)
            raise sqlite3.OperationalError("no such table: loaves")

        with self.assertRaises(sqlite3.OperationalError):
            system.open_index(index, immutable=False, first=first)
        with self.assertRaises(sqlite3.ProgrammingError):
            made[0].execute("SELECT 1")

    def test_an_open_past_its_deadline_closes_what_it_made_when_it_ends(self) -> None:
        index = self._index()
        made: list[sqlite3.Connection] = []
        descriptors: list[int] = []
        go = threading.Event()
        real = system._open_checked

        def recording(*args: object, **kwargs: object) -> tuple[int, os.stat_result]:
            found = real(*args, **kwargs)  # type: ignore[arg-type]
            descriptors.append(found[0])
            return found

        def first(connection: sqlite3.Connection) -> None:
            made.append(connection)
            go.wait(10)

        with mock.patch.object(system, "_open_checked", recording), self.assertRaises(system.Busy):
            system.open_index(index, immutable=False, first=first, deadline_seconds=0.2)
        go.set()
        for thread in threading.enumerate():
            if thread.name == "memory-forest index open":
                thread.join(10)
        with self.assertRaises(sqlite3.ProgrammingError):
            made[0].execute("SELECT 1")
        with self.assertRaises(OSError):
            os.fstat(descriptors[0])

    def test_the_descriptor_is_let_go_once(self) -> None:
        opened, _ = system.open_index(self._index(), immutable=False)
        self.addCleanup(opened.close)
        descriptor = opened.descriptor
        opened.release_descriptor()
        self.assertEqual(opened.descriptor, -1)
        with self.assertRaises(OSError):
            os.fstat(descriptor)
        opened.release_descriptor()

    def test_a_release_lets_the_lock_go_while_a_copy_of_its_descriptor_is_open(self) -> None:
        # A copy of the descriptor (a child forked while the lock was held keeps one) keeps a lock that closing
        # alone does not let go of: the release unlocks before it closes.
        path = self.folder / "proof.lock"
        held = system.lock(path)
        self.assertTrue(held.take())
        copy = os.dup(held.descriptor)
        self.addCleanup(os.close, copy)
        held.release()
        self.assertTrue(system.lock_free(path))

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
