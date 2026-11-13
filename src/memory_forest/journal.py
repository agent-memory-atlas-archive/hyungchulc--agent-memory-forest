"""The undo journal of a commit (contract, section 9; design, section 7).

``Plan.commit`` writes ``.forest/journal/commit.json`` durably, under the
writer lock and before its first page write: an id, the label, every path the
plan changes with its text before (null when the page did not exist), the
folders it makes and removes, and the byte length of ``.forest/moves.jsonl``
and of every other append-only state file the commit appends to (the judged
ledger, ``.forest/repeats-judged.jsonl``) before the commit. When the pages,
the read-back and the appends are done, the journal is removed durably; the
registry is rebuilt after that.

A journal that stands means a commit that did not finish. In the process that
wrote it, the commit's own roll-back runs; after a kill, the next taker of the
writer lock (whose holder the operating system released when it died) finds
the journal and rolls it back before anything else: each page back to its
text before, or removed; made folders removed when empty; the move log and
the other appended state files cut back to their lengths; the registry
rebuilt; the journal removed. The roll-back is idempotent, so a roll-back that
is itself interrupted is simply run again.
Archive copies the commit stored stay: the archive only grows.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import PurePath as Path
from typing import Any, Final

from . import layout, system
from .errors import MemoryForestError
from .locking import _fsync_directory, atomic_write

JOURNAL_DIR: Final[str] = f"{layout.STATE_DIR}/journal"
JOURNAL_FILE: Final[str] = f"{JOURNAL_DIR}/commit.json"
SCHEMA: Final[str] = "memory-forest-journal/1"
STANDING_CODE: Final[str] = "commit_journal_standing"


def journal_path(root: str | os.PathLike[str]) -> Path:
    return Path(root) / JOURNAL_FILE


def write(root: str | os.PathLike[str], *, label: str, before: dict[str, str | None], made_dirs: list[str],
          removed_dirs: list[str], appends: dict[str, int] | None = None) -> str:
    """Write the journal durably; returns its id. ``appends`` gives the byte length before the commit of each
    other append-only state file the commit appends to (the judged ledger)."""
    folder = Path(root) / JOURNAL_DIR
    system.make_folders(folder)
    _fsync_directory(folder.parent)
    moves = system.look_or_none(layout.moves_path(root))
    data = {
        "schema": SCHEMA,
        "id": secrets.token_hex(8),
        "label": label,
        # A byte that is not UTF-8 is read as a surrogate escape; JSON writes it as `\udcXX` and reads it back.
        "pages": dict(sorted(before.items())),
        "made_dirs": list(made_dirs),
        "removed_dirs": list(removed_dirs),
        "moves_bytes": moves.size if moves is not None and moves.kind == "file" else 0,
        "appends": dict(sorted((appends or {}).items())),
    }
    payload = json.dumps(data, ensure_ascii=True, sort_keys=True, indent=1).encode("ascii")
    atomic_write(journal_path(root), payload)
    return str(data["id"])


def read(root: str | os.PathLike[str]) -> dict[str, Any] | None:
    path = journal_path(root)
    try:
        raw = system.read_bytes(path)
    except system.Absent:
        return None
    try:
        data = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MemoryForestError("journal_invalid", "The commit journal cannot be read; nothing was rolled back.",
                                details={"path": JOURNAL_FILE}) from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise MemoryForestError("journal_invalid", "The commit journal is not one this version wrote.",
                                details={"path": JOURNAL_FILE})
    return data


def remove(root: str | os.PathLike[str]) -> None:
    """Remove the journal durably (the commit then stands)."""
    path = journal_path(root)
    if not system.remove(path):
        return
    _fsync_directory(path.parent)


def _remove_empty(root: Path, folder: str) -> None:
    try:
        target = layout.resolve(root, folder)
    except MemoryForestError:
        return
    there = system.look_or_none(target)
    if there is not None and there.kind == "folder":
        entries = system.listing(target)
        if not [entry for entry in entries if entry.name != ".DS_Store"]:
            for entry in entries:
                system.remove(entry.path)
            system.remove_folder(target)


def _cut_back(path: Path, length: int) -> None:
    """An append-only file cut back to ``length`` bytes (what the unfinished commit appended goes)."""
    system.cut_back(path, length)


def roll_back(root: str | os.PathLike[str]) -> dict[str, Any] | None:
    """Put the forest back as it was before the journal's commit; None when no journal stands.

    The caller holds the writer lock. Every step can run again, so a roll-back
    that stops halfway is finished by the next one.
    """
    safe_root = Path(root)
    data = read(safe_root)
    if data is None:
        return None
    pages: dict[str, str | None] = data.get("pages") or {}
    for path, text in sorted(pages.items()):
        target = layout.resolve(safe_root, path)
        there = system.look_or_none(target)
        if text is None:
            if there is not None and there.kind == "file":
                system.remove(target)
                _fsync_directory(target.parent)
            continue
        payload = text.encode("utf-8", "surrogateescape")
        system.make_folders(target.parent)
        if there is None or there.kind != "file" or system.read_bytes(target) != payload:
            atomic_write(target, payload)
    for folder in sorted(data.get("removed_dirs") or (), key=len):
        target = layout.resolve(safe_root, folder)
        if system.look_or_none(target) is None:
            system.make_folders(target)
    for folder in sorted(data.get("made_dirs") or (), key=len, reverse=True):
        _remove_empty(safe_root, folder)
    _cut_back(layout.moves_path(safe_root), int(data.get("moves_bytes") or 0))
    for relative, length in sorted((data.get("appends") or {}).items()):
        _cut_back(layout.state_path(safe_root, relative), int(length))
    from . import registry

    try:
        registry.build_and_write(safe_root)
    except MemoryForestError:
        pass  # derived: `forest registry build` repairs it
    remove(safe_root)
    return {"id": data.get("id"), "label": data.get("label"), "pages": len(pages)}


def recover_on_lock(root: str | os.PathLike[str]) -> None:
    """Called by the writer lock when it is taken: a standing journal is a dead writer's; roll it back."""
    standing = system.look_or_none(journal_path(root))
    if standing is None or standing.kind != "file":
        return
    done = roll_back(root)
    if done is not None:
        sys.stderr.write(f"forest: rolled back an unfinished commit ({done['label']}, {done['pages']} pages) "
                         f"that a stopped writer left in {JOURNAL_FILE}\n")


def problems(root: str | os.PathLike[str]) -> list[Any]:
    """``forest check``'s ``commit_journal_standing``: a commit that did not finish (a reader takes no lock)."""
    from .registry import Problem

    standing = system.look_or_none(journal_path(root))
    if standing is None or standing.kind != "file":
        return []
    try:
        data = read(root) or {}
        what = f"`{data.get('label')}` ({len(data.get('pages') or {})} pages)"
    except MemoryForestError:
        what = "a journal that cannot be read"
    return [Problem(STANDING_CODE, JOURNAL_FILE, None,
                    f"an unfinished commit {what}: its pages are part moved until a writer takes the lock or "
                    "`forest recover` rolls it back")]
