"""Several agents behind one connection (design, sections 6, 10 and 15).

The agent is its name. A group connection, a command's ``--agent``, a source of members, the settings parser
and ``forest agents`` all compare names with :func:`memory_forest.names.key` and find an agent with
:func:`memory_forest.names.find`: the given text is an agent's id character for character, or its key is the
key of the agent's stored name. A name no agent has is a new agent, with an id made once from the name
(:func:`memory_forest.names.new_id`) and stored beside it. Nothing is refused because of what an agent is
called, in any script.

Inside a group a caller is whoever it says it is, so any member can read what another member's own tree and
lane hold by calling under that member's name. A group tells one owner's agents apart for memory and is no
wall between them. An open group makes a new name a new agent at once. A closed group refuses a name that is
not a member and lists the members. The main agent, an orchestrator, reads the whole forest through any of
those doors and does not need to be listed.
"""

from __future__ import annotations

import json
import os
import threading
import time
import tomllib
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import PurePath as Path
from typing import Any

from . import agents as agents_mod
from . import layout, names, registry, sources, system
from .config import BOM, AgentGroup, Config, _toml_value, config_path, load_config, parse_config
from .errors import MemoryForestError
from .locking import atomic_write, hold_lock, writer_lock

# The same file ``mcp`` writes. Kept here so this module does not import the server.
SEEN_FILE = f"{layout.STATE_DIR}/mcp/seen.json"
_THREAD_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.Lock] = {}
# A group may name a source of members: a reader of the app's own list, registered in this process.
_SOURCES: dict[str, tuple[Callable[..., Any], bool]] = {}
_REFRESH = object()
# A read of a source has a deadline and a greatest number of names, and within an interval after a read the
# last list (or the last failure) is used again, so a caller cannot make the owner pay for a read on every call.
SOURCE_SECONDS = 5.0
SOURCE_MAX_NAMES = 50_000
SOURCE_INTERVAL_SECONDS = 60.0
SOURCE_STATE = f"{layout.STATE_DIR}/agents/member-sources.json"
_SOURCE_GUARD = threading.Lock()
_SOURCE_CHANGED = threading.Condition(_SOURCE_GUARD)
_SOURCE_CACHE: dict[tuple[str, str, str], tuple[float, "tuple[str, ...] | MemoryForestError", list[str]]] = {}
_SOURCE_ACTIVE: dict[str, "_SourceRead"] = {}


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


def register_member_source(name: str, reader: Callable[..., Any], *, cooperative: bool = False) -> None:
    """Register a list-of-texts reader, without starting it.

    A cooperative reader takes ``(group_id, *, deadline, cancelled)``. ``deadline`` is an absolute
    monotonic instant; ``cancelled`` is a threading.Event. It must bound its own I/O and return by the
    deadline or when cancelled. Legacy ``reader(group_id)`` calls remain supported, but cannot be stopped.
    Neither registration nor replacement releases an outstanding reader's slot.
    """
    with _SOURCE_GUARD:
        _SOURCES[name] = (reader, cooperative)


def unregister_member_source(name: str) -> None:
    with _SOURCE_GUARD:
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
        edits = [{"set_value": group_id, "table": "agents.groups", "value": {"open": True}}]
        raise MemoryForestError(
            "group_unknown",
            f"There is no group `{group_id}`. The owner declares it in forest.toml. " + edit_sentences(edits),
            details={"group": group_id, "edits": edits},
            next_step="report",
        )
    return found


def resolve_caller(root: str | os.PathLike[str], group_id: str, given: str) -> Resolved:
    """The agent ``given`` names on ``group_id``. An open group may admit it once."""
    cleaned = names.check(given)
    config = load_config(root)
    group = require_group(config, group_id)
    source_names: tuple[str, ...] = ()
    decision = _interpret(root, config, group, cleaned, refreshed=False, source_names=source_names)
    if decision is _REFRESH:
        source_names = _read_source(root, group)
        decision = _interpret(root, config, group, cleaned, refreshed=True, source_names=source_names)
    if isinstance(decision, Resolved):
        return decision
    if isinstance(decision, Admit):
        if source_names:
            cleaned = next((name for name in source_names if names.key(name) == names.key(cleaned)), cleaned)
        return _admit(root, group_id, cleaned, decision, source_names)
    raise decision


def listing(root: str | os.PathLike[str]) -> dict[str, Any]:
    """Each group, its members, the sources each agent has written through, and who may be one agent.

    An agent that is not in a group is listed after the groups. ``last_seen`` is the newest tool call on a
    connection, or the first one when that is all the seen file holds, or ``""`` when the agent has not
    called. ``sources`` come from the lane writer's small record (:mod:`memory_forest.sources`).
    ``remarks`` names pairs whose keys differ by one edit or whose names differ by one added word. A remark
    is never a refusal. A group with a source of members shows the names of the last read that are not a
    name (``left_out``) and a source that failed (``source_failed``).
    """
    config = load_config(root)
    seen = _read_seen(root)
    laned = set(agents_mod.lanes(root))
    known = {item.id: item for item in config.agents.known}
    orchestrators = set(config.agents.orchestrators)
    identities = _agent_rows(root, config)
    labels = dict(identities)
    everyone = sorted(labels)
    written = sources.listed(root, everyone)
    state = _read_source_state(root)
    covered: set[str] = set()
    groups: list[dict[str, Any]] = []
    named: list[tuple[str, str]] = []
    for group in config.agents.groups:
        members = []
        for agent_id, name in _labels(config, group, identities):
            covered.add(agent_id)
            shown = name or agent_id
            named.append((agent_id, shown))
            members.append(_row(agent_id, shown, agent_id in laned, seen, written.get(agent_id, [])))
        row = {"id": group.id, "members": members, "open": group.open, "source": group.source}
        noted = state.get(group.id)
        if group.source and isinstance(noted, dict) and noted.get("source") == group.source:
            row["left_out"] = [str(item) for item in noted.get("left_out") or []]
            if noted.get("failed"):
                row["source_failed"] = str(noted["failed"])
        groups.append(row)
    others: list[dict[str, Any]] = []
    for agent_id in everyone:
        if agent_id in covered:
            continue
        item = known.get(agent_id)
        name = item.name if item is not None and item.name else labels.get(agent_id) or agent_id
        named.append((agent_id, name))
        row = _row(agent_id, name, agent_id in laned, seen, written.get(agent_id, []))
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
        if group.get("source_failed"):
            lines.append(f"  source {group.get('source')} failed: {group['source_failed']}")
        for left in group.get("left_out") or []:
            lines.append(f"  left out of source {group.get('source')}: {left}")
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
    written = member.get("sources") or []
    if written:
        text += " — " + ", ".join(written)
    return text


def _row(agent_id: str, name: str, lane: bool, seen: Mapping[str, Any], written: list[str]) -> dict[str, Any]:
    record = seen.get(agent_id)
    last = ""
    if isinstance(record, dict):
        last = str(record.get("last") or record.get("at") or "")
    return {"id": agent_id, "lane": lane, "last_seen": last, "name": name, "sources": list(written)}


def _read_seen(root: str | os.PathLike[str]) -> dict[str, Any]:
    path = layout.resolve(root, SEEN_FILE)
    try:
        loaded = json.loads(system.read_text(path))
    except (system.Absent, system.Link, system.NotAFile, UnicodeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


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
            left_key = names.key(left_name or left_id)
            right_key = names.key(right_name or right_id)
            if not left_key or not right_key or left_key == right_key:
                continue
            if _one_edit(left_key, right_key):
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


def _labels(config: Config, group: AgentGroup, identities: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Resolve original member references against the same forest-wide identity set as the caller."""
    labels = dict(identities)
    members: dict[str, str] = {}
    for reference in group.member_refs or group.members:
        hits = names.find(names.check(reference), identities)
        if len(hits) == 1 and hits[0] != group.id:
            agent_id = hits[0]
            members.setdefault(agent_id, labels.get(agent_id) or _name_of(config, agent_id))
    return list(members.items())


def _name_of(config: Config, agent_id: str) -> str:
    known = config.agents.declared(agent_id)
    if known is not None and known.name:
        return known.name
    return ""


def _member_rows(rows: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"id": agent_id, "name": name or agent_id} for agent_id, name in rows]


def _agent_rows(root: str | os.PathLike[str], config: Config) -> list[tuple[str, str]]:
    """Every agent the forest already has: its id and its stored name (the settings', else the registry's).

    The settings' names come first, and a name once set is not replaced, so the settings' name wins."""
    rows: dict[str, str] = {}

    def add(agent_id: str, name: str) -> None:
        if not agents_mod.file_id_ok(agent_id):
            return
        current = rows.get(agent_id)
        if current is None:
            rows[agent_id] = name or ""
        elif not current and name:
            rows[agent_id] = name

    # Identity is resolved over this complete set before the group's membership is considered.
    for agent_id in config.agents.orchestrators:
        add(agent_id, _name_of(config, agent_id))
    for known in config.agents.known:
        add(known.id, known.name)
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
    return list(rows.items())


def agent_names(root: str | os.PathLike[str], config: Config) -> list[tuple[str, str]]:
    """Every name the forest holds for each agent: the rows of ``_agent_rows`` and, after them, the name of
    an agent's tree where the tree is named otherwise.

    An agent's name lives in two places: its settings entry, written when it joined, and its tree, which
    ``forest move rename`` changes. Either one names the same agent, so finding an agent by a name looks at
    both (``names.find`` gives each id once), and ``dict()`` of these rows gives the name the tree carries
    now. Admitting a new agent by a name another agent's tree already carries would make a second identity
    whose tree can never be made."""
    rows = _agent_rows(root, config)
    compiled = _compiled(root, config)
    if compiled is None:
        return rows
    first = dict(rows)
    later: list[tuple[str, str]] = []
    for agent_id in compiled.agents:
        name = str((compiled.subject(agent_id) or {}).get("name") or "")
        if name and agent_id in first and names.key(name) != names.key(first[agent_id] or agent_id):
            later.append((agent_id, name))
    return rows + later


def _compiled(root: str | os.PathLike[str], config: Config) -> registry.Registry | None:
    try:
        return registry.build(root, config=config)
    except (MemoryForestError, OSError):
        return None


def _on_source(given: str, source_names: tuple[str, ...],
               identities: list[tuple[str, str]] | None = None) -> bool:
    """Membership uses forest-wide identity for existing references, then keys for new names."""
    if identities is None:
        return any(names.key(name) == names.key(given) for name in source_names)
    chosen = names.find(given, identities)
    for name in source_names:
        source = names.find(name, identities)
        if chosen and source == chosen:
            return True
        if not chosen and not source and names.key(given) == names.key(name):
            return True
    return False


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
        f"The name is ambiguous among agents of this forest on group `{group.id}`. Pass an agent's exact stored id.",
        details={"members": [{"id": agent_id, "name": labels.get(agent_id) or agent_id} for agent_id in ids]},
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
    everyone = _agent_rows(root, config)
    rows = _labels(config, group, everyone)
    members = {agent_id for agent_id, _name in rows}
    existing = names.find(given, everyone)
    if len(existing) > 1:
        return _ambiguous(group, everyone, existing)
    if existing and (existing[0] in config.agents.orchestrators or
                     (existing[0] in members and not group.source)):
        return Resolved(existing[0])
    if not refreshed and group.source:
        return _REFRESH
    # A source of members is the list. Open, with no source, lets a new name in
    # and lets an agent that already exists join.
    listed = _on_source(existing[0] if existing else given, source_names, everyone)
    allowed = listed or (group.open and not group.source)
    if existing and allowed and existing[0] in members:
        return Resolved(existing[0])
    if len(existing) == 1 and allowed:
        return Admit(existing[0], create=False)
    if allowed and not existing:
        return _plan_new(root, config, group, given)
    return _not_in_group(group, rows, given)


def _group_full(group: AgentGroup) -> MemoryForestError:
    edits = [{"set_value": "max_members", "table": f"agents.groups.{group.id}",
              "value": len(group.members) + 1}]
    return MemoryForestError(
        "group_full",
        f"`{group.id}` already has its max_members ({group.max_members}). "
        "The owner raises max_members in forest.toml. " + edit_sentences(edits),
        details={"group": group.id, "max_members": group.max_members, "edits": edits},
        next_step="report",
    )


def _plan_new(root: str | os.PathLike[str], config: Config, group: AgentGroup, given: str) -> Admit:
    """A new agent: its id is made once from the name, and never an id something already holds. A full group
    is refused by ``_admit``, under the settings lock, for a new agent and an existing one alike."""
    return Admit(agents_mod.new_id(given, agents_mod.taken(root, config)), create=True)


# ─────────────────────────────── a source of members ───────────────────────────────


def _source_failed(group: AgentGroup, why: str) -> MemoryForestError:
    return MemoryForestError(
        "member_source_failed",
        f"The source of members `{group.source}` of the group `{group.id}` {why}, so it is not known whether "
        "this name is a member. The owner looks at that source; nothing was written.",
        details={"group": group.id, "source": group.source},
        next_step="report",
    )


@dataclass(slots=True)
class _SourceRead:
    key: tuple[str, str, str]
    group: AgentGroup
    deadline: float
    clock: Callable[[], float]
    started: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    cancelled: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    result: tuple[str, ...] | MemoryForestError = ()
    left: list[str] = field(default_factory=list)
    abandoned: bool = False


def _call_source(group: AgentGroup, items: Any) -> tuple[tuple[str, ...], list[str]]:
    """Validate before copying: at most 50,000 texts of at most 200 Unicode characters each.

    This bounds accepted UTF-8 name data to 40,000,000 bytes. A reader owns its allocations and must
    apply these bounds while reading, before returning its list. Invalid names and duplicate keys are
    reported; duplicate entries keep the first display spelling.
    """
    if not isinstance(items, list):
        raise _source_failed(group, "returned a malformed result: expected a list of texts")
    if len(items) > SOURCE_MAX_NAMES:
        raise _source_failed(group, f"lists more than {SOURCE_MAX_NAMES} names")
    kept: dict[str, str] = {}
    left: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise _source_failed(group, "returned a malformed result: every entry must be text")
        if len(item) > names.NAME_MAX:
            raise _source_failed(group, f"lists a name longer than {names.NAME_MAX} characters")
        if names.bad_reason(item) is not None:
            left.append(repr(item[:80]))
            continue
        name = names.check(item)
        key = names.key(name)
        if key in kept:
            left.append(f"duplicate {name!r}; first spelling {kept[key]!r}")
            continue
        kept[key] = name
    return tuple(kept.values()), left


def _source_work(read: _SourceRead, registered: tuple[Callable[..., Any], bool] | None) -> None:
    """Publish one result even when the reader raises; a late result never grants membership."""
    result: tuple[str, ...] | MemoryForestError
    left: list[str] = []
    if registered is None:
        result = _source_failed(read.group, "has no reader in this process")
    else:
        reader, cooperative = registered
        try:
            items = (reader(read.group.id, deadline=read.deadline, cancelled=read.cancelled)
                     if cooperative else reader(read.group.id))
        except BaseException as exc:  # noqa: BLE001 - a reader's fault is the source's failure
            result = _source_failed(read.group, f"failed ({type(exc).__name__})")
        else:
            try:
                result, left = _call_source(read.group, items)
            except MemoryForestError as exc:
                result = exc
            except BaseException as exc:  # noqa: BLE001 - malformed reader objects must release the slot too
                result = _source_failed(read.group, f"failed while validating its result ({type(exc).__name__})")
    with _SOURCE_CHANGED:
        completed = read.clock()
        if read.abandoned or completed >= read.deadline:
            result = _source_failed(read.group, "did not answer by its deadline")
            left = []
            read.cancelled.set()
        read.result, read.left = result, left
        _SOURCE_CACHE[read.key] = (completed, result, left)
        _SOURCE_ACTIVE.pop(read.group.source, None)
        read.done.set()
        _SOURCE_CHANGED.notify_all()


def _abandoned_source(group: AgentGroup, read: _SourceRead) -> MemoryForestError:
    return _source_failed(
        group, f"has an earlier read for group `{read.group.id}` started at {read.started} that has not come back; "
        "no other reader will start until it returns, or the answering process is restarted",
    )


def _read_source(
    root: str | os.PathLike[str], group: AgentGroup, *, clock: Callable[[], float] = time.monotonic,
    deadline: float | None = None,
) -> tuple[str, ...]:
    """Share a refresh per forest/group/source, inside each caller's own absolute deadline.

    Readers of different groups use the same source serially. A source that ignores cancellation keeps
    its sole process slot; later calls fail immediately. The interval begins when reader work ends.
    """
    started = clock()
    deadline = min(deadline, started + SOURCE_SECONDS) if deadline is not None else started + SOURCE_SECONDS
    cache_key = (str(system.absolute(root)), group.id, group.source)
    with _SOURCE_CHANGED:
        while True:
            active = _SOURCE_ACTIVE.get(group.source)
            if active is not None and active.abandoned:
                raise _abandoned_source(group, active)
            cached = _SOURCE_CACHE.get(cache_key)
            if cached is not None and 0 <= clock() - cached[0] < SOURCE_INTERVAL_SECONDS:
                result = cached[1]
                if isinstance(result, MemoryForestError):
                    raise result
                return result
            if active is not None and active.key == cache_key:
                read = active
                break
            remaining = deadline - clock()
            if remaining <= 0:
                raise _source_failed(group, "could not start a read before this caller's deadline")
            if active is not None:
                _SOURCE_CHANGED.wait(remaining)
                continue
            read = _SourceRead(cache_key, group, deadline, clock)
            _SOURCE_ACTIVE[group.source] = read
            registered = _SOURCES.get(group.source)
            worker = threading.Thread(target=_source_work, args=(read, registered),
                                      name=f"member-source-{group.source}", daemon=True)
            try:
                worker.start()
            except Exception as exc:
                _SOURCE_ACTIVE.pop(group.source, None)
                read.result = _source_failed(group, f"could not start its reader ({type(exc).__name__})")
                _SOURCE_CACHE[cache_key] = (clock(), read.result, [])
                read.done.set()
                _SOURCE_CHANGED.notify_all()
            break
    finished = read.done.wait(max(0, min(deadline, read.deadline) - clock()))
    with _SOURCE_CHANGED:
        if not finished and not read.done.is_set():
            if deadline < read.deadline:
                raise _source_failed(group, "did not answer before this caller's deadline")
            read.abandoned = True
            read.cancelled.set()
            read.result = _source_failed(group, "did not answer by its deadline")
            read.done.set()
            _SOURCE_CHANGED.notify_all()
        result, left = read.result, read.left
    _note_source(root, group, left, result)
    if isinstance(result, MemoryForestError):
        raise result
    return result


def source_problems(root: str | os.PathLike[str], config: Config) -> list[tuple[str, str]]:
    """Group and diagnostic for ``forest check``; inspection never starts a reader.

    Persisted failures describe the last observed read. A live abandoned read is reported from process
    state even when the diagnostic file could not be written.
    """
    state = _read_source_state(root)
    found: list[tuple[str, str]] = []
    with _SOURCE_GUARD:
        for group in config.agents.groups:
            if not group.source:
                continue
            active = _SOURCE_ACTIVE.get(group.source)
            if active is not None and active.abandoned:
                found.append((group.id, _abandoned_source(group, active).message))
                continue
            entry = state.get(group.id)
            if isinstance(entry, dict) and entry.get("source") == group.source and entry.get("failed"):
                found.append((group.id, "Last member-source read: " + str(entry["failed"])))
    return found


def _read_source_state(root: str | os.PathLike[str]) -> dict[str, Any]:
    try:
        loaded = json.loads(system.read_text(layout.resolve(root, SOURCE_STATE)))
    except (system.Absent, system.Link, system.NotAFile, UnicodeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _note_source(
    root: str | os.PathLike[str], group: AgentGroup, left: list[str], result: "tuple[str, ...] | MemoryForestError"
) -> None:
    """What ``forest agents`` shows of the last read: the names left out, and a failure. Never fails the call."""
    try:
        layout.ensure_folder(root, f"{layout.STATE_DIR}/agents")
        with exclusive(root, "member-sources", wait_seconds=0, label="member sources"):
            state = _read_source_state(root)
            entry: dict[str, Any] = {"left_out": left, "source": group.source}
            if isinstance(result, MemoryForestError):
                entry["failed"] = result.message
            if state.get(group.id) == entry:
                return
            state[group.id] = entry
            atomic_write(layout.resolve(root, SOURCE_STATE), json.dumps(state, ensure_ascii=False, sort_keys=True) + "\n")
    except (MemoryForestError, system.Outcome):
        return


# ─────────────────────────────── admitting ───────────────────────────────


@contextmanager
def _admission_lock(
    root: str | os.PathLike[str], group_id: str, given: str, admit: Admit
) -> Iterator[None]:
    """Allocate against the same coherent namespace that tree materialization writes.

    Even when a lock cannot be made or acquired, the owner gets semantic edits from the readable settings.
    Only entry failures become settings refusals; a refusal inside the protected body keeps its meaning.
    """
    with ExitStack() as stack:
        try:
            stack.enter_context(exclusive(root, "agents-group", wait_seconds=30, label="group member"))
            stack.enter_context(writer_lock(root, wait_seconds=30, label="group member allocation"))
        except (MemoryForestError, system.Outcome) as exc:
            config = load_config(root)
            group = require_group(config, group_id)
            identities = _agent_rows(root, config)
            group = replace(group, members=tuple(agent_id for agent_id, _name in _labels(config, group, identities)))
            stored_name = dict(identities).get(admit.id) or given
            edits = _edits(group, admit.id, stored_name,
                           write_known=config.agents.declared(admit.id) is None,
                           raw=_settings_data(root))
            raise _unpatched(group_id, admit.id, edits) from exc
        yield


def _settings_data(root: str | os.PathLike[str]) -> Mapping[str, Any] | None:
    """The original TOML kinds for recovery edits, when the file can still be read."""
    try:
        return tomllib.loads(system.read_bytes(config_path(root)).decode("utf-8-sig"))
    except (system.Outcome, UnicodeError, tomllib.TOMLDecodeError):
        pass


def _admit(
    root: str | os.PathLike[str],
    group_id: str,
    given: str,
    admit: Admit,
    source_names: tuple[str, ...],
) -> Resolved:
    """Add the agent to the group once. A second caller finds the member and writes nothing."""
    with _admission_lock(root, group_id, given, admit):
        config = load_config(root)
        group = require_group(config, group_id)
        decision = _interpret(root, config, group, given, refreshed=True, source_names=source_names)
        if isinstance(decision, Resolved):
            return Resolved(decision.id, wrote=False)
        if isinstance(decision, MemoryForestError):
            raise decision
        assert isinstance(decision, Admit)
        identities = _agent_rows(root, config)
        group = replace(group, members=tuple(agent_id for agent_id, _name in _labels(config, group, identities)))
        if group.max_members is not None and decision.id not in group.members and len(group.members) >= group.max_members:
            raise _group_full(group)
        # A new name is stored once; an existing identity keeps its stored display name.
        write_known = config.agents.declared(decision.id) is None
        stored_name = dict(identities).get(decision.id) or given
        edits = _edits(group, decision.id, stored_name, write_known=write_known, raw=_settings_data(root))
        path = config_path(root)
        if _settings_unwritable(path):
            raise _unwritable(group_id, decision.id, edits)
        try:
            data = system.read_bytes(path)
        except system.Outcome as exc:
            raise _unwritable(group_id, decision.id, edits) from exc
        marked = data.startswith(BOM)
        try:
            original = (data[len(BOM):] if marked else data).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _unpatched(group_id, decision.id, edits) from exc
        try:
            updated = _patch(original, group, decision.id, stored_name, write_known=write_known)
        except _Unpatchable as exc:
            raise _unpatched(group_id, decision.id, edits) from exc
        try:
            parsed = parse_config(updated, source=config.source)
        except MemoryForestError as exc:
            # The owner's file parsed before the patch: a file that does not parse after it is the patch's fault.
            raise _unpatched(group_id, decision.id, edits) from exc
        if not _means(config, parsed, group_id, decision.id, stored_name, write_known=write_known, identities=identities):
            raise _unpatched(group_id, decision.id, edits)
        try:
            atomic_write(path, (BOM if marked else b"") + updated.encode("utf-8"))
        except system.Outcome as exc:
            raise _unwritable(group_id, decision.id, edits) from exc
        return Resolved(decision.id, wrote=True)


def _means(before: Config, after: Config, group_id: str, member_id: str, name: str, *, write_known: bool,
           identities: list[tuple[str, str]]) -> bool:
    """Whether the patched settings are the settings before, with the member added and its name, if new."""
    old_group = before.agents.group(group_id)
    new_group = after.agents.group(group_id)
    if old_group is None or new_group is None:
        return False
    old_members = tuple(agent_id for agent_id, _name in _labels(before, old_group, identities))
    current = dict(identities)
    current.update((known.id, known.name) for known in after.agents.known)
    new_members = tuple(agent_id for agent_id, _name in _labels(after, new_group, list(current.items())))
    wanted = old_members if member_id in old_members else (*old_members, member_id)
    if new_members != wanted:
        return False
    if write_known:
        declared = after.agents.declared(member_id)
        if declared is None or declared.name != name:
            return False
        wanted_known = tuple(sorted(
            [*(item for item in before.agents.known if item.id != member_id), declared], key=lambda item: item.id))
        if after.agents.known != wanted_known:
            return False
    elif after.agents.known != before.agents.known:
        return False
    groups = tuple(new_group if item.id == group_id else item for item in before.agents.groups)
    if after.agents.groups != groups:
        return False
    rest = replace(after, agents=replace(after.agents, groups=before.agents.groups, known=before.agents.known))
    return rest == before


def _settings_unwritable(path: os.PathLike[str]) -> bool:
    target = Path(path)
    if not system.may(target.parent, "write"):
        return True
    return system.look_or_none(target) is not None and not system.may(target, "change")


def _edits(
    group: AgentGroup, member_id: str, name: str, *, write_known: bool, raw: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    """The edits the owner makes when the settings cannot be written or patched safely, as data."""
    edits: list[dict[str, Any]] = []
    if member_id not in group.members:
        table = None if raw is None else raw.get("agents", {}).get("groups", {}).get(group.id, {})
        if (table is None and group.members) or (table is not None and isinstance(table.get("members"), list)):
            if write_known and table is not None and member_id in table["members"]:
                # The new exact id would reinterpret a reference that previously named another agent.
                edits.append({"set_value": "members", "table": f"agents.groups.{group.id}",
                              "value": [*group.members, member_id], "previous": repr(table["members"])})
            else:
                edits.append({"add_to_list": "members", "table": f"agents.groups.{group.id}", "value": member_id})
        else:
            old = None if table is None else table.get("members")
            kept = [old] if isinstance(old, str) and names.bad_reason(old) is None else []
            if member_id not in kept:
                kept.append(member_id)
            edit: dict[str, Any] = {"set_value": "members", "table": f"agents.groups.{group.id}", "value": kept}
            if table is not None and "members" in table:
                edit["previous"] = repr(old)
            edits.append(edit)
    if write_known:
        known = {} if raw is None else raw.get("agents", {}).get("known", {})
        if not isinstance(known, Mapping):
            edits.append({"set_value": "known", "table": "agents", "value": {member_id: {"name": name}},
                          "previous": repr(known)})
        elif member_id in known:
            edits.append({"set_value": member_id, "table": "agents.known", "value": {"name": name},
                          "previous": repr(known[member_id])})
        else:
            edits.append({"new_table": f"agents.known.{member_id}", "values": {"name": name}})
    return edits


def edit_sentences(edits: list[dict[str, Any]]) -> str:
    """The edits in words: which value goes into which existing list of which table, and which table is new."""
    sentences = []
    for edit in edits:
        if "add_to_list" in edit:
            sentences.append(
                f"Add {_toml_value(edit['value'])} to the `{edit['add_to_list']}` list of the table "
                f"[{edit['table']}] that forest.toml already has."
            )
        elif "set_value" in edit:
            previous = f" It replaces {edit['previous']}." if "previous" in edit else ""
            sentences.append(
                f"Set `{edit['set_value']}` in [{edit['table']}] to {_edit_value(edit['value'])}." + previous
            )
        else:
            values = ", ".join(f"{key} = {_toml_value(value)}" for key, value in edit["values"].items())
            sentences.append(f"Add a new table [{edit['new_table']}] with {values}.")
    return " ".join(sentences)


def _edit_value(value: Any) -> str:
    """TOML value syntax for a semantic edit, including a list or an inline table."""
    if isinstance(value, list):
        return "[" + ", ".join(_edit_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_toml_value(key)} = {_edit_value(item)}" for key, item in value.items()) + " }"
    return _toml_value(value)


def _unwritable(group_id: str, member_id: str, edits: list[dict[str, Any]]) -> MemoryForestError:
    return MemoryForestError(
        "settings_unwritable",
        "forest.toml could not be written, so nothing was written and the call did not run. The owner makes "
        "these edits: " + edit_sentences(edits),
        details={"agent": member_id, "edits": edits, "group": group_id},
        next_step="report",
    )


def _unpatched(group_id: str, member_id: str, edits: list[dict[str, Any]]) -> MemoryForestError:
    return MemoryForestError(
        "settings_unpatched",
        "forest.toml could not be safely patched or its settings lock could not be acquired, so nothing "
        "was written and the call did not run. The owner makes these edits: " + edit_sentences(edits),
        details={"agent": member_id, "edits": edits, "group": group_id},
        next_step="report",
    )


class _Unpatchable(Exception):
    """The group is written in a form the patch does not change; the owner gets the edits instead."""


def _lines(text: str) -> list[str]:
    """The lines of ``text``, each with its own ending (``\\n`` or ``\\r\\n``) as found; the last may have none."""
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _ending(line: str) -> str:
    return "\r\n" if line.endswith("\r\n") else "\n"


def _file_ending(text: str) -> str:
    """The ending of the file's last line that has one, else ``\\n``."""
    for line in reversed(_lines(text)):
        if line.endswith("\n"):
            return _ending(line)
    return "\n"


def _patch(text: str, group: AgentGroup, member_id: str, name: str, *, write_known: bool) -> str:
    """The settings text with one member added, and its known table when it is new.

    Every byte of the text stays, in order; what is added is one run at each place the patch owns: inside the
    group's ``members`` list (or one line after the group's header when it has none), and one table at the
    end. A group written inline, with dotted keys or under its parent table is not changed (``_Unpatchable``).
    """
    if member_id not in group.members:
        span = _table_span(text, f"agents.groups.{group.id}")
        if span is None:
            raise _Unpatchable(group.id)
        start, end = span
        text = text[:start] + _extend_members(text[start:end], member_id) + text[end:]
    if write_known and _table_span(text, f"agents.known.{member_id}") is None:
        newline = _file_ending(text)
        table = f"[agents.known.{member_id}]{newline}name = {_toml_value(name)}"
        if text.endswith("\n"):
            text += f"{newline}{table}{newline}"
        else:
            text += f"{newline}{newline}{table}"
    return text


def _extend_members(block: str, member_id: str) -> str:
    token = _toml_value(member_id)
    key_at = _find_key(block, "members")
    if key_at is None:
        # A group that admits has a key (``open`` or ``source``), so its header line ends in a line ending.
        header = _lines(block)[0]
        return header + f"members = [{token}]{_ending(header)}" + block[len(header):]
    value_at = _value_at(block, key_at)
    if value_at is None or block[value_at] != "[":
        raise _Unpatchable("members")
    end = _array_end(block, value_at)
    if end is None:
        raise _Unpatchable("members")
    body = block[value_at + 1 : end]
    last = _last_code(body)
    line_start = block.rfind("\n", 0, end) + 1
    before_bracket = block[line_start:end]
    # The closing bracket on a line of its own: the new member is a line of its own above it, indented as the
    # items are. Otherwise (one line, or items before the bracket) it follows the last item on its line.
    if last in {"", ","} and not before_bracket.strip():
        newline = _ending(block[:line_start])
        return block[:line_start] + f"{_item_indent(body)}{token},{newline}" + block[line_start:]
    if last in {"", ","}:
        return block[:end] + (" " if last == "," else "") + token + block[end:]
    return block[:end] + ", " + token + block[end:]


def _find_key(block: str, key: str) -> int | None:
    """Index of ``key`` where a line of the table starts with it (bare or quoted) and an ``=``.

    The file parsed as TOML, so no line inside an array starts with a key and an ``=``; a line of a multi-line
    string that looks like the key is caught by the check of the patched file's meaning (``_means``).
    """
    offset = 0
    for line in _lines(block):
        stripped = line.lstrip(" \t")
        at = offset + len(line) - len(stripped)
        for written in (key, f'"{key}"', f"'{key}'"):
            if stripped.startswith(written):
                after = stripped[len(written):].lstrip(" \t")
                if after.startswith("="):
                    return at
        offset += len(line)
    return None


def _offsets(block: str) -> list[tuple[int, str]]:
    """Each line of ``block`` that is not inside a multi-line value, with its offset."""
    found: list[tuple[int, str]] = []
    offset = 0
    depth = 0
    for line in _lines(block):
        if depth == 0:
            found.append((offset, line))
        depth = max(0, depth + _bracket_change(line))
        offset += len(line)
    return found


def _bracket_change(line: str) -> int:
    """How much a line opens or closes square brackets outside strings and comments."""
    change = 0
    quote = ""
    escape = False
    for character in line:
        if quote:
            if escape:
                escape = False
            elif character == "\\" and quote == '"':
                escape = True
            elif character == quote:
                quote = ""
            continue
        if character == "#":
            break
        if character in "\"'":
            quote = character
        elif character == "[":
            change += 1
        elif character == "]":
            change -= 1
    return change


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
            elif character == "\\" and quote == '"':
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
            elif character == "\\" and quote == '"':
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
    """The start and end of the table ``key``, declared by its own header. A header in a comment is not one."""
    headers: list[tuple[int, str]] = []
    for offset, line in _offsets(text):
        found = _header_key(line)
        if found is not None:
            headers.append((offset, found))
    for index, (start, name) in enumerate(headers):
        if name != key:
            continue
        end = headers[index + 1][0] if index + 1 < len(headers) else len(text)
        return start, end
    return None
