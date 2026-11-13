"""Witness sentences with independent system calls, never with the decision's predicates.

A witness can reject a sentence. Merely finding a claim in the package table proves nothing.
"""
from __future__ import annotations

import errno
import os
import sqlite3
import stat
import threading
import tomllib
from pathlib import Path


def tried(function, *args):
    try:
        return function(*args), None
    except (OSError, ValueError, sqlite3.Error) as error:
        return None, error


def entered(path):
    # Resolving the dot entry asks the kernel for directory search permission, not directory read.
    return tried(os.lstat, os.path.join(os.fspath(path), "."))


def blob(path):
    def read():
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise IsADirectoryError(errno.EISDIR, "not a regular file")
            return os.read(fd, 65536)
        finally:
            os.close(fd)
    return tried(read)


def kind(path):
    info, error = tried(os.lstat, path)
    if error:
        return None
    return "link" if stat.S_ISLNK(info.st_mode) else "folder" if stat.S_ISDIR(info.st_mode) else "file" if stat.S_ISREG(info.st_mode) else "special"


class Evidence:
    def __init__(self, root, reference, before=None, seconds=0):
        self.root = Path(root)
        self.folder = self.root / ".forest"
        self.reference = Path(reference)
        self.before = before
        self.seconds = seconds
        self.path = self.folder / "index.sqlite"
        data, self.pointer_error = blob(self.folder / "index.current")
        self.pointer = data
        if data:
            text = data.decode("utf-8", errors="replace").strip()
            if "/" not in text and text.startswith("index") and text.endswith(".sqlite"):
                self.path = self.folder / text
        self.sides = [Path(str(self.path) + suffix) for suffix in ("-journal", "-wal", "-shm")]
        self.meta, self.db_error, self.changed = {}, None, []
        self.expected, _, _ = self.database(self.reference / ".forest/index.sqlite")
        if kind(self.path) == "file" and entered(self.folder)[0] and all(kind(p) in (None, "file") for p in self.sides):
            self.meta, self.db_error, self.changed = self.database(self.path)

    def database(self, path):
        db = None
        try:
            mode = "?mode=rw" if os.access(path.parent, os.W_OK | os.X_OK) else "?mode=ro&immutable=1"
            db = sqlite3.connect(path.as_uri() + mode, uri=True, timeout=0.01)
            meta = dict(db.execute("SELECT key, value FROM metadata"))
            # Inspect the actual required tables, not the package's probe.
            for table in ("units", "units_words", "files"):
                db.execute("SELECT COUNT(*) FROM " + table).fetchone()
            changed = []
            for relative, size, mtime in db.execute("SELECT path, size, mtime_ns FROM files"):
                info, error = tried(os.stat, self.root / relative)
                if error or (info.st_size, info.st_mtime_ns) != (size, mtime):
                    changed.append(relative)
            return meta, None, changed
        except (sqlite3.Error, OSError, ValueError) as error:
            return {}, error, []
        finally:
            if db:
                db.close()

    def denied(self, path, access):
        if access == "enter":
            return isinstance(entered(path)[1], PermissionError)
        if access == "list":
            return isinstance(tried(os.listdir, path)[1], PermissionError)
        return isinstance(blob(path)[1], PermissionError)

    def can_read(self):
        return bool(self.meta) and not self.db_error and self.meta.get("masking") == self.expected.get("masking") and not any(kind(p) for p in self.sides)

    def check(self, key):
        return WITNESSES[key](self)


def _settings_bad(e):
    data, error = blob(e.root / "forest.toml")
    if error:
        return True
    try:
        tomllib.loads(data.decode("utf-8"))
    except (ValueError, UnicodeError):
        return True
    return False


def _pointer_bad(e):
    data = e.pointer
    if data is None:
        return False
    import re
    return re.fullmatch(rb"index(?:-[0-9a-f]{16})?\.sqlite\n?", data) is None


def _damaged(e):
    return e.db_error is not None and not isinstance(e.db_error, PermissionError)


def _changed(e):
    now, error = tried(os.stat, e.path)
    return error is not None or e.before is not None and (now.st_dev, now.st_ino) != e.before


def _sqlite(code):
    return lambda e: getattr(e.db_error, "sqlite_errorcode", -1) & 255 == code


def _held(e):
    import fcntl
    path = e.folder / "locks/index.lock"
    fd, error = tried(os.open, path, os.O_RDONLY | os.O_NONBLOCK)
    if error:
        return False
    try:
        _, error = tried(fcntl.flock, fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if error:
            return isinstance(error, BlockingIOError)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


WITNESSES = {
    "root_absent": lambda e: isinstance(tried(os.lstat, e.root)[1], FileNotFoundError),
    "root_above_file": lambda e: isinstance(tried(os.lstat, e.root)[1], NotADirectoryError),
    "root_above_denied": lambda e: isinstance(tried(os.lstat, e.root)[1], PermissionError),
    "root_not_folder": lambda e: kind(e.root) not in (None, "folder"),
    "root_unenterable": lambda e: e.denied(e.root, "enter"),
    "root_unlistable": lambda e: e.denied(e.root, "list"),
    "settings_denied": lambda e: e.denied(e.root / "forest.toml", "read"),
    "settings_link": lambda e: kind(e.root / "forest.toml") == "link",
    "settings_not_a_file": lambda e: kind(e.root / "forest.toml") in ("folder", "special"),
    "settings_invalid": _settings_bad,
    "settings_failed": lambda e: blob(e.root / "forest.toml")[1] is not None,
    "settings_busy": lambda e: isinstance(blob(e.root / "forest.toml")[1], BlockingIOError),
    "folder_link": lambda e: kind(e.folder) == "link",
    "folder_not_a_folder": lambda e: kind(e.folder) in ("file", "special"),
    "folder_failed": lambda e: tried(os.lstat, e.folder)[1] is not None,
    "folder_unenterable": lambda e: e.denied(e.folder, "enter"),
    "folder_unlistable": lambda e: e.denied(e.folder, "list"),
    "pointer_denied": lambda e: e.denied(e.folder / "index.current", "read"),
    "pointer_link": lambda e: kind(e.folder / "index.current") == "link",
    "pointer_not_a_file": lambda e: kind(e.folder / "index.current") in ("folder", "special"),
    "pointer_damaged": _pointer_bad,
    "pointer_failed": lambda e: e.pointer_error is not None,
    "pointer_busy": lambda e: isinstance(e.pointer_error, BlockingIOError),
    "no_index": lambda e: kind(e.path) is None,
    "other_version": lambda e: bool(e.meta) and e.meta.get("schema") != e.expected.get("schema"),
    "not_committed": lambda e: kind(e.path) == "file" and os.stat(e.path).st_size == 0,
    "index_link": lambda e: kind(e.path) == "link",
    "index_not_a_file": lambda e: kind(e.path) in ("folder", "special"),
    "index_denied": lambda e: e.denied(e.path, "read"),
    "index_enter_denied": lambda e: isinstance(tried(os.lstat, e.path)[1], PermissionError),
    "index_failed": lambda e: blob(e.path)[1] is not None,
    "beside_not_a_file": lambda e: any(kind(p) not in (None, "file") for p in e.sides),
    "beside_unreadable": lambda e: any(e.denied(p, "read") for p in e.sides if kind(p) == "file"),
    "beside_failed": lambda e: any(tried(os.lstat, p)[1] is not None and not isinstance(tried(os.lstat, p)[1], FileNotFoundError) for p in e.sides),
    "wal": lambda e: kind(e.sides[1]) == "file" and not os.access(e.folder, os.W_OK | os.X_OK),
    "journal": lambda e: kind(e.sides[0]) == "file" and not os.access(e.folder, os.W_OK | os.X_OK),
    "damaged": _damaged,
    "masking_changed": lambda e: bool(e.meta.get("masking")) and e.meta["masking"] != e.expected.get("masking"),
    "masking_missing": lambda e: bool(e.meta) and "masking" not in e.meta,
    "pages_changed": lambda e: bool(e.changed),
    "index_busy": lambda e: _held(e) or _sqlite(sqlite3.SQLITE_BUSY)(e),
    "index_deadline": lambda e: e.seconds >= 0.02,
    "index_abandoned": lambda e: any(t.name == "memory-forest index open" for t in threading.enumerate()),
    "index_changed": _changed,
    "lock_held": _held,
    "ask_later": lambda e: _held(e) or e.seconds >= 0.02 or _changed(e),
    "sqlite_locked": _sqlite(sqlite3.SQLITE_LOCKED),
    "sqlite_read_only": _sqlite(sqlite3.SQLITE_READONLY),
    "sqlite_io": _sqlite(sqlite3.SQLITE_IOERR),
    "sqlite_protocol": _sqlite(sqlite3.SQLITE_PROTOCOL),
    "sqlite_full": _sqlite(sqlite3.SQLITE_FULL),
    "sqlite_cannot_open": _sqlite(sqlite3.SQLITE_CANTOPEN),
    "sqlite_other": lambda e: isinstance(e.db_error, sqlite3.Error),
    "tell_owner": lambda e: e.db_error is not None or tried(os.lstat, e.path)[1] is not None,
    "folder_unwritable": lambda e: not os.access(e.folder, os.W_OK | os.X_OK),
    "root_unwritable": lambda e: not os.access(e.root, os.W_OK | os.X_OK),
    "reads_answer": lambda e: e.can_read(),
    "reads_refused": lambda e: not e.can_read(),
}
# A cure's precondition is checked independently here; ActionClaimTests executes each access/owner
# action and RunMoveTests executes every command, on the same system.
for cure, observation in {
    "give_root_above_enter": "root_above_denied", "give_root_enter": "root_unenterable",
    "give_root_list": "root_unlistable", "give_settings_read": "settings_denied",
    "give_folder_enter": "folder_unenterable", "give_folder_list": "folder_unlistable",
    "give_pointer_read": "pointer_denied", "give_index_read": "index_denied",
    "give_index_enter": "index_enter_denied", "give_beside_read": "beside_unreadable",
    "owner_mends_settings": "settings_invalid", "owner_mends_folder": "folder_not_a_folder",
    "owner_moves_pointer": "pointer_not_a_file",
}.items():
    WITNESSES[cure] = lambda e, key=observation: WITNESSES[key](e)
WITNESSES.update({
    "run_build": lambda e: os.access(e.folder if kind(e.folder) == "folder" else e.root, os.W_OK | os.X_OK),
    "run_reset": lambda e: os.access(e.folder, os.W_OK | os.X_OK),
    "writer_build_folder": lambda e: not os.access(e.folder, os.W_OK | os.X_OK),
    "writer_build_root": lambda e: not os.access(e.root, os.W_OK | os.X_OK),
    "writer_reset_folder": lambda e: not os.access(e.folder, os.W_OK | os.X_OK),
})

WITNESSES["owner_mends_folder"] = lambda e: kind(e.folder) in ("link", "file", "special") or tried(os.lstat, e.folder)[1] is not None
WITNESSES["owner_moves_pointer"] = lambda e: kind(e.folder / "index.current") in ("link", "folder", "special") or e.pointer_error is not None
