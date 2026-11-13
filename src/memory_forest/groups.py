"""Several agents behind one connection (design, sections 6, 10 and 15).

The agent is its name. A group connection, a command's ``--agent``, a reader of
an app's member list and the settings parser all ask
:func:`memory_forest.agents.find_agent`. An id, a name exactly, or the same
letters apart from case, spaces, hyphens and underscores is that agent.
Nothing looser, and nothing is refused for being near another name.

Inside a group a caller is whoever it says it is, so any member can read what
another member's own tree and lane hold by calling under that member's name.
A group tells one owner's agents apart for memory and is no wall between them.
An open group makes a new name a new agent at once. A closed group refuses a
name that is not a member and lists the members. The main agent, an
orchestrator, reads the whole forest through any of those doors and does not
need to be listed.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import agents as agents_mod
from . import layout, registry, system
from .config import AGENT_TEXT_MAX, AgentGroup, Config, _toml_value, config_path, load_config, parse_config
from .errors import MemoryForestError
from .locking import atomic_write, hold_lock

# The same file ``mcp`` writes. Kept here so this module does not import the server.
SEEN_FILE = f"{layout.STATE_DIR}/mcp/seen.json"
_THREAD_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.Lock] = {}
# A group may name a source of members. The connection asks it to read again, once.
_SOURCES: dict[str, Callable[[str], Iterable[str]]] = {}
_REFRESH = object()


@dataclass(frozen=True, slots=True)
class Resolved:
    """The agent a call named. ``wrote`` is true only when this call changed the settings."""

    id: str
    wrote: bool = False


@dataclass(frozen=True, slots=True)
class Admit:
    """An agent to add to the group. ``create`` is false when the agent already exists."""

    id: str
    create: bool


def register_member_source(name: str, reader: Callable[[str], Iterable[str]]) -> None:
    """``reader(group_id)`` returns the app's own names. A test source is enough until a reader ships."""
    _SOURCES[name] = reader


def unregister_member_source(name: str) -> None:
    _SOURCES.pop(name, None)


@contextmanager
def exclusive(
    root: str | os.PathLike[str], name: str, *, wait_seconds: float = 30.0, label: str = ""
) -> Iterator[None]:
    """One writer at a time, across threads of this process and across processes.

    A file lock is held by the process, so two threads of one server would both
    pass it. The thread lock is taken first. A thread that does not get either
    lock within ``wait_seconds`` is ``lock_busy``.
    """
    key = f"{system.absolute(root)}\0{name}"
    with _THREAD_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(key, threading.Lock())
    if not thread_lock.acquire(timeout=wait_seconds):
        raise MemoryForestError(
            "lock_busy",
            "Another writer holds the lock.",
            details={"lock": name},
            next_step="retry",
            after_seconds=1,
        )
    try:
        with hold_lock(root, name, wait_seconds=wait_seconds, label=label or name):
            yield
    finally:
        thread_lock.release()


def require_group(config: Config, group_id: str) -> AgentGroup:
    """The group ``group_id``, or ``group_unknown`` when the settings have none."""
    found = config.agents.group(group_id)
    if found is None:
        raise MemoryForestError(
            "group_unknown",
            f"There is no group `{group_id}`. The owner declares [agents.groups.{group_id}] in forest.toml.",
            details={"group": group_id},
            next_step="report",
        )
    return found


def resolve_caller(root: str | os.PathLike[str], group_id: str, given: str) -> Resolved:
    """The agent ``given`` names on ``group_id``. An open group may admit it once."""
    cleaned = _clean(given)
    config = load_config(root)
    group = require_group(config, group_id)
    source_names: tuple[str, ...] = ()
    decision = _interpret(root, config, group, cleaned, refreshed=False, source_names=source_names)
    if decision is _REFRESH:
        source_names = _read_source(group)
        decision = _interpret(root, config, group, cleaned, refreshed=True, source_names=source_names)
    if isinstance(decision, Resolved):
        return decision
    if isinstance(decision, Admit):
        return _admit(root, group_id, cleaned, decision, source_names)
    raise decision


def listing(root: str | os.PathLike[str]) -> dict[str, Any]:
    """Each group, its members, the sources each agent has written through, and who may be one agent.

    An agent that is not in a group is listed after the groups. ``last_seen`` is
    the newest tool call on a connection, or the first one when that is all the
    seen file holds, or ``""`` when the agent has not called. ``remarks`` names
    pairs whose names differ by one edit or by one added word. A remark is never
    a refusal.
    """
    config = load_config(root)
    seen = _read_seen(root)
    laned = set(agents_mod.lanes(root))
    covered: set[str] = set()
    groups: list[dict[str, Any]] = []
    named: list[tuple[str, str]] = []
    for group in config.agents.groups:
        members = []
        for agent_id, name in _labels(config, group):
            covered.add(agent_id)
            shown = name or agent_id
            named.append((agent_id, shown))
            row = _row(root, agent_id, shown, agent_id in laned, seen)
            members.append(row)
        groups.append({"id": group.id, "members": members, "open": group.open, "source": group.source})
    others: list[dict[str, Any]] = []
    known = {item.id: item for item in config.agents.known}
    orchestrators = set(config.agents.orchestrators)
    for agent_id in sorted(set(known) | orchestrators | {lane for lane in laned if layout.is_slug(lane)}):
        if agent_id in covered:
            continue
        item = known.get(agent_id)
        name = item.name if item is not None and item.name else agent_id
        named.append((agent_id, name))
        row = _row(root, agent_id, name, agent_id in laned, seen)
        row["orchestrator"] = agent_id in orchestrators
        others.append(row)
    return {"agents": others, "groups": groups, "remarks": _remarks(named)}


def listing_lines(report: Mapping[str, Any]) -> list[str]:
    """The plain lines of :func:`listing`."""
    lines: list[str] = []
    groups = report.get("groups") or []
    agents = report.get("agents") or []
    remarks = report.get("remarks") or []
    if not groups and not agents:
        return ["no agents"]
    for group in groups:
        state = "open" if group.get("open") else "closed"
        lines.append(f"group {group['id']} ({state})")
        members = group.get("members") or []
        if not members:
            lines.append("  (no members)")
        for member in members:
            lines.append("  " + _member_line(member))
    for agent in agents:
        suffix = " — orchestrator" if agent.get("orchestrator") else ""
        lines.append("agent " + _member_line(agent) + suffix)
    if remarks:
        lines.append("may be one agent:")
        for remark in remarks:
            lines.append("  " + str(remark.get("line") or ""))
    return lines


def _member_line(member: Mapping[str, Any]) -> str:
    lane = "lane" if member.get("lane") else "no lane"
    seen = member.get("last_seen") or "not seen"
    text = f"{member['id']} — {member['name']} — {lane} — {seen}"
    sources = member.get("sources") or []
    if sources:
        text += " — " + ", ".join(sources)
    return text


def _row(root: str | os.PathLike[str], agent_id: str, name: str, lane: bool, seen: Mapping[str, Any]) -> dict[str, Any]:
    record = seen.get(agent_id)
    last = ""
    if isinstance(record, dict):
        last = str(record.get("last") or record.get("at") or "")
    return {
        "id": agent_id,
        "lane": lane,
        "last_seen": last,
        "name": name,
        "sources": _written_sources(root, agent_id),
    }


def _read_seen(root: str | os.PathLike[str]) -> dict[str, Any]:
    path = layout.resolve(root, SEEN_FILE)
    try:
        loaded = json.loads(system.read_text(path))
    except (system.Absent, system.Link, system.NotAFile, UnicodeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _written_sources(root: str | os.PathLike[str], agent_id: str) -> list[str]:
    """Groups and doors this agent has written a turn through, from its lane."""
    folder = layout.resolve(root, f"{layout.ISTM}/{agent_id}")
    if system.kind(folder) != "folder":
        return []
    found: set[str] = set()
    names = [entry.name for entry in system.listing(folder) if entry.kind == "file"]
    for name in names:
        if not name.endswith(".jsonl"):
            continue
        path = folder / name
        try:
            text = system.read_text(path)
        except (system.Absent, system.Link, system.NotAFile, UnicodeError):
            continue
        for line in text.split("\n"):
            if line.endswith("\r"):
                line = line[:-1]
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            for label in _source_names(record):
                found.add(label)
    return sorted(found)


def _source_names(record: Mapping[str, Any]) -> list[str]:
    source = record.get("source")
    if not isinstance(source, dict):
        return []
    found = []
    for key in ("group", "door", "adapter"):
        value = source.get(key)
        if isinstance(value, str) and value and value not in found:
            found.append(value)
    return found


def _clean(given: str) -> str:
    if not isinstance(given, str):
        raise MemoryForestError(
            "agent_name_invalid",
            "The agent is text: the caller's own name, short enough to be a file name.",
            next_step="fix",
        )
    cleaned = " ".join(given.split())
    if not cleaned:
        raise MemoryForestError(
            "agent_unset",
            "Name the caller: pass agent, the caller's own name.",
            next_step="fix",
        )
    if len(cleaned) > AGENT_TEXT_MAX or any(ord(character) < 32 or ord(character) == 127 for character in cleaned):
        raise MemoryForestError(
            "agent_name_invalid",
            f"That name cannot be an agent. Pass text of at most {AGENT_TEXT_MAX} characters that gives a slug.",
            details={"name": cleaned[:80]},
            next_step="fix",
        )
    return cleaned


def _one_edit(left: str, right: str) -> bool:
    """True when one insert, delete, substitution or adjacent transposition turns one into the other."""
    if left == right or not left or not right or abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        diffs = [index for index, (one, other) in enumerate(zip(left, right)) if one != other]
        if len(diffs) == 1:
            return True
        if len(diffs) == 2 and diffs[1] == diffs[0] + 1:
            first, second = diffs
            return left[first] == right[second] and left[second] == right[first]
        return False
    if len(left) > len(right):
        left, right = right, left
    skip = 0
    for index, character in enumerate(left):
        if character != right[index + skip]:
            if skip:
                return False
            skip = 1
            if character != right[index + skip]:
                return False
    return True


def _one_word(left: str, right: str) -> bool:
    """True when one name's words are the other's plus exactly one word."""
    first = left.casefold().split()
    second = right.casefold().split()
    if abs(len(first) - len(second)) != 1:
        return False
    if len(first) > len(second):
        first, second = second, first
    return any(second[:index] + second[index + 1 :] == first for index in range(len(second)))


def _remarks(rows: list[tuple[str, str]]) -> list[dict[str, str]]:
    unique: list[tuple[str, str]] = []
    seen: set[str] = set()
    for agent_id, name in rows:
        if agent_id in seen:
            continue
        seen.add(agent_id)
        unique.append((agent_id, name))
    found = []
    for index, (left_id, left_name) in enumerate(unique):
        for right_id, right_name in unique[index + 1 :]:
            left_letters = agents_mod.letters(left_name or left_id)
            right_letters = agents_mod.letters(right_name or right_id)
            if not left_letters or left_letters == right_letters:
                continue
            if _one_edit(left_letters, right_letters):
                how = "one edit"
            elif _one_word(left_name or left_id, right_name or right_id):
                how = "one added word"
            else:
                continue
            line = (
                f"{left_id} ({left_name}) and {right_id} ({right_name}) differ by {how}. "
                "If they are one agent, give them one name."
            )
            found.append({"how": how, "left": left_id, "line": line, "right": right_id})
    return found


def _labels(config: Config, group: AgentGroup) -> list[tuple[str, str]]:
    rows = []
    for member_id in group.members:
        known = config.agents.declared(member_id)
        rows.append((member_id, known.name if known is not None and known.name else ""))
    return rows


def _name_of(config: Config, agent_id: str) -> str:
    known = config.agents.declared(agent_id)
    if known is not None and known.name:
        return known.name
    return ""


def _member_rows(rows: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"id": agent_id, "name": name or agent_id} for agent_id, name in rows]


def _agent_rows(root: str | os.PathLike[str], config: Config) -> list[tuple[str, str]]:
    """Every agent the forest already has: id and the name to show."""
    rows: dict[str, str] = {}

    def add(agent_id: str, name: str) -> None:
        if not agents_mod.file_id_ok(agent_id):
            return
        current = rows.get(agent_id)
        if current is None:
            rows[agent_id] = name or ""
        elif not current and name:
            rows[agent_id] = name

    for known in config.agents.known:
        add(known.id, known.name)
    for agent_id in config.agents.orchestrators:
        add(agent_id, _name_of(config, agent_id))
    for group in config.agents.groups:
        for member in group.members:
            add(member, _name_of(config, member))
    for tool in config.intake.tools:
        if tool.agent:
            add(tool.agent, _name_of(config, tool.agent))
    for lane in agents_mod.lanes(root):
        add(lane, _name_of(config, lane))
    compiled = _compiled(root, config)
    if compiled is not None:
        for agent_id in compiled.agents:
            subject = compiled.subject(agent_id) or {}
            add(agent_id, str(subject.get("name") or _name_of(config, agent_id)))
    for known in config.agents.known:
        if known.name:
            rows[known.id] = known.name
    return list(rows.items())


def _compiled(root: str | os.PathLike[str], config: Config) -> registry.Registry | None:
    try:
        return registry.build(root, config=config)
    except (MemoryForestError, OSError):
        return None


def _orchestrator_rows(config: Config) -> list[tuple[str, str]]:
    return [(agent_id, _name_of(config, agent_id) or agent_id) for agent_id in config.agents.orchestrators]


def _on_source(given: str, source_names: tuple[str, ...]) -> bool:
    if not source_names:
        return False
    rows = [(name, name) for name in source_names]
    return len(agents_mod.find_agent(given, rows)) == 1


def _not_in_group(group: AgentGroup, rows: list[tuple[str, str]], given: str) -> MemoryForestError:
    return MemoryForestError(
        "agent_not_in_group",
        f"`{given}` is not a member of `{group.id}`. Call again under your own name, as it is listed.",
        details={"members": _member_rows(rows)},
        next_step="fix",
    )


def _ambiguous(group: AgentGroup, rows: list[tuple[str, str]], ids: list[str]) -> MemoryForestError:
    labels = dict(rows)
    return MemoryForestError(
        "agent_ambiguous",
        f"`{group.id}` has more than one member that name could be. Pass the member's id.",
        details={"members": [{"id": agent_id, "name": labels.get(agent_id) or agent_id} for agent_id in ids]},
        next_step="fix",
    )


def _invalid_name(given: str) -> MemoryForestError:
    return MemoryForestError(
        "agent_name_invalid",
        f"That name is not a slug of at most {AGENT_TEXT_MAX} characters, so it cannot become an agent. "
        "Pass a member's id, or a name that can be an agent.",
        details={"name": given[:80]},
        next_step="fix",
    )


def _interpret(
    root: str | os.PathLike[str],
    config: Config,
    group: AgentGroup,
    given: str,
    *,
    refreshed: bool,
    source_names: tuple[str, ...],
) -> Resolved | Admit | MemoryForestError | object:
    """A member, an admission, a refusal, or :data:`_REFRESH` when the member source should be read."""
    rows = list(_labels(config, group))
    hits = agents_mod.find_agent(given, rows)
    if len(hits) > 1:
        return _ambiguous(group, rows, hits)
    if len(hits) == 1:
        return Resolved(hits[0])
    orchestrators = agents_mod.find_agent(given, _orchestrator_rows(config))
    if len(orchestrators) > 1:
        return _ambiguous(group, _orchestrator_rows(config), orchestrators)
    if len(orchestrators) == 1:
        return Resolved(orchestrators[0])
    if not refreshed and group.source:
        return _REFRESH
    # A source of members is the list. Open, with no source, lets a new name in
    # and lets an agent that already exists join.
    listed = _on_source(given, source_names)
    allowed = listed or (group.open and not group.source)
    existing = agents_mod.find_agent(given, _agent_rows(root, config))
    if len(existing) > 1:
        return _ambiguous(group, _agent_rows(root, config), existing)
    if len(existing) == 1 and allowed:
        return Admit(existing[0], create=False)
    if len(existing) == 1:
        return _not_in_group(group, rows, given)
    if allowed:
        return _plan_new(root, config, group, given, rows)
    return _not_in_group(group, rows, given)


def _plan_new(
    root: str | os.PathLike[str], config: Config, group: AgentGroup, given: str, rows: list[tuple[str, str]]
) -> Admit | MemoryForestError:
    slug = layout.slugify(given)
    if not slug or not agents_mod.file_id_ok(slug):
        return _invalid_name(given)
    if group.max_members is not None and len(group.members) >= group.max_members:
        return MemoryForestError(
            "group_full",
            f"`{group.id}` already has its max_members ({group.max_members}). "
            "The owner raises max_members in forest.toml, or leaves it unset.",
            details={"group": group.id, "max_members": group.max_members},
            next_step="report",
        )
    taken = agents_mod.taken(root, config)
    kinds = agents_mod.kinds_of(slug, taken)
    if "agent" in kinds:
        return Admit(slug, create=False)
    if any(kind != "agent" for kind in kinds):
        agent_id = agents_mod.free_id(slug, taken)
        if not agent_id:
            return _invalid_name(given)
        return Admit(agent_id, create=True)
    del rows
    return Admit(slug, create=True)


def _read_source(group: AgentGroup) -> tuple[str, ...]:
    reader = _SOURCES.get(group.source)
    if reader is None:
        return ()
    try:
        found = reader(group.id)
    except Exception:
        return ()
    names = []
    for item in found:
        if isinstance(item, str) and item.strip():
            names.append(" ".join(item.split()))
    return tuple(names)


def _admit(
    root: str | os.PathLike[str],
    group_id: str,
    given: str,
    admit: Admit,
    source_names: tuple[str, ...],
) -> Resolved:
    """Add the agent to the group once. A second caller finds the member and writes nothing."""
    try:
        with exclusive(root, "agents-group", wait_seconds=30, label="group member"):
            config = load_config(root)
            group = require_group(config, group_id)
            decision = _interpret(root, config, group, given, refreshed=True, source_names=source_names)
            if isinstance(decision, Resolved):
                return Resolved(decision.id, wrote=False)
            if isinstance(decision, MemoryForestError):
                raise decision
            if not isinstance(decision, Admit):
                raise MemoryForestError(
                    "agent_name_invalid",
                    "That name cannot be an agent.",
                    details={"name": given[:80]},
                    next_step="fix",
                )
            if group.max_members is not None and decision.id not in group.members and len(group.members) >= group.max_members:
                raise MemoryForestError(
                    "group_full",
                    f"`{group.id}` already has its max_members ({group.max_members}). "
                    "The owner raises max_members in forest.toml, or leaves it unset.",
                    details={"group": group.id, "max_members": group.max_members},
                    next_step="report",
                )
            path = config_path(root)
            if _settings_unwritable(path):
                raise _unwritable(group_id, decision.id, given)
            try:
                original = system.read_text(path)
            except system.Outcome as exc:
                raise _unwritable(group_id, decision.id, given) from exc
            known = config.agents.declared(decision.id)
            write_known = decision.create or known is None
            stored_name = given if decision.create or known is None or not known.name else known.name
            try:
                updated = _patch(original, group, decision.id, stored_name, write_known=write_known)
                parsed = parse_config(updated)
            except MemoryForestError:
                raise
            admitted = require_group(parsed, group_id)
            declared = parsed.agents.declared(decision.id)
            if decision.id not in admitted.members or (write_known and (declared is None or declared.name != stored_name)):
                raise _unpatched(group_id, decision.id, stored_name)
            if updated == original:
                return Resolved(decision.id, wrote=False)
            try:
                atomic_write(path, updated)
            except system.Outcome as exc:
                raise _unwritable(group_id, decision.id, given) from exc
            return Resolved(decision.id, wrote=True)
    except MemoryForestError:
        raise
    except system.Outcome as exc:
        raise _unwritable(group_id, admit.id, given) from exc


def _settings_unwritable(path: os.PathLike[str]) -> bool:
    target = Path(path)
    if not system.may(target.parent, "write"):
        return True
    return system.look_or_none(target) is not None and not system.may(target, "change")


def hand_lines(group_id: str, member_id: str, name: str) -> str:
    """The lines the owner adds when the settings cannot be written or patched."""
    return (
        f"[agents.groups.{group_id}]\n"
        f"members = [{_toml_value(member_id)}]\n"
        f"\n"
        f"[agents.known.{member_id}]\n"
        f"name = {_toml_value(name)}\n"
    )


def _unwritable(group_id: str, member_id: str, name: str) -> MemoryForestError:
    lines = hand_lines(group_id, member_id, name)
    return MemoryForestError(
        "settings_unwritable",
        "forest.toml could not be written, so the turn was stored nowhere. The owner adds these lines:\n" + lines,
        details={"agent": member_id, "group": group_id, "lines": lines},
        next_step="report",
    )


def _unpatched(group_id: str, member_id: str, name: str) -> MemoryForestError:
    lines = hand_lines(group_id, member_id, name)
    return MemoryForestError(
        "settings_unpatched",
        f"forest.toml has no [agents.groups.{group_id}] table that can be extended. "
        "The owner adds these lines:\n" + lines,
        details={"agent": member_id, "group": group_id, "lines": lines},
        next_step="report",
    )


def _patch(text: str, group: AgentGroup, member_id: str, name: str, *, write_known: bool) -> str:
    """The settings text with one member added, and its known table when it is new.

    Every other line stays, in order. A header in a comment is not a header.
    A header written with quotes or spaces is found.
    """
    span = _table_span(text, f"agents.groups.{group.id}")
    if span is None:
        raise _unpatched(group.id, member_id, name)
    start, end = span
    block = text[start:end]
    try:
        block = _extend_members(block, member_id)
    except MemoryForestError:
        raise _unpatched(group.id, member_id, name) from None
    text = text[:start] + block + text[end:]
    if write_known and _table_span(text, f"agents.known.{member_id}") is None:
        newline = "\r\n" if "\r\n" in text else "\n"
        if text and not text.endswith(("\n", "\r\n")):
            text += newline
        text += f"{newline}[agents.known.{member_id}]{newline}name = {_toml_value(name)}{newline}"
    return text


def _extend_members(block: str, member_id: str) -> str:
    key_at = _find_key(block, "members")
    if key_at is None:
        newline = "\r\n" if "\r\n" in block else "\n"
        header_end = block.find("\n")
        if header_end < 0:
            return block + newline + f"members = [{_toml_value(member_id)}]" + newline
        return block[: header_end + 1] + f"members = [{_toml_value(member_id)}]" + newline + block[header_end + 1 :]
    value_at = _value_at(block, key_at)
    if value_at is None or block[value_at] != "[":
        raise MemoryForestError("settings_unpatched", "The members value is not a list.", next_step="report")
    end = _array_end(block, value_at)
    if end is None:
        raise MemoryForestError("settings_unpatched", "The members list does not close.", next_step="report")
    body = block[value_at + 1 : end]
    token = _toml_value(member_id)
    newline = "\r\n" if "\r\n" in block else "\n"
    if "\n" not in body:
        addition = token if body.strip() == "" else ", " + token
        return block[:end] + addition + block[end:]
    if _last_code(body) in {"", ","}:
        line_start = block.rfind("\n", 0, end) + 1
        indent = _item_indent(body)
        return block[:line_start] + f"{indent}{token},{newline}" + block[line_start:]
    return block[:end] + ", " + token + block[end:]


def _find_key(block: str, key: str) -> int | None:
    """Index of ``key`` where it is a table key, not a word in a string or a comment."""
    index = 0
    length = len(block)
    in_string = False
    quote = ""
    escape = False
    line_start = True
    while index < length:
        character = block[index]
        if in_string:
            if escape:
                escape = False
            elif character == "\\":
                escape = True
            elif character == quote:
                in_string = False
            index += 1
            continue
        if character == "\n":
            line_start = True
            index += 1
            continue
        if character in " \t\r":
            index += 1
            continue
        if character == "#":
            newline = block.find("\n", index)
            index = length if newline < 0 else newline
            continue
        if character in "\"'":
            in_string = True
            quote = character
            line_start = False
            index += 1
            continue
        if line_start and block.startswith(key, index):
            after = index + len(key)
            if after >= length or block[after] in " \t=":
                return index
        line_start = False
        index += 1
    return None


def _value_at(block: str, key_at: int) -> int | None:
    equals = block.find("=", key_at)
    if equals < 0:
        return None
    index = equals + 1
    length = len(block)
    while index < length:
        character = block[index]
        if character in " \t\r":
            index += 1
            continue
        if character == "#":
            newline = block.find("\n", index)
            index = length if newline < 0 else newline + 1
            continue
        if character == "\n":
            index += 1
            continue
        return index
    return None


def _array_end(text: str, open_index: int) -> int | None:
    depth = 0
    index = open_index
    in_string = False
    quote = ""
    escape = False
    while index < len(text):
        character = text[index]
        if in_string:
            if escape:
                escape = False
            elif character == "\\":
                escape = True
            elif character == quote:
                in_string = False
            index += 1
            continue
        if character == "#":
            newline = text.find("\n", index)
            index = len(text) if newline < 0 else newline + 1
            continue
        if character in "\"'":
            in_string = True
            quote = character
            index += 1
            continue
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _last_code(body: str) -> str:
    last = ""
    index = 0
    in_string = False
    quote = ""
    escape = False
    while index < len(body):
        character = body[index]
        if in_string:
            if escape:
                escape = False
            elif character == "\\":
                escape = True
            elif character == quote:
                in_string = False
            index += 1
            continue
        if character == "#":
            newline = body.find("\n", index)
            index = len(body) if newline < 0 else newline + 1
            continue
        if character in "\"'":
            in_string = True
            quote = character
            last = character
            index += 1
            continue
        if not character.isspace():
            last = character
        index += 1
    return last


def _item_indent(body: str) -> str:
    for line in reversed(body.splitlines()):
        if line.strip():
            return line[: len(line) - len(line.lstrip(" \t"))]
    return "  "


def _header_key(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("[") or stripped.startswith("[["):
        return None
    end = stripped.find("]")
    if end < 0:
        return None
    return _normalize_key(stripped[1:end])


def _normalize_key(body: str) -> str:
    parts: list[str] = []
    index = 0
    while index < len(body):
        while index < len(body) and body[index] in " \t.":
            index += 1
        if index >= len(body):
            break
        if body[index] in "\"'":
            quote = body[index]
            index += 1
            chars: list[str] = []
            while index < len(body) and body[index] != quote:
                if body[index] == "\\" and quote == '"' and index + 1 < len(body):
                    chars.append(body[index + 1])
                    index += 2
                    continue
                chars.append(body[index])
                index += 1
            if index < len(body) and body[index] == quote:
                index += 1
            parts.append("".join(chars))
            continue
        start = index
        while index < len(body) and body[index] not in " \t.\"'":
            index += 1
        parts.append(body[start:index])
    return ".".join(part for part in parts if part)


def _table_span(text: str, key: str) -> tuple[int, int] | None:
    """The start and end of the table ``key``. A header in a comment is not a header."""
    headers: list[tuple[int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        found = _header_key(line)
        if found is not None:
            headers.append((offset, found))
        offset += len(line)
    for index, (start, name) in enumerate(headers):
        if name != key:
            continue
        end = headers[index + 1][0] if index + 1 < len(headers) else len(text)
        return start, end
    return None
