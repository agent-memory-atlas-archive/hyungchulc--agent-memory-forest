"""Who reads decides what is folded.

An answer an agent reads carries no machine identity. The MCP tools fold, and so do the command-line
doors an agent uses (``--as``, ``--as-group … --agent …``), refusals included, through the same
function. Without those flags the terminal output is the owner's and keeps its paths.

The bakery, the bots, the host and the user names are invented.
"""

from __future__ import annotations

import bounds  # every invocation has a deadline

import json
import os
import shutil
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Iterator
from unittest import mock

from memory_forest import mcp, search
from memory_forest.cli import forest_main
from memory_forest.locking import hold_lock

from test_v1_find import FILES, held_in_thread
from test_v1_groups import BAKERY_GROUPS, _stdin
from v1_support import ForestTestCase


PAGE = "04 stm/bakery/ovens/floor.md"
TOOL_LINE = "/usr/local/bin/forest-tool"


class AgentDoorFoldTests(ForestTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.home = self.base / "home dir"
        self.home.mkdir()
        docs = self.home / "Jänë Docs"
        docs.mkdir()
        self.root = docs / "forest"
        shutil.copytree(self.make_forest({**FILES, "forest.toml": BAKERY_GROUPS}), self.root)
        page = self.root / PAGE
        self.home_line = str(self.home / "notes" / "oven.md")
        page.write_text(
            page.read_text(encoding="utf-8") + f"\n{TOOL_LINE}\n{self.home_line}\n", encoding="utf-8"
        )
        search.build(self.root)
        self.host, self.user = "quill-host", "quill-user"
        self.secrets = [str(self.root), str(self.root.parent), str(self.home), "Jänë", self.host, self.user]

    def _run(self, arguments: list[str], stdin: str = "") -> tuple[int, str, str]:
        out, err = StringIO(), StringIO()
        environment = {**os.environ, "HOME": str(self.home), "LOGNAME": self.user, "USER": self.user}
        with mock.patch.dict(os.environ, environment, clear=False), \
                mock.patch("socket.gethostname", return_value=self.host), \
                mock.patch("getpass.getuser", return_value=self.user), \
                redirect_stdout(out), redirect_stderr(err), _stdin(stdin):
            code = forest_main(arguments)
        return code, out.getvalue(), err.getvalue()

    def _clean(self, text: str) -> None:
        for secret in self.secrets:
            self.assertNotIn(secret, text, text)

    def _doors(self) -> Iterator[tuple[str, list[str]]]:
        """Each door, as one agent and as a member of a group, as JSON and as text."""
        for who in (["--as", "till-bot"], ["--as-group", "bakery-bots", "--agent", "till-bot"]):
            for shape in (["--json"], []):
                yield "as", [*who, "--root", str(self.root), *shape]

    def test_a_refusal_through_an_agent_door_is_folded_like_a_tool(self) -> None:
        refusals = [
            (["read", str(self.home / "notes.md")], "path_escape", "~/notes.md"),
            (["read", str(self.root / PAGE)], "path_escape", PAGE),
            (["read", "../outside.md"], "path_escape", "../outside.md"),
            (["read", "04 stm/bakery/ovens/none.md"], "path_not_found", None),
            (["navigate", str(self.home)], "path_escape", "~"),
            (["timeline", "query", "no such phrase at all"], "time_phrase_unknown", None),
            (["search", "  "], "search_empty", None),
            (["log", "an argument is not the turn"], "log_text_from_stdin", None),
        ]
        for _name, door in self._doors():
            for command, refused, expected in refusals:
                with self.subTest(command=command, door=door):
                    code, out, err = self._run([*command, *door])
                    self.assertEqual(code, 2, out + err)
                    text = out + err
                    self._clean(text)
                    self.assertIn(refused, text)
                    if "--json" in door:
                        body = json.loads(out)["error"]
                        self.assertEqual(body["code"], refused)
                        self.assertIn("next", body)
                        if expected:
                            self.assertEqual(body["details"]["path"], expected)
                    elif expected:
                        self.assertIn(f"path: {expected}", err)

    def test_a_held_lock_and_a_damaged_index_leave_folded(self) -> None:
        for _name, door in self._doors():
            with self.subTest(door=door, case="lock"):
                with held_in_thread(lambda: hold_lock(self.root, "intake", wait_seconds=30, label="door")):
                    code, out, err = self._run(["log", *door], "the till counted while the lock was held\n")
                self.assertEqual(code, 2, out + err)
                self._clean(out + err)
                self.assertIn("lock_busy", out + err)
        pointer = self.root / ".forest" / "index.current"
        pointer.write_bytes(b"\xff\xfe")
        try:
            for _name, door in self._doors():
                with self.subTest(door=door, case="pointer"):
                    code, out, err = self._run(["search", "swept", *door])
                    self.assertEqual(code, 2, out + err)
                    self._clean(out + err)
                    self.assertIn("index_unreadable", out + err)
        finally:
            search.reset(self.root)
            search.build(self.root)

    def test_a_forest_that_is_not_there_is_folded_for_an_agent_and_not_for_the_owner(self) -> None:
        elsewhere = self.home / "Jänë Docs" / "not a forest"
        elsewhere.mkdir()
        code, out, err = self._run(["search", "swept", "--as", "till-bot", "--root", str(elsewhere), "--json"])
        self.assertEqual(code, 2)
        self._clean(out + err)
        self.assertEqual(json.loads(out)["error"]["code"], "no_forest")
        owner = self._run(["index", "status", "--root", str(elsewhere)])
        self.assertIn(str(elsewhere), owner[2])

    def test_a_success_through_an_agent_door_keeps_the_forest_text(self) -> None:
        for _name, door in self._doors():
            with self.subTest(door=door):
                code, out, err = self._run(["read", PAGE, *door])
                self.assertEqual(code, 0, out + err)
                self.assertIn(TOOL_LINE, out)
                self.assertIn(self.home_line, out)
                for secret in (str(self.root), self.host, self.user):
                    self.assertNotIn(secret, out.replace(self.home_line, ""))

    def test_the_door_and_the_tool_send_the_same_refusal(self) -> None:
        path = str(self.home / "notes.md")
        _code, out, _err = self._run(["read", path, "--as", "till-bot", "--root", str(self.root), "--json"])
        from_door = json.loads(out)["error"]
        environment = {**os.environ, "HOME": str(self.home)}
        with mock.patch.dict(os.environ, environment, clear=False):
            reply, _version = mcp.handle_message(
                self.root,
                "till-bot",
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": "memory_read", "arguments": {"path": path}}},
                "stdio",
                "2025-11-25",
            )
        from_tool = json.loads(reply["result"]["content"][0]["text"])
        self.assertEqual(from_door, from_tool)
