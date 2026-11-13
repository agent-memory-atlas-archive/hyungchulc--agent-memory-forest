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
from unittest import mock

from memory_forest import intake, mcp, search
from memory_forest.cli import forest_main
from memory_forest.locking import hold_lock

from test_v1_find import FILES, _http, _log_call, held_in_thread
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

    def test_a_refusal_folds_the_forest_and_a_page_line_is_not_its_concern(self) -> None:
        root = self.root
        cleaned = mcp._fold_refusal(
            {
                "message": f"No forest.toml in {root}.",
                "note": "the note mentions /tmp/oven but is not a path",
                "owner": {"host": "counter", "label": "memory_log", "pid": 4, "user": "till"},
                "path": f"{root}/04 stm/bakery/ovens/floor.md",
                "real_path": f"{root}/04 stm/bakery/ovens/floor.md",
                "relative": "06 istm/till-bot/2051-03-02.jsonl",
                "root": str(root),
                "source": str(root / "forest.toml"),
                "tool": "/usr/local/bin/forest-tool",
            },
            root,
        )
        self.assertEqual(cleaned["owner"], {"label": "memory_log"})
        self.assertNotIn("real_path", cleaned)
        self.assertNotIn("root", cleaned)
        self.assertNotIn("source", cleaned)
        self.assertEqual(cleaned["path"], "04 stm/bakery/ovens/floor.md")
        self.assertEqual(cleaned["message"], "No forest.toml in .")
        self.assertEqual(cleaned["relative"], "06 istm/till-bot/2051-03-02.jsonl")
        self.assertEqual(cleaned["tool"], "/usr/local/bin/forest-tool")
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
        """The first call stands inside the hold until the second is known to be waiting on the turn."""
        token = mcp.ensure_token(self.root).decode("ascii")
        server = mcp.serve_http(self.root, "till-bot", "127.0.0.1", 0, token=token.encode("ascii"))
        entered, release = threading.Event(), threading.Event()
        second_waiting, progress = threading.Event(), threading.Event()
        found: dict[str, dict[str, object]] = {}
        real_append = intake._append
        original_turn = intake._INTAKE_TURN

        class _Turn:
            def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
                if original_turn.locked():
                    second_waiting.set()
                    progress.set()
                if timeout is None or timeout < 0:
                    return original_turn.acquire(blocking)
                return original_turn.acquire(blocking, timeout)

            def release(self) -> None:
                original_turn.release()

            def locked(self) -> bool:
                return original_turn.locked()

        def append(root: Path, relative: str, payload: str) -> None:
            if not entered.is_set():
                entered.set()
                if not release.wait(30):
                    raise AssertionError("the first memory_log was never released")
            real_append(root, relative, payload)

        def call(name: str, text: str) -> None:
            try:
                response = _http(
                    server.server_address[1],
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                     "params": {"name": "memory_log", "arguments": {"text": text}}},
                    token=token,
                )
                found[name] = json.loads(response.body)
            finally:
                if name == "second":
                    progress.set()

        intake._INTAKE_TURN = _Turn()  # type: ignore[assignment]
        first = threading.Thread(target=call, args=("first", "the till logged the first loaf"))
        second = threading.Thread(target=call, args=("second", "the till logged the second loaf"))
        try:
            with mock.patch.object(intake, "_append", append):
                first.start()
                self.assertTrue(entered.wait(30), "the first memory_log did not reach the hold")
                second.start()
                self.assertTrue(progress.wait(30), "the second memory_log neither waited nor finished")
                self.assertTrue(second_waiting.is_set(), f"the second call did not wait its turn: {found.get('second')}")
                release.set()
                first.join(30)
                second.join(30)
        finally:
            release.set()
            intake._INTAKE_TURN = original_turn
            server.shutdown()
            server.server_close()
            first.join(5)
            second.join(5)
        self.assertFalse(first.is_alive() or second.is_alive())
        lanes: list[str] = []
        for name in ("first", "second"):
            reply = found[name]
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

    def test_a_memory_log_that_does_not_end_is_lock_busy_for_the_next(self) -> None:
        token = mcp.ensure_token(self.root).decode("ascii")
        server = mcp.serve_http(self.root, "till-bot", "127.0.0.1", 0, token=token.encode("ascii"))
        entered, release = threading.Event(), threading.Event()
        finished = threading.Event()
        found: dict[str, object] = {}
        real_append = intake._append

        def append(root: Path, relative: str, payload: str) -> None:
            entered.set()
            if not release.wait(30):
                raise AssertionError("the stalled memory_log was never released")
            real_append(root, relative, payload)

        def call(name: str, text: str) -> None:
            try:
                response = _http(
                    server.server_address[1],
                    {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                     "params": {"name": "memory_log", "arguments": {"text": text}}},
                    token=token,
                )
                found[name] = json.loads(response.body)
            finally:
                if name == "second":
                    finished.set()

        first = threading.Thread(target=call, args=("first", "the till logged and did not finish"))
        second = threading.Thread(target=call, args=("second", "the till logged while the first still held"))
        try:
            with mock.patch.object(intake, "_append", append):
                first.start()
                self.assertTrue(entered.wait(30), "the first memory_log did not reach the hold")
                # The second call's wait is the production budget. This wait is only the deadline.
                second.start()
                self.assertTrue(finished.wait(10), "the second memory_log did not answer within the budget")
            reply = found["second"]
            result = reply["result"]
            self.assertTrue(result["isError"], result)
            body = json.loads(result["content"][0]["text"])
            self.assertEqual(body["code"], "lock_busy")
            self.assertEqual(body["next"], "retry")
            self.assertGreater(body["waited_seconds"], 1.0)
            self.assertLess(body["waited_seconds"], intake.LOG_WAIT_SECONDS + 1.0)
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            first.join(5)
            second.join(5)

    def test_every_tool_answer_keeps_the_forest_and_drops_the_machine(self) -> None:
        """A page line comes back whole. A refusal carries no root, home, host or user."""
        spaced = self.make_forest(dict(FILES), name="quill root")
        home = Path("/quill-shelf-home")
        host, user = "quill-host", "quill-user"
        tool_line = "/usr/local/bin/forest-tool"
        home_line = str(home)
        page = spaced / "04 stm/bakery/ovens/floor.md"
        page.write_text(
            page.read_text(encoding="utf-8").replace(
                "The oven room was swept today.\n",
                f"The oven room was swept today.\n{tool_line}\n{home_line}\n",
            ),
            encoding="utf-8",
        )
        search.build(spaced)
        lines = page.read_text(encoding="utf-8").splitlines()
        tool_number = lines.index(tool_line) + 1
        home_number = lines.index(home_line) + 1
        secrets = [str(spaced), str(spaced.parent), "quill root", home_line, host, user]
        environment = {**os.environ, "HOME": str(home), "LOGNAME": user, "USER": user}

        def clean(text: str, *, keep: tuple[str, ...] = ()) -> None:
            haystack = text
            for line in keep:
                haystack = haystack.replace(line, "")
            for secret in secrets:
                self.assertNotIn(secret, haystack, text)

        def call(name: str, arguments: dict[str, object]) -> str:
            reply, _version = mcp.handle_message(
                spaced, "till-bot",
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": name, "arguments": arguments}},
                "stdio", "2025-11-25",
            )
            self.assertIn("result", reply, reply)
            return reply["result"]["content"][0]["text"]

        success = (
            ("memory_search", {"words": "swept"}),
            ("memory_read", {"path": "04 stm/bakery/ovens/floor.md"}),
            ("memory_read", {"lines": f"{tool_number}-{tool_number}", "path": "04 stm/bakery/ovens/floor.md"}),
            ("memory_read", {"lines": f"{home_number}-{home_number}", "path": "04 stm/bakery/ovens/floor.md"}),
            ("memory_timeline", {"subject": "deck-oven"}),
            ("memory_navigate", {"path": "04 stm/bakery/ovens/floor.md"}),
            ("memory_log", {"text": "the till noted the floor was swept"}),
        )
        with mock.patch.dict(os.environ, environment, clear=False), \
                mock.patch("socket.gethostname", return_value=host), \
                mock.patch("getpass.getuser", return_value=user):
            answers = {name: [] for name, _arguments in success}
            for name, arguments in success:
                text = call(name, arguments)
                answers[name].append(text)
                clean(text, keep=(tool_line, home_line))
            self.assertIn(tool_line, answers["memory_search"][0])
            self.assertIn(home_line, answers["memory_search"][0])
            self.assertIn(tool_line, answers["memory_read"][0])
            self.assertIn(home_line, answers["memory_read"][0])
            one_line = json.loads(answers["memory_read"][1])
            self.assertEqual(one_line["results"][0]["sections"][0]["text"], tool_line)
            home_only = json.loads(answers["memory_read"][2])
            self.assertEqual(home_only["results"][0]["sections"][0]["text"], home_line)

            escaped = call("memory_read", {"path": str(spaced)})
            clean(escaped)
            self.assertEqual(json.loads(escaped)["details"]["path"], ".")
            outside = call("memory_read", {"path": "../outside"})
            self.assertIn("out_of_scope", outside)
            clean(outside)
            for name, arguments in (
                ("memory_search", {"words": "swept"}),
                ("memory_timeline", {"phrase": "yesterday"}),
                ("memory_navigate", {}),
                ("memory_log", {"text": "the till tried while the lock was held"}),
            ):
                with held_in_thread(lambda: hold_lock(spaced, "intake", wait_seconds=30, label="boundary")):
                    held_text = call(name, arguments)
                clean(held_text, keep=(tool_line, home_line))
                if name == "memory_log":
                    self.assertIn("lock_busy", held_text)
            toml = spaced / "forest.toml"
            saved = toml.read_text(encoding="utf-8")
            toml.unlink()
            try:
                for name, arguments in success:
                    if name == "memory_read" and "lines" in arguments:
                        continue
                    clean(call(name, dict(arguments)), keep=(tool_line, home_line))
            finally:
                toml.write_text(saved, encoding="utf-8")
            empty = self.base / "empty forest"
            empty.mkdir()
            code, _out, err = _run(["search", "swept", "--root", str(empty), "--as", "till-bot"])
            self.assertEqual(code, 2)
            self.assertIn(str(empty), err)
            self.assertIn("no_forest", err)

            pointer = spaced / ".forest" / "index.current"
            pointer.write_bytes(b"\xff\xfe")
            try:
                for name, arguments in (
                    ("memory_search", {"words": "swept"}),
                    ("memory_timeline", {"subject": "deck-oven"}),
                    ("memory_read", {"path": "04 stm/bakery/ovens/floor.md"}),
                    ("memory_navigate", {}),
                ):
                    clean(call(name, arguments), keep=(tool_line, home_line))
                self.assertIn("index_unreadable", call("memory_search", {"words": "swept"}))
            finally:
                search.reset(spaced)
                search.build(spaced)
            index = spaced / search.index_relative(spaced)
            index.write_bytes(b"not a database, and longer than one header of a page " * 4)
            try:
                damaged = call("memory_search", {"words": "swept"})
                self.assertIn("index_unreadable", damaged)
                clean(damaged, keep=(tool_line, home_line))
                clean(call("memory_timeline", {"subject": "deck-oven"}), keep=(tool_line, home_line))
            finally:
                search.reset(spaced)
                search.build(spaced)
            if os.geteuid() != 0:
                folder = spaced / ".forest"
                mode = folder.stat().st_mode
                os.chmod(folder, 0o555)
                try:
                    probe = folder / "write-probe"
                    writable = True
                    try:
                        probe.write_text("x", encoding="utf-8")
                    except OSError:
                        writable = False
                    else:
                        probe.unlink()
                    if not writable:
                        for name, arguments in (
                            ("memory_search", {"words": "swept"}),
                            ("memory_timeline", {"subject": "deck-oven"}),
                            ("memory_read", {"path": "04 stm/bakery/ovens/floor.md"}),
                            ("memory_navigate", {}),
                            ("memory_log", {"text": "the till noted a read-only folder"}),
                        ):
                            clean(call(name, arguments), keep=(tool_line, home_line))
                finally:
                    os.chmod(folder, mode)


def _run(arguments: list[str]) -> tuple[int, str, str]:
    from io import StringIO
    from contextlib import redirect_stderr, redirect_stdout

    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = forest_main(arguments)
    return code, out.getvalue(), err.getvalue()
