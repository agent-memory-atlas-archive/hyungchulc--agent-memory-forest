"""Where a turn came from: its source, written so it cannot be confused (design, sections 6 and 15).

A source is a kind and a name: ``group:<id>`` for a turn that came through a group connection (the tool or the
command), ``door:<name>`` for a turn that came through an agent's own connection (``door:mcp`` for
``memory_log`` on ``forest mcp --as``, ``door:cli`` for ``forest log --as``), and ``adapter:<name>`` for a turn
``forest intake`` read from a tool's transcript. The kind is part of the source, so a group called ``mcp`` is
``group:mcp`` and never the door. A turn carries its sources in ``sources``, in the order they came; a line
written before that field existed has its source worked out from its ``source`` object.

The sources each agent has written through are kept in one small record, ``.forest/intake/sources.json``,
which the lane writer updates when a turn brings a source the record does not hold. Beside them it keeps the
change stamp of each of the agent's lane files as the writer left them; a lane folder that no longer matches (a file
written another way, a file renamed) has its agent's entry read again from its lanes, once.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Mapping
from typing import Any, Final

from . import layout, names, system
from .errors import MemoryForestError
from .locking import atomic_write

KINDS: Final[tuple[str, ...]] = ("group", "door", "adapter")
RECORD: Final[str] = f"{layout.STATE_DIR}/intake/sources.json"
_NAME_MAX: Final[int] = 200
# A completed lane scan is useful even when a read-only forest cannot keep it.
_SCANNED: dict[tuple[str, str], tuple[Any, dict[str, Any]]] = {}


def of_call(door: str, group: str = "") -> str:
    """The source of a call: its group when it named one, else its door."""
    return f"group:{group}" if group else f"door:{door}"


def arrivals(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every arrival in order. A legacy line is one arrival, with every surviving original field."""
    value = record.get("arrivals")
    if isinstance(value, list) and value and all(isinstance(item, Mapping) for item in value):
        return [dict(item) for item in value]
    return [{"at": record.get("at"), "origin": record.get("origin", "unknown"), "source": record.get("source", {}),
             "sources": of_record(record)}]


def well_formed(text: object) -> bool:
    """Whether ``text`` is ``<kind>:<name>`` with a known kind and a name of plain characters."""
    if not isinstance(text, str):
        return False
    kind, colon, name = text.partition(":")
    return (
        bool(colon) and kind in KINDS and 0 < len(name) <= _NAME_MAX
        and not names.forbidden(name) and not any(character.isspace() for character in name)
    )


def of_record(record: Mapping[str, Any]) -> list[str]:
    """The sources of one lane line, in the order they came."""
    arrivals = record.get("arrivals")
    if isinstance(arrivals, list) and arrivals and all(isinstance(item, Mapping) for item in arrivals):
        return list(dict.fromkeys(label for arrival in arrivals if isinstance(arrival, Mapping)
                                  for label in of_record(arrival)))
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


def _lane_sizes(root: str | os.PathLike[str], agent: str) -> dict[str, list[int]]:
    """Change stamps through the door: device, inode, size, modification and change nanoseconds."""
    folder = layout.resolve(root, f"{layout.ISTM}/{agent}")
    if system.kind(folder) != "folder":
        return {}
    sizes: dict[str, list[int]] = {}
    for entry in system.listing(folder, looks=True):
        if entry.kind == "file" and entry.name.endswith(".jsonl"):
            info = entry.look
            sizes[entry.name] = [info.dev, info.ino, info.size, info.mtime_ns, info.ctime_ns]
    return sizes


def _read(root: str | os.PathLike[str]) -> dict[str, Any]:
    try:
        loaded = json.loads(system.read_text(layout.resolve(root, RECORD)))
    except (MemoryForestError, system.Outcome, UnicodeError, json.JSONDecodeError):
        return {}
    agents = loaded.get("agents") if isinstance(loaded, dict) and set(loaded) == {"agents"} else None
    if not isinstance(agents, dict) or any(not layout.is_slug(agent) or _entry_sources(entry) is None
                                          for agent, entry in agents.items()):
        return {}
    return agents


def _write(root: str | os.PathLike[str], agents: Mapping[str, Any]) -> None:
    layout.ensure_folder(root, f"{layout.STATE_DIR}/intake")
    atomic_write(layout.resolve(root, RECORD), json.dumps({"agents": agents}, ensure_ascii=False, sort_keys=True) + "\n")


def _entry_sources(entry: Any) -> list[str] | None:
    if not isinstance(entry, dict) or set(entry) != {"sources", "lanes", "seal"}:
        return None
    labels, lanes = entry["sources"], entry["lanes"]
    if not isinstance(labels, list) or any(not well_formed(item) for item in labels):
        return None
    if len(set(labels)) != len(labels) or not isinstance(lanes, dict):
        return None
    for name, stamp in lanes.items():
        if (not isinstance(name, str) or not name.endswith(".jsonl") or "/" in name
                or not isinstance(stamp, list) or len(stamp) != 5
                or any(type(part) is not int or part < 0 for part in stamp)):
            return None
    if entry["seal"] != _seal(lanes, labels):
        return None
    return labels


def _seal(lanes: Mapping[str, Any], labels: Any) -> str:
    """An integrity check of the complete derived entry, not an authentication claim."""
    return hashlib.sha256(json.dumps([lanes, labels], sort_keys=True).encode()).hexdigest()


def lanes_before(root: str | os.PathLike[str], agent: str) -> dict[str, list[int]]:
    """The change stamps of the agent's lane files before a write; :func:`note` compares the record."""
    return _lane_sizes(root, agent)


def note(root: str | os.PathLike[str], agent: str, labels: Iterable[str], before: Mapping[str, Any]) -> None:
    """After the lane writer wrote ``agent``'s lane: keep the sources it brought and the lane change stamps.

    ``before`` is :func:`lanes_before`, taken before the write. An entry that did not match the lanes before
    the write is dropped, so the next listing reads that agent's lanes again. The caller holds the lane lock.
    """
    agents = _read(root)
    entry = agents.get(agent)
    known = _entry_sources(entry)
    after = _lane_sizes(root, agent)
    if known is None or entry["lanes"] != dict(before):
        wanted = _from_lanes(root, agent)
    else:
        wanted = list(dict.fromkeys([*known, *(label for label in labels if well_formed(label))]))
    updated = {"lanes": after, "sources": wanted, "seal": _seal(after, wanted)}
    if agents.get(agent) == updated:
        return
    agents[agent] = updated
    _SCANNED.pop((str(root), agent), None)
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
            text = system.read_text(entry.path, errors="replace")
        except (system.Absent, system.Link, system.NotAFile, UnicodeError):
            continue
        for line in text.split("\n"):
            for label in of_line(line.rstrip("\r")) if line.strip() else ():
                if label not in found:
                    found.append(label)
    return found


def listed(root: str | os.PathLike[str], agents: Iterable[str], *, keep: bool = True,
           verify: bool = False) -> dict[str, list[str]]:
    """The sources each of ``agents`` has written through, from the record.

    An agent whose entry is missing, or whose lane files no longer have the change stamps the record keeps, is read
    from its lanes (every line of every lane file of that agent, once), and with ``keep`` the record is
    written again when this account can write it. A completed scan is retained even when it cannot be written.
    """
    token = system.look_or_none(system.absolute(root) / RECORD)
    record = _read(root)
    found: dict[str, list[str]] = {}
    changed = False
    for agent in agents:
        entry = record.get(agent)
        known = _entry_sources(entry)
        sizes = _lane_sizes(root, agent)
        cached = _SCANNED.get((str(root), agent))
        scanned = cached[1] if cached is not None and cached[0] == token else None
        if scanned is not None and scanned["lanes"] == sizes:
            entry, known = scanned, scanned["sources"]
        elif verify or known is None or entry.get("lanes") != sizes:
            known = _from_lanes(root, agent)
            entry = {"lanes": sizes, "sources": known, "seal": _seal(sizes, known)}
            _SCANNED[(str(root), agent)] = (token, entry)
        if record.get(agent) == entry:
            found[agent] = known
            continue
        found[agent] = known
        if sizes or agent in record:
            record[agent] = entry
            changed = True
    if changed and keep:
        try:
            _write(root, record)
        except (MemoryForestError, system.Outcome):
            pass
        else:
            token = system.look_or_none(system.absolute(root) / RECORD)
            for agent in found:
                cached = _SCANNED.get((str(root), agent))
                if cached is not None:
                    _SCANNED[(str(root), agent)] = (token, cached[1])
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
