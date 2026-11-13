"""The fourth review of search and the MCP server: a build that did not build, a retry that ends,
and an index whose mark matches a missing table.

The bakery and the bots are invented. Nothing here is a real person, shop or transcript.
"""

from __future__ import annotations

import json
import os
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from memory_forest import mcp, search
from memory_forest.errors import MemoryForestError
from memory_forest.locking import hold_lock

from test_v1_find import FILES, _cli, _log_call, held_in_thread
from v1_support import ForestTestCase


_DOCS = Path(__file__).resolve().parents[1] / "docs" / "tools.md"


def _sqlite_error(message: str, code: int, name: str) -> sqlite3.OperationalError:
    error = sqlite3.OperationalError(message)
    error.sqlite_errorcode = code  # type: ignore[attr-defined]
    error.sqlite_errorname = name  # type: ignore[attr-defined]
    return error


class ReviewFourTests(ForestTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_forest(dict(FILES))
        self.index = self.root / ".forest" / "index.sqlite"

    def test_a_build_that_did_not_build_is_a_refusal(self) -> None:
        search.build(self.root)
        holder = sqlite3.connect(self.index, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        try:
            code, out, err = _cli(self, ["index", "build", "--json"])
        finally:
            holder.rollback()
            holder.close()
        self.assertNotEqual(code, 0, out + err)
        body = json.loads(out)["error"]
        self.assertEqual(body["code"], "index_busy")
        self.assertEqual(body["next"], "retry")
        self.assertIn("the index file is in use", body["message"].casefold())
        self.assertNotIn("already running", body["message"].casefold())
        self.assertIn("waited", body["message"].casefold())
        self.assertGreater(body["stop_after_seconds"], body["after_seconds"])
        with held_in_thread(lambda: hold_lock(self.root, "index", wait_seconds=60, label="test")):
            locked, locked_out, locked_err = _cli(self, ["index", "build", "--json"])
        self.assertEqual(locked, 2, locked_out + locked_err)
        locked_body = json.loads(locked_out)["error"]
        self.assertEqual(locked_body["code"], "index_busy")
        self.assertIn("index lock", locked_body["message"].casefold())
        self.assertNotIn("in use", locked_body["message"].casefold())

    def test_locked_is_a_fault_and_a_protocol_error_ends_as_io(self) -> None:
        locked = search.index_error(_sqlite_error("database table is locked", 6, "SQLITE_LOCKED"), path=".forest/index.sqlite")
        self.assertEqual(locked.code, "index_failed")
        self.assertEqual(locked.next_step, "report")

        def raise_locked(path: Path, busy_seconds: float = 0.0) -> sqlite3.Connection:
            raise search.index_error(_sqlite_error("database table is locked", 6, "SQLITE_LOCKED"), path=".forest/index.sqlite")

        pauses: list[float] = []
        with mock.patch.object(search, "_open_writer", raise_locked), \
                mock.patch.object(search, "_retry_pause", lambda seconds: pauses.append(seconds)):
            with self.assertRaises(MemoryForestError) as refused:
                search.build(self.root, wait_seconds=30)
        self.assertEqual(refused.exception.code, "index_failed")
        self.assertEqual(pauses, [])

        calls = {"n": 0}
        real = search._open_writer

        def once(path: Path, busy_seconds: float = search.WRITER_TIMEOUT_SECONDS) -> sqlite3.Connection:
            calls["n"] += 1
            if calls["n"] == 1:
                raise search.index_error(_sqlite_error("locking protocol", 15, "SQLITE_PROTOCOL"), path=".forest/index.sqlite")
            return real(path, busy_seconds)

        with mock.patch.object(search, "_open_writer", once), \
                mock.patch.object(search, "_retry_pause", lambda seconds: pauses.append(seconds)):
            built = search.build(self.root, wait_seconds=30)
        self.assertFalse(built["busy"])
        self.assertEqual(calls["n"], 2)
        self.assertEqual(len(pauses), 1)
        self.assertEqual(search.index_error(_sqlite_error("locking protocol", 15, "SQLITE_PROTOCOL"), path=".forest/index.sqlite").code, "index_protocol")

        def always(path: Path, busy_seconds: float = 0.0) -> sqlite3.Connection:
            raise search.index_error(_sqlite_error("locking protocol", 15, "SQLITE_PROTOCOL"), path=".forest/index.sqlite")

        with mock.patch.object(search, "_open_writer", always):
            with self.assertRaises(MemoryForestError) as spent:
                search.build(self.root, wait_seconds=0)
        self.assertEqual(spent.exception.code, "index_io_error")
        self.assertEqual(spent.exception.next_step, "report")

    def test_a_word_search_hides_only_a_fault_of_the_query_text(self) -> None:
        search.build(self.root)
        mode = {"name": "syntax"}

        class Rows:
            def __iter__(self) -> object:
                return iter(())

            def fetchall(self) -> list[object]:
                return []

            def fetchone(self) -> None:
                return None

        class Connection:
            def execute(self, sql: str, params: object = ()) -> Rows:
                text = str(sql)
                if "MATCH" in text and mode["name"] == "syntax":
                    raise _sqlite_error('fts5: syntax error near "x"', 1, "SQLITE_ERROR")
                if "MATCH" in text and mode["name"] == "busy":
                    raise _sqlite_error("database is locked", 5, "SQLITE_BUSY")
                if "cjk_grams" in text and mode["name"] == "io":
                    raise _sqlite_error("disk I/O error", 10, "SQLITE_IOERR")
                return Rows()

            def close(self) -> None:
                return None

        with mock.patch.object(search, "connect", lambda root: Connection()):
            found = search.query(self.root, "trays", reader="till-bot")
            self.assertIsInstance(found["results"], list)
            mode["name"] = "busy"
            with self.assertRaises(MemoryForestError) as busy:
                search.query(self.root, "trays", reader="till-bot")
            mode["name"] = "io"
            with self.assertRaises(MemoryForestError) as io_error:
                search.query(self.root, "빵", reader="till-bot")
        self.assertEqual(busy.exception.code, "index_busy")
        self.assertEqual(io_error.exception.code, "index_io_error")

    def test_a_matching_mark_with_a_missing_table_or_column_is_damaged(self) -> None:
        search.build(self.root)
        database = sqlite3.connect(self.index)
        database.execute("DROP TABLE units")
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
            self.assertIn("table", refused.exception.message.casefold())
        self.assertEqual(search.reset(self.root)["ok"], True)
        self.assertFalse(search.build(self.root)["busy"])
        self.assertIn("trays", json.dumps(search.query(self.root, "trays", reader="till-bot")))

    def test_a_missing_column_is_the_same_damage(self) -> None:
        search.build(self.root)
        database = sqlite3.connect(self.index)
        database.execute("CREATE TABLE files_gap AS SELECT path FROM files")
        database.execute("DROP TABLE files")
        database.execute("ALTER TABLE files_gap RENAME TO files")
        database.commit()
        database.close()
        with self.assertRaises(MemoryForestError) as refused:
            search.status(self.root)
        self.assertEqual(refused.exception.code, "index_unreadable")
        self.assertEqual(refused.exception.next_step, "run")

    def test_indexed_false_names_the_reason(self) -> None:
        search.build(self.root)
        with held_in_thread(lambda: hold_lock(self.root, "index", wait_seconds=60, label="test")):
            reply, _version = mcp.handle_message(
                self.root, "till-bot", _log_call("the till logged against a held index"), "stdio", "2025-11-25"
            )
        body = json.loads(reply["result"]["content"][0]["text"])
        self.assertFalse(body["indexed"])
        self.assertEqual(body["reason"], "index_busy")
        os.chmod(self.index, 0o444)
        self.addCleanup(os.chmod, self.index, 0o644)
        reply, _version = mcp.handle_message(
            self.root, "till-bot", _log_call("the till logged against a read-only index"), "stdio", "2025-11-25"
        )
        readonly = json.loads(reply["result"]["content"][0]["text"])
        self.assertFalse(readonly["indexed"])
        self.assertEqual(readonly["reason"], "index_read_only")

    def test_the_document_table_lists_every_index_refusal(self) -> None:
        text = _DOCS.read_text(encoding="utf-8")
        rows: dict[str, str] = {}
        started = False
        for line in text.splitlines():
            if line.startswith("What SQLite refuses"):
                started = True
                continue
            if not started:
                continue
            if line.startswith("| `"):
                cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                rows[cells[0].strip("`")] = cells[2]
                continue
            if rows and not line.startswith("|"):
                break
        self.assertEqual(
            set(rows),
            {code for code, _next, _command in search.INDEX_REFUSALS},
        )
        for code, nxt, command in search.INDEX_REFUSALS:
            self.assertIn(nxt, rows[code], code)
            if command:
                self.assertIn(command, rows[code], code)

    @unittest.skipIf(os.geteuid() == 0, "the superuser writes a read-only folder")
    def test_a_read_only_forest_folder_can_be_searched_when_no_log_stands_beside_the_index(self) -> None:
        search.build(self.root)
        folder = self.index.parent
        os.chmod(folder, 0o555)
        try:
            if os.access(folder, os.W_OK):
                self.skipTest("this user can still write a read-only folder")
            state = search.status(self.root)
            self.assertTrue(state["read_only"])
            self.assertTrue(state["available"])
            found = search.query(self.root, "trays", reader="till-bot")
            self.assertTrue(found["read_only"])
            with self.assertRaises(MemoryForestError) as refused:
                search.build(self.root)
            self.assertEqual(refused.exception.code, "index_read_only")
        finally:
            os.chmod(folder, 0o700)
