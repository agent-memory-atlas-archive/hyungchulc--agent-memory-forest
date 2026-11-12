"""The one writer of a raw lane (design, section 15.1; contract, section 1).

``forest intake`` is the only program that appends to ``06 istm/<agent>/``.
One turn is one JSON line in the day's file, the day taken in the forest's time
zone. The line is appended, flushed and synced before the cursor moves, so a
crash can repeat a turn and cannot lose one. A turn whose ``id`` the lane
already holds is not written again. The index under ``.forest/intake/`` remembers
those ids and is rebuilt from the lane files whenever it disagrees with them;
the lane is the truth.

Numbers and sign-in values are masked before the line is written. A tool turn
records that the tool ran, not what it printed, unless ``intake.tool_output`` is
``keep``. A sub-agent's work is a tool turn. A session a forest stage started,
marked ``MEMORY_FOREST_INTERNAL``, is not taken in.

The writer holds the ``intake`` lock for a moment. A second intake that cannot
take it leaves its cursor and returns, so a hook never holds up the agent that
fired it. Automatic intake stays off until ``[intake.tools.<adapter>] enabled``
is true; ``--once`` takes a turn in anyway.
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
    head_internal,
)
from .config import Config, IntakeToolSettings, load_config
from .errors import MemoryForestError
from .locking import atomic_write, hold_lock


LOCK_NAME = "intake"
LOCK_WAIT_SECONDS = 0.2
INTAKE_DIR = f"{layout.STATE_DIR}/intake"
INDEX_FILE = f"{INTAKE_DIR}/index.json"


@dataclass(frozen=True, slots=True)
class Result:
    """What one intake run did, for the command to print."""

    exit_code: int
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
            0,
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
                0,
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
    """Append one turn to ``agent``'s raw lane. The caller has already chosen role and origin.

    ``memory_log`` uses this. The same masking, id index and single writer as
    ``forest intake`` apply. A second call with the same text at a different
    instant is a second turn.
    """
    if not layout.is_slug(agent):
        raise MemoryForestError("intake_agent_invalid", f"{agent} is not a slug.", details={"agent": agent})
    if role not in {"owner", "agent", "tool"} and not (
        role.startswith("other:") and role.removeprefix("other:").strip()
    ):
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
    with hold_lock(root, LOCK_NAME, wait_seconds=2.0, label="memory_log"):
        ids, sizes = _load_ids(root, write=True)
        if record["id"] in ids:
            record["path"] = ids[record["id"]]
            record["wrote"] = False
            return record
        relative = f"{layout.ISTM}/{agent}/{_day(record['at'], config)}.jsonl"
        _append(root, relative, _encode(record))
        ids[record["id"]] = relative
        sizes[relative] = layout.resolve(root, relative).stat().st_size
        _save_index(root, sizes, ids)
    record["path"] = relative
    record["wrote"] = True
    return record


def status(root: str | os.PathLike[str]) -> Result:
    """Each adapter, whether it is on, which agent it files under, and unknown lines."""
    config = load_config(root)
    rows = []
    listed = []
    for name in sorted(adapter_loader.load_adapters()):
        settings = config.intake.tool(name)
        state = _read_state(root, name)
        unknown = sum(int(item.get("unknown", 0)) for item in state["transcripts"].values())
        agent = settings.agent or "(not set)"
        mode = "on" if settings.enabled else "off"
        rows.append(f"{name}: {mode}, agent {agent}, unknown {unknown}")
        listed.append(
            {"adapter": name, "agent": settings.agent, "enabled": settings.enabled, "unknown": unknown}
        )
    return Result(0, tuple(rows), {"adapters": listed, "ok": True})


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
    state = _read_state(root, adapter.name)
    discovered = adapter.discover(settings)
    if hook is not None:
        requested = [adapter.hook(dict(hook), settings)]
        if env_internal(env):
            requested = [replace(requested[0], internal=True)]
    elif transcript is not None:
        requested = [adapter.open_path(Path(transcript), settings)]
    else:
        requested = [item for item in discovered if _allowed(item, settings)]
    requested = [item for item in requested if _allowed(item, settings)]

    if not state["placed"] and not settings.import_earlier:
        if dry_run:
            lines = tuple(
                f"{item.path.name}: 0 turns (the cursor would be placed at the end)" for item in requested
            )
            return _result(adapter.name, agent, lines, 0, dry_run=True, placed=False)
        for item in discovered:
            _remember_cursor(
                state,
                item.key,
                offset=complete_end(item.path),
                seeded=True,
                unknown=0,
                internal=item.internal,
            )
        state["placed"] = True

    if settings.import_earlier:
        for cursor in state["transcripts"].values():
            if cursor.get("seeded"):
                cursor["offset"] = 0
                cursor["seeded"] = False

    ids, sizes = _load_ids(root, write=not dry_run)
    reports: list[str] = []
    total = 0
    total_unknown = 0
    for item in requested:
        wrote, unknown, note = _one(
            root, config, adapter, settings, agent, item, state, ids, sizes, dry_run=dry_run
        )
        total += wrote
        total_unknown += unknown
        label = f"{item.path.name}: {wrote} turn" if wrote == 1 else f"{item.path.name}: {wrote} turns"
        if unknown:
            label += f", {unknown} unknown"
        if note:
            label += f" ({note})"
        reports.append(label)
    if not dry_run:
        state["placed"] = True
        _save_state(root, adapter.name, state)
        _save_index(root, sizes, ids)
    return _result(adapter.name, agent, tuple(reports), total, dry_run=dry_run, placed=True, unknown=total_unknown)


def _one(
    root,
    config: Config,
    adapter: Any,
    settings: IntakeToolSettings,
    agent: str,
    transcript: Transcript,
    state: dict[str, Any],
    ids: dict[str, str],
    sizes: dict[str, int],
    *,
    dry_run: bool,
) -> tuple[int, int, str]:
    cursor = state["transcripts"].get(transcript.key) or {
        "offset": 0,
        "seeded": False,
        "unknown": 0,
        "internal": False,
    }
    record_shape = adapter.name == "jsonl"
    if cursor.get("internal") or transcript.internal or head_internal(transcript.path, record=record_shape):
        if not dry_run:
            _remember_cursor(
                state, transcript.key, offset=complete_end(transcript.path), seeded=False,
                unknown=int(cursor.get("unknown", 0)), internal=True,
            )
        return 0, 0, "session carries MEMORY_FOREST_INTERNAL"
    batch = adapter.read(transcript, int(cursor.get("offset", 0)))
    if batch.internal:
        if not dry_run:
            _remember_cursor(
                state, transcript.key, offset=batch.offset, seeded=False,
                unknown=int(cursor.get("unknown", 0)), internal=True,
            )
        return 0, 0, "session carries MEMORY_FOREST_INTERNAL"
    records: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for turn in batch.turns:
        record = _record(config, agent, adapter.name, transcript, turn)
        if record is None:
            continue
        if record["id"] in ids or record["id"] in seen:
            continue
        seen.add(record["id"])
        records.append((record["id"], record))
    if dry_run:
        return len(records), batch.unknown, ""
    by_file: dict[str, list[dict[str, Any]]] = {}
    for turn_id, record in records:
        relative = f"{layout.ISTM}/{agent}/{_day(record['at'], config)}.jsonl"
        by_file.setdefault(relative, []).append(record)
        ids[turn_id] = relative
    for relative, group in by_file.items():
        payload = "".join(_encode(item) for item in group)
        _append(root, relative, payload)
        sizes[relative] = layout.resolve(root, relative).stat().st_size
    _remember_cursor(
        state,
        transcript.key,
        offset=batch.offset,
        # A cursor placed at the end without reading stays seeded, so turning
        # import_earlier on later still rewinds it. Reading from the start clears it.
        seeded=bool(cursor.get("seeded")),
        unknown=int(cursor.get("unknown", 0)) + batch.unknown,
        internal=False,
    )
    return len(records), batch.unknown, ""


def _record(
    config: Config,
    agent: str,
    adapter_name: str,
    transcript: Transcript,
    turn: Turn,
) -> dict[str, Any] | None:
    role = turn.role
    if role not in {"owner", "agent", "tool"} and not (
        role.startswith("other:") and role.removeprefix("other:").strip()
    ):
        return None
    origin = turn.origin if turn.origin in ORIGINS else transcript.origin
    if origin not in ORIGINS:
        origin = "interactive"
    text = turn.text
    parts = turn.parts
    name = turn.name
    if role == "tool":
        if not name:
            name = "tool"
        if config.intake.tool_output != "keep" or not text:
            text = f"ran {name}"
            parts = ()
    masked, parts = _mask_parts(text, parts, config)
    at = turn.at if turn.at.tzinfo is not None else turn.at.replace(tzinfo=timezone.utc)
    record: dict[str, Any] = {
        "at": at.isoformat(),
        "id": _compose_id(agent, turn.session or transcript.session, turn),
        "origin": origin,
        "role": role,
        "session": turn.session or transcript.session,
        "source": {"adapter": adapter_name, "place": turn.place, "transcript": transcript.key},
        "text": masked,
    }
    if role == "tool":
        record["name"] = name
    if parts:
        record["parts"] = [{"end": part.end, "kind": part.kind, "start": part.start} for part in parts]
    return record


def _compose_id(agent: str, session: str, turn: Turn) -> str:
    token = turn.turn_id or ""
    if not token or any(character in token for character in "\r\n") or len(token) > 200:
        digest = hashlib.sha256(f"{turn.place}\0{turn.text}".encode("utf-8")).hexdigest()[:20]
        token = digest
    return f"{agent}:{session.replace(chr(10), '')}:{token}"


def _day(at_text: str, config: Config) -> str:
    parsed = datetime.fromisoformat(at_text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(config.zone).date().isoformat()


def _mask_parts(text: str, parts: tuple[Part, ...], config: Config) -> tuple[str, tuple[Part, ...]]:
    valid = []
    for part in parts:
        if part.kind not in PART_KINDS:
            continue
        start = max(0, min(len(text), part.start))
        end = max(start, min(len(text), part.end))
        if end > start:
            valid.append(Part(start, end, part.kind))
    if not valid:
        return _mask(text, config), ()
    cuts = {0, len(text)}
    for part in valid:
        cuts.add(part.start)
        cuts.add(part.end)
    ordered = sorted(cuts)
    pieces: list[str] = []
    spans: list[tuple[int, int, int, int]] = []
    cursor = 0
    for left, right in zip(ordered, ordered[1:]):
        piece = _mask(text[left:right], config)
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
    return "".join(pieces), tuple(mapped_parts)


def _mask(text: str, config: Config) -> str:
    return numbers.star_numbers(credentials.mask(text, config=config), config=config)[0]


def _encode(record: Mapping[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"


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


def _remember_cursor(
    state: dict[str, Any],
    key: str,
    *,
    offset: int,
    seeded: bool,
    unknown: int,
    internal: bool,
) -> None:
    state["transcripts"][key] = {
        "internal": internal,
        "offset": offset,
        "seeded": seeded,
        "unknown": unknown,
    }


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
    return Result(0, (head, *(f"  {line}" for line in reports)), data)


@contextmanager
def _intake_lock(root: str | os.PathLike[str]) -> Iterator[object | None]:
    try:
        with hold_lock(root, LOCK_NAME, wait_seconds=LOCK_WAIT_SECONDS, label="forest intake") as token:
            yield token
    except MemoryForestError as exc:
        if exc.code != "lock_busy":
            raise
        yield None


def _empty_state() -> dict[str, Any]:
    return {"placed": False, "transcripts": {}}


def _state_path(adapter_name: str) -> str:
    return f"{INTAKE_DIR}/{adapter_name}.json"


def _read_state(root: str | os.PathLike[str], adapter_name: str) -> dict[str, Any]:
    path = layout.resolve(root, _state_path(adapter_name))
    if not path.is_file() or path.is_symlink():
        return _empty_state()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MemoryForestError(
            "intake_state_invalid",
            "The intake cursor file is not valid JSON. It was not rewritten.",
            details={"adapter": adapter_name},
        ) from exc
    if not isinstance(value, dict) or not isinstance(value.get("transcripts"), dict):
        raise MemoryForestError(
            "intake_state_invalid",
            "The intake cursor file is not a cursor record. It was not rewritten.",
            details={"adapter": adapter_name},
        )
    value.setdefault("placed", False)
    return value


def _save_state(root: str | os.PathLike[str], adapter_name: str, state: dict[str, Any]) -> None:
    layout.ensure_folder(root, INTAKE_DIR)
    atomic_write(
        layout.resolve(root, _state_path(adapter_name)),
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _load_ids(root: str | os.PathLike[str], *, write: bool) -> tuple[dict[str, str], dict[str, int]]:
    sizes = _lane_sizes(root)
    path = layout.resolve(root, INDEX_FILE)
    stored: object = None
    if path.is_file() and not path.is_symlink():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            stored = None
    if isinstance(stored, dict) and stored.get("lanes") == sizes and isinstance(stored.get("ids"), dict):
        ids = {str(key): str(value) for key, value in stored["ids"].items()}
        return ids, sizes
    ids = _rebuild_ids(root)
    if write:
        _save_index(root, sizes, ids)
    return ids, sizes


def _save_index(root: str | os.PathLike[str], sizes: dict[str, int], ids: dict[str, str]) -> None:
    layout.ensure_folder(root, INTAKE_DIR)
    atomic_write(
        layout.resolve(root, INDEX_FILE),
        json.dumps({"ids": ids, "lanes": sizes}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _lane_sizes(root: str | os.PathLike[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for relative, path in _lane_files(root):
        found[relative] = path.stat().st_size
    return found


def _rebuild_ids(root: str | os.PathLike[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for relative, path in _lane_files(root):
        with path.open("rb") as handle:
            for raw in handle:
                if not raw.endswith(b"\n"):
                    continue
                from .adapters.common import parse_object

                value = parse_object(raw[:-1])
                if isinstance(value, dict) and isinstance(value.get("id"), str):
                    found[value["id"]] = relative
    return found


def _lane_files(root: str | os.PathLike[str]) -> list[tuple[str, Path]]:
    top = Path(root) / layout.ISTM
    if not top.is_dir() or top.is_symlink():
        return []
    found = []
    for agent in sorted(os.scandir(top), key=lambda entry: entry.name):
        if agent.name.startswith(".") or agent.is_symlink() or not agent.is_dir(follow_symlinks=False):
            continue
        for entry in sorted(os.scandir(agent.path), key=lambda item: item.name):
            if (
                entry.name.startswith(".")
                or not entry.name.endswith(".jsonl")
                or entry.is_symlink()
                or not entry.is_file(follow_symlinks=False)
            ):
                continue
            found.append((f"{layout.ISTM}/{agent.name}/{entry.name}", Path(entry.path)))
    return found


def _append(root: str | os.PathLike[str], relative: str, payload: str) -> None:
    parent = relative.rsplit("/", 1)[0]
    layout.ensure_folder(root, parent)
    path = layout.resolve(root, relative)
    created = not path.exists()
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        if created:
            os.fchmod(descriptor, 0o600)
        os.write(descriptor, payload.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if created:
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
