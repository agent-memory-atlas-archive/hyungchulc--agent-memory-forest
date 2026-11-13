"""Locks and safe writes.

``maintenance_lock`` is the 0.3 sibling lock of the index maintenance lane.
The version 1 locks (the forest writer lock and the stage locks) and the
write helpers every module uses for pages and kept state follow below it.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import stat
import tempfile
import time
from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - the package supports POSIX systems only
    fcntl = None  # type: ignore[assignment]

from .errors import MemoryForestError
from .safety import require_real_root, safe_join


@contextmanager
def maintenance_lock(
    root: str | os.PathLike[str],
) -> Iterator[Path]:
    root_path = require_real_root(root)
    lock = Path(str(root_path) + ".maintenance.lock")
    if Path(os.path.realpath(lock.parent)) != lock.parent:
        raise MemoryForestError(
            "unsafe_lock_parent",
            "The maintenance lock parent must be a real directory.",
        )
    _reject_casefold_collision(lock.parent, lock.name)
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise MemoryForestError(
            "maintenance_lock_busy",
            "The sibling maintenance lock is already held.",
            details={"path": lock.name},
        ) from exc
    os.chmod(lock, 0o700, follow_symlinks=False)
    _fsync_directory(lock.parent)
    try:
        yield root_path
    finally:
        try:
            lock.rmdir()
            _fsync_directory(lock.parent)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise MemoryForestError(
                "lock_release_failed",
                "The maintenance lock could not be released safely.",
                details={"reason": exc.__class__.__name__},
            ) from exc


def _reject_casefold_collision(parent: Path, name: str) -> None:
    folded = name.casefold()
    with os.scandir(parent) as iterator:
        entries = list(iterator)
    for entry in entries:
        if entry.name.casefold() == folded and entry.name != name:
            raise MemoryForestError(
                "casefold_collision",
                "A differently cased filesystem entry collides with a lock path.",
                details={"expected": name, "existing": entry.name},
            )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


# ═══════════════════════════════ version 1 locks ═══════════════════════════════
#
# One writer edits the forest at a time (contract, section 3, Who writes). The
# writer lock and the stage locks are files under `.forest/locks/` held with an
# exclusive `flock`, so a lock dies with the process that held it. The file
# also records who holds it (process, host, time, label and a random token):
#
# - re-entry is explicit: a holder passes its `LockToken` (or the token's text,
#   to a child process it starts) to every tool it calls, and a tool given a
#   token that names the live hold of the same lock goes on without taking it
#   again. Nothing is read from the environment;
# - a record whose process is gone while nobody holds the `flock` is stale, and
#   `lock_status` says so; taking the lock simply replaces it.

WRITER_LOCK = "writer"
LOCKS_RELATIVE = ".forest/locks"
_LOCK_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
_POLL_SECONDS = 0.05


@dataclass(frozen=True, slots=True)
class LockToken:
    """Proof of a held lock, passed down to tools that need the same lock."""

    path: str
    name: str
    value: str
    pid: int

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class LockStatus:
    name: str
    path: str
    held: bool
    stale: bool
    owner: dict[str, object] | None

    def as_dict(self) -> dict[str, object]:
        return {"held": self.held, "name": self.name, "owner": self.owner, "path": self.path, "stale": self.stale}


# Locks this process holds: lock file path -> (open handle, token).
_HELD: dict[str, tuple[object, LockToken]] = {}


def _lock_file(root: str | os.PathLike[str], name: str) -> Path:
    if _LOCK_NAME_RE.fullmatch(name) is None:
        raise MemoryForestError("invalid_lock_name", "A lock name is a slug.", details={"name": name})
    # One step: the other process may have made the folder between the check and the make.
    safe_join(root, ".forest").mkdir(mode=0o700, exist_ok=True)
    safe_join(root, LOCKS_RELATIVE).mkdir(mode=0o700, exist_ok=True)
    return safe_join(root, f"{LOCKS_RELATIVE}/{name}.lock")


def _read_owner(path: Path) -> dict[str, object] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError):
        return None
    try:
        owner = json.loads(text)
    except ValueError:
        return None
    return owner if isinstance(owner, dict) else None


def _process_alive(pid: object) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _flock_free(path: Path) -> bool:
    """Whether nobody holds the lock file's flock (probed without keeping it)."""
    if str(path) in _HELD:
        return False
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return True
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return True
    finally:
        os.close(descriptor)


def lock_status(root: str | os.PathLike[str], name: str = WRITER_LOCK) -> LockStatus:
    """Who holds a lock, and whether its record is stale (its process is gone)."""
    path = _lock_file(root, name)
    owner = _read_owner(path)
    free = _flock_free(path)
    same_host = owner is not None and owner.get("host") == socket.gethostname()
    stale = owner is not None and free and (not same_host or not _process_alive(owner.get("pid")))
    public_owner = None if owner is None else {key: value for key, value in owner.items() if key != "token"}
    return LockStatus(name=name, path=f"{LOCKS_RELATIVE}/{name}.lock", held=not free, stale=stale, owner=public_owner)


def _token_matches(path: Path, name: str, token: LockToken | str) -> LockToken | None:
    value = token.value if isinstance(token, LockToken) else str(token)
    held = _HELD.get(str(path))
    if held is not None and held[1].value == value:
        return held[1]
    owner = _read_owner(path)
    if (
        owner is not None
        and owner.get("token") == value
        and owner.get("host") == socket.gethostname()
        and _process_alive(owner.get("pid"))
        and not _flock_free(path)
    ):
        return LockToken(path=str(path), name=name, value=value, pid=int(owner["pid"]))  # type: ignore[arg-type]
    return None


@contextmanager
def hold_lock(
    root: str | os.PathLike[str],
    name: str = WRITER_LOCK,
    *,
    token: LockToken | str | None = None,
    wait_seconds: float = 0.0,
    label: str = "",
) -> Iterator[LockToken]:
    """Hold the lock ``name`` of the forest at ``root``.

    With ``token``, the lock must already be held under that token (by this
    process or by the process that started it): the block runs inside that
    hold and releases nothing. Without one, the lock is taken, waiting up to
    ``wait_seconds``; a lock held elsewhere raises ``lock_busy``.
    """
    if fcntl is None:  # pragma: no cover
        raise MemoryForestError("lock_unsupported", "Forest locks need a POSIX system.")
    path = _lock_file(root, name)
    if token is not None:
        held = _token_matches(path, name, token)
        if held is None:
            raise MemoryForestError(
                "lock_token_invalid",
                "The token passed in names no live hold of this lock.",
                details={"lock": name},
            )
        yield held
        return
    if str(path) in _HELD:
        raise MemoryForestError(
            "lock_already_held",
            "This process already holds the lock; pass its token to re-enter it.",
            details={"lock": name},
        )
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    handle = os.fdopen(descriptor, "r+", encoding="utf-8")
    deadline = time.monotonic() + max(0.0, wait_seconds)
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    owner = _read_owner(path)
                    raise MemoryForestError(
                        "lock_busy",
                        "Another writer holds the lock.",
                        details={
                            "lock": name,
                            "owner": None
                            if owner is None
                            else {key: value for key, value in owner.items() if key != "token"},
                        },
                    ) from None
                time.sleep(_POLL_SECONDS)
    except BaseException:
        handle.close()
        raise
    token_value = secrets.token_hex(16)
    lock_token = LockToken(path=str(path), name=name, value=token_value, pid=os.getpid())
    record = {
        "host": socket.gethostname(),
        "label": label,
        "pid": os.getpid(),
        "since": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "token": token_value,
    }
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps(record, sort_keys=True) + "\n")
    handle.flush()
    os.fsync(handle.fileno())
    _HELD[str(path)] = (handle, lock_token)
    try:
        if name == WRITER_LOCK:
            # A commit journal found when the writer lock is taken is a dead writer's: roll it back first.
            from . import journal

            journal.recover_on_lock(root)
        yield lock_token
    finally:
        _HELD.pop(str(path), None)
        try:
            handle.seek(0)
            handle.truncate()
            handle.flush()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


def writer_lock(
    root: str | os.PathLike[str],
    *,
    token: LockToken | str | None = None,
    wait_seconds: float = 0.0,
    label: str = "",
) -> AbstractContextManager[LockToken]:
    """The forest writer lock: held by every run or tool that writes forest pages or kept state."""
    return hold_lock(root, WRITER_LOCK, token=token, wait_seconds=wait_seconds, label=label)


def stage_lock(
    root: str | os.PathLike[str],
    stage: str,
    *,
    token: LockToken | str | None = None,
    wait_seconds: float = 0.0,
    label: str = "",
) -> AbstractContextManager[LockToken]:
    """The lock of one stage (``stage-<name>``): one run of a stage at a time."""
    return hold_lock(root, f"stage-{stage}", token=token, wait_seconds=wait_seconds, label=label or stage)


def holds(root: str | os.PathLike[str], name: str = WRITER_LOCK) -> LockToken | None:
    """This process's token for a lock it holds, or None."""
    path = _lock_file(root, name)
    held = _HELD.get(str(path))
    return held[1] if held is not None else None


# ─────────────────────────────── writing files ───────────────────────────────


def atomic_write(
    path: str | os.PathLike[str], data: bytes | str, *, mode: int | None = None, keep_mode: bool = True
) -> None:
    """Write a whole file: a reader sees the old bytes or the new, never part of either.

    The new bytes go to a temporary file in the same folder, are flushed to
    disk, and replace the target with one rename; the folder is then synced.
    A symlink at the target is refused. The file keeps its mode, or gets
    ``mode`` (0600 by default) when it is new. With ``keep_mode`` false it
    gets ``mode`` (0600 by default) whatever the file it replaces had.
    """
    target = Path(path)
    payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    folder = target.parent
    try:
        info = target.lstat()
    except FileNotFoundError:
        info = None
    if info is not None and (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)):
        raise MemoryForestError(
            "unsafe_existing_path",
            "The file to replace is a symlink or not a regular file.",
            details={"path": target.name},
        )
    wanted = 0o600 if mode is None else mode
    final_mode = stat.S_IMODE(info.st_mode) if info is not None and keep_mode else wanted
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, final_mode)
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    _fsync_directory(folder)


def append_lines(path: str | os.PathLike[str], lines: Iterable[str]) -> None:
    """Append whole lines to an append-only file under an exclusive flock, then sync it."""
    payload = "".join(line if line.endswith("\n") else line + "\n" for line in lines)
    if not payload:
        return
    target = Path(path)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(target, flags, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            os.write(descriptor, payload.encode("utf-8"))
            os.fsync(descriptor)
        finally:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
