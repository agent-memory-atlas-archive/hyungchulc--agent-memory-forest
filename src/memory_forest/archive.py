"""The archive, ``.forest/archive/`` (contract, sections 4 and 7; design, section 7).

Nothing leaves the forest by deletion. A page that is folded away, or the
version of a page from before a rewrite or a record trim, is kept as one
gzip copy of its exact bytes under a dated folder, with one manifest line::

    .forest/archive/<YYYY-MM-DD>/<old path>.gz
    .forest/archive/manifest.jsonl
    {"time", "path", "archive_file", "sha256", "bytes", "reason", "successor"}

``path`` is the page's forest-relative path, ``archive_file`` is relative to
the archive folder, ``successor`` is the page that took its facts (or null).
A copy is never overwritten: a second copy of one page on one day with other
bytes gets the start of its hash in its name.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from . import layout
from .errors import MemoryForestError
from .locking import LockToken, append_lines, atomic_write, writer_lock
from .model import split_line_ending
from .safety import require_real_root


MANIFEST: Final[str] = "manifest.jsonl"


@dataclass(frozen=True, slots=True)
class ArchiveEntry:
    time: str
    path: str
    archive_file: str
    sha256: str
    bytes: int
    reason: str
    successor: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "archive_file": self.archive_file,
            "bytes": self.bytes,
            "path": self.path,
            "reason": self.reason,
            "sha256": self.sha256,
            "successor": self.successor,
            "time": self.time,
        }

    @property
    def archive_path(self) -> str:
        """The copy's path relative to the forest root, as a record pointer line names it."""
        return f"{layout.ARCHIVE_DIR}/{self.archive_file}"

    @classmethod
    def from_dict(cls, value: Any) -> ArchiveEntry | None:
        if not isinstance(value, dict):
            return None
        try:
            return cls(
                time=str(value["time"]),
                path=str(value["path"]),
                archive_file=str(value["archive_file"]),
                sha256=str(value["sha256"]),
                bytes=int(value["bytes"]),
                reason=str(value.get("reason") or ""),
                successor=value.get("successor") if isinstance(value.get("successor"), str) else None,
            )
        except (KeyError, TypeError, ValueError):
            return None


class ArchiveProofError(MemoryForestError):
    """Lines that were to leave a page are not all in its archive copy."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _forest_page(root: Path, path: str) -> str:
    relative = layout.normalize(path)
    if not layout.classify(relative).in_forest:
        raise MemoryForestError(
            "archive_path_invalid",
            "Only pages of the four forest layers are archived.",
            details={"path": relative},
        )
    return relative


def _archive_folder(root: Path) -> Path:
    layout.ensure_folder(root, layout.ARCHIVE_DIR)
    return layout.archive_path(root)


def _copy_target(root: Path, relative: str, day: str, digest: str) -> tuple[str, Path, bool]:
    """Where the copy of ``relative`` taken on ``day`` goes, and whether an equal copy is already there."""
    for name in (f"{relative}.gz", f"{relative}.{digest[:12]}.gz"):
        archive_file = f"{day}/{name}"
        target = layout.resolve(root, f"{layout.ARCHIVE_DIR}/{archive_file}")
        if not target.exists():
            return archive_file, target, False
        if _stored_digest(target) == digest:
            return archive_file, target, True
    raise MemoryForestError(
        "archive_conflict",
        "An archive copy with other bytes already holds this name.",
        details={"path": relative},
    )


def _stored_digest(path: Path) -> str | None:
    try:
        return _digest(gzip.decompress(path.read_bytes()))
    except (OSError, EOFError, zlib.error):
        return None


def store(
    root: str | os.PathLike[str],
    path: str,
    data: bytes,
    *,
    reason: str,
    successor: str | None = None,
    lock: LockToken | str | None = None,
    when: datetime | None = None,
) -> ArchiveEntry:
    """Keep ``data`` as the archive copy of the page ``path`` (which need not exist any more).

    The copy must read back to the same SHA-256 before its manifest line is
    written. Holds the writer lock, re-entered with ``lock``.
    """
    safe_root = require_real_root(root)
    relative = _forest_page(safe_root, path)
    why = " ".join(str(reason).split())
    if not why:
        raise MemoryForestError("archive_reason_missing", "An archived page needs a reason.")
    successor_path = layout.normalize(successor) if successor else None
    if successor_path is not None and not layout.classify(successor_path).in_forest:
        raise MemoryForestError(
            "archive_path_invalid",
            "A successor is a forest page.",
            details={"path": successor_path},
        )
    moment = when or _now()
    digest = _digest(data)
    with writer_lock(safe_root, token=lock, label="archive"):
        _archive_folder(safe_root)
        archive_file, target, existing = _copy_target(safe_root, relative, moment.strftime("%Y-%m-%d"), digest)
        if not existing:
            layout.ensure_folder(safe_root, layout.normalize(f"{layout.ARCHIVE_DIR}/{archive_file}").rsplit("/", 1)[0])
            atomic_write(target, gzip.compress(data, compresslevel=9, mtime=0))
            if _stored_digest(target) != digest:
                target.unlink()
                raise MemoryForestError(
                    "archive_readback_failed",
                    "The archive copy did not read back to the page's SHA-256; nothing was archived.",
                    details={"path": relative},
                )
        entry = ArchiveEntry(
            time=moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
            path=relative,
            archive_file=archive_file,
            sha256=digest,
            bytes=len(data),
            reason=why,
            successor=successor_path,
        )
        append_lines(layout.resolve(safe_root, f"{layout.ARCHIVE_DIR}/{MANIFEST}"), [_line(entry)])
    return entry


def _line(entry: ArchiveEntry) -> str:
    return json.dumps(entry.as_dict(), ensure_ascii=False, sort_keys=True)


def put(
    root: str | os.PathLike[str],
    path: str,
    *,
    reason: str,
    successor: str | None = None,
    keep: bool = False,
    lock: LockToken | str | None = None,
    when: datetime | None = None,
) -> ArchiveEntry:
    """Archive a forest page and remove it; with ``keep`` it stays (the copy is its version before a rewrite).

    The page is removed only after its copy reads back and its manifest line
    is written, and only if it did not change meanwhile.
    """
    safe_root = require_real_root(root)
    relative = _forest_page(safe_root, path)
    with writer_lock(safe_root, token=lock, label="archive put") as held:
        target = layout.resolve(safe_root, relative, must_exist=True)
        if not target.is_file():
            raise MemoryForestError("archive_path_invalid", "Only pages are archived.", details={"path": relative})
        data = target.read_bytes()
        entry = store(safe_root, relative, data, reason=reason, successor=successor, lock=held, when=when)
        if not keep:
            if target.read_bytes() != data:
                raise MemoryForestError(
                    "archive_page_changed",
                    "The page changed while it was archived; it stays in the forest.",
                    details={"path": relative},
                )
            target.unlink()
    return entry


def entries(root: str | os.PathLike[str], path: str | None = None) -> list[ArchiveEntry]:
    """The manifest, oldest first; only one page's entries when ``path`` is given."""
    safe_root = require_real_root(root)
    try:
        manifest = layout.resolve(safe_root, f"{layout.ARCHIVE_DIR}/{MANIFEST}")
    except MemoryForestError:
        return []
    try:
        with open(manifest, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except FileNotFoundError:
        return []
    wanted = layout.normalize(path) if path is not None else None
    found: list[ArchiveEntry] = []
    for line in lines:
        try:
            entry = ArchiveEntry.from_dict(json.loads(line))
        except ValueError:
            continue
        if entry is not None and (wanted is None or entry.path == wanted):
            found.append(entry)
    return found


def latest(root: str | os.PathLike[str], path: str) -> ArchiveEntry | None:
    """The newest manifest entry of a page, or None."""
    found = entries(root, path)
    return found[-1] if found else None


def read_copy(root: str | os.PathLike[str], entry: ArchiveEntry) -> bytes:
    """The bytes of one archive copy, checked against its manifest hash."""
    safe_root = require_real_root(root)
    target = layout.resolve(safe_root, f"{layout.ARCHIVE_DIR}/{entry.archive_file}", must_exist=True)
    try:
        data = gzip.decompress(target.read_bytes())
    except (OSError, EOFError, zlib.error) as exc:
        raise MemoryForestError(
            "archive_copy_unreadable",
            "The archive copy cannot be read.",
            details={"path": entry.path},
        ) from exc
    if _digest(data) != entry.sha256:
        raise MemoryForestError(
            "archive_copy_changed",
            "The archive copy no longer matches its manifest hash.",
            details={"path": entry.path},
        )
    return data


def show(root: str | os.PathLike[str], path: str) -> bytes:
    """The newest archived bytes of a page."""
    entry = latest(root, path)
    if entry is None:
        raise MemoryForestError(
            "archive_not_found",
            "The page is not in the archive manifest.",
            details={"path": layout.normalize(path)},
        )
    return read_copy(root, entry)


def prove_lines_archived(
    root: str | os.PathLike[str],
    path: str,
    lines: Iterable[str],
    *,
    entry: ArchiveEntry | None = None,
) -> ArchiveEntry:
    """Check that every line is, byte for byte, a whole line of the page's archive copy.

    The copy is ``entry`` or the page's newest one. A line is compared
    without its line ending. Raises ``ArchiveProofError`` naming the lines
    that are missing; returns the entry the proof used.
    """
    chosen = entry if entry is not None else latest(root, path)
    if chosen is None:
        raise ArchiveProofError(
            "archive_proof_failed",
            "The page has no archive copy to prove against.",
            details={"path": layout.normalize(path)},
        )
    if chosen.path != layout.normalize(path):
        raise ArchiveProofError(
            "archive_proof_failed",
            "The archive entry belongs to another page.",
            details={"entry": chosen.path, "path": layout.normalize(path)},
        )
    text = read_copy(root, chosen).decode("utf-8", errors="surrogateescape")
    copy_lines = {split_line_ending(line)[0] for line in text.split("\n")}
    missing = []
    for line in lines:
        bare = split_line_ending(line)[0]
        if bare not in copy_lines:
            missing.append(bare)
    if missing:
        raise ArchiveProofError(
            "archive_proof_failed",
            "Some lines are not in the page's archive copy.",
            details={"missing": missing[:20], "missing_count": len(missing), "path": chosen.path},
        )
    return chosen
