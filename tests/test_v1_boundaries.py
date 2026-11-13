"""A removed call, a blocked open and an unwatched capability must each fail a test."""
from __future__ import annotations

import ast
import contextlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import bounds
import door_rules
import door_runtime
from memory_forest import derived, mcp, search, system
from memory_forest.errors import MemoryForestError
from test_v1_find import FILES
from v1_support import ForestTestCase
import test_v1_door as door
from wait_child import read_fifo


class SyntaxTests(unittest.TestCase):
    def test_the_one_way_out_refuses_an_unknown_code_or_a_wrong_move(self):
        from memory_forest.leaving import body
        for refusal in (MemoryForestError("synthetic_unknown", "unknown", next_step="fix"),
                        MemoryForestError("invalid_path", "wrong move", next_step="retry")):
            for reader in ("owner", "agent", "group"):
                shown = body(refusal, reader=reader, masking=None, plain=True)
                self.assertEqual((shown["code"], shown["next"]), ("internal_error", "report"))

    def test_each_import_and_os_permission_is_needed_and_documented(self):
        for module in door_rules.ALLOWED_IMPORTS:
            with self.subTest(module=module):
                probe = ast.parse(f"import {module}")
                self.assertEqual(door_rules.forbidden_calls(probe), [])
                with mock.patch.object(door_rules, "ALLOWED_IMPORTS", door_rules.ALLOWED_IMPORTS - {module}):
                    self.assertTrue(door_rules.forbidden_calls(probe))
        for name, why in door_rules.ALLOWED_OS.items():
            self.assertTrue(why)
            probe = ast.parse(f"import os\nx = os.{name}")
            self.assertEqual(door_rules.forbidden_calls(probe), [])
            with mock.patch.dict(door_rules.ALLOWED_OS, clear=True):
                self.assertTrue(door_rules.forbidden_calls(probe))
        for name in door_rules.ALLOWED_OS_PATH:
            probe = ast.parse(f"import os\nx = os.path.{name}")
            self.assertEqual(door_rules.forbidden_calls(probe), [])
            with mock.patch.object(door_rules, "ALLOWED_OS_PATH", door_rules.ALLOWED_OS_PATH - {name}):
                self.assertTrue(door_rules.forbidden_calls(probe))

    def test_enter_and_read_facts_are_acts_and_only_write_uses_may(self):
        tree = ast.parse(Path(derived.__file__).read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        for node in calls:
            if node.func.attr == "may":
                self.assertGreater(len(node.args), 1)
                self.assertIsInstance(node.args[1], ast.Constant)
                self.assertEqual(node.args[1].value, "write")
        self.assertTrue(any(n.func.attr == "enter_folder" for n in calls))
        self.assertTrue(any(n.func.attr == "listing" for n in calls))
        self.assertTrue(any(n.func.attr == "probe_read" for n in calls))

    def test_each_escape_family_is_refused(self):
        probes = [
            'import subprocess; subprocess.run(["true"])', 'import os; os.system("true")',
            'import os; os.popen("true")', 'import os; os.posix_spawn("x", [], {})',
            'import sqlite3; sqlite3.Connection("x")', 'db.execute("ATTACH DATABASE ? AS other", ("x",))',
            'db.execute("VACUUM INTO ?", ("x",))', 'db.backup(other)', 'import mmap; mmap.mmap(0, 1)',
            'import gzip; gzip.open("x")', 'import zipfile; zipfile.ZipFile("x")',
            'import tarfile; tarfile.open("x")', 'import linecache; linecache.getline("x", 1)',
            'import logging; logging.FileHandler("x")', 'import os; os.listxattr("x")',
            'import os; os.chflags("x", 0)', 'import os; os.pathconf("x", "PC_NAME_MAX")',
            'map(open, ["x"])', 'import functools; functools.partial(open, "x")',
            'getattr(p, "read_text")()', 'getattr(p, name)()', 'forest.read_text()', 'view.read_text()',
            'from . import anything; anything.read_text()', 'f = eval', 'f = compile', 'f = __import__',
            'import os; os.walk("x")', 'import os; os.scandir("x")', 'import os; f = os.fdopen',
            'from os import stat as lookup; lookup("x")',
            'from os import fdopen as reader; f = reader',
            'from sqlite3 import Connection as database; database("x")',
        ]
        for code in probes:
            with self.subTest(code=code):
                self.assertTrue(door_rules.forbidden_calls(ast.parse(code)), code)

    def test_a_hang_exits_nonzero_and_dumps_every_thread(self):
        code = '''import unittest, threading, bounds
class Never(unittest.TestCase):
 def test_wait(self): threading.Event().wait()
unittest.main()
'''
        env = dict(os.environ, FOREST_TEST_BOUND="0.3", PYTHONPATH=os.pathsep.join([str(Path(__file__).resolve().parent), *sys.path]))
        child = subprocess.run([sys.executable, "-c", code], capture_output=True, env=env, timeout=10)
        self.assertNotEqual(child.returncode, 0)
        self.assertIn(b"Timeout", child.stderr)
        self.assertIn(b"test_wait", child.stderr)
        self.assertIn(b"bounds.py", child.stderr)

    def test_direct_module_selection_installs_the_same_bound(self):
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ, PYTHONPATH=str(root / "src"))
        name = "tests.test_v1_mutation_tool.MutationToolTests.test_timeout_is_never_an_assertion_kill"
        child = subprocess.run([sys.executable, "-m", "unittest", name], cwd=root, env=env,
                               capture_output=True, timeout=15)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertIn(b"Ran 1 test", child.stderr)
        self.assertIn(b"superuser denial rows skipped: 0", child.stderr)

    def test_superuser_skips_are_counted_and_require_the_explicit_override(self):
        code = """import unittest, bounds
@unittest.skip("the superuser is never denied")
class Denials(unittest.TestCase):
 def test_one(self): pass
 def test_two(self): pass
unittest.main()
"""
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(Path(__file__).resolve().parent), *sys.path]))
        env.pop("FOREST_TESTS_ALLOW_SUPERUSER", None)
        child = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, timeout=10)
        self.assertNotEqual(child.returncode, 0)
        self.assertIn(b"superuser denial rows skipped: 2", child.stderr)
        env["FOREST_TESTS_ALLOW_SUPERUSER"] = "1"
        child = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, timeout=10)
        self.assertEqual(child.returncode, 0, child.stderr)

    def test_the_interpreter_sees_bypasses_the_old_wrap_missed(self):
        import memory_forest.search as module
        # Code placed in the package's namespace gives the stack the package's real filename.
        probes = [
            "open(path).close()", "import sqlite3; sqlite3.Connection(path).close()",
            "import os; os.stat(path)", "import linecache; linecache.getlines(path)",
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "decoy")
            Path(path).write_text("a decoy")
            for source in probes:
                code = compile(source, module.__file__, "exec")
                with self.subTest(source=source):
                    with self.assertRaises(AssertionError), door_runtime.watching():
                        exec(code, {"__name__": module.__name__, "path": path})


class BlockTests(ForestTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.make_forest(dict(FILES))
        search.build(self.root)
        self.path = self.root / ".forest/index.sqlite"

    @unittest.skipIf(os.geteuid() == 0, "the superuser is not denied directory search or listing")
    def test_entering_and_listing_are_distinct_kernel_acts(self):
        try:
            os.chmod(self.root, 0o600)
            with self.assertRaises(system.Denied):
                system.enter_folder(self.root)
            self.assertTrue(system.listing(self.root))
            os.chmod(self.root, 0o100)
            system.enter_folder(self.root)
            with self.assertRaises(system.Denied):
                system.listing(self.root)
        finally:
            os.chmod(self.root, 0o700)

    def test_every_tool_refuses_a_missing_forest_marker_before_it_runs(self):
        (self.root / "forest.toml").unlink()
        for name in door.agent_tools():
            for group in (None, "synthetic-room"):
                arguments = dict(door.TOOL_ARGUMENTS[name])
                if group:
                    arguments["agent"] = door.READER
                reply = mcp._call(self.root, door.READER, {"name": name, "arguments": arguments}, "stdio", group=group)
                import json
                answer = json.loads(reply["content"][0]["text"])
                self.assertTrue(reply["isError"])
                self.assertEqual((answer["code"], answer["next"]), ("no_forest", "fix"))

    def test_the_low_level_open_rechecks_a_sidecar_after_a_sqlite_error(self):
        fifo = Path(str(self.path) + "-shm")
        try:
            try:
                with system.at(system.POINT_INDEX_OPEN, lambda *, path: os.mkfifo(fifo)):
                    with self.assertRaises(system.NotAFile) as caught:
                        system.open_index(self.path, immutable=False,
                                          first=lambda db: db.execute("SELECT * FROM metadata").fetchall())
            except sqlite3.Error as error:
                self.fail(f"the sidecar's real kind must replace SQLite's {type(error).__name__}")
            self.assertEqual(caught.exception.path, str(fifo))
        finally:
            fifo.unlink(missing_ok=True)

    def test_a_direct_open_rejects_each_known_sidecar_before_sqlite_runs(self):
        for suffix in ("-journal", "-wal", "-shm"):
            fifo = Path(str(self.path) + suffix)
            os.mkfifo(fifo)
            reached = threading.Event()
            try:
                with system.at(system.POINT_INDEX_OPEN, lambda *, path: reached.set()):
                    with self.assertRaises(system.NotAFile):
                        system.open_index(self.path, immutable=False, deadline_seconds=0.02)
                self.assertFalse(reached.is_set(), suffix)
            finally:
                fifo.unlink(missing_ok=True)

    def test_twenty_real_journal_blocks_end_without_leaving_a_worker(self):
        for _ in range(20):
            fifo = Path(str(self.path) + "-journal")
            def swap(*, path):
                os.mkfifo(fifo)
            with system.at(system.POINT_INDEX_OPEN, swap):
                with self.assertRaises(system.NotAFile) as caught:
                    system.open_index(self.path, immutable=False, first=lambda db: db.execute("SELECT * FROM metadata").fetchall(),
                                      deadline_seconds=0.02)
            self.assertEqual(caught.exception.path, str(fifo))
            self.assertIsNone(system.abandoned_since(self.path))
            self.assertNotIn(str(self.path), system._LIVE_OPEN)
            fifo.unlink()

    def test_a_real_locked_open_counts_once_and_status_refuses_twenty_times(self):
        ready = self.path.parent / "held"
        os.mkfifo(ready)
        script = '''import sqlite3, sys
c = sqlite3.connect(sys.argv[1], isolation_level=None)
c.execute("PRAGMA locking_mode=EXCLUSIVE")
c.execute("BEGIN EXCLUSIVE")
with open(sys.argv[2], "w") as f: f.write("held")
sys.stdin.read()
c.close()
'''
        child = subprocess.Popen([sys.executable, "-c", script, str(self.path), str(ready)], stdin=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        read_fifo(child, str(ready), what="exclusive SQLite lock", seconds=10)
        ended = threading.Event()
        clear = system._clear_abandoned
        def cleared(path):
            clear(path)
            ended.set()
        try:
            with mock.patch.object(system, "_clear_abandoned", cleared):
                with self.assertRaises(system.Busy):
                    system.open_index(self.path, immutable=False, first=lambda db: db.execute("SELECT * FROM metadata"),
                                      busy_seconds=30, deadline_seconds=0.02)
                self.assertIsNotNone(system.abandoned_since(self.path))
                for _ in range(20):
                    started = time.monotonic()
                    with self.assertRaises(MemoryForestError) as refused:
                        search.status(self.root)
                    self.assertEqual(refused.exception.details["state"], "abandoned")
                    self.assertEqual(refused.exception.next_step, "report")
                    self.assertLess(time.monotonic() - started, 1)
                    workers = [t for t in threading.enumerate() if t.name == "memory-forest index open"]
                    self.assertEqual(len(workers), 1)
                child.stdin.close()
                child.wait(timeout=10)
                self.assertTrue(ended.wait(10), "abandoned worker never closed")
        finally:
            if child.stdin and not child.stdin.closed:
                child.stdin.close()
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
            child.stderr.close()
        self.assertIsNone(system.abandoned_since(self.path))

    def test_a_blocked_worker_is_counted_even_after_the_index_path_changed(self):
        release, started, ended = threading.Event(), threading.Event(), threading.Event()
        clear = system._clear_abandoned
        def cleared(path):
            clear(path)
            ended.set()
        def first(db):
            data = self.path.read_bytes()
            self.path.unlink()
            self.path.write_bytes(data)
            started.set()
            if not release.wait(10):
                raise AssertionError("the test never released the blocked callback")
        with mock.patch.object(system, "_clear_abandoned", cleared):
            try:
                with self.assertRaises(system.Changed):
                    system.open_index(self.path, immutable=False, first=first, deadline_seconds=0.02)
                self.assertTrue(started.wait(1))
                self.assertIsNotNone(system.abandoned_since(self.path))
                for _ in range(20):
                    with self.assertRaises(system.Busy) as refusal:
                        system.open_index(self.path, immutable=False, first=first, deadline_seconds=0.02)
                    self.assertEqual(refusal.exception.note, "abandoned")
                    self.assertEqual(sum(t.name == "memory-forest index open" for t in threading.enumerate()), 1)
            finally:
                release.set()
                self.assertTrue(ended.wait(10), "the swapped worker did not close")

    def test_a_probe_process_failure_is_not_reported_as_a_disk_errno(self):
        failures = (subprocess.TimeoutExpired(["synthetic"], 1),
                    subprocess.CalledProcessError(1, ["synthetic"]),
                    OSError("synthetic process start failure"))
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), mock.patch("subprocess.run", side_effect=failure):
                with self.assertRaises(RuntimeError):
                    system.probe_read(self.path)
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout=b"not an errno")):
            with self.assertRaises(RuntimeError):
                system.probe_read(self.path)

    def test_probing_a_sidecar_keeps_this_process_posix_locks(self):
        import fcntl
        path = self.path.with_name("probe-wal")
        path.write_bytes(b"synthetic sidecar")
        fd = os.open(path, os.O_RDWR)
        try:
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            system.probe_read(path)
            code = """import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
try:
 fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
 print("free")
except BlockingIOError:
 print("held")
finally:
 os.close(fd)
"""
            child = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, timeout=10)
            self.assertEqual(child.returncode, 0, child.stderr)
            self.assertEqual(child.stdout.strip(), b"held")
            if os.geteuid() != 0:
                os.chmod(path, 0)
                with self.assertRaises(system.Denied):
                    system.probe_read(path)
        finally:
            os.close(fd)
            path.unlink()

    def test_a_fifo_swapped_for_the_index_itself_is_released_and_compared(self):
        kept = self.path.with_name("kept.sqlite")
        def swap(*, path):
            os.rename(self.path, kept)
            os.mkfifo(self.path)
        try:
            with system.at(system.POINT_INDEX_OPEN, swap):
                with self.assertRaises(system.Changed):
                    system.open_index(self.path, immutable=False, first=lambda db: db.execute("SELECT * FROM metadata"),
                                      deadline_seconds=0.02)
            self.assertIsNone(system.abandoned_since(self.path))
            self.assertNotIn(str(self.path), system._LIVE_OPEN)
        finally:
            self.path.unlink(missing_ok=True)
            os.rename(kept, self.path)

    def test_every_beside_name_is_checked_even_when_it_is_a_link(self):
        for suffix in ("-journal", "-wal", "-shm"):
            beside = Path(str(self.path) + suffix)
            beside.symlink_to(self.path)
            try:
                facts = search._facts(self.root, open_index=False).facts
                self.assertEqual(facts.beside, "not_a_file")
                self.assertEqual(facts.part, beside.name)
                with system.watching() as trace, self.assertRaises(MemoryForestError) as caught:
                    search.rows(self.root, "SELECT 1")
                self.assertFalse(any(name in {"open_index", "open_file"} for name, _ in trace.calls), "reject a known non-file before any open")
                self.assertEqual(caught.exception.code, "index_unreadable")
                self.assertIn("beside", caught.exception.message)
            finally:
                beside.unlink()


class TraceTests(ForestTestCase):
    def test_every_reader_compares_after_its_last_statement_in_both_modes(self):
        from test_v1_sweep import call
        for readonly in (False, True):
            for which in [*door.agent_commands(), *door.agent_tools(), "status"]:
                root = self.make_forest(dict(FILES), name=f"case-{readonly}-{str(which)}")
                search.build(root)
                # The same final-compare trace rule covers read and writer connections.
                folder = root / ".forest"
                if readonly:
                    os.chmod(folder, 0o500)
                try:
                    with system.watching() as trace:
                        search.status(root) if which == "status" else call(root, which)
                    opens = [i for i, (kind, name) in enumerate(trace.events) if kind == "call" and name == "open_index"]
                    if not opens:
                        continue
                    for opening in opens:
                        preceding = trace.events[:opening]
                        self.assertTrue(any(x == ("call", "look") for x in preceding))
                    statements = [i for i, event in enumerate(trace.events) if event[0] == "statement"]
                    if statements:
                        self.assertIn(("call", "same_index"), trace.events[statements[-1]+1:], (which, readonly, trace.events))
                finally:
                    os.chmod(folder, 0o700)
