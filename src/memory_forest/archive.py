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

A record trim does not copy the page: it appends the lines it moves to the
page's record history file, ``record-history/<page path>``, one block per
trim, each block with its own manifest row (``kind: record-history``).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
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
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("kind") == HISTORY_KIND:
            continue  # a record history block (`blocks`)
        entry = ArchiveEntry.from_dict(value)
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


# ─────────────────────────────── the record history files ───────────────────────────────
#
# A record trim moves lines out of a page's `## Update record` into the page's record history file,
# `.forest/archive/record-history/<the page's path at its first trim>`: one file per page for the page's
# whole life, only ever appended to. Each append is one block: a header line (an HTML comment, so the file
# reads as Markdown) and the moved lines, byte for byte and in their order. Each block has one manifest
# row, so it can be verified alone.

HISTORY_DIR: Final[str] = "record-history"
HISTORY_KIND: Final[str] = "record-history"
_HEADER_RE: Final[re.Pattern[str]] = re.compile(
    r"<!-- record lines moved (?P<day>\S+) from `(?P<page>[^`]+)` · page sha256 (?P<page_sha>[0-9a-f]{64}) · "
    r"(?P<lines>\d+) lines? · (?P<first>\S+) to (?P<last>\S+) -->"
)


@dataclass(frozen=True, slots=True)
class HistoryBlock:
    """One append to a record history file, as its manifest row gives it."""

    time: str
    path: str
    history_file: str
    offset: int
    length: int
    sha256: str
    lines: int
    first: str
    last: str
    page_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "archive_file": self.history_file,
            "first": self.first,
            "kind": HISTORY_KIND,
            "last": self.last,
            "length": self.length,
            "lines": self.lines,
            "offset": self.offset,
            "page_sha256": self.page_sha256,
            "path": self.path,
            "sha256": self.sha256,
            "time": self.time,
        }

    @property
    def archive_path(self) -> str:
        return f"{layout.ARCHIVE_DIR}/{self.history_file}"

    @classmethod
    def from_dict(cls, value: Any) -> HistoryBlock | None:
        if not isinstance(value, dict) or value.get("kind") != HISTORY_KIND:
            return None
        try:
            return cls(str(value["time"]), str(value["path"]), str(value["archive_file"]), int(value["offset"]),
                       int(value["length"]), str(value["sha256"]), int(value["lines"]), str(value["first"]),
                       str(value["last"]), str(value["page_sha256"]))
        except (KeyError, TypeError, ValueError):
            return None


def block_header(day: str, page: str, page_sha256: str, lines: int, first: str, last: str) -> str:
    plural = "line" if lines == 1 else "lines"
    return (f"<!-- record lines moved {day} from `{page}` · page sha256 {page_sha256} · {lines} {plural} · "
            f"{first} to {last} -->")


def block_bytes(header: str, lines: Iterable[str]) -> bytes:
    return "".join(f"{line}\n" for line in (header, *lines)).encode("utf-8", "surrogateescape")


def file_blocks(data: bytes) -> list[tuple[int, int, re.Match[str]]]:
    """(offset, length, header match) of each block of a history file's bytes."""
    text_lines = data.split(b"\n")
    found: list[tuple[int, int, Any]] = []
    offset = 0
    for raw in text_lines:
        match = _HEADER_RE.fullmatch(raw.decode("utf-8", "surrogateescape"))
        if match is not None:
            if found:
                start, _length, header = found[-1]
                found[-1] = (start, offset - start, header)
            found.append((offset, 0, match))
        offset += len(raw) + 1
    if found:
        start, _length, header = found[-1]
        found[-1] = (start, len(data) - start, header)
    return found


def _history_target(root: Path, history_file: str) -> Path:
    return layout.resolve(root, f"{layout.ARCHIVE_DIR}/{history_file}")


def read_history(root: str | os.PathLike[str], history_file: str) -> bytes:
    """A record history file's bytes (empty when it does not exist yet)."""
    target = _history_target(require_real_root(root), history_file)
    try:
        return target.read_bytes()
    except FileNotFoundError:
        return b""


def history_file_for(root: str | os.PathLike[str], page: str, page_sha256: str, named: Iterable[str]) -> str:
    """The history file a page's first trim starts: ``record-history/<page>``, or the next free ``-2``, ``-3``
    name when a file no pointer of this page names holds it. A file that already holds a block cut from this
    very page text (an interrupted trim) is the page's own."""
    safe_root = require_real_root(root)
    stem = layout.normalize(page).removesuffix(".md")
    own = {name.removeprefix(layout.ARCHIVE_DIR + "/") for name in named}
    number = 1
    while True:
        name = f"{HISTORY_DIR}/{stem}{'' if number == 1 else f'-{number}'}.md"
        data = read_history(safe_root, name)
        if not data and not _history_target(safe_root, name).exists():
            return name
        if name in own or any(match["page_sha"] == page_sha256 for _o, _l, match in file_blocks(data)):
            return name
        number += 1


def block_lines(block: bytes) -> list[str]:
    """The record lines of one block (its header line aside), in their order."""
    return block.decode("utf-8", "surrogateescape").split("\n")[1:-1]


def prove_block(before: bytes, after: bytes, lines: Iterable[str]) -> None:
    """The history file after an append begins with its bytes before, and what follows is one block whose
    lines are ``lines``, byte for byte and in their order."""
    added = after[len(before):] if after.startswith(before) else b""
    first, _newline, _rest = added.partition(b"\n")
    if (not added or not added.endswith(b"\n") or _HEADER_RE.fullmatch(first.decode("utf-8", "surrogateescape")) is None
            or block_lines(added) != list(lines)):
        raise ArchiveProofError(
            "archive_proof_failed",
            "The record history file does not hold the moved lines byte for byte, in order, after its earlier "
            "bytes; nothing was written.",
            details={"before": len(before), "after": len(after)},
        )


def blocks(root: str | os.PathLike[str], path: str | None = None) -> list[HistoryBlock]:
    """The manifest's record history rows, oldest first; only one page's when ``path`` is given."""
    safe_root = require_real_root(root)
    try:
        with open(layout.resolve(safe_root, f"{layout.ARCHIVE_DIR}/{MANIFEST}"), "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except FileNotFoundError:
        return []
    found = []
    for line in lines:
        try:
            block = HistoryBlock.from_dict(json.loads(line))
        except ValueError:
            continue
        if block is not None and (path is None or block.path == layout.normalize(path)):
            found.append(block)
    return found


COPY_KIND: Final[str] = "copy"


def listing(root: str | os.PathLike[str], path: str | None = None) -> list[ArchiveEntry | HistoryBlock]:
    """Every manifest row in the manifest's order, whole-page copies and record history blocks alike; only one
    page's when ``path`` is given (``forest archive list``)."""
    safe_root = require_real_root(root)
    try:
        with open(layout.resolve(safe_root, f"{layout.ARCHIVE_DIR}/{MANIFEST}"), "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except (FileNotFoundError, MemoryForestError):
        return []
    wanted = layout.normalize(path) if path is not None else None
    found: list[ArchiveEntry | HistoryBlock] = []
    for line in lines:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        row = (HistoryBlock.from_dict(value) if isinstance(value, dict) and value.get("kind") == HISTORY_KIND
               else ArchiveEntry.from_dict(value))
        if row is not None and (wanted is None or row.path == wanted):
            found.append(row)
    return found


def append_block(
    root: str | os.PathLike[str],
    history_file: str,
    *,
    page: str,
    page_sha256: str,
    lines: list[str],
    first: str,
    last: str,
    lock: LockToken | str | None = None,
    when: datetime | None = None,
) -> HistoryBlock:
    """Append one block of moved record lines to a history file, prove it, and list it in the manifest.

    A block cut from the same page text is there already when a trim was
    interrupted after its append: it is not appended again (its manifest row
    is added if it is missing).
    """
    safe_root = require_real_root(root)
    moment = when or _now()
    with writer_lock(safe_root, token=lock, label="record history"):
        _archive_folder(safe_root)
        target = _history_target(safe_root, history_file)
        before = read_history(safe_root, history_file)
        existing = [(offset, length) for offset, length, match in file_blocks(before)
                    if match["page_sha"] == page_sha256]
        if existing:
            offset, length = existing[-1]
            data = before[offset: offset + length]
            prove_block(b"", data, lines)
        else:
            header = block_header(moment.strftime("%Y-%m-%d"), layout.normalize(page), page_sha256, len(lines),
                                  first, last)
            data = block_bytes(header, lines)
            layout.ensure_folder(safe_root, layout.normalize(f"{layout.ARCHIVE_DIR}/{history_file}").rsplit("/", 1)[0])
            flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(target, flags, 0o600)
            try:
                os.write(descriptor, data)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            after = target.read_bytes()
            try:
                prove_block(before, after, lines)
            except ArchiveProofError:
                with open(target, "r+b") as handle:
                    handle.truncate(len(before))
                raise
            offset = len(before)
        block = HistoryBlock(moment.strftime("%Y-%m-%dT%H:%M:%SZ"), layout.normalize(page), history_file, offset,
                             len(data), _digest(data), len(lines), first, last, page_sha256)
        listed = any(item.history_file == history_file and item.offset == offset for item in blocks(safe_root))
        if not listed:
            append_lines(layout.resolve(safe_root, f"{layout.ARCHIVE_DIR}/{MANIFEST}"),
                         [json.dumps(block.as_dict(), ensure_ascii=False, sort_keys=True)])
    return block


def verify(root: str | os.PathLike[str]) -> list[Any]:
    """``forest archive check``: every copy against its hash, every history block against its row."""
    from .registry import Problem

    safe_root = require_real_root(root)
    found = []
    for entry in entries(safe_root):
        try:
            read_copy(safe_root, entry)
        except MemoryForestError as exc:
            found.append(Problem("archive_damaged", entry.archive_path, None, exc.message))
    for block in blocks(safe_root):
        data = read_history(safe_root, block.history_file)[block.offset: block.offset + block.length]
        header = _HEADER_RE.fullmatch(data.split(b"\n", 1)[0].decode("utf-8", "surrogateescape")) if data else None
        if _digest(data) != block.sha256 or header is None or header["page_sha"] != block.page_sha256 \
                or int(header["lines"]) != block.lines:
            found.append(Problem("archive_damaged", block.archive_path, None,
                                 f"the block at byte {block.offset} no longer matches its manifest row"))
    return found
