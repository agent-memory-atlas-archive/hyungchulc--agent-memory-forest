"""The fifth review of search and the MCP server.

The bakery and the bots are invented. Nothing here is a real person, shop or transcript.
"""

from __future__ import annotations

import bounds  # every invocation has a deadline

import json
import os
import sqlite3
from pathlib import Path
from unittest import mock

from memory_forest import derived, search, timeline
from memory_forest.errors import MemoryForestError

from test_v1_find import FILES
from v1_support import ForestTestCase


# Each of these is something a regular pointer file can hold, and none of them is an index name.
_DAMAGED = (
    "not utf-8",
    "a nul byte",
    "empty",
    "white space only",
    "more than one line",
    "a name outside the pattern",
    "a parent segment",
    "an absolute path",
    "longer than a name",
)


def _damaged_bytes(kind: str) -> bytes:
    if kind == "not utf-8":
        return b"\xff\xfe"
    if kind == "a nul byte":
        return b"index.sqlite\x00"
    if kind == "empty":
        return b""
    if kind == "white space only":
        return b" \t\n"
    if kind == "more than one line":
        return b"index.sqlite\nextra\n"
    if kind == "a name outside the pattern":
        return b"notes.sqlite\n"
    if kind == "a parent segment":
        return b"../outside.sqlite\n"
    if kind == "an absolute path":
        return b"/tmp/index.sqlite\n"
    if kind == "longer than a name":
        return b"x" * (derived.POINTER_BYTES + 8)
    raise AssertionError(kind)


class PointerContentTests(ForestTestCase):
    """A pointer is absent, one index name, or damaged. Damaged never raises out of the reader."""

    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_forest(dict(FILES))
        search.build(self.root)
        self.pointer = self.root / ".forest" / "index.current"

    def test_every_other_content_is_damaged_and_reset_replaces_it(self) -> None:
        calls = (
            ("status", lambda: search.status(self.root)),
            ("query", lambda: search.query(self.root, "trays", reader="till-bot")),
            ("build", lambda: search.build(self.root)),
        )
        for kind in _DAMAGED:
            self.pointer.write_bytes(_damaged_bytes(kind))
            for name, call in calls:
                with self.subTest(kind=kind, call=name):
                    with self.assertRaises(MemoryForestError) as refused:
                        call()
                    self.assertEqual(refused.exception.code, "index_unreadable", kind)
                    self.assertEqual(refused.exception.next_step, "run")
                    self.assertEqual(refused.exception.command, "forest index reset")
            with self.subTest(kind=kind, call="reset"):
                result = search.reset(self.root)
                self.assertTrue(result["ok"])
                name = self.pointer.read_text(encoding="utf-8").strip()
                self.assertRegex(name, r"\Aindex-[0-9a-f]{16}\.sqlite\Z")
                self.assertEqual(search.index_relative(self.root), f".forest/{name}")
                status = search.status(self.root)
                self.assertTrue(status["ok"])
                self.assertEqual(status["path"], f".forest/{name}")


class ReadOnlyForestTests(ForestTestCase):
    """A folder this process cannot write is still a place to search, except when a log is uncheckpointed."""

    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_forest(dict(FILES))
        search.build(self.root)
        self.folder = self.root / ".forest"
        self.index = self.root / ".forest" / "index.sqlite"

    def _locked(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("the superuser writes a read-only folder")
        os.chmod(self.folder, 0o555)
        if os.access(self.folder, os.W_OK):
            self.skipTest("this user can still write a read-only folder")

    def test_no_log_answers_and_names_a_page_changed_since_the_build(self) -> None:
        self._locked()
        try:
            page = self.root / "04 stm/bakery/ovens/floor.md"
            page.write_text(page.read_text(encoding="utf-8") + "\nThe floor gained a line.\n", encoding="utf-8")
            lane = self.root / "06 istm/till-bot/2051-03-02.jsonl"
            lane.write_text(lane.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            state = search.status(self.root)
            self.assertTrue(state["read_only"])
            self.assertIn("04 stm/bakery/ovens/floor.md", state["stale"])
            found = search.query(self.root, "trays", reader="till-bot")
            self.assertTrue(found["read_only"])
            self.assertGreater(found.get("stale", 0), 0)
            listed = timeline.query(self.root, "2051-03-02", reader="till-bot")
            self.assertTrue(listed["read_only"])
            with self.assertRaises(MemoryForestError) as refused:
                search.build(self.root)
            self.assertEqual(refused.exception.code, "index_read_only")
            self.assertIn("forest index build", refused.exception.message)
        finally:
            os.chmod(self.folder, 0o700)

    def test_a_write_ahead_log_cannot_be_read_until_a_writer_folds_it(self) -> None:
        wal = Path(str(self.index) + "-wal")
        wal.write_bytes(b"not a checkpointed log")
        self._locked()
        try:
            for call in (
                lambda: search.status(self.root),
                lambda: search.query(self.root, "trays", reader="till-bot"),
                lambda: timeline.query(self.root, "2051-03-02", reader="till-bot"),
            ):
                with self.assertRaises(MemoryForestError) as refused:
                    call()
                self.assertEqual(refused.exception.code, "index_read_only")
                self.assertIn("forest index build", refused.exception.message)
                self.assertIn("write-ahead", refused.exception.message.casefold())
        finally:
            os.chmod(self.folder, 0o700)

    def test_no_index_in_a_folder_that_cannot_be_written(self) -> None:
        self.index.unlink()
        self._locked()
        try:
            with self.assertRaises(MemoryForestError) as refused:
                search.status(self.root)
            self.assertEqual(refused.exception.code, "index_read_only")
            self.assertIn("no search index", refused.exception.message.casefold())
            with self.assertRaises(MemoryForestError) as again:
                search.query(self.root, "trays", reader="till-bot")
            self.assertEqual(again.exception.code, "index_read_only")
        finally:
            os.chmod(self.folder, 0o700)

    def _touching(self, *, when: str):
        """Move the index's time as a writer of another account would: while SQLite opens it (``open``), or
        after its rows were read (``read``)."""
        seen = {"n": 0}

        def touch() -> None:
            seen["n"] += 1
            info = self.index.stat()
            later = info.st_mtime_ns + 1_000_000
            os.utime(self.index, ns=(later, later))

        if when == "open":
            real = search._prepare

            def prepared(connection):
                answer = real(connection)
                touch()
                return answer

            return mock.patch.object(search, "_prepare", prepared), seen
        real_rows = search._flagged

        def flagged(connection, column):
            answer = real_rows(connection, column)
            touch()
            return answer

        return mock.patch.object(search, "_flagged", flagged), seen

    def test_a_file_that_changes_while_it_is_opened_is_busy(self) -> None:
        self._locked()
        patch, seen = self._touching(when="open")
        try:
            with patch:
                for call in (lambda: search.status(self.root), lambda: search.query(self.root, "trays", reader="till-bot")):
                    with self.assertRaises(MemoryForestError) as refused:
                        call()
                    self.assertEqual(refused.exception.code, "index_busy")
                    self.assertEqual(refused.exception.next_step, "retry")
                    self.assertEqual(refused.exception.details["state"], "busy")
                    self.assertIn("changed while it was opened", refused.exception.message)
            self.assertGreaterEqual(seen["n"], 2)
        finally:
            os.chmod(self.folder, 0o700)

    def test_a_file_that_changes_after_its_rows_were_read_is_busy_and_the_same_call_answers_later(self) -> None:
        self._locked()
        patch, seen = self._touching(when="read")
        try:
            with patch:
                with self.assertRaises(MemoryForestError) as refused:
                    search.status(self.root)
            self.assertEqual(refused.exception.code, "index_busy")
            self.assertEqual(refused.exception.next_step, "retry")
            self.assertGreaterEqual(seen["n"], 1)
            state = search.status(self.root)
            found = search.query(self.root, "trays", reader="till-bot")
            self.assertTrue(state["read_only"])
            self.assertEqual(state["state"], "sound")
            self.assertTrue(found["read_only"])
            self.assertIn("trays", json.dumps(found))
        finally:
            os.chmod(self.folder, 0o700)


class WordIndexAndLeftoverTests(ForestTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_forest(dict(FILES))
        search.build(self.root)
        self.index = self.root / ".forest" / "index.sqlite"

    def test_a_missing_word_index_table_is_damaged_for_status_build_and_query(self) -> None:
        database = sqlite3.connect(self.index)
        database.execute("DROP TABLE units_words_data")
        database.commit()
        database.close()
        for call in (
            lambda: search.status(self.root),
            lambda: search.query(self.root, "trays", reader="till-bot"),
            lambda: search.build(self.root),
        ):
            with self.assertRaises(MemoryForestError) as refused:
                call()
            self.assertEqual(refused.exception.code, "index_unreadable")
            self.assertEqual(refused.exception.command, "forest index reset")

    def test_a_reset_removes_an_old_journal_and_a_pointer_temp_and_nothing_wider(self) -> None:
        journal = self.root / ".forest" / "index-0123456789abcdef.sqlite-journal"
        temp = self.root / ".forest" / ".index.current.abcd1234.tmp"
        decoy_journal = self.root / ".forest" / "index.sqlite-journal.bak"
        decoy_temp = self.root / ".forest" / ".index.current.NOTES.tmp"
        journal.write_bytes(b"old")
        temp.write_bytes(b"temp")
        decoy_journal.write_bytes(b"keep")
        decoy_temp.write_bytes(b"keep")
        result = search.reset(self.root)
        self.assertIn(".forest/index-0123456789abcdef.sqlite-journal", result["removed"])
        self.assertIn(".forest/.index.current.abcd1234.tmp", result["removed"])
        self.assertFalse(journal.exists())
        self.assertFalse(temp.exists())
        self.assertEqual(decoy_journal.read_bytes(), b"keep")
        self.assertEqual(decoy_temp.read_bytes(), b"keep")

    def test_a_refusal_after_reset_names_the_current_index(self) -> None:
        result = search.reset(self.root)
        current = search.index_relative(self.root)
        self.assertNotEqual(current, ".forest/index.sqlite")
        self.assertEqual(result["path"], current)
        path = self.root / current
        path.write_bytes(b"not a database, and longer than one header of a page " * 4)
        with self.assertRaises(MemoryForestError) as refused:
            search.status(self.root)
        self.assertEqual(refused.exception.details["path"], current)
