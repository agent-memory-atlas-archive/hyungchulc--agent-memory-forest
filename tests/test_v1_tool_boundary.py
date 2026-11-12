"""What a tool sends is what its caller can act on.

The bakery and the bots are invented. The host name and process id in these tests are this machine's,
read at run time, and are not written into the tree.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from memory_forest import mcp, search
from memory_forest.cli import forest_main

from test_v1_find import FILES, _http, _log_call
from v1_support import ForestTestCase


_HOLD = (
    "import os, sys\n"
    "from memory_forest.locking import hold_lock\n"
    "with hold_lock(sys.argv[1], sys.argv[2], wait_seconds=0, label='holder'):\n"
    "    ready = os.open(sys.argv[3], os.O_WRONLY)\n"
    "    os.write(ready, b'held\\n')\n"
    "    os.close(ready)\n"
    "    release = os.open(sys.argv[4], os.O_RDONLY)\n"
    "    os.read(release, 8)\n"
    "    os.close(release)\n"
)


def _keys(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


@contextmanager
def _held(root: Path, name: str, directory: Path) -> Iterator[tuple[subprocess.Popen[str], dict[str, object]]]:
    """A child holds ``name`` and says so on a fifo. The block reads the lock record, then lets the child go."""
    ready = directory / f"{name}-ready"
    release = directory / f"{name}-release"
    os.mkfifo(ready)
    os.mkfifo(release)
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLD, str(root), name, str(ready), str(release)],
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        descriptor = os.open(ready, os.O_RDONLY)
        os.read(descriptor, 16)
        os.close(descriptor)
        record = json.loads((root / ".forest" / "locks" / f"{name}.lock").read_text(encoding="utf-8"))
        yield proc, record
        descriptor = os.open(release, os.O_WRONLY)
        os.write(descriptor, b"go\n")
        os.close(descriptor)
        code = proc.wait()
        _out, err = proc.communicate()
        if code != 0:
            raise AssertionError(err)
    finally:
        if proc.poll() is None:
            try:
                descriptor = os.open(release, os.O_WRONLY | os.O_NONBLOCK)
            except OSError:
                proc.kill()
            else:
                os.write(descriptor, b"go\n")
                os.close(descriptor)
            proc.wait()


class ToolBoundaryTests(ForestTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_forest(dict(FILES))
        search.build(self.root)

    def test_the_rule_drops_machine_identity_and_keeps_a_relative_path(self) -> None:
        cleaned = mcp._without_machine_identity(
            {
                "note": "the note mentions /tmp/oven but is not a path",
                "owner": {"host": "counter", "label": "memory_log", "pid": 4, "user": "till"},
                "path": "/tmp/oven",
                "relative": "06 istm/till-bot/2051-03-02.jsonl",
            }
        )
        self.assertEqual(cleaned["owner"], {"label": "memory_log"})
        self.assertNotIn("path", cleaned)
        self.assertEqual(cleaned["relative"], "06 istm/till-bot/2051-03-02.jsonl")
        self.assertIn("/tmp/oven", cleaned["note"])

    def test_a_lock_held_by_a_subprocess_does_not_leave_through_a_tool(self) -> None:
        with _held(self.root, "intake", self.base) as (_proc, record):
            reply, _version = mcp.handle_message(
                self.root, "till-bot", _log_call("the till logged while the lock was held"), "stdio", "2025-11-25"
            )
        self.assertTrue(reply["result"]["isError"])
        text = reply["result"]["content"][0]["text"]
        body = json.loads(text)
        self.assertEqual(body["code"], "lock_busy")
        self.assertEqual(body["next"], "retry")
        self.assertGreater(body["after_seconds"], 0)
        self.assertIn("stop_after_seconds", body)
        self.assertNotIn("token", body["message"])
        self.assertNotIn(str(record["host"]), text)
        self.assertNotIn(str(record["pid"]), text)
        self.assertNotIn(str(self.root), text)
        self.assertTrue(set(_keys(body)).isdisjoint(mcp._MACHINE_KEYS))

    def test_the_command_line_still_shows_the_owner(self) -> None:
        with _held(self.root, "writer", self.base) as (_proc, record):
            code, out, err = _run(["recover", "--root", str(self.root)])
        self.assertEqual(code, 2, out + err)
        self.assertIn("lock_busy", err)
        self.assertIn(str(record["host"]), err)
        self.assertIn(str(record["pid"]), err)
        self.assertNotIn(str(record["token"]), err)

    def test_two_memory_log_calls_on_the_http_server_wait_their_turn(self) -> None:
        token = mcp.ensure_token(self.root).decode("ascii")
        server = mcp.serve_http(self.root, "till-bot", "127.0.0.1", 0, token=token.encode("ascii"))
        barrier = threading.Barrier(2)
        found: list[dict[str, object]] = []

        def call(text: str) -> None:
            barrier.wait()
            response = _http(
                server.server_address[1],
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "memory_log", "arguments": {"text": text}}},
                token=token,
            )
            found.append(json.loads(response.body))

        threads = [
            threading.Thread(target=call, args=("the till logged the first loaf",)),
            threading.Thread(target=call, args=("the till logged the second loaf",)),
        ]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(len(found), 2)
        lanes: list[str] = []
        for reply in found:
            result = reply["result"]
            self.assertNotIn("error", reply)
            self.assertFalse(result["isError"], result)
            body = json.loads(result["content"][0]["text"])
            self.assertTrue(body["wrote"])
            self.assertNotIn("token", result["content"][0]["text"])
            self.assertFalse(Path(str(body["path"])).is_absolute())
            lanes.append((self.root / str(body["path"])).read_text(encoding="utf-8"))
        stored = "\n".join(lanes)
        self.assertIn("first loaf", stored)
        self.assertIn("second loaf", stored)


def _run(arguments: list[str]) -> tuple[int, str, str]:
    from io import StringIO
    from contextlib import redirect_stderr, redirect_stdout

    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = forest_main(arguments)
    return code, out.getvalue(), err.getvalue()
