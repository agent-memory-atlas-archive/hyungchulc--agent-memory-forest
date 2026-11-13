"""The search index and the reads that use it (design, sections 6, 9 and 10).

``.forest/index.sqlite`` is the index's name when no pointer has been published.
``forest index reset`` points at a new file and removes the old one; deleting
the file by hand is not the repair. One row is one page section, one collection entry, one
marked block of a daily page, or one raw-lane turn. A build or update holds
the ``index`` lock and writes the published file in place, in one transaction,
in WAL mode: a reader keeps the snapshot it opened and never waits for a
writer. A build or update never replaces or unlinks the file or its journal
files. ``forest index reset`` publishes a new generation under
``.forest/index.current`` and only then removes the old file, for an index
that is corrupt or is not a database (``index_error`` is the one place a
SQLite error is classified). A call that already opened a file finishes on
that file. The lock keeps builds out, not a reader that has the file open.

The stored text is the text a read shows: sign-in values masked, numbers
starred. Words, the trigram index and vectors are built from that text, so a
guess at a hidden value is not confirmed by a hit. The metadata keeps the
fingerprint of the masking it was built under (the settings, the ledger's
judged values, the masking code's version); when the forest's fingerprint
differs, every file is indexed again before a query is answered.

A raw lane only grows. The index keeps the end of its last complete line and a
hash of the bytes before it. On an update those bytes are read and hashed
again, in 1 MiB pieces, to prove the file was only appended to. Measured on a
development machine with the file in the page cache, 8 MiB took about 4 ms:
the cost of the hash. A read from disk adds the disk's own time. Only the new
complete lines are parsed. A half-written last line waits for its newline.

Word search uses FTS5. Where this SQLite has the trigram tokenizer, a run of
three or more characters is findable inside unspaced text, and a unicode61
index beside it finds spaced words of any length. A CJK character sequence of
one or two characters is stored as grams, so it is found inside a longer word
whether or not trigram is available. Where trigram is absent, longer unspaced
text in those scripts is found the same way. ``index status`` says which
tokenizer was built.

A search, a timeline read and a subject listing bring lane files and daily
pages up to date first, by size and time. A file whose size and time are
unchanged is hashed only when that check is older than a day, or when the
build was asked to verify. A page that is not valid UTF-8 is read with
replacement characters and named in ``index status``.

Aliases are matched from the registry before words. Vectors run only when
``[embeddings] provider`` is not ``none`` and a provider of that name is
registered. Nothing here opens a network connection.
"""

from __future__ import annotations

import errno
import functools
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import threading
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from . import credential_ledger, embeddings, layout, registry as registry_mod
from . import credentials as forest_credentials
from .collections import owner_of
from .config import Config, load_config
from .credentials import shown as show_text
from .errors import MemoryForestError
from .locking import atomic_write, hold_lock
from .model import TimelineLine, parse_page
from .registry import COLLECTION_OWN_SECTIONS, about_ids, normalize_name
from .safety import require_real_root
from .scope import Scope, for_reader


# The name a forest has when nothing has published a pointer yet. A later reset never reuses it.
INDEX_FILENAME = "index.sqlite"
INDEX_RELATIVE = f"{layout.STATE_DIR}/{INDEX_FILENAME}"
# ``.forest/index.current`` names the current index file. Absent, the legacy name above is current.
POINTER_FILENAME = "index.current"
_INDEX_NAME = re.compile(r"index(?:-[0-9a-f]{16})?\.sqlite\Z")
# A pointer is one short name. A longer file is damaged and is not read whole.
_POINTER_BYTES = 64
# 4: the metadata names the tables that held rows (``filled``), so an emptied table is damage.
SCHEMA = "4"
LOCK_NAME = "index"
# When the masking changed, a query waits this long for another build before it refuses to answer.
MASKING_WAIT_SECONDS = 60.0
# A reader never waits for a writer (WAL); this covers SQLite's own brief locks only.
READER_TIMEOUT_SECONDS = 2.0
WRITER_TIMEOUT_SECONDS = 5.0
# A write that met a busy file tries again at this interval until its budget is spent.
RETRY_INTERVAL_SECONDS = 0.1
# What a busy refusal tells the caller to wait before the same call again.
RETRY_AFTER_SECONDS = 2.0
# The caller adds each answer's waited time and this wait, and reports once the sum reaches the total.
RETRY_STOP_SECONDS = 60.0
# Ids passed to one ``IN (...)``: below the 999 host parameters of an older stock SQLite build.
IN_CHUNK = 500
VECTOR_FLOOR = 0.2
# A restore that keeps the size and the time (``rsync -t``, ``cp -p``) is
# invisible to the cheap check. Hash those files once this interval has passed.
VERIFY_INTERVAL = timedelta(hours=24)
_STAGING_SUFFIX = ".building"
_H2_RE = re.compile(r"##[ \t]+(?P<text>.+?)[ \t]*$")
_BLOCK_RE = re.compile(r"<!--[ \t]*block:[ \t]*(\S+)[ \t]+(\S+)[ \t]*-->[ \t]*$")
_FENCE_RE = re.compile(r"^[ \t]{0,3}```")
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
_TOKEN_STRIP = ".,;:!?\"'()[]{}"


def _public(function: Any) -> Any:
    """The boundary of a public function: what leaves is a public refusal.

    ``index_protocol`` is this module's own signal to try a read or a write again. A loop that retries
    decides when its budget is spent; whatever still carries the signal here becomes the I/O refusal
    the budget turns it into. A ``sqlite3.Error`` that no inner guard classified is classified here.
    """

    @functools.wraps(function)
    def boundary(*args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except MemoryForestError as exc:
            if exc.code not in INTERNAL_CODES:
                raise
            waited = float(exc.waited_seconds or 0.0)
            raise protocol_spent(waited, path=str(exc.details.get("path") or "")) from exc
        except sqlite3.Error as exc:
            raise finish_read(exc, path=_boundary_path(args[0] if args else None)) from exc

    return boundary


def _boundary_path(root: Any) -> str:
    try:
        return _index_message_path(require_real_root(root))
    except Exception:
        return layout.STATE_DIR


def trigram_available() -> bool:
    """Whether this SQLite can build an FTS5 trigram index."""
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize = 'trigram')")
    except sqlite3.OperationalError:
        return False
    else:
        return True
    finally:
        connection.close()


def tokenizer_name() -> str:
    """``trigram+unicode61`` where trigram exists, otherwise ``unicode61``."""
    return "trigram+unicode61" if trigram_available() else "unicode61"


# ─────────────────────────────── build ───────────────────────────────


class _Masker:
    """The masking settings and the credential ledger, loaded once for one build or update.

    Every heading and text of every unit goes through ``shown``; loading the ledger for each of them was most
    of an update's time. ``fingerprint`` is what the index keeps in its metadata (``credentials.fingerprint``).
    """

    __slots__ = ("config", "fingerprint", "ledger")

    def __init__(self, config: Config) -> None:
        self.config = config
        self.ledger = credential_ledger.for_config(config)
        self.fingerprint = forest_credentials.fingerprint(config, self.ledger)

    def shown(self, text: str) -> str:
        value = show_text(text, config=self.config, ledger=self.ledger)
        return value if isinstance(value, str) else text


@_public
def masking_fingerprint(root: str | os.PathLike[str]) -> str:
    """The fingerprint of the masking the forest at ``root`` applies now."""
    return _Masker(load_config(require_real_root(root))).fingerprint


@_public
def build(
    root: str | os.PathLike[str],
    *,
    only: Sequence[str] | None = None,
    wait_seconds: float = 0.0,
    verify: bool = False,
) -> dict[str, Any]:
    """Build or update the index. A lock held elsewhere returns ``busy`` without waiting past ``wait_seconds``.

    The published file is written in place, in one transaction: a reader keeps the snapshot it opened and sees
    the new one after the commit. ``verify`` hashes every file whose size and time are unchanged.
    """
    safe = require_real_root(root)
    _refuse_write(safe, "build")
    result = _attempt_write(
        safe,
        None if only is None else list(only),
        verify=verify,
        wait_seconds=wait_seconds,
        command="build",
    )
    if not result.get("busy"):
        result["indexed"] = True
    return result


@_public
def update_files(
    root: str | os.PathLike[str],
    paths: Sequence[str],
    *,
    wait_seconds: float = 0.0,
    verify: bool = False,
) -> dict[str, Any]:
    """Update those files in the published index. This does not copy the database.

    ``indexed`` is false when the index could not be written. ``reason`` names which refusal that was.
    The turns are still on disk; the next query brings them in.
    """
    safe = require_real_root(root)
    result = _attempt_write(
        safe,
        [layout.normalize(item) for item in paths],
        verify=verify,
        wait_seconds=wait_seconds,
        command="update",
    )
    if not result.get("busy"):
        result["indexed"] = True
    return result


@_public
def reset(root: str | os.PathLike[str], *, dry_run: bool = False) -> dict[str, Any]:
    """Point the index at a new file, then remove the old one and its ``-wal`` and ``-shm`` files.

    For an index that is corrupt, not a database, or damaged. The new file's name has never been used, so
    no order of removals has to be right: a reader that already opened the old file finishes on it. The
    index lock is held, which keeps a build or an update out. A server may be running. Pages and lanes stay.
    A file that cannot be removed is named in ``orphans`` and is not a failure. ``dry_run`` says what would
    change and writes nothing. A held lock is the refusal ``index_busy`` (next: retry). What is left after
    a reset is a build: the answer's next move is ``run`` ``forest index build``.
    """
    safe = require_real_root(root)
    _refuse_write(safe, "reset")
    try:
        with _index_writer(safe, 0):
            result = _reset_held(safe, dry_run=dry_run)
    except MemoryForestError as exc:
        if exc.code not in _BUSY_CODES:
            raise
        raise index_busy_error(
            "The index lock is held, so nothing was removed. Reset again when it is free.",
            path=_index_message_path(safe),
        ) from exc
    return result


_NOT_COMMITTED = "The index file is not committed yet, so there is nothing to answer from."
_FINGERPRINT_DIFFERS = (
    "The masking fingerprint differs from the index, and the index could not be rebuilt. Nothing was "
    "answered from rows masked under other rules."
)
_FINGERPRINT_MISSING = (
    "The index has no masking fingerprint, and the index could not be rebuilt. Nothing was answered."
)
_LOCK_HELD_MESSAGE = "The index lock is held."
_FILE_HELD_MESSAGE = "The index file is in use."


@_public
def refresh_lanes_and_daily(root: str | os.PathLike[str]) -> int:
    """Make the index fit to answer from. Returns how many changed files could not be brought in.

    The state of the derived folder decides first (``index_state``): a state a read cannot answer from is
    its refusal. When the masking fingerprint differs from the stored one, or the stored one cannot be
    read, every file is indexed again first, waiting up to ``MASKING_WAIT_SECONDS``. A query then refuses
    rather than answer from text masked under other rules. When the fingerprint is the same, a busy index
    answers from the rows already on file. Otherwise the changed lane files and daily pages are brought
    in, by the sizes and times of one listing of their folders. A folder this process cannot write is
    only counted: the number of changed lane files and daily pages is returned, and nothing is written.
    """
    safe = require_real_root(root)
    listing = _lane_daily_listing(safe)
    state, opened = _open_state(safe)
    try:
        refusal = state.read_refusal()
        if refusal is not None:
            raise refusal
        if state.kind == MISSING:
            stored = None
        elif state.kind == MASKING_CHANGED:
            stored = None
        else:
            assert opened is not None
            try:
                stored = {
                    row[0]: (row[1], row[2])
                    for row in opened.connection.execute(
                        "SELECT path, size, mtime_ns FROM files WHERE path LIKE ? OR path LIKE ?",
                        (f"{layout.DAILY}/%", f"{layout.ISTM}/%"),
                    )
                }
            except sqlite3.Error as exc:
                raise finish_read(exc, path=state.path) from exc
    finally:
        if opened is not None:
            opened.close()
    if state.kind == MISSING:
        # No file yet: a busy build has no rows to answer from. A file whose first build has not
        # committed is waited for like a masking change.
        if state.exists:
            result = build(safe, wait_seconds=MASKING_WAIT_SECONDS)
            if result.get("busy"):
                _raise_busy(result, _NOT_COMMITTED)
            return 0
        if build(safe, wait_seconds=0).get("busy"):
            return len(listing)
        return 0
    if state.kind == MASKING_CHANGED:
        result = build(safe, wait_seconds=MASKING_WAIT_SECONDS)
        if result.get("busy"):
            _raise_busy(result, _FINGERPRINT_MISSING if state.fingerprint == "missing" else _FINGERPRINT_DIFFERS)
        return 0
    assert stored is not None
    changed = sorted(
        {
            relative
            for relative, (_path, size, mtime_ns) in listing.items()
            if stored.get(relative) != (size, mtime_ns)
        }
        | {relative for relative in stored if relative not in listing}
    )
    if not changed or not state.writable:
        return len(changed)
    result = update_files(safe, changed, wait_seconds=0)
    if result.get("busy") or not result.get("indexed"):
        return len(changed)
    return 0


@_public
def status(root: str | os.PathLike[str]) -> dict[str, Any]:
    """Counts, the tokenizer, the state, and whether a file has changed since the build. No lock is taken.

    ``state`` is the state of the derived folder (``index_state``). A state a read cannot answer from is its
    refusal. When there is something to do, ``next`` and ``message`` say what, as a refusal would.
    """
    safe = require_real_root(root)

    def read(state: IndexState, connection: sqlite3.Connection | None) -> dict[str, Any]:
        refusal = state.read_refusal()
        if refusal is not None:
            raise refusal
        if connection is None:
            payload: dict[str, Any] = {
                "available": False,
                "exists": state.exists,
                "ok": True,
                "path": state.path,
                "state": state.kind,
            }
            _attach_next(payload, state, [])
            return payload
        try:
            meta = _metadata(connection)
            files = connection.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            units = connection.execute("SELECT COUNT(*) FROM units").fetchone()[0]
            stale = _stale_files(safe, connection)
            undecodable = _flagged(connection, "undecodable")
            torn = _flagged(connection, "torn")
            verify_due = _verify_due(connection)
        except sqlite3.Error as exc:
            raise finish_read(exc, path=state.path) from exc
        payload = {
            "available": True,
            "built_at": meta.get("built_at", ""),
            "exists": True,
            "files": files,
            "masking": "changed" if state.kind == MASKING_CHANGED else "current",
            "ok": True,
            "path": state.path,
            "schema": meta.get("schema", ""),
            "stale": stale,
            "state": state.kind,
            "tokenizer": meta.get("tokenizer", ""),
            "torn": torn,
            "undecodable": undecodable,
            "units": units,
            "verify_due": verify_due,
        }
        if is_read_only(connection):
            payload["read_only"] = True
        _attach_next(payload, state, stale)
        return payload

    return _checked_read(safe, read, describe=True)


def _attach_next(payload: dict[str, Any], state: IndexState, stale: Sequence[str]) -> None:
    """The next move a status answer carries when something is to be done, said as a refusal says it."""
    claims: list[str] = []
    if state.kind == MISSING:
        claims.append("no_index")
    elif state.kind == MASKING_CHANGED:
        claims.append(_masking_claim(state))
    elif stale:
        claims.append(_pages_changed(len(stale)))
    if not claims:
        return
    if state.writable:
        payload["next"] = "run"
        payload["command"] = _BUILD_COMMAND
        payload["message"] = _sentences([*claims, _cure_here(_BUILD_COMMAND)])
        return
    payload["next"] = "report"
    payload["message"] = _sentences(
        [*claims, _folder_claim(state), "reads_answer", _cure_by_writer(state, (_BUILD_COMMAND,))]
    )


# ─────────────────────── the state of the derived folder ───────────────────────
#
# One function reads what is true on disk (``_open_state``) and gives one state. The state gives the
# refusal of a read and of a write, with their text and next move. ``status``, ``build``, ``reset``,
# ``search``, the timeline and every MCP tool that reads or writes the index ask here, so no command
# meets a different check first. The derived folder is ``.forest``, or the forest root while there is no
# ``.forest`` yet (a build would make it there).

SOUND = "sound"
MASKING_CHANGED = "masking_changed"
MISSING = "missing"
DAMAGED = "damaged"
POINTER_DAMAGED = "pointer_damaged"
WAL_PRESENT = "wal"
STATES = (SOUND, MASKING_CHANGED, MISSING, DAMAGED, POINTER_DAMAGED, WAL_PRESENT)
_BUILD_COMMAND = "forest index build"
_RESET_COMMAND = "forest index reset"

# Every sentence a state's message is made of. Each one is a claim about the disk or about what a call
# does; the state test checks each claim by doing it. A sentence with a number or a command is built by
# ``_pages_changed``, ``_cure_here`` and ``_cure_by_writer``.
CLAIMS = {
    "pointer_damaged": "The index pointer does not name an index file.",
    "damaged": "The search index is damaged (a table, an index or a table's rows are missing) or is not a database.",
    "no_index": "There is no search index to read.",
    "wal": "A write-ahead log stands beside the index, so the index cannot be read safely.",
    "masking_changed": "The masking changed since the index was built.",
    "masking_missing": "The index has no masking fingerprint.",
    "folder_unwritable": (
        "The .forest folder cannot be written, so the index cannot be built, updated or reset here."
    ),
    "root_unwritable": (
        "The forest folder cannot be written, so .forest cannot be made and no index can be built here."
    ),
    "reads_answer": "Reading the index still answers.",
    "reads_refused": "Nothing is answered from it.",
}


@dataclass(frozen=True)
class IndexState:
    """What is true of the derived folder, as one of ``STATES``.

    ``writable``: this process can write the folder the index lives in. ``made``: ``.forest`` exists.
    ``path``: what a message names, the index file or, for a damaged pointer, the pointer. ``exists``: an
    index file is on disk. ``fingerprint``: ``missing`` or ``changed`` for ``masking_changed``.
    """

    kind: str
    writable: bool
    made: bool
    path: str
    exists: bool = False
    fingerprint: str = ""

    def read_refusal(self) -> MemoryForestError | None:
        """What a read answers in this state: None when a read answers (or builds first and answers)."""
        if self.kind == SOUND:
            return None
        if self.writable:
            if self.kind in (MISSING, MASKING_CHANGED, WAL_PRESENT):
                return None
            return _unreadable_refusal(self)
        cure = (_RESET_COMMAND, _BUILD_COMMAND) if self.kind in (DAMAGED, POINTER_DAMAGED) else (_BUILD_COMMAND,)
        claims = [_kind_claim(self), _folder_claim(self), "reads_refused", _cure_by_writer(self, cure)]
        return _state_refusal(self, "index_read_only", claims)

    def write_refusal(self, command: str) -> MemoryForestError | None:
        """What ``build``, ``update`` or ``reset`` answers in this state: None when it can run."""
        if self.writable:
            if command != "reset" and self.kind in (DAMAGED, POINTER_DAMAGED):
                return _unreadable_refusal(self)
            return None
        damaged = self.kind in (DAMAGED, POINTER_DAMAGED)
        cure = (_RESET_COMMAND, _BUILD_COMMAND) if damaged or command == "reset" else (_BUILD_COMMAND,)
        claims = [] if self.kind == SOUND else [_kind_claim(self)]
        claims += [
            _folder_claim(self),
            "reads_answer" if self.kind == SOUND else "reads_refused",
            _cure_by_writer(self, cure),
        ]
        return _state_refusal(self, "index_read_only", claims)


def _kind_claim(state: IndexState) -> str:
    if state.kind == MASKING_CHANGED:
        return _masking_claim(state)
    return {
        DAMAGED: "damaged",
        POINTER_DAMAGED: "pointer_damaged",
        MISSING: "no_index",
        WAL_PRESENT: "wal",
    }[state.kind]


def _masking_claim(state: IndexState) -> str:
    return "masking_missing" if state.fingerprint == "missing" else "masking_changed"


def _folder_claim(state: IndexState) -> str:
    return "folder_unwritable" if state.made else "root_unwritable"


def _pages_changed(count: int) -> str:
    return f"{count} file changed since the build." if count == 1 else f"{count} files changed since the build."


def _cure_here(command: str) -> str:
    return f"Run `{command}`."


def _cure_by_writer(state: IndexState, commands: Sequence[str]) -> str:
    folder = "the .forest folder" if state.made else "the forest folder"
    named = ", then ".join(f"`{command}`" for command in commands)
    return f"An account that can write {folder} runs {named}."


def _sentences(claims: Sequence[str]) -> str:
    return " ".join(CLAIMS.get(claim, claim) for claim in claims)


def _state_refusal(state: IndexState, code: str, claims: Sequence[str], *, command: str = "") -> MemoryForestError:
    return MemoryForestError(
        code,
        _sentences(claims),
        details={"path": state.path, "state": state.kind},
        next_step="run" if command else "report",
        command=command or None,
    )


def _unreadable_refusal(state: IndexState) -> MemoryForestError:
    """A damaged index or pointer in a folder this process can write: reset it."""
    claim = "pointer_damaged" if state.kind == POINTER_DAMAGED else "damaged"
    return _state_refusal(
        state, "index_unreadable", [claim, _cure_here(_RESET_COMMAND)], command=_RESET_COMMAND
    )


def _derived_folder(root: Path, folder: Path) -> tuple[bool, bool]:
    """Whether ``.forest`` exists, and whether this process can write the folder the index lives in.

    With no ``.forest``, a build would make it in the forest root, so the root is what must be writable.
    """
    try:
        folder.lstat()
    except FileNotFoundError:
        return False, os.access(root, os.W_OK | os.X_OK)
    return True, os.access(folder, os.W_OK | os.X_OK)


def derived_writable(root: str | os.PathLike[str]) -> bool:
    """Whether this process can write the folder the index and the locks live in."""
    safe = require_real_root(root)
    return _derived_folder(safe, _state_dir(safe))[1]


def _refuse_write(root: Path, command: str) -> None:
    """``build``, ``update`` and ``reset`` need a folder they can write. The state says what to tell."""
    folder = _state_dir(root)
    if _derived_folder(root, folder)[1]:
        return
    state, opened = _open_state(root)
    if opened is not None:
        opened.close()
    refusal = state.write_refusal(command)
    assert refusal is not None
    raise refusal


def _wal_exists(path: Path) -> bool:
    wal = Path(str(path) + "-wal")
    try:
        info = wal.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)


class _Reader(sqlite3.Connection):
    """A reading connection. It carries its own flag, so nothing is kept beside it by its address."""

    read_only = False


class _ImmutableReader(_Reader):
    """A connection in SQLite's immutable mode: the folder could not be written and no WAL stood beside it."""

    read_only = True


def is_read_only(connection: sqlite3.Connection | None) -> bool:
    """Whether ``connection`` was opened in SQLite's immutable mode."""
    return bool(getattr(connection, "read_only", False))


# Opened for reading only, without following a link, and without blocking on a fifo put in a file's place.
_READ_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)


def _open_regular(path: Path, relative: str, *, flags: int = _READ_FLAGS, mode: int = 0o600) -> int | None:
    """Open ``path`` first, then check what was opened. None when nothing is there.

    A symlink is ``symlink_forbidden``; anything but a regular file (a folder or a fifo in its place) is
    ``index_cannot_open``. The caller owns the descriptor.
    """
    try:
        descriptor = os.open(path, flags, mode)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise MemoryForestError(
                "symlink_forbidden",
                "Symlinks are not allowed inside a Memory Forest.",
                details={"path": relative},
                next_step="report",
            ) from exc
        if exc.errno == errno.EISDIR:
            raise _not_a_file(relative) from exc
        writing = flags & (os.O_WRONLY | os.O_RDWR)
        kind = "read_only" if writing and exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS) else "cannot_open"
        code, message = _PERSON_MESSAGES[kind]
        raise MemoryForestError(code, message, details={"path": relative}, next_step="report") from exc
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise _not_a_file(relative)
    return descriptor


def _not_a_file(relative: str) -> MemoryForestError:
    _code, message = _PERSON_MESSAGES["cannot_open"]
    return MemoryForestError("index_cannot_open", message, details={"path": relative}, next_step="report")


class _Opened:
    """A reading connection and the descriptor of the file it reads, opened before SQLite opened the path."""

    __slots__ = ("connection", "descriptor", "path", "relative", "before")

    def __init__(self, connection: sqlite3.Connection, descriptor: int, path: Path, relative: str) -> None:
        self.connection = connection
        self.descriptor = descriptor
        self.path = path
        self.relative = relative
        self.before = _descriptor_stamp(descriptor)

    def unchanged(self) -> bool:
        """The file is still the one opened, with the same size and time, and no WAL appeared beside it."""
        if _descriptor_stamp(self.descriptor) != self.before:
            return False
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return False
        if (info.st_dev, info.st_ino) != self.before[:2]:
            return False
        return not _wal_exists(self.path)

    def release_descriptor(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1

    def close(self) -> None:
        try:
            self.connection.close()
        finally:
            self.release_descriptor()


def _descriptor_stamp(descriptor: int) -> tuple[int, int, int, int]:
    """Device, inode, size and modification time of an open file. Immutable mode is honest while these stay."""
    info = os.fstat(descriptor)
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _open_state(root: Path) -> tuple[IndexState, _Opened | None]:
    """The state of the derived folder, and the open index when a read can use it.

    Nothing here writes. In a folder this process can write the index is opened as usual (WAL); in one it
    cannot, it is opened in SQLite's immutable mode, and only when no write-ahead log stands beside it.
    """
    folder = _state_dir(root)
    made, writable = _derived_folder(root, folder)
    try:
        name = _current_index_name(root)
    except MemoryForestError as exc:
        if exc.code != "index_unreadable":
            raise
        return IndexState(POINTER_DAMAGED, writable, made, f"{layout.STATE_DIR}/{POINTER_FILENAME}"), None
    relative = f"{layout.STATE_DIR}/{name}"
    path = folder / name
    descriptor = _open_regular(path, relative) if made else None
    if descriptor is None:
        return IndexState(MISSING, writable, made, relative), None
    kept = False
    try:
        if not writable and _wal_exists(path):
            return IndexState(WAL_PRESENT, writable, made, relative, exists=True), None
        connection = _connect_reader(path, relative, immutable=not writable)
        opened = _Opened(connection, descriptor, path, relative)
        try:
            meta = _prepare_reader(connection, relative)
        except MemoryForestError as exc:
            if exc.code != "index_unreadable":
                raise
            return IndexState(DAMAGED, writable, made, relative, exists=True), None
        if meta is None:
            return IndexState(MISSING, writable, made, relative, exists=True), None
        if "masking" not in meta:
            kind, fingerprint = MASKING_CHANGED, "missing"
        elif meta.get("masking") != masking_fingerprint(root):
            kind, fingerprint = MASKING_CHANGED, "changed"
        else:
            kind, fingerprint = SOUND, ""
        kept = True
        return IndexState(kind, writable, made, relative, exists=True, fingerprint=fingerprint), opened
    finally:
        if not kept:
            os.close(descriptor)


@_public
def index_state(root: str | os.PathLike[str]) -> IndexState:
    """The state of the derived folder, as every command sees it."""
    state, opened = _open_state(require_real_root(root))
    if opened is not None:
        opened.close()
    return state


def _connect_reader(path: Path, relative: str, *, immutable: bool) -> sqlite3.Connection:
    """Open the file SQLite reads. ``mode=rw`` never creates a file that went away since it was opened."""
    flags = "?mode=ro&immutable=1" if immutable else "?mode=rw"
    factory = _ImmutableReader if immutable else _Reader
    try:
        return sqlite3.connect(
            path.as_uri() + flags, uri=True, timeout=READER_TIMEOUT_SECONDS, factory=factory
        )
    except sqlite3.Error as exc:
        if not path.exists():
            raise index_busy_error("The index file went away while it was opened.", path=relative) from exc
        raise finish_read(exc, path=relative) from exc


def _prepare_reader(connection: sqlite3.Connection, relative: str) -> dict[str, str] | None:
    """The metadata of a whole index, or None (and closed) for a file whose first build has not committed.

    A file that fails the probe is closed and refused as ``index_unreadable``.
    """
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        meta = _metadata(connection)
        if meta.get("schema") != SCHEMA:
            connection.close()
            return None
        if not _probe(connection, meta):
            connection.close()
            raise index_unreadable_error(damaged=True, path=relative)
    except sqlite3.Error as exc:
        connection.close()
        if _no_schema_yet(exc):
            return None
        raise finish_read(exc, path=relative) from exc
    return meta


def _checked_read(root: Path, fn: Any, *, describe: bool = False) -> Any:
    """Run ``fn`` on the open index and close it. An immutable read is compared before and after, once more, then busy.

    ``fn`` gets the connection (None when there is no index yet). A state a read cannot answer from is its
    refusal first. With ``describe``, ``fn`` gets the state and the connection and decides itself.
    """
    for attempt in (1, 2):
        state, opened = _open_state(root)
        connection = opened.connection if opened is not None else None
        error: BaseException | None = None
        result: Any = None
        try:
            if describe:
                result = fn(state, connection)
            else:
                refusal = state.read_refusal()
                if refusal is not None:
                    raise refusal
                result = fn(connection)
        except BaseException as exc:
            error = exc
        changed = False
        if opened is not None:
            changed = is_read_only(connection) and not opened.unchanged()
            opened.close()
        if changed:
            if attempt == 1:
                continue
            raise index_busy_error("The index file changed while it was read.", path=state.path)
        if error is not None:
            if isinstance(error, MemoryForestError) and error.code == "index_unreadable" and not state.writable:
                raise IndexState(DAMAGED, False, state.made, state.path, exists=True).read_refusal() from error
            raise error
        return result
    raise AssertionError("unreachable")


def _no_schema_yet(exc: sqlite3.Error) -> bool:
    """A file with no ``metadata`` table: SQLite's generic error, naming that table."""
    return sqlite_error_kind(exc) == "other" and "no such table: metadata" in str(exc).casefold()


@_public
def connect(root: str | os.PathLike[str]) -> sqlite3.Connection | None:
    """Open the published index for reading, or None when there is no index yet. Writes are refused on it.

    The state decides (``index_state``): a state a read cannot answer from is its refusal. The index is in
    WAL mode, so a reader does not wait for a writer: it reads the last committed snapshot. In a folder
    this process cannot write, the connection is in SQLite's immutable mode (``is_read_only``).
    """
    state, opened = _open_state(require_real_root(root))
    if opened is None:
        refusal = state.read_refusal()
        if refusal is not None:
            raise refusal
        return None
    opened.release_descriptor()
    refusal = state.read_refusal()
    if refusal is not None:
        opened.connection.close()
        raise refusal
    return opened.connection


@_public
def require_index(root: str | os.PathLike[str]) -> sqlite3.Connection:
    connection = connect(root)
    if connection is None:
        raise MemoryForestError(
            "index_missing",
            "The forest has no search index. Run `forest index build`.",
            details={"path": index_relative(root)},
            next_step="run",
            command=_BUILD_COMMAND,
        )
    return connection


# The index lock is a file lock, held once per process. Threads of one process (the HTTP server's) queue here
# first, so a second thread waits like a second process instead of meeting ``lock_already_held``.
_WRITERS = threading.Lock()
_BUSY_CODES = frozenset({"lock_busy", "lock_already_held"})
# Codes this module raises to itself and never lets out (``_public``).
INTERNAL_CODES = frozenset({"index_protocol"})


def _index_message_path(root: Path) -> str:
    """The forest-relative path of the current index, for every message and ``details``.

    The legacy name is chosen only when ``_read_pointer`` finds no pointer. A damaged pointer has no
    current file, so the message names the pointer.
    """
    try:
        return f"{layout.STATE_DIR}/{_current_index_name(root)}"
    except MemoryForestError as exc:
        if exc.code == "index_unreadable":
            return f"{layout.STATE_DIR}/{POINTER_FILENAME}"
        raise


def index_unreadable_error(name: str = "", *, damaged: bool = False, path: str) -> MemoryForestError:
    """The index file is corrupt, not a database, or damaged. The next move is ``forest index reset``."""
    refusal = _unreadable_refusal(IndexState(DAMAGED, True, True, path, exists=True))
    if name:
        refusal.details["sqlite"] = name
    return refusal


def index_busy_error(
    message: str,
    name: str = "",
    *,
    waited_seconds: float = 0.0,
    path: str,
) -> MemoryForestError:
    """The index is busy. The message says how long this call waited, and ``next`` says when to stop."""
    details = {"path": path}
    if name:
        details["sqlite"] = name
    waited = max(0.0, float(waited_seconds))
    text = f"{message.rstrip()} This call waited {waited:.1f} seconds."
    return MemoryForestError(
        "index_busy",
        text,
        details=details,
        next_step="retry",
        after_seconds=RETRY_AFTER_SECONDS,
        waited_seconds=waited,
        stop_after_seconds=RETRY_STOP_SECONDS,
    )


def _raise_busy(result: dict[str, Any], because: str) -> None:
    """Raise ``index_busy`` whose text is ``because`` plus the cause the attempt could tell apart."""
    cause = str(result.get("message") or "").strip()
    message = f"{because} {cause}".strip()
    raise index_busy_error(
        message,
        waited_seconds=float(result.get("waited_seconds") or 0.0),
        path=str(result.get("path") or ""),
    )


# SQLite's primary result codes (https://sqlite.org/rescode.html): the low byte of an extended code.
_PRIMARY_KINDS = {
    3: "read_only",  # SQLITE_PERM
    5: "busy",  # SQLITE_BUSY
    6: "locked",  # SQLITE_LOCKED: a conflict inside one connection. Waiting does not cure it.
    8: "read_only",  # SQLITE_READONLY
    10: "io",  # SQLITE_IOERR
    11: "corrupt",  # SQLITE_CORRUPT
    13: "full",  # SQLITE_FULL
    14: "cannot_open",  # SQLITE_CANTOPEN
    15: "protocol",  # SQLITE_PROTOCOL: retried inside the call, then an I/O refusal.
    23: "read_only",  # SQLITE_AUTH
    26: "corrupt",  # SQLITE_NOTADB
}
# An error the ``sqlite3`` module raises itself carries no SQLite code. SQLite's own messages name the class.
_MESSAGE_KINDS = (
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
_PERSON_MESSAGES = {
    "read_only": (
        "index_read_only",
        "The search index file cannot be written. Reading it still answers when the file itself can be "
        "read. Give this user write access to the file, then build again.",
    ),
    "locked": (
        "index_failed",
        "SQLite reported a lock conflict inside this connection. Waiting does not cure it. The pages are "
        "untouched. Tell the owner the SQLite code in the details.",
    ),
    "full": (
        "index_disk_full",
        "The disk that holds the forest is full, so the search index cannot be written. Free space on that "
        "disk, then ask again. Do not reset the index: building it again needs more room than it holds now.",
    ),
    "io": (
        "index_io_error",
        "The disk returned an input/output error for the search index. Check the disk that holds the forest, "
        "then ask again. A reset would not help.",
    ),
    "cannot_open": (
        "index_cannot_open",
        "The search index cannot be opened: the index file is not a file this process can open or "
        "create (a folder in its place, or a .forest folder it cannot enter). Fix that path, then ask again.",
    ),
    "other": (
        "index_failed",
        "SQLite refused the search index for a reason this package does not classify. The pages are "
        "untouched. Tell the owner the SQLite code in the details.",
    ),
}


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


def query_text_fault(exc: sqlite3.Error) -> bool:
    """An FTS expression the caller's own text made. A busy file, a lock and an I/O error are not that."""
    if sqlite_error_kind(exc) != "other":
        return False
    text = str(exc).casefold()
    return "fts5:" in text or "unterminated string" in text or "unknown special query" in text


def index_error(exc: sqlite3.Error, *, busy_message: str = "", path: str) -> MemoryForestError:
    """The one place a ``sqlite3.Error`` from the index becomes a refusal, with its next move.

    Corrupt or not a database: ``index_unreadable``, next ``run`` the reset. Busy: ``index_busy``, next
    ``retry``. Locked is a fault of this connection: ``index_failed``, next ``report``. A protocol error
    is ``index_protocol`` so the caller can retry it; when that budget is spent the caller reports it as
    I/O. Read-only, full, I/O and cannot-open have a code of their own and next ``report``.
    """
    kind = sqlite_error_kind(exc)
    name = str(getattr(exc, "sqlite_errorname", "") or "")
    if kind == "corrupt":
        return index_unreadable_error(name, path=path)
    if kind == "busy":
        return index_busy_error(busy_message or _FILE_HELD_MESSAGE, name, path=path)
    if kind == "protocol":
        details = {"path": path}
        if name:
            details["sqlite"] = name
        return MemoryForestError(
            "index_protocol",
            "SQLite's locking protocol failed.",
            details=details,
            next_step="retry",
        )
    code, message = _PERSON_MESSAGES[kind]
    details = {"path": path}
    if name:
        details["sqlite"] = name
    return MemoryForestError(code, message, details=details, next_step="report")


def protocol_spent(waited_seconds: float, *, path: str) -> MemoryForestError:
    """A protocol error whose own budget is spent: an I/O refusal, not a busy one."""
    _code, message = _PERSON_MESSAGES["io"]
    waited = max(0.0, float(waited_seconds))
    return MemoryForestError(
        "index_io_error",
        f"{message} The locking protocol failed, and this call already waited {waited:.1f} seconds.",
        details={"path": path},
        next_step="report",
    )


def finish_read(exc: sqlite3.Error, *, path: str) -> MemoryForestError:
    """A read that does not retry. A protocol error is I/O, because this attempt is the whole budget."""
    error = index_error(exc, path=path)
    if error.code == "index_protocol":
        return protocol_spent(0.0, path=path)
    return error


# Codes a search, a status, a build or a reset can refuse with, and the next move a program follows.
# ``docs/tools.md`` renders this table; the test fails when the two disagree.
INDEX_REFUSALS = (
    ("index_busy", "retry", ""),
    ("index_cannot_open", "report", ""),
    ("index_disk_full", "report", ""),
    ("index_failed", "report", ""),
    ("index_io_error", "report", ""),
    ("index_missing", "run", "forest index build"),
    ("index_read_only", "report", ""),
    ("index_reset_refused", "report", ""),
    ("index_unreadable", "run", "forest index reset"),
    ("symlink_forbidden", "report", ""),
)


def _attempt_write(
    root: Path,
    paths: list[str] | None,
    *,
    verify: bool,
    wait_seconds: float,
    command: str,
) -> dict[str, Any]:
    """Write the index, or return a busy result.

    A held index lock is busy. A busy file (another program holds it) is busy too, and with
    ``wait_seconds`` above zero it is tried again every ``RETRY_INTERVAL_SECONDS`` until that budget is
    spent. A protocol error is retried the same way, and when the budget is spent it is an I/O refusal.
    A locked conflict inside this connection is ``index_failed`` and is not retried. Every other refusal
    is raised.
    """
    _refuse_write(root, command)
    started = time.monotonic()
    deadline = started + max(0.0, wait_seconds)
    while True:
        remaining = deadline - time.monotonic()
        try:
            with _index_writer(root, remaining if wait_seconds > 0 else 0.0):
                return _write_held(root, paths, verify=verify, busy_seconds=_busy_seconds(remaining))
        except MemoryForestError as exc:
            waited = time.monotonic() - started
            remaining = deadline - time.monotonic()
            if exc.code == "index_protocol":
                if wait_seconds <= 0 or remaining <= 0:
                    raise protocol_spent(waited, path=_index_message_path(root)) from exc
                _retry_pause(min(RETRY_INTERVAL_SECONDS, remaining))
                continue
            if exc.code not in _BUSY_CODES and exc.code != "index_busy":
                raise
            if exc.code != "index_busy" or wait_seconds <= 0 or remaining <= 0:
                # The cause the refusal named (the file in use, a page that went away), without its wait.
                cause = re.sub(r" This call waited [0-9.]+ seconds\.\Z", "", exc.message)
                message = _LOCK_HELD_MESSAGE if exc.code in _BUSY_CODES else cause or _FILE_HELD_MESSAGE
                return _busy_result(
                    message,
                    path=f"{layout.STATE_DIR}/{_current_index_name(root)}",
                    waited_seconds=waited,
                )
            _retry_pause(min(RETRY_INTERVAL_SECONDS, remaining))


def _busy_seconds(remaining: float) -> float:
    """How long SQLite itself may wait for a lock in one attempt: within the budget, never past the writer's cap."""
    return min(WRITER_TIMEOUT_SECONDS, max(RETRY_INTERVAL_SECONDS, remaining))


def _retry_pause(seconds: float) -> None:
    """The pause between two attempts on a busy file. Tests replace it to release the holder."""
    threading.Event().wait(seconds)


def _state_dir(root: Path) -> Path:
    """``.forest/`` without a path walk. The root is already real; this refuses a symlink in its place.

    A query's freshness check is counted by how many times it resolves a path. The index name is one
    component, so a ``lstat`` is enough and a walk per lookup would be paid on every search.
    """
    folder = Path(root) / layout.STATE_DIR
    try:
        info = folder.lstat()
    except FileNotFoundError:
        return folder
    if stat.S_ISLNK(info.st_mode):
        raise MemoryForestError(
            "symlink_forbidden",
            "Symlinks are not allowed inside a Memory Forest.",
            details={"path": layout.STATE_DIR},
            next_step="report",
        )
    if not stat.S_ISDIR(info.st_mode):
        raise MemoryForestError(
            "index_cannot_open",
            "The .forest folder is not a directory.",
            details={"path": layout.STATE_DIR},
            next_step="report",
        )
    return folder


def _pointer_name(blob: bytes) -> str | None:
    """The index file name in ``blob``, or None when the bytes are not exactly that name.

    A name is the file ``_publish_pointer`` writes: the name, or the name and one trailing newline.
    Anything else a regular file can hold is damaged, including bytes that are not UTF-8, a NUL, an
    empty file, white space, more than one line, a name outside the pattern, ``..`` and an absolute path.
    """
    if len(blob) > _POINTER_BYTES or b"\x00" in blob:
        return None
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if text.endswith("\n"):
        text = text[:-1]
    if "\n" in text or "\r" in text:
        return None
    if _INDEX_NAME.fullmatch(text) is None:
        return None
    return text


def _read_pointer(root: Path) -> tuple[str | None, bool]:
    """The current index file's name, and whether the pointer is damaged.

    No pointer means the legacy name. The pointer is opened first, without following a link and without
    blocking, and what was opened is then checked. A symlink pointer, or a pointer that is not a file, is
    refused before a reset writes anything. A regular file is a name or it is damaged, and so is a file
    this process cannot open. Damaged never raises from here: it is ``(None, True)``, a reset replaces it,
    and every other caller refuses it. The read stops after ``_POINTER_BYTES``, so a longer file is
    damaged without being read whole.
    """
    path = _state_dir(root) / POINTER_FILENAME
    relative = f"{layout.STATE_DIR}/{POINTER_FILENAME}"
    try:
        descriptor = os.open(path, _READ_FLAGS)
    except FileNotFoundError:
        return INDEX_FILENAME, False
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise MemoryForestError(
                "symlink_forbidden",
                "Symlinks are not allowed inside a Memory Forest. Nothing was removed.",
                details={"path": relative},
                next_step="report",
            ) from exc
        if exc.errno == errno.EISDIR:
            raise _pointer_not_a_file(relative) from exc
        return None, True
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise _pointer_not_a_file(relative)
        try:
            blob = os.read(descriptor, _POINTER_BYTES + 1)
        except OSError:
            return None, True
    finally:
        os.close(descriptor)
    name = _pointer_name(blob)
    if name is None:
        return None, True
    return name, False


def _pointer_not_a_file(relative: str) -> MemoryForestError:
    return MemoryForestError(
        "index_reset_refused",
        "The index pointer is not a file, so a new index cannot be published. Move it out of .forest/.",
        details={"path": relative},
        next_step="report",
    )


def _current_index_name(root: Path) -> str:
    """The index file every caller opens. One function, so a reset and a build cannot disagree."""
    name, bad = _read_pointer(root)
    if bad or name is None:
        state = IndexState(POINTER_DAMAGED, True, True, f"{layout.STATE_DIR}/{POINTER_FILENAME}")
        raise _unreadable_refusal(state)
    return name


@_public
def index_relative(root: str | os.PathLike[str]) -> str:
    """The forest-relative path of the current index file."""
    safe = require_real_root(root)
    return f"{layout.STATE_DIR}/{_current_index_name(safe)}"


def _family(name: str) -> tuple[str, str, str, str]:
    """Sidecars first, then the database. SQLite appends ``-wal``, ``-shm`` and ``-journal`` to the file name."""
    return (name + "-wal", name + "-shm", name + "-journal", name)


_POINTER_TEMP = re.compile(r"\.index\.current\.[a-z0-9_]{8}\.tmp\Z")


def _base_name(filename: str) -> str | None:
    for suffix in ("-journal", "-wal", "-shm"):
        if filename.endswith(suffix):
            filename = filename[: -len(suffix)]
            break
    if _INDEX_NAME.fullmatch(filename) is None:
        return None
    return filename


def _index_bases(root: Path) -> list[str]:
    """Index file names in ``.forest/``, from the directory itself. A symlink or a missing folder is none."""
    folder = _state_dir(root)
    try:
        info = folder.lstat()
    except FileNotFoundError:
        return []
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return []
    found: set[str] = set()
    with os.scandir(folder) as iterator:
        for entry in iterator:
            base = _base_name(entry.name)
            if base is not None:
                found.add(base)
    return sorted(found)


def _classify_index_file(path: Path) -> str:
    """``file``, ``left`` (a symlink or not a file) or ``absent``. Nothing is followed."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return "absent"
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return "left"
    return "file"


def _remove_index_file(path: Path) -> str:
    """Remove a regular file if it is there. A symlink, a folder and a missing file stay. ``OSError`` is left."""
    kind = _classify_index_file(path)
    if kind != "file":
        return kind
    try:
        path.unlink()
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "left"
    return "removed"


def _unused_index_name(root: Path, reserved: set[str]) -> str:
    """A generation name that is not on disk and not ``reserved``. A new index never takes an old path."""
    taken = set(reserved)
    folder = _state_dir(root)
    try:
        info = folder.lstat()
    except FileNotFoundError:
        info = None
    if info is not None and not stat.S_ISLNK(info.st_mode) and stat.S_ISDIR(info.st_mode):
        with os.scandir(folder) as iterator:
            for entry in iterator:
                taken.add(entry.name)
    for _ in range(8):
        name = f"index-{secrets.token_hex(8)}.sqlite"
        if all(candidate not in taken for candidate in _family(name)):
            return name
    raise MemoryForestError(
        "index_failed",
        "No unused name was left for a new index file.",
        details={"path": layout.STATE_DIR},
        next_step="report",
    )


def _publish_pointer(root: Path, name: str) -> None:
    """Write the pointer and sync it, then rename it into place. Readers see the old name or the new one."""
    folder = _state_dir(root)
    folder.mkdir(mode=0o700, exist_ok=True)
    # The pointer is the package's own file: owner read and write, whatever the file it replaces had.
    atomic_write(folder / POINTER_FILENAME, name + "\n", mode=0o600, keep_mode=False)


def _reset_held(root: Path, *, dry_run: bool) -> dict[str, Any]:
    """Publish a new current name, then remove the old file and any orphan of the index's own pattern."""
    current, _bad = _read_pointer(root)
    reserved = set(_family(current)) if current else set()
    new_name = _unused_index_name(root, reserved)
    ordered: list[str] = []
    seen: set[str] = set()
    for base in ([current] if current else []) + _index_bases(root):
        if base and base != new_name and base not in seen:
            seen.add(base)
            ordered.append(base)
    new_relative = f"{layout.STATE_DIR}/{new_name}"
    if dry_run:
        removed, orphans = _plan_removals(root, ordered)
        removed.extend(f"{layout.STATE_DIR}/{name}" for name in _pointer_temp_names(root))
        return {"dry_run": True, "ok": True, "orphans": orphans, "path": new_relative, "removed": removed}
    # The pointer moves first. A removal that fails after this is an orphan nothing reads.
    _publish_pointer(root, new_name)
    removed, orphans = _apply_removals(root, ordered)
    removed.extend(_remove_pointer_temps(root))
    return {
        "command": _BUILD_COMMAND,
        "dry_run": False,
        "message": "The index now points at a new file, which a build fills. Run `forest index build`.",
        "next": "run",
        "ok": True,
        "orphans": orphans,
        "path": new_relative,
        "removed": removed,
    }


def _plan_removals(root: Path, bases: list[str]) -> tuple[list[str], list[str]]:
    removed: list[str] = []
    orphans: list[str] = []
    for base in bases:
        for filename in _family(base):
            kind = _classify_index_file(_state_dir(root) / filename)
            relative = f"{layout.STATE_DIR}/{filename}"
            if kind == "file":
                removed.append(relative)
            elif kind == "left":
                orphans.append(relative)
    return removed, orphans


def _apply_removals(root: Path, bases: list[str]) -> tuple[list[str], list[str]]:
    removed: list[str] = []
    orphans: list[str] = []
    for base in bases:
        for filename in _family(base):
            relative = f"{layout.STATE_DIR}/{filename}"
            outcome = _remove_index_file(_state_dir(root) / filename)
            if outcome == "removed":
                removed.append(relative)
            elif outcome == "left":
                orphans.append(relative)
    return removed, orphans


def _remove_orphans(root: Path) -> None:
    """Remove index files that are not the current one. A symlink, a folder and a failure stay.

    Called under the index lock, from a build and from a reset. Only names of the index's own pattern
    in ``.forest/`` are considered.
    """
    current = _current_index_name(root)
    for base in _index_bases(root):
        if base == current:
            continue
        for filename in _family(base):
            _remove_index_file(_state_dir(root) / filename)
    _remove_pointer_temps(root)


def _pointer_temp_names(root: Path) -> list[str]:
    """Temporary pointer files ``atomic_write`` leaves when it is killed before the rename.

    The name is the one ``tempfile.mkstemp`` builds: ``.index.current.`` plus eight letters, digits or
    underscores, plus ``.tmp``. Nothing wider is removed.
    """
    folder = _state_dir(root)
    try:
        info = folder.lstat()
    except FileNotFoundError:
        return []
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return []
    found: list[str] = []
    with os.scandir(folder) as iterator:
        for entry in iterator:
            if _POINTER_TEMP.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                found.append(entry.name)
    return sorted(found)


def _remove_pointer_temps(root: Path) -> list[str]:
    removed: list[str] = []
    for name in _pointer_temp_names(root):
        if _remove_index_file(_state_dir(root) / name) == "removed":
            removed.append(f"{layout.STATE_DIR}/{name}")
    return removed


@contextmanager
def _index_writer(root: str | os.PathLike[str], wait_seconds: float) -> Iterator[None]:
    """Hold the right to write the index: this process's turn, then the ``index`` lock, within ``wait_seconds``."""
    wait = max(0.0, wait_seconds)
    deadline = time.monotonic() + wait
    taken = _WRITERS.acquire(timeout=wait) if wait > 0 else _WRITERS.acquire(blocking=False)
    if not taken:
        raise MemoryForestError("lock_busy", "Another thread of this process is writing the index.")
    try:
        left = max(0.0, deadline - time.monotonic())
        with hold_lock(root, LOCK_NAME, wait_seconds=left, label="forest index"):
            yield
    finally:
        _WRITERS.release()


def _busy_result(message: str, *, path: str, waited_seconds: float = 0.0) -> dict[str, Any]:
    result: dict[str, Any] = {
        "busy": True,
        "files": 0,
        "indexed": False,
        "ok": True,
        "path": path,
        "reason": "index_busy",
        "units": 0,
        "updated": 0,
        "waited_seconds": waited_seconds,
    }
    if message:
        result["message"] = message
    return result


def _write_held(
    root: Path, paths: list[str] | None, *, verify: bool, busy_seconds: float = WRITER_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """Write the index in place, in one transaction. The caller holds the index lock.

    ``paths`` None walks every file of the forest; a list updates those files. A schema, tokenizer or masking
    fingerprint that differs from the one in the file empties it and indexes every file, inside the same
    transaction. Nothing is unlinked: no journal, WAL or shared-memory file another connection may hold.
    """
    masker = _Masker(load_config(root))
    _remove_orphans(root)
    published = _index_path(root)
    published_relative = f"{layout.STATE_DIR}/{_current_index_name(root)}"
    published.parent.mkdir(mode=0o700, exist_ok=True)
    _remove_old_staging(published)
    created = not published.exists()
    tokenizer = tokenizer_name()
    connection = _open_writer(published, busy_seconds)
    try:
        connection.execute("BEGIN IMMEDIATE")
        meta = _metadata_or_none(connection)
        if meta is not None and meta.get("schema") == SCHEMA and not _probe(connection, meta):
            raise index_unreadable_error(damaged=True, path=published_relative)
        recreate = (
            meta is None
            or meta.get("schema") != SCHEMA
            or meta.get("tokenizer") != tokenizer
            or meta.get("masking") != masker.fingerprint
        )
        if recreate:
            _drop_all(connection)
            _create_schema(connection, trigram="trigram" in tokenizer)
        walk = paths is None or recreate
        present = dict(_source_files(root)) if walk else {}
        wanted = sorted(present) if walk else sorted(set(paths or ()))
        updated = 0
        for relative in wanted:
            path = present.get(relative) if walk else _file_at(root, relative)
            if path is None:
                if connection.execute("SELECT 1 FROM files WHERE path = ?", (relative,)).fetchone():
                    _drop_path(connection, relative)
                    updated += 1
                continue
            if _unchanged(connection, relative, path, verify=verify):
                continue
            _replace_path(connection, masker, relative, path, tokenizer)
            updated += 1
        if walk:
            stored = {row[0] for row in connection.execute("SELECT path FROM files")}
            for relative in sorted(stored - set(present)):
                _drop_path(connection, relative)
                updated += 1
        _set_meta(connection, "schema", SCHEMA)
        _set_meta(connection, "tokenizer", tokenizer)
        _set_meta(connection, "masking", masker.fingerprint)
        _set_meta(connection, "built_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        _set_meta(connection, "filled", json.dumps(_filled_tables(connection)))
        connection.commit()
        files = connection.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        units = connection.execute("SELECT COUNT(*) FROM units").fetchone()[0]
        _checkpoint(connection)
    except sqlite3.Error as exc:
        try:
            connection.rollback()
        except sqlite3.Error:
            pass
        raise index_error(exc, busy_message=_WRITER_HELD, path=published_relative) from exc
    except FileNotFoundError as exc:
        # A page or a lane file went away between the listing and the read. Nothing was published.
        try:
            connection.rollback()
        except sqlite3.Error:
            pass
        raise index_busy_error(
            "A page went away while the index was built, so nothing was published.", path=published_relative
        ) from exc
    except BaseException:
        try:
            connection.rollback()
        except sqlite3.Error:
            pass
        raise
    finally:
        connection.close()
    for suffix in ("", "-wal", "-shm"):
        _owner_only(Path(str(published) + suffix))
    if created:
        _fsync_directory(published.parent)
    return {
        "busy": False,
        "files": files,
        "ok": True,
        "path": published_relative,
        "tokenizer": tokenizer,
        "units": units,
        "updated": updated,
    }


def _open_writer(published: Path, busy_seconds: float = WRITER_TIMEOUT_SECONDS) -> sqlite3.Connection:
    """The published file, opened for writing in WAL mode. A file SQLite cannot read is refused, not replaced.

    The file is opened (or made) first, without following a link, and what was opened is checked before
    SQLite opens the path. Every SQLite error is classified by ``index_error``; a mode other than WAL means
    an old reader keeps the file in its rollback journal, which is busy.
    """
    relative = f"{layout.STATE_DIR}/{published.name}"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = _open_regular(published, relative, flags=flags)
    assert descriptor is not None
    try:
        connection = sqlite3.connect(published, timeout=busy_seconds, isolation_level=None)
    except sqlite3.Error as exc:
        raise index_error(exc, busy_message=_WRITER_HELD, path=relative) from exc
    finally:
        os.close(descriptor)
    try:
        mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        connection.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.Error as exc:
        connection.close()
        raise index_error(exc, busy_message=_WRITER_HELD, path=relative) from exc
    if str(mode).lower() != "wal":
        connection.close()
        raise index_busy_error(
            "The search index could not be put in WAL mode; another connection holds it.",
            path=relative,
        )
    return connection


# A busy file when opening for writing is the same cause the build tells apart: the file is in use.
_WRITER_HELD = _FILE_HELD_MESSAGE


def _checkpoint(connection: sqlite3.Connection) -> None:
    """Fold the WAL into the file and truncate it when no reader needs it. Never waits for a reader."""
    try:
        connection.execute("PRAGMA busy_timeout = 0")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        pass


def _remove_old_staging(published: Path) -> None:
    """A staging copy an earlier release left behind. No connection reads it: it was never published."""
    staging = published.with_name(published.name + _STAGING_SUFFIX)
    for leftover in (staging, Path(str(staging) + "-journal")):
        _remove_index_file(leftover)


def _owner_only(path: Path) -> None:
    """Mode 0600 on a file of the index, through a descriptor: a link put in its place is not followed."""
    try:
        descriptor = os.open(path, _READ_FLAGS)
    except OSError:
        return
    try:
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _metadata_or_none(connection: sqlite3.Connection) -> dict[str, str] | None:
    """The metadata, or None for a file with no schema yet. Any other error is the caller's to classify."""
    try:
        return _metadata(connection)
    except sqlite3.Error as exc:
        if _no_schema_yet(exc):
            return None
        raise


def _drop_all(connection: sqlite3.Connection) -> None:
    """Every table of the file, virtual tables first (their own tables go with them)."""
    for kind in ("virtual", "plain"):
        rows = connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for name, sql in rows:
            virtual = str(sql or "").upper().startswith("CREATE VIRTUAL TABLE")
            if (kind == "virtual") != virtual:
                continue
            connection.execute(f'DROP TABLE IF EXISTS "{name.replace(chr(34), chr(34) * 2)}"')


# The columns each plain table is read for. The probe selects exactly these, as the reads do.
_SELECTED_COLUMNS = {
    "cjk_grams": ("gram", "unit_id"),
    "files": (
        "path", "size", "mtime_ns", "sha256", "verified_at", "undecodable", "torn", "indexed_bytes",
        "indexed_lines", "prefix_sha256",
    ),
    "metadata": ("key", "value"),
    "times": ("unit_id", "start_utc", "end_utc", "zone"),
    "units": (
        "id", "path", "address", "layer", "tree", "agent", "kind", "subjects", "heading", "text",
        "line_start", "line_end", "page_zone",
    ),
    "vectors": ("unit_id", "vector"),
}
# The word indexes a search matches against.
_MATCHED_TABLES = ("units_words", "units_tri")


@functools.lru_cache(maxsize=2)
def expected_objects(trigram: bool) -> frozenset[tuple[str, str]]:
    """Every table, index and shadow table this schema makes, as ``(type, name)``.

    Read from the schema itself, built once in memory, so an object added to ``_create_schema`` is
    checked without anyone listing it.
    """
    connection = sqlite3.connect(":memory:")
    try:
        _create_schema(connection, trigram=trigram)
        rows = connection.execute(
            "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
    finally:
        connection.close()
    return frozenset((str(kind), str(name)) for kind, name in rows)


def _filled_tables(connection: sqlite3.Connection) -> list[str]:
    """The tables that hold at least one row, at the commit that writes this list."""
    names = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return [name for name in names if connection.execute(f'SELECT 1 FROM "{name}" LIMIT 1').fetchone()]


def _probe(connection: sqlite3.Connection, meta: dict[str, str]) -> bool:
    """Whether a query can run on this file: what a search or a timeline read touches, touched once.

    Every object of the schema is there; each plain table is read for the columns the code selects; each
    word index is matched; and every table that held rows at the last commit still holds one. An error of
    a missing or corrupt object is damage (False). Any other error (busy, I/O) is raised for its own
    refusal.
    """
    trigram = "trigram" in str(meta.get("tokenizer") or "")
    try:
        try:
            filled = json.loads(meta.get("filled") or "")
        except ValueError:
            return False
        if not isinstance(filled, list):
            return False
        present = {
            (str(kind), str(name))
            for kind, name in connection.execute("SELECT type, name FROM sqlite_master")
        }
        if not expected_objects(trigram) <= present:
            return False
        for table, columns in _SELECTED_COLUMNS.items():
            connection.execute(f"SELECT {', '.join(columns)} FROM {table} LIMIT 1").fetchall()
        for table in _MATCHED_TABLES[: 2 if trigram else 1]:
            connection.execute(
                f"SELECT rowid, heading, text FROM {table} WHERE {table} MATCH ? LIMIT 1", ('"probe"',)
            ).fetchall()
        tables = {name for kind, name in present if kind == "table"}
        for name in filled:
            if not isinstance(name, str) or name not in tables:
                return False
            if connection.execute(f'SELECT 1 FROM "{name}" LIMIT 1').fetchone() is None:
                return False
    except sqlite3.Error as exc:
        if sqlite_error_kind(exc) in ("other", "corrupt"):
            return False
        raise
    return True


def _create_schema(connection: sqlite3.Connection, *, trigram: bool) -> None:
    statements = [
        "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        """CREATE TABLE files (
            path TEXT PRIMARY KEY,
            size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            verified_at TEXT NOT NULL,
            undecodable INTEGER NOT NULL,
            torn INTEGER NOT NULL,
            indexed_bytes INTEGER NOT NULL,
            indexed_lines INTEGER NOT NULL,
            prefix_sha256 TEXT NOT NULL
        )""",
        """CREATE TABLE units (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL,
            address TEXT NOT NULL,
            layer TEXT NOT NULL,
            tree TEXT NOT NULL,
            agent TEXT NOT NULL,
            kind TEXT NOT NULL,
            subjects TEXT NOT NULL,
            heading TEXT NOT NULL,
            text TEXT NOT NULL,
            line_start INTEGER NOT NULL,
            line_end INTEGER NOT NULL,
            page_zone TEXT NOT NULL
        )""",
        "CREATE INDEX units_path ON units(path)",
        "CREATE INDEX units_address ON units(address)",
        """CREATE TABLE times (
            unit_id INTEGER NOT NULL,
            start_utc TEXT NOT NULL,
            end_utc TEXT NOT NULL,
            zone TEXT NOT NULL
        )""",
        "CREATE INDEX times_start ON times(start_utc)",
        "CREATE INDEX times_unit ON times(unit_id)",
        "CREATE TABLE vectors (unit_id INTEGER PRIMARY KEY, vector TEXT NOT NULL)",
        "CREATE VIRTUAL TABLE units_words USING fts5(heading, text, tokenize = 'unicode61 remove_diacritics 2')",
        "CREATE TABLE cjk_grams (gram TEXT NOT NULL, unit_id INTEGER NOT NULL)",
        "CREATE INDEX cjk_grams_gram ON cjk_grams(gram)",
        "CREATE INDEX cjk_grams_unit ON cjk_grams(unit_id)",
    ]
    if trigram:
        statements.append("CREATE VIRTUAL TABLE units_tri USING fts5(heading, text, tokenize = 'trigram')")
    # One statement at a time: ``executescript`` would commit the transaction this runs in.
    for statement in statements:
        connection.execute(statement)


def _source_files(root: Path) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for layer in layout.FOREST_LAYERS:
        for relative in layout.iter_pages(root, (layer,)):
            found.append((relative, layout.resolve(root, relative)))
    found.extend((relative, item[0]) for relative, item in _lane_daily_listing(root).items())
    return found


def _lane_daily_listing(root: Path) -> dict[str, tuple[Path, int, int]]:
    """Daily pages and raw-lane files with their sizes and times, from one listing of their folders.

    The two top folders are resolved once; below them a symlink is refused from the listing itself, so a query
    costs one ``scandir`` per folder and one ``stat`` per file, never a path walk per file.
    """
    found: dict[str, tuple[Path, int, int]] = {}
    daily = layout.resolve(root, layout.DAILY)
    if daily.is_symlink():
        raise MemoryForestError(
            "symlink_forbidden",
            "Symlinks are not allowed inside a Memory Forest.",
            next_step="report",
        )
    if daily.is_dir():
        stack: list[tuple[str, str]] = [(str(daily), layout.DAILY)]
        while stack:
            folder, prefix = stack.pop()
            with os.scandir(folder) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        raise MemoryForestError(
                            "symlink_forbidden",
                            "Symlinks are not allowed inside a Memory Forest.",
                            next_step="report",
                        )
                    relative = f"{prefix}/{entry.name}"
                    if entry.is_dir(follow_symlinks=False):
                        stack.append((entry.path, relative))
                    elif entry.is_file(follow_symlinks=False) and entry.name.endswith(".md"):
                        try:
                            info = entry.stat(follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        found[relative] = (Path(entry.path), info.st_size, info.st_mtime_ns)
    top = layout.resolve(root, layout.ISTM)
    if not top.exists():
        return found
    if top.is_symlink() or not top.is_dir():
        raise MemoryForestError(
            "symlink_forbidden",
            "Symlinks are not allowed inside a Memory Forest.",
            next_step="report",
        )
    with os.scandir(top) as agents:
        lanes = sorted(agents, key=lambda entry: entry.name)
    for agent in lanes:
        if agent.is_symlink():
            raise MemoryForestError(
                "symlink_forbidden",
                "Symlinks are not allowed inside a Memory Forest.",
                next_step="report",
            )
        if agent.name.startswith(".") or not agent.is_dir(follow_symlinks=False):
            continue
        with os.scandir(agent.path) as entries:
            for entry in entries:
                if entry.is_symlink():
                    raise MemoryForestError(
                        "symlink_forbidden",
                        "Symlinks are not allowed inside a Memory Forest.",
                        next_step="report",
                    )
                if entry.name.startswith(".") or not entry.name.endswith(".jsonl"):
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                found[f"{layout.ISTM}/{agent.name}/{entry.name}"] = (
                    Path(entry.path), info.st_size, info.st_mtime_ns,
                )
    return dict(sorted(found.items()))


def _file_at(root: Path, relative: str) -> Path | None:
    try:
        path = layout.resolve(root, layout.normalize(relative))
    except MemoryForestError:
        return None
    if path.is_symlink() or not path.is_file():
        return None
    return path


def _unchanged(connection: sqlite3.Connection, relative: str, path: Path, *, verify: bool) -> bool:
    """Size and time first. Hash when ``verify`` is set or the stored hash is older than a day."""
    info = path.lstat()
    row = connection.execute(
        "SELECT size, mtime_ns, sha256, verified_at FROM files WHERE path = ?", (relative,)
    ).fetchone()
    if row is None or row[0] != info.st_size or row[1] != info.st_mtime_ns:
        return False
    if not verify and not _verification_due(row[3]):
        return True
    if _hash(path) != row[2]:
        return False
    connection.execute(
        "UPDATE files SET verified_at = ? WHERE path = ?",
        (_verified_stamp(), relative),
    )
    return True


def _is_lane(relative: str) -> bool:
    return relative.startswith(layout.ISTM + "/") and relative.endswith(".jsonl")


def _replace_path(
    connection: sqlite3.Connection,
    masker: _Masker,
    relative: str,
    path: Path,
    tokenizer: str,
) -> None:
    with _open_page(path) as handle:
        info = os.fstat(handle.fileno())
        if _is_lane(relative):
            _index_lane(connection, masker, relative, handle, info, tokenizer)
            return
        data = handle.read()
    digest = hashlib.sha256(data).hexdigest()
    text, undecodable = _decode(data)
    verified = _verified_stamp()
    row = connection.execute("SELECT sha256 FROM files WHERE path = ?", (relative,)).fetchone()
    if row is not None and row[0] == digest:
        connection.execute(
            "UPDATE files SET size = ?, mtime_ns = ?, verified_at = ?, undecodable = ? WHERE path = ?",
            (info.st_size, info.st_mtime_ns, verified, int(undecodable), relative),
        )
        return
    _drop_path(connection, relative)
    _insert_units(connection, masker, units_of(relative, text, masker.config), tokenizer)
    connection.execute(
        "INSERT INTO files (path, size, mtime_ns, sha256, verified_at, undecodable, torn, indexed_bytes, "
        "indexed_lines, prefix_sha256) VALUES (?, ?, ?, ?, ?, ?, 0, ?, 0, ?)",
        (relative, info.st_size, info.st_mtime_ns, digest, verified, int(undecodable), len(data), digest),
    )


def _index_lane(
    connection: sqlite3.Connection,
    masker: _Masker,
    relative: str,
    handle: Any,
    info: os.stat_result,
    tokenizer: str,
) -> None:
    """A raw lane only grows. The bytes already indexed are read and hashed; only the new complete lines are parsed.

    ``indexed_bytes`` is the end of the last complete line indexed and ``prefix_sha256`` the hash of the bytes
    before it. Those bytes are read again, in 1 MiB pieces, to prove the file was only appended to. A
    half-written last line (no newline yet) is left for the next update. A file that shrank or whose indexed
    bytes changed is indexed again whole.
    """
    row = connection.execute(
        "SELECT sha256, undecodable, torn, indexed_bytes, indexed_lines, prefix_sha256 FROM files WHERE path = ?",
        (relative,),
    ).fetchone()
    verified = _verified_stamp()
    if row is not None and 0 < row[3] <= info.st_size:
        prefix = hashlib.sha256()
        remaining = int(row[3])
        while remaining:
            chunk = handle.read(min(1 << 20, remaining))
            if not chunk:
                break
            prefix.update(chunk)
            remaining -= len(chunk)
        if remaining == 0 and prefix.hexdigest() == row[5]:
            tail = handle.read()
            whole = prefix.copy()
            whole.update(tail)
            cut = tail.rfind(b"\n") + 1
            added = tail[:cut]
            prefix.update(added)
            text, undecodable = _decode(added)
            units, torn = _turn_units(relative, text, layout.classify(relative), masker.config,
                                      first_line=int(row[4]) + 1)
            _insert_units(connection, masker, units, tokenizer)
            connection.execute(
                "UPDATE files SET size = ?, mtime_ns = ?, sha256 = ?, verified_at = ?, undecodable = ?, "
                "torn = ?, indexed_bytes = ?, indexed_lines = ?, prefix_sha256 = ? WHERE path = ?",
                (
                    info.st_size, info.st_mtime_ns, whole.hexdigest(), verified,
                    int(bool(row[1]) or undecodable), int(bool(row[2]) or torn),
                    int(row[3]) + cut, int(row[4]) + added.count(b"\n"), prefix.hexdigest(), relative,
                ),
            )
            return
        handle.seek(0)
    data = handle.read()
    digest = hashlib.sha256(data).hexdigest()
    if row is not None and row[0] == digest:
        connection.execute(
            "UPDATE files SET size = ?, mtime_ns = ?, verified_at = ? WHERE path = ?",
            (info.st_size, info.st_mtime_ns, verified, relative),
        )
        return
    cut = data.rfind(b"\n") + 1
    complete = data[:cut]
    text, undecodable = _decode(complete)
    units, torn = _turn_units(relative, text, layout.classify(relative), masker.config)
    _drop_path(connection, relative)
    _insert_units(connection, masker, units, tokenizer)
    connection.execute(
        "INSERT INTO files (path, size, mtime_ns, sha256, verified_at, undecodable, torn, indexed_bytes, "
        "indexed_lines, prefix_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            relative, info.st_size, info.st_mtime_ns, digest, verified, int(undecodable), int(torn), cut,
            complete.count(b"\n"), hashlib.sha256(complete).hexdigest(),
        ),
    )


def _insert_units(
    connection: sqlite3.Connection,
    masker: _Masker,
    units: list[dict[str, Any]],
    tokenizer: str,
) -> None:
    """Insert the units as a read would show them, each table in one statement."""
    if not units:
        return
    config = masker.config
    shown_units = [
        {**unit, "heading": masker.shown(unit["heading"]), "text": masker.shown(unit["text"])} for unit in units
    ]
    provider = embeddings.get(config.embeddings.provider)
    vectors: Sequence[Sequence[float]] | None = None
    if provider is not None:
        try:
            vectors = provider.embed([unit["text"] for unit in shown_units])
        except Exception as exc:
            raise MemoryForestError(
                "embeddings_failed",
                "The registered embedding provider failed. Nothing was published.",
                details={"provider": provider.name},
            ) from exc
        if len(vectors) != len(shown_units):
            raise MemoryForestError(
                "embeddings_failed",
                "The embedding provider returned a different number of vectors than texts.",
                details={"provider": provider.name},
            )
    first = int(connection.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM units").fetchone()[0])
    ids = range(first, first + len(shown_units))
    connection.executemany(
        "INSERT INTO units (id, path, address, layer, tree, agent, kind, subjects, heading, text, "
        "line_start, line_end, page_zone) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                unit_id, unit["path"], unit["address"], unit["layer"], unit["tree"], unit["agent"], unit["kind"],
                json.dumps(unit["subjects"], ensure_ascii=False), unit["heading"], unit["text"],
                unit["line_start"], unit["line_end"], unit["page_zone"],
            )
            for unit_id, unit in zip(ids, shown_units)
        ],
    )
    words = [(unit_id, unit["heading"], unit["text"]) for unit_id, unit in zip(ids, shown_units)]
    connection.executemany("INSERT INTO units_words (rowid, heading, text) VALUES (?, ?, ?)", words)
    if "trigram" in tokenizer:
        connection.executemany("INSERT INTO units_tri (rowid, heading, text) VALUES (?, ?, ?)", words)
    connection.executemany(
        "INSERT INTO times (unit_id, start_utc, end_utc, zone) VALUES (?, ?, ?, ?)",
        [
            (unit_id, _stamp(start), _stamp(end), zone)
            for unit_id, unit in zip(ids, shown_units)
            for start, end, zone in unit["times"]
        ],
    )
    if vectors is not None:
        connection.executemany(
            "INSERT INTO vectors (unit_id, vector) VALUES (?, ?)",
            [(unit_id, json.dumps(list(vector))) for unit_id, vector in zip(ids, vectors)],
        )
    connection.executemany(
        "INSERT INTO cjk_grams (gram, unit_id) VALUES (?, ?)",
        [
            (gram, unit_id)
            for unit_id, unit in zip(ids, shown_units)
            for gram in _cjk_grams(unit["heading"] + "\n" + unit["text"])
        ],
    )


def _drop_path(connection: sqlite3.Connection, relative: str) -> None:
    owned = "(SELECT id FROM units WHERE path = ?)"
    connection.execute(f"DELETE FROM units_words WHERE rowid IN {owned}", (relative,))
    if _has_trigram(connection):
        connection.execute(f"DELETE FROM units_tri WHERE rowid IN {owned}", (relative,))
    connection.execute(f"DELETE FROM times WHERE unit_id IN {owned}", (relative,))
    connection.execute(f"DELETE FROM vectors WHERE unit_id IN {owned}", (relative,))
    connection.execute(f"DELETE FROM cjk_grams WHERE unit_id IN {owned}", (relative,))
    connection.execute("DELETE FROM units WHERE path = ?", (relative,))
    connection.execute("DELETE FROM files WHERE path = ?", (relative,))


def _has_trigram(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'units_tri'"
    ).fetchone()
    return row is not None


def _stale_files(root: Path, connection: sqlite3.Connection) -> list[str]:
    present = {relative: path for relative, path in _source_files(root)}
    stored = {
        row[0]: (row[1], row[2], row[3])
        for row in connection.execute("SELECT path, size, mtime_ns, sha256 FROM files")
    }
    stale: list[str] = []
    for relative, path in present.items():
        old = stored.get(relative)
        try:
            info = path.lstat()
            if old is None or old[0] != info.st_size or old[1] != info.st_mtime_ns:
                if old is None or _hash(path) != old[2]:
                    stale.append(relative)
        except FileNotFoundError:
            # Gone since the listing: changed since the build.
            stale.append(relative)
    for relative in stored:
        if relative not in present:
            stale.append(relative)
    return sorted(stale)


def _metadata(connection: sqlite3.Connection) -> dict[str, str]:
    return {row[0]: row[1] for row in connection.execute("SELECT key, value FROM metadata")}


def _set_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT INTO metadata (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _open_page(path: Path) -> Any:
    """A page or lane file, opened first without following a link or blocking, then checked to be a file.

    A file that went away raises ``FileNotFoundError`` for the caller to name. A symlink put in its place
    is ``symlink_forbidden``, and anything else that is not a regular file is ``non_regular_file``.
    """
    try:
        descriptor = os.open(path, _READ_FLAGS)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise MemoryForestError(
                "symlink_forbidden", "Symlinks are not allowed inside a Memory Forest.", next_step="report"
            ) from exc
        raise
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise MemoryForestError(
            "non_regular_file", "A forest page is not a regular file.", next_step="report"
        )
    return os.fdopen(descriptor, "rb")


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with _open_page(path) as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decode(data: bytes) -> tuple[str, bool]:
    """Bytes as text. A page that is not UTF-8 is read with replacement characters."""
    try:
        return data.decode("utf-8"), False
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace"), True


def _read_text(path: Path) -> tuple[str, bool]:
    """The file as text. A page that is not UTF-8 is read with replacement characters."""
    with _open_page(path) as handle:
        return _decode(handle.read())


def _verified_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _verification_due(stamp: str | None) -> bool:
    if not stamp:
        return True
    try:
        verified = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - verified >= VERIFY_INTERVAL


def _flagged(connection: sqlite3.Connection, column: str) -> list[str]:
    if column not in {"undecodable", "torn"}:
        return []
    rows = connection.execute(f"SELECT path FROM files WHERE {column} = 1 ORDER BY path")
    return [row[0] for row in rows]


def _verify_due(connection: sqlite3.Connection) -> list[str]:
    found = []
    for path, stamp in connection.execute("SELECT path, verified_at FROM files ORDER BY path"):
        if _verification_due(stamp):
            found.append(path)
    return found


def _cjk_ids(connection: sqlite3.Connection, tokens: Sequence[str]) -> set[int]:
    """Units whose CJK grams contain each token, including a short token inside a longer word."""
    found: set[int] = set()
    for token in tokens:
        for run in _CJK_RUN.findall(token):
            found.update(_units_for_run(connection, run))
    return found


def _units_for_run(connection: sqlite3.Connection, run: str) -> set[int]:
    grams = [run] if len(run) == 1 else [run[index : index + 2] for index in range(len(run) - 1)]
    matched: set[int] | None = None
    for gram in grams:
        rows = {
            int(row[0])
            for row in connection.execute("SELECT unit_id FROM cjk_grams WHERE gram = ?", (gram,))
        }
        matched = rows if matched is None else matched & rows
        if not matched:
            return set()
    return matched or set()


def _cjk_grams(text: str) -> list[str]:
    """Unigrams and bigrams of each uninterrupted CJK run, so a short word is found inside a longer one."""
    found: list[str] = []
    seen: set[str] = set()
    for run in _CJK_RUN.findall(text):
        for index, character in enumerate(run):
            if character not in seen:
                seen.add(character)
                found.append(character)
            if index:
                pair = run[index - 1 : index + 1]
                if pair not in seen:
                    seen.add(pair)
                    found.append(pair)
    return found


_CJK_RUN = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]+")


def _index_path(root: Path) -> Path:
    """The current index file. Every caller asks here, so a reset and a reader cannot disagree.

    A symlink at the file is refused. A missing file is the path a build will create. The name is one
    component matched against the index pattern, so this does not walk the forest.
    """
    name = _current_index_name(root)
    path = _state_dir(root) / name
    try:
        info = path.lstat()
    except FileNotFoundError:
        return path
    if stat.S_ISLNK(info.st_mode):
        raise MemoryForestError(
            "symlink_forbidden",
            "Symlinks are not allowed inside a Memory Forest.",
            details={"path": f"{layout.STATE_DIR}/{name}"},
            next_step="report",
        )
    return path


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_stamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


# ─────────────────────────────── units ───────────────────────────────


def units_of(relative: str, text: str, config: Config) -> list[dict[str, Any]]:
    """The index rows of one file, in file order."""
    info = layout.classify(relative)
    if info.role is layout.PageRole.RAW_FILE:
        return _turn_units(relative, text, info, config)[0]
    if info.role is layout.PageRole.DAILY_PAGE:
        return _daily_units(relative, text, config)
    return _page_units(relative, text, info, config)


def _page_units(relative: str, text: str, info: layout.PathInfo, config: Config) -> list[dict[str, Any]]:
    lines = text.splitlines()
    page = parse_page(text)
    zone = _page_zone(page, config)
    subjects = _page_subjects(page)
    entries = _entry_table(text) if info.role is layout.PageRole.COLLECTION_PAGE else {}
    headings = _heading_lines(lines)
    units: list[dict[str, Any]] = []
    used: set[str] = set()
    preamble_end = headings[0] if headings else len(lines)
    body_start = _frontmatter_end(lines)
    if any(line.strip() for line in lines[body_start:preamble_end]):
        units.append(
            _unit(
                relative,
                _unique_address(relative, "head", used),
                info,
                "head",
                subjects,
                "",
                "\n".join(lines[body_start:preamble_end]),
                body_start + 1,
                preamble_end,
                zone,
                config,
            )
        )
    names = [item[0] for item in entries.values()]
    timeline_by_entry: dict[str, list[tuple[datetime, datetime, str]]] = {}
    for index, start in enumerate(headings):
        end = headings[index + 1] if index + 1 < len(headings) else len(lines)
        heading = _H2_RE.fullmatch(lines[start])
        title = heading["text"] if heading else lines[start][3:].strip()
        entry = entries.get(start + 1)
        if entry is not None:
            address = _unique_address(relative, entry[1], used)
            kind = "entry"
            entry_subjects = [entry[1], *subjects]
        else:
            slug = layout.slugify(title) or f"s{start + 1}"
            address = _unique_address(relative, slug, used)
            kind = "section"
            entry_subjects = list(subjects)
        unit = _unit(
            relative, address, info, kind, entry_subjects, title,
            "\n".join(lines[start:end]), start + 1, end, zone, config,
        )
        units.append(unit)
        if entry is not None:
            timeline_by_entry.setdefault(entry[0], []).append(unit)
    timeline = next((unit for unit in units if unit["heading"].casefold() == "timeline"), None)
    if timeline is not None and names:
        for line in timeline["text"].splitlines():
            owner = owner_of(line, names)
            if owner is None:
                continue
            parsed = TimelineLine.match(line)
            if parsed is None:
                continue
            interval = _timeline_interval(parsed, zone, config.zone)
            if interval is None:
                continue
            for unit in timeline_by_entry.get(owner, ()):
                unit["times"].append(interval)
    return units


def _daily_units(relative: str, text: str, config: Config) -> list[dict[str, Any]]:
    lines = text.splitlines()
    marks = [index for index, line in enumerate(lines) if _BLOCK_RE.fullmatch(line)]
    info = layout.classify(relative)
    zone = config.zone
    if not marks:
        return [
            _unit(
                relative, f"{relative}#page", info, "block", [], "", text, 1, len(lines) or 1, zone, config,
                agent="",
            )
        ]
    units = []
    if marks[0] > 0 and any(line.strip() for line in lines[: marks[0]]):
        units.append(
            _unit(
                relative, f"{relative}#preamble", info, "block", [], "",
                "\n".join(lines[: marks[0]]), 1, marks[0], zone, config,
            )
        )
    for position, start in enumerate(marks):
        match = _BLOCK_RE.fullmatch(lines[start])
        assert match is not None
        agent, stamp = match.group(1), match.group(2)
        end = marks[position + 1] if position + 1 < len(marks) else len(lines)
        moment = _parse_moment(stamp, zone)
        slug = layout.slugify(f"{agent}-{stamp}") or f"block-{start + 1}"
        unit = _unit(
            relative, f"{relative}#{slug}", info, "block", [agent] if layout.is_slug(agent) else [],
            f"{agent} {stamp}", "\n".join(lines[start:end]), start + 1, end, zone, config, agent=agent,
        )
        if moment is not None:
            following = None
            if position + 1 < len(marks):
                next_match = _BLOCK_RE.fullmatch(lines[marks[position + 1]])
                if next_match is not None:
                    following = _parse_moment(next_match.group(2), zone)
            stop = following if following is not None and following > moment else _end_of_local_day(moment)
            if stop <= moment:
                stop = moment + timedelta(microseconds=1)
            unit["times"] = [(moment, stop, _zone_name(moment)), *unit["times"]]
        units.append(unit)
    return units


def _turn_units(
    relative: str,
    text: str,
    info: layout.PathInfo,
    config: Config,
    *,
    first_line: int = 1,
) -> tuple[list[dict[str, Any]], bool]:
    """The turns of raw-lane text, its first line numbered ``first_line``, and whether a line is not JSON.

    A lane is one JSON object per line, split on newlines only (a turn's text may hold other line separators).
    """
    units = []
    torn = False
    for number, line in enumerate(_lane_lines(text), start=first_line):
        unit = _turn_unit(relative, number, line, info, config)
        if unit is None and line.strip() and not _is_json(line):
            torn = True
        if unit is not None:
            units.append(unit)
    return units, torn


def _lane_lines(text: str) -> list[str]:
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def _is_json(line: str) -> bool:
    try:
        json.loads(line)
    except json.JSONDecodeError:
        return False
    return True


def _turn_unit(
    relative: str, number: int, line: str, info: layout.PathInfo, config: Config
) -> dict[str, Any] | None:
    """One line of a lane as a unit, or None for a blank line or one that is not a JSON object."""
    if not line.strip():
        return None
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    agent = info.tree or ""
    body = value.get("text") if isinstance(value.get("text"), str) else ""
    if not body:
        body = " ".join(str(item) for item in value.values() if isinstance(item, str))
    turn_id = value.get("id") if isinstance(value.get("id"), str) and value.get("id") else f"L{number}"
    moment = _parse_moment(str(value.get("at") or ""), config.zone)
    unit = _unit(
        relative, f"{relative}#{turn_id}", info, "turn", [agent] if agent else [],
        turn_id, body, number, number, config.zone, config, agent=agent,
    )
    if moment is not None:
        unit["times"] = [(moment, moment + timedelta(microseconds=1), _zone_name(moment))]
    return unit


def _unit(
    relative: str,
    address: str,
    info: layout.PathInfo,
    kind: str,
    subjects: list[str],
    heading: str,
    text: str,
    line_start: int,
    line_end: int,
    zone: ZoneInfo,
    config: Config,
    *,
    agent: str = "",
) -> dict[str, Any]:
    layer = PurePosixPath(relative).parts[0]
    tree = "" if kind in {"turn", "block"} or info.role in (layout.PageRole.DAILY_PAGE, layout.PageRole.ROOT_INDEX) else (info.tree or "")
    times = _times_in(text, zone, config.zone) if kind != "turn" else []
    return {
        "address": address,
        "agent": agent,
        "heading": heading,
        "kind": kind,
        "layer": layer,
        "line_end": max(line_start, line_end),
        "line_start": line_start,
        "path": relative,
        "subjects": [item for item in subjects if item],
        "page_zone": zone.key if isinstance(zone, ZoneInfo) else _zone_name(datetime.now(zone)),
        "text": text,
        "times": times,
        "tree": tree,
    }


def _entry_table(text: str) -> dict[int, tuple[str, str]]:
    """Map a heading's 1-based line to ``(entry name, entry id)``."""
    found, _problems = registry_mod.parse_collection_page(text)
    return {entry.line: (entry.name, entry.id) for entry in found}


def _page_subjects(page: Any) -> list[str]:
    subjects: list[str] = []
    entity = page.field("entity").strip()
    if entity:
        subjects.append(entity)
    for item in about_ids(page.field("about") or None):
        if item not in subjects:
            subjects.append(item)
    return subjects


def _page_zone(page: Any, config: Config) -> ZoneInfo:
    name = page.field("timezone").strip()
    if not name:
        return config.zone
    try:
        return ZoneInfo(name)
    except Exception:
        return config.zone


def _heading_lines(lines: list[str]) -> list[int]:
    found = []
    fenced = False
    for index, line in enumerate(lines):
        if _FENCE_RE.match(line):
            fenced = not fenced
            continue
        if not fenced and _H2_RE.fullmatch(line):
            found.append(index)
    return found


def _frontmatter_end(lines: list[str]) -> int:
    if not lines or lines[0].strip() != "---":
        return 0
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return index + 1
    return 0


def _unique_address(relative: str, slug: str, used: set[str]) -> str:
    base = slug or "section"
    name = base
    number = 2
    while f"{relative}#{name}" in used:
        name = f"{base}-{number}"
        number += 1
    address = f"{relative}#{name}"
    used.add(address)
    return address


def _times_in(text: str, zone: ZoneInfo, forest_zone: ZoneInfo) -> list[tuple[datetime, datetime, str]]:
    found = []
    for line in text.splitlines():
        parsed = TimelineLine.match(line)
        if parsed is None:
            continue
        interval = _timeline_interval(parsed, zone, forest_zone)
        if interval is not None:
            found.append(interval)
    return found


def _timeline_interval(
    line: TimelineLine, page_zone: ZoneInfo, forest_zone: ZoneInfo
) -> tuple[datetime, datetime, str] | None:
    zone = _zone_of(line.zone, page_zone, forest_zone)
    span = _calendar_span(line.date, zone)
    if span is None:
        return None
    start, end = span
    if line.start and re.fullmatch(r"\d{4}-\d{2}-\d{2}", line.date):
        clocked = _clock(start, line.start)
        if clocked is None:
            return None
        start = clocked
        if line.end:
            stop = _clock(span[0], line.end)
            if stop is None:
                return None
            if stop <= start:
                stop += timedelta(days=1)
            end = stop
        else:
            end = start + timedelta(microseconds=1)
    return start, end, line.zone or _zone_name(start)


def _calendar_span(date_text: str, zone: ZoneInfo) -> tuple[datetime, datetime] | None:
    try:
        return _calendar_span_unchecked(date_text, zone)
    except ValueError:
        return None


def _calendar_span_unchecked(date_text: str, zone: ZoneInfo) -> tuple[datetime, datetime] | None:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
        year, month, day = (int(part) for part in date_text.split("-"))
        start = datetime(year, month, day, tzinfo=zone)
        return start, start + timedelta(days=1)
    if re.fullmatch(r"\d{4}-\d{2}", date_text):
        year, month = (int(part) for part in date_text.split("-"))
        start = datetime(year, month, 1, tzinfo=zone)
        end = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1, tzinfo=zone)
        return start, end
    quarter = re.fullmatch(r"(\d{4})-q([1-4])", date_text)
    if quarter:
        year = int(quarter.group(1))
        month = (int(quarter.group(2)) - 1) * 3 + 1
        start = datetime(year, month, 1, tzinfo=zone)
        end_month = month + 3
        end_year = year + (end_month > 12)
        end_month = end_month - 12 if end_month > 12 else end_month
        return start, datetime(end_year, end_month, 1, tzinfo=zone)
    if re.fullmatch(r"\d{4}", date_text):
        year = int(date_text)
        return datetime(year, 1, 1, tzinfo=zone), datetime(year + 1, 1, 1, tzinfo=zone)
    return None


def _clock(day: datetime, clock: str) -> datetime | None:
    try:
        hour, minute = (int(part) for part in clock.split(":", 1))
        if hour > 23 or minute > 59:
            return None
        return day.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except ValueError:
        return None


def _zone_of(name: str | None, page_zone: ZoneInfo, forest_zone: ZoneInfo) -> ZoneInfo | timezone:
    if not name:
        return page_zone or forest_zone
    parsed = _fixed_zone(name)
    if parsed is not None:
        return parsed
    try:
        return ZoneInfo(name)
    except Exception:
        return page_zone or forest_zone


def _fixed_zone(name: str) -> timezone | ZoneInfo | None:
    if name in {"Z", "UTC"}:
        return timezone.utc
    match = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", name)
    if match is None:
        return None
    sign = 1 if match.group(1) == "+" else -1
    return timezone(sign * timedelta(hours=int(match.group(2)), minutes=int(match.group(3))))


def _zone_name(moment: datetime) -> str:
    """A zone name that parses again: an area name, ``UTC``, or a numeric offset."""
    name = moment.tzname() or ""
    if "/" in name:
        return name
    offset = moment.utcoffset() or timedelta(0)
    if offset == timedelta(0):
        return "UTC"
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{total % 3600 // 60:02d}"


def _parse_moment(value: str, fallback: ZoneInfo) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=fallback)
    return parsed


def _end_of_local_day(moment: datetime) -> datetime:
    zone = moment.tzinfo or timezone.utc
    local = moment.astimezone(zone)
    start = datetime(local.year, local.month, local.day, tzinfo=zone)
    return start + timedelta(days=1)


# ─────────────────────────────── search ───────────────────────────────


@_public
def query(
    root: str | os.PathLike[str],
    question: str,
    *,
    reader: str | None = None,
    named: Iterable[str] = (),
    limit: int = 10,
    config: Config | None = None,
) -> dict[str, Any]:
    """Registry names first, then words, then vectors when a provider is registered."""
    safe = require_real_root(root)
    config = config or load_config(safe)
    words = question.strip()
    if not words:
        raise MemoryForestError("search_empty", "Search needs the words to look for.")
    if limit < 1:
        raise MemoryForestError("search_limit", "The limit is at least 1.", details={"limit": limit})
    stale = refresh_lanes_and_daily(safe)
    built = registry_mod.build(safe, config=config)
    scope = for_reader(config, built, reader, named=named, root=safe)
    started = time.monotonic()
    deadline = started + READER_TIMEOUT_SECONDS

    def read(connection: sqlite3.Connection | None) -> dict[str, Any]:
        if connection is None:
            raise MemoryForestError(
                "index_missing",
                "The forest has no search index. Run `forest index build`.",
                details={"path": _index_message_path(safe)},
                next_step="run",
                command="forest index build",
            )
        try:
            pages: dict[str, dict[str, Any]] = {}
            _add_alias_hits(connection, pages, words, built, scope)
            _add_word_hits(connection, pages, words, scope)
            vector_note = _add_vector_hits(connection, pages, words, config, scope)
            ordered = sorted(pages.values(), key=lambda item: (item["tier"], -item["score"], item["path"]))[:limit]
            payload = _envelope(scope, ordered, vectors=vector_note, question=words)
            if stale:
                payload["stale"] = stale
            if is_read_only(connection):
                payload["read_only"] = True
            return payload
        except sqlite3.Error as exc:
            # ``index_protocol`` goes to the loop below, which alone decides whether time is left.
            raise index_error(exc, path=_index_message_path(safe)) from exc

    while True:
        try:
            return _checked_read(safe, read)
        except MemoryForestError as exc:
            if exc.code != "index_protocol":
                raise
            now = time.monotonic()
            if now >= deadline:
                raise protocol_spent(now - started, path=str(exc.details.get("path") or "")) from exc
            remaining = deadline - now
        _retry_pause(min(RETRY_INTERVAL_SECONDS, remaining))


@_public
def read(
    root: str | os.PathLike[str],
    spec: str,
    *,
    reader: str | None = None,
    named: Iterable[str] = (),
    lines: tuple[int, int] | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """The page, or the addressed section, as written. Masking is the caller's, at the edge that prints."""
    safe = require_real_root(root)
    config = config or load_config(safe)
    path, _, address = spec.strip().partition("#")
    path = layout.normalize(path)
    _refuse_outside_path(path)
    info = layout.classify(path)
    built = registry_mod.build(safe, config=config)
    scope = for_reader(config, built, reader, named=named, root=safe)
    if not scope.allows(info):
        raise MemoryForestError(
            "out_of_scope",
            "That path is outside this reader's trees and lane.",
            details={"path": path},
        )
    try:
        file_text, undecodable = _read_text(layout.resolve(safe, path, must_exist=True))
    except MemoryForestError:
        raise
    except OSError as exc:
        raise MemoryForestError("read_failed", "The page could not be read.", details={"path": path}) from exc
    start, end, body, heading = _addressed(path, file_text, address, config)
    if lines is not None:
        want_start, want_end = lines
        if want_start < 1 or want_end < want_start:
            raise MemoryForestError("read_lines", "Lines are written A-B, with A at least 1.")
        overlap_start = max(start, want_start)
        overlap_end = min(end, want_end)
        if overlap_start > overlap_end:
            raise MemoryForestError(
                "read_lines_empty",
                "Those lines fall outside the addressed section.",
                details={"lines": f"{want_start}-{want_end}", "path": path},
            )
        file_lines = _file_lines(path, file_text)
        body = "\n".join(file_lines[overlap_start - 1 : overlap_end])
        start, end = overlap_start, overlap_end
    tree = info.tree or ""
    payload = _envelope(
        scope,
        [
            {
                "path": path,
                "score": 1,
                "sections": [
                    {
                        "address": f"{path}#{address}" if address else path,
                        "agent": info.tree if info.role is layout.PageRole.RAW_FILE else "",
                        "heading": heading,
                        "kind": "read",
                        "line_end": end,
                        "line_start": start,
                        "text": body,
                        "tree": tree if info.role is not layout.PageRole.RAW_FILE else "",
                        "why": ["read"],
                    }
                ],
                "tier": 0,
                "tree": tree if tree in scope.trees else "",
                "why": ["read"],
            }
        ],
    )
    if undecodable:
        payload["status"] = f"undecodable: {path}"
        payload["undecodable"] = [path]
    return payload


@_public
def navigate(
    root: str | os.PathLike[str],
    *,
    reader: str | None = None,
    path: str | None = None,
    named: Iterable[str] = (),
    config: Config | None = None,
) -> dict[str, Any]:
    """One level down from the root index. Each returned line is a line of the page, not a summary."""
    safe = require_real_root(root)
    config = config or load_config(safe)
    built = registry_mod.build(safe, config=config)
    scope = for_reader(config, built, reader, named=named, root=safe)
    target = _navigate_target(path, built, scope)
    info = layout.classify(target)
    if not scope.allows(info):
        raise MemoryForestError(
            "out_of_scope",
            "That path is outside this reader's trees and lane.",
            details={"path": target},
        )
    try:
        file_text, undecodable = _read_text(layout.resolve(safe, target, must_exist=True))
    except OSError as exc:
        raise MemoryForestError("read_failed", "The page could not be read.", details={"path": target}) from exc
    kept = [
        {"line": number, "text": line}
        for number, line in enumerate(file_text.splitlines(), start=1)
        if line.strip() and _line_visible(line, scope, built)
    ]
    payload = {
        "consulted": list(scope.trees),
        "lanes": [],
        "ok": True,
        "path": target,
        "reader": scope.reader,
        "results": [
            {
                "path": target,
                "sections": [
                    {
                        "address": target,
                        "heading": "",
                        "kind": "navigate",
                        "line_end": kept[-1]["line"] if kept else 0,
                        "line_start": kept[0]["line"] if kept else 0,
                        "text": "\n".join(item["text"] for item in kept),
                        "why": ["navigate"],
                    }
                ],
                "tree": info.tree or "",
                "why": ["navigate"],
            }
        ],
        "trees": [info.tree] if info.tree and info.tree in scope.trees else [],
    }
    if undecodable:
        payload["status"] = f"undecodable: {target}"
        payload["undecodable"] = [target]
    return payload


def _refuse_outside_path(path: str) -> None:
    """A path the caller wrote that is not a forest path at all: absolute, or climbing out with ``..``.

    It is told apart from a forest path outside the reader's scope, so the answer says what is wrong.
    """
    if path.startswith("/") or ".." in path.split("/"):
        raise MemoryForestError(
            "path_escape",
            "A forest path is written relative to the forest root, without `..`. This one is not.",
            details={"path": path},
        )


def _navigate_target(path: str | None, built: registry_mod.Registry, scope: Scope) -> str:
    if path is None or not path.strip():
        return layout.ROOT_INDEX
    text = layout.normalize(path)
    _refuse_outside_path(text)
    if "/" not in text and layout.is_slug(text):
        if text not in scope.trees:
            raise MemoryForestError(
                "out_of_scope",
                "That tree is outside this reader's scope.",
                details={"path": text},
            )
        if text in built.tree_kinds:
            return layout.tree_page(text)
    return text


def _line_visible(line: str, scope: Scope, built: registry_mod.Registry) -> bool:
    from .model import code_spans

    for span in code_spans(line):
        named = span.text.strip().strip("`")
        if _hides(named, scope, built):
            return False
    return True


def _hides(named: str, scope: Scope, built: registry_mod.Registry) -> bool:
    if not named:
        return False
    if "/" in named or named.endswith(".md"):
        info = layout.classify(named if named.endswith(".md") else named)
        return not scope.allows(info)
    if layout.is_slug(named) and named in built.tree_kinds and named not in scope.trees:
        return True
    return False


def _add_alias_hits(
    connection: sqlite3.Connection,
    pages: dict[str, dict[str, Any]],
    question: str,
    built: registry_mod.Registry,
    scope: Scope,
) -> None:
    folded = normalize_name(question)
    matches: list[tuple[int, str, str, str, str, list[str]]] = []
    for subject_id, subject in built.entities.items():
        labels = [(subject.get("name") or "", "name"), (subject_id, "id")]
        labels += [(alias, "alias") for alias in subject.get("aliases") or ()]
        for text, kind in labels:
            key = normalize_name(text)
            if key and (len(key) >= 2 or key == folded) and _contains_name(folded, key):
                home = str(subject.get("home") or "")
                about = list(subject.get("about_leaves") or built.about_index.get(subject_id) or ())
                matches.append((len(key), subject_id, text, kind, home, about))
        for entry in subject.get("entries") or ():
            labels = [(entry.get("name") or "", "name"), (entry.get("id") or "", "id")]
            labels += [(alias, "alias") for alias in entry.get("aliases") or ()]
            for text, kind in labels:
                key = normalize_name(text)
                if not key or (len(key) < 2 and key != folded) or not _contains_name(folded, key):
                    continue
                home = f"{entry.get('page')}#{entry.get('id')}"
                about = list(entry.get("about_leaves") or ())
                matches.append((len(key), str(entry.get("id")), text, kind, home, about))
    matches.sort(key=lambda item: -item[0])
    seen: set[tuple[str, str]] = set()
    for length, subject_id, text, kind, home, about in matches:
        if (subject_id, home) in seen:
            continue
        seen.add((subject_id, home))
        why = f'{kind}: "{text}" names {subject_id}'
        _attach(connection, pages, home, scope, why, tier=0, score=length + 1000)
        for leaf in about:
            if (subject_id, leaf) in seen:
                continue
            seen.add((subject_id, leaf))
            _attach(connection, pages, leaf, scope, f"about: names {subject_id}", tier=0, score=length)


def _attach(
    connection: sqlite3.Connection,
    pages: dict[str, dict[str, Any]],
    spec: str,
    scope: Scope,
    why: str,
    *,
    tier: int,
    score: int,
) -> None:
    path, _, address = spec.partition("#")
    path = layout.normalize(path)
    info = layout.classify(path)
    if not scope.allows(info):
        return
    rows = connection.execute(
        "SELECT * FROM units WHERE path = ? ORDER BY line_start, id", (path,)
    ).fetchall()
    if address:
        rows = [row for row in rows if row["address"] == f"{path}#{address}"] or rows[:1]
    elif rows:
        heads = [row for row in rows if row["kind"] == "head"]
        rows = heads[:1] or rows[:1]
    for row in rows:
        _add_row(pages, _row_dict(row), why, tier=tier, score=score, scope=scope)


def _add_word_hits(
    connection: sqlite3.Connection,
    pages: dict[str, dict[str, Any]],
    question: str,
    scope: Scope,
) -> None:
    tokens = _tokens(question)
    if not tokens:
        return
    ids: set[int] = set()
    quoted = [_quote(token) for token in tokens]
    if quoted:
        try:
            for row in connection.execute(
                "SELECT rowid FROM units_words WHERE units_words MATCH ?",
                (" OR ".join(quoted),),
            ):
                ids.add(int(row[0]))
        except sqlite3.OperationalError as exc:
            if not query_text_fault(exc):
                raise
    if _has_trigram(connection):
        for token in tokens:
            if len(token) < 3:
                continue
            try:
                for row in connection.execute(
                    "SELECT rowid FROM units_tri WHERE units_tri MATCH ?",
                    (_quote(token),),
                ):
                    ids.add(int(row[0]))
            except sqlite3.OperationalError as exc:
                if not query_text_fault(exc):
                    raise
    ids.update(_cjk_ids(connection, tokens))
    if not ids:
        return
    for row in _units_by_id(connection, ids):
        unit = _row_dict(row)
        if not _visible(scope, unit["path"]):
            continue
        matched = [token for token in tokens if _token_in(token, unit["heading"] + "\n" + unit["text"])]
        if not matched:
            continue
        _add_row(pages, unit, "words: " + ", ".join(matched), tier=1, score=len(matched), scope=scope)


def _units_by_id(connection: sqlite3.Connection, ids: Iterable[int]) -> list[sqlite3.Row]:
    """The unit rows of ``ids``, in id order, asked ``IN_CHUNK`` ids at a time so no statement passes
    SQLite's limit on host parameters, whichever build of SQLite is linked."""
    ordered = sorted(set(ids))
    rows: list[sqlite3.Row] = []
    for start in range(0, len(ordered), IN_CHUNK):
        chunk = ordered[start : start + IN_CHUNK]
        rows.extend(
            connection.execute(
                f"SELECT * FROM units WHERE id IN ({','.join('?' for _ in chunk)}) ORDER BY id", tuple(chunk)
            )
        )
    return rows


def _add_vector_hits(
    connection: sqlite3.Connection,
    pages: dict[str, dict[str, Any]],
    question: str,
    config: Config,
    scope: Scope,
) -> str:
    if config.embeddings.provider == "none":
        return "off"
    provider = embeddings.get(config.embeddings.provider)
    if provider is None:
        return "skipped"
    stored = connection.execute("SELECT unit_id, vector FROM vectors").fetchall()
    if not stored:
        return "skipped"
    try:
        query_vector = list(provider.embed([question])[0])
    except Exception as exc:
        raise MemoryForestError(
            "embeddings_failed",
            "The registered embedding provider failed. No network call was made by this package.",
            details={"provider": provider.name},
        ) from exc
    ranked: list[tuple[float, int]] = []
    for unit_id, raw in stored:
        try:
            vector = json.loads(raw)
        except json.JSONDecodeError:
            continue
        score = _cosine(query_vector, vector)
        if score >= VECTOR_FLOOR:
            ranked.append((score, int(unit_id)))
    ranked.sort(key=lambda item: -item[0])
    if not ranked:
        return "used"
    ids = [unit_id for _score, unit_id in ranked]
    rows = {int(row["id"]): row for row in _units_by_id(connection, ids)}
    for score, unit_id in ranked:
        row = rows.get(unit_id)
        if row is None:
            continue
        unit = _row_dict(row)
        if not _visible(scope, unit["path"]):
            continue
        _add_row(pages, unit, f"vector: {score:.2f}", tier=2, score=int(score * 100), scope=scope)
    return "used"


def _add_row(
    pages: dict[str, dict[str, Any]],
    unit: dict[str, Any],
    why: str,
    *,
    tier: int,
    score: int,
    scope: Scope,
) -> None:
    tree = unit["tree"] or (unit["agent"] if unit["agent"] in scope.trees else "")
    page = pages.get(unit["path"])
    if page is None:
        page = {"path": unit["path"], "score": score, "sections": [], "tier": tier, "tree": tree, "why": []}
        pages[unit["path"]] = page
    page["tier"] = min(page["tier"], tier)
    page["score"] = max(page["score"], score)
    if tree and not page["tree"]:
        page["tree"] = tree
    section = next((item for item in page["sections"] if item["address"] == unit["address"]), None)
    if section is None:
        section = {
            "address": unit["address"],
            "agent": unit["agent"],
            "heading": unit["heading"],
            "kind": unit["kind"],
            "line_end": unit["line_end"],
            "line_start": unit["line_start"],
            "text": unit["text"],
            "tree": tree,
            "why": [],
        }
        page["sections"].append(section)
    if why not in section["why"]:
        section["why"].append(why)
    if why not in page["why"]:
        page["why"].append(why)


def _envelope(
    scope: Scope,
    pages: list[dict[str, Any]],
    *,
    vectors: str = "off",
    question: str | None = None,
) -> dict[str, Any]:
    lanes = sorted({section["agent"] for page in pages for section in page["sections"] if section.get("agent")})
    trees = sorted({page["tree"] for page in pages if page.get("tree")})
    results = []
    for page in pages:
        results.append(
            {
                "path": page["path"],
                "sections": [
                    {
                        "address": section["address"],
                        "heading": section["heading"],
                        "kind": section["kind"],
                        "lines": [section["line_start"], section["line_end"]],
                        "text": section["text"],
                        "why": list(section["why"]),
                    }
                    for section in page["sections"]
                ],
                "tree": page.get("tree") or "",
                "why": list(page["why"]),
            }
        )
    payload: dict[str, Any] = {
        "consulted": list(scope.trees),
        "lanes": lanes,
        "ok": True,
        "reader": scope.reader,
        "results": results,
        "trees": trees,
        "vectors": vectors,
    }
    if question is not None:
        payload["question"] = question
    return payload


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "address": row["address"],
        "agent": row["agent"],
        "heading": row["heading"],
        "kind": row["kind"],
        "line_end": row["line_end"],
        "line_start": row["line_start"],
        "path": row["path"],
        "subjects": json.loads(row["subjects"]),
        "text": row["text"],
        "tree": row["tree"],
    }


def _visible(scope: Scope, path: str) -> bool:
    return scope.allows(layout.classify(path))


def _contains_name(question: str, name: str) -> bool:
    if _CJK_RE.search(name):
        return name in question
    start = 0
    while True:
        index = question.find(name, start)
        if index < 0:
            return False
        before = question[index - 1] if index else " "
        after = question[index + len(name)] if index + len(name) < len(question) else " "
        if not before.isalnum() and not after.isalnum():
            return True
        start = index + 1


def _tokens(question: str) -> list[str]:
    tokens = []
    for raw in normalize_name(question).split():
        token = raw.strip(_TOKEN_STRIP)
        if token:
            tokens.append(token)
    return tokens


def _token_in(token: str, text: str) -> bool:
    haystack = normalize_name(text)
    if _CJK_RE.search(token):
        return token in haystack
    return _contains_name(haystack, token)


def _quote(token: str) -> str:
    return '"' + token.replace('"', '""') + '"'


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _file_lines(path: str, text: str) -> list[str]:
    """The lines of a file as the index numbers them: a raw lane splits on newlines only."""
    return _lane_lines(text) if _is_lane(path) else text.splitlines()


def _addressed(path: str, text: str, address: str, config: Config) -> tuple[int, int, str, str]:
    lines = _file_lines(path, text)
    if not address:
        return 1, len(lines) or 1, text, ""
    if path.startswith(layout.ISTM + "/"):
        for number, line in enumerate(lines, start=1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                value = None
            raw_id = value.get("id") if isinstance(value, dict) else None
            turn_id = raw_id if isinstance(raw_id, str) and raw_id else f"L{number}"
            if turn_id == address:
                return number, number, line, address
        raise MemoryForestError("address_unknown", "That address is not a turn in the file.", details={"path": path})
    for unit in units_of(path, text, config):
        fragment = unit["address"].split("#", 1)[1]
        if fragment == address:
            return unit["line_start"], unit["line_end"], unit["text"], unit["heading"]
    raise MemoryForestError(
        "address_unknown",
        "That address is not a section of the page.",
        details={"path": path},
    )


def mask_result(value: Any, *, config: Config) -> Any:
    """What a command or the MCP server returns: sign-in values masked, numbers starred."""
    return show_text(value, config=config)


def parse_lines(text: str | None) -> tuple[int, int] | None:
    """``A-B`` as a 1-based inclusive range, or None when ``text`` is empty."""
    if text is None or not str(text).strip():
        return None
    match = re.fullmatch(r"(\d+)-(\d+)", str(text).strip())
    if match is None:
        raise MemoryForestError("read_lines", "Lines are written A-B, for example 4-12.")
    start, end = int(match.group(1)), int(match.group(2))
    if start < 1 or end < start:
        raise MemoryForestError("read_lines", "Lines are written A-B, with A at least 1.")
    return start, end
