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
import json
import os
from collections.abc import Mapping
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
INTAKE_DIR = f"{layout.STATE_DIR}/intake"
FAILURES = f"{INTAKE_DIR}/failures.jsonl"
PREFIX_BYTES = 64
_EMPTY_PREFIX = hashlib.sha256(b"").hexdigest()


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
    """Take turns in from ``adapter_name``. ``hook`` is the hook's JSON object."""
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
    if dry_run:
        return _execute(
            root, config, adapter, settings, chosen, transcript, hook, env, dry_run=True
        )
    with _intake_lock(root) as held:
        if held is None:
            message = "another intake holds the lock; this one left its cursor"
            return Result(
                (message,),
                {"adapter": adapter_name, "lock": "busy", "message": message, "ok": True, "wrote": 0},
            )
        return _execute(
            root, config, adapter, settings, chosen, transcript, hook, env, dry_run=False
        )


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
    with hold_lock(root, LOCK_NAME, wait_seconds=2.0, label="memory_log"):
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
        unknown, dropped, rewinds, ahead, version = _adapter_totals(root, config, name, settings)
        line = f"{name}: {mode}, agent {agent}, unknown {unknown}"
        if dropped:
            detail = ", ".join(f"{label} {count}" for label, count in sorted(dropped.items()))
            line += f" ({detail})"
        if version:
            line += f", version {version}"
        if rewinds:
            line += f", rewinds {rewinds}"
        if ahead:
            line += ", ahead " + ", ".join(ahead)
        if name in failed:
            line += f", plugin failed ({failed[name]})"
        rows.append(line)
        listed.append(
            {
                "adapter": name,
                "agent": settings.agent,
                "ahead": ahead,
                "dropped": dropped,
                "enabled": settings.enabled,
                "loaded": True,
                "plugin_failed": failed.get(name, ""),
                "rewinds": rewinds,
                "unknown": unknown,
                "version": version,
            }
        )
    failure_rows, failure_count = _failure_lines(root)
    if failure_count:
        rows.append(f"failures {failure_count}")
        rows.extend(failure_rows)
    return Result(tuple(rows), {"adapters": listed, "failures": failure_count, "ok": True})


def log_failure(root: str | os.PathLike[str], class_name: str, place: str) -> None:
    """One hook failure: the class and the place, and neither a path nor a message."""
    safe_class = "".join(character for character in class_name if character.isalnum() or character == "_")[:80]
    safe_place = "".join(character for character in place if character.isalnum() or character in "-_/")[:80]
    if not safe_class:
        safe_class = "Error"
    if not safe_place:
        safe_place = "intake"
    try:
        layout.ensure_folder(root, INTAKE_DIR)
        _append(root, FAILURES, json.dumps({"class": safe_class, "place": safe_place}, sort_keys=True) + "\n")
    except (OSError, MemoryForestError):
        return


def _execute(
    root: str | os.PathLike[str],
    config: Config,
    adapter: Any,
    settings: IntakeToolSettings,
    agent: str,
    transcript: str | None,
    hook: Mapping[str, Any] | None,
    env: Mapping[str, str],
    *,
    dry_run: bool,
) -> Result:
    sweep = hook is None and transcript is None
    if hook is not None:
        item = adapter.hook(dict(hook), settings)
        if env_internal(env):
            item = replace(item, internal=True)
        discovered = [item]
    elif transcript is not None:
        discovered = [adapter.open_path(Path(transcript), settings)]
    else:
        discovered = adapter.discover(settings)
    meta = _read_meta(root, agent, adapter.name) if sweep else {"placed": True}
    if sweep and not meta.get("placed") and not settings.import_earlier:
        if dry_run:
            lines = tuple(
                f"{item.path.name}: 0 turns (the cursor would be placed at the end)" for item in discovered
            )
            return _result(adapter.name, agent, lines, 0, dry_run=True, placed=False)
        for item in discovered:
            if _cursor_exists(root, agent, adapter.name, item.key):
                continue
            _follow_end(
                root, agent, adapter.name, item,
                excluded=not _allowed(item, settings),
                seeded=_allowed(item, settings),
            )
        meta["placed"] = True
        _write_meta(root, agent, adapter.name, meta)
    elif sweep and not meta.get("placed") and not dry_run:
        meta["placed"] = True
        _write_meta(root, agent, adapter.name, meta)
    requested = []
    for item in discovered:
        if _allowed(item, settings):
            requested.append(item)
        elif not dry_run:
            _follow_end(root, agent, adapter.name, item, excluded=True)

    reports: list[str] = []
    total = 0
    total_unknown = 0
    for item in requested:
        try:
            wrote, unknown, note = _one(
                root, config, adapter, settings, agent, item, dry_run=dry_run
            )
        except Exception as exc:
            log_failure(root, exc.__class__.__name__ if not isinstance(exc, MemoryForestError) else exc.code, adapter.name)
            label = item.key or item.path.name
            reports.append(f"{label}: failed ({exc.__class__.__name__})")
            continue
        total += wrote
        total_unknown += unknown
        label = f"{Path(item.key).name}: {wrote} turn" if wrote == 1 else f"{Path(item.key).name}: {wrote} turns"
        if unknown:
            label += f", {unknown} unknown"
        if note:
            label += f" ({note})"
        reports.append(label)
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
) -> tuple[int, int, str]:
    cursor = _load_cursor(root, agent, adapter.name, transcript.key)
    # A cursor that only followed an excluded file stays where it is. import_earlier
    # rewinds a cursor that was placed at the end on the first run, not one that
    # was excluded: excluded means those turns are never taken.
    if settings.import_earlier and cursor.get("seeded") and not cursor.get("excluded"):
        cursor["offset"] = 0
        cursor["prefix"] = _EMPTY_PREFIX
        cursor["seeded"] = False
        cursor["excluded"] = False
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
    if rewind or not cursor.get("described"):
        hooked_origin = transcript.origin
        forced_internal = transcript.internal
        describe = getattr(adapter, "describe", None)
        if describe is not None:
            transcript = describe(transcript, settings)
        if hooked_origin == "subagent":
            transcript = replace(transcript, origin="subagent")
        if forced_internal:
            transcript = replace(transcript, internal=True)
    else:
        transcript = replace(
            transcript,
            session=str(cursor.get("session") or transcript.session),
            cwd=str(cursor.get("cwd") or transcript.cwd),
            origin=str(cursor.get("origin") or transcript.origin),
            internal=bool(cursor.get("internal")),
            origin_raw=str(cursor.get("origin_raw") or ""),
            version=str(cursor.get("version") or ""),
            described=True,
            parent=str(cursor.get("parent") or ""),
        )
    internal_note = "session carries MEMORY_FOREST_INTERNAL"
    if transcript.internal or _stage_session(root, transcript.session):
        if not dry_run:
            _store_cursor(root, agent, adapter.name, transcript, cursor, offset=_file_end(transcript.path), internal=True)
        return 0, 0, internal_note
    batch = adapter.read(transcript, offset)
    if batch.faults and not dry_run:
        for class_name, place in batch.faults:
            log_failure(root, class_name, f"{adapter.name} {place}")
    if batch.internal or (offset == 0 and any(text_internal(turn.text) for turn in batch.turns[:3])):
        if not dry_run:
            _remember_batch(root, agent, adapter.name, transcript, cursor, batch, internal=True)
        return 0, batch.unknown, internal_note
    full = rewind or offset == 0
    records: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for turn in batch.turns:
        record = _record(config, agent, adapter.name, transcript, turn)
        if record is None:
            continue
        if record["id"] in seen:
            continue
        day = _day(record["at"], config)
        if _id_taken(root, agent, day, record["id"], full=full):
            continue
        seen.add(record["id"])
        records.append((record["id"], record))
    if dry_run:
        return len(records), batch.unknown, ""
    by_file: dict[str, list[dict[str, Any]]] = {}
    for turn_id, record in records:
        relative = f"{layout.ISTM}/{agent}/{_day(record['at'], config)}.jsonl"
        by_file.setdefault(relative, []).append(record)
    for relative, group in by_file.items():
        payload = "".join(_encode(item) for item in group)
        _append(root, relative, payload)
        day = Path(relative).stem
        _note_ids(root, agent, day, [item["id"] for item in group])
    _remember_batch(root, agent, adapter.name, transcript, cursor, batch, internal=False)
    return len(records), batch.unknown, ""


def _record(
    config: Config,
    agent: str,
    adapter_name: str,
    transcript: Transcript,
    turn: Turn,
) -> dict[str, Any] | None:
    role = turn.role
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
    masked, parts, repaired = _mask_parts(text, parts, config)
    at = turn.at if turn.at.tzinfo is not None else turn.at.replace(tzinfo=timezone.utc)
    source = {"adapter": adapter_name, "place": turn.place, "transcript": transcript.key}
    if transcript.origin_raw:
        source["origin_raw"] = transcript.origin_raw
    if transcript.parent:
        source["parent"] = transcript.parent
    record: dict[str, Any] = {
        "at": at.isoformat(),
        "id": _compose_id(agent, turn.session or transcript.session, turn),
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


def _role_ok(role: str) -> bool:
    if role in {"owner", "agent", "tool", "system"}:
        return True
    return role.startswith("other:") and bool(role.removeprefix("other:").strip())


def _compose_id(agent: str, session: str, turn: Turn) -> str:
    """The source's own id, or a hash of the turn's content and time. The byte offset is not part of it."""
    token = turn.turn_id or ""
    if not token or any(character in token for character in "\r\n") or len(token) > 200:
        material = "\0".join(
            (
                turn.session or session,
                turn.role,
                turn.at.isoformat(),
                turn.text,
                turn.name or "",
            )
        )
        token = hashlib.sha256(material.encode("utf-8", "replace")).hexdigest()[:20]
    safe_session = session.replace("\n", "").replace("\r", "")
    return f"{agent}:{safe_session}:{token}"


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
    text, repaired = _repair(text)
    valid = []
    for part in parts:
        if part.kind not in PART_KINDS:
            continue
        start = max(0, min(len(text), part.start))
        end = max(start, min(len(text), part.end))
        if end > start:
            valid.append(Part(start, end, part.kind))
    if not valid:
        masked, changed = _repair(_mask(text, config))
        return masked, (), repaired or changed
    cuts = {0, len(text)}
    for part in valid:
        cuts.add(part.start)
        cuts.add(part.end)
    ordered = sorted(cuts)
    pieces: list[str] = []
    spans: list[tuple[int, int, int, int]] = []
    cursor = 0
    for left, right in zip(ordered, ordered[1:]):
        piece, changed = _repair(_mask(text[left:right], config))
        repaired = repaired or changed
        spans.append((left, right, cursor, cursor + len(piece)))
        pieces.append(piece)
        cursor += len(piece)

    def mapped(point: int) -> int:
        for old_start, old_end, new_start, new_end in spans:
            if point == old_start:
                return new_start
            if point == old_end:
                return new_end
        return cursor

    mapped_parts = []
    for part in valid:
        start = mapped(part.start)
        end = mapped(part.end)
        if end > start:
            mapped_parts.append(Part(start, end, part.kind))
    return "".join(pieces), tuple(mapped_parts), repaired


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
def _intake_lock(root: str | os.PathLike[str]) -> Iterator[object | None]:
    try:
        with hold_lock(root, LOCK_NAME, wait_seconds=LOCK_WAIT_SECONDS, label="forest intake") as token:
            yield token
    except MemoryForestError as exc:
        if exc.code != "lock_busy":
            raise
        yield None


def cursor_file(root: str | os.PathLike[str], agent: str, adapter_name: str, source_key: str) -> Path:
    """The cursor for one agent, one adapter and one source."""
    digest = hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:20]
    relative = f"{INTAKE_DIR}/cursors/{agent}/{adapter_name}/{digest}.json"
    return layout.resolve(root, relative)


def _cursor_dir(root, agent: str, adapter_name: str) -> Path:
    return layout.resolve(root, f"{INTAKE_DIR}/cursors/{agent}/{adapter_name}")


def _cursor_exists(root, agent: str, adapter_name: str, source_key: str) -> bool:
    path = cursor_file(root, agent, adapter_name, source_key)
    return path.is_file() and not path.is_symlink()


def _load_cursor(root, agent: str, adapter_name: str, source_key: str) -> dict[str, Any]:
    path = cursor_file(root, agent, adapter_name, source_key)
    if not path.is_file() or path.is_symlink():
        return {"source": source_key}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _set_aside(path)
        log_failure(root, "intake_state_invalid", adapter_name)
        return {"source": source_key}
    if not isinstance(value, dict):
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


def _save_cursor(root, agent: str, adapter_name: str, source_key: str, cursor: Mapping[str, Any]) -> None:
    body = dict(cursor)
    body["source"] = source_key
    layout.ensure_folder(root, f"{INTAKE_DIR}/cursors/{agent}/{adapter_name}")
    atomic_write(
        cursor_file(root, agent, adapter_name, source_key),
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _follow_end(root, agent: str, adapter_name: str, transcript: Transcript, *, excluded: bool, seeded: bool = False) -> None:
    """Move the cursor to the end of the file and write no turns."""
    try:
        st = transcript.path.stat()
        end = complete_end(transcript.path)
    except OSError:
        return
    cursor = _load_cursor(root, agent, adapter_name, transcript.key)
    cursor.update(
        {
            "described": True,
            "dev": st.st_dev,
            "excluded": excluded,
            "ino": st.st_ino,
            "offset": end,
            "prefix": _prefix_hash(transcript.path, end),
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
    _save_cursor(root, agent, adapter_name, transcript.key, cursor)


def _store_cursor(root, agent, adapter_name, transcript: Transcript, cursor: dict[str, Any], *, offset: int, internal: bool) -> None:
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
    _save_cursor(root, agent, adapter_name, transcript.key, cursor)


def _remember_batch(root, agent, adapter_name, transcript: Transcript, cursor: dict[str, Any], batch, *, internal: bool) -> None:
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
    _store_cursor(root, agent, adapter_name, transcript, cursor, offset=batch.offset, internal=internal)


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


def _read_meta(root, agent: str, adapter_name: str) -> dict[str, Any]:
    path = _cursor_dir(root, agent, adapter_name) / "_meta.json"
    if not path.is_file() or path.is_symlink():
        return {"placed": False}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _set_aside(path)
        log_failure(root, "intake_state_invalid", adapter_name)
        return {"placed": False}
    if not isinstance(value, dict):
        _set_aside(path)
        log_failure(root, "intake_state_invalid", adapter_name)
        return {"placed": False}
    value.setdefault("placed", False)
    return value


def _write_meta(root, agent: str, adapter_name: str, meta: Mapping[str, Any]) -> None:
    folder = _cursor_dir(root, agent, adapter_name)
    folder.mkdir(parents=True, exist_ok=True)
    atomic_write(folder / "_meta.json", json.dumps(dict(meta), sort_keys=True) + "\n")


def _each_cursor(root, agent: str, adapter_name: str) -> list[tuple[Path, dict[str, Any]]]:
    folder = _cursor_dir(root, agent, adapter_name)
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


def _adapter_totals(root, config: Config, adapter_name: str, settings: IntakeToolSettings) -> tuple[int, dict[str, int], int, list[str], str]:
    unknown = 0
    dropped: dict[str, int] = {}
    rewinds = 0
    ahead: list[str] = []
    version = ""
    agents = _agents_with_cursors(root, adapter_name, settings.agent)
    for agent in agents:
        for _path, cursor in _each_cursor(root, agent, adapter_name):
            unknown += int(cursor.get("unknown") or 0)
            rewinds += int(cursor.get("rewinds") or 0)
            raw_dropped = cursor.get("dropped")
            if isinstance(raw_dropped, dict):
                for label, count in raw_dropped.items():
                    try:
                        dropped[str(label)] = dropped.get(str(label), 0) + int(count)
                    except (TypeError, ValueError):
                        continue
            if not version and isinstance(cursor.get("version"), str):
                version = cursor["version"]
            source = cursor.get("source")
            if isinstance(source, str) and source and _ahead(settings, source, cursor):
                ahead.append(Path(source).name)
    return unknown, dropped, rewinds, ahead, version


def _agents_with_cursors(root, adapter_name: str, configured: str) -> list[str]:
    base = layout.resolve(root, f"{INTAKE_DIR}/cursors")
    found = []
    if base.is_dir() and not base.is_symlink():
        for agent in sorted(base.iterdir(), key=lambda item: item.name):
            if (agent / adapter_name).is_dir():
                found.append(agent.name)
    if configured and configured not in found:
        found.append(configured)
    return found


def _ahead(settings: IntakeToolSettings, source: str, cursor: Mapping[str, Any]) -> bool:
    if not settings.path:
        return False
    root = Path(settings.path).expanduser()
    path = root / source
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
            shown.append(f"  {value['class']} {value['place']}")
    return shown, len(lines)


def _id_taken(root, agent: str, day: str, turn_id: str, *, full: bool) -> bool:
    if full:
        return turn_id in _day_ids(root, agent, day)
    return turn_id in _gap_ids(root, agent, day)


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
            return {line for line in path.read_text(encoding="utf-8").splitlines() if line}
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
    for raw in data.splitlines():
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
    _write_end(root, agent, day, size)


def _write_end(root, agent: str, day: str, size: int) -> None:
    folder = _id_dir(root, agent)
    folder.mkdir(parents=True, exist_ok=True)
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
