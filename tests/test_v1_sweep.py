"""Every door call of every agent call fails in turn, with each outcome it can answer; then every SQLite
statement, with each class of error (the sweep of section 3 of the round's order).

The commands and tools come from the parser and the tool table (``test_v1_door``). For each, a clean run
counts the door's calls and SQLite's statements; then, for N from 1 to that count, the Nth call answers each
outcome ``system.CAN`` lists for it, and the Nth statement fails with each class of ``system.SQLITE_FAULTS``.
Every run: the answer is a success or a refusal of the table with its next move; no sentence contradicts
what was injected; nothing names a path of the machine; it returns within the deadline; a call that only
reads changed nothing on disk, and a call that may build leaves the index sound.

The bakery and the bots are invented. Nothing here is a real person, shop or transcript.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import inspect
import io
import json
import os
import shlex
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from memory_forest import cli, derived, errors, mcp, search, system, timeline
from memory_forest.errors import REFUSALS, MemoryForestError

import door_rules
import test_v1_door as door
from test_v1_find import FILES

READER = door.READER
# A small forest: one tree page, one agent tree and one lane, so the sweep stays in seconds.
SWEEP_FILES = {
    "forest.toml": FILES["forest.toml"].replace('[agents]\norchestrators = ["front-desk"]\n', "[agents]\n"),
    "01 xltm/XLTM.md": (
        "# XLTM\n\n## Identity\n\n- Name: Ilse Varrow; memory text calls her Ilse.\n\n## Trees\n\n"
        "- `bakery` — the bakery, its oven and its floor.\n- `till-bot` — Till Bot, the counter.\n\n"
        "## Agents\n\n- `till-bot` — Till Bot · the counter\n"
    ),
    "02 ltm/bakery_LTM.md": FILES["02 ltm/bakery_LTM.md"],
    "02 ltm/till-bot_LTM.md": FILES["02 ltm/till-bot_LTM.md"],
    "03 mtm/bakery/bakery_MTM.md": (
        "# bakery\n\n## Subjects\n\n- `floor-notes` — Floor notes · topic · leaf · `04 stm/bakery/ovens/floor.md`\n\n"
        "## Branches\n\n## Links\n\n## Update record\n"
    ),
    "03 mtm/bakery/ovens.md": FILES["03 mtm/bakery/ovens.md"],
    "03 mtm/till-bot/till-bot_MTM.md": FILES["03 mtm/till-bot/till-bot_MTM.md"],
    "04 stm/bakery/ovens/floor.md": FILES["04 stm/bakery/ovens/floor.md"],
    "06 istm/till-bot/2051-03-02.jsonl": FILES["06 istm/till-bot/2051-03-02.jsonl"],
}
# What the MCP server writes by design on a read: when the agent was last seen (``mcp._remember``).
SEEN = (".forest/mcp", ".forest/mcp/seen.json", ".forest/locks/mcp-seen.lock")
# Calls that write by design: the turn and its index rows.
WRITERS = frozenset({("log",), "memory_log"})
# Calls that may build the index before they read (a read in a writable forest builds a missing index).
BUILDERS = frozenset({("search",), ("timeline", "query"), ("timeline", "subject"), "memory_search", "memory_timeline"})
DEADLINE_SECONDS = system.OPEN_DEADLINE_SECONDS + 5.0
MOVES = ("fix", "retry", "run", "report")

# Words that say one kind of outcome. A sentence answering another kind may not use them.
SAYS = {
    "absent": ("does not exist", "no such", "not found", "is missing", "went away", "no forest", "no search index",
               "there is no"),
    "denied": ("cannot read", "cannot list", "cannot enter", "cannot write", "access to", "permission"),
    "link": ("symlink", "a link", "symbolic link"),
    "not_a_file": ("not a regular file", "not a file"),
    "not_a_folder": ("not a folder",),
    "damaged": ("damaged", "corrupt", "malformed", "not a database", "does not name an index"),
    "busy": ("in use", "is busy", "answers later", "changed while"),
}
NEVER_SAID = {
    "absent": ("denied", "link", "not_a_file", "not_a_folder", "damaged"),
    "denied": ("absent", "link", "not_a_file", "not_a_folder", "damaged", "busy"),
    # A thing found where an earlier look saw a file means the path changed: "changed while" is true of it.
    "not_a_file": ("absent", "denied", "link", "damaged"),
    "not_a_folder": ("absent", "denied", "link", "damaged"),
    "link": ("absent", "denied", "not_a_file", "not_a_folder", "damaged"),
    "changed": ("absent", "denied", "link", "not_a_file", "not_a_folder", "damaged"),
    "busy": ("absent", "denied", "link", "not_a_file", "not_a_folder", "damaged"),
    "other": ("absent", "denied", "link", "not_a_file", "not_a_folder", "damaged", "busy"),
}
SQLITE_NEVER_SAID = {
    "busy": ("absent", "denied", "damaged"),
    "protocol": ("absent", "denied", "damaged"),
    "locked": ("absent", "denied", "damaged"),
    "not_a_database": ("absent", "denied", "busy"),
    "damaged": ("absent", "denied", "busy"),
    "disk": ("absent", "denied", "damaged", "busy"),
    "full": ("absent", "denied", "damaged", "busy"),
    "read_only": ("absent", "damaged", "busy"),
    "other": ("absent", "denied", "damaged", "busy"),
}


def snapshot(root: Path) -> dict[str, tuple[int, int, bytes | str | None]]:
    """Every entry under ``root`` (and ``root`` itself): its kind, its mode and its bytes or link target."""
    found: dict[str, tuple[int, int, bytes | str | None]] = {}
    info = os.lstat(root)
    found["."] = (stat.S_IFMT(info.st_mode), stat.S_IMODE(info.st_mode), None)
    for folder, folders, files in os.walk(root):
        for name in sorted(folders + files):
            path = Path(folder, name)
            info = os.lstat(path)
            body: bytes | str | None = None
            if stat.S_ISREG(info.st_mode):
                body = path.read_bytes()
            elif stat.S_ISLNK(info.st_mode):
                body = os.readlink(path)
            found[path.relative_to(root).as_posix()] = (stat.S_IFMT(info.st_mode), stat.S_IMODE(info.st_mode), body)
    return found


def differences(before: dict, after: dict, *, ignore: tuple[str, ...] = ()) -> list[str]:
    return sorted(
        name for name in set(before) | set(after)
        if before.get(name) != after.get(name) and not any(name == kept or name.startswith(kept + "/") for kept in ignore)
    )


class Answer:
    """One call's answer: success, or a refusal with its code, next move, message and details."""

    def __init__(self, raw: str, refused: bool, body: dict) -> None:
        self.raw = raw
        self.refused = refused
        self.body = body
        error = body.get("error") if isinstance(body.get("error"), dict) else body
        self.code = error.get("code") if refused else None
        self.next = error.get("next") if refused else None
        self.message = str(error.get("message") or "") if refused else ""
        self.details = error.get("details") or {} if refused else {}
        self.command = error.get("command") if refused else None


def call(root: Path, which: tuple[str, ...] | str) -> Answer:
    if isinstance(which, tuple):
        code, out, err = door.run_command(root, which)
        try:
            body = json.loads(out) if out.strip() else {}
        except json.JSONDecodeError:
            body = {"error": {"code": "<not json>", "message": out + err}}
        return Answer(out + err, code != 0, body)
    reply = mcp._call(root, READER, {"name": which, "arguments": dict(door.TOOL_ARGUMENTS[which])}, "stdio")
    text = reply["content"][0]["text"]
    return Answer(text, bool(reply["isError"]), json.loads(text))


# The intake modules are not through the door yet (``door_rules.NOT_YET``; another branch replaces them). A
# call the door gets while one of them is on the stack is theirs: what such a fault leaves is reported as
# left for the merge, not judged here.
_INTAKE_FILES = frozenset(str(door_rules.PACKAGE / name) for name in door_rules.INTAKE)


def _intake_on_stack() -> bool:
    frame = sys._getframe(2)
    while frame is not None:
        if frame.f_code.co_filename in _INTAKE_FILES:
            return True
        frame = frame.f_back
    return False


def _tail(path: str) -> str:
    return "/".join(Path(path).parts[-2:])


def label(which: tuple[str, ...] | str) -> str:
    return " ".join(which) if isinstance(which, tuple) else which


@unittest.skipIf(os.geteuid() == 0, "the superuser is never denied")
class SweepTests(unittest.TestCase):
    """The sweep. One forest; it is put back from a clean copy whenever a run changed it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls._temporary.name).resolve()
        cls.root = cls.base / "forest"
        cls.clean = cls.base / "clean"
        for relative, text in SWEEP_FILES.items():
            target = cls.clean / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        search.build(cls.clean)
        # The server's seen record is made once and stays fresh for the whole sweep.
        cls._seen = mock.patch.object(mcp, "SEEN_INTERVAL_SECONDS", 10.0**9)
        cls._seen.start()
        door.run_tool(cls.clean, "memory_search")
        shutil.copytree(cls.clean, cls.root, symlinks=True)
        cls.pristine = snapshot(cls.root)
        cls.answer_of = {}
        for which in cls.calls():
            cls.answer_of[label(which)] = cls._clean_answer(which)
        cls.sizes: dict[str, int] = {}
        cls.seconds: dict[str, float] = {}
        cls.left_for_merge: list[str] = []

    @classmethod
    def tearDownClass(cls) -> None:
        cls._seen.stop()
        cls._temporary.cleanup()

    @classmethod
    def calls(cls) -> list[tuple[str, ...] | str]:
        return [*door.agent_commands(), *door.agent_tools()]

    @classmethod
    def _restore(cls) -> None:
        shutil.rmtree(cls.root)
        shutil.copytree(cls.clean, cls.root, symlinks=True)

    @classmethod
    def _clean_answer(cls, which) -> Answer:
        answer = call(cls.root, which)
        if differences(cls.pristine, snapshot(cls.root), ignore=SEEN):
            cls._restore()
        return answer

    def _trace(self, which) -> tuple[system.Watch, list[bool]]:
        """A clean run: the door's calls and statements, and for each call whether intake made it."""
        intake_made: list[bool] = []
        enter = system._enter

        def counted(name, path, access):
            intake_made.append(_intake_on_stack())
            return enter(name, path, access)

        with mock.patch.object(system, "_enter", counted), system.watching() as watch:
            answer = call(self.root, which)
        self.assertFalse(answer.refused, (label(which), answer.raw))
        self.assertEqual(len(intake_made), len(watch.calls))
        if differences(self.pristine, snapshot(self.root), ignore=SEEN):
            self._restore()
        return watch, intake_made

    def _judge(self, which, injected: str, kind: str, never: tuple[str, ...], answer: Answer, seconds: float,
               watch: system.Watch, problems: list[str], *, intake_made: bool = False) -> None:
        where = f"{label(which)} {injected}={kind}"
        if not watch.injected:
            problems.append(f"{where}: the injected fault was not reached (the calls differ from the clean run)")
        if seconds > DEADLINE_SECONDS:
            problems.append(f"{where}: took {seconds:.1f} s")
        for text in (str(self.base), self.base.name, os.path.expanduser("~")):
            if text and text in answer.raw:
                problems.append(f"{where}: names a path of the machine: {answer.raw[:200]!r}")
        if answer.refused:
            code, move = answer.code, answer.next
            if code not in REFUSALS:
                problems.append(f"{where}: {code} is not a code of the table: {answer.raw[:300]!r}")
                return
            if code in ("internal_error", "tool_failed"):
                problems.append(f"{where}: a fault of the disk shows as {code}: {answer.raw[:300]!r}")
            if move not in MOVES or move != REFUSALS[code][0]:
                problems.append(f"{where}: {code} has the move {move!r}, the table says {REFUSALS[code][0]!r}")
            if move == "run":
                words = shlex.split(answer.command or "")
                if not words or words[0] != "forest":
                    problems.append(f"{where}: {code} runs {answer.command!r}")
            text = answer.message.casefold()
            for other in never:
                for phrase in SAYS[other]:
                    if phrase in text:
                        problems.append(f"{where}: {code} says {phrase!r} ({other}): {answer.message!r}")
            if kind == "other" and injected == "call" and "EIO" not in answer.raw:
                problems.append(f"{where}: {code} does not name the error: {answer.message!r}")
        after = snapshot(self.root)
        changed = differences(self.pristine, after, ignore=SEEN)
        if changed:
            if which in WRITERS:
                outside = [name for name in changed if not name.startswith((".forest", "06 istm"))]
                lane = [name for name in changed if name.startswith("06 istm")]
                if answer.refused and lane and not outside and intake_made:
                    self.left_for_merge.append(f"{where}: {answer.code} after the turn was written")
                elif outside or answer.refused and lane:
                    problems.append(f"{where}: {answer.code or 'a success'} changed {(outside or lane)[:3]}: "
                                    f"{answer.message[:160]!r}")
            elif which in BUILDERS:
                outside = [name for name in changed if not name.startswith(".forest")]
                if outside:
                    problems.append(f"{where}: a read changed {outside[:5]}")
            else:
                problems.append(f"{where}: a call that only reads changed {changed[:5]}")
            if which in BUILDERS and any(name.startswith(".forest") for name in changed):
                state = search.status(self.root).get("state")
                if state != "sound":
                    problems.append(f"{where}: left the index {state}")
            self._restore()

    def _sweep(self, which) -> tuple[int, list[str]]:
        problems: list[str] = []
        trace, intake_made = self._trace(which)
        runs = 0
        for number, (name, _path) in enumerate(trace.calls, start=1):
            for kind in system.CAN[name]:
                started = time.monotonic()
                with system.watching(fail_calls={number: kind}) as watch:
                    answer = call(self.root, which)
                seconds = time.monotonic() - started
                runs += 1
                self._judge(which, f"call {number} ({name} {_tail(_path)})", kind, NEVER_SAID[kind], answer, seconds,
                            watch, problems, intake_made=intake_made[number - 1])
        for number in range(1, trace.statements + 1):
            for kind in system.SQLITE_FAULTS:
                started = time.monotonic()
                with system.watching(fail_statements={number: kind}) as watch:
                    answer = call(self.root, which)
                seconds = time.monotonic() - started
                runs += 1
                self._judge(which, f"statement {number}", kind, SQLITE_NEVER_SAID[kind], answer, seconds, watch,
                            problems)
        return runs, problems

    def test_every_call_of_every_agent_call_fails_in_turn(self) -> None:
        everything: list[str] = []
        total = 0
        started = time.monotonic()
        for which in self.calls():
            with self.subTest(call=label(which)):
                began = time.monotonic()
                runs, problems = self._sweep(which)
                total += runs
                self.sizes[label(which)] = runs
                self.seconds[label(which)] = time.monotonic() - began
                everything.extend(problems)
        seconds = time.monotonic() - started
        sys.stderr.write(f"\nsweep: {total} runs in {seconds:.1f} s: "
                         + ", ".join(f"{name} {self.sizes[name]} ({self.seconds[name]:.1f} s)" for name in self.sizes)
                         + f"; {len(self.left_for_merge)} runs left for the merge (a fault inside intake's append)\n")
        self.assertEqual(everything[:40], [], f"{len(everything)} problems")


class EdgeTests(unittest.TestCase):
    def test_every_public_function_that_takes_a_root_passes_the_edge(self) -> None:
        found = 0
        for module in (search, timeline):
            for name, function in vars(module).items():
                if name.startswith("_") or not inspect.isfunction(function) or function.__module__ != module.__name__:
                    continue
                if "root" not in inspect.signature(function).parameters or function is search.edge_refusal:
                    continue
                found += 1
                with self.subTest(function=f"{module.__name__}.{name}"):
                    self.assertEqual(function.__code__.co_name, "boundary")
                    self.assertEqual(function.__code__.co_filename, search.__file__)
        self.assertGreater(found, 10)

    def _sweep_forest(self, settings_tail: str = "") -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve() / "forest"
        files = {**SWEEP_FILES, "forest.toml": SWEEP_FILES["forest.toml"] + settings_tail}
        for relative, text in files.items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text(text, encoding="utf-8")
        search.build(root)
        return root

    def test_a_tool_call_the_server_cannot_take_is_refused_by_its_kind(self) -> None:
        root = self._sweep_forest()
        refused = (
            ({"name": 7}, "Name a tool this server serves."),
            ({"name": "memory_bake"}, "Name a tool this server serves."),
            ({"name": "memory_search", "arguments": ["trays"]}, "Pass the tool's arguments as an object."),
        )
        for params, message in refused:
            with self.subTest(params=params):
                with self.assertRaises(mcp._BadArguments) as refusal:
                    mcp._call(root, READER, params, "stdio")
                self.assertEqual(refusal.exception.message, message)
        wrong = {"name": "memory_search", "arguments": {"words": "trays", "shelf": "upper"}}
        reply = mcp._call(root, READER, wrong, "stdio", "2025-11-25")  # a tool error from this version on
        answer = json.loads(reply["content"][0]["text"])
        self.assertEqual((reply["isError"], answer["code"], answer["next"]), (True, "arguments_invalid", "fix"))
        with self.assertRaises(mcp._BadArguments):
            mcp._call(root, READER, wrong, "stdio", "2025-06-18")
        searched = {"name": "memory_search", "arguments": {"words": "trays"}}
        with mock.patch.object(mcp, "_invoke", side_effect=mcp._BadArguments("Name a page this reader may read.")), \
                self.assertRaises(mcp._BadArguments):
            mcp._call(root, READER, searched, "stdio")

    def test_a_group_tool_answers_for_the_agent_it_names_and_drops_the_name_from_the_call(self) -> None:
        group = '\n[agents.known.till-bot]\nname = "Till Bot"\n\n[agents.groups.bakery-bots]\nmembers = ["till-bot"]\n' \
                'open = false\n'
        root = self._sweep_forest(group)
        called = {"name": "memory_search", "arguments": {"words": "trays", "agent": "till-bot"}}
        with mock.patch.object(mcp, "_invoke", wraps=mcp._invoke) as invoked:
            reply = mcp._call(root, "", called, "stdio", group="bakery-bots")
        self.assertFalse(reply["isError"], reply)
        (given_root, reader, name, arguments, _transport), _options = invoked.call_args
        self.assertEqual((reader, name, arguments), ("till-bot", "memory_search", {"words": "trays"}))

    def test_what_a_tool_meets_is_a_refusal_of_its_own_and_a_written_turn_is_never_told_unwritten(self) -> None:
        root = self._sweep_forest()
        page = root / "04 stm" / "bakery" / "ovens" / "floor.md"

        def answer(name: str, **arguments: str) -> tuple[bool, dict[str, Any]]:
            given = {**door.TOOL_ARGUMENTS[name], **arguments}
            reply = mcp._call(root, READER, {"name": name, "arguments": given}, "stdio")
            return reply["isError"], json.loads(reply["content"][0]["text"])

        met = {
            "a refusal": (MemoryForestError("search_empty", "Give the words to search for.", next_step="fix"),
                          "search_empty", "fix"),
            "an outcome of the door": (system.Other(page), "filesystem_error", "report"),
            "a fault of the code": (RuntimeError("a fault of the code"), "tool_failed", "report"),
        }
        for label, (raised, code, move) in met.items():
            with self.subTest(met=label), mock.patch.object(mcp, "_invoke", side_effect=raised):
                failed, body = answer("memory_search")
                self.assertEqual((failed, body["code"], body["next"]), (True, code, move))
        # The masking cannot be read while the answer leaves: a read is refused, with only the words of a fixed
        # vocabulary in its details; a turn already written says it was written, and what withheld the rest.
        unreadable = {
            "the settings": (MemoryForestError("settings_invalid", "forest.toml does not hold settings."),
                             "settings_invalid", {}),
            "the ledger": (system.Denied(root / ".forest" / "credentials" / "key", access="read"), "access_denied",
                           {"access": "read", "kind": "denied"}),
            "a fault of the code": (RuntimeError("a fault of the code"), "tool_failed", {}),
        }
        for label, (raised, code, details) in unreadable.items():
            with self.subTest(unreadable=label), mock.patch.object(search, "mask_result", side_effect=raised):
                failed, body = answer("memory_search")
                self.assertEqual((failed, body["code"], body["details"]), (True, code, details))
        for raised, code in ((unreadable["the settings"][0], "settings_invalid"),
                             (unreadable["the ledger"][0], "access_denied")):
            with self.subTest(written=code), mock.patch.object(search, "mask_result", side_effect=raised):
                failed, body = answer("memory_log", text=f"the till counted the trays, then {code}")
                self.assertEqual((failed, body["wrote"], body["withheld"]), (False, True, code))
                self.assertNotIn("path", body)
        said = io.StringIO()
        with mock.patch.object(mcp, "_remember", side_effect=system.Denied(root / ".forest" / "mcp", access="write")), \
                contextlib.redirect_stderr(said):
            failed, body = answer("memory_search")
        self.assertFalse(failed)
        self.assertIn("the seen record could not be written", said.getvalue())

    def test_the_boundary_of_a_public_function_turns_what_no_code_decided_on_into_a_refusal(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        built, unbuilt = base / "built", base / "unbuilt"
        for root in (built, unbuilt):
            for relative, text in SWEEP_FILES.items():
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                (root / relative).write_text(text, encoding="utf-8")
        search.build(built)
        kept = MemoryForestError("search_empty", "Give the words to search for.", next_step="fix")
        unclassified = sqlite3.OperationalError("no such function: proof_time")  # no code: read by its words
        damaged = sqlite3.DatabaseError("database disk image is malformed")
        damaged.sqlite_errorcode = sqlite3.SQLITE_CORRUPT  # type: ignore[attr-defined]

        def public(root=None, raised: BaseException = ValueError("a fault of the code")):
            raise raised

        boundary = search._public(public)

        def refused(*args: Any, **kwargs: Any) -> MemoryForestError:
            with self.assertRaises(MemoryForestError) as caught:
                boundary(*args, **kwargs)
            return caught.exception

        self.assertIs(refused(built, kept), kept)
        spent = refused(built, MemoryForestError("index_protocol", "SQLite's locking protocol failed.",
                                                 details={"path": ".forest/index.sqlite"}, waited_seconds=1.5))
        self.assertEqual((spent.code, spent.next_step, spent.details), ("index_io_error", "report",
                                                                         {"path": ".forest/index.sqlite"}))
        self.assertIn("this call already waited 1.5 seconds", spent.message)
        bare = refused(built, MemoryForestError("index_protocol", "SQLite's locking protocol failed."))
        self.assertEqual(bare.details, {"path": ""})
        self.assertIn("this call already waited 0.0 seconds", bare.message)
        for root, error, code, move, state in (
            (built, unclassified, "index_failed", "report", "failed"),
            (built, damaged, "index_unreadable", "run", "damaged"),
            (unbuilt, unclassified, "index_failed", "report", None),  # no index: the error by its own class
            (base / "absent", unclassified, "index_failed", "report", None),
        ):
            for by_name in (False, True):
                with self.subTest(root=root.name, error=str(error), by_name=by_name):
                    refusal = refused(root=root, raised=error) if by_name else refused(root, error)
                    self.assertEqual((refusal.code, refusal.next_step, refusal.details.get("state")),
                                     (code, move, state))
        page = system.Other(built / "04 stm" / "bakery" / "ovens" / "floor.md")
        for by_name in (False, True):
            outcome = refused(root=built, raised=page) if by_name else refused(built, page)
            self.assertEqual((outcome.code, outcome.details["path"]), ("filesystem_error", "04 stm/bakery/ovens/floor.md"))
        self.assertEqual(refused(raised=page).details["path"], "a path outside the forest")
        fault = refused()
        self.assertEqual((fault.code, fault.details), ("internal_error", {"reason": "ValueError"}))

    def test_the_forest_a_command_line_refusal_is_about(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        forest = base / "forest"
        for relative, text in SWEEP_FILES.items():
            (forest / relative).parent.mkdir(parents=True, exist_ok=True)
            (forest / relative).write_text(text, encoding="utf-8")
        (base / "empty").mkdir()
        self.assertEqual(cli._fold_root(argparse.Namespace(root=str(forest))), str(forest))
        self.assertEqual(cli._fold_root(argparse.Namespace(root=str(base / "empty"))), str(base / "empty"))
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        os.chdir(base)
        self.assertEqual(cli._fold_root(argparse.Namespace(root=None)), str(base))
        gone = base / "gone"
        gone.mkdir()
        os.chdir(gone)
        gone.rmdir()  # the working folder is gone: the refusal is about "."
        self.assertEqual(cli._fold_root(argparse.Namespace(root=None)), ".")
        os.chdir(here)

    def test_a_place_is_named_inside_the_forest_and_in_words_outside_it(self) -> None:
        root = "/forests/bakery"
        rows = {
            "/forests/bakery": "the forest folder",
            "/forests/bakery/": "the forest folder",
            "/forests/bakery/04 stm/bakery/ovens/floor.md": "04 stm/bakery/ovens/floor.md",
            "/forests/bakery/.forest/": ".forest",
            "/forests": "a folder above the forest folder",
            "/": "a folder above the forest folder",
            "/forests/bakery-annex/floor.md": "a path outside the forest",
            "/forests/bake": "a path outside the forest",
            "/orchard/floor.md": "a path outside the forest",
        }
        for path, shown in rows.items():
            with self.subTest(path=path):
                self.assertEqual(errors.shown_place(path, root), shown)
                self.assertEqual(errors.shown_place(path, root + "/"), shown)
        self.assertEqual(errors.shown_place("/forests/bakery/floor.md", None), "a path outside the forest")

    def test_an_outcome_no_code_decided_on_is_a_refusal_by_its_kind(self) -> None:
        root = "/forests/bakery"
        page, shown = f"{root}/04 stm/bakery/ovens/floor.md", "04 stm/bakery/ovens/floor.md"
        rows = [
            (system.Denied(page, access="read"), "access_denied", "report", shown,
             f"This account cannot read {shown}. The owner gives this account read access to it."),
            (system.Denied(f"{root}/04 stm", access="list"), "access_denied", "report", "04 stm",
             "This account cannot list 04 stm. The owner gives this account list access to it."),
            (system.Denied(page, access="enter", folder=f"{root}/04 stm"), "access_denied", "report", "04 stm",
             "This account cannot enter 04 stm. The owner gives this account enter access to it."),
            (system.Denied(page, access="write"), "access_denied", "report", shown,
             f"This account cannot write in {shown}. The owner gives this account write access to it."),
            (system.Denied(page), "access_denied", "report", shown,
             f"This account cannot read {shown}. The owner gives this account read access to it."),
            (system.Changed(page), "file_busy", "retry", shown,
             f"{shown} changed while it was used. The same call answers later."),
            (system.Busy(page), "file_busy", "retry", shown, f"{shown} was in use. The same call answers later."),
            (system.Absent(page), "file_busy", "retry", shown,
             f"{shown} went away while it was used. The same call answers later."),
            (system.Link(page), "symlink_forbidden", "report", shown,
             f"Symlinks are not allowed inside a Memory Forest; {shown} is one."),
            (system.NotAFile(page), "non_regular_file", "report", shown, f"{shown} is not a regular file."),
            (system.NotAFolder(page), "unsafe_existing_path", "report", shown, f"{shown} is not a folder."),
            (system.Other(page), "filesystem_error", "report", shown,
             f"{shown} could not be used: the system answered EIO. Tell the owner."),
            (OSError(errno.ENOSPC, "no space", page), "filesystem_error", "report", shown,
             f"{shown} could not be used: the system answered ENOSPC. Tell the owner."),
            (OSError(errno.ELOOP, "a link", page), "symlink_forbidden", "report", shown,
             f"Symlinks are not allowed inside a Memory Forest; {shown} is one."),
        ]
        for outcome, code, move, place, message in rows:
            with self.subTest(outcome=type(outcome).__name__, message=message):
                refusal = errors.door_refusal(outcome, root=root)
                self.assertEqual((refusal.code, refusal.next_step, refusal.message), (code, move, message))
                self.assertEqual(refusal.details["path"], place)
        self.assertEqual(errors.door_refusal(system.Denied(page), root=root).details,
                         {"kind": "denied", "path": shown, "access": "read"})
        self.assertEqual(errors.door_refusal(system.Other(page), root=root).details,
                         {"kind": "other", "path": shown, "error": "EIO"})
        self.assertEqual(errors.door_refusal(system.Busy(page), root=root).details, {"kind": "busy", "path": shown})
        self.assertEqual(errors.door_refusal(system.Link(page)).details["path"], "a path outside the forest")


def _public_calls(root: Path) -> dict[str, Any]:
    """The public functions an outside program calls on a forest, each with plain arguments."""
    return {
        "search.status": lambda: search.status(root),
        "search.query": lambda: search.query(root, "trays", reader=READER),
        "search.read": lambda: search.read(root, door.PAGE, reader=READER),
        "search.navigate": lambda: search.navigate(root, reader=READER),
        "search.build": lambda: search.build(root),
        "search.reset": lambda: search.reset(root),
        "timeline.query": lambda: timeline.query(root, "2051-03-02", reader=READER),
        "timeline.subject": lambda: timeline.subject(root, "till-bot", reader=READER),
    }


def _owner_command(root: Path, *words: str) -> Answer:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.forest_main([*words, "--root", str(root), "--json"])
    return Answer(out.getvalue() + err.getvalue(), code != 0, json.loads(out.getvalue()) if out.getvalue().strip() else {})


@unittest.skipIf(os.geteuid() == 0, "the superuser enters and lists every folder")
class ReviewRowTests(unittest.TestCase):
    """The review's rows on a real disk: one answer at every door, true, with the access to give."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name).resolve() / "forest"
        for relative, text in SWEEP_FILES.items():
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        search.build(self.root)
        self.addCleanup(self._temporary.cleanup)

    def _expect(self, answer: Answer, label: str, facts: derived.Facts) -> None:
        decision = derived.decide(facts, "read")
        with self.subTest(door=label):
            self.assertTrue(answer.refused, answer.raw)
            self.assertEqual((answer.code, answer.next), (decision.code, decision.move), answer.raw)
            self.assertEqual(answer.details.get("state"), decision.state, answer.raw)
            self.assertEqual(answer.message, derived.sentences(decision, facts))
            for untrue in ("no forest", "forest init", "does not name an index", "damaged"):
                self.assertNotIn(untrue, answer.message.casefold())

    def test_a_root_listed_and_not_entered_is_one_true_answer_at_every_door(self) -> None:
        before = snapshot(self.root)
        mode = stat.S_IMODE(os.lstat(self.root).st_mode)
        os.chmod(self.root, 0o444)
        try:
            answers = {f"forest {label(words)}": call(self.root, words) for words in door.agent_commands()}
            for words in (("index", "status"), ("index", "build"), ("index", "reset")):
                answers[f"forest {label(words)}"] = _owner_command(self.root, *words)
            answers.update({name: call(self.root, name) for name in door.agent_tools()})
            for name, function in _public_calls(self.root).items():
                with self.assertRaises(MemoryForestError) as caught:
                    function()
                refusal = caught.exception
                answers[name] = Answer(str(refusal), True, {"code": refusal.code, "next": refusal.next_step,
                                                            "message": str(refusal), "details": refusal.details})
        finally:
            os.chmod(self.root, mode)
        self.assertEqual(snapshot(self.root), before)
        # Opening the folder follows the read bit on this system, so 0444 is entered and then
        # forest.toml cannot be read. The execute bit alone is not what the act of entering asks.
        facts = derived.Facts(root_enter="ok", root_list="ok", root_write="no", settings="denied")
        self.assertGreater(len(answers), 20)
        for name, answer in answers.items():
            self._expect(answer, name, facts)

    def test_a_forest_dot_folder_written_and_entered_not_listed_refuses_a_build_and_a_reset(self) -> None:
        folder = self.root / ".forest"
        before = snapshot(self.root)
        mode = stat.S_IMODE(os.lstat(folder).st_mode)
        os.chmod(folder, 0o300)
        try:
            answers = {f"forest {' '.join(words)}": _owner_command(self.root, *words)
                       for words in (("index", "build"), ("index", "reset"))}
            for name in ("search.build", "search.reset"):
                with self.assertRaises(MemoryForestError) as caught:
                    _public_calls(self.root)[name]()
                refusal = caught.exception
                answers[name] = Answer(str(refusal), True, {"code": refusal.code, "next": refusal.next_step,
                                                            "message": str(refusal), "details": refusal.details})
        finally:
            os.chmod(folder, mode)
        self.assertEqual(snapshot(self.root), before)
        facts = derived.Facts(settings="ok", folder="folder", folder_enter="denied", folder_list="denied",
                              folder_write="no")
        for name, answer in answers.items():
            self._expect(answer, name, facts)


class OwnerFileTests(unittest.TestCase):
    """A fault in a file of the owner's has a code of its own with ``report`` on every door, apart from the
    code for the caller's own wrong argument."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name).resolve() / "forest"
        for relative, text in SWEEP_FILES.items():
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        search.build(self.root)
        self.addCleanup(self._temporary.cleanup)

    def _every_door(self) -> dict[str, Answer]:
        return {label(which): call(self.root, which) for which in [*door.agent_commands(), *door.agent_tools()]}

    def test_settings_that_do_not_parse_or_are_not_a_file(self) -> None:
        settings = self.root / "forest.toml"
        kept = settings.read_bytes()
        for fault, make in (("not settings", lambda: settings.write_bytes(b"[forest\n")),
                            ("not UTF-8", lambda: settings.write_bytes(b"\xff\xfe")),
                            ("a folder", lambda: (settings.unlink(), settings.mkdir()))):
            make()
            try:
                for name, answer in self._every_door().items():
                    with self.subTest(fault=fault, door=name):
                        self.assertEqual((answer.code, answer.next), ("settings_invalid", "report"), answer.raw)
            finally:
                if settings.is_dir():
                    settings.rmdir()
                settings.write_bytes(kept)

    def test_a_damaged_phrase_table_is_the_owners_and_a_phrase_no_table_knows_the_callers(self) -> None:
        tables = self.root / ".forest" / "phrases"
        tables.mkdir()
        (tables / "en.json").write_text("{", encoding="utf-8")
        for phrase, wanted in (("yesterday", ("time_phrase_table_invalid", "report")),):
            with mock.patch.dict(door.COMMAND_ARGUMENTS, {("timeline", "query"): ["timeline", "query", phrase]}), \
                    mock.patch.dict(door.TOOL_ARGUMENTS, {"memory_timeline": {"phrase": phrase}}):
                for which in (("timeline", "query"), "memory_timeline"):
                    with self.subTest(phrase=phrase, door=label(which)):
                        answer = call(self.root, which)
                        self.assertEqual((answer.code, answer.next), wanted, answer.raw)
        (tables / "en.json").unlink()
        phrase = "the day the oven sang"
        with mock.patch.dict(door.COMMAND_ARGUMENTS, {("timeline", "query"): ["timeline", "query", phrase]}), \
                mock.patch.dict(door.TOOL_ARGUMENTS, {"memory_timeline": {"phrase": phrase}}):
            for which in (("timeline", "query"), "memory_timeline"):
                with self.subTest(phrase=phrase, door=label(which)):
                    answer = call(self.root, which)
                    self.assertEqual((answer.code, answer.next), ("time_phrase_unknown", "fix"), answer.raw)

    def test_a_registry_file_that_is_not_a_file_is_the_owners(self) -> None:
        self.assertEqual(_owner_command(self.root, "registry", "build").refused, False)
        registry = self.root / ".forest" / "registry.json"
        registry.unlink()
        registry.mkdir()
        answer = _owner_command(self.root, "registry", "show", "till-bot")
        self.assertEqual((answer.code, answer.next), ("registry_unreadable", "report"), answer.raw)
        registry.rmdir()
        registry.write_text("{", encoding="utf-8")
        answer = _owner_command(self.root, "registry", "show", "till-bot")
        self.assertEqual((answer.code, answer.next, answer.command), ("registry_invalid", "run", "forest registry build"),
                         answer.raw)


if __name__ == "__main__":
    unittest.main()
