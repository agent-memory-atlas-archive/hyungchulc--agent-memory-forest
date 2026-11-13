"""The state of the derived folder: the facts, then a decision, then sentences that are data (``derived``).

Three parts. The decision is pure, so the first part walks every combination of facts ``impossible`` does
not remove, for every command, and checks each claim's condition. The second makes a forest for every
outcome of every step this system can produce and checks the facts read from it are exactly that,
naming why the system cannot produce the rest. The third carries out every claim that names an action,
once per kind of cure, on a forest left exactly as the facts say.

The bakery and the bots are invented. Nothing here is a real person, shop or transcript.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import threading
import unittest

import bounds  # installs a deadline for every unittest invocation
from pathlib import Path
from unittest import mock

from memory_forest import cli, derived, mcp, search, system
from memory_forest.errors import REFUSALS, MemoryForestError

from test_v1_find import FILES
from wait_child import read_fifo, write_fifo
from claim_witnesses import Evidence, WITNESSES, assert_cured

READER = "till-bot"
PAGE = "04 stm/bakery/ovens/floor.md"


def _all() -> list[derived.Facts]:
    return list(derived.combinations())


class DecisionTests(unittest.TestCase):
    """Every combination of facts, every command: a state, the same for every command, and true claims."""

    @classmethod
    def setUpClass(cls) -> None:
        started = time.monotonic()
        cls.combinations = _all()
        cls.decisions = {(facts, command): derived.decide(facts, command)
                         for facts in cls.combinations for command in derived.COMMANDS}
        cls.seconds = time.monotonic() - started

    def test_the_walk_is_whole_and_well_under_a_second(self) -> None:
        self.assertEqual(derived.facts_fields(), tuple(derived.STEPS))
        self.assertGreater(len(self.combinations), 1000)
        self.assertEqual(len(set(self.combinations)), len(self.combinations))
        self.assertLess(self.seconds, 1.0, f"{len(self.combinations)} combinations took {self.seconds:.2f} s")

    def test_every_combination_has_one_state_whatever_the_command(self) -> None:
        for facts in self.combinations:
            state = derived.state_of(facts)
            self.assertIn(state, derived.STATES)
            for command in derived.COMMANDS:
                self.assertEqual(self.decisions[(facts, command)].state, state, (facts, command))

    def test_every_state_is_reached(self) -> None:
        self.assertEqual({derived.state_of(facts) for facts in self.combinations}, set(derived.STATES))

    def test_every_claim_said_holds(self) -> None:
        self.assertEqual(set(WITNESSES), set(derived.CLAIMS))
        said: set[str] = set()
        for (facts, command), decision in self.decisions.items():
            for key in decision.claims:
                said.add(key)
                self.assertTrue(derived.CLAIMS[key].holds(facts), (key, facts, command))
        self.assertEqual(sorted(set(derived.CLAIMS) - said), [], "a claim no decision says")

    def test_every_move_obeys_the_table_and_every_command_parses(self) -> None:
        parser = cli.build_forest_parser()
        for (facts, command), decision in self.decisions.items():
            if decision.code is not None:
                self.assertIn(decision.code, derived.CODES)
                self.assertIn(decision.move, derived.CODES[decision.code])
                self.assertEqual(REFUSALS[decision.code][0], decision.move, decision.code)
            else:
                self.assertIn(decision.move, (None, "run", "report", "retry"))
                if command != "status":
                    self.assertIsNone(decision.move, (facts, command))
            if decision.move == "run":
                self.assertIn(decision.command, (derived.BUILD_COMMAND, derived.RESET_COMMAND))
                words = shlex.split(decision.command)
                self.assertEqual(words[0], "forest")
                parsed = parser.parse_args(words[1:])
                self.assertTrue(callable(parsed._run))
                self.assertTrue(any(derived.CLAIMS[key].action[:1] == ("run",) for key in decision.claims))
            else:
                self.assertIsNone(decision.command)

    def test_each_rule_of_impossible_removes_a_combination(self) -> None:
        examples = {
            "reached_after_a_stop": derived.Facts(root_enter="denied", root_write="no", settings="ok"),
            "skipped_though_reached": derived.Facts(),
            "root_write_without_enter": derived.Facts(root_enter="denied", root_write="yes"),
            "folder_write_without_enter": derived.Facts(
                settings="ok", folder="folder", folder_enter="denied", folder_list="ok", folder_write="yes",
            ),
        }
        self.assertEqual(set(examples), set(derived.RULES))
        for rule, facts in examples.items():
            with self.subTest(rule=rule):
                self.assertEqual(derived.impossible(facts), rule)
                self.assertNotIn(facts, self.combinations)

    def test_the_review_rows(self) -> None:
        base = dict(settings="ok", folder="folder", folder_enter="ok", folder_list="ok", folder_write="yes",
                    pointer="absent", generation="file", beside="none", open="ok", metadata="equal")
        rows = {
            "root listed, not entered": (derived.Facts(root_enter="denied", root_list="ok", root_write="no"),
                                         "root_unenterable", "access_denied", "report"),
            ".forest listed, not entered": (derived.Facts(settings="ok", folder="folder", folder_enter="denied",
                                                          folder_list="ok", folder_write="no"),
                                            "folder_unenterable", "access_denied", "report"),
            ".forest written and entered, not listed": (
                derived.Facts(settings="ok", folder="folder", folder_enter="ok", folder_list="denied", folder_write="yes"),
                "folder_unlistable", "access_denied", "report"),
            "an index of another version": (derived.Facts(**{**base, "metadata": "other_version"}), "missing", None, "run"),
            "the pointer cannot be read": (derived.Facts(**{**base, "folder_write": "no", "pointer": "denied",
                                                           "generation": derived.NR, "beside": derived.NR,
                                                           "open": derived.NR, "metadata": derived.NR}),
                                           "pointer_unreadable", "access_denied", "report"),
        }
        for label, (facts, state, code, move) in rows.items():
            with self.subTest(row=label):
                self.assertIsNone(derived.impossible(facts))
                decision = derived.decide(facts, "status")
                self.assertEqual((decision.state, decision.code, decision.move), (state, code, move))
                text = derived.sentences(decision, facts).casefold()
                self.assertNotIn("no forest", text)
                self.assertNotIn("does not name an index file", text)
        held = derived.Facts(**{**base, "changes": "some", "lock": "held"})
        decision = derived.decide(held, "status")
        self.assertEqual((decision.code, decision.move), (None, "retry"))
        self.assertIn("lock_held", decision.claims)
        other = derived.decide(derived.Facts(**{**base, "metadata": "other_version"}), "status")
        self.assertIn("other_version", other.claims)
        self.assertNotIn("no_index", other.claims)

    def test_the_two_folder_words_are_two_claims(self) -> None:
        for facts in self.combinations:
            for key in ("folder_unwritable", "writer_build_folder", "writer_reset_folder"):
                if derived.CLAIMS[key].holds(facts):
                    self.assertEqual(facts.folder, "folder")
            for key in ("root_unwritable", "writer_build_root"):
                if derived.CLAIMS[key].holds(facts):
                    self.assertEqual(facts.folder, "absent")


NR = derived.NR
BUILD = derived.BUILD_COMMAND
RESET = derived.RESET_COMMAND
INDEX = {"pointer": "name", "generation": "file", "beside": "none", "open": "ok"}
GO = (None, None, None, (), False)
BUILD_FIRST = (None, None, None, (), True)


def inside(write: str, *, lock: str = "free", changes: str | None = None, **steps: str) -> derived.Facts:
    """Facts inside a ``.forest`` this account can enter and list, and can (``yes``) or cannot write: the steps
    given, every other step from the pointer on not reached (``changes`` is read when the metadata is equal)."""
    values = {step: NR for step in ("pointer", "generation", "beside", "open", "metadata")}
    values.update(steps)
    if changes is None:
        changes = "none" if values["metadata"] == "equal" else NR
    return derived.Facts(settings="ok", folder="folder", folder_enter="ok", folder_list="ok", folder_write=write,
                         changes=changes, lock=lock, **values)


def refuse(code: str, move: str, *claims: str, command: str | None = None) -> tuple:
    return (code, move, command, claims, False)


def every(expected: tuple, **commands: tuple) -> dict[str, tuple]:
    """The same answer for every command, but those named."""
    return {command: commands.get(command, expected) for command in derived.COMMANDS}


def run_reset(what: str) -> dict[str, tuple]:
    """A damaged index or pointer in a folder this account can write: every command but reset runs the reset."""
    return every(refuse("index_unreadable", "run", what, "run_reset", command=RESET), reset=GO)


def busy_reset(what: str) -> dict[str, tuple]:
    """The same with the index lock held: a reset proceeds (it waits for the lock); the rest retry."""
    return every(refuse("index_busy", "retry", what, "lock_held", "ask_later"), reset=GO)


def builds(what: str) -> dict[str, tuple]:
    """No index to answer from, in a folder this account can write: a read builds first; status runs a build."""
    return every(GO, read=BUILD_FIRST, status=(None, "run", BUILD, (what, "run_build"), False))


def read_only(what: str, folder: str, cure: str) -> dict[str, tuple]:
    """A folder this account cannot write: report, naming what an account that can write runs."""
    return every(refuse("index_read_only", "report", what, folder, "reads_refused", cure))


def busy(what: str) -> dict[str, tuple]:
    return every(refuse("index_busy", "retry", what, "ask_later"))


# Each row: what the facts are, the state, and what each command does there, written out by hand. A change
# of the decision fails a row here, whatever the invariants above still accept.
DECISIONS: list[tuple[str, derived.Facts, str, dict[str, tuple]]] = [
    ("nothing at the path", derived.Facts(root="absent", root_enter=NR, root_list=NR, root_write=NR),
     "root_absent", every(refuse("root_not_found", "fix", "root_absent"))),
    ("a file above the path", derived.Facts(root="above_file", root_enter=NR, root_list=NR, root_write=NR),
     "root_above_file", every(refuse("root_above_file", "fix", "root_above_file"))),
    ("a folder above cannot be entered",
     derived.Facts(root="above_denied", root_enter=NR, root_list=NR, root_write=NR),
     "root_above_denied", every(refuse("access_denied", "report", "root_above_denied", "give_root_above_enter"))),
    ("the path is not a folder", derived.Facts(root="not_a_folder", root_enter=NR, root_list=NR, root_write=NR),
     "root_not_folder", every(refuse("root_not_directory", "fix", "root_not_folder"))),
    ("the root cannot be entered", derived.Facts(root_enter="denied", root_write="no"), "root_unenterable",
     every(refuse("access_denied", "report", "root_unenterable", "give_root_enter"))),
    ("the root cannot be listed", derived.Facts(root_list="denied"), "root_unlistable",
     every(refuse("access_denied", "report", "root_unlistable", "give_root_list"))),
    ("forest.toml cannot be read", derived.Facts(settings="denied"), "settings_unreadable",
     every(refuse("access_denied", "report", "settings_denied", "give_settings_read"))),
    ("forest.toml is a link", derived.Facts(settings="link"), "settings_invalid",
     every(refuse("settings_invalid", "report", "settings_link", "owner_mends_settings"))),
    ("forest.toml is a folder", derived.Facts(settings="not_a_file"), "settings_invalid",
     every(refuse("settings_invalid", "report", "settings_not_a_file", "owner_mends_settings"))),
    ("forest.toml does not parse", derived.Facts(settings="invalid"), "settings_invalid",
     every(refuse("settings_invalid", "report", "settings_invalid", "owner_mends_settings"))),
    ("forest.toml met an error", derived.Facts(settings="other"), "settings_invalid",
     every(refuse("settings_invalid", "report", "settings_failed", "owner_mends_settings", "tell_owner"))),
    ("forest.toml is busy", derived.Facts(settings="busy"), "busy", busy("settings_busy")),
    (".forest is a link", derived.Facts(settings="ok", folder="link"), "folder_unusable",
     every(refuse("symlink_forbidden", "report", "folder_link", "owner_mends_folder"))),
    (".forest is a file", derived.Facts(settings="ok", folder="not_a_folder"), "folder_unusable",
     every(refuse("index_cannot_open", "report", "folder_not_a_folder", "owner_mends_folder"))),
    (".forest cannot be looked at", derived.Facts(settings="ok", folder="denied"), "folder_unusable",
     every(refuse("filesystem_error", "report", "folder_failed", "owner_mends_folder", "tell_owner"))),
    (".forest met an error", derived.Facts(settings="absent", folder="other"), "folder_unusable",
     every(refuse("filesystem_error", "report", "folder_failed", "owner_mends_folder", "tell_owner"))),
    (".forest cannot be entered", derived.Facts(settings="ok", folder="folder", folder_enter="denied",
                                                folder_list="ok", folder_write="no"), "folder_unenterable",
     every(refuse("access_denied", "report", "folder_unenterable", "give_folder_enter"))),
    (".forest cannot be listed", derived.Facts(settings="ok", folder="folder", folder_enter="ok",
                                               folder_list="denied", folder_write="yes"), "folder_unlistable",
     every(refuse("access_denied", "report", "folder_unlistable", "give_folder_list"))),
    # No index to answer from.
    ("no .forest, the root can be written", derived.Facts(settings="ok", folder="absent"), "missing",
     builds("no_index")),
    ("no .forest and no settings", derived.Facts(settings="absent", folder="absent"), "missing",
     builds("no_index")),
    ("no .forest, the root cannot be written", derived.Facts(root_write="no", settings="ok", folder="absent"),
     "missing", read_only("no_index", "root_unwritable", "writer_build_root")),
    ("no index file", inside("yes", pointer="absent", generation="absent"), "missing", builds("no_index")),
    ("no index file, the lock held", inside("yes", lock="held", pointer="absent", generation="absent"), "missing",
     every(GO, read=BUILD_FIRST, status=(None, "retry", None, ("no_index", "lock_held", "ask_later"), False))),
    ("no index file, .forest cannot be written", inside("no", pointer="name", generation="absent"), "missing",
     read_only("no_index", "folder_unwritable", "writer_build_folder")),
    ("another version's index", inside("yes", **INDEX, metadata="other_version"), "missing",
     builds("other_version")),
    ("another version's index, read only", inside("no", **INDEX, metadata="other_version"), "missing",
     read_only("other_version", "folder_unwritable", "writer_build_folder")),
    ("a first build not committed", inside("yes", **INDEX, metadata="not_committed"), "missing",
     builds("not_committed")),
    ("the masking changed", inside("yes", **INDEX, metadata="masking_changed"), "masking_changed",
     builds("masking_changed")),
    ("the masking changed, the lock held", inside("yes", lock="held", **INDEX, metadata="masking_changed"),
     "masking_changed",
     every(GO, read=BUILD_FIRST, status=(None, "retry", None, ("masking_changed", "lock_held", "ask_later"), False))),
    ("no masking fingerprint, read only", inside("no", **INDEX, metadata="masking_missing"), "masking_changed",
     read_only("masking_missing", "folder_unwritable", "writer_build_folder")),
    # A damaged index or pointer.
    ("the pointer cannot be read", inside("yes", pointer="denied"), "pointer_unreadable",
     run_reset("pointer_denied")),
    ("the pointer cannot be read, the lock held", inside("yes", lock="held", pointer="denied"),
     "pointer_unreadable", busy_reset("pointer_denied")),
    ("the pointer cannot be read, read only", inside("no", pointer="denied"), "pointer_unreadable",
     every(refuse("access_denied", "report", "pointer_denied", "give_pointer_read"))),
    ("the pointer names nothing", inside("yes", pointer="damaged"), "pointer_damaged",
     run_reset("pointer_damaged")),
    ("the pointer names nothing, read only", inside("no", pointer="damaged"), "pointer_damaged",
     read_only("pointer_damaged", "folder_unwritable", "writer_reset_folder")),
    ("the pointer is a link", inside("yes", pointer="link"), "pointer_unusable",
     every(refuse("symlink_forbidden", "report", "pointer_link", "owner_moves_pointer"))),
    ("the pointer is a folder", inside("no", pointer="not_a_file"), "pointer_unusable",
     every(refuse("index_cannot_open", "report", "pointer_not_a_file", "owner_moves_pointer"))),
    ("the pointer met an error", inside("yes", pointer="other"), "pointer_unusable",
     every(refuse("filesystem_error", "report", "pointer_failed", "owner_moves_pointer", "tell_owner"))),
    ("the pointer is busy", inside("yes", pointer="busy"), "busy", busy("pointer_busy")),
    ("the index is a link", inside("yes", pointer="name", generation="link"), "index_unusable",
     run_reset("index_link")),
    ("the index is a link, read only", inside("no", pointer="name", generation="link"), "index_unusable",
     read_only("index_link", "folder_unwritable", "writer_reset_folder")),
    ("the index is a folder, read only", inside("no", pointer="absent", generation="not_a_file"),
     "index_unusable", read_only("index_not_a_file", "folder_unwritable", "writer_reset_folder")),
    ("the index cannot be looked at", inside("yes", pointer="name", generation="denied"), "index_unusable",
     run_reset("index_denied")),
    ("the index cannot be looked at, read only", inside("no", pointer="name", generation="denied"),
     "index_unusable", every(refuse("access_denied", "report", "index_denied", "give_index_read"))),
    ("the index met an error, read only", inside("no", pointer="name", generation="other"), "index_unusable",
     every(refuse("filesystem_error", "report", "index_failed", "tell_owner"))),
    ("something not a file beside", inside("yes", pointer="name", generation="file", beside="not_a_file"),
     "index_unusable", run_reset("beside_not_a_file")),
    ("something not a file beside, read only", inside("no", pointer="name", generation="file",
                                                      beside="not_a_file"),
     "index_unusable", read_only("beside_not_a_file", "folder_unwritable", "writer_reset_folder")),
    ("a name beside cannot be looked at, read only", inside("no", pointer="name", generation="file",
                                                            beside="failed"),
     "index_unusable", every(refuse("filesystem_error", "report", "beside_failed", "tell_owner"))),
    ("the index cannot be opened", inside("yes", pointer="name", generation="file", beside="none", open="denied"),
     "index_unusable", run_reset("index_denied")),
    ("the index cannot be opened, read only", inside("no", pointer="name", generation="file", beside="none",
                                                     open="denied"),
     "index_unusable", every(refuse("access_denied", "report", "index_denied", "give_index_read"))),
    ("the open met an error", inside("yes", pointer="name", generation="file", beside="none", open="other"),
     "index_unusable", run_reset("index_failed")),
    ("the open met an error, read only", inside("no", pointer="name", generation="file", beside="none",
                                                open="other"),
     "index_unusable", every(refuse("filesystem_error", "report", "index_failed", "tell_owner"))),
    ("a damaged index", inside("yes", **INDEX, metadata="damaged"), "damaged", run_reset("damaged")),
    ("a damaged index, the lock held", inside("yes", lock="held", **INDEX, metadata="damaged"), "damaged",
     busy_reset("damaged")),
    ("a damaged index, read only", inside("no", **INDEX, metadata="damaged"), "damaged",
     read_only("damaged", "folder_unwritable", "writer_reset_folder")),
    # A log beside the index.
    ("a write-ahead log, read only", inside("no", pointer="name", generation="file", beside="wal"), "wal",
     read_only("wal", "folder_unwritable", "writer_build_folder")),
    ("a rollback journal, read only", inside("no", pointer="name", generation="file", beside="journal"), "wal",
     read_only("journal", "folder_unwritable", "writer_build_folder")),
    ("a write-ahead log in a folder that can be written",
     inside("yes", pointer="name", generation="file", beside="wal", open="ok", metadata="equal"), "sound",
     every(GO)),
    # Busy.
    ("the index is busy", inside("yes", pointer="name", generation="file", beside="none", open="busy"), "busy",
     busy("index_busy")),
    ("the open did not come back", inside("yes", pointer="name", generation="file", beside="none", open="deadline"),
     "busy", busy("index_deadline")),
    ("an earlier open has not come back",
     inside("yes", pointer="name", generation="file", beside="none", open="abandoned"), "abandoned",
     every(refuse("index_open_abandoned", "report", "index_abandoned"))),
    ("the look of the index was refused for enter",
     inside("yes", pointer="name", generation="enter_denied"), "index_unusable",
     every(refuse("access_denied", "report", "index_enter_denied", "give_index_enter"))),
    ("a file beside the index cannot be read",
     inside("no", pointer="name", generation="file", beside="unreadable"), "index_unusable",
     every(refuse("access_denied", "report", "beside_unreadable", "give_beside_read", "writer_reset_folder"))),
    ("the index changed while opened", inside("no", pointer="name", generation="file", beside="none",
                                              open="changed"), "busy", busy("index_changed")),
    ("the metadata is busy", inside("yes", **INDEX, metadata="busy"), "busy", busy("index_busy")),
    # SQLite failed in a way no command of this caller cures.
    *[(f"SQLite answered {kind}", inside("yes", **INDEX, metadata=kind), "failed",
       every(refuse(code, "report", claim, "tell_owner")))
      for kind, code, claim in (
          ("locked", "index_failed", "sqlite_locked"), ("read_only", "index_failed", "sqlite_read_only"),
          ("io", "index_io_error", "sqlite_io"), ("protocol", "index_io_error", "sqlite_protocol"),
          ("full", "index_disk_full", "sqlite_full"), ("cannot_open", "index_cannot_open", "sqlite_cannot_open"),
          ("other", "index_failed", "sqlite_other"))],
    # Sound.
    ("sound", inside("yes", **INDEX, metadata="equal"), "sound", every(GO)),
    ("sound, pages changed", inside("yes", changes="some", **INDEX, metadata="equal"), "sound",
     every(GO, status=(None, "run", BUILD, ("pages_changed", "run_build"), False))),
    ("sound, pages changed, the lock held", inside("yes", lock="held", changes="some", **INDEX, metadata="equal"),
     "sound", every(GO, status=(None, "retry", None, ("pages_changed", "lock_held", "ask_later"), False))),
    ("sound, read only", inside("no", **INDEX, metadata="equal"), "sound",
     every(refuse("index_read_only", "report", "folder_unwritable", "reads_answer", "writer_build_folder"),
           read=GO, status=GO,
           reset=refuse("index_read_only", "report", "folder_unwritable", "reads_answer", "writer_reset_folder"))),
    ("sound, read only, pages changed", inside("no", changes="some", **INDEX, metadata="equal"), "sound",
     every(refuse("index_read_only", "report", "folder_unwritable", "reads_answer", "writer_build_folder"),
           read=GO,
           status=(None, "report", None, ("pages_changed", "folder_unwritable", "reads_answer",
                                          "writer_build_folder"), False),
           reset=refuse("index_read_only", "report", "folder_unwritable", "reads_answer", "writer_reset_folder"))),
]


class DecisionTableTests(unittest.TestCase):
    """The decision, row by row, against answers written out by hand."""

    def test_every_row(self) -> None:
        for label, facts, state, answers in DECISIONS:
            self.assertIsNone(derived.impossible(facts), label)
            for command, (code, move, run, claims, build_first) in answers.items():
                with self.subTest(row=label, command=command):
                    decision = derived.decide(facts, command)
                    self.assertEqual(
                        (decision.state, decision.code, decision.move, decision.command, decision.claims,
                         decision.build_first),
                        (state, code, move, run, claims, build_first),
                    )

    def test_the_rows_reach_every_state_and_every_code(self) -> None:
        self.assertEqual({state for _label, _facts, state, _answers in DECISIONS}, set(derived.STATES))
        codes = {answer[0] for *_rest, answers in DECISIONS for answer in answers.values()} - {None}
        self.assertEqual(codes, set(derived.CODES))

    def test_a_command_or_a_step_the_decision_does_not_know_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            derived.decide(inside("yes", **INDEX, metadata="equal"), "index everything")
        with self.assertRaises(ValueError):
            derived._reached(derived.Facts(), "the oven's dial")

    def test_writable_is_the_folder_the_index_lives_in(self) -> None:
        """The ``.forest`` folder when it is there, the root while it is not, and nothing when it is neither."""
        rows = [
            (derived.Facts(settings="ok", folder="folder", folder_enter="ok", folder_list="ok", folder_write="yes"), True),
            (derived.Facts(settings="ok", folder="folder", folder_enter="ok", folder_list="ok", folder_write="no",
                           root_write="yes"), False),
            (derived.Facts(settings="ok", folder="absent"), True),
            (derived.Facts(settings="ok", folder="absent", root_write="no"), False),
            *[(derived.Facts(settings="ok", folder=fault), False) for fault in ("link", "not_a_folder", "denied", "other")],
        ]
        for facts, expected in rows:
            with self.subTest(folder=facts.folder, folder_write=facts.folder_write, root_write=facts.root_write):
                self.assertEqual(derived.writable(facts), expected)

    def test_the_sentences_fill_in_the_count_and_the_error(self) -> None:
        changed = inside("no", changes="some", **INDEX, metadata="equal")
        decision = derived.decide(changed, "status")
        for count, said in ((1, "1 file changed since the build."), (3, "3 files changed since the build.")):
            self.assertTrue(derived.sentences(decision, dataclasses.replace(changed, count=count)).startswith(said))
        failed = derived.Facts(settings="ok", folder="other")
        decision = derived.decide(failed, "read")
        self.assertIn("could not be looked at (EIO).", derived.sentences(decision, dataclasses.replace(failed, error="EIO")))
        self.assertIn("could not be looked at (an error the system did not name).", derived.sentences(decision, failed))
        self.assertEqual(derived.sentences(derived.Decision("sound"), changed), "")

    def test_the_walk_has_its_size(self) -> None:
        # The number of combinations ``impossible`` leaves: a change of a step's values or of a rule changes it.
        self.assertEqual(len(list(derived.combinations())), 2792)


# ─────────────────────────── the facts, read from a real disk ───────────────────────────

HOLDER = r"""
import fcntl, os, sys
descriptor = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(descriptor, fcntl.LOCK_EX)
with open(sys.argv[2], "w") as handshake:
    handshake.write("held\n")
sys.stdin.read()
"""

EXCLUSIVE = r"""
import sqlite3, sys
connection = sqlite3.connect(sys.argv[1], isolation_level=None)
connection.execute("PRAGMA locking_mode = EXCLUSIVE")
connection.execute("BEGIN EXCLUSIVE")
with open(sys.argv[2], "w") as handshake:
    handshake.write("held\n")
sys.stdin.read()
"""

# Every entry tries the nearest reproducible state on a fresh real forest.
from fact_probes import attempt, RACE_PROBES, metadata_locked, metadata_full

NOT_PRODUCED = {
    (step, wanted): attempt(step, wanted, nearest)
    for step, wanted, nearest in (
        ("settings", "busy", "not_a_file"), ("settings", "other", "invalid"),
        ("folder", "denied", ("root_enter", "denied")), ("folder", "other", "not_a_folder"),
        ("pointer", "busy", "not_a_file"), ("pointer", "other", "damaged"),
        ("generation", "denied", ("open", "denied")),
        ("generation", "other", "not_a_file"), ("beside", "failed", ("folder_enter", "denied")),
        ("open", "other", "denied"), ("open", "busy", "deadline"),
        ("metadata", "locked", "equal"), ("metadata", "full", "equal"),
        ("metadata", "protocol", "busy"), ("metadata", "other", "damaged"),
    )
}

NOT_PRODUCED[("metadata", "locked")] = metadata_locked
NOT_PRODUCED[("metadata", "full")] = metadata_full


class FactsOnDiskTests(unittest.TestCase):
    """For every outcome of every step this system can produce, the forest made that way gives that fact."""

    @classmethod
    def setUpClass(cls) -> None:
        if os.geteuid() == 0:
            raise unittest.SkipTest("the superuser enters, lists and writes every folder")
        cls._temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls._temporary.name).resolve()
        cls.built = cls.base / "built"
        cls.built.mkdir(mode=0o700)
        for relative, text in FILES.items():
            target = cls.built / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        search.build(cls.built)
        cls.count = 0

    @classmethod
    def tearDownClass(cls) -> None:
        for child in cls.base.iterdir():
            try:
                os.chmod(child, 0o700)
            except OSError:
                pass
        for path in cls.base.rglob("*"):
            if path.is_dir() and not path.is_symlink():
                os.chmod(path, 0o700)
        os.chmod(cls.base, 0o700)
        cls._temporary.cleanup()

    def _above_file(self, root: Path) -> Path:
        path = root.parent / f"file-{root.name}"
        path.write_text("x", encoding="utf-8")
        return path / "forest"

    def _above_denied(self, root: Path) -> Path:
        blocked = root.parent / f"blocked-{root.name}"
        blocked.mkdir()
        self.addCleanup(lambda: os.chmod(blocked, 0o700))
        os.chmod(blocked, 0)
        return blocked / "forest"

    def _not_a_folder(self, root: Path) -> Path:
        path = root.parent / f"notdir-{root.name}"
        path.write_text("x", encoding="utf-8")
        return path

    def _forest(self) -> Path:
        type(self).count += 1
        root = self.base / f"forest-{self.count}"
        shutil.copytree(self.built, root, symlinks=True)
        self.addCleanup(self._open_up, root)
        return root

    @staticmethod
    def _open_up(root: Path) -> None:
        if not root.exists():
            return
        os.chmod(root, 0o700)
        for path in [root / ".forest", root / "forest.toml", root / ".forest" / "index.current",
                     root / ".forest" / "index.sqlite"]:
            if path.exists() and not path.is_symlink():
                os.chmod(path, 0o700 if path.is_dir() else 0o600)

    def _facts(self, root: Path, **options) -> derived.Facts:
        info, _ = __import__("claim_witnesses").tried(os.stat, root / ".forest/index.sqlite")
        identity = (info.st_dev, info.st_ino) if info is not None else None
        started = time.monotonic()
        reading = search._facts(root, changes=lambda found: search._stale_files(root, found.index.connection),
                                check_lock=True, **options)
        reading.close()
        evidence = Evidence(root, self.built, identity, time.monotonic() - started)
        for command in derived.COMMANDS:
            for key in derived.decide(reading.facts, command).claims:
                self.assertTrue(evidence.check(key), (key, command, reading.facts))
        return reading.facts

    def _held(self, script: str, *args: str):
        handshake = self.base / f"handshake-{self.count}-{len(args)}"
        os.mkfifo(handshake)
        child = subprocess.Popen([sys.executable, "-c", script, *args, str(handshake)], stdin=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)

        def release() -> None:
            if child.stdin is not None:
                child.stdin.close()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
            if child.stderr is not None:
                child.stderr.close()

        self.addCleanup(release)
        read_fifo(child, str(handshake), what="the child to hold its lock")
        return child

    def _cases(self):
        """``(step, value, make)``: ``make(root)`` leaves the forest so; it may return options for the read."""
        folder = lambda root: root / ".forest"  # noqa: E731
        index = lambda root: root / ".forest" / "index.sqlite"  # noqa: E731
        pointer = lambda root: root / ".forest" / "index.current"  # noqa: E731

        def chmod(path_of, mode):
            return lambda root: os.chmod(path_of(root), mode)

        def sql(statement):
            def make(root):
                database = sqlite3.connect(index(root))
                try:
                    database.execute(statement)
                    database.commit()
                finally:
                    database.close()
            return make

        def replace(path_of, kind):
            def make(root):
                path = path_of(root)
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                if kind == "folder":
                    path.mkdir()
                elif kind == "fifo":
                    os.mkfifo(path)
                elif kind == "file":
                    path.write_text("a file\n", encoding="utf-8")
                elif kind == "link":
                    target = root.parent / f"elsewhere-{root.name}-{path.name}"
                    if path_of is folder:
                        target.mkdir()
                    else:
                        target.write_text("index.sqlite\n", encoding="utf-8")
                    path.symlink_to(target)
            return make

        def write(path_of, data):
            return lambda root: path_of(root).write_bytes(data)

        def exclusive(deadline):
            def make(root):
                self._held(EXCLUSIVE, str(index(root)))
                return {"deadline": deadline}
            return make

        def abandoned(root):
            ended = threading.Event()
            clear = system._clear_abandoned
            def cleared(path):
                clear(path)
                ended.set()
            self.enterContext(mock.patch.object(system, "_clear_abandoned", cleared))
            self.addCleanup(lambda: self.assertTrue(ended.wait(10), "abandoned open never ended"))
            self._held(EXCLUSIVE, str(index(root)))
            with self.assertRaises(system.Busy):
                system.open_index(index(root), immutable=False, first=lambda db: db.execute("SELECT * FROM metadata"),
                                  busy_seconds=30, deadline_seconds=0.02)

        def generation_enter_denied(root):
            original = system.look
            changed = False
            def looked(path):
                nonlocal changed
                if Path(path) == index(root) and not changed:
                    changed = True
                    os.chmod(folder(root), 0o600)
                return original(path)
            self.enterContext(mock.patch.object(system, "look", looked))

        def swapped(root):
            return {"swap": True}

        def lock_held(root):
            locks = root / ".forest" / "locks"
            locks.mkdir(exist_ok=True)
            self._held(HOLDER, str(locks / "index.lock"))

        def page_changed(root):
            page = root / PAGE
            page.write_text(page.read_text(encoding="utf-8") + "\nA new line.\n", encoding="utf-8")

        settings = lambda root: root / "forest.toml"  # noqa: E731
        # Every mode of the forest folder and of .forest by its owner's three bits: what each step reads there.
        # The dot lookup proves search; a separate listing proves read. Neither is os.access.
        modes = {
            0o700: ("ok", "ok", "yes"), 0o600: ("denied", "ok", "no"), 0o500: ("ok", "ok", "no"),
            0o400: ("denied", "ok", "no"), 0o300: ("ok", "denied", "yes"), 0o100: ("ok", "denied", "no"),
            0o200: ("denied", "denied", "no"), 0o000: ("denied", "denied", "no"),
        }
        return [
            *[
                (step, value, chmod(lambda root: root, mode))
                for mode, values in modes.items()
                for step, value in zip(("root_enter", "root_list", "root_write"), values)
            ],
            ("root", "folder", lambda root: None),
            ("root", "absent", lambda root: root.parent / f"missing-{root.name}"),
            ("root", "above_file", self._above_file),
            ("root", "above_denied", self._above_denied),
            ("root", "not_a_folder", self._not_a_folder),
            ("settings", "ok", lambda root: None),
            ("settings", "absent", lambda root: settings(root).unlink()),
            ("settings", "invalid", write(settings, b"[forest\n")),
            ("settings", "denied", chmod(settings, 0o200)),
            ("settings", "not_a_file", replace(settings, "fifo")),
            ("settings", "link", replace(settings, "link")),
            ("folder", "folder", lambda root: None),
            ("folder", "absent", lambda root: shutil.rmtree(folder(root))),
            ("folder", "link", replace(folder, "link")),
            ("folder", "not_a_folder", replace(folder, "file")),
            *[
                (step, value, chmod(folder, mode))
                for mode, values in modes.items()
                for step, value in zip(("folder_enter", "folder_list", "folder_write"), values)
            ],
            ("pointer", "absent", lambda root: None),
            ("pointer", "name", write(pointer, b"index.sqlite\n")),
            ("pointer", "damaged", write(pointer, b"../outside.sqlite\n")),
            ("pointer", "denied", lambda root: (write(pointer, b"index.sqlite\n")(root), os.chmod(pointer(root), 0o200))),
            ("pointer", "link", lambda root: (write(pointer, b"x")(root), replace(pointer, "link")(root))),
            ("pointer", "not_a_file", lambda root: pointer(root).mkdir()),
            ("generation", "absent", lambda root: index(root).unlink()),
            ("generation", "file", lambda root: None),
            ("generation", "enter_denied", generation_enter_denied),
            ("generation", "link", replace(index, "link")),
            ("generation", "not_a_file", replace(index, "fifo")),
            ("beside", "none", lambda root: None),
            ("beside", "wal", lambda root: Path(f"{index(root)}-wal").write_bytes(b"")),
            ("beside", "journal", lambda root: Path(f"{index(root)}-journal").write_bytes(b"")),
            ("beside", "not_a_file", lambda root: Path(f"{index(root)}-wal").mkdir()),
            ("beside", "unreadable", lambda root: (Path(f"{index(root)}-wal").write_bytes(b"wal"), os.chmod(Path(f"{index(root)}-wal"), 0))),
            ("open", "ok", lambda root: None),
            ("open", "denied", chmod(index, 0o200)),
            ("open", "deadline", exclusive(0.5)),
            ("open", "changed", swapped),
            ("open", "abandoned", abandoned),
            ("metadata", "equal", lambda root: None),
            ("metadata", "masking_changed", sql("UPDATE metadata SET value = 'another' WHERE key = 'masking'")),
            ("metadata", "masking_missing", sql("DELETE FROM metadata WHERE key = 'masking'")),
            ("metadata", "other_version", sql("UPDATE metadata SET value = '3' WHERE key = 'schema'")),
            ("metadata", "not_committed", lambda root: (index(root).unlink(), sqlite3.connect(index(root)).close())),
            ("metadata", "damaged", sql("DROP TABLE units_words")),
            ("metadata", "busy", exclusive(20.0)),
            ("changes", "none", lambda root: None),
            ("changes", "some", page_changed),
            ("lock", "free", lambda root: None),
            ("lock", "held", lock_held),
        ]

    def test_every_outcome_this_system_produces_is_exactly_that_fact(self) -> None:
        produced = set()
        for step, value, make in self._cases():
            with self.subTest(step=step, value=value):
                root = self._forest()
                made = make(root)
                if isinstance(made, Path):
                    root = made
                    options = {}
                else:
                    options = made if isinstance(made, dict) else {}
                deadline = options.get("deadline", system.OPEN_DEADLINE_SECONDS)
                swap = system.at(system.POINT_INDEX_OPEN, lambda *, path: Path(path).unlink()) if options.get(
                    "swap") else contextlib.nullcontext()
                with mock.patch.object(system, "OPEN_DEADLINE_SECONDS", deadline), swap:
                    facts = self._facts(root)
                self.assertEqual(getattr(facts, step), value, facts)
                self.assertIsNone(derived.impossible(facts), facts)
                produced.add((step, value))
        every = {(step, value) for step, values in derived.STEPS.items() for value in values if value != derived.NR}
        self.assertEqual(sorted(every - produced - set(NOT_PRODUCED) - set(RACE_PROBES)), [], "an outcome with neither a forest nor a reason")
        self.assertEqual(sorted(produced & set(NOT_PRODUCED)), [], "an outcome produced and said to be impossible")

    def test_each_unproduced_outcome_is_tried(self):
        for key, probe in {**NOT_PRODUCED, **RACE_PROBES}.items():
            with self.subTest(outcome=key):
                self.assertTrue(callable(probe))
                probe(self, self._forest())


class FactsReadingTests(unittest.TestCase):
    """What the facts reader gives besides the facts (the path a refusal names, the error of a step), what it
    does where the disk cannot be made to answer so (by the door's injection), and how an error becomes a
    refusal."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls._temporary.name).resolve()
        cls.built = cls.base / "built"
        for relative, text in FILES.items():
            target = cls.built / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        search.build(cls.built)
        cls.count = 0

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def _forest(self) -> Path:
        type(self).count += 1
        root = self.base / f"forest-{self.count}"
        shutil.copytree(self.built, root, symlinks=True)
        return root

    def _read(self, root: Path, **options) -> derived.Reading:
        reading = search._facts(root, **options)
        reading.close()
        return reading

    def test_the_path_each_stop_names(self) -> None:
        def unlinked(relative):
            return lambda root: (root / relative).unlink()

        rows = [
            ("sound", lambda root: None, ".forest/index.sqlite"),
            ("forest.toml does not parse", lambda root: (root / "forest.toml").write_text("[forest\n"), "forest.toml"),
            ("no .forest", lambda root: shutil.rmtree(root / ".forest"), ".forest/index.sqlite"),
            (".forest is a file", lambda root: (shutil.rmtree(root / ".forest"), (root / ".forest").write_text("x")),
             ".forest"),
            ("the pointer names nothing", lambda root: (root / ".forest" / "index.current").write_bytes(b"x\n"),
             ".forest/index.current"),
            ("no index file", unlinked(".forest/index.sqlite"), ".forest/index.sqlite"),
        ]
        for label, make, path in rows:
            with self.subTest(row=label):
                root = self._forest()
                make(root)
                self.assertEqual(self._read(root).path, path)

    def test_a_pointer_that_is_not_utf8_is_damaged(self) -> None:
        self.assertIsNone(derived.pointer_name(b"\xffindex.sqlite\n"))
        self.assertEqual(derived.pointer_name(b"index.sqlite\n"), "index.sqlite")
        self.assertEqual(derived.pointer_name(b"index-0123456789abcdef.sqlite"), "index-0123456789abcdef.sqlite")
        root = self._forest()
        (root / ".forest" / "index.current").write_bytes(b"\xffindex.sqlite\n")
        self.assertEqual(self._read(root).facts.pointer, "damaged")

    def test_a_look_beside_that_fails_is_failed_whatever_follows(self) -> None:
        """The look at ``-journal`` fails and a ``-wal`` file stands beside: the step is ``failed``, never ``wal``."""
        root = self._forest()
        index = root / ".forest" / "index.sqlite"
        Path(f"{index}-wal").write_bytes(b"")
        with system.watching() as watch:
            self._read(root)
        number = next(n for n, (name, path) in enumerate(watch.calls, 1) if path.endswith("index.sqlite-journal"))
        with system.watching(fail_calls={number: "other"}):
            reading = self._read(root)
        self.assertEqual((reading.facts.beside, reading.facts.error), ("failed", "EIO"))

    def test_a_writer_opens_the_index_only_in_a_folder_it_cannot_write(self) -> None:
        root = self._forest()
        self.assertEqual(self._read(root, open_writable=False).facts.open, derived.NR)
        self.assertEqual(self._read(root).facts.open, "ok")
        os.chmod(root / ".forest", 0o500)
        try:
            self.assertEqual(self._read(root, open_writable=False).facts.open, "ok")
        finally:
            os.chmod(root / ".forest", 0o700)

    def test_a_fault_while_the_index_is_open_closes_it_and_leaves(self) -> None:
        root = self._forest()

        class Stop(Exception):
            pass

        def changes(reading):
            raise Stop

        with self.assertRaises(Stop):
            search._facts(root, changes=changes)

    def test_an_error_of_sqlite_is_read_by_its_code_then_by_its_words(self) -> None:
        def error(text: str, code: int | None = None) -> sqlite3.Error:
            made = sqlite3.OperationalError(text)
            if code is not None:
                made.sqlite_errorcode = code
            return made

        for code, kind in ((3, "read_only"), (5, "busy"), (6, "locked"), (8, "read_only"), (10, "io"),
                           (11, "corrupt"), (13, "full"), (14, "cannot_open"), (15, "protocol"), (23, "read_only"),
                           (26, "corrupt"), (1, "other"), (11 | (1 << 8), "corrupt"), (10 | (13 << 8), "io")):
            with self.subTest(code=code):
                # The code decides, whatever the words say.
                self.assertEqual(derived.sqlite_error_kind(error("database is locked", code)), kind)
        for text, kind in (("database disk image is malformed", "corrupt"), ("file is not a database", "corrupt"),
                           ("database table is locked", "locked"), ("database is locked", "busy"),
                           ("locking protocol", "protocol"), ("attempt to write a readonly database", "read_only"),
                           ("database or disk is full", "full"), ("disk I/O error", "io"),
                           ("unable to open database file", "cannot_open"), ("no such table: units", "other")):
            with self.subTest(text=text):
                self.assertEqual(derived.sqlite_error_kind(error(text)), kind)
        self.assertEqual(derived.sqlite_kind(error("malformed", 11)), "damaged")
        self.assertEqual(derived.sqlite_kind(error("disk I/O error", 10)), "io")
        self.assertEqual(derived.sqlite_kind(error("disk I/O error", 10), ("io",)), "damaged")

    def test_a_refusal_carries_the_state_the_path_the_error_and_the_wait(self) -> None:
        failed = derived.Facts(settings="ok", folder="other", error="EIO")
        decision = derived.decide(failed, "read")
        refusal = derived.refusal(decision, derived.Reading(failed, ".forest"), extra="kept")
        self.assertEqual((refusal.code, refusal.next_step, refusal.command), ("filesystem_error", "report", None))
        self.assertEqual(refusal.details, {"path": ".forest", "state": "folder_unusable", "extra": "kept",
                                           "error": "EIO"})
        self.assertIsNone(refusal.waited_seconds)
        unnamed = dataclasses.replace(failed, error="")
        self.assertNotIn("error", derived.refusal(decision, derived.Reading(unnamed, ".forest")).details)
        link = derived.Facts(settings="ok", folder="link", error="ELOOP")
        self.assertNotIn("error", derived.refusal(derived.decide(link, "read"), derived.Reading(link, ".forest")).details)
        busy = derived.Facts(settings="busy")
        waited = derived.refusal(derived.decide(busy, "read"), derived.Reading(busy, "forest.toml"))
        self.assertEqual((waited.code, waited.next_step, waited.waited_seconds), ("index_busy", "retry", 0.0))
        with self.assertRaises(AssertionError):
            derived.refusal(derived.decide(inside("yes", **INDEX, metadata="equal"), "read"),
                            derived.Reading(failed, ".forest"))


# ─────────────────────────── every action a claim names, carried out ───────────────────────────


def _cli(root: Path, *words: str) -> dict:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.forest_main([*words, "--root", str(root), "--json"])
    text = out.getvalue()
    answer = json.loads(text) if text.strip() else {}
    return {"code": code, **answer}


class ActionClaimTests(unittest.TestCase):
    """Each claim that names an action, carried out once per kind of cure, on a forest left as the facts say."""

    @classmethod
    def setUpClass(cls) -> None:
        if os.geteuid() == 0:
            raise unittest.SkipTest("the superuser enters, lists and writes every folder")

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.base = Path(self._temporary.name).resolve()
        self.root = self.base / "forest"
        self.root.mkdir(mode=0o700)
        for relative, text in FILES.items():
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        self.folder = self.root / ".forest"
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        os.chmod(self.root, 0o700)
        for path in (self.folder, self.root / "forest.toml", self.folder / "index.current", self.folder / "index.sqlite"):
            if path.exists() and not path.is_symlink():
                os.chmod(path, 0o700 if path.is_dir() else 0o600)
        self._temporary.cleanup()

    def _decision(self, command: str = "status") -> tuple[derived.Decision, derived.Reading]:
        reading = search._facts(self.root, check_lock=True)
        reading.close()
        return derived.decide(reading.facts, command), reading

    def _claims(self) -> tuple[str, ...]:
        decision, _reading = self._decision()
        if decision.code is None and decision.move is None:
            decision, _reading = self._decision("build")
        return decision.claims

    def _sound(self) -> None:
        assert_cured(self, self.root)
        decision, _reading = self._decision("read")
        self.assertEqual(decision.code, None, decision)
        self.assertIn("trays", json.dumps(search.query(self.root, "trays", reader=READER)))

    def _run(self, command: str) -> None:
        words = shlex.split(command)[1:]
        answer = _cli(self.root, *words)
        self.assertEqual(answer["code"], 0, answer)

    def test_run_build_cures_a_missing_index(self) -> None:
        self.assertIn("run_build", self._claims())
        self.assertFalse(self.folder.exists())
        self._run(derived.CLAIMS["run_build"].action[1])
        self._sound()

    def test_run_reset_then_build_cures_a_damaged_index(self) -> None:
        search.build(self.root)
        database = sqlite3.connect(self.folder / "index.sqlite")
        database.execute("DROP TABLE units_words")
        database.commit()
        database.close()
        claims = self._claims()
        self.assertEqual(claims, ("damaged", "run_reset"))
        self._run(derived.CLAIMS["run_reset"].action[1])
        decision, _reading = self._decision()
        self.assertEqual(decision.command, derived.BUILD_COMMAND)
        self._run(decision.command)
        self._sound()

    def _as_writer(self, folder: Path, commands: tuple[str, ...]) -> None:
        """What an account that can write ``folder`` does: here the test gives itself that access, runs the
        commands, and takes it back, so the forest is again as the facts said."""
        mode = folder.stat().st_mode & 0o777
        os.chmod(folder, 0o700)
        try:
            for command in commands:
                self._run(command)
        finally:
            os.chmod(folder, mode)

    def test_a_writer_of_the_forest_folder_builds_when_there_is_no_forest_folder(self) -> None:
        os.chmod(self.root, 0o500)
        claims = self._claims()
        self.assertIn("writer_build_root", claims)
        self.assertIn("root_unwritable", claims)
        self.assertFalse(self.folder.exists())
        self.assertFalse(os.access(self.root, os.W_OK))
        self._as_writer(self.root, derived.CLAIMS["writer_build_root"].action[2:])
        self._sound()

    def test_a_writer_of_the_forest_dot_folder_builds_a_missing_index(self) -> None:
        self.folder.mkdir(mode=0o700)
        os.chmod(self.folder, 0o500)
        claims = self._claims()
        self.assertIn("writer_build_folder", claims)
        self.assertIn("folder_unwritable", claims)
        self.assertTrue(self.folder.is_dir())
        self.assertFalse(os.access(self.folder, os.W_OK))
        self._as_writer(self.folder, derived.CLAIMS["writer_build_folder"].action[2:])
        self._sound()

    def test_a_writer_of_the_forest_dot_folder_resets_and_builds_a_damaged_index(self) -> None:
        search.build(self.root)
        (self.folder / "index.current").write_text("../outside.sqlite\n", encoding="utf-8")
        os.chmod(self.folder, 0o500)
        claims = self._claims()
        self.assertEqual(claims, ("pointer_damaged", "folder_unwritable", "reads_refused", "writer_reset_folder"))
        self.assertFalse(os.access(self.folder, os.W_OK))
        self.assertEqual(search.index_relative(self.root), derived.POINTER_RELATIVE)
        self._as_writer(self.folder, derived.CLAIMS["writer_reset_folder"].action[2:])
        self._sound()

    def _give(self, path: Path, bits: int, claims: tuple[str, ...], access: int) -> None:
        self.assertFalse(os.access(path, access), "the access the claim names is not there before it is given")
        os.chmod(path, (path.stat().st_mode & 0o777) | bits)
        self.assertTrue(os.access(path, access))

    def test_each_access_the_owner_gives_cures_its_state(self) -> None:
        search.build(self.root)
        rows = (
            ("give_root_enter", self.root, 0o600, 0o700),
            ("give_root_list", self.root, 0o100, 0o700),
            ("give_folder_enter", self.folder, 0o600, 0o700),
            ("give_folder_list", self.folder, 0o300, 0o700),
            ("give_settings_read", self.root / "forest.toml", 0o000, 0o600),
        )
        for key, path, mode, bits in rows:
            with self.subTest(claim=key):
                self.assertEqual(derived.CLAIMS[key].action[0], "give")
                os.chmod(path, mode)
                decision, _reading = self._decision()
                self.assertIn(key, decision.claims, decision)
                self.assertEqual(decision.move, "report")
                os.chmod(path, bits)
                self._sound()

    def test_the_named_ancestor_is_the_folder_whose_search_access_cures_the_read(self):
        search.build(self.root)
        parent = self.root.parent / "ancestor"
        parent.mkdir()
        moved = parent / "forest"
        os.rename(self.root, moved)
        self.root, self.folder = moved, moved / ".forest"
        os.chmod(parent, 0o600)
        try:
            decision, reading = self._decision()
            self.assertEqual(reading.facts.part, "..")
            self.assertIn("give_root_above_enter", decision.claims)
            self.assertEqual(derived.refusal(decision, reading).details["folder"], "..")
            os.chmod(parent, 0o700)
            self._sound()
        finally:
            os.chmod(parent, 0o700)

    def test_read_access_and_the_named_writer_commands_cure_each_unreadable_sidecar(self):
        search.build(self.root)
        for suffix in ("-wal", "-shm", "-journal"):
            path = self.folder / (Path(search.index_relative(self.root)).name + suffix)
            path.write_bytes(b"synthetic sidecar")
            os.chmod(path, 0)
            os.chmod(self.folder, 0o500)
            try:
                decision, reading = self._decision()
                self.assertIn("give_beside_read", decision.claims)
                self.assertEqual(derived.refusal(decision, reading).details["beside"], path.name)
                os.chmod(path, 0o400)
                self._as_writer(self.folder, derived.CLAIMS["writer_reset_folder"].action[2:])
                self._sound()
            finally:
                os.chmod(self.folder, 0o700)
                path.unlink(missing_ok=True)

    def test_search_access_changed_after_the_folder_was_entered_is_the_named_cure(self):
        search.build(self.root)
        original = system.look
        index = self.folder / "index.sqlite"
        for denied_folder, relative in ((self.folder, ".forest"), (self.root, "."), (self.root.parent, "..")):
            changed = False
            mode = denied_folder.stat().st_mode & 0o777
            def looked(path):
                nonlocal changed
                if Path(path) == index and not changed:
                    changed = True
                    os.chmod(denied_folder, 0o600)
                return original(path)
            try:
                with mock.patch.object(system, "look", looked):
                    decision, reading = self._decision()
                self.assertIn("give_index_enter", decision.claims)
                self.assertEqual(reading.facts.part, relative)
                self.assertEqual(derived.refusal(decision, reading).details["folder"], relative)
            finally:
                os.chmod(denied_folder, mode)
            self._sound()

    def test_read_access_to_the_pointer_and_the_index_in_a_folder_that_cannot_be_written(self) -> None:
        search.build(self.root)
        self._run("forest index reset")
        self._run("forest index build")
        name = (self.folder / "index.current").read_text(encoding="utf-8").strip()
        for key, path in (("give_pointer_read", self.folder / "index.current"), ("give_index_read", self.folder / name)):
            with self.subTest(claim=key):
                os.chmod(path, 0o200)
                os.chmod(self.folder, 0o500)
                try:
                    decision, _reading = self._decision()
                    self.assertIn(key, decision.claims)
                    self._give(path, 0o400, decision.claims, os.R_OK)
                    self._sound()
                finally:
                    os.chmod(self.folder, 0o700)

    def test_what_the_owner_mends_cures_its_state(self) -> None:
        search.build(self.root)
        settings = self.root / "forest.toml"
        kept = settings.read_bytes()
        settings.write_bytes(b"[forest\n")
        claims = self._claims()
        self.assertEqual(claims, ("settings_invalid", "owner_mends_settings"))
        settings.write_bytes(kept)
        self._sound()
        pointer = self.folder / "index.current"
        pointer.mkdir()
        self.assertEqual(self._claims(), ("pointer_not_a_file", "owner_moves_pointer"))
        pointer.rmdir()
        self._sound()
        shutil.rmtree(self.folder)
        self.folder.write_text("not a folder\n", encoding="utf-8")
        self.assertEqual(self._claims(), ("folder_not_a_folder", "owner_mends_folder"))
        self.folder.unlink()
        self.folder.mkdir(mode=0o700)
        decision, _reading = self._decision()
        self.assertEqual((decision.state, decision.move), ("missing", "run"))
        self._run(decision.command)
        self._sound()

    def test_the_same_call_answers_later_once_the_lock_is_free(self) -> None:
        search.build(self.root)
        page = self.root / PAGE
        page.write_text(page.read_text(encoding="utf-8") + "\nA new line.\n", encoding="utf-8")
        locks = self.folder / "locks"
        locks.mkdir(exist_ok=True)
        handshake = self.base / "handshake"
        os.mkfifo(handshake)
        child = subprocess.Popen([sys.executable, "-c", HOLDER, str(locks / "index.lock"), str(handshake)],
                                 stdin=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            read_fifo(child, str(handshake), what="the child to hold the index lock")
            held = search.status(self.root)
            self.assertEqual(held["next"], "retry")
            self.assertIn(derived.CLAIMS["ask_later"].sentence, held["message"])
            self.assertEqual(derived.CLAIMS["ask_later"].action, ("wait",))
        finally:
            if child.stdin is not None:
                child.stdin.close()
            child.wait(timeout=60)
            if child.stderr is not None:
                child.stderr.close()
        later = search.status(self.root)
        self.assertEqual((later["next"], later["command"]), ("run", derived.BUILD_COMMAND))

    def test_every_kind_of_cure_is_carried_out_here_or_named_why_not(self) -> None:
        carried = {
            ("run", derived.BUILD_COMMAND), ("run", derived.RESET_COMMAND),
            ("writer", "root", derived.BUILD_COMMAND), ("writer", "folder", derived.BUILD_COMMAND),
            ("writer", "folder", derived.RESET_COMMAND, derived.BUILD_COMMAND),
            ("give", "root", "enter"), ("give", "root", "list"), ("give", "folder", "enter"),
            ("give", "folder", "list"), ("give", "settings", "read"), ("give", "pointer", "read"),
            ("give", "index", "read"), ("owner", "settings"), ("owner", "pointer"), ("owner", "folder"),
            ("wait",), ("give", "above", "enter"), ("give", "beside", "read"), ("give", "index", "enter"),
        }
        not_carried = {
            ("owner", "index"): "said only with an error of the disk or of SQLite this system does not produce "
                                "on demand (NOT_PRODUCED)",
        }
        named = {claim.action for claim in derived.CLAIMS.values() if claim.action}
        self.assertEqual(named, carried | set(not_carried))


if __name__ == "__main__":
    unittest.main()
