"""Every command, every reader flag, text and JSON, and every tool, on every disk shape the order lists.

The commands come from the parser, the tools from the tool table, and the shapes from the eight
permission modes plus the review's other modes, a file, a fifo, a link to a folder and a link to
nothing, each for the forest folder, ``.forest`` and ``.forest/credentials``. A command added to the
parser is in this test without anyone listing it.

The bakery and the bots are invented.
"""

from __future__ import annotations

import argparse
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

import bounds  # installs a deadline for every unittest invocation
from pathlib import Path
from unittest import mock

from memory_forest import cli, mcp, search, system

import door_runtime
from test_v1_sweep import SWEEP_FILES

READER = "till-bot"
PAGE = "04 stm/bakery/ovens/floor.md"
# The eight modes of one permission digit, and the review's modes that are not among them.
MODES = (0o000, 0o111, 0o222, 0o333, 0o444, 0o555, 0o666, 0o777, 0o100, 0o200, 0o300, 0o400, 0o600)
KINDS = ("file", "fifo", "link_folder", "link_nothing")
TARGETS = ("forest", ".forest", "credentials")
# ``--as`` on a move is a new name, not the reader. Only these destinations choose who reads.
READER_DESTS = {"reader": "agent", "group": "group", "caller": "owner"}


def _commands(parser=None):
    """Discover the parser's leaves and reader actions, including commands added outside its table."""
    parser = cli.build_forest_parser() if parser is None else parser
    def walk(current, words):
        children = [action for action in current._actions if isinstance(action, argparse._SubParsersAction)]
        for action in children:
            for word, child in action.choices.items():
                yield from walk(child, (*words, word))
        if children:
            return
        run = current.get_default("_run")
        if not callable(run):
            return
        arguments = []
        for action in current._actions:
            if action.dest in {"help", "json", "root", "version"}:
                continue
            arguments.append(cli.Argument(tuple(action.option_strings) or (action.dest,),
                                          {"dest": action.dest, "nargs": action.nargs,
                                           "required": action.required}))
        yield cli.ForestCommand(words, run, current.description or "", tuple(arguments),
                               uses_root=bool(current.get_default("_uses_root")))
    return list(walk(parser, ()))


class ParserProductTests(unittest.TestCase):
    def test_a_command_added_to_the_parser_is_in_the_product(self):
        parser = argparse.ArgumentParser()
        leaf = parser.add_subparsers().add_parser("knead")
        leaf.add_argument("words")
        leaf.add_argument("--as", dest="reader")
        leaf.set_defaults(_run=lambda arguments: 0, _uses_root=True)
        found = _commands(parser)
        self.assertEqual([command.words for command in found], [("knead",)])
        self.assertEqual(_reader_flags(found[0]), [((), "owner"), (("--as", READER), "agent")])
        self.assertIn("--root", _argv(found[0], Path("synthetic"), (), as_json=True, nonce="0"))


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
        extra = (flag, value)
        if dest == "group" and any(a.options.get("dest") == "caller" for a in command.arguments):
            extra += ("--agent", READER)
        found.append((extra, READER_DESTS[dest]))
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


def _run_unwatched(argv: list[str], stdin_bytes: bytes = b"") -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    stdin = io.TextIOWrapper(io.BytesIO(stdin_bytes), encoding="utf-8")
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.object(sys, "stdin", stdin):
        try:
            code = cli.forest_main(argv)
        except SystemExit as exc:
            code = int(exc.code) if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def _run(*args, **kwargs):
    with door_runtime.watching():
        return _run_unwatched(*args, **kwargs)


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
    bound_seconds = 900

    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls._temporary.name).resolve()
        cls.template = cls.base / "template"
        for relative, text in SWEEP_FILES.items():
            target = cls.template / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        settings = cls.template / "forest.toml"
        settings.write_text(settings.read_text() + '\n[agents.groups.bakers]\nmembers = ["till-bot"]\nopen = false\n')
        search.build(cls.template)
        credentials = cls.template / ".forest" / "credentials"
        if not credentials.exists():
            credentials.mkdir(mode=0o700)
        cls.commands = _commands()
        cls.tools = list(mcp._TOOLS)
        cls.home = str(Path.home())
        cls.host = socket.gethostname()
        cls.user = getpass.getuser()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def _secret(self, work: Path, text: str) -> str | None:
        for secret in (str(work), str(self.base), self.home, self.host, self.user):
            if secret and len(secret) > 1 and secret in text:
                return secret
        return None

    def test_every_command_and_tool_on_every_disk_shape(self) -> None:
        self.assertGreater(len(self.commands), 5)
        self.assertGreater(len(self.tools), 3)
        started = time.monotonic()
        self.cells = 0
        for target, kind, mode in _shapes():
            self.disk_shape = (target, kind, mode)
            label = f"{target} {kind}" if kind != "mode" else f"{target} {mode:04o}"
            self._one_shape(self.template, label)
        elapsed = time.monotonic() - started
        # Kept for the report. A hang would not reach this line.
        sys.stderr.write(f"\nshape product: {len(_shapes())} states, {self.cells} cells in {elapsed:.1f} s\n")
        self.assertLess(elapsed, 900, f"{len(_shapes())} shapes took {elapsed:.0f}s")

    @contextlib.contextmanager
    def cell(self, _work):
        with tempfile.TemporaryDirectory(dir=self.base) as temporary:
            work = Path(temporary) / "forest"
            shutil.copytree(self.template, work, symlinks=True)
            kept = _apply(work, *self.disk_shape)
            try:
                self.cells += 1
                yield work
            finally:
                kept.restore()

    def _one_shape(self, work: Path, label: str) -> None:
        tool_answers = {}
        for tool in self.tools:
            for who in ("agent", "group"):
                caught = io.StringIO()
                arguments = _tool_arguments(tool)
                if who == "group":
                    arguments["agent"] = READER
                with contextlib.redirect_stderr(caught), self.cell(work) as current, door_runtime.watching():
                    reply = mcp._call(current, READER, {"name": tool["name"], "arguments": arguments}, "stdio",
                                      group="bakers" if who == "group" else None)
                self.assertNotIn("Traceback", caught.getvalue())
                body = json.loads(reply["content"][0]["text"])
                text = reply["content"][0]["text"]
                self.assertNotIn("Traceback", text)
                if reply["isError"]:
                    self.assertIsNone(self._secret(work, text), f"{label} {tool['name']}")
                    tool_answers[(tool["name"], who)] = (str(body.get("code")), str(body.get("next") or ""))
                else:
                    tool_answers[(tool["name"], who)] = (None, None)
        for command in self.commands:
            paired = "memory_" + command.words[0]
            if command.words[0] == "timeline":
                paired = "memory_timeline"
            if (paired, "agent") not in tool_answers:
                paired = ""
            for extra, who in _reader_flags(command):
                answers = []
                for as_json in (False, True):
                    with self.cell(work) as current:
                        argv = _argv(command, current, extra, as_json=as_json, nonce=str(as_json))
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
                if who in {"agent", "group"} and paired and extra[:1] in {("--as",), ("--as-group",)}:
                    self.assertEqual(answers[1], tool_answers[(paired, who)], f"{label} {paired} {who}")


class DiskProductTests(WaysOutTests):
    """Every distinct real-disk step of FactsOnDiskTests, crossed with the same parser and tool table."""
    bound_seconds = 1800

    def test_every_command_and_tool_on_every_disk_shape(self):
        from test_v1_derived import FactsOnDiskTests
        sample = FactsOnDiskTests()
        unique = {}
        for index, (step, value, _make) in enumerate(sample._cases()):
            unique.setdefault((step, value), index)
        started = time.monotonic()
        self.cells = 0
        for label, index in unique.items():
            self.case_index = index
            with self.subTest(disk=label):
                try:
                    self._one_shape(self.template, str(label))
                except AssertionError as error:
                    sys.stderr.write(f"\ndisk product {label}: {error}\n")
                    raise
        sys.stderr.write(f"\ndisk product: {len(unique)} states, {self.cells} cells in {time.monotonic() - started:.1f} s\n")

    @contextlib.contextmanager
    def cell(self, _work):
        from test_v1_derived import FactsOnDiskTests
        with tempfile.TemporaryDirectory(dir=self.base) as temporary:
            base = Path(temporary)
            root = base / "forest"
            shutil.copytree(self.template, root, symlinks=True)
            case = FactsOnDiskTests()
            case.base, case.built, case.count = base, self.template, 1
            case.addCleanup(case._open_up, root)
            try:
                step, value, make = case._cases()[self.case_index]
                made = make(root)
                options = made if isinstance(made, dict) else {}
                current = made if isinstance(made, Path) else root
                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.object(system, "OPEN_DEADLINE_SECONDS", min(options.get("deadline", 0.15), 0.15)))
                    if (step, value) == ("metadata", "busy"):
                        original = system.open_index
                        def opened(*args, **kwargs):
                            kwargs["busy_seconds"] = 0.01
                            kwargs["deadline_seconds"] = 1.0
                            return original(*args, **kwargs)
                        stack.enter_context(mock.patch.object(system, "open_index", opened))
                    stack.enter_context(mock.patch.object(search, "MASKING_WAIT_SECONDS", 0.05))
                    stack.enter_context(mock.patch.object(search, "READER_TIMEOUT_SECONDS", 0.05))
                    if options.get("swap"):
                        stack.enter_context(system.at(system.POINT_INDEX_OPEN, lambda *, path: Path(path).unlink()))
                    self.cells += 1
                    yield current
            finally:
                self.assertTrue(case.doCleanups(), "disk-cell cleanup failed")


if __name__ == "__main__":
    unittest.main()
