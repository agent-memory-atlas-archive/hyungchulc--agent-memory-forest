"""Several agents behind one connection (design, sections 6, 10 and 15).

A group is one app the owner runs, with several named agents and one tool
connection. The caller says its own name. That name resolves only inside the
group: a member's id, its declared name, or the same letters apart from case,
spaces, hyphens and underscores. It is never an agent outside the group, never
an orchestrator who is not a member, and never the group's own id.

Inside the group the name tells the agents apart for memory. It is not a wall:
each member reads what that member may read of the shared trees. A closed
group refuses a stranger and names the members. An open group admits a new
slug once, unless it is a near-miss of a member, and writes that member into
the settings the way a settings file is written: the same bytes when nothing
changed.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from . import agents as agents_mod
from . import layout, registry
from .config import AgentGroup, Config, _toml_value, config_path, load_config, parse_config
from .errors import MemoryForestError
from .locking import atomic_write, hold_lock

# The same file ``mcp`` writes. Kept here so this module does not import the server.
SEEN_FILE = f"{layout.STATE_DIR}/mcp/seen.json"
_ADMIT = object()
_THREAD_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.Lock] = {}


@dataclass(frozen=True, slots=True)
class Resolved:
    """The member a call named. ``wrote`` is true only when this call changed the settings."""

    id: str
    wrote: bool = False


@contextmanager
def exclusive(
    root: str | os.PathLike[str], name: str, *, wait_seconds: float = 30.0, label: str = ""
) -> Iterator[None]:
    """One writer at a time, across threads of this process and across processes.

    A file lock is held by the process, so two threads of one server would both
    pass it. The thread lock is taken first. A thread that does not get either
    lock within ``wait_seconds`` is ``lock_busy``.
    """
    key = f"{os.path.abspath(root)}\0{name}"
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
            f"There is no group `{group_id}`.",
            details={"group": group_id},
            next_step="report",
            command=f"declare [agents.groups.{group_id}] in forest.toml",
        )
    return found


def resolve_caller(root: str | os.PathLike[str], group_id: str, given: str) -> Resolved:
    """The member ``given`` names on ``group_id``. An open group may admit it once."""
    cleaned = _clean(given)
    config = load_config(root)
    group = require_group(config, group_id)
    decision = _interpret(root, config, group, cleaned)
    if isinstance(decision, Resolved):
        return decision
    if decision is not _ADMIT:
        raise decision
    return _admit(root, group.id, cleaned)


def listing(root: str | os.PathLike[str]) -> dict[str, Any]:
    """Each group, its members, whether each has a lane, and when it was last seen.

    An agent that is not in a group is listed after the groups. ``last_seen`` is
    the newest tool call on a connection, or the first one when that is all the
    seen file holds, or ``""`` when the agent has not called.
    """
    config = load_config(root)
    seen = _read_seen(root)
    laned = set(agents_mod.lanes(root))
    covered: set[str] = set()
    groups: list[dict[str, Any]] = []
    for group in config.agents.groups:
        members = []
        for agent_id, name in _labels(config, group):
            covered.add(agent_id)
            members.append(_row(agent_id, name or agent_id, agent_id in laned, seen))
        groups.append({"id": group.id, "members": members, "open": group.open})
    others: list[dict[str, Any]] = []
    known = {item.id: item for item in config.agents.known}
    orchestrators = set(config.agents.orchestrators)
    for agent_id in sorted(set(known) | orchestrators | {lane for lane in laned if layout.is_slug(lane)}):
        if agent_id in covered:
            continue
        item = known.get(agent_id)
        name = item.name if item is not None and item.name else agent_id
        row = _row(agent_id, name, agent_id in laned, seen)
        row["orchestrator"] = agent_id in orchestrators
        others.append(row)
    return {"agents": others, "groups": groups}


def listing_lines(report: Mapping[str, Any]) -> list[str]:
    """The plain lines of :func:`listing`."""
    lines: list[str] = []
    groups = report.get("groups") or []
    agents = report.get("agents") or []
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
    return lines


def _member_line(member: Mapping[str, Any]) -> str:
    lane = "lane" if member.get("lane") else "no lane"
    seen = member.get("last_seen") or "not seen"
    return f"{member['id']} — {member['name']} — {lane} — {seen}"


def _row(agent_id: str, name: str, lane: bool, seen: Mapping[str, Any]) -> dict[str, Any]:
    record = seen.get(agent_id)
    last = ""
    if isinstance(record, dict):
        last = str(record.get("last") or record.get("at") or "")
    return {"id": agent_id, "lane": lane, "last_seen": last, "name": name}


def _read_seen(root: str | os.PathLike[str]) -> dict[str, Any]:
    path = layout.resolve(root, SEEN_FILE)
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _clean(given: str) -> str:
    if not isinstance(given, str):
        raise MemoryForestError(
            "agent_unset",
            "Name the caller.",
            next_step="run",
            command="pass agent, the caller's own name",
        )
    cleaned = " ".join(given.split())
    if not cleaned:
        raise MemoryForestError(
            "agent_unset",
            "Name the caller.",
            next_step="run",
            command="pass agent, the caller's own name",
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in cleaned):
        raise MemoryForestError(
            "agent_name_invalid",
            "That name cannot be an agent.",
            details={"name": cleaned},
            next_step="run",
            command="pass a member's id",
        )
    return cleaned


def _letters(text: str) -> str:
    """The letters of ``text``, ignoring case, spaces, hyphens and underscores."""
    return "".join(character for character in text.casefold() if character not in " -_")


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


def _labels(config: Config, group: AgentGroup) -> list[tuple[str, str]]:
    rows = []
    for member_id in group.members:
        known = config.agents.declared(member_id)
        rows.append((member_id, known.name if known is not None and known.name else ""))
    return rows


def _forms(agent_id: str, name: str) -> set[str]:
    found = {_letters(agent_id)}
    if name:
        found.add(_letters(name))
    found.discard("")
    return found


def _member_rows(config: Config, group: AgentGroup) -> list[dict[str, str]]:
    return [{"id": agent_id, "name": name or agent_id} for agent_id, name in _labels(config, group)]


def _interpret(
    root: str | os.PathLike[str], config: Config, group: AgentGroup, given: str
) -> Resolved | MemoryForestError | object:
    """A member, a refusal, or :data:`_ADMIT` when an open group may add ``given``."""
    labels = _labels(config, group)
    slug = layout.slugify(given)
    folded = _letters(given)
    exact: list[str] = []
    for agent_id, name in labels:
        if given == agent_id or (name and given == name) or (slug and slug == agent_id):
            exact.append(agent_id)
    if len(exact) == 1:
        return Resolved(exact[0])
    if len(exact) > 1:
        return _ambiguous(group, config, exact)
    folded_hits = [
        agent_id for agent_id, name in labels if folded and folded in _forms(agent_id, name)
    ]
    if len(folded_hits) == 1:
        return Resolved(folded_hits[0])
    if len(folded_hits) > 1:
        return _ambiguous(group, config, folded_hits)
    near = [
        (agent_id, name or agent_id)
        for agent_id, name in labels
        if folded and any(_one_edit(folded, form) for form in _forms(agent_id, name))
    ]
    if len(near) == 1:
        agent_id, name = near[0]
        return MemoryForestError(
            "agent_near_miss",
            f"`{given}` is one edit from `{agent_id}` ({name}). A typing slip does not make another agent.",
            details={"agent": agent_id, "name": name},
            next_step="run",
            command=f"pass agent {agent_id}",
        )
    if len(near) > 1:
        return MemoryForestError(
            "agent_near_miss",
            f"`{given}` is one edit from more than one member. Pass the member's id.",
            details={"members": [{"id": agent_id, "name": name} for agent_id, name in near]},
            next_step="run",
            command="pass the member's id",
        )
    if _is_group_itself(group, given, slug, folded):
        return MemoryForestError(
            "agent_is_group",
            f"`{group.id}` is the group, not an agent. Pass a member.",
            details={"group": group.id},
            next_step="run",
            command="pass a member's id",
        )
    compiled = _compiled(root, config)
    outside = _outside(root, config, group, compiled)
    hits = [
        (agent_id, name)
        for agent_id, name in outside.items()
        if _same_agent(given, slug, folded, agent_id, name)
    ]
    orchestrators = [hit for hit in hits if hit[0] in config.agents.orchestrators]
    if orchestrators:
        agent_id, name = orchestrators[0]
        return _outside_refusal(group, agent_id, name, orchestrator=True)
    if hits:
        agent_id, name = hits[0]
        return _outside_refusal(group, agent_id, name, orchestrator=agent_id in config.agents.orchestrators)
    near_outside = [
        (agent_id, name)
        for agent_id, name in outside.items()
        if folded and any(_one_edit(folded, form) for form in _forms(agent_id, name))
    ]
    if near_outside:
        orchestrators = [hit for hit in near_outside if hit[0] in config.agents.orchestrators]
        agent_id, name = (orchestrators or near_outside)[0]
        return _outside_refusal(group, agent_id, name, orchestrator=bool(orchestrators), near=True)
    if not group.open:
        return MemoryForestError(
            "agent_not_in_group",
            f"`{given}` is not a member of `{group.id}`. Call again with one of them.",
            details={"members": _member_rows(config, group)},
            next_step="run",
            command="call again with one of the members",
        )
    if not slug or not layout.is_slug(slug):
        return MemoryForestError(
            "agent_name_invalid",
            "That name is not a slug, so it cannot become an agent.",
            details={"name": given},
            next_step="run",
            command="pass a member's id, or a name that slugs to a new agent",
        )
    areas = _area_trees(compiled)
    reason = agents_mod.why_invalid(slug, config, areas)
    if reason is not None:
        return MemoryForestError(
            "agent_name_invalid",
            f"`{slug}` {reason}.",
            details={"agent": slug},
            next_step="run",
            command="pass a member's id, or a name that can be an agent",
        )
    return _ADMIT


def _outside_refusal(
    group: AgentGroup, agent_id: str, name: str, *, orchestrator: bool, near: bool = False
) -> MemoryForestError:
    """A caller cannot be an agent outside the group, nor one edit away from one."""
    if near:
        message = (
            f"`{agent_id}` ({name}) is outside `{group.id}`, and that name is one edit away. "
            "A typing slip does not make another agent."
        )
    elif orchestrator:
        message = f"`{agent_id}` ({name}) is an orchestrator and is not a member of `{group.id}`."
    else:
        message = f"`{agent_id}` ({name}) is an agent outside `{group.id}`."
    return MemoryForestError(
        "orchestrator_outside_group" if orchestrator else "agent_outside_group",
        message,
        details={"agent": agent_id, "name": name},
        next_step="run",
        command="pass a member's id",
    )


def _ambiguous(group: AgentGroup, config: Config, ids: list[str]) -> MemoryForestError:
    labels = dict(_labels(config, group))
    return MemoryForestError(
        "agent_ambiguous",
        f"`{group.id}` has more than one member that name could be. Pass the member's id.",
        details={"members": [{"id": agent_id, "name": labels.get(agent_id) or agent_id} for agent_id in ids]},
        next_step="run",
        command="pass the member's id",
    )


def _is_group_itself(group: AgentGroup, given: str, slug: str, folded: str) -> bool:
    if given == group.id or (slug and slug == group.id):
        return True
    return bool(folded) and folded == _letters(group.id)


def _same_agent(given: str, slug: str, folded: str, agent_id: str, name: str) -> bool:
    if given == agent_id or (name and given == name) or (slug and slug == agent_id):
        return True
    if not folded:
        return False
    return folded == _letters(agent_id) or (bool(name) and folded == _letters(name))


def _compiled(root: str | os.PathLike[str], config: Config) -> registry.Registry | None:
    try:
        return registry.build(root, config=config)
    except (MemoryForestError, OSError):
        return None


def _area_trees(compiled: registry.Registry | None) -> list[str]:
    if compiled is None:
        return []
    return sorted(
        tree for tree, kind in compiled.tree_kinds.items() if kind != registry.TREE_KIND_AGENT
    )


def _outside(
    root: str | os.PathLike[str],
    config: Config,
    group: AgentGroup,
    compiled: registry.Registry | None,
) -> dict[str, str]:
    """Agents that are not members of ``group``: id to the name to show.

    A declared name wins. A registry name fills an id that still has none.
    """
    members = set(group.members)
    found: dict[str, str] = {}

    def add(agent_id: str, name: str, *, overwrite: bool) -> None:
        if agent_id in members or not layout.is_slug(agent_id):
            return
        shown = name or agent_id
        if agent_id not in found:
            found[agent_id] = shown
        elif overwrite and found[agent_id] == agent_id and shown != agent_id:
            found[agent_id] = shown

    for known in config.agents.known:
        add(known.id, known.name or known.id, overwrite=True)
    for agent_id in config.agents.orchestrators:
        add(agent_id, agent_id, overwrite=False)
    for lane in agents_mod.lanes(root):
        add(lane, lane, overwrite=False)
    if compiled is not None:
        for agent_id in compiled.agents:
            subject = compiled.subject(agent_id) or {}
            add(agent_id, str(subject.get("name") or agent_id), overwrite=True)
    return found


def _admit(root: str | os.PathLike[str], group_id: str, given: str) -> Resolved:
    """Add ``given`` to the group once. A second caller finds the member and writes nothing."""
    with exclusive(root, "agents-group", wait_seconds=30, label="group member"):
        config = load_config(root)
        group = require_group(config, group_id)
        decision = _interpret(root, config, group, given)
        if isinstance(decision, Resolved):
            return Resolved(decision.id, wrote=False)
        if decision is not _ADMIT:
            raise decision
        member_id = layout.slugify(given)
        path = config_path(root)
        original = path.read_text(encoding="utf-8")
        updated = _patch(original, group, member_id, given)
        parsed = parse_config(updated)
        admitted = require_group(parsed, group_id)
        known = parsed.agents.declared(member_id)
        if member_id not in admitted.members or known is None or known.name != given:
            raise MemoryForestError(
                "settings_unpatched",
                "The group could not be updated in forest.toml.",
                details={"group": group_id, "agent": member_id},
                next_step="report",
                command=f"add `{member_id}` under [agents.groups.{group_id}] and [agents.known.{member_id}]",
            )
        if updated == original:
            return Resolved(member_id, wrote=False)
        atomic_write(path, updated)
        return Resolved(member_id, wrote=True)


def _patch(text: str, group: AgentGroup, member_id: str, name: str) -> str:
    """The settings text with ``member_id`` in the group and its given name kept."""
    header = f"[agents.groups.{group.id}]"
    start, end = _table_span(text, header)
    block = text[start:end]
    members = tuple(dict.fromkeys((*group.members, member_id)))
    block = _replace_members(block, members)
    text = text[:start] + block + text[end:]
    known = f"[agents.known.{member_id}]"
    if known not in text:
        if not text.endswith("\n"):
            text += "\n"
        text += f"\n{known}\nname = {_toml_value(name)}\n"
    return text


def _replace_members(block: str, members: tuple[str, ...]) -> str:
    rendered = "members = " + _toml_value(members)
    line_at = 0
    found = -1
    while line_at <= len(block):
        next_line = block.find("\n", line_at)
        row = block[line_at: next_line if next_line >= 0 else len(block)]
        left, separator, _right = row.strip().partition("=")
        if separator and left.strip() == "members":
            found = line_at + row.index("members")
            break
        if next_line < 0:
            break
        line_at = next_line + 1
    if found < 0:
        newline = block.find("\n")
        if newline < 0:
            return block + "\n" + rendered + "\n"
        return block[: newline + 1] + rendered + "\n" + block[newline + 1 :]
    bracket = block.find("[", found)
    if bracket < 0:
        raise MemoryForestError(
            "settings_unpatched",
            "The group's members line could not be updated.",
            next_step="report",
            command="write members as a list of slugs under the group",
        )
    depth = 0
    end = None
    for index in range(bracket, len(block)):
        if block[index] == "[":
            depth += 1
        elif block[index] == "]":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end is None:
        raise MemoryForestError(
            "settings_unpatched",
            "The group's members line could not be updated.",
            next_step="report",
            command="write members as a list of slugs under the group",
        )
    return block[:found] + rendered + block[end:]


def _table_span(text: str, header: str) -> tuple[int, int]:
    """The start and end of the table ``header`` (already ``[name]``). The end is the next table."""
    marker = header if header.startswith("[") else f"[{header}]"
    start = 0
    at = -1
    while True:
        found = text.find(marker, start)
        if found < 0:
            raise MemoryForestError(
                "settings_unpatched",
                f"forest.toml has no `[{header}]` table to update.",
                details={"table": header},
                next_step="report",
                command=f"keep [{header}] as its own table",
            )
        line_start = text.rfind("\n", 0, found) + 1
        if text[line_start:found].strip() == "":
            at = found
            break
        start = found + len(marker)
    line = text.find("\n", at)
    if line < 0:
        return at, len(text)
    rest = line + 1
    while rest < len(text):
        next_line = text.find("\n", rest)
        row = text[rest: next_line if next_line >= 0 else len(text)]
        if row.startswith("["):
            return at, rest
        if next_line < 0:
            break
        rest = next_line + 1
    return at, len(text)
