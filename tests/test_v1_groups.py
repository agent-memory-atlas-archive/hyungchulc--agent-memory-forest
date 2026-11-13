"""Several agents behind one connection, and the same doors as commands.

The bakery, Ilse, and the bots are invented. Nothing here is a real person,
shop, transcript or secret.
"""

from __future__ import annotations

import json
import select
import subprocess
import sys
import threading
import time
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest import mock

from memory_forest import groups, levels, mcp, search
from memory_forest.cli import forest_main
from memory_forest.config import load_config, parse_config, render_config
from memory_forest.errors import MemoryForestError

from test_v1_find import FILES, _paths, _post, _texts
from v1_support import ForestTestCase


SRC = str(Path(__file__).resolve().parents[1] / "src")
COOLING = "04 stm/oven-bot/methods/cooling.md"
SECRET = "secret cooling"


def _agents_block(extra: str) -> str:
    return FILES["forest.toml"].replace(
        '[agents]\norchestrators = ["front-desk"]\n',
        "[agents]\norchestrators = [\"front-desk\"]\n" + extra,
    )


BAKERY_GROUPS = _agents_block(
    """
[agents.known.till-bot]
name = "Till Bot"

[agents.known.oven-bot]
name = "Oven Bot"

[agents.known.delivery-bot]
name = "Delivery Bot"

[agents.groups.bakery-bots]
members = ["till-bot", "oven-bot"]
open = false

[agents.groups.night-shift]
members = []
open = true
"""
)

STUDIO = """# kept for the group test
[forest]
timezone = "UTC"

[owner]
id = "juno-hale"
name = "Juno"

[agents]
orchestrators = ["studio-hub"]

[agents.known.kiln-bot]
name = "Kiln Bot"

[agents.known.ledger-bot]
name = "Ledger Bot"

[agents.known.post-bot]
name = "Post Bot"

[agents.known.studio-hub]
name = "Studio Hub"

[agents.groups.studio-counter]
members = ["kiln-bot", "ledger-bot"]
open = false

[agents.groups.night-kiln]
members = ["kiln-bot"]
open = true
"""


@contextmanager
def _stdin(text: str):
    previous = sys.stdin
    sys.stdin = StringIO(text)
    try:
        yield
    finally:
        sys.stdin = previous


def _call(name: str, arguments: dict, *, call_id: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": call_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def _tool_body(reply: dict) -> dict:
    return json.loads(reply["result"]["content"][0]["text"])


class GroupResolutionTests(ForestTestCase):
    def _studio(self) -> Path:
        return self.make_forest({"forest.toml": STUDIO}, name="studio")

    def test_settings_round_trip_and_reject_a_group_that_names_itself(self) -> None:
        config = parse_config(STUDIO)
        self.assertEqual(config.agents.group("studio-counter").members, ("kiln-bot", "ledger-bot"))
        self.assertFalse(config.agents.group("studio-counter").open)
        self.assertTrue(config.agents.group("night-kiln").open)
        again = parse_config(render_config(config))
        self.assertEqual(again.agents.groups, config.agents.groups)
        self.assertEqual(again.agents.known, config.agents.known)
        with self.assertRaises(MemoryForestError) as caught:
            parse_config('[agents.groups.kiln-bot]\nmembers = ["kiln-bot"]\nopen = false\n')
        self.assertEqual(caught.exception.details["key"], "agents.groups.kiln-bot")

    def test_a_name_resolves_only_inside_the_group(self) -> None:
        root = self._studio()
        cases = {
            "kiln-bot": "kiln-bot",
            "Kiln Bot": "kiln-bot",
            "KILN-BOT": "kiln-bot",
            "kiln bot": "kiln-bot",
            "kiln_bot": "kiln-bot",
            "KilnBot": "kiln-bot",
        }
        for given, agent_id in cases.items():
            with self.subTest(given=given):
                resolved = groups.resolve_caller(root, "studio-counter", given)
                self.assertEqual(resolved.id, agent_id)
                self.assertFalse(resolved.wrote)
        before = (root / "forest.toml").read_bytes()
        self.assertEqual(groups.resolve_caller(root, "night-kiln", "kilnbot").id, "kiln-bot")
        self.assertEqual((root / "forest.toml").read_bytes(), before)

    def test_each_near_miss_and_the_refusals_name_the_next_move(self) -> None:
        root = self._studio()
        before = (root / "forest.toml").read_bytes()
        slips = ("kiln-bott", "kiln-bt", "kinl-bot")
        for given in slips:
            with self.subTest(given=given):
                with self.assertRaises(MemoryForestError) as caught:
                    groups.resolve_caller(root, "night-kiln", given)
                self.assertEqual(caught.exception.code, "agent_near_miss")
                self.assertEqual(caught.exception.details["agent"], "kiln-bot")
                self.assertEqual(caught.exception.details["name"], "Kiln Bot")
                self.assertEqual(caught.exception.next_step, "run")
                self.assertTrue(caught.exception.command)
        self.assertEqual((root / "forest.toml").read_bytes(), before)

        with self.assertRaises(MemoryForestError) as stranger:
            groups.resolve_caller(root, "studio-counter", "parcel-clerk")
        self.assertEqual(stranger.exception.code, "agent_not_in_group")
        listed = stranger.exception.details["members"]
        self.assertEqual(
            sorted((row["id"], row["name"]) for row in listed),
            [("kiln-bot", "Kiln Bot"), ("ledger-bot", "Ledger Bot")],
        )
        self.assertEqual(stranger.exception.next_step, "run")

        with self.assertRaises(MemoryForestError) as outside:
            groups.resolve_caller(root, "night-kiln", "Post Bot")
        self.assertEqual(outside.exception.code, "agent_outside_group")
        self.assertEqual(outside.exception.details["agent"], "post-bot")
        with self.assertRaises(MemoryForestError) as slipped:
            groups.resolve_caller(root, "night-kiln", "post-bott")
        self.assertEqual(slipped.exception.code, "agent_outside_group")
        self.assertEqual(slipped.exception.details["agent"], "post-bot")
        self.assertIn("one edit", slipped.exception.message)

        with self.assertRaises(MemoryForestError) as orchestrator:
            groups.resolve_caller(root, "night-kiln", "Studio Hub")
        self.assertEqual(orchestrator.exception.code, "orchestrator_outside_group")
        self.assertEqual(orchestrator.exception.details["agent"], "studio-hub")

        with self.assertRaises(MemoryForestError) as itself:
            groups.resolve_caller(root, "night-kiln", "night-kiln")
        self.assertEqual(itself.exception.code, "agent_is_group")
        self.assertEqual((root / "forest.toml").read_bytes(), before)

    def test_an_open_group_admits_a_name_once_and_the_second_call_writes_nothing(self) -> None:
        root = self._studio()
        first = groups.resolve_caller(root, "night-kiln", "Parcel Clerk")
        self.assertEqual((first.id, first.wrote), ("parcel-clerk", True))
        text = (root / "forest.toml").read_text(encoding="utf-8")
        self.assertIn("# kept for the group test", text)
        parsed = parse_config(text)
        self.assertEqual(parsed.agents.group("night-kiln").members, ("kiln-bot", "parcel-clerk"))
        self.assertEqual(parsed.agents.declared("parcel-clerk").name, "Parcel Clerk")
        blob = (root / "forest.toml").read_bytes()
        second = groups.resolve_caller(root, "night-kiln", "Parcel Clerk")
        third = groups.resolve_caller(root, "night-kiln", "parcel-clerk")
        self.assertFalse(second.wrote)
        self.assertFalse(third.wrote)
        self.assertEqual(second.id, "parcel-clerk")
        self.assertEqual((root / "forest.toml").read_bytes(), blob)
        self.assertIn("# kept for the group test", blob.decode("utf-8"))

    def test_two_calls_at_once_admit_one_member_and_write_the_settings_once(self) -> None:
        root = self._studio()
        self._admit_from_two_threads(root)
        self.assertEqual(load_config(root).agents.group("night-kiln").members, ("kiln-bot", "parcel-clerk"))
        other = self.make_forest({"forest.toml": STUDIO}, name="studio-procs")
        wrote = self._admit_from_two_processes(other)
        self.assertEqual(sorted(wrote), ["kept", "wrote"])
        self.assertEqual(load_config(other).agents.group("night-kiln").members, ("kiln-bot", "parcel-clerk"))
        self.assertEqual(load_config(other).agents.declared("parcel-clerk").name, "Parcel Clerk")

    def _admit_from_two_threads(self, root: Path) -> None:
        barrier = threading.Barrier(2)
        found: list[bool] = []
        errors: list[BaseException] = []

        def work() -> None:
            try:
                barrier.wait(30)
                found.append(groups.resolve_caller(root, "night-kiln", "Parcel Clerk").wrote)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=work) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(sum(found), 1)

    def _admit_from_two_processes(self, root: Path) -> list[str]:
        code = (
            "import sys\n"
            "sys.path.insert(0, sys.argv[2])\n"
            "from memory_forest.groups import resolve_caller\n"
            "sys.stdout.write('ready\\n')\n"
            "sys.stdout.flush()\n"
            "sys.stdin.readline()\n"
            "result = resolve_caller(sys.argv[1], 'night-kiln', 'Parcel Clerk')\n"
            "sys.stdout.write('wrote\\n' if result.wrote else 'kept\\n')\n"
            "sys.stdout.flush()\n"
        )
        procs = []
        for _number in range(2):
            procs.append(
                subprocess.Popen(
                    [sys.executable, "-c", code, str(root), SRC],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        ready = 0
        deadline = time.monotonic() + 30
        while ready < 2 and time.monotonic() < deadline:
            streams = [proc.stdout for proc in procs if proc.stdout is not None]
            remaining = max(0.0, deadline - time.monotonic())
            readable, _ignored, _also = select.select(streams, [], [], remaining)
            for stream in readable:
                if stream.readline().strip() == "ready":
                    ready += 1
        self.assertEqual(ready, 2)
        for proc in procs:
            assert proc.stdin is not None
            proc.stdin.write("go\n")
            proc.stdin.flush()
        wrote = []
        for proc in procs:
            assert proc.stdout is not None
            line = proc.stdout.readline().strip()
            stderr = proc.stderr.read() if proc.stderr is not None else ""
            self.assertEqual(proc.wait(timeout=30), 0, stderr)
            wrote.append(line)
            if proc.stdin is not None:
                proc.stdin.close()
            if proc.stdout is not None:
                proc.stdout.close()
            if proc.stderr is not None:
                proc.stderr.close()
        return wrote


class GroupConnectionTests(ForestTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_forest({**FILES, "forest.toml": BAKERY_GROUPS})

    def _reply(self, name: str, arguments: dict, *, group: str | None = "bakery-bots", reader: str = "") -> dict:
        message = _call(name, arguments)
        reply, _version = mcp.handle_message(
            self.root, reader, message, "stdio", "2025-11-25", group=group
        )
        assert isinstance(reply, dict)
        return reply

    def test_as_keeps_the_tools_and_an_agent_argument_cannot_set_the_reader(self) -> None:
        listed, _version = mcp.handle_message(
            self.root,
            "till-bot",
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            "stdio",
            "2025-11-25",
        )
        assert isinstance(listed, dict)
        tools = listed["result"]["tools"]
        self.assertEqual(
            [tool["name"] for tool in tools],
            ["memory_search", "memory_read", "memory_timeline", "memory_navigate", "memory_log"],
        )
        for tool in tools:
            self.assertNotIn("agent", tool["inputSchema"]["properties"])
        refused = self._reply(
            "memory_search",
            {"agent": "oven-bot", "words": "secret cooling curve"},
            group=None,
            reader="till-bot",
        )
        self.assertTrue(refused["result"]["isError"])
        body = _tool_body(refused)
        self.assertEqual(body["code"], "arguments_invalid")
        self.assertNotIn(SECRET, refused["result"]["content"][0]["text"])

    def test_each_tool_reads_the_members_scope_and_not_another_members(self) -> None:
        listed, _version = mcp.handle_message(
            self.root,
            "",
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
            "stdio",
            None,
            group="bakery-bots",
        )
        assert isinstance(listed, dict)
        self.assertLessEqual(len(listed["result"]["instructions"]), 2048)
        self.assertIn("agent", listed["result"]["instructions"])
        tools, _version = mcp.handle_message(
            self.root,
            "",
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            "stdio",
            "2025-11-25",
            group="bakery-bots",
        )
        assert isinstance(tools, dict)
        for tool in tools["result"]["tools"]:
            self.assertIn("agent", tool["inputSchema"]["required"])

        till_secret = _tool_body(self._reply("memory_search", {"agent": "till-bot", "words": "secret cooling curve"}))
        oven_secret = _tool_body(self._reply("memory_search", {"agent": "oven-bot", "words": "secret cooling curve"}))
        self.assertNotIn(SECRET, _texts(till_secret))
        self.assertIn(SECRET, _texts(oven_secret))
        till_trays = _tool_body(self._reply("memory_search", {"agent": "till-bot", "words": "till counted the trays"}))
        oven_trays = _tool_body(self._reply("memory_search", {"agent": "oven-bot", "words": "till counted the trays"}))
        self.assertIn("till counted", _texts(till_trays))
        self.assertNotIn("till counted", _texts(oven_trays))

        till_read = self._reply("memory_read", {"agent": "till-bot", "path": COOLING})
        self.assertTrue(till_read["result"]["isError"])
        self.assertEqual(_tool_body(till_read)["code"], "out_of_scope")
        self.assertNotIn(SECRET, till_read["result"]["content"][0]["text"])
        oven_read = _tool_body(self._reply("memory_read", {"agent": "oven-bot", "path": COOLING}))
        self.assertIn(SECRET, _texts(oven_read))

        till_time = _tool_body(self._reply("memory_timeline", {"agent": "till-bot", "subject": "oven-bot"}))
        oven_time = _tool_body(self._reply("memory_timeline", {"agent": "oven-bot", "subject": "oven-bot"}))
        self.assertNotIn(SECRET, _texts(till_time))
        self.assertIn(SECRET, _texts(oven_time))

        till_walk = _tool_body(self._reply("memory_navigate", {"agent": "till-bot"}))
        oven_walk = _tool_body(self._reply("memory_navigate", {"agent": "oven-bot"}))
        self.assertIn("bakery", till_walk["results"][0]["sections"][0]["text"])
        self.assertNotIn("oven-bot", till_walk["results"][0]["sections"][0]["text"])
        self.assertNotIn("till-bot", oven_walk["results"][0]["sections"][0]["text"])

        logged = _tool_body(
            self._reply("memory_log", {"agent": "till-bot", "text": "till logged a group turn about the rye count"})
        )
        self.assertTrue(logged["wrote"])
        self.assertIn("06 istm/till-bot/", logged["path"])
        till_lane = (self.root / logged["path"]).read_text(encoding="utf-8")
        self.assertIn("rye count", till_lane)
        oven_lane = (self.root / "06 istm" / "oven-bot" / "2051-03-02.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("rye count", oven_lane)
        seen = json.loads((self.root / ".forest" / "mcp" / "seen.json").read_text(encoding="utf-8"))
        self.assertEqual(seen["till-bot"]["tool"], "memory_search")
        self.assertTrue(seen["till-bot"]["last"])
        self.assertEqual(seen["oven-bot"]["tool"], "memory_search")

        stranger = self._reply("memory_search", {"agent": "parcel-clerk", "words": "rye"})
        self.assertTrue(stranger["result"]["isError"])
        body = _tool_body(stranger)
        self.assertEqual(body["code"], "agent_not_in_group")
        self.assertEqual(body["next"], "run")
        self.assertIn("till-bot", json.dumps(body["details"]))
        self.assertIn("Till Bot", json.dumps(body["details"]))

    def test_memory_log_on_an_open_group_writes_the_members_lane_and_its_tree_follows(self) -> None:
        reply = self._reply(
            "memory_log",
            {"agent": "Parcel Clerk", "text": "parcel clerk logged the evening crates"},
            group="night-shift",
        )
        body = _tool_body(reply)
        self.assertFalse(reply["result"]["isError"])
        self.assertTrue(body["wrote"])
        self.assertIn("06 istm/parcel-clerk/", body["path"])
        self.assertIn("evening crates", (self.root / body["path"]).read_text(encoding="utf-8"))
        known = load_config(self.root).agents.declared("parcel-clerk")
        assert known is not None
        self.assertEqual(known.name, "Parcel Clerk")
        blob = (self.root / "forest.toml").read_bytes()
        again = self._reply(
            "memory_search",
            {"agent": "parcel-clerk", "words": "evening crates"},
            group="night-shift",
        )
        self.assertFalse(again["result"]["isError"])
        self.assertEqual((self.root / "forest.toml").read_bytes(), blob)
        levels.apply(self.root, ["parcel-clerk"])
        self.assertTrue((self.root / "02 ltm" / "parcel-clerk_LTM.md").is_file())

    def test_http_group_uses_the_member(self) -> None:
        token = mcp.ensure_token(self.root).decode("ascii")
        server = mcp.serve_http(self.root, "", "127.0.0.1", 0, token=token.encode("ascii"), group="bakery-bots")
        try:
            status, body = _post(
                server.server_address[1],
                _call("memory_read", {"agent": "oven-bot", "path": COOLING}),
                token=token,
                protocol="2025-11-25",
            )
            self.assertEqual(status, 200)
            payload = json.loads(body)
            self.assertFalse(payload["result"]["isError"])
            self.assertIn(SECRET, payload["result"]["content"][0]["text"])
            status, body = _post(
                server.server_address[1],
                _call("memory_read", {"agent": "till-bot", "path": COOLING}, call_id=2),
                token=token,
                protocol="2025-11-25",
            )
            self.assertEqual(status, 200)
            payload = json.loads(body)
            self.assertTrue(payload["result"]["isError"])
            self.assertNotIn(SECRET, payload["result"]["content"][0]["text"])
        finally:
            server.shutdown()
            server.server_close()

    def test_the_stdio_flag_serves_the_group(self) -> None:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from memory_forest.cli import forest_main; raise SystemExit(forest_main())",
                "mcp",
                "--as-group",
                "bakery-bots",
                "--root",
                str(self.root),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.stdin is not None and proc.stdout is not None
        try:
            proc.stdin.write(
                json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
                ).encode()
                + b"\n"
            )
            proc.stdin.close()
            line = proc.stdout.readline()
            listed = json.loads(line)
            self.assertIn("agent", listed["result"]["tools"][0]["inputSchema"]["required"])
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
            if proc.stderr is not None:
                proc.stderr.close()
            if proc.stdout is not None:
                proc.stdout.close()


class GroupCommandTests(ForestTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_forest({**FILES, "forest.toml": BAKERY_GROUPS})
        search.build(self.root)

    def _cli(self, *args: str, stdin: str = "") -> tuple[int, str, str]:
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err), _stdin(stdin):
            code = forest_main([*args, "--root", str(self.root), "--json"])
        return code, out.getvalue(), err.getvalue()

    def test_each_command_answers_in_the_members_scope(self) -> None:
        found = self._cli("search", "secret cooling curve", "--as-group", "bakery-bots", "--agent", "oven-bot")
        self.assertEqual(found[0], 0)
        self.assertIn(COOLING, _paths(json.loads(found[1])))
        hidden = self._cli("search", "secret cooling curve", "--as-group", "bakery-bots", "--agent", "till-bot")
        self.assertEqual(hidden[0], 0)
        self.assertEqual(json.loads(hidden[1])["results"], [])

        read = self._cli("read", COOLING, "--as-group", "bakery-bots", "--agent", "oven-bot")
        self.assertEqual(read[0], 0)
        self.assertIn(SECRET, read[1])
        refused = self._cli("read", COOLING, "--as-group", "bakery-bots", "--agent", "till-bot")
        self.assertEqual(refused[0], 2)
        self.assertNotIn(SECRET, refused[1] + refused[2])
        self.assertEqual(json.loads(refused[1])["error"]["code"], "out_of_scope")

        when = self._cli("timeline", "subject", "oven-bot", "--as-group", "bakery-bots", "--agent", "oven-bot")
        self.assertEqual(when[0], 0)
        self.assertIn(SECRET, when[1])
        other = self._cli("timeline", "subject", "oven-bot", "--as-group", "bakery-bots", "--agent", "till-bot")
        self.assertEqual(other[0], 0)
        self.assertNotIn(SECRET, other[1])

        walked = self._cli("navigate", "--as-group", "bakery-bots", "--agent", "till-bot")
        self.assertEqual(walked[0], 0)
        self.assertIn("bakery", walked[1])
        self.assertNotIn("oven-bot", walked[1])
        oven_walk = self._cli("navigate", "--as-group", "bakery-bots", "--agent", "oven-bot")
        self.assertNotIn("till-bot", oven_walk[1])

    def test_forest_log_reads_stdin_writes_the_lane_and_refuses_an_argument(self) -> None:
        leaked = self._cli("log", "--as", "till-bot", "should not land from argv")
        self.assertEqual(leaked[0], 2)
        error = json.loads(leaked[1])["error"]
        self.assertEqual(error["code"], "log_text_from_stdin")
        self.assertEqual(error["next"], "run")
        self.assertTrue(error["command"])
        self.assertNotIn("should not land from argv", (self.root / "06 istm" / "till-bot" / "2051-03-02.jsonl").read_text(encoding="utf-8"))
        flagged = self._cli("log", "--as", "till-bot", "--text", "also not an argument")
        self.assertEqual(json.loads(flagged[1])["error"]["code"], "log_text_from_stdin")
        role = self._cli("log", "--as", "till-bot", "--role", "owner", stdin="nope\n")
        self.assertEqual(json.loads(role[1])["error"]["code"], "log_caller_fields")
        self.assertEqual(json.loads(role[1])["error"]["next"], "run")

        logged = self._cli(
            "log",
            "--as-group",
            "night-shift",
            "--agent",
            "Parcel Clerk",
            stdin="password: tulip7x\n",
        )
        self.assertEqual(logged[0], 0, logged[1] + logged[2])
        body = json.loads(logged[1])
        self.assertTrue(body["wrote"])
        self.assertIn("06 istm/parcel-clerk/", body["path"])
        lane = (self.root / body["path"]).read_text(encoding="utf-8")
        self.assertNotIn("tulip7x", lane)
        self.assertIn("password", lane)
        levels.apply(self.root, ["parcel-clerk"])
        self.assertTrue((self.root / "02 ltm" / "parcel-clerk_LTM.md").is_file())
        blob = (self.root / "forest.toml").read_bytes()
        again = self._cli(
            "log",
            "--as-group",
            "night-shift",
            "--agent",
            "Parcel Clerk",
            stdin="the second crate was already on the shelf\n",
        )
        self.assertEqual(again[0], 0, again[2])
        self.assertEqual((self.root / "forest.toml").read_bytes(), blob)

        fixed = datetime(2051, 3, 2, 15, 0, tzinfo=timezone.utc)

        class Frozen(datetime):
            @classmethod
            def now(cls, tz: timezone | None = None) -> datetime:
                return fixed

        with mock.patch("memory_forest.intake.datetime", Frozen):
            first = self._cli("log", "--as", "oven-bot", "--session", "shift", stdin="the same cooling note\n")
            second = self._cli("log", "--as", "oven-bot", "--session", "shift", stdin="the same cooling note\n")
        self.assertTrue(json.loads(first[1])["wrote"])
        self.assertFalse(json.loads(second[1])["wrote"])

    def test_forest_agents_shows_the_group_the_lane_and_the_last_call(self) -> None:
        reply, _version = mcp.handle_message(
            self.root,
            "",
            _call("memory_search", {"agent": "till-bot", "words": "bakery"}),
            "stdio",
            "2025-11-25",
            group="bakery-bots",
        )
        assert isinstance(reply, dict)
        self.assertFalse(reply["result"]["isError"])
        code, out, err = self._cli("agents")
        self.assertEqual(code, 0, err)
        report = json.loads(out)
        bakery = next(group for group in report["groups"] if group["id"] == "bakery-bots")
        self.assertFalse(bakery["open"])
        till = next(member for member in bakery["members"] if member["id"] == "till-bot")
        self.assertEqual(till["name"], "Till Bot")
        self.assertTrue(till["lane"])
        self.assertTrue(till["last_seen"])
        oven = next(member for member in bakery["members"] if member["id"] == "oven-bot")
        self.assertTrue(oven["lane"])
        self.assertEqual(oven["last_seen"], "")
        plain = StringIO()
        with redirect_stdout(plain):
            self.assertEqual(forest_main(["agents", "--root", str(self.root)]), 0)
        self.assertIn("group bakery-bots (closed)", plain.getvalue())
        self.assertIn("till-bot — Till Bot — lane —", plain.getvalue())


if __name__ == "__main__":
    unittest.main()
