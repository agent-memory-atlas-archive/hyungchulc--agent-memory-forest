"""The one writer of a raw lane (design, section 15.1; contract, section 1).

``forest intake`` is the only program that appends to ``06 istm/<agent>/``.
One turn is one JSON line in the day's file, the day taken in the forest's time
zone. The line is appended, flushed and synced before the cursor moves, so a
crash can repeat a turn and cannot lose one. A turn whose ``id`` the lane
already holds is not written again.

A hook reads only the transcript it was called for. Ids are one append-only
file per lane file, so that read does not grow with the rest of the history.
The cursor remembers the file's device, inode, the size it saw and a hash of
the bytes just before the offset. When those no longer match, the source is
read again from the start and the ids keep what was already taken.

Numbers and sign-in values are masked before the line is written. A tool turn
records that the tool ran, not what it printed, unless ``intake.tool_output`` is
``keep``. A sub-agent's work is a tool turn. A session a forest stage started
is not taken in. The writer holds the ``intake`` lock for a moment. A second
intake that cannot take it leaves its cursor and returns.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import threading
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import credentials, layout, numbers
from . import adapters as adapter_loader
from .adapters.common import (
    ORIGINS,
    PART_KINDS,
    Part,
    Transcript,
    Turn,
    complete_end,
    env_internal,
    parse_object,
    text_internal,
)
from .config import Config, IntakeToolSettings, load_config
from .errors import MemoryForestError
from .locking import atomic_write, hold_lock


LOCK_NAME = "intake"
LOCK_WAIT_SECONDS = 0.2
# Threads of this process take the intake lock one at a time. ``hold_lock`` refuses a second take in the
# same process, and the HTTP server runs one thread per request.
_INTAKE_TURN = threading.Lock()
INTAKE_DIR = f"{layout.STATE_DIR}/intake"
FAILURES = f"{INTAKE_DIR}/failures.jsonl"
# What belongs to a tool's transcripts is kept by tool: its cursors, the mark that intake was turned on for
# it, and the counters its hooks leave without a lock. What belongs to an agent (its lane and the lane's
# ids) is kept by agent. A cursor names the agent whose lane its turns went to.
TOOLS_DIR = f"{INTAKE_DIR}/tools"
PREFIX_BYTES = 64
_EMPTY_PREFIX = hashlib.sha256(b"").hexdigest()
# A session with no owner turn is ordinary after this many turns. The adapters
# turn a short harness preamble into turns before the owner speaks; three was
# not past that preamble, and holding the cursor longer re-reads a session
# that is not going to open with the stage marker.
TURNS_BEFORE_OWNER = 12
# A hook reads at most this many transcript bytes in one call. Reading and masking
# 256 KiB of JSONL took about 50 ms on the machine this was written on, and the
# rest stays for the next call or a sweep. A line longer than this is the sweep's.
HOOK_READ_BYTES = 256 * 1024
# The most transcripts one sweep takes from an adapter's ``discover``.
DISCOVER_LIMIT = 100_000
_CHECKPOINT: Callable[[str], None] | None = None


@dataclass(frozen=True, slots=True)
class Result:
    """What one intake run did, for the command to print."""

    lines: tuple[str, ...]
    data: dict[str, Any]


def run(
    root: str | os.PathLike[str],
    adapter_name: str,
    *,
    agent: str | None = None,
    transcript: str | None = None,
    hook: Mapping[str, Any] | None = None,
    dry_run: bool = False,
    once: bool = False,
    environ: Mapping[str, str] | None = None,
) -> Result:
    """Take turns in from ``adapter_name``. ``hook`` is the hook's JSON object.

    A hook does one small thing. Before the first sweep of the tool has turned intake on, it reads nothing
    and adds one line to a counter that needs no lock. After it, the hook names its one transcript, takes
    the lock for a moment, and reads that transcript's cursor only; when the lock is held it leaves one
    marker for its transcript and returns.
    """
    if transcript and hook is not None:
        raise MemoryForestError(
            "intake_arguments",
            "Pass either --transcript or --hook, not both.",
        )
    config = load_config(root)
    if not layout.is_slug(adapter_name):
        raise MemoryForestError(
            "intake_adapter_unknown",
            f"{adapter_name} is not an adapter name.",
            details={"adapter": adapter_name},
        )
    adapter = adapter_loader.get_adapter(adapter_name)
    settings = config.intake.tool(adapter_name)
    if not settings.enabled and not once:
        message = (
            f"intake for {adapter_name} is off; "
            f"set [intake.tools.{adapter_name}] enabled = true, or pass --once"
        )
        return Result(
            (message,),
            {"adapter": adapter_name, "enabled": False, "message": message, "ok": True, "wrote": 0},
        )
    if adapter_name == "jsonl" and not settings.path and transcript is None and hook is None:
        raise MemoryForestError(
            "intake_path_unset",
            "The jsonl adapter needs the folder of record files. Set path under [intake.tools.jsonl].",
            details={"adapter": adapter_name},
        )
    chosen = (agent or settings.agent or "").strip()
    if not chosen:
        raise MemoryForestError(
            "intake_agent_unset",
            "Intake does not know which agent these turns belong to. "
            f"Pass --as <id>, or set agent under [intake.tools.{adapter_name}].",
            details={"adapter": adapter_name},
        )
    if not layout.is_slug(chosen):
        raise MemoryForestError(
            "intake_agent_invalid",
            f"{chosen} is not a slug.",
            details={"agent": chosen},
        )
    env = os.environ if environ is None else environ
    sweep = hook is None and transcript is None
    if hook is not None:
        if not _read_meta(root, adapter_name, dry_run=True).get("placed"):
            if not dry_run:
                _count(root, adapter_name, "before-on")
            message = f"{adapter_name}: intake is not on yet; run `forest intake --from {adapter_name}` to turn it on"
            return Result((message,), {"adapter": adapter_name, "agent": chosen, "dry_run": dry_run, "message": message,
                                       "ok": True, "on": False, "wrote": 0})
        try:
            item = _plugin(adapter.hook, dict(hook), settings)
        except MemoryForestError as exc:
            if exc.code != "transcript_deferred":
                raise
            if not dry_run:
                _count(root, adapter_name, "deferred")
            message = f"{adapter_name}: waiting for a sweep"
            return _result(adapter_name, chosen, (message,), 0, dry_run=dry_run, placed=True)
        if env_internal(env):
            item = replace(item, internal=True)
        discovered = [item]
    elif transcript is not None:
        discovered = [_plugin(adapter.open_path, Path(transcript), settings)]
    else:
        discovered = _plugin(_discover, adapter, settings)
    if dry_run:
        return _execute(root, config, adapter, settings, chosen, discovered, sweep=sweep,
                        bounded=hook is not None, dry_run=True)
    with _intake_lock(root) as held:
        if held is None:
            if hook is not None:
                _mark_busy(root, adapter_name, discovered[0].key)
            message = "another intake holds the lock; this one left its cursor"
            return Result(
                (message,),
                {"adapter": adapter_name, "lock": "busy", "message": message, "ok": True, "wrote": 0},
            )
        return _execute(root, config, adapter, settings, chosen, discovered, sweep=sweep,
                        bounded=hook is not None, dry_run=False)


def _discover(adapter: Any, settings: IntakeToolSettings) -> list[Transcript]:
    """The adapter's transcripts, taken inside the plugin guard and bounded, whatever ``discover`` returns."""
    found = adapter.discover(settings)
    return list(itertools.islice(iter(found), DISCOVER_LIMIT))


def append_owned(
    root: str | os.PathLike[str],
    agent: str,
    text: str,
    *,
    role: str,
    origin: str,
    session: str = "mcp",
    at: datetime | None = None,
) -> dict[str, Any]:
    """Append one turn to ``agent``'s raw lane. The caller has already chosen role and origin."""
    if not layout.is_slug(agent):
        raise MemoryForestError("intake_agent_invalid", f"{agent} is not a slug.", details={"agent": agent})
    if not _role_ok(role):
        raise MemoryForestError("intake_role_invalid", "That role is not one the lane records.", details={"role": role})
    if origin not in ORIGINS:
        raise MemoryForestError(
            "intake_origin_invalid", "That origin is not one the lane records.", details={"origin": origin}
        )
    if not str(text).strip():
        raise MemoryForestError("intake_text_empty", "A turn needs text.")
    config = load_config(root)
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    session_name = session.strip() or "mcp"
    turn = Turn(
        turn_id=None,
        at=moment,
        session=session_name,
        role=role,
        text=text,
        place=moment.isoformat(),
        origin=origin,
    )
    transcript = Transcript(key="mcp", path=Path("mcp"), session=session_name, cwd="", origin=origin)
    record = _record(config, agent, "mcp", transcript, turn)
    if record is None:
        raise MemoryForestError("intake_turn_rejected", "The turn was not a record the lane keeps.")
    day = _day(record["at"], config)
    with _intake_turn(), hold_lock(root, LOCK_NAME, wait_seconds=2.0, label="memory_log"):
        if _id_taken(root, agent, day, record["id"], full=True):
            record["path"] = f"{layout.ISTM}/{agent}/{day}.jsonl"
            record["wrote"] = False
            return record
        relative = f"{layout.ISTM}/{agent}/{day}.jsonl"
        _append(root, relative, _encode(record))
        _note_ids(root, agent, day, [record["id"]])
    record["path"] = relative
    record["wrote"] = True
    return record


def status(root: str | os.PathLike[str]) -> Result:
    """Each adapter, the lines it did not take, sources waiting on a cursor, and hook failures."""
    config = load_config(root)
    failed = adapter_loader.load_failures()
    rows: list[str] = []
    listed: list[dict[str, Any]] = []
    names = sorted(set(adapter_loader.load_adapters()) | set(failed))
    for name in names:
        if name in failed and name not in adapter_loader.load_adapters():
            rows.append(f"{name}: failed to load ({failed[name]})")
            listed.append({"adapter": name, "failed": failed[name], "loaded": False})
            continue
        settings = config.intake.tool(name)
        agent = settings.agent or "(not set)"
        mode = "on" if settings.enabled else "off"
        totals = _adapter_totals(root, name, settings)
        placed = bool(_read_meta(root, name, dry_run=True).get("placed"))
        before_on = _counted(root, name, "before-on")
        busy = _busy_count(root, name)
        deferred = _counted(root, name, "deferred")
        line = f"{name}: {mode}, agent {agent}, unknown {totals['unknown']}"
        if totals["dropped"]:
            detail = ", ".join(f"{label} {count}" for label, count in sorted(totals["dropped"].items()))
            line += f" ({detail})"
        if totals["internal"]:
            line += f", internal {totals['internal']}"
        if totals["turn_faults"]:
            line += f", turn faults {totals['turn_faults']}"
        if totals["version"]:
            line += f", version {totals['version']}"
        if totals["rewinds"]:
            line += f", rewinds {totals['rewinds']}"
        if totals["ahead"]:
            line += ", ahead " + ", ".join(totals["ahead"])
        if totals["long_line"]:
            line += f", {totals['long_line']} waiting for a sweep (a line longer than a hook reads)"
        if not placed and (settings.enabled or before_on):
            line += (f", not turned on yet: {before_on} hook calls read nothing; "
                     f"run `forest intake --from {name}` to turn it on")
        elif before_on:
            line += f", {before_on} hook calls came before intake was turned on"
        for owner, count in sorted(totals["elsewhere"].items()):
            line += (f", {count} transcripts kept for {owner} are not read while the agent is {agent}; "
                     f"run `forest intake rename-agent {owner} {agent}` or set the agent back to {owner}")
        if deferred:
            line += f", waiting {deferred}"
        if busy:
            line += f", busy {busy}"
        if name in failed:
            line += f", plugin failed ({failed[name]})"
        rows.append(line)
        listed.append(
            {
                "adapter": name,
                "agent": settings.agent,
                "ahead": totals["ahead"],
                "before_on": before_on,
                "busy": busy,
                "dropped": totals["dropped"],
                "elsewhere": totals["elsewhere"],
                "enabled": settings.enabled,
                "internal": totals["internal"],
                "loaded": True,
                "long_line": totals["long_line"],
                "on": placed,
                "plugin_failed": failed.get(name, ""),
                "rewinds": totals["rewinds"],
                "turn_faults": totals["turn_faults"],
                "unknown": totals["unknown"],
                "version": totals["version"],
                "waiting": deferred,
            }
        )
    failure_rows, failure_count = _failure_lines(root)
    if failure_count:
        rows.append(f"failures {failure_count}")
        rows.extend(failure_rows)
    return Result(tuple(rows), {"adapters": listed, "failures": failure_count, "ok": True})


def rename_agent(root: str | os.PathLike[str], old: str, new: str) -> Result:
    """Move one agent's lane and ids, and the name in each cursor that names it. The intake lock is held.

    The steps go in an order that a second run completes after an interruption at any of them: the lane
    folder, then the ids folder, then each cursor (each written whole). A step already done is skipped.
    """
    if not layout.is_slug(old) or not layout.is_slug(new):
        raise MemoryForestError("intake_agent_invalid", "An agent id is a slug.", details={"agent": old})
    if old == new:
        return Result((f"{old} is already the name",), {"agent": new, "ok": True, "renamed": False})
    pairs = (
        ("lane", f"{layout.ISTM}/{old}", f"{layout.ISTM}/{new}"),
        ("ids", f"{INTAKE_DIR}/ids/{old}", f"{INTAKE_DIR}/ids/{new}"),
    )
    moved_cursors = 0
    with _intake_turn(), hold_lock(root, LOCK_NAME, wait_seconds=2.0, label="forest intake rename-agent"):
        planned = []
        for step, source_rel, dest_rel in pairs:
            source = layout.resolve(root, source_rel)
            dest = layout.resolve(root, dest_rel)
            if source.exists():
                if dest.exists():
                    raise MemoryForestError(
                        "intake_rename_exists",
                        f"{new} already has a lane or ids of its own.",
                        details={"agent": new},
                    )
                planned.append((step, source, dest))
        for step, source, dest in planned:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.rename(source, dest)
            _checkpoint(f"rename-{step}")
        for adapter_name in _tool_names(root):
            for _path, cursor in _each_cursor(root, adapter_name):
                if cursor.get("agent") != old:
                    continue
                cursor["agent"] = new
                _save_cursor(root, adapter_name, str(cursor.get("source") or ""), cursor)
                moved_cursors += 1
                _checkpoint("rename-cursor")
    message = f"renamed {old} to {new}"
    return Result((message,), {"agent": new, "cursors": moved_cursors, "from": old, "ok": True, "renamed": True})


def reread(root: str | os.PathLike[str], adapter_name: str) -> Result:
    """Read one adapter's transcripts again from the point intake started from. Stored turns are kept by their ids.

    A cursor goes back to the offset intake first placed it at, and no further, so a line from before intake
    was turned on is not taken. An excluded cursor is left alone. What a read carried over is cleared.
    """
    if not layout.is_slug(adapter_name):
        raise MemoryForestError("intake_adapter_unknown", f"{adapter_name} is not an adapter name.")
    moved = 0
    with _intake_turn(), hold_lock(root, LOCK_NAME, wait_seconds=2.0, label="forest intake reread"):
        for _path, cursor in _each_cursor(root, adapter_name):
            if cursor.get("excluded"):
                continue
            floor = int(cursor.get("floor") or 0)
            cursor.pop("carry", None)
            cursor["reread"] = {"faults": 0}
            cursor["long_line"] = False
            if floor > 0 and isinstance(cursor.get("floor_prefix"), str):
                cursor["offset"] = floor
                cursor["prefix"] = cursor["floor_prefix"]
            else:
                cursor["offset"] = 0
                cursor["owner"] = ""
                cursor["prefix"] = _EMPTY_PREFIX
                cursor["seeded"] = False
                cursor["internal"] = False
                cursor.pop("dev", None)
                cursor.pop("ino", None)
            _save_cursor(root, adapter_name, str(cursor.get("source") or ""), cursor)
            moved += 1
    message = f"{adapter_name}: {moved} cursors will be read again from where intake started"
    return Result((message,), {"adapter": adapter_name, "cursors": moved, "ok": True})


def log_failure(root: str | os.PathLike[str], class_name: str, place: str, *, wrote: int | None = None) -> None:
    """One hook failure: the class and the place, and neither a path nor a message. ``wrote`` is how many
    turns the failed run had already stored."""
    safe_class = "".join(character for character in class_name if character.isalnum() or character == "_")[:80]
    safe_place = "".join(character for character in place if character.isalnum() or character in "-_/ ")[:80]
    if not safe_class:
        safe_class = "Error"
    if not safe_place:
        safe_place = "intake"
    line: dict[str, Any] = {"class": safe_class, "place": safe_place}
    if wrote is not None:
        line["wrote"] = int(wrote)
    try:
        layout.ensure_folder(root, INTAKE_DIR)
        _append(root, FAILURES, json.dumps(line, sort_keys=True) + "\n")
    except (OSError, MemoryForestError):
        return


class _WroteSome(Exception):
    """A step failed after some turns were already stored. ``wrote`` is that count."""

    def __init__(self, wrote: int, cause: BaseException) -> None:
        self.wrote = wrote
        self.cause = cause


def _plugin(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call adapter code. ``SystemExit`` becomes an error. ``KeyboardInterrupt`` still propagates."""
    try:
        return fn(*args, **kwargs)
    except KeyboardInterrupt:
        raise
    except Exception:
        raise
    except BaseException as exc:
        raise MemoryForestError(
            "plugin_failed",
            "A plugin ended the process.",
            details={"class": exc.__class__.__name__},
        ) from exc


def _execute(
    root: str | os.PathLike[str],
    config: Config,
    adapter: Any,
    settings: IntakeToolSettings,
    agent: str,
    discovered: list[Transcript],
    *,
    sweep: bool,
    bounded: bool,
    dry_run: bool,
) -> Result:
    meta = _read_meta(root, adapter.name, dry_run=dry_run)
    if sweep and not dry_run:
        _clear_count(root, adapter.name, "deferred")
        from .stage import prune_sessions

        prune_sessions(root)
    # The first sweep of a tool turns intake on for it: a cursor at the end of every transcript that exists
    # (unless earlier turns are imported), and the mark that it did.
    if sweep and not meta.get("placed") and not settings.import_earlier:
        if dry_run:
            lines = tuple(
                f"{item.path.name}: 0 turns (the cursor would be placed at the end)" for item in discovered
            )
            return _result(adapter.name, agent, lines, 0, dry_run=True, placed=False)
        for item in discovered:
            if _cursor_exists(root, adapter.name, item.key):
                continue
            described = _described(adapter, item, settings, payload_cwd=item.cwd)
            _follow_end(
                root, agent, adapter.name, described,
                excluded=not _allowed(described, settings),
                seeded=_allowed(described, settings),
            )
        meta["placed"] = True
        _write_meta(root, adapter.name, meta)
        return _result(adapter.name, agent, (), 0, dry_run=False, placed=True)
    if sweep and not meta.get("placed") and not dry_run:
        meta["placed"] = True
        _write_meta(root, adapter.name, meta)
    id_cache: dict[tuple[str, str], set[str]] = {}
    reports: list[str] = []
    total = 0
    total_unknown = 0
    for item in discovered:
        label = item.key or item.path.name
        try:
            wrote, unknown, note = _one(
                root, config, adapter, settings, agent, item,
                dry_run=dry_run,
                bounded=bounded,
                id_cache=id_cache,
            )
        except _WroteSome as exc:
            cause = exc.cause
            if not dry_run:
                log_failure(
                    root,
                    cause.code if isinstance(cause, MemoryForestError) else cause.__class__.__name__,
                    adapter.name,
                    wrote=exc.wrote,
                )
            total += exc.wrote
            reports.append(f"{Path(label).name}: {exc.wrote} turns, then failed ({cause.__class__.__name__})")
            continue
        except Exception as exc:
            if not dry_run:
                log_failure(root, exc.__class__.__name__ if not isinstance(exc, MemoryForestError) else exc.code, adapter.name)
            reports.append(f"{Path(label).name}: failed ({exc.__class__.__name__})")
            continue
        total += wrote
        total_unknown += unknown
        line = f"{Path(label).name}: {wrote} turn" if wrote == 1 else f"{Path(label).name}: {wrote} turns"
        if unknown:
            line += f", {unknown} unknown"
        if note:
            line += f" ({note})"
        reports.append(line)
    return _result(adapter.name, agent, tuple(reports), total, dry_run=dry_run, placed=True, unknown=total_unknown)


def _one(
    root,
    config: Config,
    adapter: Any,
    settings: IntakeToolSettings,
    agent: str,
    transcript: Transcript,
    *,
    dry_run: bool,
    bounded: bool,
    id_cache: dict[tuple[str, str], set[str]],
) -> tuple[int, int, str]:
    payload_cwd = transcript.cwd
    cursor = _load_cursor(root, adapter.name, transcript.key, dry_run=dry_run)
    owner_agent = cursor.get("agent")
    if isinstance(owner_agent, str) and owner_agent and owner_agent != agent:
        # The turns of this transcript went to another agent's lane. It is not read until the name is set back
        # or ``forest intake rename-agent`` moves that lane; ``status`` counts it.
        return 0, 0, f"kept for {owner_agent}"
    if bounded and cursor.get("long_line"):
        return 0, 0, "waiting for a sweep"
    # A cursor that only followed an excluded file stays where it is. import_earlier
    # rewinds a cursor that was placed at the end on the first run, not one that
    # was excluded: excluded means those turns are never taken.
    if settings.import_earlier and cursor.get("seeded") and not cursor.get("excluded"):
        cursor["offset"] = 0
        cursor["prefix"] = _EMPTY_PREFIX
        cursor["seeded"] = False
        cursor["excluded"] = False
        cursor["owner"] = ""
        cursor["floor"] = 0
        cursor.pop("floor_prefix", None)
        cursor.pop("carry", None)
    offset = int(cursor.get("offset") or 0)
    try:
        st = transcript.path.stat()
    except OSError as exc:
        raise MemoryForestError(
            "transcript_missing",
            "The transcript is not a file.",
            details={"adapter": adapter.name, "path": transcript.path.name},
        ) from exc
    prefix = _prefix_hash(transcript.path, offset if offset <= st.st_size else 0)
    rewind = False
    if not _identity_ok(cursor, st, prefix, offset):
        offset = 0
        rewind = True
        cursor["rewinds"] = int(cursor.get("rewinds") or 0) + 1
        cursor["owner"] = ""
        cursor["floor"] = 0
        cursor.pop("floor_prefix", None)
        cursor.pop("carry", None)
    fresh = "dev" not in cursor and not _cursor_exists(root, adapter.name, transcript.key)
    if rewind or not cursor.get("described") or fresh:
        transcript = _described(adapter, transcript, settings, payload_cwd=payload_cwd)
    else:
        transcript = replace(
            transcript,
            session=str(cursor.get("session") or transcript.session),
            cwd=str(cursor.get("cwd") or transcript.cwd),
            origin=str(cursor.get("origin") or transcript.origin),
            internal=transcript.internal or bool(cursor.get("internal")),
            origin_raw=str(cursor.get("origin_raw") or ""),
            version=str(cursor.get("version") or ""),
            described=True,
            parent=str(cursor.get("parent") or ""),
        )
    cursor["agent"] = agent
    if not _allowed(transcript, settings):
        if not dry_run:
            _follow_end(root, agent, adapter.name, transcript, excluded=True)
        return 0, 0, "excluded"
    if cursor.get("excluded"):
        # The exclude was lifted: the past it covered is not taken, and a reread goes back no further.
        cursor["excluded"] = False
        cursor["floor"] = offset
        cursor["floor_prefix"] = prefix
    internal_note = "session carries MEMORY_FOREST_INTERNAL"
    decided = cursor.get("owner")
    # A stage session is found by one ``stat``, only while the session's decision is open.
    if decided not in {"ordinary", "internal"} and _stage_session(root, transcript.session):
        transcript = replace(transcript, internal=True)
    if transcript.internal or decided == "internal":
        if not dry_run:
            cursor["owner"] = "internal"
            _store_cursor(root, adapter.name, transcript, cursor, offset=_file_end(transcript.path), internal=True)
            _clear_busy(root, adapter.name, transcript.key)
        return 0, 0, internal_note
    carry = cursor.get("carry") if isinstance(cursor.get("carry"), dict) else None
    batch = _plugin(
        adapter.read,
        transcript,
        offset,
        carry=carry,
        max_bytes=HOOK_READ_BYTES if bounded else None,
    )
    if bounded and batch.offset <= offset and not batch.turns and _file_end(transcript.path) > offset:
        # The next line is longer than a hook reads. It is the sweep's: the hook notes that once and later
        # hooks of this transcript return at once.
        if not dry_run:
            cursor["long_line"] = True
            _store_cursor(root, adapter.name, transcript, cursor, offset=offset, internal=False)
            _clear_busy(root, adapter.name, transcript.key)
        return 0, 0, "a line longer than a hook reads waits for a sweep"
    if batch.faults and not dry_run:
        for class_name, place in batch.faults:
            log_failure(root, class_name, f"{adapter.name} {place}")
    if batch.internal:
        if not dry_run:
            cursor["owner"] = "internal"
            _remember_batch(root, adapter.name, transcript, cursor, batch, internal=True)
        return 0, batch.unknown, internal_note
    if decided not in {"ordinary", "internal"} and offset == 0 and not cursor.get("seeded"):
        # Only the first owner turn's own text can mark a session as internal.
        owner = next((turn for turn in batch.turns if turn.role == "owner"), None)
        if owner is not None and text_internal(owner.text):
            if not dry_run:
                cursor["owner"] = "internal"
                _store_cursor(
                    root, adapter.name, transcript, cursor,
                    offset=_file_end(transcript.path), internal=True,
                )
            return 0, batch.unknown, internal_note
        if owner is None and len(batch.turns) < TURNS_BEFORE_OWNER:
            if not dry_run:
                _store_cursor(root, adapter.name, transcript, cursor, offset=0, internal=False)
            return 0, batch.unknown, "waiting for the owner turn"
        cursor["owner"] = "ordinary"
    full = rewind or offset == 0
    records: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    faults = 0
    for turn in batch.turns:
        try:
            record = _record(config, agent, adapter.name, transcript, turn)
            if record is None:
                continue
            day = _day(record["at"], config)
        except Exception as exc:
            faults += 1
            if not dry_run:
                log_failure(root, exc.__class__.__name__, f"{adapter.name} {turn.place}")
            continue
        if record["id"] in seen:
            continue
        if _id_taken(root, agent, day, record["id"], full=full, persist=not dry_run, cache=id_cache):
            continue
        seen.add(record["id"])
        records.append((record["id"], record))
    cursor["turn_faults"] = int(cursor.get("turn_faults") or 0) + faults
    cursor["carry"] = batch.carry
    cursor["long_line"] = False
    rereading = cursor.get("reread") if isinstance(cursor.get("reread"), dict) else None
    if rereading is not None:
        rereading["faults"] = int(rereading.get("faults") or 0) + faults + len(batch.faults)
        if batch.offset >= _file_end(transcript.path):
            # The reread reached the end: a reread with no fault clears what the transcript showed before.
            if not rereading["faults"]:
                cursor["turn_faults"] = 0
            cursor.pop("reread", None)
    if dry_run:
        return len(records), batch.unknown, ""
    by_file: dict[str, list[dict[str, Any]]] = {}
    for turn_id, record in records:
        relative = f"{layout.ISTM}/{agent}/{_day(record['at'], config)}.jsonl"
        by_file.setdefault(relative, []).append(record)
    written = 0
    try:
        for relative, group in by_file.items():
            payload = "".join(_encode(item) for item in group)
            _checkpoint("append")
            _append(root, relative, payload)
            written += len(group)
            day = Path(relative).stem
            _note_ids(root, agent, day, [item["id"] for item in group])
            id_cache.setdefault((agent, day), set()).update(item["id"] for item in group)
        _remember_batch(root, adapter.name, transcript, cursor, batch, internal=False)
    except Exception as exc:
        # Any failure after a line is in the lane reports what was written.
        raise _WroteSome(written, exc) from exc
    _clear_busy(root, adapter.name, transcript.key)
    return len(records), batch.unknown, ""


def _described(adapter: Any, transcript: Transcript, settings: IntakeToolSettings, *, payload_cwd: str) -> Transcript:
    """The transcript after ``describe``. The payload folder counts only when the file states none."""
    hooked_origin = transcript.origin
    forced_internal = transcript.internal
    describe = getattr(adapter, "describe", None)
    if describe is not None:
        transcript = _plugin(describe, transcript, settings)
    if not transcript.cwd and payload_cwd:
        transcript = replace(transcript, cwd=payload_cwd)
    if hooked_origin == "subagent":
        transcript = replace(transcript, origin="subagent")
    if forced_internal:
        transcript = replace(transcript, internal=True)
    return transcript


def _checkpoint(step: str) -> None:
    hook = _CHECKPOINT
    if hook is not None:
        hook(step)


# ─────────────────────────── counters a hook leaves without the lock ───────────────────────────


def _tool_dir(root, adapter_name: str) -> Path:
    return layout.resolve(root, f"{TOOLS_DIR}/{adapter_name}")


def _count(root, adapter_name: str, name: str) -> None:
    """One short line, appended without the intake lock."""
    try:
        layout.ensure_folder(root, f"{TOOLS_DIR}/{adapter_name}")
        path = _tool_dir(root, adapter_name) / name
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            os.write(descriptor, b".\n")
        finally:
            os.close(descriptor)
    except (OSError, MemoryForestError):
        return


def _counted(root, adapter_name: str, name: str) -> int:
    path = _tool_dir(root, adapter_name) / name
    try:
        if not path.is_file() or path.is_symlink():
            return 0
        return path.read_bytes().count(b"\n")
    except OSError:
        return 0


def _clear_count(root, adapter_name: str, name: str) -> None:
    path = _tool_dir(root, adapter_name) / name
    try:
        if path.is_file() and not path.is_symlink():
            path.write_bytes(b"")
    except OSError:
        return


def _busy_dir(root, adapter_name: str) -> Path:
    return _tool_dir(root, adapter_name) / "busy"


def _mark_busy(root, adapter_name: str, source_key: str) -> None:
    """A hook that found the lock held leaves one small marker for its transcript. It needs no lock."""
    try:
        layout.ensure_folder(root, f"{TOOLS_DIR}/{adapter_name}/busy")
        path = _busy_dir(root, adapter_name) / _key_digest(source_key)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(descriptor)
    except (OSError, MemoryForestError):
        return


def _clear_busy(root, adapter_name: str, source_key: str) -> None:
    """The run that read the transcript removes its marker."""
    try:
        (_busy_dir(root, adapter_name) / _key_digest(source_key)).unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def _busy_count(root, adapter_name: str) -> int:
    folder = _busy_dir(root, adapter_name)
    try:
        if not folder.is_dir() or folder.is_symlink():
            return 0
        return sum(1 for entry in os.scandir(folder) if entry.is_file(follow_symlinks=False))
    except OSError:
        return 0


def _record(
    config: Config,
    _agent: str,
    adapter_name: str,
    transcript: Transcript,
    turn: Turn,
) -> dict[str, Any] | None:
    del _agent
    role = turn.role
    if role.startswith("other:"):
        masked_name, _changed = _repair(_mask(role.removeprefix("other:"), config))
        role = "other:" + masked_name
    if not _role_ok(role):
        return None
    origin = turn.origin if turn.origin in ORIGINS else transcript.origin
    if origin not in ORIGINS:
        origin = "unknown"
    text = turn.text
    parts = turn.parts
    name = turn.name
    arguments = turn.arguments if role == "tool" and config.intake.tool_output == "keep" else None
    if role == "tool":
        if not name:
            name = "tool"
        if config.intake.tool_output != "keep" or not text:
            text = f"ran {name}"
            parts = ()
            arguments = None
    # The home folder is folded first, so the part offsets are mapped once, onto the text that is stored.
    text, parts = _fold_home_parts(text, parts)
    if isinstance(arguments, str):
        arguments = _fold_home(arguments)
    masked, parts, repaired = _mask_parts(text, parts, config)
    at = turn.at if turn.at.tzinfo is not None else turn.at.replace(tzinfo=timezone.utc)
    source = {"adapter": adapter_name, "place": turn.place, "transcript": transcript.key}
    if transcript.origin_raw:
        source["origin_raw"] = transcript.origin_raw
    if transcript.parent:
        source["parent"] = transcript.parent
    record: dict[str, Any] = {
        "at": at.isoformat(),
        "id": _compose_id(
            turn.session or transcript.session, turn, stand_alone=_stand_alone(adapter_name, turn.turn_id)
        ),
        "origin": origin,
        "role": role,
        "session": turn.session or transcript.session,
        "source": source,
        "text": masked,
    }
    if role == "tool":
        record["name"] = name or "tool"
    if arguments:
        record["arguments"] = arguments
    if parts:
        record["parts"] = [{"end": part.end, "kind": part.kind, "start": part.start} for part in parts]
    repaired = _mask_strings(record, config) or repaired
    if repaired:
        record["repaired"] = True
    return record


def _home_pattern(home: str) -> re.Pattern[str]:
    """The home folder where it is a whole path: ``/srv/al`` in ``/srv/al/x``, never in ``/srv/alice/x``."""
    return re.compile(r"(?:(?<=file://)|(?<![\w.\-~/]))" + re.escape(home) + r"(?![\w\-~]|\.\w)")


def _fold_home(text: str) -> str:
    """Write ``~`` in place of the home folder. Only a text or a kept tool argument is folded."""
    return _fold_home_parts(text, ())[0]


def _fold_home_parts(text: str, parts: tuple[Part, ...]) -> tuple[str, tuple[Part, ...]]:
    """``text`` with ``~`` for the home folder, and each part moved to the characters it named."""
    home = str(Path.home()).rstrip("/")
    if len(home) <= 1 or home not in text:
        return text, parts
    matches = list(_home_pattern(home).finditer(text))
    if not matches:
        return text, parts
    pieces: list[str] = []
    cursor = 0
    for found in matches:
        pieces += [text[cursor:found.start()], "~"]
        cursor = found.end()
    pieces.append(text[cursor:])

    def moved(offset: int) -> int:
        shift = 0
        for found in matches:
            if offset >= found.end():
                shift += len(found.group(0)) - 1
            elif offset > found.start():
                return found.start() - shift + (1 if offset == found.end() else 0)
            else:
                break
        return offset - shift

    return "".join(pieces), tuple(Part(moved(part.start), moved(part.end), part.kind) for part in parts)


def _role_ok(role: str) -> bool:
    if role in {"owner", "agent", "tool", "system"}:
        return True
    return role.startswith("other:") and bool(role.removeprefix("other:").strip())


def _stand_alone(adapter_name: str, turn_id: str | None = None) -> bool:
    """Whether this adapter's own turn id is unique without the session. The lane names the agent.

    An adapter whose ids are unique only in some forms says so per id (``id_stands_alone``)."""
    try:
        adapter = adapter_loader.get_adapter(adapter_name)
    except MemoryForestError:
        return False
    check = getattr(adapter, "id_stands_alone", None)
    if callable(check) and turn_id:
        try:
            return bool(check(turn_id))
        except Exception:
            return False
    return bool(getattr(adapter, "ids_stand_alone", False))


# Every character at which ``str.splitlines`` breaks a line, the escape mark and the separator are escaped,
# so a part can never hold the separator or end a line of the day's id file.
_ID_SEPARATOR = ":"
_ID_ESCAPED = frozenset("%:\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")


def _id_part(value: str) -> str:
    """A session or turn id as it is, with ``%``, ``:`` and the line breaks escaped; a hash of its exact code
    points when it cannot be encoded or is longer than 200 characters. Two raw parts never become one."""
    try:
        value.encode("utf-8")
        encodable = True
    except UnicodeEncodeError:
        encodable = False
    if not encodable or len(value) > 200:
        # ``%h`` cannot start an escaped part: a raw ``%`` is written ``%25``.
        return "%h" + hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()
    if not any(character in _ID_ESCAPED for character in value):
        return value
    return "".join(
        (f"%{ord(character):02X}" if ord(character) < 0x100 else f"%u{ord(character):04X}")
        if character in _ID_ESCAPED else character
        for character in value
    )


def _compose_id(session: str, turn: Turn, *, stand_alone: bool) -> str:
    """The source's own id, or a hash of the turn's content and time. The byte offset is not part of it.

    The agent is not part of the id: the lane names the agent. The session is part of it only when the
    source's turn id is not unique by itself. Each part is escaped before the parts
    are joined (``%``, the separator and every line break), so two different raw ids never become one,
    and the day's id file, read by ``\\n`` alone, gives back the stored id.
    """
    token = turn.turn_id or ""
    if not token:
        material = "\0".join(
            (
                turn.session or session,
                turn.role,
                turn.at.isoformat(),
                turn.text,
                turn.name or "",
            )
        )
        token = hashlib.sha256(material.encode("utf-8", "surrogatepass")).hexdigest()[:20]
    else:
        token = _id_part(token)
    if stand_alone:
        return token
    return f"{_id_part(session)}{_ID_SEPARATOR}{token}"


def _day(at_text: str, config: Config) -> str:
    parsed = datetime.fromisoformat(at_text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(config.zone).date().isoformat()


def _repair(text: str) -> tuple[str, bool]:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return text.encode("utf-8", "replace").decode("utf-8"), True
    return text, False


def _mask_parts(text: str, parts: tuple[Part, ...], config: Config) -> tuple[str, tuple[Part, ...], bool]:
    """Mask the whole text, then map part boundaries onto it.

    A boundary that falls inside a replaced value moves to the nearer edge of the replacement.
    A tag that marks a part is not a value: the masker skips it.
    """
    text, repaired = _repair(text)
    valid = []
    for part in parts:
        if part.kind not in PART_KINDS:
            continue
        start = max(0, min(len(text), part.start))
        end = max(start, min(len(text), part.end))
        if end > start:
            valid.append(Part(start, end, part.kind))
    masked, changed = _repair(_mask(text, config))
    repaired = repaired or changed
    if not valid:
        return masked, (), repaired
    mapping = _offset_map(text, masked)
    mapped_parts = []
    for part in valid:
        start = mapping[min(part.start, len(mapping) - 1)]
        end = mapping[min(part.end, len(mapping) - 1)]
        if end > start:
            mapped_parts.append(Part(start, end, part.kind))
    return masked, tuple(mapped_parts), repaired


def _offset_map(original: str, masked: str) -> list[int]:
    """For each offset in ``original``, including the end, the offset in ``masked``."""
    mapping = [0] * (len(original) + 1)
    index = 0
    point = 0
    while index < len(original) and point < len(masked):
        if original[index] == masked[point]:
            mapping[index] = point
            index += 1
            point += 1
            continue
        if masked.startswith(credentials.REDACTED, point):
            after = point + len(credentials.REDACTED)
            tail = masked[after:after + 24]
            found = original.find(tail, index) if tail else len(original)
            if found < 0:
                found = len(original)
            span = max(1, found - index)
            for cursor in range(index, found):
                mapping[cursor] = point if (cursor - index) * 2 <= span else after
            mapping[found] = after
            index, point = found, after
            continue
        found_at = False
        for shift in range(1, 64):
            if point + shift > len(masked):
                break
            window = masked[point + shift:point + shift + 12]
            if len(window) < 3:
                continue
            found = original.find(window, index)
            if index <= found <= index + 80:
                span = max(1, found - index)
                for cursor in range(index, found):
                    mapping[cursor] = point if (cursor - index) * 2 <= span else point + shift
                mapping[found] = point + shift
                index, point = found, point + shift
                found_at = True
                break
        if not found_at:
            mapping[index] = point
            index += 1
            if point < len(masked):
                point += 1
    while index <= len(original):
        mapping[index] = min(point, len(masked))
        index += 1
    mapping[len(original)] = len(masked)
    return mapping


def _mask(text: str, config: Config) -> str:
    return numbers.star_numbers(credentials.mask(text, config=config), config=config)[0]


def _mask_strings(record: dict[str, Any], config: Config) -> bool:
    """Mask and repair every string the line will store. Returns whether any string was repaired."""
    repaired = False
    for key in ("text", "name", "session", "arguments"):
        value = record.get(key)
        if not isinstance(value, str):
            continue
        masked, changed = _repair(_mask(value, config))
        record[key] = masked
        repaired = repaired or changed
    source = record.get("source")
    if isinstance(source, dict):
        for key, value in list(source.items()):
            if not isinstance(value, str):
                continue
            masked, changed = _repair(_mask(value, config))
            source[key] = masked
            repaired = repaired or changed
    return repaired


def _encode(record: Mapping[str, Any]) -> str:
    text = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    return text.encode("utf-8", "replace").decode("utf-8")


def _allowed(transcript: Transcript, settings: IntakeToolSettings) -> bool:
    if settings.include and not _inside(transcript.cwd, settings.include):
        return False
    if settings.exclude and _inside(transcript.cwd, settings.exclude):
        return False
    return True


def _inside(cwd: str, roots: tuple[str, ...]) -> bool:
    if not cwd:
        return False
    folder = _normal(cwd)
    for root in roots:
        base = _normal(root)
        if folder == base or base in folder.parents:
            return True
    return False


def _normal(path: str) -> Path:
    candidate = Path(path).expanduser()
    try:
        return candidate.resolve()
    except OSError:
        return Path(os.path.abspath(candidate))


def _stage_session(root, session: str) -> bool:
    """Whether a stage recorded ``session``: one ``stat`` of the file named by the session's hash."""
    if not session:
        return False
    from .stage import session_recorded

    return session_recorded(root, session)


def _identity_ok(cursor: Mapping[str, Any], st: os.stat_result, prefix: str, offset: int) -> bool:
    """A cursor with no identity yet is trusted. A changed file is not."""
    if "dev" not in cursor or "ino" not in cursor:
        return True
    try:
        same_file = int(cursor["dev"]) == st.st_dev and int(cursor["ino"]) == st.st_ino
    except (TypeError, ValueError):
        return False
    if not same_file or st.st_size < offset:
        return False
    stored = cursor.get("prefix")
    if isinstance(stored, str) and stored and stored != prefix:
        return False
    return True


def _prefix_hash(path: Path, offset: int) -> str:
    if offset <= 0:
        return _EMPTY_PREFIX
    start = max(0, offset - PREFIX_BYTES)
    with path.open("rb") as handle:
        handle.seek(start)
        data = handle.read(offset - start)
    return hashlib.sha256(data).hexdigest()


def _file_end(path: Path) -> int:
    try:
        return complete_end(path)
    except OSError:
        return 0


def _result(
    adapter_name: str,
    agent: str,
    reports: tuple[str, ...],
    wrote: int,
    *,
    dry_run: bool,
    placed: bool,
    unknown: int = 0,
) -> Result:
    verb = "would be taken in" if dry_run else "taken in"
    head = f"{agent}: {wrote} turn {verb}" if wrote == 1 else f"{agent}: {wrote} turns {verb}"
    data = {
        "adapter": adapter_name,
        "agent": agent,
        "dry_run": dry_run,
        "ok": True,
        "placed": placed,
        "transcripts": list(reports),
        "unknown": unknown,
        "wrote": wrote,
    }
    return Result((head, *(f"  {line}" for line in reports)), data)


@contextmanager
def _intake_turn() -> Iterator[None]:
    """Wait for this process's other intake, then let it go when the file lock does.

    Two ``memory_log`` calls on the threaded server would otherwise refuse each other with ``lock_already_held``
    and tell the caller to pass a token it was never given.
    """
    _INTAKE_TURN.acquire()
    try:
        yield
    finally:
        _INTAKE_TURN.release()


@contextmanager
def _intake_lock(root: str | os.PathLike[str]) -> Iterator[object | None]:
    with _intake_turn():
        try:
            with hold_lock(root, LOCK_NAME, wait_seconds=LOCK_WAIT_SECONDS, label="forest intake") as token:
                yield token
        except MemoryForestError as exc:
            if exc.code != "lock_busy":
                raise
            yield None


def _key_digest(source_key: str) -> str:
    return hashlib.sha256(source_key.encode("utf-8", "surrogatepass")).hexdigest()[:20]


def cursor_file(root: str | os.PathLike[str], adapter_name: str, source_key: str) -> Path:
    """The cursor of one transcript of one tool. The cursor names the agent its turns went to."""
    return layout.resolve(root, f"{TOOLS_DIR}/{adapter_name}/cursors/{_key_digest(source_key)}.json")


def _cursor_dir(root, adapter_name: str) -> Path:
    return layout.resolve(root, f"{TOOLS_DIR}/{adapter_name}/cursors")


def _cursor_exists(root, adapter_name: str, source_key: str) -> bool:
    path = cursor_file(root, adapter_name, source_key)
    return path.is_file() and not path.is_symlink()


def _load_cursor(root, adapter_name: str, source_key: str, *, dry_run: bool = False) -> dict[str, Any]:
    path = cursor_file(root, adapter_name, source_key)
    if not path.is_file() or path.is_symlink():
        return {"source": source_key}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        if not dry_run:
            _set_aside(path)
            log_failure(root, "intake_state_invalid", adapter_name)
        return {"source": source_key}
    if not isinstance(value, dict):
        if not dry_run:
            _set_aside(path)
            log_failure(root, "intake_state_invalid", adapter_name)
        return {"source": source_key}
    value.setdefault("source", source_key)
    return value


def _set_aside(path: Path) -> None:
    target = path.with_name(path.name + ".aside")
    number = 1
    while target.exists():
        target = path.with_name(f"{path.name}.aside-{number}")
        number += 1
    try:
        os.replace(path, target)
    except OSError:
        return


def _save_cursor(root, adapter_name: str, source_key: str, cursor: Mapping[str, Any]) -> None:
    body = dict(cursor)
    body["source"] = source_key
    layout.ensure_folder(root, f"{TOOLS_DIR}/{adapter_name}/cursors")
    _checkpoint("cursor")
    atomic_write(
        cursor_file(root, adapter_name, source_key),
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _follow_end(root, agent: str, adapter_name: str, transcript: Transcript, *, excluded: bool, seeded: bool = False) -> None:
    """Move the cursor to the end of the file and write no turns. A placement is the floor a reread keeps."""
    try:
        st = transcript.path.stat()
        end = complete_end(transcript.path)
    except OSError:
        return
    cursor = _load_cursor(root, adapter_name, transcript.key)
    prefix = _prefix_hash(transcript.path, end)
    cursor.update(
        {
            "agent": agent,
            "described": True,
            "dev": st.st_dev,
            "excluded": excluded,
            "ino": st.st_ino,
            "offset": end,
            "prefix": prefix,
            "owner": "ordinary" if seeded and not excluded else "",
            "seeded": seeded and not excluded,
            "session": transcript.session,
            "size": st.st_size,
            "cwd": transcript.cwd,
            "origin": transcript.origin,
            "origin_raw": transcript.origin_raw,
            "parent": transcript.parent,
            "version": transcript.version,
        }
    )
    if seeded and not excluded:
        cursor["floor"] = end
        cursor["floor_prefix"] = prefix
    _save_cursor(root, adapter_name, transcript.key, cursor)


def _store_cursor(root, adapter_name, transcript: Transcript, cursor: dict[str, Any], *, offset: int, internal: bool) -> None:
    try:
        st = transcript.path.stat()
    except OSError:
        return
    cursor.update(_discovery(transcript))
    cursor.update(
        {
            "dev": st.st_dev,
            "ino": st.st_ino,
            "internal": internal,
            "offset": offset,
            "prefix": _prefix_hash(transcript.path, offset),
            "seeded": bool(cursor.get("seeded")),
            "size": st.st_size,
        }
    )
    _save_cursor(root, adapter_name, transcript.key, cursor)


def _remember_batch(root, adapter_name, transcript: Transcript, cursor: dict[str, Any], batch, *, internal: bool) -> None:
    dropped = dict(cursor.get("dropped") or {})
    if not isinstance(dropped, dict):
        dropped = {}
    for label, count in batch.dropped:
        dropped[label] = int(dropped.get(label, 0)) + count
    cursor["dropped"] = dropped
    cursor["unknown"] = int(cursor.get("unknown") or 0) + int(batch.unknown)
    faults = list(cursor.get("faults") or [])
    faults.extend([list(item) for item in batch.faults])
    cursor["faults"] = faults[-8:]
    _store_cursor(root, adapter_name, transcript, cursor, offset=batch.offset, internal=internal)


def _discovery(transcript: Transcript) -> dict[str, Any]:
    return {
        "cwd": transcript.cwd,
        "described": True,
        "origin": transcript.origin,
        "origin_raw": transcript.origin_raw,
        "parent": transcript.parent,
        "session": transcript.session,
        "version": transcript.version,
    }


def _meta_path(root, adapter_name: str) -> Path:
    return _tool_dir(root, adapter_name) / "state.json"


def _read_meta(root, adapter_name: str, *, dry_run: bool = False) -> dict[str, Any]:
    """The tool's own state: whether intake was turned on for it. A file that cannot be read is set aside,
    except in a dry run, which writes nothing."""
    path = _meta_path(root, adapter_name)
    if not path.is_file() or path.is_symlink():
        return {"placed": False}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        value = None
    if not isinstance(value, dict):
        if not dry_run:
            _set_aside(path)
            log_failure(root, "intake_state_invalid", adapter_name)
        return {"placed": False}
    value.setdefault("placed", False)
    return value


def _write_meta(root, adapter_name: str, meta: Mapping[str, Any]) -> None:
    layout.ensure_folder(root, f"{TOOLS_DIR}/{adapter_name}")
    atomic_write(_meta_path(root, adapter_name), json.dumps(dict(meta), sort_keys=True) + "\n")


def _tool_names(root) -> list[str]:
    base = layout.resolve(root, TOOLS_DIR)
    if not base.is_dir() or base.is_symlink():
        return []
    return sorted(entry.name for entry in base.iterdir() if entry.is_dir() and not entry.is_symlink())


def _each_cursor(root, adapter_name: str) -> list[tuple[Path, dict[str, Any]]]:
    folder = _cursor_dir(root, adapter_name)
    if not folder.is_dir() or folder.is_symlink():
        return []
    found = []
    for entry in sorted(folder.iterdir(), key=lambda item: item.name):
        if entry.name.startswith("_") or entry.suffix != ".json" or entry.is_symlink():
            continue
        try:
            value = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            found.append((entry, value))
    return found


def _adapter_totals(root, adapter_name: str, settings: IntakeToolSettings) -> dict[str, Any]:
    totals: dict[str, Any] = {
        "ahead": [], "dropped": {}, "elsewhere": {}, "internal": 0, "long_line": 0, "rewinds": 0,
        "turn_faults": 0, "unknown": 0, "version": "",
    }
    transcript_root = _transcript_root(adapter_name, settings)
    for _path, cursor in _each_cursor(root, adapter_name):
        owner = cursor.get("agent")
        if settings.agent and isinstance(owner, str) and owner and owner != settings.agent:
            totals["elsewhere"][owner] = totals["elsewhere"].get(owner, 0) + 1
        totals["unknown"] += int(cursor.get("unknown") or 0)
        totals["rewinds"] += int(cursor.get("rewinds") or 0)
        totals["turn_faults"] += int(cursor.get("turn_faults") or 0)
        if cursor.get("long_line"):
            totals["long_line"] += 1
        if cursor.get("internal") or cursor.get("owner") == "internal":
            totals["internal"] += 1
        raw_dropped = cursor.get("dropped")
        if isinstance(raw_dropped, dict):
            for label, count in raw_dropped.items():
                try:
                    totals["dropped"][str(label)] = totals["dropped"].get(str(label), 0) + int(count)
                except (TypeError, ValueError):
                    continue
        if not totals["version"] and isinstance(cursor.get("version"), str):
            totals["version"] = cursor["version"]
        source = cursor.get("source")
        if isinstance(source, str) and source and _ahead(transcript_root, source, cursor):
            totals["ahead"].append(Path(source).name)
    return totals


def _transcript_root(adapter_name: str, settings: IntakeToolSettings) -> Path | None:
    try:
        adapter = adapter_loader.get_adapter(adapter_name)
    except MemoryForestError:
        return None
    root_of = getattr(adapter, "transcript_root", None)
    if root_of is None:
        return Path(settings.path).expanduser() if settings.path else None
    found = root_of(settings)
    return found if isinstance(found, Path) else None


def _ahead(transcript_root: Path | None, source: str, cursor: Mapping[str, Any]) -> bool:
    if transcript_root is None:
        return False
    path = transcript_root / source
    try:
        if not path.is_file() or path.is_symlink():
            return False
        return path.stat().st_size > int(cursor.get("offset") or 0)
    except (OSError, ValueError):
        return False


def _failure_lines(root) -> tuple[list[str], int]:
    path = layout.resolve(root, FAILURES)
    if not path.is_file() or path.is_symlink():
        return [], 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return [], 0
    shown = []
    for line in lines[-5:]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("class"), str) and isinstance(value.get("place"), str):
            row = f"  {value['class']} {value['place']}"
            if isinstance(value.get("wrote"), int):
                row += f": {value['wrote']} turns, then failed"
            shown.append(row)
    return shown, len(lines)


def _id_taken(
    root,
    agent: str,
    day: str,
    turn_id: str,
    *,
    full: bool,
    persist: bool = True,
    cache: dict[tuple[str, str], set[str]] | None = None,
) -> bool:
    if cache is None:
        cache = {}
    return turn_id in _known_ids(root, agent, day, cache, full=full, persist=persist)


def _known_ids(
    root, agent: str, day: str, cache: dict[tuple[str, str], set[str]], *, full: bool, persist: bool
) -> set[str]:
    """The ids the day's lane holds, read once per day per run: the ids file and the bytes after ``.end``.

    A rewind, or a missing ids file, also reads that one lane file. The read is not repeated in the run.
    """
    key = (agent, day)
    if key in cache:
        return cache[key]
    path = _ids_path(root, agent, day)
    has_file = path.is_file() and not path.is_symlink()
    file_ids: set[str] = set()
    if has_file:
        try:
            file_ids = {line for line in path.read_text(encoding="utf-8").split("\n") if line}
        except (OSError, UnicodeError):
            file_ids = set()
    found = set(file_ids) | _gap_ids(root, agent, day)
    if full or not has_file:
        lane = _lane_path(root, agent, day)
        try:
            data = lane.read_bytes() if lane.is_file() and not lane.is_symlink() else b""
        except OSError:
            data = b""
        found |= _ids_in(data)
        if persist and not has_file:
            _covered_size = _covered(root, agent, day)[1]
            _write_ids(root, agent, day, found, _covered_size)
    cache[key] = found
    return found


def _id_dir(root, agent: str) -> Path:
    return layout.resolve(root, f"{INTAKE_DIR}/ids/{agent}")


def _ids_path(root, agent: str, day: str) -> Path:
    return _id_dir(root, agent) / f"{day}.ids"


def _end_path(root, agent: str, day: str) -> Path:
    return _id_dir(root, agent) / f"{day}.end"


def _lane_path(root, agent: str, day: str) -> Path:
    return layout.resolve(root, f"{layout.ISTM}/{agent}/{day}.jsonl")


def _covered(root, agent: str, day: str) -> tuple[int, int]:
    lane = _lane_path(root, agent, day)
    try:
        size = lane.stat().st_size if lane.is_file() and not lane.is_symlink() else 0
    except OSError:
        size = 0
    end = _end_path(root, agent, day)
    covered = 0
    if end.is_file() and not end.is_symlink():
        try:
            covered = int(end.read_text(encoding="utf-8").strip() or "0")
        except (OSError, UnicodeError, ValueError):
            covered = 0
    if covered < 0 or covered > size:
        covered = 0
    return covered, size


def _gap_ids(root, agent: str, day: str) -> set[str]:
    covered, size = _covered(root, agent, day)
    if covered == size:
        return set()
    lane = _lane_path(root, agent, day)
    try:
        with lane.open("rb") as handle:
            handle.seek(covered)
            data = handle.read(size - covered)
    except OSError:
        return set()
    return _ids_in(data)


def _day_ids(root, agent: str, day: str) -> set[str]:
    covered, size = _covered(root, agent, day)
    path = _ids_path(root, agent, day)
    if path.is_file() and not path.is_symlink() and covered == size:
        try:
            return {line for line in path.read_text(encoding="utf-8").split("\n") if line}
        except (OSError, UnicodeError):
            pass
    lane = _lane_path(root, agent, day)
    ids: set[str] = set()
    if lane.is_file() and not lane.is_symlink():
        try:
            ids = _ids_in(lane.read_bytes())
        except OSError:
            ids = set()
    _write_ids(root, agent, day, ids, size)
    return ids


def _ids_in(data: bytes) -> set[str]:
    """Ids in complete lines. A trailing partial line is not an id yet."""
    found: set[str] = set()
    if not data:
        return found
    if not data.endswith(b"\n"):
        cut = data.rfind(b"\n")
        data = data[: cut + 1] if cut >= 0 else b""
    for raw in data.split(b"\n"):
        if not raw:
            continue
        value = parse_object(raw)
        if isinstance(value, dict) and isinstance(value.get("id"), str):
            found.add(value["id"])
    return found


def _note_ids(root, agent: str, day: str, ids: list[str]) -> None:
    """Append the ids of the lane's new tail, including a torn write's lines, then remember the end."""
    tail = _gap_ids(root, agent, day)
    fresh = list(dict.fromkeys([*tail, *ids]))
    _checkpoint("ids")
    if fresh:
        folder = _id_dir(root, agent)
        folder.mkdir(parents=True, exist_ok=True)
        path = _ids_path(root, agent, day)
        payload = "".join(item + "\n" for item in fresh).encode("utf-8")
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            _write_all(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    _write_end(root, agent, day, _lane_path(root, agent, day).stat().st_size)


def _write_ids(root, agent: str, day: str, ids: set[str], size: int) -> None:
    folder = _id_dir(root, agent)
    folder.mkdir(parents=True, exist_ok=True)
    atomic_write(_ids_path(root, agent, day), "".join(item + "\n" for item in sorted(ids)))
    _write_end(root, agent, day, size, mark=False)


def _write_end(root, agent: str, day: str, size: int, *, mark: bool = True) -> None:
    folder = _id_dir(root, agent)
    folder.mkdir(parents=True, exist_ok=True)
    if mark:
        _checkpoint("end")
    atomic_write(_end_path(root, agent, day), f"{size}\n")


def _append(root: str | os.PathLike[str], relative: str, payload: str) -> None:
    parent = relative.rsplit("/", 1)[0]
    layout.ensure_folder(root, parent)
    path = layout.resolve(root, relative)
    created = not path.exists()
    data = payload.encode("utf-8")
    # Read-write so a torn tail can be cut back before the append.
    flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        if created:
            os.fchmod(descriptor, 0o600)
        _trim_torn(descriptor)
        _write_all(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if created:
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def _trim_torn(descriptor: int) -> None:
    """A lane file that does not end in a newline loses that torn tail before the next append."""
    size = os.lseek(descriptor, 0, os.SEEK_END)
    if size <= 0:
        return
    os.lseek(descriptor, size - 1, os.SEEK_SET)
    if os.read(descriptor, 1) == b"\n":
        return
    position = size
    while position > 0:
        step = min(8192, position)
        position -= step
        os.lseek(descriptor, position, os.SEEK_SET)
        chunk = os.read(descriptor, step)
        found = chunk.rfind(b"\n")
        if found >= 0:
            os.ftruncate(descriptor, position + found + 1)
            return
    os.ftruncate(descriptor, 0)


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("a lane write made no progress")
        view = view[written:]


# ─────────────────────────────── lanes written under earlier masking rules ───────────────────────────────


def _mask_lane_value(value: Any, config: Config, ledger: Any) -> Any:
    if isinstance(value, str):
        return credentials.mask(value, config=config, ledger=ledger)
    if isinstance(value, dict):
        return {key: _mask_lane_value(item, config, ledger) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_lane_value(item, config, ledger) for item in value]
    return value


def _masked_lane_line(raw: bytes, config: Config, ledger: Any) -> bytes | None:
    """The line as today's rules would store it, or None when they change nothing (or it is no record).

    Every string of the record is masked except ``id`` and ``at``, as the writer masks them. A line the
    writer's encoding does not reproduce byte for byte is left as written: rewriting it would change more
    than the masked spans."""
    record = parse_object(raw)
    if not isinstance(record, dict):
        return None
    masked = {key: value if key in {"id", "at"} else _mask_lane_value(value, config, ledger)
              for key, value in record.items()}
    if masked == record:
        return None
    if _encode(record).encode("utf-8") != raw + b"\n":
        return b""
    return _encode(masked).encode("utf-8").rstrip(b"\n")


def _lane_files(root) -> list[tuple[str, str, Path]]:
    base = layout.resolve(root, layout.ISTM)
    found: list[tuple[str, str, Path]] = []
    if not base.is_dir() or base.is_symlink():
        return found
    for agent in sorted(base.iterdir(), key=lambda item: item.name):
        if not agent.is_dir() or agent.is_symlink() or not layout.is_slug(agent.name):
            continue
        for path in sorted(agent.glob("*.jsonl")):
            if path.is_file() and not path.is_symlink():
                found.append((agent.name, path.stem, path))
    return found


def lane_masking(root: str | os.PathLike[str], *, fix: bool = False) -> list[dict[str, Any]]:
    """For each lane (``06 istm/<agent>``), how many lines today's masking rules would change: a count, never a
    value. With ``fix`` each such line is rewritten in place, under the intake lock: the file is replaced
    atomically, it keeps its number of lines, and only the masked spans differ. The id index of a rewritten
    file is written again from it. A line the writer's encoding does not reproduce is counted as kept."""
    from . import credential_ledger

    config = load_config(root)
    ledger = credential_ledger.for_config(config)
    totals: dict[str, dict[str, Any]] = {}

    def scan(agent: str, path: Path) -> tuple[bytes, int, int]:
        data = path.read_bytes()
        complete = data[: data.rfind(b"\n") + 1] if b"\n" in data else b""
        tail = data[len(complete):]
        lines = complete.split(b"\n")[:-1] if complete else []
        changed = kept = 0
        out: list[bytes] = []
        for raw in lines:
            new = _masked_lane_line(raw, config, ledger)
            if new is None:
                out.append(raw)
                continue
            if new == b"":
                kept += 1
                out.append(raw)
                continue
            changed += 1
            out.append(new)
        rebuilt = b"".join(line + b"\n" for line in out) + tail
        return rebuilt, changed, kept

    def visit(write: bool) -> None:
        for agent, day, path in _lane_files(root):
            rebuilt, changed, kept = scan(agent, path)
            row = totals.setdefault(agent, {"lane": f"{layout.ISTM}/{agent}", "lines": 0, "kept": 0, "written": 0})
            row["lines"] += changed + kept
            row["kept"] += kept
            if write and changed:
                before = path.read_bytes().count(b"\n")
                atomic_write(path, rebuilt)
                if path.read_bytes().count(b"\n") != before:  # pragma: no cover - atomic_write writes all or nothing
                    raise MemoryForestError("lane_fix_lines", "A rewritten lane lost or gained a line.")
                _write_ids(root, agent, day, _ids_in(rebuilt), len(rebuilt))
                row["written"] += changed

    if not fix:
        visit(False)
    else:
        with _intake_turn(), hold_lock(root, LOCK_NAME, wait_seconds=2.0, label="forest credentials fix"):
            visit(True)
    return [row for row in totals.values() if row["lines"] or row["kept"]]
