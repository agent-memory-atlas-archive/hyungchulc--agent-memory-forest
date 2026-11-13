"""The index's own catalogue, the edge of the public functions, and open-then-look.

The state of the derived folder (facts, decision, claims) is ``test_v1_derived``. This file walks the
index's own catalogue: every table, index and shadow table, dropped and emptied, and status, build and
query agree, and the next move cures it.

The bakery and the bots are invented. Nothing here is a real person, shop or transcript.
"""

from __future__ import annotations

import hashlib
import io
import itertools
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Callable

from memory_forest import mcp, search, system, timeline
from memory_forest.cli import forest_main
from memory_forest.errors import MemoryForestError

from test_v1_find import FILES


PAGE = "04 stm/bakery/ovens/floor.md"
LANE = "06 istm/till-bot/2051-03-02.jsonl"


def _cli(arguments: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = forest_main(arguments)
    return code, out.getvalue(), err.getvalue()


def _api(call: Callable[[], Any]) -> dict[str, Any]:
    try:
        return {"ok": call()}
    except MemoryForestError as exc:
        return {"error": exc.as_dict()}


def _write(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")



def _damaged(path: Path) -> bool:
    """Read with SQLite directly: not a database, an object of the schema missing, or a filled table empty."""
    try:
        database = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
        try:
            names = {row[0] for row in database.execute("SELECT name FROM sqlite_master")}
            if not {name for _kind, name in search.expected_objects("trigram" in search.tokenizer_name())} <= names:
                return True
            for table in sorted(names & set(_reference_filled())):
                if database.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone() is None:
                    return True
            return False
        finally:
            database.close()
    except sqlite3.Error:
        return True


_FILLED: list[str] = []


def _reference_filled() -> list[str]:
    """The tables a build of the fixture fills, from a build in a folder of its own."""
    if not _FILLED:
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch).resolve() / "reference"
            _write(root, FILES)
            search.build(root)
            database = sqlite3.connect(root / ".forest" / "index.sqlite")
            try:
                names = [row[0] for row in database.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )]
                _FILLED.extend(name for name in names if database.execute(f'SELECT 1 FROM "{name}" LIMIT 1').fetchone())
            finally:
                database.close()
    return _FILLED


def _read_meta(path: Path) -> dict[str, str]:
    database = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        return dict(database.execute("SELECT key, value FROM metadata").fetchall())
    finally:
        database.close()


def _stored_hashes(path: Path) -> dict[str, str]:
    database = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        return dict(database.execute("SELECT path, sha256 FROM files").fetchall())
    finally:
        database.close()


# ─────────────────────────── the index's own catalogue ───────────────────────────

# A table that a build of the fixture leaves empty: emptying it changes nothing, and the index is sound.
EMPTY_AT_BUILD = {
    "vectors": "no embedding provider is registered, so a build stores no vector and a query reads none",
}
# What SQLite itself will not drop.
UNDROPPABLE = re.compile(r"sqlite_autoindex_")


class CatalogueTests(unittest.TestCase):
    """Every object the index holds, dropped and emptied: status, build and query agree, and the move cures."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls._temporary.name).resolve()
        cls.built = cls.base / "built"
        _write(cls.built, FILES)
        search.build(cls.built)
        database = sqlite3.connect(cls.built / ".forest" / "index.sqlite")
        try:
            cls.catalogue = sorted(
                (str(kind), str(name)) for kind, name in database.execute("SELECT type, name FROM sqlite_master")
            )
            cls.empty = sorted(
                name
                for kind, name in cls.catalogue
                if kind == "table" and database.execute(f'SELECT 1 FROM "{name}" LIMIT 1').fetchone() is None
            )
        finally:
            database.close()
        cls.count = 0

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def _copy(self) -> Path:
        type(self).count += 1
        root = self.base / f"copy-{self.count}"
        shutil.copytree(self.built, root)
        return root

    def _changed(self, statement: str) -> Path:
        root = self._copy()
        database = sqlite3.connect(root / ".forest" / "index.sqlite")
        try:
            database.execute(statement)
            database.commit()
        finally:
            database.close()
        return root

    def test_the_catalogue_is_the_schema(self) -> None:
        names = {name for _kind, name in self.catalogue if not UNDROPPABLE.match(name)}
        expected = {name for _kind, name in search.expected_objects("trigram" in search.tokenizer_name())}
        self.assertEqual(names, expected)
        self.assertEqual(self.empty, sorted(EMPTY_AT_BUILD), "a table a build leaves empty is named with its reason")

    def test_every_object_dropped(self) -> None:
        for kind, name in self.catalogue:
            with self.subTest(kind=kind, name=name):
                if UNDROPPABLE.match(name):
                    with self.assertRaises(sqlite3.Error):
                        self._changed(f'DROP INDEX "{name}"')
                    continue
                verb = "INDEX" if kind == "index" else "TABLE"
                self._agree_and_cure(f'DROP {verb} IF EXISTS "{name}"', sound=False)

    def test_every_table_emptied(self) -> None:
        for kind, name in self.catalogue:
            if kind != "table":
                continue
            with self.subTest(name=name):
                self._agree_and_cure(f'DELETE FROM "{name}"', sound=name in EMPTY_AT_BUILD)

    def test_damage_only_the_probe_finds(self) -> None:
        """Every table still holds rows and every object is there: only a match of each word index, the select
        of each table's columns, or the list of filled tables the build recorded finds these.

        A word index's segments are gone and its structure record stays: a scan of the table reads its rows
        and the structure, and only a match reads the segments (a deleted structure record is found by the
        scan too, so it would not show that the match is needed)."""
        rows = {
            "the word index's segments": "DELETE FROM units_words_data WHERE id > 10",
            **({"the trigram index's segments": "DELETE FROM units_tri_data WHERE id > 10"}
               if "trigram" in search.tokenizer_name() else {}),
            **{f"a column of {table}": f'ALTER TABLE "{table}" RENAME COLUMN "{columns[-1]}" TO "renamed"'
               for table, columns in search._SELECTED_COLUMNS.items() if table != "metadata"},
            "a column of metadata": 'ALTER TABLE metadata RENAME COLUMN "value" TO "renamed"',
            "a list of filled tables that is not JSON": "UPDATE metadata SET value = 'units, files' WHERE key = 'filled'",
            "a list of filled tables that is not a list": "UPDATE metadata SET value = '{}' WHERE key = 'filled'",
            "a filled table that is not in the schema":
                """UPDATE metadata SET value = '["units", "no such\\"table"]' WHERE key = 'filled'""",
        }
        for label, statement in rows.items():
            with self.subTest(row=label):
                self._agree_and_cure(statement, sound=False)

    def test_an_error_inside_the_probe_is_decided_by_its_class(self) -> None:
        """SQLite fails one statement of the probe: damage is ``index_unreadable``, every other class is its own
        refusal, never damage and never sound."""
        root = self._copy()
        with system.watching() as watch:
            search.status(root)
        texts = watch.statement_texts
        first = texts.index("SELECT type, name FROM sqlite_master") + 1
        last = max(number for number, text in enumerate(texts, 1) if text.startswith('SELECT 1 FROM "'))
        expected = {
            "busy": "index_busy", "locked": "index_failed", "not_a_database": "index_unreadable",
            "damaged": "index_unreadable", "protocol": "index_io_error", "disk": "index_io_error",
            "full": "index_disk_full", "read_only": "index_failed", "other": "index_failed",
        }
        self.assertEqual(set(expected), set(system.SQLITE_FAULTS))
        for number in range(first, last + 1):
            for kind, code in expected.items():
                with self.subTest(statement=texts[number - 1][:40], fault=kind):
                    with system.watching(fail_statements={number: kind}) as watch:
                        answer = _api(lambda: search.status(root))
                    self.assertEqual(watch.injected, [(number, "statement", kind)])
                    self.assertEqual(answer.get("error", {}).get("code"), code, answer)

    def _agree_and_cure(self, statement: str, *, sound: bool) -> None:
        state = search.status(self._changed(statement)) if sound else None
        status = _api(lambda: search.status(self._changed(statement)))
        query = _api(lambda: search.query(self._changed(statement), "trays", reader="till-bot"))
        build = _api(lambda: search.build(self._changed(statement)))
        if sound:
            assert state is not None
            self.assertEqual(state["state"], "sound")
            self.assertNotIn("next", state)
            self.assertIn("ok", query)
            self.assertIn("ok", build)
            return
        if "error" in status:
            body = status["error"]
            self.assertEqual(body["code"], "index_unreadable", body)
            self.assertEqual(body["details"]["state"], "damaged")
            # The same refusal, word for word, from a query and from a build.
            self.assertEqual(query.get("error"), body, query)
            self.assertEqual(build.get("error"), body, build)
            first = body["command"]
        else:
            # An emptied metadata table is no index yet: status says so, and a query or a build makes one.
            payload = status["ok"]
            self.assertEqual(payload["state"], "missing", payload)
            self.assertIn("ok", query, query)
            self.assertIn("ok", build, build)
            first = payload["command"]
        root = self._changed(statement)
        queue = [first]
        while queue:
            words = queue.pop(0).split()
            code, out, err = _cli([*words[1:], "--root", str(root), "--json"])
            self.assertEqual(code, 0, out + err)
            answer = json.loads(out)
            if answer.get("next") == "run":
                queue.append(answer["command"])
        cured = search.status(root)
        self.assertEqual(cured["state"], "sound", cured)
        self.assertNotIn("next", cured)
        self.assertIn("trays", json.dumps(search.query(root, "trays", reader="till-bot")))


if __name__ == "__main__":
    unittest.main()


# ─────────────────────────── what leaves a public function ───────────────────────────


class BoundaryTests(unittest.TestCase):
    """An internal code never leaves: it is turned into a public one at the edge of every public function."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls._temporary.name).resolve() / "forest"
        _write(cls.root, FILES)
        search.build(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def _public_calls(self) -> list[tuple[str, Callable[[], Any]]]:
        root = self.root
        return [
            ("status", lambda: search.status(root)),
            ("query", lambda: search.query(root, "trays", reader="till-bot")),
            ("connect", lambda: search.connect(root)),
            ("require_index", lambda: search.require_index(root)),
            ("index_state", lambda: search.index_state(root)),
            ("refresh_lanes_and_daily", lambda: search.refresh_lanes_and_daily(root)),
            ("timeline.query", lambda: timeline.query(root, "2051-03-02", reader="till-bot")),
            ("timeline.subject", lambda: timeline.subject(root, "deck-oven", reader="till-bot")),
        ]

    def test_every_public_read_turns_the_internal_code_into_a_public_one(self) -> None:
        from unittest import mock

        internal = MemoryForestError("index_protocol", "inside", details={"path": ".forest/index.sqlite"})

        def raise_internal(*_args: Any, **_kwargs: Any) -> Any:
            raise internal

        with mock.patch.object(search, "_facts", raise_internal), \
                mock.patch.object(search, "_retry_pause", lambda seconds: None):
            for name, call in self._public_calls():
                with self.subTest(call=name):
                    with self.assertRaises(MemoryForestError) as refused:
                        call()
                    self.assertNotIn(refused.exception.code, search.INTERNAL_CODES)
                    self.assertEqual(refused.exception.code, "index_io_error")
                    self.assertEqual(refused.exception.next_step, "report")

    def test_every_public_write_turns_the_internal_code_into_a_public_one(self) -> None:
        from unittest import mock

        def raise_internal(*_args: Any, **_kwargs: Any) -> Any:
            raise MemoryForestError("index_protocol", "inside", details={"path": ".forest/index.sqlite"})

        with mock.patch.object(search, "_write_held", raise_internal), \
                mock.patch.object(search, "_reset_held", raise_internal), \
                mock.patch.object(search, "_retry_pause", lambda seconds: None):
            for name, call in (
                ("build", lambda: search.build(self.root)),
                ("build waiting", lambda: search.build(self.root, wait_seconds=0.3)),
                ("update_files", lambda: search.update_files(self.root, [LANE])),
                ("reset", lambda: search.reset(self.root)),
            ):
                with self.subTest(call=name):
                    with self.assertRaises(MemoryForestError) as refused:
                        call()
                    self.assertEqual(refused.exception.code, "index_io_error")

    def test_a_raw_sqlite_error_is_classified_at_the_edge(self) -> None:
        from unittest import mock

        def raise_raw(*_args: Any, **_kwargs: Any) -> Any:
            raise sqlite3.DatabaseError("database disk image is malformed")

        with mock.patch.object(search, "_facts", raise_raw):
            for name, call in self._public_calls():
                with self.subTest(call=name):
                    with self.assertRaises(MemoryForestError) as refused:
                        call()
                    self.assertEqual(refused.exception.code, "index_unreadable")

    def test_every_clock_sequence_of_a_protocol_retry_leaves_as_io(self) -> None:
        """The loop alone reads the clock. Each row is when the deadline passes: before the first retry,
        after one, after two, after three. The review's sequence (inside the read the deadline had not
        passed, in the loop it had) is the first row, because the read no longer looks at the clock."""
        from unittest import mock

        rows = [(0.0,) + (0.0,) * before + (search.READER_TIMEOUT_SECONDS + 1.0,) for before in range(4)]
        for row in rows:
            with self.subTest(clock=row):
                ticks = iter(row + (search.READER_TIMEOUT_SECONDS + 1.0,) * 8)
                attempts = {"n": 0}

                def checked(_root: Any, _fn: Any) -> Any:
                    attempts["n"] += 1
                    raise MemoryForestError("index_protocol", "inside", details={"path": ".forest/index.sqlite"})

                with mock.patch.object(search, "refresh_lanes_and_daily", lambda root: 0), \
                        mock.patch.object(search, "_checked_read", checked), \
                        mock.patch.object(search, "_retry_pause", lambda seconds: None), \
                        mock.patch.object(search.time, "monotonic", lambda: next(ticks)):
                    with self.assertRaises(MemoryForestError) as refused:
                        search.query(self.root, "trays", reader="till-bot")
                self.assertEqual(refused.exception.code, "index_io_error")
                self.assertEqual(attempts["n"], len(row) - 1)


# ─────────────────────────── open first, then look ───────────────────────────


class LookThenOpenTests(unittest.TestCase):
    """What is opened is checked after it is opened. Nothing blocks on a fifo, and nothing that went away
    between two steps leaves as a raw error."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name).resolve() / "forest"
        _write(self.root, FILES)
        search.build(self.root)
        self.folder = self.root / ".forest"

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _within(self, call: Callable[[], Any], seconds: float = 20) -> dict[str, Any]:
        """Run ``call`` in a thread and wait for it on an event with a deadline."""
        import threading

        outcome: dict[str, Any] = {}
        done = threading.Event()

        def run() -> None:
            outcome.update(_api(call))
            done.set()

        threading.Thread(target=run, daemon=True).start()
        self.assertTrue(done.wait(seconds), "the call blocked on what was put in the file's place")
        return outcome

    def test_a_fifo_in_the_place_of_the_pointer_or_the_index_does_not_block(self) -> None:
        pointer = self.folder / "index.current"
        os.mkfifo(pointer)
        try:
            for name, call in (
                ("status", lambda: search.status(self.root)),
                ("query", lambda: search.query(self.root, "trays", reader="till-bot")),
                ("reset", lambda: search.reset(self.root)),
            ):
                with self.subTest(place="pointer", call=name):
                    refused = self._within(call)["error"]
                    self.assertEqual((refused["code"], refused["details"]["state"]), ("index_cannot_open", "pointer_unusable"))
        finally:
            pointer.unlink()
        index = self.folder / "index.sqlite"
        index.unlink()
        os.mkfifo(index)
        for name, call in (
            ("status", lambda: search.status(self.root)),
            ("query", lambda: search.query(self.root, "trays", reader="till-bot")),
            ("build", lambda: search.build(self.root)),
        ):
            with self.subTest(place="index", call=name):
                # A fifo in the index's place, in a folder this account can write: a reset is the cure it can run.
                refused = self._within(call)["error"]
                self.assertEqual((refused["code"], refused["next"]), ("index_unreadable", "run"))
                self.assertIn("not a regular file", refused["message"])

    def test_an_index_file_that_goes_away_before_sqlite_opens_it_is_busy(self) -> None:
        from unittest import mock

        real = sqlite3.connect
        index = self.folder / "index.sqlite"

        def gone_first(*args: Any, **kwargs: Any) -> sqlite3.Connection:
            if index.exists():
                index.unlink()
            return real(*args, **kwargs)

        with mock.patch.object(search.sqlite3, "connect", gone_first):
            refused = _api(lambda: search.status(self.root))
        self.assertEqual(refused["error"]["code"], "index_busy", refused)
        self.assertEqual(refused["error"]["next"], "retry")
        self.assertFalse(index.exists(), "no empty file was made where the index went away")

    def test_a_page_that_goes_away_during_a_build_is_busy(self) -> None:
        from unittest import mock

        real = search._open_page
        page = self.root / PAGE
        (self.root / PAGE).write_text(page.read_text(encoding="utf-8") + "\nA new line.\n", encoding="utf-8")

        def gone(path: Path) -> Any:
            if path.name == page.name:
                raise system.Absent(path)
            return real(path)

        with mock.patch.object(search, "_open_page", gone):
            answer = search.build(self.root)
            code, out, err = _cli(["index", "build", "--root", str(self.root), "--json"])
        self.assertTrue(answer["busy"], answer)
        self.assertIn("A page went away", answer["message"])
        refused = json.loads(out)["error"]
        self.assertEqual((code, refused["code"], refused["next"]), (2, "index_busy", "retry"))
        self.assertIn("A page went away", refused["message"])
        self.assertEqual(search.status(self.root)["state"], "sound")
