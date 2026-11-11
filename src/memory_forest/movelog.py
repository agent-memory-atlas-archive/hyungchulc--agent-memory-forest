"""The move log, ``.forest/moves.jsonl`` (contract, sections 6 and 9; design, section 7).

Every path a forest operation moves, and every registry or collection line it
retypes or re-homes, gets one JSON line, appended and never rewritten::

    {"time": "...Z", "operation": "move", "from": "<old>", "to": "<new>", "subject": "<id>", "reason": "..."}

``from`` and ``to`` are forest-relative paths, folders included, or
addresses: ``<page>#<id>`` for a collection entry or a registry line inside a
page. ``to`` is null when the old place went to the archive. ``resolve``
follows the log from an old path to where the thing is now.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Final

from . import archive, layout
from .errors import MemoryForestError
from .locking import LockToken, append_lines, writer_lock
from .safety import require_real_root


# Operations whose `from` is still where it was: the line names a sibling made from it
# (a split), so following a page to its present place passes over them.
NON_MOVING_OPERATIONS: Final[frozenset[str]] = frozenset({"split", "copy"})
# Operations that end a chain in the archive (with `to` null, or `to` the page that took
# the old one's facts).
ARCHIVING_OPERATIONS: Final[frozenset[str]] = frozenset({"archive"})
OPERATION_RE: Final[re.Pattern[str]] = re.compile(r"[a-z][a-z0-9-]*")
MAX_CHAIN: Final[int] = 10_000


@dataclass(frozen=True, slots=True)
class MoveRecord:
    operation: str
    from_path: str
    to_path: str | None
    subject: str | None = None
    reason: str = ""
    time: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "from": self.from_path,
            "operation": self.operation,
            "reason": self.reason,
            "subject": self.subject,
            "time": self.time,
            "to": self.to_path,
        }

    def to_line(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, value: Any) -> MoveRecord | None:
        if not isinstance(value, dict):
            return None
        source, target = value.get("from"), value.get("to")
        operation = value.get("operation")
        if not isinstance(source, str) or not isinstance(operation, str):
            return None
        if target is not None and not isinstance(target, str):
            return None
        subject = value.get("subject")
        return cls(
            operation=operation,
            from_path=source,
            to_path=target,
            subject=subject if isinstance(subject, str) else None,
            reason=str(value.get("reason") or ""),
            time=str(value.get("time") or ""),
        )


def now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def record(
    operation: str,
    from_path: str,
    to_path: str | None,
    *,
    subject: str | None = None,
    reason: str = "",
    time: str | None = None,
) -> MoveRecord:
    """A move record, checked and with its paths in the form pages write them."""
    if OPERATION_RE.fullmatch(operation) is None:
        raise MemoryForestError("move_record_invalid", "An operation name is a lower-case word.", details={"operation": operation})
    source = layout.normalize(from_path)
    target = layout.normalize(to_path) if to_path is not None else None
    for value in (source, target):
        if value is None:
            continue
        page = value.partition("#")[0]
        if not page or page.startswith("/") or ".." in page.split("/") or "\n" in value:
            raise MemoryForestError("move_record_invalid", "A move names forest-relative paths.", details={"path": value})
    if source == target:
        raise MemoryForestError("move_record_invalid", "A move goes somewhere else.", details={"path": source})
    return MoveRecord(
        operation=operation,
        from_path=source,
        to_path=target,
        subject=subject,
        reason=" ".join(reason.split()),
        time=time or now_text(),
    )


def append(
    root: str | os.PathLike[str],
    records: Iterable[MoveRecord],
    *,
    lock: LockToken | str | None = None,
) -> int:
    """Append records to the log under the writer lock (re-entered when ``lock`` is the holder's token)."""
    items = list(records)
    if not items:
        return 0
    safe_root = require_real_root(root)
    with writer_lock(safe_root, token=lock, label="move log"):
        layout.ensure_folder(safe_root, layout.STATE_DIR)
        append_lines(layout.moves_path(safe_root), [item.to_line() for item in items])
    return len(items)


def read(root: str | os.PathLike[str], *, include_non_moving: bool = False) -> list[MoveRecord]:
    """The log's records, oldest first. Lines that do not parse are passed over."""
    safe_root = require_real_root(root)
    path = layout.moves_path(safe_root)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except FileNotFoundError:
        return []
    records: list[MoveRecord] = []
    for line in lines:
        try:
            parsed = MoveRecord.from_dict(json.loads(line))
        except ValueError:
            continue
        if parsed is None:
            continue
        if not include_non_moving and parsed.operation in NON_MOVING_OPERATIONS:
            continue
        records.append(parsed)
    return records


@dataclass(slots=True)
class Resolution:
    """Where an old path leads: ``current`` (``path`` exists), ``archived`` or ``unknown``."""

    query: str
    status: str
    path: str | None
    chain: list[MoveRecord] = field(default_factory=list)
    archive: dict[str, Any] | None = None
    successor: str | None = None
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "archive": self.archive,
            "chain": [item.as_dict() for item in self.chain],
            "note": self.note,
            "path": self.path,
            "query": self.query,
            "status": self.status,
            "successor": self.successor,
        }


def _under(path: str, folder: str) -> bool:
    return path.startswith(folder.rstrip("/") + "/")


def _next_step(records: list[MoveRecord], start: int, current: str) -> tuple[int, str] | None:
    """The first record at or after ``start`` that moves ``current`` (or a folder or page holding it)."""
    page, hash_mark, anchor = current.partition("#")
    for position in range(start, len(records)):
        item = records[position]
        if item.from_path == current:
            return position, item.to_path or ""
        if item.to_path is None:
            continue
        if hash_mark and "#" not in item.from_path and item.from_path == page:
            # the page that held the entry or line moved, and the entry with it
            return position, f"{item.to_path}#{anchor}"
        if "#" not in item.from_path and _under(current, item.from_path):
            return position, item.to_path.rstrip("/") + current[len(item.from_path.rstrip("/")) :]
    return None


def _page_file(root: Any, page: str) -> Any:
    try:
        return layout.resolve(root, page)
    except MemoryForestError:
        return None


def _exists(root: Any, path: str) -> bool:
    """Whether a page, a folder or an address is there: for ``<page>#<id>`` the page must hold the id in a
    code span (a collection entry's line, a registry line)."""
    page, mark, anchor = path.partition("#")
    target = _page_file(root, page)
    if target is None or not target.exists():
        return False
    if not mark or not anchor:
        return True
    if not target.is_file():
        return False
    with open(target, "r", encoding="utf-8", errors="surrogateescape") as handle:
        return f"`{anchor}`" in handle.read()


def _entry_in_folder(root: Any, address: str) -> str | None:
    """An entry no record sends on and not on the page reached: the address on another collection page of the
    same folder that holds it, if exactly one does."""
    page, _mark, anchor = address.partition("#")
    if layout.page_role(page) is not layout.PageRole.COLLECTION_PAGE:
        return None
    folder = page.rsplit("/", 1)[0]
    target = _page_file(root, folder)
    if target is None or not target.is_dir():
        return None
    found = []
    for item in sorted(target.iterdir()):
        sibling = f"{folder}/{item.name}"
        if item.is_file() and sibling != page and layout.page_role(sibling) is layout.PageRole.COLLECTION_PAGE:
            if _exists(root, f"{sibling}#{anchor}"):
                found.append(f"{sibling}#{anchor}")
    return found[0] if len(found) == 1 else None


def resolve(root: str | os.PathLike[str], old_path: str) -> Resolution:
    """Follow the move log from ``old_path`` to the present place.

    The walk starts at the newest record that moved the old path itself (or,
    for a path with none, the oldest record that moved a folder or page
    holding it) and follows each later record from the place reached:
    chains, moved folders, a page folded into another and the addresses
    ``<page>#<id>`` of entries and registry lines. Records are only ever read
    forward, and a place visited twice stops the walk, so a cycle in a broken
    log cannot loop. An archived place reports its archive entry and the page
    that took its facts, followed to where that page is now.
    """
    return _resolve(require_real_root(root), layout.normalize(old_path), depth=0)


def _resolve(safe_root: Any, query: str, *, depth: int) -> Resolution:
    records = read(safe_root)
    result = Resolution(query=query, status="unknown", path=None)
    direct = [index for index, item in enumerate(records) if item.from_path == query]
    position = direct[-1] if direct else 0
    current = query
    visited: set[tuple[int, str]] = set()
    archived = False
    while len(result.chain) < MAX_CHAIN:
        step = _next_step(records, position, current)
        if step is None:
            break
        index, target = step
        if (index, current) in visited:
            result.note = "the move log loops; the walk stopped where it came back"
            break
        visited.add((index, current))
        item = records[index]
        result.chain.append(item)
        if item.operation in ARCHIVING_OPERATIONS or not target:
            archived = True
            result.successor = item.to_path
            break
        current = target
        position = index + 1
    if not archived and _exists(safe_root, current):
        result.status, result.path = "current", current
        if result.chain and _exists(safe_root, query):
            result.note = f"a page exists at {query} again"
        return result
    if not archived and "#" in current:
        sibling = _entry_in_folder(safe_root, current)
        if sibling is not None:
            result.status, result.path = "current", sibling
            result.note = "the entry stands on another collection page of its folder"
            return result
    archived_page = current.partition("#")[0]
    entry = archive.latest(safe_root, archived_page)
    if entry is not None or archived:
        result.status, result.path = "archived", current
        result.archive = entry.as_dict() if entry is not None else None
        successor = (entry.successor if entry is not None else None) or result.successor
        result.successor = successor
        if successor and depth < 8:
            onward = _resolve(safe_root, layout.normalize(successor), depth=depth + 1)
            if onward.status == "current":
                result.successor = onward.path
        return result
    result.path = current if result.chain else None
    return result
