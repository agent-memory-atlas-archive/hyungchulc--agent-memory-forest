"""The index keeps up (masking, appends, one way of writing the file, large queries); protocol details.

The bakery, Ilse, the lockers and the two bots are invented. Nothing here is a
real person, shop, code or transcript.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from memory_forest import credential_ledger, credentials, layout, mcp, search
from memory_forest.errors import MemoryForestError
from memory_forest.locking import hold_lock

from test_v1_find import FILES, SETTINGS, _cli, _initialize, _http, _read_frame, _rpc, _stdio, held_in_thread
from v1_support import ForestTestCase
from wait_child import read_fifo, write_fifo

_HOLD_BUILD = Path(__file__).resolve().parent / "launchers" / "hold_index_build.py"


LOCKER = "04 stm/bakery/ovens/floor.md"
TILL = "06 istm/till-bot/2051-03-02.jsonl"


def _turn(number: int, text: str) -> str:
    return json.dumps(
        {"at": "2051-03-02T12:00:00+00:00", "id": f"till-bot:bulk:{number}", "role": "agent", "session": "bulk",
         "text": text},
        sort_keys=True,
    ) + "\n"


def _search_paths(case: ForestTestCase, words: str) -> list[str]:
    code, out, err = _cli(case, ["search", words, "--as", "till-bot", "--json"])
    case.assertEqual(code, 0, err)
    return [page["path"] for page in json.loads(out)["results"]]


def _mcp_search(proc: subprocess.Popen, words: str, msg_id: int) -> list[dict]:
    """The results of ``memory_search`` (the answer also repeats the caller's own question)."""
    reply = _rpc(
        proc,
        {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
         "params": {"name": "memory_search", "arguments": {"words": words}}},
    )
    assert not reply["result"]["isError"], reply
    return json.loads(reply["result"]["content"][0]["text"])["results"]


def _delete_reader(path: Path, schema: str | None = None) -> sqlite3.Connection:
    """A reader that keeps the file out of WAL mode, so a writer cannot set it."""
    connection = sqlite3.connect(path)
    mode = connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
    if mode != "delete":
        connection.close()
        raise AssertionError(mode)
    if schema is not None:
        connection.execute("UPDATE metadata SET value = ? WHERE key = 'schema'", (schema,))
        connection.commit()
    connection.execute("BEGIN")
    connection.execute("SELECT COUNT(*) FROM units").fetchone()
    return connection


def _corrupt_pages(path: Path) -> None:
    """Keep the SQLite header and destroy the pages. Sidecars go, so the bad pages are what is read."""
    data = bytearray(path.read_bytes())
    if not bytes(data).startswith(b"SQLite format 3\x00"):
        raise AssertionError("not a database header")
    for index in range(100, len(data)):
        data[index] = 0xA5
    path.write_bytes(data)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        if sidecar.is_file() and not sidecar.is_symlink():
            sidecar.unlink()


def _holds(result: dict, text: str) -> bool:
    return any(text in section["text"] for page in result["results"] for section in page["sections"])


@contextmanager
def _counting(module: object, name: str):
    """Count the calls of ``module.name`` while the block runs."""
    original = getattr(module, name)
    calls = {"n": 0}

    def counted(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    with mock.patch.object(module, name, counted):
        yield calls


class MaskingFingerprintTests(ForestTestCase):
    """A value a read masks is never confirmed by search, whatever changed the masking."""

    def setUp(self) -> None:
        super().setUp()
        files = dict(FILES)
        files[LOCKER] = files[LOCKER].replace(
            "The oven room was swept today.",
            "The oven room was swept today.\n\nLocker combo: zebra99.\n\n"
            '> "the side gate opens with 84213 tonight"',
        )
        self.root = self.make_forest(files)
        search.build(self.root)

    def _settings(self, extra: str) -> None:
        (self.root / "forest.toml").write_text(SETTINGS + extra, encoding="utf-8")

    def test_a_new_label_word_hides_the_value_from_search_through_the_cli_and_mcp(self) -> None:
        self.assertIn(LOCKER, _search_paths(self, "zebra99"))
        reader = search.connect(self.root)
        assert reader is not None
        self._settings('\n[credentials]\nlabel_words = ["password", "combo"]\n')
        shown = _cli(self, ["read", LOCKER, "--as", "till-bot", "--json"])
        self.assertIn("[REDACTED]", shown[1])
        self.assertNotIn("zebra99", shown[1])
        self.assertEqual(search.status(self.root)["masking"], "changed")
        self.assertEqual(_search_paths(self, "zebra99"), [])
        self.assertEqual(search.status(self.root)["masking"], "current")
        self.assertEqual(_cli(self, ["index", "build", "--verify"])[0], 0)
        self.assertEqual(_search_paths(self, "zebra99"), [])
        with _stdio(self.root) as proc:
            _rpc(proc, _initialize("2025-11-25"))
            self.assertEqual(_mcp_search(proc, "zebra99", 2), [])
        # A connection opened before the index was emptied and filled again still answers.
        self.assertGreater(reader.execute("SELECT COUNT(*) FROM units").fetchone()[0], 0)
        reader.close()

    def test_a_value_added_to_the_ledger_is_hidden_from_search_through_the_cli_and_mcp(self) -> None:
        self.assertIn(LOCKER, _search_paths(self, "84213"))
        candidates = _cli(self, ["credentials", "candidates", "--json"])
        names = [f"{item['path']}:{item['line']}:{item['place']}" for item in json.loads(candidates[1])["candidates"]]
        self.assertTrue(any(name.startswith(LOCKER + ":") for name in names), candidates)
        judged = _cli(self, ["credentials", "judge", *[name for name in names if name.startswith(LOCKER)], "--sign-in"])
        self.assertEqual(judged[0], 0, judged[2])
        self.assertNotIn("84213", _cli(self, ["read", LOCKER, "--as", "till-bot", "--json"])[1])
        with _stdio(self.root) as proc:
            _rpc(proc, _initialize("2025-06-18"))
            self.assertEqual(_mcp_search(proc, "84213", 2), [])
        self.assertEqual(_search_paths(self, "84213"), [])

    def test_a_masking_change_is_a_rebuild_at_update_and_at_build(self) -> None:
        lane = self.root / TILL
        self._settings('\n[credentials]\nlabel_words = ["password", "combo"]\n')
        with lane.open("a", encoding="utf-8") as handle:
            handle.write(_turn(1, "the till noted the rye tally"))
        updated = search.update_files(self.root, [TILL])
        self.assertTrue(updated["indexed"])
        self.assertGreater(updated["updated"], 1)  # every file, not the one named
        self.assertEqual(search.status(self.root)["masking"], "current")
        self.assertEqual(_search_paths(self, "zebra99"), [])
        self._settings("")
        rebuilt = search.build(self.root)
        self.assertEqual(rebuilt["updated"], rebuilt["files"])
        self.assertIn(LOCKER, _search_paths(self, "zebra99"))

    def test_threads_of_one_server_wait_for_each_other_instead_of_failing(self) -> None:
        self._settings('\n[credentials]\nlabel_words = ["password", "combo"]\n')
        lane = self.root / TILL
        with lane.open("a", encoding="utf-8") as handle:
            handle.write(_turn(1, "the till noted the oat tally"))
        failures: list[BaseException] = []
        answers: list[dict] = []

        def ask() -> None:
            try:
                answers.append(search.query(self.root, "zebra99 oat", reader="till-bot"))
            except BaseException as exc:  # the assertion is that no thread fails
                failures.append(exc)

        threads = [threading.Thread(target=ask) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(failures, [])
        self.assertEqual(len(answers), 6)
        for answer in answers:
            self.assertNotIn(LOCKER, [page["path"] for page in answer["results"]])

    def test_a_query_refuses_rather_than_answer_under_old_masking_when_it_cannot_rebuild(self) -> None:
        self._settings('\n[credentials]\nlabel_words = ["password", "combo"]\n')
        with held_in_thread(lambda: search._index_writer(self.root, 60)):
            with mock.patch.object(search, "MASKING_WAIT_SECONDS", 0.2):
                began = time.monotonic()
                with self.assertRaises(MemoryForestError) as refused:
                    search.query(self.root, "zebra99", reader="till-bot")
                waited = time.monotonic() - began
            self.assertFalse(search._WRITERS.acquire(blocking=False))
        self.assertGreaterEqual(waited, 0.15)
        self.assertEqual(refused.exception.code, "index_busy")
        self.assertNotIn("zebra99", refused.exception.message)

    def test_a_busy_open_answers_stale_when_the_fingerprint_matches(self) -> None:
        held = _delete_reader(self.root / layout.STATE_DIR / "index.sqlite")
        try:
            lane = self.root / TILL
            with lane.open("a", encoding="utf-8") as handle:
                handle.write(_turn(9, "the till noted a fresh tray"))
            found = search.query(self.root, "zebra99", reader="till-bot")
        finally:
            held.close()
        self.assertGreater(found.get("stale", 0), 0)
        self.assertIn(LOCKER, [page["path"] for page in found["results"]])
        self.assertIn("zebra99", json.dumps(found))

    def test_a_busy_open_refuses_when_the_fingerprint_differs(self) -> None:
        self._settings('\n[credentials]\nlabel_words = ["password", "combo"]\n')
        held = _delete_reader(self.root / layout.STATE_DIR / "index.sqlite")
        try:
            with mock.patch.object(search, "MASKING_WAIT_SECONDS", 0.2):
                began = time.monotonic()
                with self.assertRaises(MemoryForestError) as refused:
                    search.query(self.root, "zebra99", reader="till-bot")
                waited = time.monotonic() - began
        finally:
            held.close()
        self.assertGreaterEqual(waited, 0.15)
        self.assertEqual(refused.exception.code, "index_busy")
        self.assertNotIn("zebra99", refused.exception.message)

    def test_a_busy_open_refuses_when_the_fingerprint_cannot_be_read(self) -> None:
        path = self.root / layout.STATE_DIR / "index.sqlite"
        held = _delete_reader(path, schema="9")
        try:
            with mock.patch.object(search, "MASKING_WAIT_SECONDS", 0.2):
                began = time.monotonic()
                with self.assertRaises(MemoryForestError) as refused:
                    search.query(self.root, "zebra99", reader="till-bot")
                waited = time.monotonic() - began
        finally:
            held.close()
        self.assertGreaterEqual(waited, 0.15)
        self.assertEqual(refused.exception.code, "index_busy")
        self.assertNotIn("zebra99", str(refused.exception))


class LaneAppendTests(ForestTestCase):
    """An appended lane line costs one line, in the published file."""

    def setUp(self) -> None:
        super().setUp()
        files = dict(FILES)
        files[TILL] = files[TILL] + "".join(_turn(number, f"tray {number} went out") for number in range(1, 200))
        self.root = self.make_forest(files)
        search.build(self.root)
        self.index = self.root / layout.STATE_DIR / "index.sqlite"
        self.lane = self.root / TILL

    def _row(self) -> tuple[int, int, int]:
        database = sqlite3.connect(self.index)
        try:
            return database.execute(
                "SELECT size, indexed_bytes, indexed_lines FROM files WHERE path = ?", (TILL,)
            ).fetchone()
        finally:
            database.close()

    def test_an_append_parses_only_the_new_lines_in_the_same_file(self) -> None:
        inode = self.index.stat().st_ino
        with self.lane.open("a", encoding="utf-8") as handle:
            handle.write(_turn(500, "the zanthorpe crate came back"))
            handle.write(_turn(501, "the quillet crate came back"))
        with _counting(search, "_turn_unit") as parsed:
            found = search.query(self.root, "zanthorpe", reader="till-bot")
        self.assertEqual(parsed["n"], 2)
        self.assertIn(TILL, [page["path"] for page in found["results"]])
        self.assertEqual(self.index.stat().st_ino, inode)
        size, indexed, lines = self._row()
        self.assertEqual((size, indexed), (self.lane.stat().st_size, self.lane.stat().st_size))
        self.assertEqual(lines, 202)
        read = search.read(self.root, f"{TILL}#till-bot:bulk:501", reader="till-bot")
        self.assertEqual(read["results"][0]["sections"][0]["lines"], [202, 202])

    def test_the_ledger_is_loaded_once_per_update_not_once_per_unit(self) -> None:
        with self.lane.open("a", encoding="utf-8") as handle:
            for number in range(700, 760):
                handle.write(_turn(number, f"crate {number} came back"))
        with _counting(credential_ledger, "for_config") as ledgers, _counting(search, "_turn_unit") as parsed:
            self.assertTrue(search.update_files(self.root, [TILL])["indexed"])
        self.assertEqual(parsed["n"], 60)
        self.assertEqual(ledgers["n"], 1)

    def test_a_half_written_line_waits_for_its_newline(self) -> None:
        before = self._row()
        line = _turn(600, "the marrowby crate is half written")
        with self.lane.open("a", encoding="utf-8") as handle:
            handle.write(line[:-1])
        with _counting(search, "_turn_unit") as parsed:
            self.assertEqual(search.query(self.root, "marrowby", reader="till-bot")["results"], [])
        self.assertEqual(parsed["n"], 0)
        self.assertEqual(self._row()[1:], before[1:])
        with self.lane.open("a", encoding="utf-8") as handle:
            handle.write("\n")
        with _counting(search, "_turn_unit") as parsed:
            found = search.query(self.root, "marrowby", reader="till-bot")
        self.assertEqual(parsed["n"], 1)
        self.assertIn(TILL, [page["path"] for page in found["results"]])

    def test_a_changed_prefix_or_a_shorter_file_is_indexed_again_whole(self) -> None:
        text = self.lane.read_text(encoding="utf-8")
        self.lane.write_text(text.replace("tray 1 went out", "tray 1 came in"), encoding="utf-8")
        with _counting(search, "_turn_unit") as parsed:
            found = search.query(self.root, "came", reader="till-bot")
        self.assertEqual(parsed["n"], 200)
        self.assertIn(TILL, [page["path"] for page in found["results"]])
        self.assertFalse(_holds(search.query(self.root, "tray", reader="till-bot", limit=5), "tray 1 went out"))
        self.lane.write_text(_turn(1, "only the plimsoll tray"), encoding="utf-8")
        with _counting(search, "_turn_unit") as parsed:
            search.query(self.root, "plimsoll", reader="till-bot")
        self.assertEqual(parsed["n"], 1)
        self.assertEqual(self._row()[2], 1)


class PublishingTests(ForestTestCase):
    """The published file is only written in place, so no build pulls a journal from under a reader."""

    def setUp(self) -> None:
        super().setUp()
        files = dict(FILES)
        files[TILL] = files[TILL] + "".join(
            _turn(number, f"tray {number} went out past the counter and the rye shelf") for number in range(1, 6000)
        )
        self.root = self.make_forest(files)
        search.build(self.root)
        self.index = self.root / layout.STATE_DIR / "index.sqlite"

    def test_a_reader_opened_before_a_build_answers_after_it_and_a_query_during_it_never_raises(self) -> None:
        inode = self.index.stat().st_ino
        reader = search.connect(self.root)
        assert reader is not None
        cursor = reader.execute("SELECT text FROM units ORDER BY id")
        self.assertIsNotNone(cursor.fetchone())  # a read transaction is open across the build
        lane = self.root / TILL
        lane.write_text(lane.read_text(encoding="utf-8").replace("went out", "came back"), encoding="utf-8")
        held = self.base / "build-held"
        release = self.base / "build-release"
        os.mkfifo(held)
        os.mkfifo(release)
        builder = subprocess.Popen(
            [sys.executable, str(_HOLD_BUILD), str(self.root), str(held), str(release)],
            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            read_fifo(builder, str(held), what="the build to reach its transaction")
            failures: list[BaseException] = []
            answered = 0
            try:
                search.query(self.root, "counter", reader="till-bot", limit=3)
                answered += 1
            except BaseException as exc:  # the assertion is that nothing is raised
                failures.append(exc)
            write_fifo(builder, str(release), b"go\n", what="the build to take the release")
            code = builder.wait(timeout=60)
            _out, err = builder.communicate()
            self.assertEqual(code, 0, err)
        finally:
            if builder.poll() is None:
                builder.kill()
                builder.wait(timeout=30)
        self.assertEqual(failures, [])
        self.assertGreater(answered, 0)
        self.assertTrue(cursor.fetchall())
        fresh = reader.execute("SELECT COUNT(*) FROM units WHERE text LIKE '%came back%'").fetchone()[0]
        self.assertGreater(fresh, 0)
        reader.close()
        self.assertEqual(self.index.stat().st_ino, inode)
        self.assertFalse(_holds(search.query(self.root, "went", reader="till-bot"), "went out past"))

    def test_a_build_refuses_a_file_it_cannot_read_and_does_not_unlink_it(self) -> None:
        self.index.write_bytes(b"not a database, and longer than one header of a page " * 4)
        with self.assertRaises(MemoryForestError) as refused:
            search.build(self.root)
        self.assertEqual(refused.exception.code, "index_unreadable")
        self.assertEqual(refused.exception.next_step, "run")
        self.assertEqual(refused.exception.command, "forest index reset")
        self.assertTrue(self.index.exists())

    def test_malformed_pages_are_the_same_refusal_and_reset_removes_only_the_index(self) -> None:
        page = self.root / LOCKER
        page_text = page.read_text(encoding="utf-8")
        decoy = self.root / layout.STATE_DIR / "kept.txt"
        decoy.write_bytes(b"leave this")
        note = self.root / layout.STATE_DIR / "notes.txt"
        note.write_text("leave this too\n", encoding="utf-8")
        wal = Path(str(self.index) + "-wal")
        shm = Path(str(self.index) + "-shm")
        wal.write_bytes(b"wal")
        shm.write_bytes(b"shm")
        preview = _cli(self, ["index", "reset", "--dry-run"])
        self.assertEqual(preview[0], 0, preview[2])
        self.assertIn("would remove: .forest/index.sqlite", preview[1])
        self.assertIn("would remove: .forest/index.sqlite-wal", preview[1])
        self.assertIn("would remove: .forest/index.sqlite-shm", preview[1])
        self.assertIn("would point at: .forest/index-", preview[1])
        self.assertFalse((self.root / ".forest" / "index.current").exists())
        self.assertTrue(self.index.is_file())
        self.assertEqual(decoy.read_bytes(), b"leave this")
        _corrupt_pages(self.index)
        with self.assertRaises(MemoryForestError) as refused:
            search.query(self.root, "counter", reader="till-bot")
        self.assertEqual(refused.exception.code, "index_unreadable")
        self.assertEqual(refused.exception.as_dict()["next"], "run")
        self.assertEqual(refused.exception.as_dict()["command"], "forest index reset")
        self.assertTrue(self.index.is_file())
        cli = _cli(self, ["search", "counter", "--as", "till-bot", "--json"])
        self.assertEqual(cli[0], 2, cli[1] + cli[2])
        payload = json.loads(cli[1])
        self.assertEqual(payload["error"]["code"], "index_unreadable")
        self.assertEqual(payload["error"]["next"], "run")
        self.assertEqual(payload["error"]["command"], "forest index reset")
        with _stdio(self.root) as proc:
            _rpc(proc, _initialize("2025-11-25"))
            reply = _rpc(
                proc,
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                 "params": {"name": "memory_search", "arguments": {"words": "counter"}}},
            )
        self.assertTrue(reply["result"]["isError"])
        body = json.loads(reply["result"]["content"][0]["text"])
        self.assertEqual(body["next"], "run")
        self.assertEqual(body["command"], "forest index reset")
        token = mcp.ensure_token(self.root).decode("ascii")
        server = mcp.serve_http(self.root, "till-bot", "127.0.0.1", 0, token=token.encode("ascii"))
        try:
            response = _http(
                server.server_address[1],
                {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                 "params": {"name": "memory_search", "arguments": {"words": "counter"}}},
                token=token,
            )
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(response.status, 200)
        http_body = json.loads(response.body)
        self.assertTrue(http_body["result"]["isError"])
        self.assertEqual(json.loads(http_body["result"]["content"][0]["text"])["command"], "forest index reset")
        wal.write_bytes(b"wal")
        shm.write_bytes(b"shm")
        removed = _cli(self, ["index", "reset"])
        self.assertEqual(removed[0], 0, removed[2])
        self.assertIn("removed: .forest/index.sqlite", removed[1])
        self.assertFalse(self.index.exists())
        self.assertFalse(wal.exists())
        self.assertFalse(shm.exists())
        self.assertEqual(decoy.read_bytes(), b"leave this")
        self.assertEqual(note.read_text(encoding="utf-8"), "leave this too\n")
        self.assertEqual(page.read_text(encoding="utf-8"), page_text)
        again = search.query(self.root, "counter", reader="till-bot")
        self.assertIn(TILL, [item["path"] for item in again["results"]])


class SqliteErrorClassTests(ForestTestCase):
    """Only a corrupt file is index_unreadable. Busy waits or answers stale; read-only and cannot-open say what to do."""

    MASKED = '\n[credentials]\nlabel_words = ["password", "combo"]\n'

    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_forest(dict(FILES))
        self.index = self.root / layout.STATE_DIR / "index.sqlite"

    def _append(self, number: int, text: str) -> None:
        with (self.root / TILL).open("a", encoding="utf-8") as handle:
            handle.write(_turn(number, text))

    def _settings(self, extra: str) -> None:
        (self.root / "forest.toml").write_text(SETTINGS + extra, encoding="utf-8")

    def test_a_build_paused_in_its_transaction_is_no_index_yet_for_a_second_reader(self) -> None:
        self.assertFalse(self.index.exists())
        held = self.base / "index-held"
        release = self.base / "index-release"
        os.mkfifo(held)
        os.mkfifo(release)
        builder = subprocess.Popen(
            [sys.executable, str(_HOLD_BUILD), str(self.root), str(held), str(release)],
            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            read_fifo(builder, str(held), what="the build to reach its transaction")
            self.assertTrue(self.index.is_file())
            self.assertIsNone(search.connect(self.root))
            state = search.status(self.root)
            self.assertEqual((state["available"], state["exists"]), (False, True))
            code, out, err = _cli(self, ["index", "status", "--json"])
            self.assertEqual(code, 0, out + err)
            with mock.patch.object(search, "MASKING_WAIT_SECONDS", 0.2):
                with self.assertRaises(MemoryForestError) as held:
                    search.query(self.root, "trays", reader="till-bot")
            self.assertEqual(held.exception.code, "index_busy")
            self.assertEqual(held.exception.next_step, "retry")
            self.assertIsNone(held.exception.command)
            self.assertNotIn("fingerprint", held.exception.message.casefold())
            self.assertIn("not committed", held.exception.message.casefold())
            write_fifo(builder, str(release), b"go\n", what="the build to take the release")
            code = builder.wait(timeout=120)
            _out, err = builder.communicate()
            self.assertEqual(code, 0, err)
        finally:
            if builder.poll() is None:
                builder.kill()
                builder.wait(timeout=30)
        found = search.query(self.root, "trays", reader="till-bot")
        self.assertIn(TILL, [page["path"] for page in found["results"]])

    def test_an_outside_writer_is_busy_for_a_build_and_stale_or_retry_for_a_query(self) -> None:
        search.build(self.root)
        holder = sqlite3.connect(self.index, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        try:
            self._append(41, "the till noted the quorn tally")
            built = search.build(self.root)
            self.assertTrue(built["busy"])
            self.assertFalse(built["indexed"])
            found = search.query(self.root, "trays", reader="till-bot")
            self.assertGreater(found.get("stale", 0), 0)
            self.assertIn(TILL, [page["path"] for page in found["results"]])
            self._settings(self.MASKED)
            with mock.patch.object(search, "MASKING_WAIT_SECONDS", 0.2):
                with self.assertRaises(MemoryForestError) as refused:
                    search.query(self.root, "trays", reader="till-bot")
        finally:
            holder.rollback()
            holder.close()
        body = refused.exception.as_dict()
        self.assertEqual(body["code"], "index_busy")
        self.assertEqual(body["next"], "retry")
        self.assertGreater(body["after_seconds"], 0)
        self.assertNotIn("command", body)
        self.assertIn("fingerprint", body["message"].casefold())
        self.assertIn("in use", body["message"].casefold())
        self.assertIn("stop_after_seconds", body)
        self.assertIn("waited_seconds", body)
        self.assertTrue(self.index.is_file())
        self.assertIn(TILL, [page["path"] for page in search.query(self.root, "quorn", reader="till-bot")["results"]])

    @unittest.skipIf(os.geteuid() == 0, "the superuser writes a read-only file")
    def test_a_read_only_index_is_a_refusal_of_its_own_and_not_a_reset(self) -> None:
        search.build(self.root)
        self._append(42, "the till noted the brambling tally")
        os.chmod(self.index, 0o444)
        self.addCleanup(os.chmod, self.index, 0o644)
        for call in (lambda: search.build(self.root), lambda: search.query(self.root, "brambling", reader="till-bot")):
            with self.assertRaises(MemoryForestError) as refused:
                call()
            body = refused.exception.as_dict()
            self.assertEqual(body["code"], "index_read_only")
            self.assertEqual(body["next"], "report")
            self.assertNotIn("command", body)
            self.assertNotIn("reset", body["message"].replace("A reset would not help", ""))
        self.assertTrue(self.index.is_file())
        code, out, _err = _cli(self, ["index", "build", "--json"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["code"], "index_read_only")

    def test_an_index_path_that_cannot_be_opened_is_a_refusal_of_its_own(self) -> None:
        self.index.parent.mkdir(mode=0o700, exist_ok=True)
        self.index.mkdir()
        with self.assertRaises(MemoryForestError) as refused:
            search.build(self.root)
        self.assertEqual(refused.exception.code, "index_cannot_open")
        self.assertEqual(refused.exception.next_step, "report")
        self.assertIsNone(refused.exception.command)
        reset = search.reset(self.root)
        self.assertTrue(reset["ok"])
        self.assertIn(".forest/index.sqlite", reset["orphans"])
        self.assertTrue(self.index.is_dir())
        self.assertNotEqual(search.index_relative(self.root), ".forest/index.sqlite")
        built = search.build(self.root)
        self.assertFalse(built["busy"])
        self.assertTrue(self.index.is_dir())
        self.assertTrue((self.root / search.index_relative(self.root)).is_file())

    def test_reset_checks_every_path_before_it_removes_one(self) -> None:
        search.build(self.root)
        wal = Path(str(self.index) + "-wal")
        shm = Path(str(self.index) + "-shm")
        wal.write_bytes(b"wal")
        elsewhere = self.base / "elsewhere"
        elsewhere.write_bytes(b"keep")
        shm.symlink_to(elsewhere)
        code, out, err = _cli(self, ["index", "reset"])
        self.assertEqual(code, 0, err)
        self.assertFalse(self.index.exists())
        self.assertFalse(wal.exists())
        self.assertTrue(shm.is_symlink())
        self.assertEqual(elsewhere.read_bytes(), b"keep")
        self.assertIn("orphan: .forest/index.sqlite-shm", out)
        self.assertNotIn("symlink_forbidden", err)

    def test_reset_with_the_lock_held_exits_non_zero_and_says_retry(self) -> None:
        search.build(self.root)
        with held_in_thread(lambda: hold_lock(self.root, "index", wait_seconds=60, label="test")):
            code, out, err = _cli(self, ["index", "reset", "--json"])
            text = _cli(self, ["index", "reset"])
        self.assertEqual(code, 2, out + err)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "index_busy")
        self.assertEqual(payload["error"]["next"], "retry")
        self.assertGreater(payload["error"]["after_seconds"], 0)
        self.assertEqual(text[0], 2)
        self.assertIn("next: retry", text[2])
        self.assertTrue(self.index.is_file())
        self.assertEqual(_cli(self, ["index", "reset"])[0], 0)
        self.assertFalse(self.index.exists())

    def test_a_busy_open_is_tried_again_at_a_short_interval_until_it_goes_through(self) -> None:
        search.build(self.root)
        self._settings(self.MASKED)
        held = _delete_reader(self.index)
        pauses: list[float] = []

        def pause(seconds: float) -> None:
            pauses.append(seconds)
            held.close()  # the old reader lets go during the first pause

        try:
            with mock.patch.object(search, "MASKING_WAIT_SECONDS", 600.0), \
                    mock.patch.object(search, "WRITER_TIMEOUT_SECONDS", search.RETRY_INTERVAL_SECONDS), \
                    mock.patch.object(search, "_retry_pause", pause):
                found = search.query(self.root, "trays", reader="till-bot")
        finally:
            held.close()
        self.assertGreaterEqual(len(pauses), 1)
        self.assertTrue(all(seconds <= search.RETRY_INTERVAL_SECONDS for seconds in pauses), pauses)
        self.assertNotIn("stale", found)
        self.assertIn(TILL, [page["path"] for page in found["results"]])
        self.assertEqual(search.status(self.root)["masking"], "current")

    def test_a_schema_3_file_with_no_masking_key_waits_and_refuses(self) -> None:
        search.build(self.root)
        database = sqlite3.connect(self.index)
        database.execute("DELETE FROM metadata WHERE key = 'masking'")
        database.commit()
        database.close()
        self.assertEqual(search.status(self.root)["schema"], search.SCHEMA)
        budgets: list[float] = []
        original = search._index_writer

        def recording(root, wait_seconds):
            budgets.append(wait_seconds)
            return original(root, wait_seconds)

        with held_in_thread(lambda: hold_lock(self.root, "index", wait_seconds=60, label="test")):
            with mock.patch.object(search, "MASKING_WAIT_SECONDS", 0.2), \
                    mock.patch.object(search, "_index_writer", recording):
                with self.assertRaises(MemoryForestError) as refused:
                    search.query(self.root, "trays", reader="till-bot")
        self.assertTrue(budgets and budgets[0] > 0, budgets)
        self.assertEqual(refused.exception.code, "index_busy")
        self.assertEqual(refused.exception.next_step, "retry")

    def test_every_tool_refusal_is_one_json_shape_with_or_without_a_next_move(self) -> None:
        with self.assertRaises(MemoryForestError) as missing:
            search.require_index(self.root)
        self.assertEqual(missing.exception.as_dict()["next"], "run")
        self.assertEqual(missing.exception.as_dict()["command"], "forest index build")
        search.build(self.root)
        calls = [
            ("2025-11-25", {"name": "memory_search", "arguments": {"words": "  "}}, "search_empty"),
            ("2025-11-25", {"name": "memory_search", "arguments": {"words": "trays", "limit": 0}},
             "arguments_invalid"),
            ("2025-11-25", {"name": "memory_read", "arguments": {"path": "04 stm/bakery/none.md"}}, None),
        ]
        for version, params, expected in calls:
            with self.subTest(params=params), _stdio(self.root) as proc:
                _rpc(proc, _initialize(version))
                reply = _rpc(proc, {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": params})
                self.assertTrue(reply["result"]["isError"], reply)
                body = json.loads(reply["result"]["content"][0]["text"])
                self.assertEqual(set(body) - {"next", "command", "after_seconds"}, {"code", "details", "message"})
                self.assertNotIn("next", body)
                if expected:
                    self.assertEqual(body["code"], expected)
        self._settings(self.MASKED)
        with held_in_thread(lambda: hold_lock(self.root, "index", wait_seconds=60, label="test")):
            with mock.patch.object(search, "MASKING_WAIT_SECONDS", 0.2):
                reply, _version = mcp.handle_message(
                    self.root, "till-bot",
                    {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                     "params": {"name": "memory_search", "arguments": {"words": "trays"}}},
                    "stdio", "2025-11-25",
                )
        body = json.loads(reply["result"]["content"][0]["text"])
        self.assertEqual((body["code"], body["next"]), ("index_busy", "retry"))
        self.assertGreater(body["after_seconds"], 0)


class ProtocolTests(ForestTestCase):
    """Each detail as the negotiated revision's own text states it."""

    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_forest(FILES)
        search.build(self.root)

    @staticmethod
    def _call(msg_id: int, name: str, arguments: dict | None) -> dict:
        params: dict = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        return {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call", "params": params}

    def test_input_validation_is_a_tool_result_from_2025_11_25_and_a_protocol_error_before(self) -> None:
        bad = [("memory_search", {"words": "rye", "limit": 0}), ("memory_read", {"path": LOCKER, "lines": "x"})]
        for version in mcp.PROTOCOL_VERSIONS:
            with self.subTest(version=version), _stdio(self.root) as proc:
                _rpc(proc, _initialize(version))
                for number, (name, arguments) in enumerate(bad, start=2):
                    reply = _rpc(proc, self._call(number, name, arguments))
                    if version == "2025-11-25":
                        self.assertTrue(reply["result"]["isError"], reply)
                    else:
                        self.assertEqual(reply["error"]["code"], -32602, reply)
                unknown = _rpc(proc, self._call(9, "memory_missing", {}))
                self.assertEqual(unknown["error"]["code"], -32602)
                listed = _rpc(proc, self._call(10, "memory_navigate", None))
                self.assertFalse(listed["result"]["isError"])

    def test_an_unreadable_id_is_omitted_where_allowed_and_null_elsewhere(self) -> None:
        with _stdio(self.root) as proc:
            proc.stdin.write(b"{not json\n")
            proc.stdin.flush()
            before = _read_frame(proc.stdout, proc)
            self.assertEqual(before["error"]["code"], -32700)
            self.assertIn("id", before)
            self.assertIsNone(before["id"])
            _rpc(proc, _initialize("2025-11-25"))
            proc.stdin.write(b"{not json\n")
            proc.stdin.flush()
            newest = _read_frame(proc.stdout, proc)
            self.assertEqual(newest["error"]["code"], -32700)
            self.assertNotIn("id", newest)
            null_id = _rpc(proc, {"jsonrpc": "2.0", "id": None, "method": "ping"})
            self.assertEqual(null_id["error"]["code"], -32600)
            self.assertNotIn("id", null_id)
        with _stdio(self.root) as proc:
            _rpc(proc, _initialize("2025-06-18"))
            proc.stdin.write(b"[1, 2\n")
            proc.stdin.flush()
            older = _read_frame(proc.stdout, proc)
            self.assertIsNone(older["id"])
        token = mcp.ensure_token(self.root).decode("ascii")
        server = mcp.serve_http(self.root, "till-bot", "127.0.0.1", 0, token=token.encode("ascii"), read_timeout=0.4)
        try:
            broken = _http(server.server_address[1], None, token=token, raw=b"{not json")
            self.assertEqual(broken.status, 400)
            self.assertNotIn("id", json.loads(broken.body))
        finally:
            server.shutdown()
            server.server_close()

    def test_an_oversize_line_is_one_error_and_the_next_line_is_read(self) -> None:
        with _stdio(self.root) as proc:
            _rpc(proc, _initialize("2025-11-25"))
            proc.stdin.write(b'{"jsonrpc":"2.0","id":5,"method":"ping","params":{"pad":"' + b"a" * mcp.MESSAGE_LIMIT
                             + b'"}}\n')
            proc.stdin.write(b'{"jsonrpc":"2.0","id":6,"method":"ping"}\n')
            proc.stdin.flush()
            refused = _read_frame(proc.stdout, proc)
            self.assertEqual(refused["error"]["code"], -32600)
            following = _read_frame(proc.stdout, proc)
            self.assertEqual(following["id"], 6)
            self.assertEqual(following["result"], {})

    def test_read_message_holds_no_more_than_the_limit(self) -> None:
        class Stream:
            def __init__(self, data: bytes) -> None:
                self.data, self.position, self.largest = data, 0, 0

            def readline(self, size: int = -1) -> bytes:
                self.largest = max(self.largest, size)
                end = self.data.find(b"\n", self.position)
                end = len(self.data) if end < 0 else end + 1
                if 0 <= size < end - self.position:
                    end = self.position + size
                piece = self.data[self.position : end]
                self.position = end
                return piece

        stream = Stream(b"x" * (mcp.MESSAGE_LIMIT * 3) + b"\n" + b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
        self.assertIs(mcp.read_message(stream), mcp._OVERSIZE)
        self.assertEqual(mcp.read_message(stream)["id"], 1)
        self.assertLessEqual(stream.largest, mcp.MESSAGE_LIMIT + 1)
        self.assertIsNone(mcp.read_message(stream))

    def test_a_batch_before_initialize_or_holding_initialize_is_refused(self) -> None:
        with _stdio(self.root) as proc:
            early = _rpc(proc, [{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
            self.assertEqual(early["error"]["code"], -32600)
            folded = _rpc(proc, [_initialize("2025-03-26")])
            self.assertEqual(folded["error"]["code"], -32600)
            _rpc(proc, _initialize("2025-03-26"))
            again = _rpc(proc, [{"jsonrpc": "2.0", "id": 2, "method": "ping"}, _initialize("2025-03-26")])
            self.assertEqual(again["error"]["code"], -32600)
            accepted = _rpc(proc, [{"jsonrpc": "2.0", "id": 3, "method": "ping"}])
            self.assertEqual([item["id"] for item in accepted], [3])
            fraction = _rpc(proc, {"jsonrpc": "2.0", "id": 1.5, "method": "ping"})
            self.assertEqual(fraction["error"]["code"], -32600)
            self.assertIsNone(fraction["id"])
        token = mcp.ensure_token(self.root).decode("ascii")
        server = mcp.serve_http(self.root, "till-bot", "127.0.0.1", 0, token=token.encode("ascii"))
        try:
            early = _http(
                server.server_address[1],
                [{"jsonrpc": "2.0", "id": 7, "method": "ping"}],
                token=token,
                protocol="2025-03-26",
            )
            self.assertEqual(early.status, 200)
            self.assertEqual(json.loads(early.body)[0]["id"], 7)
            folded = _http(
                server.server_address[1],
                [{"jsonrpc": "2.0", "id": 8, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}],
                token=token,
                protocol="2025-03-26",
            )
            self.assertEqual(folded.status, 200)
            folded_body = json.loads(folded.body)
            self.assertEqual(folded_body["error"]["code"], -32600)
            self.assertIn("id", folded_body)
            self.assertIsNone(folded_body["id"])
            mixed = _http(
                server.server_address[1],
                [{"jsonrpc": "2.0", "id": 9, "method": "ping"}, "not-an-object"],
                token=token,
                protocol="2025-03-26",
            )
            self.assertEqual(mixed.status, 200)
            items = json.loads(mixed.body)
            self.assertEqual(items[0]["id"], 9)
            self.assertIsNone(items[1]["id"])
            self.assertEqual(items[1]["error"]["code"], -32600)
        finally:
            server.shutdown()
            server.server_close()


class QuerySizeTests(ForestTestCase):
    def test_a_character_in_more_units_than_an_old_variable_limit(self) -> None:
        files = dict(FILES)
        files[TILL] = files[TILL] + "".join(_turn(number, f"빵 {number}번 판이 나갔다") for number in range(1, 1201))
        self.root = self.make_forest(files)
        search.build(self.root)
        opened = search._connect_reader

        def stock(path, relative, *, immutable):
            connection = opened(path, relative, immutable=immutable)
            connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
            return connection

        with mock.patch.object(search, "_connect_reader", stock):
            found = search.query(self.root, "빵", reader="till-bot")
        sections = [section for page in found["results"] for section in page["sections"]]
        self.assertGreaterEqual(len(sections), 1200)

    def test_the_freshness_check_does_not_walk_each_path(self) -> None:
        files = dict(FILES)
        for day in range(60):
            stamp = (date(2051, 1, 1) + timedelta(days=day)).isoformat()
            files[f"05 daily/{stamp}.md"] = f"# {stamp}\n\nThe counter was quiet.\n"
        self.root = self.make_forest(files)
        search.build(self.root)
        with _counting(layout, "resolve") as walked:
            self.assertEqual(search.refresh_lanes_and_daily(self.root), 0)
        self.assertLessEqual(walked["n"], 4)


# One digest of MASKING_VERSION and the sources a read runs. Raise the version when those sources change,
# then replace this pin with the digest the failure prints.
_MASKING_PIN = "d374ac38ba3c71ba9e92939e138270f25cf9bb3579243e2ca4c97c6bf6699641"
_MASKING_SOURCES = (
    ("numbers.py", "star_numbers: the code a read runs on a number"),
    ("credentials.py", "the sign-in rules, shown, and MASKING_VERSION"),
    ("credential_ledger.py", "mask_known: the judged digests a read applies"),
)


def _masking_digest() -> str:
    root = Path(__file__).resolve().parents[1] / "src" / "memory_forest"
    digest = hashlib.sha256()
    digest.update(credentials.MASKING_VERSION.encode())
    digest.update(b"\0")
    for name, _why in _MASKING_SOURCES:
        data = (root / name).read_bytes()
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


class MaskingSourcePinTests(unittest.TestCase):
    """MASKING_VERSION moves when the code a read runs on changes."""

    def test_the_pin_covers_the_masking_sources_and_the_version(self) -> None:
        digest = _masking_digest()
        covered = "\n".join(f"- {name}: {why}" for name, why in _MASKING_SOURCES)
        self.assertEqual(
            digest,
            _MASKING_PIN,
            "MASKING_VERSION is "
            f"{credentials.MASKING_VERSION!r} and the pinned digest does not match the sources.\n"
            "If the masking behavior changed, raise MASKING_VERSION in credentials.py.\n"
            "Then set _MASKING_PIN in this test to the new digest.\n"
            f"Covered files:\n{covered}\n"
            f"digest: {digest}",
        )


if __name__ == "__main__":
    unittest.main()
