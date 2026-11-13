"""A turn said twice is two turns; one turn through two sources is one line; a write that happened says so.

The bakery and its bots are invented. Every clock here is handed in to the call; none is frozen for the
process.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any
from unittest import mock

from memory_forest import intake, levels, mcp, sources
from memory_forest.cli import forest_main
from memory_forest import cli_find
from memory_forest.errors import MemoryForestError

from test_v1_find import FILES
from test_v1_groups import BAKERY_GROUPS, SRC
from v1_support import ForestTestCase

NOON = datetime(2051, 3, 2, 12, 0, tzinfo=timezone.utc)


def _lines(root: Path, relative: str) -> list[dict]:
    return [json.loads(line) for line in (root / relative).read_text(encoding="utf-8").splitlines() if line.strip()]


def _log(root: Path, text: str, *, at: datetime, door: str = "mcp", group: str = "", session: str = "shift",
         turn_id: str | None = None, agent: str = "till-bot") -> dict:
    return mcp.record_turn(root, agent, text, origin="interactive", session=session, door=door, group=group,
                           turn_id=turn_id, at=at)


class _Bytes:
    """A standard input with a ``buffer`` of bytes, a terminal or not."""

    def __init__(self, data: bytes, *, terminal: bool) -> None:
        self.buffer = BytesIO(data)
        self._terminal = terminal

    def isatty(self) -> bool:
        return self._terminal


class TurnTests(ForestTestCase):
    def _forest(self, name: str = "bakery") -> Path:
        return self.make_forest({**FILES, "forest.toml": BAKERY_GROUPS}, name=name)

    def test_the_same_words_twice_through_one_door_are_two_lines(self) -> None:
        root = self._forest()
        for door, group in (("mcp", ""), ("cli", ""), ("mcp", "night-shift")):
            first = _log(root, f"yes ({door} {group})", at=NOON, door=door, group=group)
            again = _log(root, f"yes ({door} {group})", at=NOON, door=door, group=group)
            later = _log(root, f"yes ({door} {group})", at=NOON + timedelta(days=1), door=door, group=group)
            self.assertEqual([first["stored"], again["stored"], later["stored"]], ["written"] * 3)
            self.assertTrue(first["wrote"] and again["wrote"] and later["wrote"])
            self.assertEqual(len({first["id"], again["id"], later["id"]}), 3)
            same = [line for line in _lines(root, first["path"]) if line["text"] == f"yes ({door} {group})"]
            self.assertEqual(len(same), 2)

    def test_one_turn_through_two_sources_inside_the_window_is_one_line_that_names_both(self) -> None:
        root = self._forest()
        for order, (first_source, second_source) in enumerate(((("mcp", "night-shift"), ("cli", "")),
                                                              (("cli", ""), ("mcp", "night-shift")))):
            words = f"the rye trays are out ({order})"
            first = _log(root, words, at=NOON, door=first_source[0], group=first_source[1])
            second = _log(root, words, at=NOON + timedelta(seconds=intake.TURN_JOIN_SECONDS), door=second_source[0],
                          group=second_source[1])
            self.assertEqual((first["stored"], second["stored"]), ("written", "joined"))
            self.assertFalse(second["wrote"])
            self.assertEqual(second["id"], first["id"])
            labels = [f"group:{source[1]}" if source[1] else f"door:{source[0]}" for source in (first_source, second_source)]
            self.assertEqual(second["sources"], labels)
            kept = [line for line in _lines(root, first["path"]) if line["text"] == words]
            self.assertEqual(len(kept), 1)
            self.assertEqual(kept[0]["sources"], labels)
            self.assertEqual(kept[0]["source"]["door"], first_source[0])
            # A third time through either source is another turn.
            third = _log(root, words, at=NOON + timedelta(seconds=5), door=second_source[0], group=second_source[1])
            self.assertEqual(third["stored"], "written")

    def test_outside_the_window_or_by_another_role_or_session_the_same_words_are_two_lines(self) -> None:
        root = self._forest()
        first = _log(root, "the ovens are warm", at=NOON, door="mcp")
        late = _log(root, "the ovens are warm", at=NOON + timedelta(seconds=intake.TURN_JOIN_SECONDS + 1), door="cli")
        other = _log(root, "the ovens are warm", at=NOON, door="cli", session="other")
        self.assertEqual([late["stored"], other["stored"]], ["written", "written"])
        self.assertEqual(len([line for line in _lines(root, first["path"]) if line["text"] == "the ovens are warm"]), 3)

    def test_the_callers_turn_id_makes_one_turn_and_other_words_under_it_are_refused(self) -> None:
        root = self._forest()
        first = _log(root, "twelve loaves", at=NOON, turn_id="msg-0042")
        again = _log(root, "twelve loaves", at=NOON + timedelta(hours=3), turn_id="msg-0042")
        self.assertEqual((again["stored"], again["wrote"], again["id"]), ("already stored", False, first["id"]))
        through = _log(root, "twelve loaves", at=NOON + timedelta(hours=4), turn_id="msg-0042", door="cli")
        self.assertEqual(through["sources"], ["door:mcp", "door:cli"])
        next_day = _log(root, "twelve loaves", at=NOON + timedelta(hours=20), turn_id="msg-0042")
        self.assertEqual(next_day["id"], first["id"])
        before = (root / first["path"]).read_bytes()
        with self.assertRaises(MemoryForestError) as caught:
            _log(root, "thirteen loaves", at=NOON, turn_id="msg-0042")
        self.assertEqual((caught.exception.code, caught.exception.next_step), ("turn_id_reused", "fix"))
        self.assertEqual((root / first["path"]).read_bytes(), before)
        elsewhere = _log(root, "thirteen loaves", at=NOON, turn_id="msg-0042", session="other")
        self.assertEqual(elsewhere["stored"], "written")
        repeated = _log(root, "twelve loaves", at=NOON, turn_id="msg-0043")
        self.assertEqual(repeated["stored"], "written")
        with self.assertRaises(MemoryForestError) as bad:
            _log(root, "x", at=NOON, turn_id="msg\n42")
        self.assertEqual(bad.exception.code, "arguments_invalid")
        kept = [line for line in _lines(root, first["path"]) if line.get("turn") == "msg-0042"]
        self.assertEqual(len(kept), 2)  # the first, and the one of the other session

    def test_both_doors_take_the_turn_id(self) -> None:
        root = self._forest()
        reply, _version = mcp.handle_message(
            root, "till-bot", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "memory_log", "arguments": {"text": "the counter is open", "turn_id": "t-1", "session": "cli"}}},
            "stdio", "2025-11-25",
        )
        assert isinstance(reply, dict)
        first = json.loads(reply["result"]["content"][0]["text"])
        out = StringIO()
        with redirect_stdout(out), redirect_stderr(StringIO()), mock.patch.object(
                sys, "stdin", _Bytes(b"the counter is open\n", terminal=False)):
            code = forest_main(["log", "--as", "till-bot", "--turn-id", "t-1", "--root", str(root), "--json"])
        self.assertEqual(code, 0, out.getvalue())
        second = json.loads(out.getvalue())
        self.assertEqual((second["stored"], second["id"]), ("already stored", first["id"]))


class LaneWriterTests(ForestTestCase):
    """The lane writer's own checks, and stored lines it meets that it did not write."""

    def _forest(self, name: str = "writer") -> Path:
        return self.make_forest({**FILES, "forest.toml": BAKERY_GROUPS}, name=name)

    def test_each_bad_argument_is_its_own_refusal_and_nothing_is_written(self) -> None:
        root = self._forest()
        lane = root / "06 istm" / "till-bot"
        before = sorted(path.name for path in lane.iterdir())
        for code, change in (
            ("intake_agent_invalid", {"agent": "Till Bot"}),
            ("intake_agent_invalid", {"agent": "t" * 201}),
            ("intake_role_invalid", {"role": "baker"}),
            ("intake_origin_invalid", {"origin": "somewhere"}),
            ("intake_text_empty", {"text": " \n"}),
        ):
            arguments = {"agent": "till-bot", "text": "the till is open", "role": "agent", "origin": "interactive",
                         "session": "shift", **change}
            with self.subTest(code=code, change=sorted(change)):
                agent = arguments.pop("agent")
                text = arguments.pop("text")
                with self.assertRaises(MemoryForestError) as caught:
                    intake.append_owned(root, agent, text, at=NOON, **arguments)
                self.assertEqual(caught.exception.code, code)
        self.assertEqual(sorted(path.name for path in lane.iterdir()), before)

    def test_the_group_is_on_the_line_only_when_the_call_named_one_and_a_time_without_a_zone_is_utc(self) -> None:
        root = self._forest()
        plain = intake.append_owned(root, "till-bot", "no group", role="agent", origin="interactive",
                                    session="shift", at=datetime(2051, 3, 2, 12, 0))
        grouped = intake.append_owned(root, "till-bot", "a group", role="agent", origin="interactive",
                                      session="shift", group="night-shift", at=NOON)
        lines = {line["text"]: line for line in _lines(root, plain["path"])}
        self.assertNotIn("group", lines["no group"]["source"])
        self.assertEqual(lines["a group"]["source"]["group"], "night-shift")
        self.assertEqual(lines["no group"]["at"], "2051-03-02T12:00:00+00:00")
        self.assertEqual(lines["no group"]["source"]["place"], "2051-03-02T12:00:00+00:00")
        self.assertEqual(grouped["path"], plain["path"])

    def test_a_turn_id_is_matched_by_id_only_and_stored_lines_with_odd_times_are_read_safely(self) -> None:
        root = self._forest()
        first = _log(root, "Done.", at=NOON, turn_id="a")
        other = _log(root, "Done.", at=NOON + timedelta(seconds=5), turn_id="b", door="cli")
        self.assertEqual((first["stored"], other["stored"]), ("written", "written"))
        # The same turn again through the same source changes no lane, so the sources record stays as it was.
        sources.listed(root, ["till-bot"])  # the fixture's lane came by hand: the listing records it
        record = root / sources.RECORD
        kept = (record.read_bytes(), record.stat().st_ino)
        self.assertEqual(_log(root, "Done.", at=NOON + timedelta(seconds=9), turn_id="a")["stored"], "already stored")
        self.assertEqual((record.read_bytes(), record.stat().st_ino), kept)
        path = root / first["path"]
        with path.open("a", encoding="utf-8") as handle:
            for number, moment in enumerate(("not a time", "2051-03-02T13:00:00")):
                handle.write(json.dumps({"id": f"hand-{number}", "at": moment, "role": "agent", "session": "shift",
                                         "source": {"adapter": "jsonl"}, "text": f"by hand {number}"}) + "\n")
        unreadable = _log(root, "by hand 0", at=NOON + timedelta(hours=1), door="cli")
        naive = _log(root, "by hand 1", at=NOON + timedelta(hours=1, seconds=30), door="cli")
        self.assertEqual((unreadable["stored"], naive["stored"]), ("written", "joined"))
        self.assertEqual(naive["sources"], ["adapter:jsonl", "door:cli"])


def _tool(root: Path, name: Any, arguments: Any, *, group: str | None = "night-shift",
          version: str = "2025-11-25") -> dict:
    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    reply, _version = mcp.handle_message(root, "till-bot", message, "stdio", version, group=group)
    assert isinstance(reply, dict)
    return reply


def _body(reply: dict) -> dict:
    return json.loads(reply["result"]["content"][0]["text"])


class ToolCallTests(ForestTestCase):
    """One group call: what is refused before anything runs, what an admission adds, and every fault after it."""

    def _forest(self) -> Path:
        return self.make_forest({**FILES, "forest.toml": BAKERY_GROUPS}, name="calls")

    def test_a_call_that_names_no_tool_or_passes_no_object_or_bad_arguments_is_refused_before_it_runs(self) -> None:
        root = self._forest()
        before = (root / "forest.toml").read_bytes()
        for name, arguments, version in (
            ("memory_nothing", {"agent": "New Clerk"}, "2025-11-25"),
            ("memory_log", ["New Clerk"], "2025-11-25"),
            ("memory_log", {"agent": "New Clerk", "text": 7}, "2025-06-18"),
        ):
            with self.subTest(name=name, version=version):
                reply = _tool(root, name, arguments, version=version)
                self.assertEqual(reply["error"]["code"], -32602, reply)
        reply = _tool(root, "memory_log", {"agent": "New Clerk", "text": 7})
        self.assertEqual(_body(reply)["code"], "arguments_invalid")
        self.assertEqual((root / "forest.toml").read_bytes(), before)

    def test_only_the_call_that_admitted_the_caller_says_so(self) -> None:
        root = self._forest()
        first = _body(_tool(root, "memory_log", {"agent": "New Clerk", "text": "the first turn"}))
        second = _body(_tool(root, "memory_log", {"agent": "New Clerk", "text": "the second turn"}))
        self.assertEqual(first.get("admitted"), "new-clerk")
        self.assertNotIn("admitted", second)

    def test_every_fault_after_an_admission_is_a_refusal_that_names_the_admission(self) -> None:
        root = self._forest()
        faults = (
            ("bad argument inside", mcp._BadArguments("The lines are two numbers."), None),
            ("refusal", MemoryForestError("index_busy", "busy", next_step="retry"), "index_busy"),
            ("disk", PermissionError(13, "Permission denied", str(root / "06 istm")), None),
            ("fault", RuntimeError("a fault of the code"), "tool_failed"),
        )
        for number, (label, fault, code) in enumerate(faults):
            with self.subTest(label):
                def invoke(*_args: Any, fault: BaseException = fault, **_kwargs: Any) -> Any:
                    raise fault

                with mock.patch.object(mcp, "_invoke", invoke):
                    reply = _tool(root, "memory_search", {"agent": f"Clerk {number}", "words": "trays"})
                if isinstance(fault, mcp._BadArguments):
                    self.assertEqual(reply["error"]["code"], -32602, reply)
                    continue
                body = _body(reply)
                self.assertTrue(reply["result"]["isError"])
                if code is not None:
                    self.assertEqual(body["code"], code)
                self.assertEqual(body["details"]["admitted"], f"clerk-{number}")

    def test_a_turn_whose_answer_cannot_be_masked_still_says_it_was_written_and_a_read_does_not(self) -> None:
        root = self._forest()

        real = mcp._tool_text

        def failing(fault: BaseException) -> Any:
            # The answer's masking fails; a refusal's own masking (``refusal=True``) still works.
            def text(*args: Any, **kwargs: Any) -> Any:
                if kwargs.get("refusal"):
                    return real(*args, **kwargs)
                raise fault
            return text

        unreadable = failing(MemoryForestError("config_invalid", "the settings cannot be read now", next_step="report"))
        broken = failing(RuntimeError("a fault of the code"))

        with mock.patch.object(mcp, "_tool_text", unreadable):
            written = _body(_tool(root, "memory_log", {"agent": "Till Bot", "text": "masked later"}))
            read = _tool(root, "memory_search", {"agent": "Till Bot", "words": "trays"})
        self.assertEqual((written["wrote"], written["stored"], written["withheld"]), (True, "written", "config_invalid"))
        self.assertNotIn("id", written)
        self.assertTrue(read["result"]["isError"])
        self.assertEqual(_body(read)["code"], "internal_error")
        with mock.patch.object(mcp, "_tool_text", failing(PermissionError(13, "Permission denied", str(root)))):
            disk = _tool(root, "memory_search", {"agent": "Till Bot", "words": "trays"})
        self.assertTrue(disk["result"]["isError"])
        with mock.patch.object(mcp, "_tool_text", broken):
            failed = _tool(root, "memory_search", {"agent": "Till Bot", "words": "trays"})
        self.assertEqual(_body(failed)["code"], "tool_failed")
        self.assertEqual(sorted(mcp.withheld({"wrote": True, "id": "x", "path": "p"}, OSError(5, "I/O error"))),
                         ["withheld", "wrote"])

    def test_a_forest_this_account_cannot_write_refuses_the_turn_as_unwritten_and_names_the_index(self) -> None:
        root = self._forest()
        refusal = MemoryForestError("index_read_only", "read only", next_step="report")
        with mock.patch.object(mcp.search, "unwritable", lambda _root: refusal):
            with self.assertRaises(MemoryForestError) as caught:
                _log(root, "not kept", at=NOON)
        self.assertEqual(caught.exception.code, "log_read_only")
        other = MemoryForestError("index_link", "a link", next_step="report")
        with mock.patch.object(mcp.search, "unwritable", lambda _root: other):
            with self.assertRaises(MemoryForestError) as linked:
                _log(root, "not kept", at=NOON)
        self.assertEqual(linked.exception.code, "index_link")
        mcp.search.build(root)
        written = _log(root, "kept", at=NOON)
        self.assertEqual((written["indexed"], written["index"]), (True, "updated"))
        with mock.patch.object(mcp.search, "update_files", lambda *_args, **_kwargs: {"indexed": False}):
            busy = _log(root, "kept, the index busy", at=NOON)
        self.assertEqual((busy["indexed"], busy["index"], busy["reason"]), (False, "pending", "index_busy"))


class WriteAnswerTests(ForestTestCase):
    """Once the line is stored the answer says written; the tree and the index are named as they came out."""

    def _forest(self, name: str = "bakery") -> Path:
        return self.make_forest({**FILES, "forest.toml": BAKERY_GROUPS}, name=name)

    def test_a_writer_lock_held_by_another_process_leaves_the_tree_pending_and_the_next_write_makes_it(self) -> None:
        root = self._forest()
        code = (
            "import sys\n"
            "sys.path.insert(0, sys.argv[2])\n"
            "from memory_forest.locking import writer_lock\n"
            "with writer_lock(sys.argv[1], label='held by a test'):\n"
            "    sys.stdout.write('held\\n'); sys.stdout.flush()\n"
            "    sys.stdin.readline()\n"
            "sys.stdout.write('released\\n'); sys.stdout.flush()\n"
        )
        holder = subprocess.Popen([sys.executable, "-c", code, str(root), SRC], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            assert holder.stdout is not None and holder.stdin is not None
            self.assertEqual(_line_within(holder.stdout, 30), "held")
            body = mcp.record_turn(root, "crate-bot", "first crate on the shelf", origin="interactive",
                                   session="shift", at=NOON, tree_wait=0.2)
            self.assertEqual((body["wrote"], body["stored"], body["tree"]), (True, "written", "pending"))
            self.assertEqual(body["tree_reason"], "lock_busy")
            self.assertFalse((root / "02 ltm" / "crate-bot_LTM.md").exists())
            self.assertEqual(len(_lines(root, body["path"])), 1)
            holder.stdin.write("go\n")
            holder.stdin.flush()
            self.assertEqual(_line_within(holder.stdout, 30), "released")
        finally:
            _stop(holder)
        again = mcp.record_turn(root, "crate-bot", "second crate on the shelf", origin="interactive",
                                session="shift", at=NOON)
        self.assertEqual((again["wrote"], again["tree"]), (True, "made"))
        self.assertTrue((root / "02 ltm" / "crate-bot_LTM.md").is_file())
        self.assertEqual([line["text"] for line in _lines(root, body["path"])],
                         ["first crate on the shelf", "second crate on the shelf"])
        third = mcp.record_turn(root, "crate-bot", "third crate", origin="interactive", session="shift", at=NOON)
        self.assertEqual(third["tree"], "already there")
        self.assertEqual(levels.agent_tree(root, "crate-bot"), "already there")

    def test_two_processes_write_one_agent_through_two_groups_at_once(self) -> None:
        root = self._forest()
        turns = 15
        code = (
            "import json, sys\n"
            "sys.path.insert(0, sys.argv[2])\n"
            "from datetime import datetime, timezone\n"
            "from memory_forest.mcp import record_turn\n"
            "sys.stdout.write('ready\\n'); sys.stdout.flush()\n"
            "sys.stdin.readline()\n"
            "for number in range(int(sys.argv[4])):\n"
            "    body = record_turn(sys.argv[1], 'crate-bot', f'{sys.argv[3]} turn {number}', origin='interactive',\n"
            "                       session='shift', group=sys.argv[3])\n"
            "    sys.stdout.write(json.dumps(body) + '\\n'); sys.stdout.flush()\n"
        )
        procs = [subprocess.Popen([sys.executable, "-c", code, str(root), SRC, group, str(turns)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for group in ("night-shift", "day-shift")]
        try:
            for proc in procs:
                assert proc.stdout is not None
                self.assertEqual(_line_within(proc.stdout, 30), "ready")
            for proc in procs:
                assert proc.stdin is not None
                proc.stdin.write("go\n")
                proc.stdin.flush()
            answers = {}
            for proc, group in zip(procs, ("night-shift", "day-shift")):
                assert proc.stdout is not None
                answers[group] = [json.loads(_line_within(proc.stdout, 60)) for _turn in range(turns)]
                self.assertEqual(proc.wait(timeout=60), 0, proc.stderr.read() if proc.stderr else "")
        finally:
            for proc in procs:
                _stop(proc)
        for group, bodies in answers.items():
            self.assertTrue(all(body["wrote"] and body["stored"] == "written" for body in bodies), bodies)
        lines = []
        for path in sorted((root / "06 istm" / "crate-bot").glob("*.jsonl")):
            lines.extend(_lines(root, str(path.relative_to(root))))
        for group in ("night-shift", "day-shift"):
            mine = [line["text"] for line in lines if line["sources"] == [f"group:{group}"]]
            self.assertEqual(mine, [f"{group} turn {number}" for number in range(turns)])
        self.assertTrue((root / "02 ltm" / "crate-bot_LTM.md").is_file())


class BoundTests(ForestTestCase):
    """One bound on a turn's words for both doors; the protocol line has its own, larger one."""

    def test_at_the_bound_and_one_past_it_plain_and_escaped_through_both_doors(self) -> None:
        root = self.make_forest({**FILES, "forest.toml": BAKERY_GROUPS})
        self.assertGreater(mcp.MESSAGE_LIMIT, 6 * mcp.TURN_MAX_BYTES + 4096)
        for kind, unit in (("plain", "a"), ("escaped", "\x01")):
            at_bound = unit * mcp.TURN_MAX_BYTES
            past = at_bound + unit
            for text, fits in ((at_bound, True), (past, False)):
                with self.subTest(kind=kind, fits=fits, door="memory_log"):
                    answer = _stdio_log(root, text)
                    self.assertEqual(answer.get("code"), None if fits else "log_too_large", answer)
                with self.subTest(kind=kind, fits=fits, door="forest log"):
                    out = StringIO()
                    with redirect_stdout(out), redirect_stderr(StringIO()), mock.patch.object(
                            sys, "stdin", _Bytes(text.encode("utf-8"), terminal=False)):
                        code = forest_main(["log", "--as", "till-bot", "--root", str(root), "--json"])
                    body = json.loads(out.getvalue())
                    if fits:
                        self.assertEqual(code, 0, out.getvalue()[:300])
                        self.assertTrue(body["wrote"])
                    else:
                        self.assertEqual(body["error"]["code"], "log_too_large")
                        self.assertEqual(body["error"]["next"], "fix")

    def test_a_terminal_has_no_deadline_and_a_pipe_has_one(self) -> None:
        ticks = iter(range(0, 10_000_000, 100))

        def clock() -> float:
            return float(next(ticks))

        for terminal, refused in ((True, False), (False, True)):
            read_end, write_end = os.pipe()
            os.write(write_end, b"a turn typed by a person\n")
            os.close(write_end)
            stream = os.fdopen(read_end, "rb")
            try:
                fake = _Bytes(b"", terminal=terminal)
                fake.buffer = stream  # type: ignore[assignment]
                with mock.patch.object(sys, "stdin", fake), mock.patch.object(intake.time, "monotonic", clock):
                    if refused:
                        with self.assertRaises(MemoryForestError) as caught:
                            cli_find._log_text()
                        self.assertEqual(caught.exception.code, "log_read_timeout")
                    else:
                        self.assertEqual(cli_find._log_text(), "a turn typed by a person\n")
            finally:
                stream.close()


def _stdio_log(root: Path, text: str) -> dict:
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory_log", "arguments": {"text": text}}},
    ]
    stdin = BytesIO(b"".join(json.dumps(message, ensure_ascii=True).encode("ascii") + b"\n" for message in messages))
    stdout = BytesIO()
    mcp.serve_stdio(root, "till-bot", stdin=stdin, stdout=stdout)
    replies = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
    reply = next(item for item in replies if item.get("id") == 2)
    if "error" in reply:
        return {"code": f"rpc {reply['error']['code']}"}
    return json.loads(reply["result"]["content"][0]["text"])


def _line_within(stream, seconds: float) -> str:
    ready, _ignored, _also = select.select([stream], [], [], seconds)
    if not ready:
        raise AssertionError("no line from the child within the deadline")
    return stream.readline().strip()


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=30)
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is not None:
            stream.close()
