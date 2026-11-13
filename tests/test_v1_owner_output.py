"""What the owner reads at a terminal when a command meets a fault no code decided on, pinned word for word.

Without ``--as``, a command's ``filesystem_error``, ``read_failed``, ``internal_error`` and an ``index_failed``
from an error of SQLite no read classified are refusals of the same shape as every other: lines on stderr,
or one JSON object on stdout with ``--json``. Stand-in commands raise what a real command can meet (an
outcome of the door, text that is not UTF-8, a fault of the code, an error SQLite returned, Ctrl-C); what is
pinned is the edge of the command line, which every command shares.

The bakery is invented. Nothing here is a real person, shop or transcript.
"""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest

import bounds  # installs a deadline for every unittest invocation
from pathlib import Path
from unittest import mock

from memory_forest import cli, system
from memory_forest.errors import MemoryForestError

from test_v1_find import FILES

PAGE = "04 stm/bakery/ovens/floor.md"


def _door_other(arguments) -> int:
    with system.watching(fail_calls={1: "other"}):
        system.read_bytes(Path(arguments.root) / PAGE)
    return 0


def _not_text(arguments) -> int:
    b"the oven \xff".decode("utf-8")
    return 0


def _fault(arguments) -> int:
    raise RuntimeError("a fault the code did not expect, at the till")


def _sqlite_other(arguments) -> int:
    """An error SQLite itself returns (with its code), met where no read classified it."""
    with contextlib.closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("SELECT proof_time()")
    return 0


def _interrupted(arguments) -> int:
    raise KeyboardInterrupt


def _hooked(arguments) -> int:
    """A stand-in run as a hook (``intake`` and ``--hook`` in its words): it meets what ``raised`` names."""
    raised = {
        "refusal": MemoryForestError("search_empty", "Give the words to search for.", next_step="fix"),
        "not-text": UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
        "door-other": system.Other(Path(arguments.root) / PAGE),
        "sqlite-other": sqlite3.OperationalError("no such function: proof_time"),
        "interrupted": KeyboardInterrupt(),
        "fault": RuntimeError("a fault the code did not expect, at the till"),
    }[arguments.raised]
    raise raised


STAND_INS = [
    cli.ForestCommand(("stand-in", "intake"), _hooked, "Meet a fault as a hook does.",
                      (cli.arg("raised"), cli.arg("--hook", action="store_true"))),
    cli.ForestCommand(("stand-in", "door-other"), _door_other, "Read a page; the door answers other."),
    cli.ForestCommand(("stand-in", "not-text"), _not_text, "Decode bytes that are not UTF-8."),
    cli.ForestCommand(("stand-in", "fault"), _fault, "Raise a fault of the code."),
    cli.ForestCommand(("stand-in", "sqlite-other"), _sqlite_other, "Meet an error of SQLite no read classified."),
    cli.ForestCommand(("stand-in", "interrupted"), _interrupted, "Stop as Ctrl-C stops a command."),
]

FILESYSTEM_ERROR = {
    "code": "filesystem_error",
    "details": {"error": "EIO", "kind": "other", "path": PAGE},
    "message": f"{PAGE} could not be used: the system answered EIO. Tell the owner.",
    "next": "report",
}
READ_FAILED = {
    "code": "read_failed",
    "details": {"reason": "UnicodeDecodeError"},
    "message": "A file could not be read as text.",
    "next": "report",
}
INTERNAL_ERROR = {
    "code": "internal_error",
    "details": {"reason": "RuntimeError"},
    "message": "The command failed without exposing internal details.",
    "next": "report",
}
# The forest has no index yet, so the error is told by its own class, with SQLite's name for it.
SQLITE_MESSAGE = ("SQLite refused the search index for a reason this package does not classify. The pages are "
                  "untouched. Tell the owner the SQLite code in the details.")
INDEX_FAILED = {
    "code": "index_failed",
    "details": {"path": ".forest/index.sqlite", "sqlite": "SQLITE_ERROR"},
    "message": SQLITE_MESSAGE,
    "next": "report",
}
# What the owner reads, line for line: the code and the message, each detail, then the next move.
TEXT = {
    "door-other": (
        f"forest: filesystem_error: {PAGE} could not be used: the system answered EIO. Tell the owner.\n"
        "  error: EIO\n"
        "  kind: other\n"
        f"  path: {PAGE}\n"
        "  next: report\n"
    ),
    "not-text": (
        "forest: read_failed: A file could not be read as text.\n"
        "  reason: UnicodeDecodeError\n"
        "  next: report\n"
    ),
    "fault": (
        "forest: internal_error: The command failed without exposing internal details.\n"
        "  reason: RuntimeError\n"
        "  next: report\n"
    ),
    "sqlite-other": (
        f"forest: index_failed: {SQLITE_MESSAGE}\n"
        "  path: .forest/index.sqlite\n"
        "  sqlite: SQLITE_ERROR\n"
        "  next: report\n"
    ),
}
BODIES = {"door-other": FILESYSTEM_ERROR, "not-text": READ_FAILED, "fault": INTERNAL_ERROR,
          "sqlite-other": INDEX_FAILED}


class OwnerOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "forest"
        for relative, text in FILES.items():
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")

    def _run(self, name: str, *, as_json: bool) -> tuple[int, str, str]:
        argv = ["stand-in", name, "--root", str(self.root)] + (["--json"] if as_json else [])
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "FOREST_COMMANDS", [*cli.FOREST_COMMANDS, *STAND_INS]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.forest_main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_text_at_the_terminal(self) -> None:
        for name, text in TEXT.items():
            with self.subTest(command=name):
                self.assertEqual(self._run(name, as_json=False), (2, "", text))

    def test_json_with_the_flag(self) -> None:
        for name, body in BODIES.items():
            with self.subTest(command=name):
                expected = json.dumps({"error": body, "ok": False}, ensure_ascii=False, indent=2, sort_keys=True)
                self.assertEqual(self._run(name, as_json=True), (2, expected + "\n", ""))

    def test_an_interrupted_command_says_nothing_and_exits_130(self) -> None:
        for as_json in (False, True):
            with self.subTest(json=as_json):
                self.assertEqual(self._run("interrupted", as_json=as_json), (130, "", ""))

    def test_a_hook_says_nothing_exits_0_and_logs_only_the_class(self) -> None:
        classes = {"refusal": "search_empty", "not-text": "UnicodeError", "door-other": "Other",
                   "sqlite-other": "OperationalError", "interrupted": "KeyboardInterrupt", "fault": "RuntimeError"}
        failures = self.root / ".forest" / "intake" / "failures.jsonl"
        for raised, logged in classes.items():
            with self.subTest(raised=raised):
                argv = ["stand-in", "intake", raised, "--hook", "--root", str(self.root)]
                out, err = io.StringIO(), io.StringIO()
                with mock.patch.object(cli, "FOREST_COMMANDS", [*cli.FOREST_COMMANDS, *STAND_INS]), \
                        contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = cli.forest_main(argv)
                self.assertEqual((code, out.getvalue(), err.getvalue()), (0, "", ""))
                last = json.loads(failures.read_text(encoding="utf-8").splitlines()[-1])
                self.assertEqual(last["class"], logged)
        argv = ["stand-in", "intake", "--hook", "--root", str(self.root)]  # no argument: the parser refuses
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "FOREST_COMMANDS", [*cli.FOREST_COMMANDS, *STAND_INS]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.forest_main(argv)
        self.assertEqual((code, out.getvalue(), err.getvalue()), (0, "", ""))
        self.assertEqual(json.loads(failures.read_text(encoding="utf-8").splitlines()[-1])["class"], "intake_arguments")

    def _main(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "FOREST_COMMANDS", [*cli.FOREST_COMMANDS, *STAND_INS]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.forest_main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_without_a_hook_a_refusal_exits_2_and_a_wrong_call_is_the_parsers(self) -> None:
        code, out, err = self._main(["stand-in", "intake", "refusal", "--root", str(self.root), "--json"])
        self.assertEqual((code, json.loads(out)["error"]["code"], err), (2, "search_empty", ""))
        with self.assertRaises(SystemExit) as stopped, contextlib.redirect_stderr(io.StringIO()):
            self._main(["stand-in", "intake", "--root", str(self.root)])
        self.assertEqual(stopped.exception.code, 2)

    def test_a_folder_that_is_not_a_forest_is_refused_before_the_command_runs(self) -> None:
        empty = self.root.parent / "empty"
        empty.mkdir()
        code, out, _err = self._main(["stand-in", "fault", "--root", str(empty), "--json"])
        self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "no_forest"))

    def test_no_part_of_the_fault_leaves(self) -> None:
        for name in TEXT:
            for as_json in (False, True):
                code, out, err = self._run(name, as_json=as_json)
                with self.subTest(command=name, json=as_json):
                    self.assertNotIn(str(self.root), out + err)
                    self.assertNotIn("at the till", out + err)
                    self.assertNotIn("Traceback", out + err)


if __name__ == "__main__":
    unittest.main()
