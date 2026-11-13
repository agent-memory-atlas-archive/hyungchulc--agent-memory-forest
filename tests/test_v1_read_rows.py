"""The one read of the index: it is given a statement, it returns rows, and the connection does not leave.

The compare after the statement, the compare after a failed read, and an index that is gone at the open
are each tried by doing them. The bakery is invented.
"""

from __future__ import annotations

import sqlite3
import unittest

import bounds  # installs a deadline for every unittest invocation
from unittest import mock

from memory_forest import derived, search, system
from memory_forest.errors import MemoryForestError

from test_v1_find import FILES
from v1_support import ForestTestCase


def _after_last_statement(events: list[tuple[str, str]]) -> list[tuple[str, str]]:
    last = max(index for index, event in enumerate(events) if event[0] == "statement")
    return events[last + 1 :]


class ReadRowsTests(ForestTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_forest(dict(FILES))
        search.build(self.root)
        self.index = self.root / ".forest" / "index.sqlite"

    def test_a_statement_returns_rows_and_no_connection_leaves(self) -> None:
        self.assertFalse(hasattr(search, "connect"))
        self.assertFalse(hasattr(search, "require_index"))
        found = search.rows(self.root, "SELECT COUNT(*) FROM units")
        self.assertIsInstance(found, list)
        self.assertIsInstance(found[0], tuple)
        self.assertGreater(found[0][0], 0)

    def test_the_identity_is_compared_after_the_last_statement_and_the_three_names_are_looked_at(self) -> None:
        with system.watching() as watch:
            search.rows(self.root, "SELECT COUNT(*) FROM files")
        self.assertTrue(
            any(kind == "call" and name == "same_index" for kind, name in _after_last_statement(watch.events)),
            watch.events,
        )
        opened = next(index for index, (name, _path) in enumerate(watch.calls) if name == "open_index")
        for suffix in ("-journal", "-wal", "-shm"):
            earlier = [
                index for index, (name, path) in enumerate(watch.calls)
                if name == "look" and path.endswith(suffix)
            ]
            self.assertTrue(earlier and min(earlier) < opened, suffix)

    def test_a_file_replaced_after_the_statement_is_not_answered(self) -> None:
        """The compare after a successful read. Without it the rows leave."""
        path = self.index
        original = system.IndexConnection.execute

        def execute(connection: sqlite3.Connection, sql: str, parameters: object = ()) -> sqlite3.Cursor:
            result = original(connection, sql, parameters)
            if sql == "SELECT COUNT(*) FROM files":
                data = path.read_bytes()
                path.unlink()
                path.write_bytes(data)
            return result

        with mock.patch.object(system.IndexConnection, "execute", execute):
            with self.assertRaises(MemoryForestError) as caught:
                search.rows(self.root, "SELECT COUNT(*) FROM files")
        self.assertEqual(caught.exception.code, "index_busy")
        self.assertIn(derived.CLAIMS["index_changed"].sentence, caught.exception.message)

    def test_a_failed_read_compares_before_it_chooses_the_error(self) -> None:
        """``_failed_read`` compares. Without that compare the SQLite error's own class leaves."""
        path = self.index
        original = system.IndexConnection.execute

        def execute(connection: sqlite3.Connection, sql: str, parameters: object = ()) -> sqlite3.Cursor:
            if sql == "SELECT COUNT(*) FROM files":
                data = path.read_bytes()
                path.unlink()
                path.write_bytes(data)
                raise sqlite3.OperationalError("no such table: units")
            return original(connection, sql, parameters)

        with mock.patch.object(system.IndexConnection, "execute", execute):
            with self.assertRaises(MemoryForestError) as caught:
                search.rows(self.root, "SELECT COUNT(*) FROM files")
        self.assertEqual(caught.exception.code, "index_busy")
        self.assertIn(derived.CLAIMS["index_changed"].sentence, caught.exception.message)

    def test_an_index_that_is_absent_when_the_open_runs_is_changed(self) -> None:
        """The open step counts ``absent`` as changed. Without that word the refusal is another code."""
        with system.watching() as watched:
            search.rows(self.root, "SELECT 1")
        number = next(index for index, (name, _path) in enumerate(watched.calls, start=1) if name == "open_index")
        with system.watching(fail_calls={number: "absent"}):
            with self.assertRaises(MemoryForestError) as caught:
                search.rows(self.root, "SELECT 1")
        self.assertEqual(caught.exception.code, "index_busy")
        self.assertEqual(caught.exception.details["state"], "busy")
        self.assertIn(derived.CLAIMS["index_changed"].sentence, caught.exception.message)

    def test_a_non_sqlite_failure_still_compares_after_the_read(self):
        def read(db):
            db.execute("SELECT 1").fetchall()
            raise OSError("a synthetic read failure")
        with system.watching() as trace, self.assertRaises(OSError):
            search._read_index(self.root, read)
        self.assertIn(("call", "same_index"), _after_last_statement(trace.events))

    def test_a_failure_after_metadata_compares_before_the_reading_is_closed(self):
        def changes(reading):
            reading.index.connection.execute("SELECT 1").fetchall()
            raise OSError("a synthetic listing failure")
        with system.watching() as trace, self.assertRaises(OSError):
            search._facts(self.root, changes=changes)
        self.assertIn(("call", "same_index"), _after_last_statement(trace.events))


if __name__ == "__main__":
    unittest.main()
