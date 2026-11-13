"""The state of the derived folder (design, section 10): facts, then a decision, then sentences that are data.

``read_facts`` walks the path once, through the door: the root, the settings file, ``.forest``, the pointer,
the generation the pointer names, what lies beside it, the open by SQLite, the metadata against the settings,
the files changed since the build and the index lock. Each step's fact is the door's outcome for that step
(for a folder also whether this account may enter, list and write it), or ``not_reached``. It is the only
reader of those things: every command and tool asks here.

``decide`` is a pure function of the facts and the command that asks: no disk, no clock. It gives the state
(the same for every command), the refusal's code, the next move and the claims. A claim is data: its
sentence, the condition over the facts under which it may be said and, where it says what a command or a
person does, the action that shows it. The test walks every combination of facts that ``impossible`` does
not remove and checks every claim's condition.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, fields
from pathlib import PurePath as Path
from typing import Any, Final

from . import layout, system
from .config import CONFIG_FILENAME, DEFAULT_CONFIG, MAX_CONFIG_BYTES, Config, settings_from_bytes
from .errors import MemoryForestError, state_error

NR: Final = "not_reached"

# The name a forest's index has when no pointer has been published. A later reset never reuses it.
INDEX_FILENAME: Final = "index.sqlite"
# ``.forest/index.current`` names the current index file.
POINTER_FILENAME: Final = "index.current"
POINTER_RELATIVE: Final = f"{layout.STATE_DIR}/{POINTER_FILENAME}"
# A pointer is one short name. A longer file is damaged and is not read whole.
POINTER_BYTES: Final = 64
LOCK_RELATIVE: Final = f"{layout.STATE_DIR}/locks/index.lock"
BUILD_COMMAND: Final = "forest index build"
RESET_COMMAND: Final = "forest index reset"

# ─────────────────────────────── the facts ───────────────────────────────

# Every step and the values its fact can take, in the order the walk reads them.
STEPS: Final[dict[str, tuple[str, ...]]] = {
    # What the root's own look found, before any enter or list. ``folder`` is the only value the walk continues from.
    "root": ("folder", "absent", "above_file", "above_denied", "not_a_folder"),
    "root_enter": ("ok", "denied", NR),
    "root_list": ("ok", "denied", NR),
    "root_write": ("yes", "no", NR),
    "settings": ("ok", "absent", "invalid", "denied", "not_a_file", "link", "busy", "other", NR),
    "folder": ("folder", "absent", "link", "not_a_folder", "denied", "other", NR),
    "folder_enter": ("ok", "denied", NR),
    "folder_list": ("ok", "denied", NR),
    "folder_write": ("yes", "no", NR),
    "pointer": ("absent", "name", "damaged", "denied", "link", "not_a_file", "busy", "other", NR),
    "generation": ("absent", "file", "link", "not_a_file", "denied", "enter_denied", "other", NR),
    "beside": ("none", "wal", "journal", "not_a_file", "unreadable", "failed", NR),
    "open": ("ok", "denied", "busy", "deadline", "abandoned", "changed", "other", NR),
    "metadata": (
        "equal", "masking_changed", "masking_missing", "other_version", "not_committed", "damaged",
        "busy", "locked", "read_only", "io", "full", "cannot_open", "protocol", "other", NR,
    ),
    "changes": ("none", "some", NR),
    "lock": ("free", "held", NR),
}


@dataclass(frozen=True, slots=True)
class Facts:
    """One value per step (``STEPS``). ``error`` names the system's error of an ``other`` outcome and
    ``count`` how many files changed; neither is part of the walk."""

    root: str = "folder"
    root_enter: str = "ok"
    root_list: str = "ok"
    root_write: str = "yes"
    settings: str = NR
    folder: str = NR
    folder_enter: str = NR
    folder_list: str = NR
    folder_write: str = NR
    pointer: str = NR
    generation: str = NR
    beside: str = NR
    open: str = NR
    metadata: str = NR
    changes: str = NR
    lock: str = NR
    error: str = field(default="", compare=False)
    count: int = field(default=0, compare=False)
    # The name a sentence fills in (a path part, a beside file, a moment). Not a step of the walk.
    part: str = field(default="", compare=False)
    # The access the act that failed asked for (``enter``, ``read``, ``list``, ``write``). Not a step.
    access: str = field(default="", compare=False)
    # Seconds a deadline was given. Not a step.
    seconds: float = field(default=0.0, compare=False)


def _reached(facts: Facts, step: str) -> str:
    """``always``, ``never`` or ``maybe``: whether the walk reaches ``step`` after the facts before it."""
    if step == "root":
        return "always"
    if step in ("root_enter", "root_list", "root_write"):
        return "always" if facts.root == "folder" else "never"
    if step == "settings":
        return "always" if facts.root_enter == "ok" and facts.root_list == "ok" else "never"
    if step == "folder":
        return "always" if facts.settings in ("ok", "absent") else "never"
    if step in ("folder_enter", "folder_list", "folder_write"):
        return "always" if facts.folder == "folder" else "never"
    if step == "pointer":
        inside = facts.folder == "folder" and facts.folder_enter == "ok" and facts.folder_list == "ok"
        return "always" if inside else "never"
    if step == "generation":
        return "always" if facts.pointer in ("absent", "name") else "never"
    if step == "beside":
        return "always" if facts.generation == "file" else "never"
    if step == "open":
        opens = facts.beside == "none" or (facts.beside in ("wal", "journal") and facts.folder_write == "yes")
        return "always" if opens else "never"
    if step == "metadata":
        return "always" if facts.open == "ok" else "never"
    if step == "changes":
        return "maybe" if facts.metadata == "equal" else "never"
    if step == "lock":
        inside = facts.folder == "folder" and facts.folder_enter == "ok" and facts.folder_list == "ok"
        return "maybe" if inside else "never"
    raise ValueError(step)


# The rules of ``impossible``: each name says why a combination cannot come from the walk.
RULES: Final = ("reached_after_a_stop", "skipped_though_reached", "root_write_without_enter",
                "folder_write_without_enter")


def impossible(facts: Facts, upto: str | None = None) -> str | None:
    """The rule that removes ``facts`` (or its steps up to ``upto``), or None when the walk can give it."""
    for step in STEPS:
        value = (facts.root, facts.root_enter, facts.root_list, facts.root_write, facts.settings, facts.folder, facts.folder_enter, facts.folder_list, facts.folder_write, facts.pointer, facts.generation, facts.beside, facts.open, facts.metadata, facts.changes, facts.lock)[tuple(STEPS).index(step)]
        reached = _reached(facts, step)
        if reached == "never" and value != NR:
            return "reached_after_a_stop"
        if reached == "always" and value == NR:
            return "skipped_though_reached"
        if step == "root_write" and facts.root_enter == "denied" and value == "yes":
            return "root_write_without_enter"
        if step == "folder_write" and facts.folder_enter == "denied" and value == "yes":
            return "folder_write_without_enter"
        if step == upto:
            break
    return None


def combinations() -> Iterator[Facts]:
    """Every combination of facts ``impossible`` does not remove, walked step by step."""
    names = list(STEPS)

    def walk(index: int, chosen: dict[str, str]) -> Iterator[Facts]:
        if index == len(names):
            yield Facts(**chosen)
            return
        step = names[index]
        for value in STEPS[step]:
            trial = {**chosen, step: value}
            facts = Facts(**trial)
            if impossible(facts, upto=step) is None:
                yield from walk(index + 1, trial)

    yield from walk(0, {})


def writable(facts: Facts) -> bool:
    """Whether this account can write the folder the index lives in (the root while there is no ``.forest``)."""
    if facts.folder == "folder":
        return facts.folder_write == "yes"
    if facts.folder == "absent":
        return facts.root_write == "yes"
    return False


# ─────────────────────────────── the claims ───────────────────────────────


@dataclass(frozen=True, slots=True)
class Claim:
    """A sentence, when it may be said, and what it says a person or a command does.

    ``action``: ``("run", command)`` this caller runs; ``("writer", folder, command, ...)`` an account that
    can write ``folder`` (``folder`` is ``.forest``, ``root`` the forest folder) runs the commands;
    ``("give", place, access)`` the owner gives this account ``access`` to ``place``; ``("owner", place)``
    the owner mends what stands at ``place``; ``("wait",)`` the same call later.
    """

    key: str
    sentence: str
    holds: Callable[[Facts], bool]
    action: tuple[str, ...] = ()


def _inside(facts: Facts) -> bool:
    return facts.folder == "folder" and facts.folder_enter == "ok" and facts.folder_list == "ok"


def _busy(facts: Facts) -> bool:
    return (
        facts.settings == "busy" or facts.pointer == "busy"
        or facts.open in ("busy", "changed", "deadline")
        or facts.metadata == "busy"
    )


_SETTINGS_FAULTS = ("invalid", "not_a_file", "link", "other")
_POINTER_FAULTS = ("link", "not_a_file", "other")
_FOLDER_FAULTS = ("link", "not_a_folder", "denied", "other")

CLAIMS: Final[dict[str, Claim]] = {
    claim.key: claim
    for claim in (
        Claim("root_absent", "There is nothing at the path named for the forest folder.",
              lambda f: f.root == "absent"),
        Claim("root_above_file", "A part of the path is a file ({part}), so the forest folder is not there.",
              lambda f: f.root == "above_file"),
        Claim("root_above_denied",
              "This account cannot enter {part}, relative to the forest folder, so the forest folder cannot be read.",
              lambda f: f.root == "above_denied"),
        Claim("give_root_above_enter",
              "The owner gives this account enter (x) access to {part}, relative to the forest folder.",
              lambda f: f.root == "above_denied", ("give", "above", "enter")),
        Claim("root_not_folder", "The forest folder is not a folder.",
              lambda f: f.root == "not_a_folder"),
        Claim("root_unenterable", "This account cannot enter the forest folder, so nothing in it can be read.",
              lambda f: f.root == "folder" and f.root_enter == "denied"),
        Claim("give_root_enter", "The owner gives this account enter (x) access to the forest folder.",
              lambda f: f.root == "folder" and f.root_enter == "denied", ("give", "root", "enter")),
        Claim("root_unlistable", "This account cannot list the forest folder.",
              lambda f: f.root == "folder" and f.root_enter == "ok" and f.root_list == "denied"),
        Claim("give_root_list", "The owner gives this account read (r) access to the forest folder.",
              lambda f: f.root_list == "denied", ("give", "root", "list")),
        Claim("settings_denied", "This account cannot read forest.toml.", lambda f: f.settings == "denied"),
        Claim("give_settings_read", "The owner gives this account read access to forest.toml.",
              lambda f: f.settings == "denied", ("give", "settings", "read")),
        Claim("settings_link", "forest.toml is a symlink.", lambda f: f.settings == "link"),
        Claim("settings_not_a_file", "forest.toml is not a regular file.", lambda f: f.settings == "not_a_file"),
        Claim("settings_invalid", "forest.toml does not hold settings this package can read.",
              lambda f: f.settings == "invalid"),
        Claim("settings_failed", "forest.toml could not be read ({error}).", lambda f: f.settings == "other"),
        Claim("owner_mends_settings", "The owner mends or replaces forest.toml.",
              lambda f: f.settings in _SETTINGS_FAULTS, ("owner", "settings")),
        Claim("settings_busy", "forest.toml could not be read just now.", lambda f: f.settings == "busy"),
        Claim("folder_link", "The .forest folder is a symlink.", lambda f: f.folder == "link"),
        Claim("folder_not_a_folder", "The .forest folder is not a folder.", lambda f: f.folder == "not_a_folder"),
        Claim("folder_failed", "The .forest folder could not be looked at ({error}).",
              lambda f: f.folder in ("denied", "other")),
        Claim("owner_mends_folder", "The owner puts a folder in its place.",
              lambda f: f.folder in _FOLDER_FAULTS, ("owner", "folder")),
        Claim("folder_unenterable", "This account cannot enter the .forest folder, so the index cannot be read.",
              lambda f: f.folder == "folder" and f.folder_enter == "denied"),
        Claim("give_folder_enter", "The owner gives this account enter (x) access to the .forest folder.",
              lambda f: f.folder == "folder" and f.folder_enter == "denied", ("give", "folder", "enter")),
        Claim("folder_unlistable",
              "This account cannot list the .forest folder, so the index cannot be read, built or reset here.",
              lambda f: f.folder == "folder" and f.folder_enter == "ok" and f.folder_list == "denied"),
        Claim("give_folder_list", "The owner gives this account read (r) access to the .forest folder.",
              lambda f: f.folder == "folder" and f.folder_list == "denied", ("give", "folder", "list")),
        Claim("pointer_denied", "This account cannot read the index pointer, .forest/index.current.",
              lambda f: f.pointer == "denied"),
        Claim("give_pointer_read", "The owner gives this account read access to .forest/index.current.",
              lambda f: f.pointer == "denied", ("give", "pointer", "read")),
        Claim("pointer_damaged", "The index pointer does not name an index file.", lambda f: f.pointer == "damaged"),
        Claim("pointer_link", "The index pointer is a symlink.", lambda f: f.pointer == "link"),
        Claim("pointer_not_a_file", "The index pointer is not a file.", lambda f: f.pointer == "not_a_file"),
        Claim("pointer_failed", "The index pointer could not be read ({error}).", lambda f: f.pointer == "other"),
        Claim("owner_moves_pointer", "The owner moves what stands at .forest/index.current out of .forest.",
              lambda f: f.pointer in _POINTER_FAULTS, ("owner", "pointer")),
        Claim("pointer_busy", "The index pointer could not be read just now.", lambda f: f.pointer == "busy"),
        Claim("no_index", "There is no search index to read.",
              lambda f: f.folder == "absent" or f.generation == "absent"),
        Claim("other_version", "The index was built by another version of the package.",
              lambda f: f.metadata == "other_version"),
        Claim("not_committed", "The index file's first build is not committed yet.",
              lambda f: f.metadata == "not_committed"),
        Claim("index_link", "The index file is a symlink.", lambda f: f.generation == "link"),
        Claim("index_not_a_file", "The index file is not a regular file.", lambda f: f.generation == "not_a_file"),
        Claim("index_denied", "This account cannot read the index file.",
              lambda f: f.generation == "denied" or f.open == "denied"),
        Claim("give_index_read", "The owner gives this account read access to the index file.",
              lambda f: (f.generation == "denied" or f.open == "denied") and f.access != "enter",
              ("give", "index", "read")),
        Claim("index_enter_denied",
              "This account cannot enter {part}, so the index file cannot be read.",
              lambda f: f.generation == "enter_denied"),
        Claim("give_index_enter", "The owner gives this account enter (x) access to {part}.",
              lambda f: f.generation == "enter_denied", ("give", "index", "enter")),
        Claim("index_failed", "The index file could not be opened ({error}).",
              lambda f: f.generation == "other" or f.open == "other"),
        Claim("beside_not_a_file",
              "Something that is not a file stands at a name SQLite keeps beside the index (-wal, -shm or -journal).",
              lambda f: f.beside == "not_a_file"),
        Claim("beside_failed",
              "A name SQLite keeps beside the index (-wal, -shm or -journal) could not be looked at: the system "
              "answered {error}.",
              lambda f: f.beside == "failed"),
        Claim("beside_unreadable",
              "This account cannot read {part}, a file SQLite keeps beside the index.",
              lambda f: f.beside == "unreadable"),
        Claim("give_beside_read", "The owner gives this account read access to {part}.",
              lambda f: f.beside == "unreadable" and f.folder_write == "no", ("give", "beside", "read")),
        Claim("wal", "A write-ahead log stands beside the index, so the index cannot be read safely.",
              lambda f: f.beside == "wal" and not writable(f)),
        Claim("journal", "A rollback journal stands beside the index, so the index cannot be read safely.",
              lambda f: f.beside == "journal" and not writable(f)),
        Claim("damaged",
              "The search index is damaged (a table, an index or a table's rows are missing) or is not a database.",
              lambda f: f.metadata == "damaged"),
        Claim("masking_changed", "The masking changed since the index was built.",
              lambda f: f.metadata == "masking_changed"),
        Claim("masking_missing", "The index has no masking fingerprint.", lambda f: f.metadata == "masking_missing"),
        Claim("pages_changed", "{count} files changed since the build.", lambda f: f.changes == "some"),
        Claim("index_busy", "The index file is in use.",
              lambda f: f.open == "busy" or f.metadata == "busy"),
        Claim("index_deadline", "The open of the index did not come back in {seconds}.",
              lambda f: f.open == "deadline"),
        Claim("index_abandoned",
              "An earlier open of the index has not come back since {part}; the process that answers must be "
              "started again.",
              lambda f: f.open == "abandoned"),
        Claim("index_changed", "The index file changed while it was opened.", lambda f: f.open == "changed"),
        Claim("lock_held", "The index lock is held by another process, so a build or a reset now would be refused.",
              lambda f: f.lock == "held"),
        Claim("ask_later", "The same call answers later.", lambda f: _busy(f) or f.lock == "held", ("wait",)),
        Claim("sqlite_locked", "SQLite reported a lock conflict inside this connection. Waiting does not cure it.",
              lambda f: f.metadata == "locked"),
        Claim("sqlite_read_only", "SQLite answered that the index cannot be written in this mode.",
              lambda f: f.metadata == "read_only"),
        Claim("sqlite_io", "The disk returned an input/output error for the search index.",
              lambda f: f.metadata == "io"),
        Claim("sqlite_protocol", "SQLite's locking protocol failed while the index was opened.",
              lambda f: f.metadata == "protocol"),
        Claim("sqlite_full", "SQLite reported that storage for the search index is full.", lambda f: f.metadata == "full"),
        Claim("sqlite_cannot_open", "SQLite could not open the index file or the files it keeps beside it.",
              lambda f: f.metadata == "cannot_open"),
        Claim("sqlite_other", "SQLite refused the search index for a reason this package does not classify.",
              lambda f: f.metadata == "other"),
        Claim("tell_owner", "Tell the owner what the details name.",
              lambda f: f.metadata in ("locked", "read_only", "io", "protocol", "full", "cannot_open", "other")
              or f.generation == "other" or f.open == "other" or f.beside == "failed"
              or f.folder in ("denied", "other") or f.settings == "other" or f.pointer == "other", ("owner", "index")),
        Claim("folder_unwritable",
              "The system says the .forest folder cannot be written, so the index cannot be built, updated or "
              "reset here.",
              lambda f: f.folder == "folder" and f.folder_write == "no"),
        Claim("root_unwritable",
              "The system says the forest folder cannot be written, so .forest cannot be made and no index can "
              "be built here.",
              lambda f: f.folder == "absent" and f.root_write == "no"),
        Claim("reads_answer", "Reading the index still answers.", lambda f: decide(f, "read").code is None),
        Claim("reads_refused", "Nothing is answered from it.", lambda f: decide(f, "read").code is not None),
        Claim("run_build", f"Run `{BUILD_COMMAND}`.",
              lambda f: writable(f) and f.lock != "held" and (f.folder == "absent" or _inside(f)),
              ("run", BUILD_COMMAND)),
        Claim("run_reset", f"Run `{RESET_COMMAND}`.",
              lambda f: _inside(f) and f.folder_write == "yes" and f.lock != "held", ("run", RESET_COMMAND)),
        Claim("writer_build_folder", f"An account that can write the .forest folder runs `{BUILD_COMMAND}`.",
              lambda f: f.folder == "folder" and f.folder_write == "no", ("writer", "folder", BUILD_COMMAND)),
        Claim("writer_build_root", f"An account that can write the forest folder runs `{BUILD_COMMAND}`.",
              lambda f: f.folder == "absent" and f.root_write == "no", ("writer", "root", BUILD_COMMAND)),
        Claim("writer_reset_folder",
              f"An account that can write the .forest folder runs `{RESET_COMMAND}`, then `{BUILD_COMMAND}`.",
              lambda f: f.folder == "folder" and f.folder_write == "no",
              ("writer", "folder", RESET_COMMAND, BUILD_COMMAND)),
    )
}


# ─────────────────────────────── the decision ───────────────────────────────

STATES: Final = (
    "sound", "missing", "masking_changed", "damaged", "pointer_damaged", "wal", "index_unusable",
    "root_absent", "root_above_file", "root_above_denied", "root_not_folder",
    "root_unenterable", "root_unlistable", "settings_unreadable", "settings_invalid", "folder_unusable",
    "folder_unenterable", "folder_unlistable", "pointer_unreadable", "pointer_unusable", "busy", "abandoned",
    "failed",
)
COMMANDS: Final = ("status", "read", "build", "update", "reset")
MOVES: Final = ("fix", "retry", "run", "report")
# Every code a decision gives, with the move it always carries (``index_busy`` waits; the rest are a person's).
CODES: Final[dict[str, tuple[str, ...]]] = {
    "access_denied": ("report",),
    "root_not_found": ("fix",),
    "root_not_directory": ("fix",),
    "root_above_file": ("fix",),
    "index_open_abandoned": ("report",),
    "settings_invalid": ("report",),
    "symlink_forbidden": ("report",),
    "index_cannot_open": ("report",),
    "filesystem_error": ("report",),
    "index_unreadable": ("run",),
    "index_read_only": ("report",),
    "index_busy": ("retry",),
    "index_failed": ("report",),
    "index_io_error": ("report",),
    "index_disk_full": ("report",),
}


@dataclass(frozen=True, slots=True)
class Decision:
    """What a command does in a state: refuse with ``code`` and ``move``, or go on (``code`` None).

    A command that goes on may still carry a move and claims (``index status`` says what to do next);
    ``build_first`` is a read that builds before it answers.
    """

    state: str
    code: str | None = None
    move: str | None = None
    command: str | None = None
    claims: tuple[str, ...] = ()
    build_first: bool = False


def state_of(facts: Facts) -> str:
    """The state of the derived folder: one of ``STATES``, whatever command asks."""
    f = facts
    if f.root == "absent":
        return "root_absent"
    if f.root == "above_file":
        return "root_above_file"
    if f.root == "above_denied":
        return "root_above_denied"
    if f.root == "not_a_folder":
        return "root_not_folder"
    if f.root_enter == "denied":
        return "root_unenterable"
    if f.root_list == "denied":
        return "root_unlistable"
    if f.settings == "denied":
        return "settings_unreadable"
    if f.settings in _SETTINGS_FAULTS:
        return "settings_invalid"
    if f.settings == "busy":
        return "busy"
    if f.folder in _FOLDER_FAULTS:
        return "folder_unusable"
    if f.folder == "absent":
        return "missing"
    if f.folder_enter == "denied":
        return "folder_unenterable"
    if f.folder_list == "denied":
        return "folder_unlistable"
    if f.pointer == "denied":
        return "pointer_unreadable"
    if f.pointer == "damaged":
        return "pointer_damaged"
    if f.pointer in _POINTER_FAULTS:
        return "pointer_unusable"
    if f.pointer == "busy":
        return "busy"
    if f.generation == "absent":
        return "missing"
    if f.generation in ("link", "not_a_file", "denied", "enter_denied", "other") or f.beside in (
        "not_a_file", "unreadable", "failed"
    ):
        return "index_unusable"
    if f.beside in ("wal", "journal") and not writable(f):
        return "wal"
    if f.open == "denied" or f.open == "other":
        return "index_unusable"
    if f.open == "abandoned":
        return "abandoned"
    if f.open in ("busy", "changed", "deadline") or f.metadata == "busy":
        return "busy"
    if f.metadata in ("other_version", "not_committed"):
        return "missing"
    if f.metadata == "damaged":
        return "damaged"
    if f.metadata in ("masking_changed", "masking_missing"):
        return "masking_changed"
    if f.metadata in ("locked", "read_only", "io", "protocol", "full", "cannot_open", "other"):
        return "failed"
    return "sound"


def _first(facts: Facts, keys: Sequence[str]) -> str:
    """The first claim of ``keys`` whose condition holds (the one that says what is wrong)."""
    return next(key for key in keys if CLAIMS[key].holds(facts))


_WHAT = (
    "pointer_damaged", "pointer_denied", "index_link", "index_not_a_file", "index_enter_denied", "index_denied",
    "index_failed", "beside_not_a_file", "beside_unreadable", "beside_failed", "damaged", "no_index",
    "other_version", "not_committed", "masking_missing", "masking_changed", "wal", "journal",
)
_FAILED = {
    "locked": ("index_failed", "sqlite_locked"),
    "read_only": ("index_failed", "sqlite_read_only"),
    "io": ("index_io_error", "sqlite_io"),
    "protocol": ("index_io_error", "sqlite_protocol"),
    "full": ("index_disk_full", "sqlite_full"),
    "cannot_open": ("index_cannot_open", "sqlite_cannot_open"),
    "other": ("index_failed", "sqlite_other"),
}


def what_claim(facts: Facts) -> str:
    """The claim that says what is wrong with the index in a state a build or a reset cures."""
    return _first(facts, _WHAT)


def decide(facts: Facts, command: str) -> Decision:
    """The decision for ``command`` (one of ``COMMANDS``) in the state the facts give. Pure."""
    if command not in COMMANDS:
        raise ValueError(command)
    f = facts
    state = state_of(f)
    if state == "root_absent":
        return Decision(state, "root_not_found", "fix", None, ("root_absent",))
    if state == "root_above_file":
        return Decision(state, "root_above_file", "fix", None, ("root_above_file",))
    if state == "root_above_denied":
        return Decision(state, "access_denied", "report", None, ("root_above_denied", "give_root_above_enter"))
    if state == "root_not_folder":
        return Decision(state, "root_not_directory", "fix", None, ("root_not_folder",))
    if state == "abandoned":
        return Decision(state, "index_open_abandoned", "report", None, ("index_abandoned",))
    if state in ("root_unenterable", "root_unlistable", "folder_unenterable", "folder_unlistable",
                 "settings_unreadable"):
        what = {
            "root_unenterable": ("root_unenterable", "give_root_enter"),
            "root_unlistable": ("root_unlistable", "give_root_list"),
            "folder_unenterable": ("folder_unenterable", "give_folder_enter"),
            "folder_unlistable": ("folder_unlistable", "give_folder_list"),
            "settings_unreadable": ("settings_denied", "give_settings_read"),
        }[state]
        return Decision(state, "access_denied", "report", None, what)
    if state == "settings_invalid":
        what = _first(f, ("settings_link", "settings_not_a_file", "settings_invalid", "settings_failed"))
        tail = ("owner_mends_settings", "tell_owner") if f.settings == "other" else ("owner_mends_settings",)
        return Decision(state, "settings_invalid", "report", None, (what, *tail))
    if state == "folder_unusable":
        code = {"link": "symlink_forbidden", "not_a_folder": "index_cannot_open"}.get(f.folder, "filesystem_error")
        what = _first(f, ("folder_link", "folder_not_a_folder", "folder_failed"))
        tail = ("owner_mends_folder", "tell_owner") if f.folder in ("denied", "other") else ("owner_mends_folder",)
        return Decision(state, code, "report", None, (what, *tail))
    if state == "pointer_unusable":
        code = {"link": "symlink_forbidden", "not_a_file": "index_cannot_open"}.get(f.pointer, "filesystem_error")
        what = _first(f, ("pointer_link", "pointer_not_a_file", "pointer_failed"))
        tail = ("owner_moves_pointer", "tell_owner") if f.pointer == "other" else ("owner_moves_pointer",)
        return Decision(state, code, "report", None, (what, *tail))
    if state == "busy":
        what = _first(f, ("settings_busy", "pointer_busy", "index_changed", "index_deadline", "index_busy"))
        return Decision(state, "index_busy", "retry", None, (what, "ask_later"))
    if state == "failed":
        code, what = _FAILED[f.metadata]
        return Decision(state, code, "report", None, (what, "tell_owner"))
    folder_claim = "folder_unwritable" if f.folder == "folder" else "root_unwritable"
    if state in ("pointer_unreadable", "pointer_damaged", "index_unusable", "damaged"):
        what = _first(f, _WHAT)
        if f.generation == "enter_denied":
            return Decision(state, "access_denied", "report", None, ("index_enter_denied", "give_index_enter"))
        if f.beside == "unreadable" and not writable(f):
            return Decision(state, "access_denied", "report", None, ("beside_unreadable", "give_beside_read", "writer_reset_folder"))
        if writable(f):
            if command == "reset":
                return Decision(state)
            if f.lock == "held":
                return Decision(state, "index_busy", "retry", None, (what, "lock_held", "ask_later"))
            return Decision(state, "index_unreadable", "run", RESET_COMMAND, (what, "run_reset"))
        if state == "pointer_unreadable":
            return Decision(state, "access_denied", "report", None, (what, "give_pointer_read"))
        if f.generation == "denied" or f.open == "denied":
            return Decision(state, "access_denied", "report", None, (what, "give_index_read"))
        if f.generation == "other" or f.open == "other" or f.beside == "failed":
            return Decision(state, "filesystem_error", "report", None, (what, "tell_owner"))
        return Decision(state, "index_read_only", "report", None,
                        (what, folder_claim, "reads_refused", "writer_reset_folder"))
    if state == "wal":
        what = _first(f, ("wal", "journal"))
        return Decision(state, "index_read_only", "report", None,
                        (what, folder_claim, "reads_refused", "writer_build_folder"))
    writer = "writer_build_folder" if f.folder == "folder" else "writer_build_root"
    if state in ("missing", "masking_changed"):
        what = _first(f, _WHAT)
        if writable(f):
            if command == "read":
                return Decision(state, build_first=True)
            if command == "status":
                if f.lock == "held":
                    return Decision(state, None, "retry", None, (what, "lock_held", "ask_later"))
                return Decision(state, None, "run", BUILD_COMMAND, (what, "run_build"))
            return Decision(state)
        return Decision(state, "index_read_only", "report", None, (what, folder_claim, "reads_refused", writer))
    # sound
    if writable(f):
        if command == "status" and f.changes == "some":
            if f.lock == "held":
                return Decision(state, None, "retry", None, ("pages_changed", "lock_held", "ask_later"))
            return Decision(state, None, "run", BUILD_COMMAND, ("pages_changed", "run_build"))
        return Decision(state)
    if command == "read":
        return Decision(state)
    if command == "status":
        if f.changes == "some":
            return Decision(state, None, "report", None, ("pages_changed", folder_claim, "reads_answer", writer))
        return Decision(state)
    cure = "writer_reset_folder" if command == "reset" and f.folder == "folder" else writer
    return Decision(state, "index_read_only", "report", None, (folder_claim, "reads_answer", cure))


_PART: Final[dict[str, str]] = {
    "root_above_file": "a file above the forest folder",
    "root_above_denied": "a folder above the forest folder",
    "give_root_above_enter": "a folder above the forest folder",
    "index_enter_denied": "the folder that holds the index file",
    "give_index_enter": "the folder that holds the index file",
    "beside_unreadable": "the file SQLite keeps beside the index",
    "give_beside_read": "the file SQLite keeps beside the index",
    "index_abandoned": "it was started",
}


def sentences(decision: Decision, facts: Facts) -> str:
    """The message a decision's claims make, in order, with the error's name and the count filled in."""
    said = []
    for key in decision.claims:
        text = CLAIMS[key].sentence
        if key == "pages_changed":
            text = "1 file changed since the build." if facts.count == 1 else text.format(count=facts.count)
        elif "{error}" in text:
            text = text.format(error=facts.error or "an error the system did not name")
        elif "{part}" in text:
            text = text.format(part=facts.part or _PART.get(key, "the place the look named"))
        elif "{seconds}" in text:
            seconds = facts.seconds or system.OPEN_DEADLINE_SECONDS
            shown = int(seconds) if float(seconds).is_integer() else seconds
            unit = "second" if shown == 1 else "seconds"
            text = text.format(seconds=f"{shown} {unit}")
        said.append(text)
    return " ".join(said)


# ─────────────────────────────── reading the facts ───────────────────────────────


@dataclass
class Reading:
    """What ``read_facts`` found: the facts, what a message names, and the open index when it was opened."""

    facts: Facts
    path: str
    name: str | None = None
    index: system.IndexFile | None = None
    meta: dict[str, str] | None = None
    config: Config | None = None
    changed: list[str] = field(default_factory=list)

    def close(self) -> None:
        if self.index is not None:
            self.index.close()
            self.index = None


def pointer_name(blob: bytes) -> str | None:
    """The index file name in ``blob``, or None when the bytes are not exactly that name.

    A name is what a reset writes: the name, or the name and one trailing newline. Anything else a regular
    file can hold is damaged: bytes that are not UTF-8, a NUL, an empty file, white space, more than one
    line, a name outside the pattern, ``..`` and an absolute path. The pattern is the whole test: it admits
    no other character and nothing longer than ``POINTER_BYTES``.
    """
    import re

    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if text.endswith("\n"):
        text = text[:-1]
    return text if re.fullmatch(r"index(?:-[0-9a-f]{16})?\.sqlite", text) else None


def _outcome(exc: system.Outcome, allowed: Sequence[str]) -> str:
    """The step's value for a door outcome: its kind when the step can hold it, else ``other``."""
    return exc.kind if exc.kind in allowed else "other"


def read_pointer(folder: Path) -> tuple[str, str | None, str]:
    """The pointer step: its value, the name it gives (the legacy name when there is no pointer), the error."""
    try:
        blob = system.read_bytes(folder / POINTER_FILENAME, limit=POINTER_BYTES + 1)
    except system.Outcome as exc:
        if exc.kind == "absent":
            return "absent", INDEX_FILENAME, ""
        return _outcome(exc, STEPS["pointer"]), None, exc.error
    name = pointer_name(blob)
    return ("name", name, "") if name is not None else ("damaged", None, "")


def _root_place(root: Path) -> tuple[str, str]:
    """What the root's own look found, and the part a sentence names.

    ``folder`` when the path is a folder. Otherwise the look decides, and the walk stops.
    """
    try:
        seen = system.look(root)
    except system.Absent:
        return "absent", ""
    except system.NotAFolder:
        return "above_file", _file_part(root)
    except system.Denied as exc:
        folder = Path(exc.folder) if exc.folder else Path(exc.path)
        if folder != root and folder != Path(root):
            # The door's name for a folder outside the forest. Its own directory name is a path of the machine.
            return "above_denied", _ancestor_name(root, folder)
        return "folder", ""
    except system.Outcome:
        raise
    if seen.kind == "link":
        return "not_a_folder", ""
    if seen.kind != "folder":
        return "not_a_folder", seen.kind
    return "folder", ""


def _ancestor_name(root: Path, part: Path) -> str:
    """An exact ancestor, relative to the chosen root, without a machine path or account name."""
    for distance, ancestor in enumerate(root.parents, 1):
        if ancestor == part:
            return "/".join(".." for _ in range(distance))
    return "a folder above the forest folder"


def _file_part(path: Path) -> str:
    """The path part that is a file, so a folder cannot stand under it."""
    current = path
    while True:
        parent = current.parent
        if parent == current:
            return _ancestor_name(path, current)
        try:
            info = system.look(parent)
        except system.Outcome:
            current = parent
            continue
        if info.kind != "folder":
            return _ancestor_name(path, parent)
        return _ancestor_name(path, current)


def _entered(path: Path) -> tuple[bool, str]:
    """Whether this account entered ``path``, and the access a denial asked for."""
    try:
        system.enter_folder(path)
    except system.Denied as exc:
        return False, exc.access or "enter"
    return True, ""


def _listed(path: Path) -> tuple[bool, str]:
    """Whether this account listed ``path``, and the access a denial asked for."""
    try:
        system.listing(path)
    except system.Denied as exc:
        return False, exc.access or "list"
    return True, ""


def _moment(since: float | None) -> str:
    """A UTC moment for the sentence of an open that has not come back, or words when the time is unknown."""
    if since is None:
        return ""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(since))


def _apply_beside(found: dict[str, Any], looked: system.Beside) -> None:
    """A second look at the names beside the index replaces what the open had said."""
    found["beside"] = looked.kind if looked.kind in STEPS["beside"] else "failed"
    found["open"] = NR
    found["metadata"] = NR


def read_facts(
    root: Path,
    *,
    prepare: Callable[[sqlite3.Connection], tuple[str, dict[str, str] | None]] | None,
    fingerprint: Callable[[Config], str],
    changes: Callable[[Reading], list[str]] | None = None,
    check_lock: bool = False,
    open_writable: bool = True,
) -> Reading:
    """Walk the path once, through the door, and say what each step found.

    ``prepare`` runs on the index SQLite opened (``ok`` with the metadata, ``not_committed``,
    ``other_version`` or ``damaged``); without it the index is not opened (a writer that opens it itself).
    ``changes`` lists the files changed since the build (``index status``); ``check_lock`` probes the index
    lock. With ``open_writable`` false the index is opened only in a folder this account cannot write (in
    SQLite's immutable mode, which changes nothing on disk): a writer opens it itself. The index stays open
    in the reading when the metadata is read; the caller closes it.
    """
    found: dict[str, Any] = {}
    error = ""
    part = ""
    access = ""
    seconds = 0.0

    def done(path: str, **extra: Any) -> Reading:
        return Reading(
            Facts(**found, error=error, count=len(extra.get("changed", ())), part=part, access=access,
                  seconds=seconds),
            path, **extra,
        )

    place, part = _root_place(root)
    found["root"] = place
    if place != "folder":
        found["root_enter"] = NR
        found["root_list"] = NR
        found["root_write"] = NR
        return done(".")
    entered, access = _entered(root)
    found["root_enter"] = "ok" if entered else "denied"
    listed, listed_access = _listed(root)
    found["root_list"] = "ok" if listed else "denied"
    if not entered:
        access = access or listed_access
    found["root_write"] = "yes" if found["root_enter"] == "ok" and system.may(root, "write") else "no"
    if found["root_enter"] == "denied" or found["root_list"] == "denied":
        return done(".")
    settings = root / CONFIG_FILENAME
    config = DEFAULT_CONFIG
    try:
        data = system.read_bytes(settings, limit=MAX_CONFIG_BYTES + 1)
    except system.Outcome as exc:
        if exc.kind != "absent":
            found["settings"] = _outcome(exc, STEPS["settings"])
            error = exc.error
            return done(CONFIG_FILENAME)
        found["settings"] = "absent"
    else:
        try:
            config = settings_from_bytes(data, source=str(settings))
        except MemoryForestError:
            found["settings"] = "invalid"
            return done(CONFIG_FILENAME)
        found["settings"] = "ok"
    folder = root / layout.STATE_DIR
    try:
        seen = system.look(folder)
    except system.Outcome as exc:
        found["folder"] = _outcome(exc, STEPS["folder"])
        error = exc.error
    else:
        found["folder"] = {"folder": "folder", "link": "link"}.get(seen.kind, "not_a_folder")
    if found["folder"] != "folder":
        return done(layout.STATE_DIR if found["folder"] != "absent" else f"{layout.STATE_DIR}/{INDEX_FILENAME}",
                    config=config)
    entered, access = _entered(folder)
    found["folder_enter"] = "ok" if entered else "denied"
    listed, listed_access = _listed(folder)
    found["folder_list"] = "ok" if listed else "denied"
    if not entered:
        access = access or listed_access
    found["folder_write"] = "yes" if found["folder_enter"] == "ok" and system.may(folder, "write") else "no"
    if found["folder_enter"] == "denied" or found["folder_list"] == "denied":
        return done(layout.STATE_DIR, config=config)
    if check_lock:
        try:
            found["lock"] = "free" if system.lock_free(root / LOCK_RELATIVE) else "held"
        except system.Outcome:
            found["lock"] = NR
    found["pointer"], name, error = read_pointer(folder)
    if found["pointer"] not in ("absent", "name"):
        return done(POINTER_RELATIVE, config=config)
    assert name is not None
    relative = f"{layout.STATE_DIR}/{name}"
    path = folder / name
    try:
        seen = system.look(path)
    except system.Outcome as exc:
        found["generation"] = "enter_denied" if exc.access == "enter" else _outcome(exc, STEPS["generation"])
        error = exc.error
        access = exc.access
        if exc.folder:
            denied_folder = Path(exc.folder)
            part = (
                "." if denied_folder == root else denied_folder.relative_to(root).as_posix()
                if denied_folder.is_relative_to(root) else _ancestor_name(root, denied_folder)
            )
    else:
        found["generation"] = {"file": "file", "link": "link"}.get(seen.kind, "not_a_file")
    if found["generation"] != "file":
        return done(relative, name=name, config=config)
    beside = "none"
    for suffix in ("-journal", "-wal", "-shm"):
        try:
            there = system.look(Path(f"{path}{suffix}"))
        except system.Absent:
            continue
        except system.Outcome as exc:
            beside, error = "failed", exc.error
            break
        if there.kind != "file":
            beside = "not_a_file"
            part = Path(f"{path}{suffix}").name
            break
        try:
            system.probe_read(Path(f"{path}{suffix}"))
        except system.Absent:
            continue
        except system.Outcome as exc:
            beside = "unreadable" if exc.kind == "denied" else "not_a_file" if exc.kind in ("link", "not_a_file") else "failed"
            part = Path(f"{path}{suffix}").name
            access, error = exc.access or "read", exc.error
            break
        if suffix != "-shm":
            beside = suffix[1:]
    found["beside"] = beside
    write = found["folder_write"] == "yes"
    if beside in ("not_a_file", "unreadable", "failed") or (beside != "none" and not write) or prepare is None or (
        write and not open_writable
    ):
        return done(relative, name=name, config=config)
    try:
        index, (verdict, meta) = system.open_index(path, immutable=not write, first=prepare)
    except system.Outcome as exc:
        looked = system.relook_beside(path)
        if looked is not None and looked.kind in ("not_a_file", "unreadable", "failed"):
            _apply_beside(found, looked)
            part = looked.name or part
            access = looked.access or access
            error = looked.error or exc.error
            return done(relative, name=name, config=config)
        if exc.kind == "changed":
            found["open"] = "changed"
        elif exc.note == "deadline":
            found["open"] = "deadline"
            seconds = float(exc.seconds or system.OPEN_DEADLINE_SECONDS)
        elif exc.note == "abandoned":
            found["open"] = "abandoned"
            since = system.abandoned_since(path)
            part = _moment(since)
        elif exc.kind == "busy":
            found["open"] = "busy"
        elif exc.kind == "denied":
            found["open"] = "denied"
            access = exc.access or "read"
        elif exc.kind in ("absent", "link", "not_a_file", "changed"):
            found["open"] = "changed"
        else:
            found["open"] = "other"
        error = exc.error
        return done(relative, name=name, config=config)
    except sqlite3.Error as exc:
        looked = system.relook_beside(path)
        if looked is not None and looked.kind in ("not_a_file", "unreadable", "failed"):
            _apply_beside(found, looked)
            part = looked.name or part
            access = looked.access or access
            error = looked.error
            return done(relative, name=name, config=config)
        found["open"] = "ok"
        found["metadata"] = sqlite_kind(exc, ("corrupt",))
        return done(relative, name=name, config=config)
    found["open"] = "ok"
    if verdict != "ok" or meta is None:
        index.close()
        found["metadata"] = verdict
        return done(relative, name=name, config=config)
    try:
        stored = meta.get("masking")
        if stored is None:
            found["metadata"] = "masking_missing"
        elif stored != fingerprint(config):
            found["metadata"] = "masking_changed"
        else:
            found["metadata"] = "equal"
        reading = done(relative, name=name, index=index, meta=meta, config=config)
        if changes is not None and found["metadata"] == "equal":
            listed = changes(reading)
            found["changes"] = "some" if listed else "none"
            reading = done(relative, name=name, index=index, meta=meta, config=config, changed=listed)
        return reading
    except BaseException:
        try:
            if not index.same():
                raise system.Changed(path)
        finally:
            index.close()
        raise


# SQLite's primary result codes (https://sqlite.org/rescode.html): the low byte of an extended code.
_PRIMARY_KINDS: Final = {
    3: "read_only",  # SQLITE_PERM
    5: "busy",  # SQLITE_BUSY
    6: "locked",  # SQLITE_LOCKED: a conflict inside one connection. Waiting does not cure it.
    8: "read_only",  # SQLITE_READONLY
    10: "io",  # SQLITE_IOERR
    11: "corrupt",  # SQLITE_CORRUPT
    13: "full",  # SQLITE_FULL
    14: "cannot_open",  # SQLITE_CANTOPEN
    15: "protocol",  # SQLITE_PROTOCOL
    23: "read_only",  # SQLITE_AUTH
    26: "corrupt",  # SQLITE_NOTADB
}
# An error the ``sqlite3`` module raises itself carries no SQLite code. SQLite's own messages name the class.
_MESSAGE_KINDS: Final = (
    ("malformed", "corrupt"),
    ("not a database", "corrupt"),
    ("database table is locked", "locked"),
    ("database is locked", "busy"),
    ("locking protocol", "protocol"),
    ("readonly", "read_only"),
    ("read-only", "read_only"),
    ("disk is full", "full"),
    ("disk i/o error", "io"),
    ("unable to open", "cannot_open"),
)


def sqlite_error_kind(exc: sqlite3.Error) -> str:
    """``corrupt``, ``busy``, ``locked``, ``protocol``, ``read_only``, ``full``, ``io``, ``cannot_open`` or ``other``.

    The SQLite result code decides (``sqlite_errorcode``, set on every error SQLite itself returns since
    Python 3.11, the package's minimum). An error the module raises without a code is read by its message.
    """
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int):
        return _PRIMARY_KINDS.get(code & 0xFF, "other")
    text = str(exc).casefold()
    return next((kind for marker, kind in _MESSAGE_KINDS if marker in text), "other")


def sqlite_kind(exc: sqlite3.Error, damage: Sequence[str] = ("corrupt",)) -> str:
    """The metadata step's value for an error SQLite raised while the index was opened and first read."""
    kind = sqlite_error_kind(exc)
    return "damaged" if kind in damage else kind


def refusal(decision: Decision, reading: Reading, **details: Any) -> MemoryForestError:
    """The refusal a decision gives, with its state and the path a message names."""
    assert decision.code is not None
    shown = {"path": reading.path, "state": decision.state, **details}
    if reading.facts.beside in ("not_a_file", "unreadable", "failed") and reading.facts.part:
        shown["beside"] = reading.facts.part
    if (reading.facts.root == "above_denied" or reading.facts.generation == "enter_denied") and reading.facts.part:
        shown["folder"] = reading.facts.part
    if reading.facts.error and "{error}" in "".join(CLAIMS[key].sentence for key in decision.claims):
        shown["error"] = reading.facts.error
    return state_error(
        decision.code,
        sentences(decision, reading.facts),
        details=shown,
        next_step=decision.move,
        command=decision.command,
        waited_seconds=0.0 if decision.move == "retry" else None,
    )


def facts_fields() -> tuple[str, ...]:
    """The steps, as the dataclass holds them (the test checks they are ``STEPS``)."""
    return tuple(item.name for item in fields(Facts) if item.name in STEPS)
