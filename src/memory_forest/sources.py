"""Where a turn came from: its source, written so it cannot be confused (design, sections 6 and 15).

A source is a kind and a name: ``group:<id>`` for a turn that came through a group connection (the tool or the
command), ``door:<name>`` for a turn that came through an agent's own connection (``door:mcp`` for
``memory_log`` on ``forest mcp --as``, ``door:cli`` for ``forest log --as``), and ``adapter:<name>`` for a turn
``forest intake`` read from a tool's transcript. The kind is part of the source, so a group called ``mcp`` is
``group:mcp`` and never the door. A turn carries its sources in ``sources``, in the order they came; a line
written before that field existed has its source worked out from its ``source`` object.

The sources each agent has written through are kept in one small record, ``.forest/intake/sources.json``,
which the lane writer updates when a turn brings a source the record does not hold. Beside them it keeps the
size of each of the agent's lane files as the writer left them; a lane folder that no longer matches (a file
written another way, a file renamed) has its agent's entry read again from its lanes, once.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from typing import Any, Final

from . import layout, system
from .errors import MemoryForestError
from .locking import atomic_write

KINDS: Final[tuple[str, ...]] = ("group", "door", "adapter")
RECORD: Final[str] = f"{layout.STATE_DIR}/intake/sources.json"
_NAME_MAX: Final[int] = 200


def of_call(door: str, group: str = "") -> str:
    """The source of a call: its group when it named one, else its door."""
    return f"group:{group}" if group else f"door:{door}"


def well_formed(text: object) -> bool:
    """Whether ``text`` is ``<kind>:<name>`` with a known kind and a name of plain characters."""
    if not isinstance(text, str):
        return False
    kind, colon, name = text.partition(":")
    return (
        bool(colon) and kind in KINDS and 0 < len(name) <= _NAME_MAX
        and not any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in name)
    )


def of_record(record: Mapping[str, Any]) -> list[str]:
    """The sources of one lane line, in the order they came."""
    listed = record.get("sources")
    if isinstance(listed, list):
        kept = [item for item in listed if well_formed(item)]
        if kept:
            return list(dict.fromkeys(kept))
    source = record.get("source")
    if not isinstance(source, Mapping):
        return []
    for kind in ("group", "door", "adapter"):
        value = source.get(kind)
        if isinstance(value, str) and value:
            label = f"{kind}:{value}"
            return [label] if well_formed(label) else []
    return []


def of_line(line: str) -> list[str]:
    """The sources of one lane line's text; none for a line that is not a JSON object."""
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return []
    return of_record(record) if isinstance(record, dict) else []


# ─────────────────────────────── the record ───────────────────────────────


def _lane_sizes(root: str | os.PathLike[str], agent: str) -> dict[str, int]:
    folder = layout.resolve(root, f"{layout.ISTM}/{agent}")
    if system.kind(folder) != "folder":
        return {}
    sizes: dict[str, int] = {}
    for entry in system.listing(folder, looks=True):
        if entry.kind == "file" and entry.name.endswith(".jsonl"):
            sizes[entry.name] = entry.look.size if entry.look is not None else 0
    return sizes


def _read(root: str | os.PathLike[str]) -> dict[str, Any]:
    try:
        loaded = json.loads(system.read_text(layout.resolve(root, RECORD)))
    except (system.Absent, system.Link, system.NotAFile, UnicodeError, json.JSONDecodeError):
        return {}
    agents = loaded.get("agents") if isinstance(loaded, dict) else None
    return agents if isinstance(agents, dict) else {}


def _write(root: str | os.PathLike[str], agents: Mapping[str, Any]) -> None:
    layout.ensure_folder(root, f"{layout.STATE_DIR}/intake")
    atomic_write(layout.resolve(root, RECORD), json.dumps({"agents": agents}, ensure_ascii=False, sort_keys=True) + "\n")


def _entry_sources(entry: Any) -> list[str] | None:
    if not isinstance(entry, dict) or not isinstance(entry.get("sources"), list):
        return None
    return [item for item in entry["sources"] if well_formed(item)]


def lanes_before(root: str | os.PathLike[str], agent: str) -> dict[str, int]:
    """The sizes of the agent's lane files before a write; :func:`note` compares them with the record."""
    return _lane_sizes(root, agent)


def note(root: str | os.PathLike[str], agent: str, labels: Iterable[str], before: Mapping[str, int]) -> None:
    """After the lane writer wrote ``agent``'s lane: keep the sources it brought and the lane files' sizes.

    ``before`` is :func:`lanes_before`, taken before the write. An entry that did not match the lanes before
    the write is dropped, so the next listing reads that agent's lanes again. The caller holds the lane lock.
    """
    agents = _read(root)
    entry = agents.get(agent)
    known = _entry_sources(entry)
    after = _lane_sizes(root, agent)
    if known is None and not before:
        known, entry = [], {"lanes": {}}  # the agent's first lane: nothing came before this write
    if known is None or entry.get("lanes") != dict(before):
        if agent in agents:
            agents.pop(agent)
            _write(root, agents)
        return
    wanted = list(dict.fromkeys([*known, *(label for label in labels if well_formed(label))]))
    if wanted == known and entry.get("lanes") == after:
        return
    agents[agent] = {"lanes": after, "sources": wanted}
    _write(root, agents)


def _from_lanes(root: str | os.PathLike[str], agent: str) -> list[str]:
    folder = layout.resolve(root, f"{layout.ISTM}/{agent}")
    found: list[str] = []
    if system.kind(folder) != "folder":
        return found
    for entry in sorted(system.listing(folder), key=lambda item: item.name):
        if entry.kind != "file" or not entry.name.endswith(".jsonl"):
            continue
        try:
            text = system.read_text(entry.path)
        except (system.Absent, system.Link, system.NotAFile, UnicodeError):
            continue
        for line in text.split("\n"):
            for label in of_line(line.rstrip("\r")) if line.strip() else ():
                if label not in found:
                    found.append(label)
    return found


def listed(root: str | os.PathLike[str], agents: Iterable[str], *, keep: bool = True) -> dict[str, list[str]]:
    """The sources each of ``agents`` has written through, from the record.

    An agent whose entry is missing, or whose lane files no longer have the sizes the record keeps, is read
    from its lanes (every line of every lane file of that agent, once), and with ``keep`` the record is
    written again when this account can write it. A reading's check of its source does not keep it.
    """
    record = _read(root)
    found: dict[str, list[str]] = {}
    changed = False
    for agent in agents:
        entry = record.get(agent)
        known = _entry_sources(entry)
        sizes = _lane_sizes(root, agent)
        if known is not None and entry.get("lanes") == sizes:
            found[agent] = known
            continue
        found[agent] = _from_lanes(root, agent)
        if sizes or agent in record:
            record[agent] = {"lanes": sizes, "sources": found[agent]}
            changed = True
    if changed and keep:
        try:
            _write(root, record)
        except (MemoryForestError, system.Outcome):
            pass
    return found


def require(text: object, known: Iterable[str]) -> str:
    """``text`` when it is one of the ``known`` sources; else ``source_unknown``, with the known ones listed."""
    choices = sorted(set(known))
    if well_formed(text) and text in choices:
        return str(text)
    shown = str(text)[:80] if isinstance(text, str) else type(text).__name__
    raise MemoryForestError(
        "source_unknown",
        f"`{shown}` is not a source of the turns this reader may read. A source is written "
        "`group:<id>`, `door:<name>` or `adapter:<name>`; pass one of the sources listed.",
        details={"sources": choices},
        next_step="fix",
    )
