"""Reset publishes a new index file, then removes the old one.

The bakery and the bots are invented. Nothing here is a real person, shop or transcript.
"""

from __future__ import annotations

import bounds  # every invocation has a deadline

import errno
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest import mock

from memory_forest import search, system
from memory_forest.errors import MemoryForestError

from test_v1_find import FILES
from v1_support import ForestTestCase
from wait_child import read_fifo, write_fifo


TILL = "06 istm/till-bot/2051-03-02.jsonl"
_HOLD_RESET = Path(__file__).resolve().parent / "launchers" / "hold_index_reset.py"


def _turn(text: str) -> str:
    return json.dumps(
        {
            "at": "2051-03-02T18:00:00+00:00",
            "id": "till-bot:shift:quorn",
            "origin": "interactive",
            "role": "agent",
            "session": "shift",
            "text": text,
        },
        sort_keys=True,
    ) + "\n"


class IndexResetTests(ForestTestCase):
    """A new index never has the path of an old one."""

    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_forest(dict(FILES))
        self.index = self.root / ".forest" / "index.sqlite"
        self.pointer = self.root / ".forest" / "index.current"

    def test_a_forest_with_no_pointer_still_opens(self) -> None:
        search.build(self.root)
        self.assertFalse(self.pointer.exists())
        self.assertEqual(search.index_relative(self.root), ".forest/index.sqlite")
        counted = search.rows(self.root, "SELECT COUNT(*) FROM units")
        self.assertGreater(counted[0][0], 0)
        self.assertIn(TILL, [page["path"] for page in search.query(self.root, "trays", reader="till-bot")["results"]])

    def test_a_removal_that_fails_after_the_pointer_moved_names_the_orphan(self) -> None:
        search.build(self.root)
        original = system.os.unlink

        def fail(path: object, *args: object, **kwargs: object) -> None:
            if Path(path).name == "index.sqlite":
                raise OSError(errno.EBUSY, "busy", str(path))
            original(path, *args, **kwargs)

        with mock.patch.object(system.os, "unlink", fail):
            result = search.reset(self.root)
        self.assertTrue(result["ok"])
        self.assertFalse(result["dry_run"])
        self.assertIn(".forest/index.sqlite", result["orphans"])
        self.assertTrue(self.index.is_file())
        name = self.pointer.read_text(encoding="utf-8").strip()
        self.assertNotEqual(name, "index.sqlite")
        self.assertEqual(result["path"], f".forest/{name}")
        self.assertFalse((self.root / ".forest" / name).exists())
        built = search.build(self.root)
        opened = self.root / built["path"]
        self.assertEqual(built["path"], f".forest/{name}")
        self.assertTrue(opened.is_file())
        self.assertNotEqual(opened, self.index)
        checked = sqlite3.connect(opened)
        try:
            self.assertEqual(checked.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertGreater(checked.execute("SELECT COUNT(*) FROM units").fetchone()[0], 0)
        finally:
            checked.close()
        self.assertEqual(self.root / search.index_relative(self.root), opened)

    def test_a_reader_keeps_the_old_file_and_the_new_file_is_whole(self) -> None:
        search.build(self.root)
        old_path = self.root / search.index_relative(self.root)
        reader = sqlite3.connect(old_path)
        reader.execute("PRAGMA query_only = ON")
        before = reader.execute("SELECT text FROM units WHERE text LIKE '%trays%'").fetchone()
        self.assertIsNotNone(before)
        search.reset(self.root)
        self.assertFalse(old_path.exists())
        with (self.root / TILL).open("a", encoding="utf-8") as handle:
            handle.write(_turn("till counted the quorn at the counter"))
        search.build(self.root)
        new_path = self.root / search.index_relative(self.root)
        self.assertNotEqual(new_path, old_path)
        self.assertEqual(
            reader.execute("SELECT text FROM units WHERE text LIKE '%trays%'").fetchone()[0], before[0]
        )
        self.assertIsNone(reader.execute("SELECT text FROM units WHERE text LIKE '%quorn%'").fetchone())
        reader.close()
        fresh = sqlite3.connect(new_path)
        try:
            self.assertEqual(fresh.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertIsNotNone(fresh.execute("SELECT 1 FROM units WHERE text LIKE '%quorn%'").fetchone())
            self.assertIsNone(fresh.execute("SELECT 1 FROM sqlite_master WHERE name = 'units' AND sql IS NULL").fetchone())
        finally:
            fresh.close()

    def test_a_symlink_or_a_folder_where_an_orphan_would_be_removed_stays(self) -> None:
        search.build(self.root)
        state = self.root / ".forest"
        stale = state / "index-aaaaaaaaaaaaaaaa.sqlite"
        stale.write_bytes(b"not a database")
        target = self.base / "keep-me"
        target.write_bytes(b"keep")
        link = state / "index-bbbbbbbbbbbbbbbb.sqlite"
        link.symlink_to(target)
        folder = state / "index-cccccccccccccccc.sqlite"
        folder.mkdir()
        result = search.reset(self.root)
        self.assertTrue(result["ok"])
        self.assertFalse(stale.exists())
        self.assertTrue(link.is_symlink())
        self.assertTrue(folder.is_dir())
        self.assertEqual(target.read_bytes(), b"keep")
        self.assertIn(".forest/index-bbbbbbbbbbbbbbbb.sqlite", result["orphans"])
        self.assertIn(".forest/index-cccccccccccccccc.sqlite", result["orphans"])
        self.assertNotIn(".forest/index-aaaaaaaaaaaaaaaa.sqlite", result["orphans"])
        self.assertNotIn(result["path"], result["orphans"])

    def test_a_pointer_that_is_not_a_file_removes_nothing(self) -> None:
        search.build(self.root)
        kept = self.index.read_bytes()
        self.pointer.mkdir()
        with self.assertRaises(MemoryForestError) as refused:
            search.reset(self.root)
        self.assertEqual(refused.exception.code, "index_cannot_open")
        self.assertEqual(refused.exception.details["state"], "pointer_unusable")
        self.assertEqual(refused.exception.next_step, "report")
        self.assertEqual(self.index.read_bytes(), kept)
        self.pointer.rmdir()
        elsewhere = self.base / "elsewhere"
        elsewhere.write_bytes(b"keep")
        self.pointer.symlink_to(elsewhere)
        with self.assertRaises(MemoryForestError) as linked:
            search.reset(self.root)
        self.assertEqual(linked.exception.code, "symlink_forbidden")
        self.assertEqual(self.index.read_bytes(), kept)
        self.assertEqual(elsewhere.read_bytes(), b"keep")

    def test_a_pointer_whose_text_is_not_a_name_is_replaced_without_following_it(self) -> None:
        search.build(self.root)
        self.pointer.write_text("../outside.sqlite\n", encoding="utf-8")
        with self.assertRaises(MemoryForestError) as refused:
            search.rows(self.root, "SELECT 1")
        self.assertEqual(refused.exception.code, "index_unreadable")
        self.assertEqual(refused.exception.command, "forest index reset")
        result = search.reset(self.root)
        self.assertTrue(result["ok"])
        self.assertRegex(self.pointer.read_text(encoding="utf-8").strip(), r"\Aindex-[0-9a-f]{16}\.sqlite\Z")
        self.assertFalse(self.index.exists())
        self.assertFalse((self.root / "outside.sqlite").exists())

    def test_two_resets_at_once_leave_one_pointer(self) -> None:
        search.build(self.root)
        held = self.base / "reset-held"
        release = self.base / "reset-release"
        os.mkfifo(held)
        os.mkfifo(release)
        proc = subprocess.Popen(
            [sys.executable, str(_HOLD_RESET), str(self.root), str(held), str(release)],
            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            read_fifo(proc, str(held), what="the reset to hold the lock")
            self.assertTrue(self.index.is_file())
            self.assertFalse(self.pointer.exists())
            with self.assertRaises(MemoryForestError) as busy:
                search.reset(self.root)
            self.assertEqual(busy.exception.code, "index_busy")
            self.assertEqual(busy.exception.next_step, "retry")
            self.assertTrue(self.index.is_file())
            self.assertFalse(self.pointer.exists())
            write_fifo(proc, str(release), b"go\n", what="the reset to take the release")
            code = proc.wait(timeout=30)
            out, err = proc.communicate()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=30)
        self.assertEqual(code, 0, err)
        body = json.loads(out)
        self.assertTrue(body["ok"])
        self.assertNotEqual(body["path"], ".forest/index.sqlite")
        self.assertEqual(self.pointer.read_text(encoding="utf-8").strip(), Path(body["path"]).name)
        self.assertFalse(self.index.exists())
        again = search.reset(self.root)
        self.assertNotEqual(again["path"], body["path"])
        self.assertEqual(self.pointer.read_text(encoding="utf-8").strip(), Path(again["path"]).name)
