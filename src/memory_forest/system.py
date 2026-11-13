"""The one door to the operating system's file system.

Every module of the package that looks at a path, lists a folder, reads, writes, appends, removes or
locks a file, or opens the search index for SQLite, does it through the functions here. A test reads
the package's syntax tree and proves no other module does, and another runs every command and tool an
agent can call with the file functions wrapped and proves it again by running. This is also where
everything that differs between operating systems belongs: what this module does on a system is what
the package does there.

**One answer type.** A call succeeds, or it raises an ``Outcome``, one of a closed set taken from what
the system answered (its error number), never guessed:

- ``absent``: nothing is there (``ENOENT``). Nothing else is ever called absent.
- ``denied``: this account may not ``read``, ``list``, ``enter`` or ``write`` it, and ``access`` says
  which; for ``enter``, ``folder`` is the folder that cannot be entered.
- ``not_a_file`` and ``not_a_folder``: a folder, a fifo, a device or a socket where a file was asked for
  (``found`` says which), and the reverse.
- ``link``: a link where none is followed.
- ``changed``: what the path names is no longer what was opened.
- ``busy``: the file is in use (``EAGAIN``, ``EBUSY``), or a call met its deadline.
- ``other``: anything else, with the error's name in ``error``.

``Outcome`` is an ``OSError`` with the system's error number, so code that is not through the door yet
still catches it as it caught the error before.

**Open first, then look.** A file is opened without following a link in the last part of its path and
without blocking, and the open descriptor is then checked. What was checked is what is read: the
search index is handed to SQLite as that descriptor where the system offers a way (``/dev/fd/N`` names
the open file itself; the door checks that on the descriptor before using it), and in every mode the
door compares what the path names before a row is trusted and after the read.

**Counted, and made to fail, by tests only.** ``watching()`` counts every call, the bytes read and the
SQLite statements, and can make the Nth call answer any outcome it can answer, or the Nth statement
fail with any class of SQLite error. ``at()`` runs a test's function at a named point. Nothing but a
test turns them on: no environment variable and no setting does.
"""

from __future__ import annotations

import errno
import os
import secrets
import sqlite3
import stat
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - a system without fcntl has no lock of this kind
    fcntl = None  # type: ignore[assignment]


# ─────────────────────────────── outcomes ───────────────────────────────

KINDS = ("absent", "denied", "not_a_file", "not_a_folder", "link", "changed", "busy", "other")
ACCESSES = ("read", "list", "enter", "write")


class Outcome(OSError):
    """What the system answered when a call did not succeed. ``kind`` is one of ``KINDS``."""

    kind = "other"

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        number: int | None = None,
        access: str = "",
        folder: str | os.PathLike[str] = "",
        found: str = "",
        note: str = "",
    ) -> None:
        number = _DEFAULT_NUMBER.get(self.kind, errno.EIO) if number is None else number
        super().__init__(number, os.strerror(number), os.fspath(path))
        self.path = os.fspath(path)
        self.access = access
        self.folder = os.fspath(folder) if folder else ""
        self.found = found
        self.note = note

    @property
    def error(self) -> str:
        """The system's name for the error number (``EIO``), or ``deadline`` for a call that met its deadline."""
        if self.note == "deadline":
            return "deadline"
        return errno.errorcode.get(self.errno or 0, str(self.errno))

    def where(self) -> str:
        """The path this answer is about: the folder that cannot be entered, or the path itself."""
        return self.folder or self.path


class Absent(Outcome, FileNotFoundError):
    kind = "absent"


class Denied(Outcome, PermissionError):
    kind = "denied"


class NotAFile(Outcome, IsADirectoryError):
    kind = "not_a_file"


class NotAFolder(Outcome, NotADirectoryError):
    kind = "not_a_folder"


class Link(Outcome):
    kind = "link"


class Changed(Outcome):
    kind = "changed"


class Busy(Outcome, BlockingIOError):
    kind = "busy"


class Other(Outcome):
    kind = "other"


BY_KIND: dict[str, type[Outcome]] = {
    cls.kind: cls for cls in (Absent, Denied, NotAFile, NotAFolder, Link, Changed, Busy, Other)
}
_DEFAULT_NUMBER = {
    "absent": errno.ENOENT,
    "denied": errno.EACCES,
    "not_a_file": errno.EISDIR,
    "not_a_folder": errno.ENOTDIR,
    "link": errno.ELOOP,
    "changed": errno.ESTALE,
    "busy": errno.EAGAIN,
    "other": errno.EIO,
}
_BUSY_NUMBERS = {errno.EAGAIN, errno.EWOULDBLOCK, errno.EBUSY, getattr(errno, "ETXTBSY", errno.EBUSY)}
_DENIED_NUMBERS = {errno.EACCES, errno.EPERM, errno.EROFS}


# ─────────────────────────────── counting, failing and points (tests only) ───────────────────────────────

# What each call can answer, besides success. The sweep of the tests makes the Nth call answer each of these.
CAN: dict[str, tuple[str, ...]] = {
    "look": ("absent", "denied", "not_a_folder", "other"),
    "listing": ("absent", "denied", "not_a_folder", "link", "other"),
    "read": ("absent", "denied", "not_a_file", "not_a_folder", "link", "busy", "other"),
    "open_file": ("absent", "denied", "not_a_file", "not_a_folder", "link", "busy", "other"),
    "make_folder": ("absent", "denied", "not_a_folder", "link", "other"),
    "write_whole": ("absent", "denied", "not_a_file", "link", "other"),
    "cut_back": ("denied", "not_a_file", "link", "other"),
    "create_new": ("absent", "denied", "other"),
    "append": ("absent", "denied", "not_a_file", "link", "busy", "other"),
    "remove": ("denied", "not_a_file", "other"),
    "remove_folder": ("absent", "denied", "not_a_folder", "other"),
    "set_mode": ("absent", "denied", "link", "other"),
    "sync_folder": ("absent", "denied", "other"),
    "may": (),
    "real_path": (),
    "lock": ("absent", "denied", "not_a_file", "link", "other"),
    "lock_free": ("denied", "not_a_file", "link", "other"),
    "open_index": ("absent", "denied", "not_a_file", "not_a_folder", "link", "changed", "busy", "other"),
    "same_index": ("changed",),
}
# The classes of SQLite error a statement can be made to fail with: (primary code, name, class, text).
SQLITE_FAULTS: dict[str, tuple[int, str, type[sqlite3.Error], str]] = {
    "busy": (5, "SQLITE_BUSY", sqlite3.OperationalError, "database is locked"),
    "locked": (6, "SQLITE_LOCKED", sqlite3.OperationalError, "database table is locked"),
    "not_a_database": (26, "SQLITE_NOTADB", sqlite3.DatabaseError, "file is not a database"),
    "damaged": (11, "SQLITE_CORRUPT", sqlite3.DatabaseError, "database disk image is malformed"),
    "protocol": (15, "SQLITE_PROTOCOL", sqlite3.OperationalError, "locking protocol"),
    "disk": (10, "SQLITE_IOERR", sqlite3.OperationalError, "disk I/O error"),
    "full": (13, "SQLITE_FULL", sqlite3.OperationalError, "database or disk is full"),
    "read_only": (8, "SQLITE_READONLY", sqlite3.OperationalError, "attempt to write a readonly database"),
    "other": (1, "SQLITE_ERROR", sqlite3.OperationalError, "SQL logic error"),
}


@dataclass
class Watch:
    """What the door did while a test watched: each call by name and path, the bytes read, the statements."""

    fail_calls: Mapping[int, str] = field(default_factory=dict)
    fail_statements: Mapping[int, str] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    bytes_read: int = 0
    statements: int = 0
    statement_texts: list[str] = field(default_factory=list)
    injected: list[tuple[int, str, str]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


_WATCH: Watch | None = None
_POINTS: dict[str, Callable[..., None]] = {}


@contextmanager
def watching(
    *, fail_calls: Mapping[int, str] | None = None, fail_statements: Mapping[int, str] | None = None
) -> Iterator[Watch]:
    """Count what the door does inside the block; with ``fail_calls`` the Nth call (from 1) answers that kind,
    and with ``fail_statements`` the Nth SQLite statement fails with that class (``SQLITE_FAULTS``)."""
    global _WATCH
    if _WATCH is not None:
        raise RuntimeError("the door is already watched")
    watch = Watch(dict(fail_calls or {}), dict(fail_statements or {}))
    _WATCH = watch
    try:
        yield watch
    finally:
        _WATCH = None


@contextmanager
def at(name: str, function: Callable[..., None]) -> Iterator[None]:
    """Run ``function`` at the door's named point ``name`` inside the block (a test's hook)."""
    if name in _POINTS:
        raise RuntimeError(f"the point {name} is already taken")
    _POINTS[name] = function
    try:
        yield
    finally:
        _POINTS.pop(name, None)


# The named points a test can use.
POINT_INDEX_OPEN = "index: between the door's look and SQLite's open"


def _point(name: str, **info: Any) -> None:
    hook = _POINTS.get(name)
    if hook is not None:
        hook(**info)


def _enter(name: str, path: str | os.PathLike[str], access: str) -> None:
    """Count one call of the door; a test that asked for it makes this call answer an outcome."""
    watch = _WATCH
    if watch is None:
        return
    with watch.lock:
        watch.calls.append((name, os.fspath(path)))
        number = len(watch.calls)
        kind = watch.fail_calls.get(number)
        if kind is not None:
            watch.injected.append((number, name, kind))
    if kind is not None:
        raise _made(kind, path, access)


def _made(kind: str, path: str | os.PathLike[str], access: str) -> Outcome:
    """The outcome a test asked for, shaped as this call would answer it."""
    cls = BY_KIND[kind]
    if kind == "denied":
        return cls(path, access=access or "read", folder=os.path.dirname(os.fspath(path)) if access == "enter" else "")
    if kind == "not_a_file":
        return cls(path, found="folder")
    return cls(path)


def _count_bytes(count: int) -> None:
    watch = _WATCH
    if watch is not None and count:
        with watch.lock:
            watch.bytes_read += count


def _statement(sql: str) -> None:
    watch = _WATCH
    if watch is None:
        return
    with watch.lock:
        watch.statements += 1
        watch.statement_texts.append(" ".join(sql.split())[:80])
        kind = watch.fail_statements.get(watch.statements)
        if kind is not None:
            watch.injected.append((watch.statements, "statement", kind))
    if kind is not None:
        code, name, cls, text = SQLITE_FAULTS[kind]
        error = cls(text)
        error.sqlite_errorcode = code  # type: ignore[attr-defined]
        error.sqlite_errorname = name  # type: ignore[attr-defined]
        raise error


# ─────────────────────────────── reading the system's answer ───────────────────────────────


def _classify(exc: OSError, path: str | os.PathLike[str], access: str) -> Outcome:
    """The outcome for an error the system returned while this call did ``access`` on ``path``."""
    if isinstance(exc, Outcome):
        return exc
    number = exc.errno or errno.EIO
    if number == errno.ENOENT:
        return Absent(path, number=number)
    if number == errno.ELOOP:
        return Link(path, number=number)
    if number == errno.ENOTDIR:
        return NotAFolder(path, number=number)
    if number == errno.EISDIR:
        return NotAFile(path, number=number, found="folder")
    if number in _BUSY_NUMBERS:
        return Busy(path, number=number)
    if number in _DENIED_NUMBERS:
        return _denied(path, number, access)
    return Other(path, number=number)


def _denied(path: str | os.PathLike[str], number: int, access: str) -> Outcome:
    """Which access was missing. A path whose own look fails was not reached: a folder above it cannot be entered."""
    text = os.fspath(path)
    if number == errno.EROFS:
        return Denied(text, number=number, access="write", folder=os.path.dirname(text))
    if access in ("read", "list"):
        try:
            os.lstat(text)
        except PermissionError:
            return Denied(text, number=number, access="enter", folder=_unenterable(text))
        except OSError:
            pass
        return Denied(text, number=number, access=access)
    if access == "write":
        parent = os.path.dirname(text) or "."
        try:
            os.lstat(parent)
        except PermissionError:
            return Denied(text, number=number, access="enter", folder=_unenterable(parent))
        except OSError:
            pass
        if not os.access(parent, os.X_OK):
            return Denied(text, number=number, access="enter", folder=parent)
        if number == errno.EPERM:
            try:
                info = os.lstat(text)
            except OSError:
                info = None
            if info is not None and stat.S_ISDIR(info.st_mode):
                return NotAFile(text, number=number, found="folder")
        if os.path.lexists(text) and os.access(parent, os.W_OK | os.X_OK):
            # The folder can be written: what cannot be written is the file itself.
            return Denied(text, number=number, access="write")
        return Denied(text, number=number, access="write", folder=parent)
    return Denied(text, number=number, access="enter", folder=_unenterable(text))


def _unenterable(path: str) -> str:
    """The deepest folder above ``path`` that can be looked at: the one this account cannot enter."""
    current = os.path.dirname(path)
    while current and current != os.path.dirname(current):
        try:
            os.lstat(current)
        except PermissionError:
            current = os.path.dirname(current)
            continue
        except OSError:
            return current
        return current
    return current or "."  # a relative path whose first folder cannot be looked at: the working folder


def outcome_of(exc: OSError, path: str | os.PathLike[str] = "", access: str = "read") -> Outcome:
    """The outcome for an ``OSError`` met outside the door (a socket, a stream): by its error number."""
    return _classify(exc, path or exc.filename or "", access)


# ─────────────────────────────── looking ───────────────────────────────

_KIND_OF = (
    (stat.S_ISREG, "file"),
    (stat.S_ISDIR, "folder"),
    (stat.S_ISLNK, "link"),
    (stat.S_ISFIFO, "fifo"),
    (stat.S_ISSOCK, "socket"),
    (stat.S_ISCHR, "device"),
    (stat.S_ISBLK, "device"),
)


def kind_of(mode: int) -> str:
    """``file``, ``folder``, ``link``, ``fifo``, ``socket`` or ``device`` for a stat mode."""
    return next((name for test, name in _KIND_OF if test(mode)), "device")


@dataclass(frozen=True, slots=True)
class Look:
    """What stands at a path, as ``lstat`` (or ``fstat`` of an open file) answered."""

    kind: str
    mode: int
    size: int
    mtime_ns: int
    dev: int
    ino: int
    uid: int

    @classmethod
    def of(cls, info: os.stat_result) -> Look:
        return cls(
            kind_of(info.st_mode), stat.S_IMODE(info.st_mode), info.st_size, info.st_mtime_ns,
            info.st_dev, info.st_ino, info.st_uid,
        )

    @property
    def identity(self) -> tuple[int, int]:
        return (self.dev, self.ino)


def look(path: str | os.PathLike[str]) -> Look:
    """What stands at ``path``, without following a link in its last part."""
    _enter("look", path, "enter")
    try:
        return Look.of(os.lstat(path))
    except OSError as exc:
        raise _classify(exc, path, "enter") from None


def look_or_none(path: str | os.PathLike[str]) -> Look | None:
    """What stands at ``path``, or None when nothing is there. Every other outcome is raised."""
    try:
        return look(path)
    except Absent:
        return None


def kind(path: str | os.PathLike[str]) -> str | None:
    """The kind of what stands at ``path`` (``file``, ``folder``, ``link``, ...), or None when nothing is there."""
    there = look_or_none(path)
    return None if there is None else there.kind


def may(path: str | os.PathLike[str], access: str) -> bool:
    """Whether this account may ``read``, ``list``, ``enter`` or ``write`` (create and remove in) ``path``, or
    ``change`` the file at it.

    The system's own access check, for the real user. A folder is written when it can be written and entered.
    """
    _enter("may", path, "")
    flags = {
        "read": os.R_OK, "list": os.R_OK, "enter": os.X_OK, "write": os.W_OK | os.X_OK, "change": os.W_OK,
    }[access]
    return os.access(path, flags)


@dataclass(frozen=True, slots=True)
class Entry:
    """One name of a listing: what stands there, and its whole look when the listing was asked for it."""

    name: str
    kind: str
    path: Path
    look: Look | None = None


_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(
    os, "O_CLOEXEC", 0
) | getattr(os, "O_NONBLOCK", 0)


def listing(folder: str | os.PathLike[str], *, looks: bool = False) -> list[Entry]:
    """The names in ``folder``, sorted, each with what stands there; nothing is followed.

    The folder is opened first, without following a link, and the names are read from that descriptor.
    A name that went away before its look is not listed. With ``looks`` each entry carries its ``lstat``.
    """
    _enter("listing", folder, "list")
    base = Path(folder)
    try:
        descriptor = os.open(folder, _DIRECTORY_FLAGS)
    except OSError as exc:
        if exc.errno in (errno.ENOTDIR, errno.ELOOP):
            raise _not_folder_or_link(folder, exc) from None
        raise _classify(exc, folder, "list") from None
    found: list[Entry] = []
    try:
        with os.scandir(descriptor) as entries:
            for entry in entries:
                try:
                    if looks:
                        seen = Look.of(entry.stat(follow_symlinks=False))
                        kind = seen.kind
                    else:
                        seen = None
                        if entry.is_symlink():
                            kind = "link"
                        elif entry.is_dir(follow_symlinks=False):
                            kind = "folder"
                        elif entry.is_file(follow_symlinks=False):
                            kind = "file"
                        else:
                            kind = kind_of(entry.stat(follow_symlinks=False).st_mode)
                except FileNotFoundError:
                    continue
                found.append(Entry(entry.name, kind, base / entry.name, seen))
    except OSError as exc:
        raise _classify(exc, folder, "list") from None
    finally:
        os.close(descriptor)
    return sorted(found, key=lambda item: item.name)


def _not_folder_or_link(path: str | os.PathLike[str], exc: OSError) -> Outcome:
    """``O_DIRECTORY`` with ``O_NOFOLLOW`` refused: a link stands there, or something that is not a folder."""
    try:
        info = os.lstat(path)
    except OSError:
        return _classify(exc, path, "list")
    if stat.S_ISLNK(info.st_mode):
        return Link(path, number=errno.ELOOP)
    return NotAFolder(path, number=errno.ENOTDIR, found=kind_of(info.st_mode))


# ─────────────────────────────── reading ───────────────────────────────

_READ_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
_CHUNK = 1 << 20


def _open_checked(path: str | os.PathLike[str], flags: int, mode: int, access: str) -> tuple[int, os.stat_result]:
    """Open ``path`` without following a link or blocking, then check the descriptor is a regular file."""
    try:
        descriptor = os.open(path, flags, mode)
    except OSError as exc:
        if exc.errno not in (errno.ENOENT, errno.EACCES, errno.EPERM, errno.ELOOP, errno.EROFS):
            # A fifo with no reader (ENXIO), a socket, a device: what stands there is not a regular file.
            try:
                there = os.lstat(path)
            except OSError:
                there = None
            if there is not None and not stat.S_ISREG(there.st_mode) and not stat.S_ISLNK(there.st_mode):
                raise NotAFile(path, number=exc.errno, found=kind_of(there.st_mode)) from None
        raise _classify(exc, path, access) from None
    try:
        info = os.fstat(descriptor)
    except OSError as exc:
        os.close(descriptor)
        raise _classify(exc, path, access) from None
    if not stat.S_ISREG(info.st_mode):
        os.close(descriptor)
        raise NotAFile(path, found=kind_of(info.st_mode))
    return descriptor, info


def read_bytes(path: str | os.PathLike[str], *, limit: int | None = None) -> bytes:
    """A regular file's bytes, whole, or at most ``limit`` of them."""
    _enter("read", path, "read")
    descriptor, _info = _open_checked(path, _READ_FLAGS, 0, "read")
    try:
        return _read_all(descriptor, path, limit)
    finally:
        os.close(descriptor)


def read_text(path: str | os.PathLike[str], *, errors: str = "strict", limit: int | None = None) -> str:
    """A regular file's text, read as UTF-8 with ``errors``."""
    return read_bytes(path, limit=limit).decode("utf-8", errors)


def _read_all(descriptor: int, path: str | os.PathLike[str], limit: int | None) -> bytes:
    pieces: list[bytes] = []
    left = limit
    while left is None or left > 0:
        try:
            piece = os.read(descriptor, _CHUNK if left is None else min(_CHUNK, left))
        except OSError as exc:
            raise _classify(exc, path, "read") from None
        if not piece:
            break
        pieces.append(piece)
        if left is not None:
            left -= len(piece)
    data = b"".join(pieces)
    _count_bytes(len(data))
    return data


class Reader:
    """An open regular file, read in pieces: ``read``, ``seek`` and the ``look`` of what was opened."""

    __slots__ = ("descriptor", "path", "look")

    def __init__(self, descriptor: int, path: str | os.PathLike[str], info: os.stat_result) -> None:
        self.descriptor = descriptor
        self.path = path
        self.look = Look.of(info)

    def read(self, size: int = -1) -> bytes:
        return _read_all(self.descriptor, self.path, None if size is None or size < 0 else size)

    def seek(self, position: int) -> None:
        os.lseek(self.descriptor, position, os.SEEK_SET)

    def fileno(self) -> int:
        return self.descriptor

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1

    def __enter__(self) -> Reader:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def open_file(path: str | os.PathLike[str]) -> Reader:
    """A regular file opened for reading in pieces; the caller closes it (``with`` does)."""
    _enter("open_file", path, "read")
    descriptor, info = _open_checked(path, _READ_FLAGS, 0, "read")
    return Reader(descriptor, path, info)


# ─────────────────────────────── writing ───────────────────────────────

_TEMP_LETTERS = "abcdefghijklmnopqrstuvwxyz0123456789_"


def make_folder(path: str | os.PathLike[str], *, mode: int = 0o700) -> bool:
    """Make ``path`` with ``mode``. True when this call made it; a folder another process made is success (False)."""
    _enter("make_folder", path, "write")
    try:
        os.mkdir(path, mode)
    except FileExistsError:
        info = look(path)
        if info.kind == "link":
            raise Link(path) from None
        if info.kind != "folder":
            raise NotAFolder(path, found=info.kind) from None
        return False
    except OSError as exc:
        raise _classify(exc, path, "write") from None
    try:
        os.chmod(path, mode, follow_symlinks=False)
    except OSError as exc:
        raise _classify(exc, path, "write") from None
    return True


def make_folders(path: str | os.PathLike[str], *, mode: int = 0o700) -> None:
    """Make ``path`` and every missing folder above it, each with ``mode``; folders already there stay as they are."""
    target = Path(path)
    missing: list[Path] = []
    current = target
    while look_or_none(current) is None:
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    for folder in reversed(missing):
        make_folder(folder, mode=mode)


def cut_back(path: str | os.PathLike[str], length: int) -> bool:
    """An append-only file cut back to ``length`` bytes and synced; False when it is absent or no longer."""
    _enter("cut_back", path, "write")
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor, info = _open_checked(path, flags, 0, "write")
    except Absent:
        return False
    try:
        if info.st_size <= length:
            return False
        os.ftruncate(descriptor, length)
        os.fsync(descriptor)
    except OSError as exc:
        raise _classify(exc, path, "write") from None
    finally:
        os.close(descriptor)
    return True


def write_whole(
    path: str | os.PathLike[str], data: bytes | str, *, mode: int | None = None, keep_mode: bool = True
) -> None:
    """Write a whole file: a reader sees the old bytes or the new, never part of either.

    The bytes go to a temporary file beside it (``.<name>.<8 letters>.tmp``), are synced, take the mode,
    and replace the target with one rename; the folder is then synced. A link or anything but a regular
    file at the target is refused. The file keeps the mode it has, or gets ``mode`` (0600 when none is
    stated) when it is new; with ``keep_mode`` false it gets ``mode`` whatever the file it replaces had.
    """
    _enter("write_whole", path, "write")
    target = Path(path)
    payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    try:
        info = os.lstat(target)
    except FileNotFoundError:
        info = None
    except OSError as exc:
        raise _classify(exc, target, "write") from None
    if info is not None and stat.S_ISLNK(info.st_mode):
        raise Link(target)
    if info is not None and not stat.S_ISREG(info.st_mode):
        raise NotAFile(target, found=kind_of(info.st_mode))
    wanted = 0o600 if mode is None else mode
    final_mode = stat.S_IMODE(info.st_mode) if info is not None and keep_mode else wanted
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    for _ in range(16):
        temporary = target.with_name(
            f".{target.name}." + "".join(secrets.choice(_TEMP_LETTERS) for _ in range(8)) + ".tmp"
        )
        try:
            descriptor = os.open(temporary, flags, 0o600)
            break
        except FileExistsError:
            continue
        except OSError as exc:
            raise _classify(exc, target, "write") from None
    else:  # pragma: no cover - sixteen taken names in a row
        raise Other(target, number=errno.EEXIST)
    try:
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
            os.fchmod(descriptor, final_mode)
        finally:
            os.close(descriptor)
        os.replace(temporary, target)
    except BaseException as exc:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        if isinstance(exc, OSError):
            raise _classify(exc, target, "write") from None
        raise
    sync_folder(target.parent)


def create_new(path: str | os.PathLike[str], data: bytes, *, mode: int = 0o600) -> bool:
    """Create ``path`` with ``data`` only if nothing is there. False when something already is."""
    _enter("create_new", path, "write")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags, mode)
    except FileExistsError:
        return False
    except OSError as exc:
        raise _classify(exc, path, "write") from None
    try:
        try:
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
            os.fchmod(descriptor, mode)
        finally:
            os.close(descriptor)
    except OSError as exc:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise _classify(exc, path, "write") from None
    return True


APPEND_WAIT_SECONDS = 5.0


def append(path: str | os.PathLike[str], data: bytes, *, mode: int = 0o600) -> None:
    """Append ``data`` to an append-only file under the caller's lock, then sync it.

    The file's own ``flock`` is taken too, so another appender of the same file waits, at most
    ``APPEND_WAIT_SECONDS``; then the answer is ``busy``.
    """
    _enter("append", path, "write")
    if not data:
        return
    flags = (
        os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor, _info = _open_checked(path, flags, mode, "write")
    try:
        _flock_within(descriptor, path, APPEND_WAIT_SECONDS)
        try:
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        except OSError as exc:
            raise _classify(exc, path, "write") from None
        finally:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _flock_within(descriptor: int, path: str | os.PathLike[str], seconds: float) -> None:
    if fcntl is None:  # pragma: no cover
        return
    deadline = time.monotonic() + seconds
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise Busy(path, note="deadline") from None
            threading.Event().wait(0.05)
        except OSError as exc:
            raise _classify(exc, path, "write") from None


def remove(path: str | os.PathLike[str]) -> bool:
    """Remove the file at ``path``. False when nothing was there; a folder there is ``not_a_file``."""
    _enter("remove", path, "write")
    try:
        os.unlink(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        # A folder there is EISDIR on some systems and EPERM on others: either is ``not_a_file`` (``_denied``).
        raise _classify(exc, path, "write") from None
    return True


def remove_folder(path: str | os.PathLike[str]) -> None:
    """Remove the empty folder ``path``."""
    _enter("remove_folder", path, "write")
    try:
        os.rmdir(path)
    except OSError as exc:
        raise _classify(exc, path, "write") from None


def set_mode(path: str | os.PathLike[str], mode: int) -> None:
    """Give ``path`` the mode ``mode``, without following a link."""
    _enter("set_mode", path, "write")
    try:
        os.chmod(path, mode, follow_symlinks=False)
    except NotImplementedError:  # pragma: no cover - a system without lchmod
        info = look(path)
        if info.kind == "link":
            raise Link(path) from None
        os.chmod(path, mode)
    except OSError as exc:
        raise _classify(exc, path, "write") from None


def sync_folder(path: str | os.PathLike[str]) -> None:
    """Flush a folder's names to disk, so a rename in it survives a crash."""
    _enter("sync_folder", path, "read")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
    except OSError as exc:
        raise _classify(exc, path, "read") from None
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise _classify(exc, path, "write") from None
    finally:
        os.close(descriptor)


# ─────────────────────────────── where things are ───────────────────────────────


def absolute(path: str | os.PathLike[str]) -> Path:
    """``path`` with ``~`` expanded, made absolute from the working folder, without resolving links."""
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def real_path(path: str | os.PathLike[str]) -> Path:
    """``path`` with every link in it resolved, as the system resolves it."""
    _enter("real_path", path, "enter")
    try:
        return Path(os.path.realpath(path, strict=False))
    except OSError as exc:
        raise _classify(exc, path, "enter") from None


def working_folder() -> Path:
    """The process's working folder."""
    _enter("working_folder", ".", "enter")
    try:
        return Path(os.getcwd())
    except OSError as exc:
        raise _classify(exc, ".", "enter") from None


def home_folder() -> str:
    """This account's home folder, as the system names it (``~``)."""
    return os.path.expanduser("~")


def account_id() -> int | None:
    """This process's user id, where the system has one."""
    return os.getuid() if hasattr(os, "getuid") else None


# ─────────────────────────────── locks ───────────────────────────────

# Whether this system has the exclusive ``flock`` the forest's locks are made of.
LOCKS = fcntl is not None


class LockFile:
    """A lock file held open: ``take`` its exclusive ``flock`` without waiting, write who holds it, ``release``."""

    __slots__ = ("descriptor", "path")

    def __init__(self, descriptor: int, path: str | os.PathLike[str]) -> None:
        self.descriptor = descriptor
        self.path = path

    def take(self) -> bool:
        """True when this took the lock; False when another holds it."""
        if fcntl is None:  # pragma: no cover
            return True
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        except OSError as exc:
            raise _classify(exc, self.path, "write") from None
        return True

    def record(self, text: str) -> None:
        """Write ``text`` as the whole content of the lock file and sync it."""
        try:
            os.lseek(self.descriptor, 0, os.SEEK_SET)
            os.ftruncate(self.descriptor, 0)
            data = text.encode("utf-8")
            view = memoryview(data)
            while view:
                written = os.write(self.descriptor, view)
                view = view[written:]
            os.fsync(self.descriptor)
        except OSError as exc:
            raise _classify(exc, self.path, "write") from None

    def release(self) -> None:
        """Empty the record, then let the lock go and close the file."""
        try:
            try:
                os.lseek(self.descriptor, 0, os.SEEK_SET)
                os.ftruncate(self.descriptor, 0)
            finally:
                if fcntl is not None:
                    fcntl.flock(self.descriptor, fcntl.LOCK_UN)
        finally:
            self.close()

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1


def lock(path: str | os.PathLike[str]) -> LockFile:
    """Open (or make, mode 0600) the lock file at ``path``; nothing is taken yet."""
    _enter("lock", path, "write")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(
        os, "O_CLOEXEC", 0
    )
    descriptor, _info = _open_checked(path, flags, 0o600, "write")
    return LockFile(descriptor, path)


def lock_free(path: str | os.PathLike[str]) -> bool:
    """Whether nobody holds the lock file's ``flock`` (probed without keeping it). No file is free."""
    _enter("lock_free", path, "read")
    try:
        descriptor, _info = _open_checked(path, _READ_FLAGS, 0, "read")
    except Absent:
        return True
    try:
        if fcntl is None:  # pragma: no cover
            return True
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        except OSError as exc:
            raise _classify(exc, path, "read") from None
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return True
    finally:
        os.close(descriptor)


# ─────────────────────────────── the search index, for SQLite ───────────────────────────────

# The open by SQLite and its first statement are given this long; then the call answers busy.
OPEN_DEADLINE_SECONDS = 10.0


class IndexConnection(sqlite3.Connection):
    """A connection to the index. Every statement is counted for a test that watches; ``read_only`` is the
    connection's own flag (SQLite's immutable mode), so nothing is kept beside it by its address."""

    read_only = False

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:  # type: ignore[override]
        _statement(sql)
        return super().execute(sql, parameters)

    def executemany(self, sql: str, parameters: Any, /) -> sqlite3.Cursor:  # type: ignore[override]
        _statement(sql)
        return super().executemany(sql, parameters)

    def executescript(self, script: str, /) -> sqlite3.Cursor:  # type: ignore[override]
        _statement(script)
        return super().executescript(script)

    def commit(self) -> None:
        _statement("COMMIT")
        super().commit()


class ImmutableIndexConnection(IndexConnection):
    read_only = True


def _descriptor_path(descriptor: int, info: os.stat_result) -> str | None:
    """``/dev/fd/N`` when the system names the open file itself there, else None (the path is used)."""
    candidate = f"/dev/fd/{descriptor}"
    try:
        named = os.stat(candidate)
    except OSError:
        return None
    if (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino):
        return None
    return candidate


class IndexFile:
    """The index file as the door checked it, and the SQLite connection that reads it.

    ``same()`` says whether what the path names is still the file that was opened; in immutable mode it
    also says the file's size and time are unchanged and no write-ahead log stands beside it.
    """

    __slots__ = ("connection", "descriptor", "path", "before", "immutable", "through")

    def __init__(
        self, connection: sqlite3.Connection, descriptor: int, path: Path, info: os.stat_result, *,
        immutable: bool, through: str,
    ) -> None:
        self.connection = connection
        self.descriptor = descriptor
        self.path = path
        self.before = info
        self.immutable = immutable
        self.through = through

    def same(self) -> bool:
        try:
            _enter("same_index", self.path, "read")
        except Outcome:
            return False
        try:
            now = os.fstat(self.descriptor) if self.descriptor >= 0 else None
            named = os.lstat(self.path)
        except OSError:
            return False
        if (named.st_dev, named.st_ino) != (self.before.st_dev, self.before.st_ino):
            return False
        if not self.immutable:
            return True
        if now is None or (now.st_size, now.st_mtime_ns) != (self.before.st_size, self.before.st_mtime_ns):
            return False
        try:
            os.lstat(str(self.path) + "-wal")
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return False

    def release_descriptor(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1

    def close(self) -> None:
        try:
            self.connection.close()
        finally:
            self.release_descriptor()


def open_index(
    path: str | os.PathLike[str],
    *,
    immutable: bool,
    first: Callable[[sqlite3.Connection], Any] | None = None,
    write: bool = False,
    busy_seconds: float = 2.0,
    deadline_seconds: float | None = None,
) -> tuple[IndexFile, Any]:
    """Open the index for SQLite and run ``first`` on it, both within ``deadline_seconds``.

    The file is opened first (for ``write``, made with mode 0600 when absent), without following a link
    or blocking, and the descriptor is checked to be a regular file. In ``immutable`` mode (a folder this
    account cannot write) SQLite reads that descriptor through ``/dev/fd/N`` where the system names the
    open file there; otherwise, and for WAL mode, SQLite opens the path, and ``same()`` is compared before
    ``first``'s rows are trusted. An error SQLite meets while the path names something else is
    ``changed``. A call that passes its deadline (``OPEN_DEADLINE_SECONDS`` unless one is given) is ``busy``,
    or ``changed`` when the path names something else by then; SQLite's own thread finishes on its own and
    closes what it opened.
    """
    target = Path(path)
    deadline = OPEN_DEADLINE_SECONDS if deadline_seconds is None else deadline_seconds
    _enter("open_index", target, "write" if write else "read")
    if write:
        flags = (
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        descriptor, info = _open_checked(target, flags, 0o600, "write")
    else:
        descriptor, info = _open_checked(target, _READ_FLAGS, 0, "read")
    through = target.as_uri()
    if immutable:
        named = _descriptor_path(descriptor, info)
        through = f"file:{named}" if named else through
        uri, factory = through + "?mode=ro&immutable=1", ImmutableIndexConnection
    else:
        uri, factory = through + ("" if write else "?mode=rw"), IndexConnection
    try:
        _point(POINT_INDEX_OPEN, path=target)
    except BaseException:
        os.close(descriptor)
        raise
    handle_box: dict[str, Any] = {}
    done = threading.Event()
    state = {"abandoned": False}
    guard = threading.Lock()

    def run() -> None:
        connection = None
        try:
            connection = sqlite3.connect(
                uri, uri=True, timeout=busy_seconds, factory=factory, check_same_thread=False,
                isolation_level=None if write else "",
            )
            result = first(connection) if first is not None else None
            handle_box["connection"] = connection
            handle_box["result"] = result
        except BaseException as exc:  # noqa: BLE001 - handed to the caller's thread
            handle_box["error"] = exc
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass
        finally:
            with guard:
                done.set()
                late = state["abandoned"]
            if late:
                found = handle_box.get("connection")
                if found is not None:
                    try:
                        found.close()
                    except sqlite3.Error:
                        pass
                os.close(descriptor)

    threading.Thread(target=run, name="memory-forest index open", daemon=True).start()
    finished = done.wait(deadline)
    with guard:
        if not finished and not done.is_set():
            state["abandoned"] = True
            try:
                named = os.lstat(target)
            except OSError:
                named = None
            if named is None or (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino):
                raise Changed(target, note="deadline")
            raise Busy(target, note="deadline")
    opened = IndexFile(
        handle_box.get("connection"), descriptor, target, info, immutable=immutable, through=through  # type: ignore[arg-type]
    )
    if "error" in handle_box:
        error = handle_box["error"]
        moved = not opened.same()
        os.close(descriptor)
        opened.descriptor = -1
        if moved:
            raise Changed(target) from error
        raise error
    if not opened.same():
        opened.close()
        raise Changed(target)
    return opened, handle_box.get("result")
