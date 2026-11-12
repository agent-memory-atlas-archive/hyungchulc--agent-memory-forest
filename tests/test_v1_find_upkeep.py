"""The index keeps up (masking, appends, one way of writing the file, large queries); protocol details.

The bakery, Ilse, the lockers and the two bots are invented. Nothing here is a
real person, shop, code or transcript.
"""

from __future__ import annotations

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

from memory_forest import credential_ledger, layout, mcp, search
from memory_forest.errors import MemoryForestError
from memory_forest.locking import hold_lock

from test_v1_find import FILES, SETTINGS, _cli, _initialize, _http, _read_frame, _rpc, _stdio
from v1_support import ForestTestCase


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
        with mock.patch.object(search, "MASKING_WAIT_SECONDS", 0.2), mock.patch.object(
            search, "build", return_value={"busy": True, "indexed": False}
        ):
            with self.assertRaises(MemoryForestError) as refused:
                search.query(self.root, "zebra99", reader="till-bot")
        self.assertEqual(refused.exception.code, "index_busy")
        self.assertNotIn("zebra99", refused.exception.message)


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
        builder = subprocess.Popen(
            [sys.executable, "-c",
             "import sys; from memory_forest import search; search.build(sys.argv[1], wait_seconds=30)",
             str(self.root)],
            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and builder.poll() is None:
                try:
                    with hold_lock(self.root, "index", wait_seconds=0, label="test"):
                        pass
                except MemoryForestError as exc:
                    self.assertEqual(exc.code, "lock_busy")
                    break
                time.sleep(0.005)
            failures: list[BaseException] = []
            answered = 0
            while builder.poll() is None:
                try:
                    search.query(self.root, "counter", reader="till-bot", limit=3)
                    answered += 1
                except BaseException as exc:  # the assertion is that nothing is raised
                    failures.append(exc)
            self.assertEqual(builder.wait(timeout=60), 0)
        finally:
            if builder.poll() is None:
                builder.kill()
                builder.wait()
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
        self.assertTrue(self.index.exists())


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
            before = _read_frame(proc.stdout)
            self.assertEqual(before["error"]["code"], -32700)
            self.assertIn("id", before)
            self.assertIsNone(before["id"])
            _rpc(proc, _initialize("2025-11-25"))
            proc.stdin.write(b"{not json\n")
            proc.stdin.flush()
            newest = _read_frame(proc.stdout)
            self.assertEqual(newest["error"]["code"], -32700)
            self.assertNotIn("id", newest)
            null_id = _rpc(proc, {"jsonrpc": "2.0", "id": None, "method": "ping"})
            self.assertEqual(null_id["error"]["code"], -32600)
            self.assertNotIn("id", null_id)
        with _stdio(self.root) as proc:
            _rpc(proc, _initialize("2025-06-18"))
            proc.stdin.write(b"[1, 2\n")
            proc.stdin.flush()
            older = _read_frame(proc.stdout)
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
            refused = _read_frame(proc.stdout)
            self.assertEqual(refused["error"]["code"], -32600)
            following = _read_frame(proc.stdout)
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


class QuerySizeTests(ForestTestCase):
    def test_a_character_in_more_units_than_an_old_variable_limit(self) -> None:
        files = dict(FILES)
        files[TILL] = files[TILL] + "".join(_turn(number, f"빵 {number}번 판이 나갔다") for number in range(1, 1201))
        self.root = self.make_forest(files)
        search.build(self.root)
        opened = search.connect

        def stock(root):
            connection = opened(root)
            if connection is not None:
                connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
            return connection

        with mock.patch.object(search, "connect", stock):
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


if __name__ == "__main__":
    unittest.main()
