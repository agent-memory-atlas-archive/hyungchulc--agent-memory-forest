"""Finding: the index, search, timeline, reader scope and the MCP server.

The bakery, Ilse, and the two bots are invented. Nothing here is a real
person, shop or transcript.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from io import StringIO
from contextlib import redirect_stderr, redirect_stdout
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

오늘 유약 이 두꺼웠다.
글레이즈 가 흘렀다.
釉薬焼成は予定より早く終わった。

The door password: tulip.

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
        self.assertEqual(status["schema"], "1")
        if "trigram" in status["tokenizer"]:
            japanese = search.query(self.root, "釉薬焼", reader="till-bot")
            self.assertIn("釉薬焼", _texts(japanese))
        else:
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
            self.assertTrue(rejected["result"]["isError"])
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

    def test_a_non_loopback_bind_needs_a_mode_0600_token_and_never_prints_it(self) -> None:
        token = "shelf-token-cedar"
        with self.assertRaises(MemoryForestError) as missing:
            mcp.run(self.root, "till-bot", "0.0.0.0:8765")
        self.assertEqual(missing.exception.code, "mcp_token_missing")
        self.assertNotIn(token, missing.exception.message)
        path = self.root / ".forest" / "mcp" / "token"
        path.parent.mkdir(mode=0o700, exist_ok=True)
        path.write_text(token + "\n", encoding="utf-8")
        os.chmod(path, 0o644)
        with self.assertRaises(MemoryForestError) as loose:
            mcp.run(self.root, "till-bot", "0.0.0.0:8765")
        self.assertEqual(loose.exception.code, "mcp_token_mode")
        self.assertNotIn(token, loose.exception.message)
        error = StringIO()
        with redirect_stderr(error):
            pass
        self.assertNotIn(token, error.getvalue())
        os.chmod(path, 0o600)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        server = mcp.serve_http(self.root, "till-bot", "0.0.0.0", 0, token=mcp.read_token(self.root))
        try:
            port = server.server_address[1]
            status, body = _post(port, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            self.assertEqual(status, 401)
            self.assertNotIn(token, body.decode())
            status, body = _post(
                port,
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                token=token,
            )
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["result"]["protocolVersion"], "2025-06-18")
            self.assertNotIn(token, body.decode())
        finally:
            server.shutdown()
            server.server_close()
        loop = mcp.serve_http(self.root, "till-bot", "127.0.0.1", 0, token=None)
        try:
            status, _body = _post(
                loop.server_address[1],
                {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            )
            self.assertEqual(status, 200)
        finally:
            loop.shutdown()
            loop.server_close()


def _frame(payload: dict) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def _read_frame(stream) -> dict:
    headers = {}
    while True:
        line = stream.readline()
        if line in (b"\r\n", b"\n"):
            break
        key, value = line.split(b":", 1)
        headers[key.lower()] = value.strip()
    length = int(headers[b"content-length"])
    return json.loads(stream.read(length).decode("utf-8"))


def _rpc(proc: subprocess.Popen, payload: dict) -> dict:
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(_frame(payload))
    proc.stdin.flush()
    return _read_frame(proc.stdout)


def _post(port: int, payload: dict, token: str | None = None) -> tuple[int, bytes]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json, text/event-stream")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        exc.close()
        return exc.code, body


if __name__ == "__main__":
    unittest.main()
