"""Every command, every reader flag, text and JSON, and every tool, on every disk shape the order lists.

The commands come from the parser, the tools from the tool table, and the shapes from the eight
permission modes plus the review's other modes, a file, a fifo, a link to a folder and a link to
nothing, each for the forest folder, ``.forest`` and ``.forest/credentials``. A command added to the
parser is in this test without anyone listing it.

The bakery and the bots are invented.
"""

from __future__ import annotations

import contextlib
import getpass
import io
import json
import os
import shutil
import socket
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from memory_forest import cli, mcp, search

from test_v1_sweep import SWEEP_FILES

READER = "till-bot"
PAGE = "04 stm/bakery/ovens/floor.md"
# The eight modes of one permission digit, and the review's modes that are not among them.
MODES = (0o000, 0o111, 0o222, 0o333, 0o444, 0o555, 0o666, 0o777, 0o100, 0o200, 0o300, 0o400, 0o600)
KINDS = ("file", "fifo", "link_folder", "link_nothing")
TARGETS = ("forest", ".forest", "credentials")
# ``--as`` on a move is a new name, not the reader. Only these destinations choose who reads.
READER_DESTS = {"reader": "agent", "group": "group", "caller": "owner"}


def _shapes() -> list[tuple[str, str, int | None]]:
    found = []
    for target in TARGETS:
        for mode in MODES:
            found.append((target, "mode", mode))
        for kind in KINDS:
            found.append((target, kind, None))
    return found


def _sample(name: str, work: Path, nonce: str = "0") -> str:
    if name in {"directory"}:
        return str(work / f"created-{nonce}")
    if name in {"path", "old_path", "page"}:
        return PAGE
    if name in {"words", "phrase", "query"}:
        return "trays" if name == "words" else "2051-03-02"
    if name in {"reader", "agent", "caller", "named", "name"}:
        return READER
    if name == "group":
        return "bakers"
    if name == "reason":
        return "because"
    if name in {"limit"}:
        return "5"
    return "sample"


def _reader_flags(command) -> list[tuple[tuple[str, ...], str]]:
    """The owner, and one invocation for each flag that chooses who reads, with that reader."""
    found: list[tuple[tuple[str, ...], str]] = [((), "owner")]
    for argument in command.arguments:
        options = argument.options
        dest = str(options.get("dest") or "")
        if dest not in READER_DESTS:
            continue
        flags = [flag for flag in argument.flags if flag.startswith("-")]
        flag = flags[0]
        value = "bakers" if dest == "group" else READER
        found.append(((flag, value), READER_DESTS[dest]))
    return found


def _argv(command, work: Path, extra: tuple[str, ...], *, as_json: bool, nonce: str) -> list[str]:
    words = list(command.words)
    for argument in command.arguments:
        flag = argument.flags[0]
        options = argument.options
        if flag.startswith("-"):
            if options.get("required"):
                words.extend([flag, _sample(str(options.get("dest") or flag), work, nonce)])
            continue
        if options.get("nargs") in ("?", "*"):
            continue
        words.append(_sample(str(options.get("dest") or flag), work, nonce))
    words.extend(extra)
    if as_json:
        words.append("--json")
    if command.uses_root:
        words.extend(["--root", str(work)])
    return words


def _run(argv: list[str], stdin_bytes: bytes = b"") -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    stdin = io.TextIOWrapper(io.BytesIO(stdin_bytes), encoding="utf-8")
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.object(sys, "stdin", stdin):
        try:
            code = cli.forest_main(argv)
        except SystemExit as exc:
            code = int(exc.code) if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def _refusal(code: int, out: str, err: str) -> tuple[str | None, str | None]:
    """The code and move, or (None, None) when the answer is not a refusal."""
    text = out.strip()
    if text.startswith("{"):
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            return None, None
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict) and error.get("code"):
            return str(error.get("code")), str(error.get("next") or "")
        return None, None
    for line in err.splitlines():
        if line.startswith("forest: "):
            parts = line.split(": ", 2)
            if len(parts) >= 2:
                move = ""
                for later in err.splitlines():
                    if later.startswith("  next: "):
                        move = later.split(": ", 1)[1].strip()
                return parts[1], move
    if code == 2 and "forest:" in err:
        return "unparsed", ""
    return None, None


def _tool_arguments(tool: dict) -> dict[str, object]:
    schema = tool["inputSchema"]
    required = list(schema.get("required") or [])
    properties = schema.get("properties") or {}
    values = {
        "words": "trays",
        "path": PAGE,
        "text": "the till counted the trays",
        "phrase": "2051-03-02",
        "subject": "till-bot",
        "lines": "1-2",
        "source": "door:counter",
        "limit": 5,
    }
    arguments = {name: values.get(name, "sample") for name in required}
    if not arguments and "phrase" in properties:
        arguments["phrase"] = values["phrase"]
    return arguments


def _target(work: Path, name: str) -> Path:
    if name == "forest":
        return work
    if name == ".forest":
        return work / ".forest"
    return work / ".forest" / "credentials"


class _Kept:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.existed = path.exists() or path.is_symlink()
        self.aside = path.with_name(path.name + ".kept")
        self.mode = stat.S_IMODE(path.lstat().st_mode) if self.existed and not path.is_symlink() else None
        self.displaced = False

    def displace(self) -> None:
        if self.existed:
            os.rename(self.path, self.aside)
            self.displaced = True

    def restore(self) -> None:
        if self.displaced or not self.existed:
            if self.path.is_symlink() or self.path.exists():
                if self.path.is_dir() and not self.path.is_symlink():
                    shutil.rmtree(self.path)
                else:
                    self.path.unlink()
            if self.displaced and self.aside.exists():
                os.rename(self.aside, self.path)
            return
        if self.mode is not None and self.path.exists() and not self.path.is_symlink():
            os.chmod(self.path, self.mode)


def _apply(work: Path, target: str, kind: str, mode: int | None) -> _Kept:
    path = _target(work, target)
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = _Kept(path)
    if kind == "mode":
        if not path.exists():
            path.write_bytes(b"")
            kept.existed = False
        os.chmod(path, int(mode or 0))
        return kept
    kept.displace()
    link_dir = work.parent / "linked-folder"
    link_dir.mkdir(exist_ok=True)
    if kind == "file":
        path.write_bytes(b"not a folder\n")
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "link_folder":
        path.symlink_to(link_dir)
    elif kind == "link_nothing":
        path.symlink_to(work.parent / "no-such-target")
    else:
        raise AssertionError(kind)
    return kept


class WaysOutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls._temporary.name).resolve()
        cls.template = cls.base / "template"
        for relative, text in SWEEP_FILES.items():
            target = cls.template / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        search.build(cls.template)
        credentials = cls.template / ".forest" / "credentials"
        if not credentials.exists():
            credentials.write_bytes(b"")
            os.chmod(credentials, 0o600)
        cls.commands = list(cli.FOREST_COMMANDS)
        cls.tools = list(mcp._TOOLS)
        cls.home = str(Path.home())
        cls.host = socket.gethostname()
        cls.user = getpass.getuser()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def _copy(self) -> Path:
        work = self.base / "work"
        if work.exists():
            shutil.rmtree(work)
        shutil.copytree(self.template, work, symlinks=True)
        return work

    def _secret(self, work: Path, text: str) -> str | None:
        for secret in (str(work), self.home, self.host, self.user):
            if secret and len(secret) > 1 and secret in text:
                return secret
        return None

    def test_every_command_and_tool_on_every_disk_shape(self) -> None:
        self.assertGreater(len(self.commands), 5)
        self.assertGreater(len(self.tools), 3)
        started = time.monotonic()
        for target, kind, mode in _shapes():
            work = self._copy()
            kept = _apply(work, target, kind, mode)
            label = f"{target} {kind}" if kind != "mode" else f"{target} {mode:04o}"
            try:
                self._one_shape(work, label)
            finally:
                kept.restore()
        elapsed = time.monotonic() - started
        # Kept for the report. A hang would not reach this line.
        self.assertLess(elapsed, 600, f"{len(_shapes())} shapes took {elapsed:.0f}s")

    def _one_shape(self, work: Path, label: str) -> None:
        tool_answers = {}
        for tool in self.tools:
            caught = io.StringIO()
            with contextlib.redirect_stderr(caught):
                reply = mcp._call(
                    work, READER, {"name": tool["name"], "arguments": _tool_arguments(tool)}, "stdio"
                )
            self.assertNotIn("Traceback", caught.getvalue())
            body = json.loads(reply["content"][0]["text"])
            text = reply["content"][0]["text"]
            self.assertNotIn("Traceback", text)
            if reply["isError"]:
                self.assertIsNone(self._secret(work, text), f"{label} {tool['name']}")
                tool_answers[tool["name"]] = (str(body.get("code")), str(body.get("next") or ""))
            else:
                tool_answers[tool["name"]] = (None, None)
        for command in self.commands:
            paired = "memory_" + command.words[0]
            if command.words[0] == "timeline":
                paired = "memory_timeline"
            if paired not in tool_answers:
                paired = ""
            for extra, who in _reader_flags(command):
                answers = []
                for as_json in (False, True):
                    argv = _argv(command, work, extra, as_json=as_json, nonce=str(as_json))
                    stdin_bytes = b"the till counted the trays\n" if command.words == ("log",) else b""
                    code, out, err = _run(argv, stdin_bytes)
                    refusal = _refusal(code, out, err)
                    answers.append(refusal)
                    with self.subTest(shape=label, command=" ".join(command.words), extra=extra, json=as_json):
                        self.assertNotIn("Traceback", out)
                        self.assertNotIn("Traceback", err)
                        if refusal[0]:
                            self.assertEqual(code, 2, out + err)
                        if who in {"agent", "group"}:
                            self.assertIsNone(self._secret(work, out + err), out + err)
                self.assertEqual(answers[0], answers[1], f"{label} {' '.join(command.words)} {extra}")
                if who == "agent" and paired and extra[:1] == ("--as",):
                    self.assertEqual(answers[1], tool_answers[paired], f"{label} {paired}")


if __name__ == "__main__":
    unittest.main()
