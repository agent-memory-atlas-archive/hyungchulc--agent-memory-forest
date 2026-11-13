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
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from . import agents as agents_mod
from . import layout, names, registry, sources, system
from .config import BOM, AgentGroup, Config, _toml_value, config_path, load_config, parse_config
from .errors import MemoryForestError
from .locking import atomic_write, hold_lock

# The same file ``mcp`` writes. Kept here so this module does not import the server.
SEEN_FILE = f"{layout.STATE_DIR}/mcp/seen.json"
_THREAD_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.Lock] = {}
# A group may name a source of members: a reader of the app's own list, registered in this process.
_SOURCES: dict[str, Callable[[str], Iterable[str]]] = {}
_REFRESH = object()
# A read of a source has a deadline and a greatest number of names, and within an interval after a read the
# last list (or the last failure) is used again, so a caller cannot make the owner pay for a read on every call.
SOURCE_SECONDS = 5.0
SOURCE_MAX_NAMES = 50_000
SOURCE_INTERVAL_SECONDS = 60.0
SOURCE_STATE = f"{layout.STATE_DIR}/agents/member-sources.json"
_SOURCE_GUARD = threading.Lock()
_SOURCE_CACHE: dict[tuple[str, str, str], tuple[float, "tuple[str, ...] | MemoryForestError"]] = {}
_clock: Callable[[], float] = time.monotonic


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
    everyone = sorted({*known, *orchestrators, *(lane for lane in laned if layout.is_slug(lane)),
                       *(member for group in config.agents.groups for member in group.members)})
    written = sources.listed(root, everyone)
    state = _read_source_state(root)
    covered: set[str] = set()
    groups: list[dict[str, Any]] = []
    named: list[tuple[str, str]] = []
    for group in config.agents.groups:
        members = []
        for agent_id, name in _labels(config, group):
            covered.add(agent_id)
            shown = name or agent_id
            named.append((agent_id, shown))
            members.append(_row(agent_id, shown, agent_id in laned, seen, written.get(agent_id, [])))
        row = {"id": group.id, "members": members, "open": group.open, "source": group.source}
        noted = state.get(group.id)
        if group.source and isinstance(noted, dict):
            row["left_out"] = [str(item) for item in noted.get("left_out") or []]
            if noted.get("failed"):
                row["source_failed"] = str(noted["failed"])
        groups.append(row)
    others: list[dict[str, Any]] = []
    for agent_id in everyone:
        if agent_id in covered:
            continue
        item = known.get(agent_id)
        name = item.name if item is not None and item.name else agent_id
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
            if not left_key or left_key == right_key:
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


def _labels(config: Config, group: AgentGroup) -> list[tuple[str, str]]:
    return [(member_id, _name_of(config, member_id)) for member_id in group.members]


def _name_of(config: Config, agent_id: str) -> str:
    known = config.agents.declared(agent_id)
    if known is not None and known.name:
        return known.name
    return ""


def _member_rows(rows: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"id": agent_id, "name": name or agent_id} for agent_id, name in rows]


def _agent_rows(root: str | os.PathLike[str], config: Config) -> list[tuple[str, str]]:
    """Every agent the forest already has: its id and its stored name (the settings', else the registry's)."""
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
    return [(agent_id, _name_of(config, agent_id)) for agent_id in config.agents.orchestrators]


def _on_source(given: str, source_names: tuple[str, ...]) -> bool:
    return len(names.find(given, [(name, name) for name in source_names])) == 1


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
    hits = names.find(given, rows)
    if len(hits) > 1:
        return _ambiguous(group, rows, hits)
    if len(hits) == 1:
        return Resolved(hits[0])
    orchestrators = names.find(given, _orchestrator_rows(config))
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
    everyone = _agent_rows(root, config)
    existing = names.find(given, everyone)
    if len(existing) > 1:
        return _ambiguous(group, everyone, existing)
    if len(existing) == 1 and allowed:
        return Admit(existing[0], create=False)
    if len(existing) == 1:
        return _not_in_group(group, rows, given)
    if allowed:
        return _plan_new(root, config, group, given)
    return _not_in_group(group, rows, given)


def _group_full(group: AgentGroup) -> MemoryForestError:
    return MemoryForestError(
        "group_full",
        f"`{group.id}` already has its max_members ({group.max_members}). "
        "The owner raises max_members in forest.toml, or leaves it unset.",
        details={"group": group.id, "max_members": group.max_members},
        next_step="report",
    )


def _plan_new(root: str | os.PathLike[str], config: Config, group: AgentGroup, given: str) -> Admit | MemoryForestError:
    """A new agent: its id is made once from the name, and never an id something already holds."""
    if group.max_members is not None and len(group.members) >= group.max_members:
        return _group_full(group)
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


def _call_source(group: AgentGroup) -> tuple[tuple[str, ...], list[str]]:
    """The names a source lists, and what it lists that is not a name. A bound or a fault is the failure."""
    reader = _SOURCES.get(group.source)
    if reader is None:
        raise _source_failed(group, "has no reader in this process")
    box: dict[str, Any] = {}
    done = threading.Event()

    def work() -> None:
        try:
            items: list[Any] = []
            for item in reader(group.id):
                items.append(item)
                if len(items) > SOURCE_MAX_NAMES:
                    box["too_many"] = True
                    break
            box["items"] = items
        except BaseException as exc:  # noqa: BLE001 - a reader's fault of any kind is the source's failure
            box["error"] = type(exc).__name__
        finally:
            done.set()

    threading.Thread(target=work, name=f"member-source-{group.id}", daemon=True).start()
    if not done.wait(SOURCE_SECONDS):
        raise _source_failed(group, f"did not answer within {SOURCE_SECONDS:g} seconds")
    if "error" in box:
        raise _source_failed(group, f"failed ({box['error']})")
    if box.get("too_many"):
        raise _source_failed(group, f"lists more than {SOURCE_MAX_NAMES} names")
    kept: list[str] = []
    left: list[str] = []
    for item in box.get("items") or []:
        if names.bad_reason(item) is None:
            kept.append(str(item).strip())
        else:
            left.append(repr(item)[:80])
    return tuple(kept), left


def _read_source(root: str | os.PathLike[str], group: AgentGroup) -> tuple[str, ...]:
    """The group's source of members, read at most once per :data:`SOURCE_INTERVAL_SECONDS`."""
    cache_key = (str(system.absolute(root)), group.id, group.source)
    now = _clock()
    with _SOURCE_GUARD:
        cached = _SOURCE_CACHE.get(cache_key)
    if cached is not None and 0 <= now - cached[0] < SOURCE_INTERVAL_SECONDS:
        result = cached[1]
    else:
        left: list[str] = []
        try:
            result, left = _call_source(group)
        except MemoryForestError as exc:
            result = exc
        with _SOURCE_GUARD:
            _SOURCE_CACHE[cache_key] = (now, result)
        _note_source(root, group, left, result)
    if isinstance(result, MemoryForestError):
        raise result
    return result


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


def _admit(
    root: str | os.PathLike[str],
    group_id: str,
    given: str,
    admit: Admit,
    source_names: tuple[str, ...],
) -> Resolved:
    """Add the agent to the group once. A second caller finds the member and writes nothing."""
    with exclusive(root, "agents-group", wait_seconds=30, label="group member"):
        config = load_config(root)
        group = require_group(config, group_id)
        decision = _interpret(root, config, group, given, refreshed=True, source_names=source_names)
        if isinstance(decision, Resolved):
            return Resolved(decision.id, wrote=False)
        if isinstance(decision, MemoryForestError):
            raise decision
        assert isinstance(decision, Admit)
        if group.max_members is not None and decision.id not in group.members and len(group.members) >= group.max_members:
            raise _group_full(group)
        known = config.agents.declared(decision.id)
        write_known = decision.create or known is None
        stored_name = given if write_known else (known.name if known is not None else given)
        edits = _edits(group, decision.id, stored_name, write_known=write_known)
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
        if not _means(config, parsed, group_id, decision.id, stored_name, write_known=write_known):
            raise _unpatched(group_id, decision.id, edits)
        if updated == original:
            return Resolved(decision.id, wrote=False)
        try:
            atomic_write(path, (BOM if marked else b"") + updated.encode("utf-8"))
        except system.Outcome as exc:
            raise _unwritable(group_id, decision.id, edits) from exc
        return Resolved(decision.id, wrote=True)


def _means(before: Config, after: Config, group_id: str, member_id: str, name: str, *, write_known: bool) -> bool:
    """Whether the patched settings are the settings before, with the member added and its name, if new."""
    old_group = before.agents.group(group_id)
    new_group = after.agents.group(group_id)
    if old_group is None or new_group is None:
        return False
    wanted = old_group.members if member_id in old_group.members else (*old_group.members, member_id)
    if new_group.members != wanted:
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


def _edits(group: AgentGroup, member_id: str, name: str, *, write_known: bool) -> list[dict[str, Any]]:
    """The edits the owner makes when the settings cannot be written or patched safely, as data."""
    edits: list[dict[str, Any]] = []
    if member_id not in group.members:
        edits.append({"add_to_list": "members", "table": f"agents.groups.{group.id}", "value": member_id})
    if write_known:
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
        else:
            values = ", ".join(f"{key} = {_toml_value(value)}" for key, value in edit["values"].items())
            sentences.append(f"Add a new table [{edit['new_table']}] with {values}.")
    return " ".join(sentences)


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
        f"forest.toml writes [agents.groups.{group_id}] in a form this patch does not change safely, so nothing "
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
        lines = _lines(block)
        header = lines[0]
        if header.endswith("\n"):
            return header + f"members = [{token}]{_ending(header)}" + block[len(header):]
        return block + f"{_file_ending(block)}members = [{token}]"
    value_at = _value_at(block, key_at)
    if value_at is None or block[value_at] != "[":
        raise _Unpatchable("members")
    end = _array_end(block, value_at)
    if end is None:
        raise _Unpatchable("members")
    body = block[value_at + 1 : end]
    last = _last_code(body)
    if "\n" not in body:
        if last == "":
            addition = token
        elif last == ",":
            addition = " " + token
        else:
            addition = ", " + token
        return block[:end] + addition + block[end:]
    line_start = block.rfind("\n", 0, end) + 1
    before_bracket = block[line_start:end]
    if last in {"", ","} and not before_bracket.strip():
        newline = _ending(block[:line_start])
        return block[:line_start] + f"{_item_indent(body)}{token},{newline}" + block[line_start:]
    if last in {"", ","}:
        return block[:end] + (" " if last == "," else "") + token + block[end:]
    return block[:end] + ", " + token + block[end:]


def _find_key(block: str, key: str) -> int | None:
    """Index of ``key`` where it is a key of the table (bare or quoted), not a word in a string or a comment."""
    for offset, line in _offsets(block):
        stripped = line.lstrip(" \t")
        at = offset + len(line) - len(stripped)
        for written in (key, f'"{key}"', f"'{key}'"):
            if stripped.startswith(written):
                after = stripped[len(written):].lstrip(" \t")
                if after.startswith("="):
                    return at
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
