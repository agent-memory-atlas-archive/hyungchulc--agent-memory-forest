"""Finding: the index, search, timeline, reader scope and the MCP server.

The bakery, Ilse, and the two bots are invented. Nothing here is a real
person, shop or transcript.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from unittest import mock
from pathlib import Path

from memory_forest import embeddings, mcp, search, timeline
from memory_forest.cli import forest_main
from memory_forest.config import DEFAULT_CONFIG
from memory_forest.errors import MemoryForestError
from memory_forest.locking import hold_lock

from v1_support import ForestTestCase


WHEN = "\u2014"
NOW = datetime(2051, 3, 2, 12, 0, tzinfo=timezone.utc)

SETTINGS = """[forest]
timezone = "UTC"
language = "en"

[owner]
id = "ilse-varrow"
name = "Ilse"
aliases = ["Ilse Varrow"]

[agents]
orchestrators = ["front-desk"]

[embeddings]
provider = "none"
"""

SETTINGS_VECTORS = SETTINGS.replace('provider = "none"', 'provider = "openai-compatible"')

ROOT = """# XLTM

## Identity

- Name: Ilse Varrow; memory text calls her Ilse.

## Trees

- `bakery` — the bakery, its oven and its floor.
- `front-desk` — Front Desk, the orchestrator.
- `till-bot` — Till Bot, the counter.
- `oven-bot` — Oven Bot, the kiln room.

## Agents

- `front-desk` — Front Desk · orchestrator
- `till-bot` — Till Bot · the counter
- `oven-bot` — Oven Bot · the kiln room
"""


def _index(tree: str, subjects: list[str]) -> str:
    lines = "\n".join(subjects)
    return f"# {tree}\n\n## Subjects\n\n{lines}\n\n## Branches\n\n## Links\n\n## Update record\n"


DECK = f"""---
entity: deck-oven
subtopic: notes
kind: event
about: none
timezone: UTC
updated_at: 2051-03-02
---

# Deck Oven

The old oven keeps the evening glaze.

오늘 유약이 두꺼웠다.
글레이즈가 흘렀다.
釉薬焼成は予定より早く終わった。

The door password: tulip.
The supplier account is 100200300400.

## Timeline

- 2051-03-02 18:30 Pacific/Auckland {WHEN} the Auckland evening firing ended.
- 2051-03-02 06:30 Pacific/Auckland {WHEN} the Auckland morning that is UTC evening.
- 2051-03-01 18:30 UTC {WHEN} the UTC evening firing ended.
- 2051-03-01 09:00 UTC {WHEN} the morning check.

## Links

- up: [[03 mtm/bakery/ovens|ovens]]

## Update record
"""

FLOOR = """---
entity: floor-notes
subtopic: notes
kind: event
about: none
timezone: UTC
updated_at: 2051-03-02
---

# Floor

The oven room was swept today.

## Links

- up: [[03 mtm/bakery/ovens|ovens]]
"""

MILLSTONE = """---
entity: millstone-flour
subtopic: notes
kind: purchase
about: deck-oven
timezone: UTC
updated_at: 2051-03-02
---

# Millstone Flour

Millstone delivered flour for the deck oven.

## Links

- up: [[03 mtm/bakery/suppliers|suppliers]]
"""

COOLING = """---
entity: oven-bot
subtopic: methods
kind: rule
about: none
timezone: UTC
updated_at: 2051-03-02
---

# Cooling

secret cooling curve of oven bot only

## Links

- up: [[03 mtm/oven-bot/methods|methods]]
"""

DAILY = f"""# 2051-03-01

<!-- block: till-bot 2051-03-01T18:40 -->

## 18:40 till-bot — evening note

The daily page says the glaze cooled on the counter.
"""

OVEN_LANE = "\n".join(
    [
        json.dumps(
            {
                "at": "2051-03-02T11:00:00+00:00",
                "id": "oven-bot:shift:cooling",
                "origin": "interactive",
                "role": "agent",
                "session": "shift",
                "source": {"adapter": "jsonl", "place": "1", "transcript": "shift"},
                "text": "the evening glaze is still cooling in the lane",
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        json.dumps(
            {
                "at": "2051-03-02T08:00:00+00:00",
                "id": "oven-bot:shift:morning",
                "origin": "interactive",
                "role": "agent",
                "session": "shift",
                "source": {"adapter": "jsonl", "place": "2", "transcript": "shift"},
                "text": "the morning trays were counted in the lane",
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    ]
) + "\n"

TILL_LANE = json.dumps(
    {
        "at": "2051-03-02T11:30:00+00:00",
        "id": "till-bot:shift:trays",
        "origin": "interactive",
        "role": "agent",
        "session": "shift",
        "source": {"adapter": "jsonl", "place": "1", "transcript": "shift"},
        "text": "till counted the trays at the counter",
    },
    ensure_ascii=False,
    sort_keys=True,
) + "\n"

FILES = {
    "forest.toml": SETTINGS,
    "01 xltm/XLTM.md": ROOT,
    "02 ltm/bakery_LTM.md": "# bakery\n\n## Scope\n\nThe bakery.\n",
    "02 ltm/front-desk_LTM.md": "---\nentity: front-desk\n---\n\n# Front Desk\n\n## Role\n\norchestrator\n",
    "02 ltm/till-bot_LTM.md": "---\nentity: till-bot\n---\n\n# Till Bot\n\n## Role\n\nthe counter\n",
    "02 ltm/oven-bot_LTM.md": "---\nentity: oven-bot\n---\n\n# Oven Bot\n\n## Role\n\nthe kiln room\n",
    "03 mtm/bakery/bakery_MTM.md": _index(
        "bakery",
        [
            "- `deck-oven` — Deck Oven · device · leaf · `04 stm/bakery/ovens/deck-oven.md` · aliases: the old oven",
            "- `floor-notes` — Floor notes · topic · leaf · `04 stm/bakery/ovens/floor.md`",
            "- `millstone-flour` — Millstone Flour · organization · leaf · `04 stm/bakery/suppliers/millstone-flour.md`"
            " · aliases: Millstone",
        ],
    ),
    "03 mtm/bakery/ovens.md": "# ovens\n\n## Leaves\n",
    "03 mtm/bakery/suppliers.md": "# suppliers\n\n## Leaves\n",
    "03 mtm/front-desk/front-desk_MTM.md": _index(
        "front-desk",
        ["- `front-desk` — Front Desk · agent · tree · `02 ltm/front-desk_LTM.md` · aliases: the desk"],
    ),
    "03 mtm/till-bot/till-bot_MTM.md": _index(
        "till-bot",
        ["- `till-bot` — Till Bot · agent · tree · `02 ltm/till-bot_LTM.md`"],
    ),
    "03 mtm/oven-bot/oven-bot_MTM.md": _index(
        "oven-bot",
        ["- `oven-bot` — Oven Bot · agent · tree · `02 ltm/oven-bot_LTM.md`"],
    ),
    "03 mtm/oven-bot/methods.md": "# methods\n\n## Leaves\n",
    "04 stm/bakery/ovens/deck-oven.md": DECK,
    "04 stm/bakery/ovens/floor.md": FLOOR,
    "04 stm/bakery/suppliers/millstone-flour.md": MILLSTONE,
    "04 stm/oven-bot/methods/cooling.md": COOLING,
    "05 daily/2051-03-01.md": DAILY,
    "06 istm/oven-bot/2051-03-02.jsonl": OVEN_LANE,
    "06 istm/till-bot/2051-03-02.jsonl": TILL_LANE,
}


def _texts(result: dict) -> str:
    return "\n".join(section["text"] for page in result["results"] for section in page["sections"])


def _paths(result: dict) -> list[str]:
    return [page["path"] for page in result["results"]]


class IndexSearchTests(ForestTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_forest(FILES)
        self.assertEqual(search.build(self.root)["busy"], False)

    def test_alias_is_before_a_word_hit_and_brings_about_leaves(self) -> None:
        result = search.query(self.root, "the old oven was swept", reader="till-bot")
        self.assertEqual(result["results"][0]["path"], "04 stm/bakery/ovens/deck-oven.md")
        self.assertTrue(any(why.startswith("alias:") for why in result["results"][0]["why"]))
        self.assertIn("04 stm/bakery/suppliers/millstone-flour.md", _paths(result))
        self.assertLess(
            _paths(result).index("04 stm/bakery/ovens/deck-oven.md"),
            _paths(result).index("04 stm/bakery/ovens/floor.md"),
        )
        self.assertIn("bakery", result["trees"])
        self.assertNotIn("oven-bot", result["consulted"])

    def test_korean_and_japanese_text_is_findable(self) -> None:
        korean = search.query(self.root, "유약", reader="till-bot")
        self.assertIn("유약", _texts(korean))
        glaze = search.query(self.root, "글레이즈", reader="till-bot")
        self.assertIn("글레이즈", _texts(glaze))
        status = search.status(self.root)
        self.assertEqual(status["schema"], search.SCHEMA)
        japanese = search.query(self.root, "釉薬", reader="till-bot")
        self.assertIn("釉薬", _texts(japanese))
        if "trigram" not in status["tokenizer"]:
            self.assertEqual(status["tokenizer"], "unicode61")

    def test_time_phrases_in_both_languages_and_an_unfiled_turn(self) -> None:
        evening = timeline.query(self.root, "yesterday evening", reader="front-desk", now=NOW)
        body = _texts(evening)
        self.assertIn("Auckland evening firing", body)
        self.assertIn("UTC evening firing", body)
        self.assertIn("glaze cooled on the counter", body)
        self.assertNotIn("Auckland morning that is UTC evening", body)
        self.assertNotIn("morning check", body)
        korean = timeline.query(self.root, "어제 저녁", reader="front-desk", now=NOW)
        self.assertIn("Auckland evening firing", _texts(korean))
        self.assertNotIn("morning check", _texts(korean))
        recent = timeline.query(self.root, "the last 3 hours", reader="front-desk", now=NOW)
        recent_text = _texts(recent)
        self.assertIn("still cooling in the lane", recent_text)
        self.assertNotIn("morning trays", recent_text)
        self.assertIn("06 istm/oven-bot/2051-03-02.jsonl", _paths(recent))
        korean_recent = timeline.query(self.root, "지난 3시간", reader="front-desk", now=NOW)
        self.assertIn("still cooling in the lane", _texts(korean_recent))
        self.assertEqual(timeline.languages(), ["en", "ko"])

    def test_an_agent_cannot_reach_another_agents_tree_or_lane(self) -> None:
        hidden = "04 stm/oven-bot/methods/cooling.md"
        lane = "06 istm/oven-bot/2051-03-02.jsonl"
        till = search.query(self.root, "secret cooling curve", reader="till-bot")
        self.assertNotIn(hidden, _paths(till))
        self.assertNotIn("secret cooling", _texts(till))
        with self.assertRaises(MemoryForestError) as caught:
            search.read(self.root, hidden, reader="till-bot")
        self.assertEqual(caught.exception.code, "out_of_scope")
        self.assertNotIn("secret cooling", caught.exception.message)
        with self.assertRaises(MemoryForestError) as caught:
            search.read(self.root, f"{lane}#oven-bot:shift:cooling", reader="till-bot")
        self.assertEqual(caught.exception.code, "out_of_scope")
        own = search.query(self.root, "till counted the trays", reader="till-bot")
        self.assertIn("06 istm/till-bot/2051-03-02.jsonl", _paths(own))
        other = search.query(self.root, "till counted the trays", reader="oven-bot")
        self.assertNotIn("till counted", _texts(other))
        desk = search.query(self.root, "secret cooling curve", reader="front-desk")
        self.assertIn(hidden, _paths(desk))
        named = search.read(self.root, hidden, reader="till-bot", named=["oven-bot"])
        self.assertIn("secret cooling", _texts(named))
        with self.assertRaises(MemoryForestError):
            search.read(self.root, lane, reader="till-bot", named=["oven-bot"])
        walked = search.navigate(self.root, reader="till-bot")
        self.assertIn("bakery", walked["results"][0]["sections"][0]["text"])
        self.assertNotIn("oven-bot", walked["results"][0]["sections"][0]["text"])
        evening = timeline.query(self.root, "the last 3 hours", reader="till-bot", now=NOW)
        self.assertNotIn("still cooling", _texts(evening))

    def test_the_index_rebuilds_from_nothing_and_a_reader_does_not_wait(self) -> None:
        index = self.root / ".forest" / "index.sqlite"
        index.unlink()
        self.assertFalse(index.exists())
        built = search.build(self.root)
        self.assertGreater(built["units"], 0)
        self.assertIn("유약", _texts(search.query(self.root, "유약", reader="till-bot")))
        again = search.build(self.root)
        self.assertEqual(again["updated"], 0)
        floor = self.root / "04 stm" / "bakery" / "ovens" / "floor.md"
        floor.write_text(floor.read_text(encoding="utf-8") + "\nA new rye crumb was on the floor.\n", encoding="utf-8")
        self.assertIn("04 stm/bakery/ovens/floor.md", search.status(self.root)["stale"])
        search.build(self.root)
        self.assertIn("rye crumb", _texts(search.query(self.root, "rye crumb", reader="till-bot")))

        started = threading.Event()

        def hold() -> None:
            with hold_lock(self.root, "index", wait_seconds=2, label="test"):
                started.set()
                time.sleep(0.8)

        thread = threading.Thread(target=hold)
        thread.start()
        self.assertTrue(started.wait(2))
        began = time.monotonic()
        found = search.query(self.root, "유약", reader="till-bot")
        elapsed = time.monotonic() - began
        thread.join()
        self.assertLess(elapsed, 0.4)
        self.assertIn("유약", _texts(found))

    def test_read_masks_a_sign_in_value_and_the_command_prints_json(self) -> None:
        read = search.read(self.root, "04 stm/bakery/ovens/deck-oven.md", reader="till-bot")
        self.assertIn("password: tulip", _texts(read))
        buffer = StringIO()
        with redirect_stdout(buffer):
            code = forest_main(
                ["read", "04 stm/bakery/ovens/deck-oven.md", "--as", "till-bot", "--root", str(self.root), "--json"]
            )
        self.assertEqual(code, 0)
        printed = buffer.getvalue()
        self.assertIn("[REDACTED]", printed)
        self.assertNotIn("tulip", printed)
        payload = json.loads(printed)
        self.assertEqual(payload["reader"], "till-bot")

    def test_vectors_use_a_registered_provider_and_none_makes_no_call(self) -> None:
        class Fake:
            name = "openai-compatible"

            def __init__(self) -> None:
                self.calls = 0

            def embed(self, texts: list[str]) -> list[list[float]]:
                self.calls += 1
                return [self._one(text) for text in texts]

            def _one(self, text: str) -> list[float]:
                vector = [0.0] * 4
                folded = text.casefold()
                vector[0 if "hearth" in folded or "deck oven" in folded else 1] = 1.0
                return vector

        fake = Fake()
        embeddings.register(fake)
        self.addCleanup(lambda: embeddings.unregister(fake.name))
        plain = search.query(self.root, "hearth", reader="till-bot")
        self.assertEqual(fake.calls, 0)
        self.assertEqual(plain["vectors"], "off")
        self.assertEqual(plain["results"], [])
        vector_root = self.make_forest({**FILES, "forest.toml": SETTINGS_VECTORS}, name="vectors")
        search.build(vector_root)
        self.assertGreater(fake.calls, 0)
        found = search.query(vector_root, "hearth", reader="till-bot")
        self.assertEqual(found["vectors"], "used")
        self.assertIn("04 stm/bakery/ovens/deck-oven.md", _paths(found))
        self.assertTrue(any(why.startswith("vector:") for why in found["results"][0]["why"]))

    def test_a_collection_entry_is_one_address(self) -> None:
        text = (
            "---\nentity: regulars-seen-once\n---\n\n# Regulars\n\n"
            "## Tamsin Orrow\n\n- entry: `tamsin-orrow` · person · aliases: Tam\n\nAsked for rye.\n\n"
            "## Timeline\n\n- 2051-03-02 09:10 UTC — Tamsin Orrow: asked for rye.\n"
        )
        units = search.units_of(
            "04 stm/people/regulars/seen-once-2051-q1.md", text, DEFAULT_CONFIG
        )
        entry = next(unit for unit in units if unit["address"].endswith("#tamsin-orrow"))
        self.assertEqual(entry["kind"], "entry")
        self.assertIn("tamsin-orrow", entry["subjects"])
        self.assertTrue(any(start.hour == 9 for start, _end, _zone in entry["times"]))


class McpTests(ForestTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_forest(FILES)
        search.build(self.root)

    def test_stdio_round_trip_and_memory_log_is_then_found(self) -> None:
        self.assertLessEqual(len(mcp.INSTRUCTIONS), 2048)
        self.assertTrue(all(tool["description"] for tool in mcp._TOOLS))
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from memory_forest.cli import forest_main; raise SystemExit(forest_main())",
                "mcp",
                "--as",
                "till-bot",
                "--root",
                str(self.root),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.stdin is not None and proc.stdout is not None
        try:
            init = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"},
                    },
                },
            )
            self.assertEqual(init["result"]["protocolVersion"], "2025-11-25")
            self.assertLessEqual(len(init["result"]["instructions"]), 2048)
            listed = _rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            names = [tool["name"] for tool in listed["result"]["tools"]]
            self.assertEqual(
                names,
                ["memory_search", "memory_read", "memory_timeline", "memory_navigate", "memory_log"],
            )
            rejected = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "memory_log", "arguments": {"text": "should not land", "role": "owner"}},
                },
            )
            self.assertEqual(rejected["error"]["code"], -32602)
            lane = (self.root / "06 istm" / "till-bot" / "2051-03-02.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("should not land", lane)
            logged = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "memory_log",
                        "arguments": {"text": "the till logged a rye count from the session"},
                    },
                },
            )
            self.assertFalse(logged["result"]["isError"])
            body = json.loads(logged["result"]["content"][0]["text"])
            self.assertTrue(body["wrote"])
            self.assertTrue(body["indexed"])
            found = search.query(self.root, "rye count from the session", reader="till-bot")
            self.assertIn("rye count from the session", _texts(found))
            hidden = search.query(self.root, "rye count from the session", reader="oven-bot")
            self.assertNotIn("rye count", _texts(hidden))
            seen = json.loads((self.root / ".forest" / "mcp" / "seen.json").read_text(encoding="utf-8"))
            self.assertEqual(seen["till-bot"]["tool"], "memory_log")
            self.assertEqual(seen["till-bot"]["transport"], "stdio")
            older = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
                },
            )
            self.assertEqual(older["result"]["protocolVersion"], "2024-11-05")
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait(timeout=5)
            proc.stdout.close()
            proc.stderr.close()

    def test_every_bind_needs_a_mode_0600_token_and_never_prints_it(self) -> None:
        token = "shelf-token-cedar"
        path = self.root / ".forest" / "mcp" / "token"
        path.parent.mkdir(mode=0o700, exist_ok=True)
        path.write_text(token + "\n", encoding="utf-8")
        os.chmod(path, 0o644)
        with self.assertRaises(MemoryForestError) as loose:
            mcp.run(self.root, "till-bot", "0.0.0.0:8765")
        self.assertEqual(loose.exception.code, "mcp_token_mode")
        self.assertNotIn(token, loose.exception.message)
        os.chmod(path, 0o600)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        error = StringIO()
        with redirect_stderr(error):
            server = mcp.serve_http(self.root, "till-bot", "0.0.0.0", 0, token=mcp.read_token(self.root))
        self.assertNotIn(token, error.getvalue())
        try:
            port = server.server_address[1]
            status, body = _post(port, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            self.assertEqual(status, 401)
            self.assertNotIn(token, body.decode())
            status, body, headers = _post(
                port,
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                token=token,
                header=True,
            )
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["result"]["protocolVersion"], "2025-06-18")
            self.assertEqual(headers.get("MCP-Protocol-Version"), "2025-06-18")
            self.assertNotIn(token, body.decode())
        finally:
            server.shutdown()
            server.server_close()
        path.unlink()
        loop_error = StringIO()
        with redirect_stderr(loop_error):
            loop = mcp.serve_http(self.root, "till-bot", "127.0.0.1", 0, token=None)
        created = (self.root / ".forest" / "mcp" / "token").read_bytes().strip()
        self.assertEqual(stat.S_IMODE((self.root / ".forest" / "mcp" / "token").stat().st_mode), 0o600)
        self.assertNotIn(created.decode("ascii"), loop_error.getvalue())
        try:
            status, _body = _post(loop.server_address[1], {"jsonrpc": "2.0", "id": 1, "method": "ping"})
            self.assertEqual(status, 401)
            status, _body = _post(
                loop.server_address[1],
                {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                token=created.decode("ascii"),
            )
            self.assertEqual(status, 200)
        finally:
            loop.shutdown()
            loop.server_close()


def _frame(payload: dict | list) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


def _read_frame(stream) -> dict | list:
    line = stream.readline()
    if not line:
        raise AssertionError("the server closed stdout without a message")
    self_check = line.decode("utf-8")
    if self_check.startswith("Content-Length:"):
        raise AssertionError("stdio is still framed with Content-Length")
    return json.loads(self_check)


def _rpc(proc: subprocess.Popen, payload: dict | list) -> dict | list:
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(_frame(payload))
    proc.stdin.flush()
    return _read_frame(proc.stdout)


def _post(
    port: int,
    payload: dict | list,
    token: str | None = None,
    *,
    header: bool = False,
    origin: str | None = None,
    host: str | None = None,
    content_type: str | None = "application/json",
    protocol: str | None = None,
) -> tuple[int, bytes] | tuple[int, bytes, dict[str, str]]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", data=data, method="POST")
    if content_type is not None:
        request.add_header("Content-Type", content_type)
    request.add_header("Accept", "application/json, text/event-stream")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    if origin:
        request.add_header("Origin", origin)
    if host:
        request.add_header("Host", host)
    if protocol:
        request.add_header("MCP-Protocol-Version", protocol)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            status, body, headers = response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read()
        headers = dict(exc.headers)
        status = exc.code
        exc.close()
    if header:
        return status, body, headers
    return status, body


class ReviewPathTests(ForestTestCase):
    """The review's missing paths, through the CLI or the real MCP loop."""

    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_forest(FILES)
        search.build(self.root)

    def test_stdio_newline_framing_for_every_claimed_version(self) -> None:
        for version in mcp.PROTOCOL_VERSIONS:
            with self.subTest(version=version):
                with _stdio(self.root) as proc:
                    init = _rpc(proc, _initialize(version))
                    self.assertEqual(init["result"]["protocolVersion"], version)
                    found = _rpc(
                        proc,
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/call",
                            "params": {"name": "memory_search", "arguments": {"words": "유약"}},
                        },
                    )
                    self.assertFalse(found["result"]["isError"])
                    self.assertIn("유약", found["result"]["content"][0]["text"])

    def test_malformed_json_unknown_method_notification_and_batch_on_stdio(self) -> None:
        with _stdio(self.root) as proc:
            _rpc(proc, _initialize("2025-03-26"))
            proc.stdin.write(b"this is not json\n")
            proc.stdin.flush()
            broken = _read_frame(proc.stdout)
            self.assertEqual(broken["error"]["code"], -32700)
            unknown = _rpc(proc, {"jsonrpc": "2.0", "id": 7, "method": "no/such"})
            self.assertEqual(unknown["error"]["code"], -32601)
            tool = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "tools/call",
                    "params": {"name": "memory_missing", "arguments": {}},
                },
            )
            self.assertEqual(tool["error"]["code"], -32602)
            proc.stdin.write(_frame({"jsonrpc": "2.0", "method": "notifications/initialized"}))
            proc.stdin.write(_frame({"jsonrpc": "2.0", "id": 1, "result": {"ignored": True}}))
            proc.stdin.write(_frame({"jsonrpc": "2.0", "id": 9, "method": "ping"}))
            proc.stdin.flush()
            ping = _read_frame(proc.stdout)
            self.assertEqual(ping["id"], 9)
            self.assertEqual(ping["result"], {})
            batch = _rpc(
                proc,
                [
                    {"jsonrpc": "2.0", "id": "a", "method": "ping"},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                ],
            )
            self.assertIsInstance(batch, list)
            self.assertEqual([item["id"] for item in batch], ["a"])
            _rpc(proc, _initialize("2025-11-25"))
            refused = _rpc(proc, [{"jsonrpc": "2.0", "id": "b", "method": "ping"}])
            self.assertEqual(refused["error"]["code"], -32600)
            self.assertNotIsInstance(refused, list)
            still = _rpc(proc, {"jsonrpc": "2.0", "id": 11, "method": "ping"})
            self.assertEqual(still["id"], 11)
        with _stdio(self.root) as proc:
            _rpc(proc, _initialize("2024-11-05"))
            older = _rpc(proc, [{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
            self.assertEqual(older["error"]["code"], -32600)
            _rpc(proc, _initialize("2025-06-18"))
            removed = _rpc(proc, [{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
            self.assertEqual(removed["error"]["code"], -32600)

    def test_http_refuses_a_page_and_speaks_the_negotiated_version(self) -> None:
        token = mcp.ensure_token(self.root).decode("ascii")
        server = mcp.serve_http(self.root, "till-bot", "127.0.0.1", 0, token=token.encode("ascii"), read_timeout=0.4)
        port = server.server_address[1]
        lane = self.root / "06 istm" / "till-bot" / "2051-03-02.jsonl"
        before = lane.read_text(encoding="utf-8")
        try:
            status, body = _post(port, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
            self.assertEqual(status, 401)
            status, body = _post(
                port,
                _log_call("a cross-site write must not land"),
                token=token,
                origin="http://evil.example",
            )
            self.assertEqual(status, 403)
            self.assertNotIn("cross-site", body.decode())
            self.assertEqual(lane.read_text(encoding="utf-8"), before)
            refused = _http(port, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token=token, host=f"evil.example:{port}")
            self.assertEqual(refused.status, 403)
            status, body = _post(
                port,
                {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                token=token,
                content_type="text/plain",
            )
            self.assertEqual(status, 415)
            status, body = _post(port, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token=token + "é")
            self.assertEqual(status, 401)
            status, body, headers = _post(
                port,
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                token=token,
                header=True,
            )
            self.assertEqual(status, 200)
            self.assertEqual(headers.get("MCP-Protocol-Version"), "2025-06-18")
            status, body, headers = _post(
                port,
                {"jsonrpc": "2.0", "id": 2, "method": "ping"},
                token=token,
                protocol="2025-06-18",
                header=True,
            )
            self.assertEqual(headers.get("MCP-Protocol-Version"), "2025-06-18")
            status, _body, headers = _post(
                port,
                {"jsonrpc": "2.0", "id": 3, "method": "ping"},
                token=token,
                header=True,
            )
            self.assertEqual(headers.get("MCP-Protocol-Version"), "2025-03-26")
            status, body = _post(port, {"jsonrpc": "2.0", "id": 4, "method": "no/such"}, token=token, protocol="2025-11-25")
            self.assertEqual(json.loads(body)["error"]["code"], -32601)
            status, body = _post(
                port,
                {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "memory_missing", "arguments": {}}},
                token=token,
                protocol="2025-11-25",
            )
            self.assertEqual(json.loads(body)["error"]["code"], -32602)
            status, body = _post(
                port,
                [{"jsonrpc": "2.0", "id": 6, "method": "ping"}],
                token=token,
                protocol="2025-11-25",
            )
            self.assertEqual(status, 400)
            self.assertNotIsInstance(json.loads(body), list)
            status, body = _post(
                port,
                [
                    {"jsonrpc": "2.0", "id": 7, "method": "ping"},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                ],
                token=token,
                protocol="2025-03-26",
            )
            self.assertEqual(status, 200)
            parsed = json.loads(body)
            self.assertIsInstance(parsed, list)
            self.assertEqual(parsed[0]["id"], 7)
            status, body = _post(
                port,
                {"jsonrpc": "2.0", "id": 8, "result": {}},
                token=token,
                protocol="2025-11-25",
            )
            self.assertEqual(status, 202)
            self.assertEqual(body, b"")
            sock = socket.create_connection(("127.0.0.1", port), timeout=2)
            time.sleep(0.7)
            sock.close()
            status, _body = _post(port, {"jsonrpc": "2.0", "id": 9, "method": "ping"}, token=token)
            self.assertEqual(status, 200)
            probe = socket.create_connection(("127.0.0.1", port), timeout=2)
            probe.sendall(
                (
                    f"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                    f"Authorization: Bearer {token}\r\nContent-Type: application/json\r\n"
                    f"Content-Length: {mcp.MESSAGE_LIMIT + 1}\r\nConnection: close\r\n\r\n"
                ).encode("ascii")
            )
            probe.settimeout(2)
            oversized = probe.recv(4096)
            probe.close()
            self.assertIn(b"413", oversized)
            status, _body = _post(port, {"jsonrpc": "2.0", "id": 10, "method": "ping"}, token=token)
            self.assertEqual(status, 200)
        finally:
            server.shutdown()
            server.server_close()

    def test_a_non_ascii_token_matches_as_bytes(self) -> None:
        path = self.root / ".forest" / "mcp" / "token"
        path.parent.mkdir(mode=0o700, exist_ok=True)
        raw = "caf\u00e9".encode("utf-8")
        path.write_bytes(raw + b"\n")
        os.chmod(path, 0o600)
        server = mcp.serve_http(self.root, "till-bot", "127.0.0.1", 0, token=raw)
        try:
            status, _body = _post(server.server_address[1], {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token="caf\u00e9")
            self.assertEqual(status, 401)
            status, _body = _post(
                server.server_address[1],
                {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                token=raw.decode("latin-1"),
            )
            self.assertEqual(status, 200)
        finally:
            server.shutdown()
            server.server_close()

    def test_a_bad_date_and_an_undecodable_page_do_not_stop_the_tools(self) -> None:
        page = "04 stm/bakery/ovens/floor.md"
        target = self.root / "04 stm" / "bakery" / "ovens" / "floor.md"
        target.write_bytes("the glaze \xff stayed in the bowl\n".encode("latin-1"))
        built = search.build(self.root)
        self.assertFalse(built["busy"])
        status = search.status(self.root)
        self.assertIn(page, status["undecodable"])
        self.assertIn("04 stm/bakery/ovens/deck-oven.md", _paths(search.query(self.root, "유약", reader="till-bot")))
        error = StringIO()
        out = StringIO()
        with redirect_stdout(out), redirect_stderr(error):
            code = forest_main(["timeline", "query", "2051-02-30", "--as", "front-desk", "--root", str(self.root), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("time_phrase_unknown", out.getvalue())
        self.assertNotIn("Traceback", error.getvalue() + out.getvalue())
        with redirect_stdout(out), redirect_stderr(error):
            code = forest_main(
                ["timeline", "query", "2051-03-01T25:00", "--as", "front-desk", "--root", str(self.root), "--json"]
            )
        self.assertEqual(code, 2)
        self.assertIn("time_phrase_unknown", out.getvalue())
        with _stdio(self.root) as proc:
            _rpc(proc, _initialize("2025-11-25"))
            for phrase in ("2051-02-30", "2051-03-01T25:00"):
                reply = _rpc(
                    proc,
                    {
                        "jsonrpc": "2.0",
                        "id": phrase,
                        "method": "tools/call",
                        "params": {"name": "memory_timeline", "arguments": {"phrase": phrase}},
                    },
                )
                self.assertTrue(reply["result"]["isError"])
                self.assertIn("does not exist", reply["result"]["content"][0]["text"])
                self.assertNotIn("Traceback", reply["result"]["content"][0]["text"])
            calls = [
                ("memory_search", {"words": "유약"}),
                ("memory_read", {"path": page}),
                ("memory_navigate", {"path": "bakery"}),
                ("memory_log", {"text": "the till noted the bowl after a bad page"}),
            ]
            for name, arguments in calls:
                reply = _rpc(
                    proc,
                    {"jsonrpc": "2.0", "id": name, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
                )
                self.assertIn("result", reply)
                self.assertNotIn("Traceback", json.dumps(reply))
            listed = _rpc(proc, {"jsonrpc": "2.0", "id": "list", "method": "tools/list"})
            self.assertEqual(len(listed["result"]["tools"]), 5)
        read = _cli(self, ["read", page, "--as", "till-bot", "--json"])
        self.assertEqual(read[0], 0)
        self.assertIn("undecodable", read[1])
        self.assertNotIn("Traceback", read[2])

    def test_a_torn_last_line_does_not_hide_the_turns_before_it(self) -> None:
        lane = self.root / "06 istm" / "till-bot" / "2051-03-02.jsonl"
        lane.write_text(lane.read_text(encoding="utf-8") + '{"at":"2051-03-02T12:00:00+00:00","text":\n', encoding="utf-8")
        built = search.build(self.root)
        self.assertFalse(built["busy"])
        self.assertIn("06 istm/till-bot/2051-03-02.jsonl", search.status(self.root)["torn"])
        found = _cli(self, ["search", "till counted the trays", "--as", "till-bot", "--json"])
        self.assertEqual(found[0], 0)
        self.assertIn("till counted the trays", found[1])

    def test_a_query_after_intake_needs_no_manual_build(self) -> None:
        transcript = self.root.parent / "counter-session.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "at": "2051-03-02T16:00:00+00:00",
                    "session": "counter",
                    "role": "agent",
                    "text": "the till noted a barley delivery after intake",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        taken = _cli(
            self,
            [
                "intake",
                "--from",
                "jsonl",
                "--as",
                "till-bot",
                "--transcript",
                str(transcript),
                "--once",
                "--json",
            ],
        )
        self.assertEqual(taken[0], 0, taken[2])
        found = _cli(self, ["search", "barley delivery after intake", "--as", "till-bot", "--json"])
        self.assertEqual(found[0], 0, found[2])
        self.assertIn("barley delivery after intake", found[1])
        index = self.root / ".forest" / "index.sqlite"
        index.unlink()
        again = _cli(self, ["search", "barley delivery after intake", "--as", "till-bot", "--json"])
        self.assertEqual(again[0], 0, again[2])
        self.assertIn("barley delivery after intake", again[1])

    def test_a_second_build_waits_for_nobody_and_a_half_written_file_is_not_what_a_reader_sees(self) -> None:
        started = threading.Event()

        def hold() -> None:
            with hold_lock(self.root, "index", wait_seconds=2, label="test"):
                started.set()
                time.sleep(0.6)

        thread = threading.Thread(target=hold)
        thread.start()
        self.assertTrue(started.wait(2))
        # A second process, so the lock this process holds is "already running" rather than re-entry.
        busy = subprocess.run(
            [
                sys.executable,
                "-c",
                "from memory_forest.cli import forest_main; raise SystemExit(forest_main())",
                "index",
                "build",
                "--root",
                str(self.root),
            ],
            capture_output=True,
            text=True,
        )
        thread.join()
        self.assertEqual(busy.returncode, 0, busy.stderr)
        self.assertIn("already running", busy.stdout)
        seen = _cli(self, ["search", "유약", "--as", "till-bot", "--json"])
        self.assertIn("유약", seen[1])
        staging = self.root / ".forest" / "index.sqlite.building"
        staging.write_bytes(b"not a database")
        still = _cli(self, ["search", "유약", "--as", "till-bot", "--json"])
        self.assertEqual(still[0], 0, still[2])
        self.assertIn("유약", still[1])
        rebuilt = _cli(self, ["index", "build"])
        self.assertEqual(rebuilt[0], 0, rebuilt[2])
        self.assertFalse(staging.exists())

    def test_search_is_not_an_oracle_for_a_masked_value_or_a_starred_number(self) -> None:
        secret = _cli(self, ["search", "tulip", "--as", "till-bot", "--json"])
        self.assertEqual(secret[0], 0, secret[2])
        self.assertEqual(json.loads(secret[1])["results"], [])
        hidden = _cli(self, ["search", "20030", "--as", "till-bot", "--json"])
        self.assertEqual(json.loads(hidden[1])["results"], [])
        whole = _cli(self, ["search", "100200300400", "--as", "till-bot", "--json"])
        self.assertEqual(json.loads(whole[1])["results"], [])
        shown = _cli(self, ["read", "04 stm/bakery/ovens/deck-oven.md", "--as", "till-bot", "--json"])
        self.assertIn("[REDACTED]", shown[1])
        self.assertNotIn("tulip", shown[1])
        self.assertNotIn("100200300400", shown[1])
        self.assertIn("100", shown[1])

    def test_short_cjk_inside_an_unspaced_word_and_subject_order(self) -> None:
        one = _cli(self, ["search", "약", "--as", "till-bot", "--json"])
        self.assertIn("유약", one[1])
        stem = _cli(self, ["search", "釉", "--as", "till-bot", "--json"])
        self.assertIn("釉薬", stem[1])
        when = "\u2014"
        early = self.root / "04 stm" / "bakery" / "ovens" / "zeta-clock.md"
        mid = self.root / "04 stm" / "bakery" / "ovens" / "mid-clock.md"
        early.write_text(
            "---\nentity: clock-subject\ntimezone: UTC\n---\n\n# Zeta\n\n"
            f"## Timeline\n\n- 2051-03-03 10:00 UTC {when} the later line is written first.\n\n"
            f"## Earlier\n\n- 2051-03-01 10:00 UTC {when} the earlier line is written second.\n",
            encoding="utf-8",
        )
        mid.write_text(
            "---\nentity: clock-subject\ntimezone: UTC\n---\n\n# Mid\n\n"
            f"## Timeline\n\n- 2051-03-02 10:00 UTC {when} the middle line.\n",
            encoding="utf-8",
        )
        self.assertEqual(_cli(self, ["index", "build"])[0], 0)
        listed = _cli(self, ["timeline", "subject", "clock-subject", "--as", "till-bot", "--json"])
        self.assertEqual(listed[0], 0, listed[2])
        paths = [page["path"] for page in json.loads(listed[1])["results"]]
        self.assertLess(
            paths.index("04 stm/bakery/ovens/zeta-clock.md"),
            paths.index("04 stm/bakery/ovens/mid-clock.md"),
        )

    def test_read_lines_address_navigate_and_scope_use_the_real_path(self) -> None:
        lines = _cli(self, ["read", "04 stm/bakery/ovens/deck-oven.md", "--lines", "1-2", "--as", "till-bot", "--json"])
        self.assertEqual(lines[0], 0, lines[2])
        section = json.loads(lines[1])["results"][0]["sections"][0]
        self.assertEqual(section["lines"], [1, 2])
        addressed = _cli(
            self,
            ["read", "04 stm/bakery/ovens/deck-oven.md#timeline", "--as", "till-bot", "--json"],
        )
        self.assertEqual(addressed[0], 0, addressed[2])
        body = json.loads(addressed[1])["results"][0]["sections"][0]["text"]
        self.assertIn("Auckland evening firing", body)
        self.assertNotIn("door password", body)
        hidden = "04 stm/oven-bot/methods/cooling.md"
        refused = _cli(self, ["read", hidden, "--as", "till-bot", "--json"])
        self.assertEqual(refused[0], 2)
        self.assertIn("out_of_scope", refused[1])
        self.assertNotIn("secret cooling", refused[1] + refused[2])
        missed = _cli(self, ["search", "secret cooling curve", "--as", "till-bot", "--json"])
        self.assertEqual(json.loads(missed[1])["results"], [])
        with _stdio(self.root) as proc:
            _rpc(proc, _initialize("2025-11-25"))
            reply = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "memory_read", "arguments": {"path": hidden}},
                },
            )
            self.assertTrue(reply["result"]["isError"])
            self.assertNotIn("secret cooling", reply["result"]["content"][0]["text"])
            walked = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "memory_navigate", "arguments": {"path": "bakery"}},
                },
            )
            text = walked["result"]["content"][0]["text"]
            self.assertIn("bakery", text)
            self.assertNotIn("secret cooling", text)
            below = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "memory_navigate", "arguments": {"path": "03 mtm/bakery/bakery_MTM.md"}},
                },
            )
            self.assertIn("deck-oven", below["result"]["content"][0]["text"])
            self.assertFalse(below["result"]["isError"])

    def test_a_line_uses_the_page_zone_and_a_forest_can_add_a_phrase_table(self) -> None:
        floor = self.root / "04 stm" / "bakery" / "ovens" / "floor.md"
        text = floor.read_text(encoding="utf-8").replace("timezone: UTC", "timezone: Pacific/Auckland")
        floor.write_text(
            text + f"\n## Timeline\n\n- 2051-03-02 18:30 {WHEN} the floor's own evening.\n",
            encoding="utf-8",
        )
        self.assertEqual(_cli(self, ["index", "build"])[0], 0)
        evening = timeline.query(self.root, "yesterday evening", reader="front-desk", now=NOW)
        self.assertIn("floor's own evening", _texts(evening))
        phrases = self.root / ".forest" / "phrases"
        phrases.mkdir()
        (phrases / "qa.json").write_text(
            json.dumps(
                {
                    "language": "qa",
                    "fold": True,
                    "dayparts": {"vespero": [17, 22]},
                    "units": {"horo": 3600},
                    "numbers": {"tri": 3},
                    "phrases": [{"pattern": "hierau {daypart}", "use": "day", "day_shift": -1}],
                }
            ),
            encoding="utf-8",
        )
        self.assertIn("qa", timeline.languages(self.root))
        added = _cli(self, ["timeline", "query", "hierau vespero", "--as", "front-desk", "--json"])
        self.assertEqual(added[0], 0, added[1] + added[2])

    def test_an_unchanged_size_and_time_is_hashed_when_verify_is_due(self) -> None:
        floor = self.root / "04 stm" / "bakery" / "ovens" / "floor.md"
        original = floor.stat()

        def rewrite(old: str, new: str) -> None:
            floor.write_text(floor.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")
            os.utime(floor, ns=(original.st_atime_ns, original.st_mtime_ns))

        rewrite("today", "night")
        skipped = _cli(self, ["index", "build"])
        self.assertEqual(skipped[0], 0, skipped[2])
        self.assertEqual(json.loads(_cli(self, ["search", "night", "--as", "till-bot", "--json"])[1])["results"], [])
        checked = _cli(self, ["index", "build", "--verify"])
        self.assertEqual(checked[0], 0, checked[2])
        self.assertIn("night", _cli(self, ["search", "night", "--as", "till-bot", "--json"])[1])
        rewrite("night", "dusky")
        database = sqlite3.connect(self.root / ".forest" / "index.sqlite")
        database.execute("UPDATE files SET verified_at = '2000-01-01T00:00:00Z'")
        database.commit()
        database.close()
        due = _cli(self, ["index", "build"])
        self.assertEqual(due[0], 0, due[2])
        self.assertIn("dusky", _cli(self, ["search", "dusky", "--as", "till-bot", "--json"])[1])

    def test_memory_log_does_not_copy_the_index_and_says_when_it_is_not_indexed(self) -> None:
        index = self.root / ".forest" / "index.sqlite"
        inode = index.stat().st_ino
        parsed = {"n": 0}
        original = search._turn_unit

        def counted(*args, **kwargs):
            parsed["n"] += 1
            return original(*args, **kwargs)

        lanes = []
        with mock.patch.object(search, "_turn_unit", counted):
            for number, text in enumerate(("the till logged a first crate", "the till logged a second crate"), 1):
                parsed["n"] = 0
                reply, _version = mcp.handle_message(
                    self.root, "till-bot", _log_call(text), "stdio", "2025-11-25"
                )
                body = json.loads(reply["result"]["content"][0]["text"])
                self.assertTrue(body["wrote"])
                self.assertTrue(body["indexed"])
                lanes.append(body["path"])
                # One turn was written, so one line was parsed: the lane was not read again whole.
                self.assertEqual(parsed["n"], 1, number)
                # The update wrote into the published file; no copy was swapped in.
                self.assertEqual(index.stat().st_ino, inode)
        self.assertEqual(lanes[0], lanes[1])
        self.assertFalse((self.root / ".forest" / "index.sqlite.building").exists())
        self.assertIn("second crate", _cli(self, ["search", "second crate", "--as", "till-bot", "--json"])[1])
        with _stdio(self.root) as proc:
            _rpc(proc, _initialize("2025-11-25"))
            started = threading.Event()

            def hold() -> None:
                with hold_lock(self.root, "index", wait_seconds=2, label="test"):
                    started.set()
                    time.sleep(0.8)

            thread = threading.Thread(target=hold)
            thread.start()
            self.assertTrue(started.wait(2))
            logged = _rpc(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "memory_log", "arguments": {"text": "the till logged a held index"}},
                },
            )
            thread.join()
            body = json.loads(logged["result"]["content"][0]["text"])
            self.assertTrue(body["wrote"])
            self.assertFalse(body["indexed"])
        found = _cli(self, ["search", "logged a held index", "--as", "till-bot", "--json"])
        self.assertIn("logged a held index", found[1])


def _initialize(version: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
    }


def _log_call(text: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "memory_log", "arguments": {"text": text}},
    }


def _cli(case: ForestTestCase, arguments: list[str]) -> tuple[int, str, str]:
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = forest_main([*arguments, "--root", str(case.root)])
    return code, out.getvalue(), err.getvalue()


class _Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body


def _http(
    port: int,
    payload: dict | list | None,
    *,
    token: str,
    host: str | None = None,
    content_type: str = "application/json",
    raw: bytes | None = None,
) -> _Response:
    body = raw if raw is not None else json.dumps(payload).encode("utf-8")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": content_type, "Authorization": "Bearer " + token}
    if host:
        headers["Host"] = host
    connection.request("POST", "/mcp", body=body, headers=headers)
    response = connection.getresponse()
    data = response.read()
    status = response.status
    connection.close()
    return _Response(status, data)


@contextmanager
def _stdio(root, agent: str = "till-bot"):
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from memory_forest.cli import forest_main; raise SystemExit(forest_main())",
            "mcp",
            "--as",
            agent,
            "--root",
            str(root),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        yield proc
    finally:
        if proc.stdin:
            proc.stdin.close()
        proc.kill()
        proc.wait(timeout=5)
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
