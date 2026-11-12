"""The Codex CLI adapter.

Read against codex-cli 0.167.2. A session is a ``rollout-*.jsonl`` file under
``$CODEX_HOME/sessions`` (or ``~/.codex/sessions``) in ``YYYY/MM/DD/``. Each line
has ``type``, ``timestamp`` and ``payload``. ``session_meta`` names the session
(``id``, ``cwd``, ``source``, ``thread_source``, ``parent_thread_id``). Turns are
read from ``response_item``: a ``message`` (``role``, ``content[].type`` of
``input_text`` or ``output_text``), a ``custom_tool_call`` (``name``,
``call_id``) and its ``custom_tool_call_output``. A user message may carry
``internal_chat_message_metadata_passthrough.content_item_metadata[].provenance.type``;
only ``user`` (or an item with no provenance) is the owner's text. ``event_msg``,
``turn_context``, ``world_state`` and ``token_usage_record`` repeat or decorate
the same session and are not turns.

A line whose ``type`` is not one of those is skipped and counted. A last line
with no newline is left for the next read.

``thread_source`` ``subagent`` (or ``source.subagent``) is a sub-agent: each
message keeps its own id and is a tool turn. A ``parent_thread_id`` alone is
not. ``thread_source`` ``automation``, ``exec``, ``github`` or ``mcp`` is
headless. ``cli``, ``vscode`` and ``user`` are the owner's own client.
Anything else is ``unknown``, and the raw value is kept. The rollout does not store the
process environment. A hook sees ``MEMORY_FOREST_INTERNAL`` in its own
environment. A sweep sees a stage session by its first owner turn, which opens
with the marker line. Read again against the rollouts on a development machine:
every user item carries an ``id``, provenance is ``user``, ``harness``,
``agents_md`` or ``tool``, and the blocks that open with a tag open with
``environment_context``, ``subagent_notification`` or ``turn_aborted``. A hook sends ``transcript_path`` (which may be null), ``session_id``
and ``cwd``; when the path is null the session id is looked up in the rollout
file name.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..config import IntakeToolSettings
from ..errors import MemoryForestError
from .common import (
    Part,
    ReadBatch,
    Transcript,
    Turn,
    complete_lines,
    confine_file,
    counts_of,
    HOOK_WALK_FILES,
    find_marks,
    harness_only,
    harness_parts,
    head_objects,
    name_has_id,
    parse_object,
    parse_time,
    place_of,
    relative_key,
    session_token,
    stated_version,
    tool_home,
    walk_jsonl,
)


_SKIP = frozenset({"event_msg", "token_usage_record", "turn_context", "world_state"})
_ITEM_SKIP = frozenset({"additional_tools", "reasoning"})
_HEADLESS = frozenset({"automation", "exec", "github", "mcp"})
# The owner's own client, and a thread the transcript marks as the user's.
_INTERACTIVE = frozenset({"cli", "vscode", "user"})
_HARNESS_PROVENANCE = frozenset({"agents_md", "developer_instructions", "harness", "model"})
_SUBAGENT_TOOLS = frozenset({"spawn_agent", "spawn_subagent", "Agent", "Task"})


class CodexAdapter:
    name = "codex"
    # ``payload.id`` and ``call_id`` are opaque tokens, unique without the session.
    ids_stand_alone = True

    def discover(self, settings: IntakeToolSettings) -> list[Transcript]:
        root = _sessions_root(settings)
        if root is None:
            return []
        return [
            self._open(path, root)
            for path in walk_jsonl(root, lambda name: name.startswith("rollout-") and name.endswith(".jsonl"))
        ]

    def open_path(self, path: Path, settings: IntakeToolSettings) -> Transcript:
        root = _sessions_root(settings)
        if root is None:
            raise MemoryForestError(
                "transcript_missing",
                "The transcript root is not a directory.",
                details={"adapter": self.name},
            )
        return self._open(confine_file(str(path), root), root)

    def hook(self, payload: dict, settings: IntakeToolSettings) -> Transcript:
        """The transcript the hook names. The file body is not read."""
        root = _sessions_root(settings)
        if root is None:
            raise MemoryForestError(
                "transcript_missing",
                "The transcript root is not a directory.",
                details={"adapter": self.name},
            )
        raw = payload.get("transcript_path") or payload.get("transcriptPath")
        path: Path | None = None
        if isinstance(raw, str) and raw:
            path = confine_file(raw, root)
        else:
            session = session_token(payload.get("session_id") or payload.get("sessionId"))
            if session is not None:
                path = _find_rollout(root, session, max_files=HOOK_WALK_FILES)
        if path is None:
            raise MemoryForestError(
                "hook_incomplete",
                "The hook payload has no transcript path.",
                details={"adapter": self.name},
            )
        session = session_token(payload.get("session_id") or payload.get("sessionId")) or path.stem
        cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else ""
        return Transcript(relative_key(path, root), path, session, cwd, "unknown", described=False)

    def transcript_root(self, settings: IntakeToolSettings) -> Path | None:
        return _sessions_root(settings)

    def describe(self, transcript: Transcript, settings: IntakeToolSettings) -> Transcript:
        root = _sessions_root(settings)
        if root is None:
            return transcript
        return self._open(transcript.path, root)

    def read(self, transcript: Transcript, offset: int, carry: dict | None = None, max_bytes: int | None = None) -> ReadBatch:
        del carry
        lines, new_offset = complete_lines(transcript.path, offset, max_bytes=max_bytes)
        unknown = 0
        dropped: dict[str, int] = {}
        faults: list[tuple[str, str]] = []
        order: list[Turn] = []
        pending: dict[str, Turn] = {}
        internal = transcript.internal
        for start, raw in lines:
            place = place_of(start)
            try:
                value = parse_object(raw)
                if value is None:
                    continue
                if value is False or not isinstance(value, dict):
                    unknown += 1
                    dropped["invalid"] = dropped.get("invalid", 0) + 1
                    continue
                kind = value.get("type")
                if kind == "compacted":
                    moment = parse_time(value.get("timestamp"))
                    if moment is None:
                        dropped["compacted"] = dropped.get("compacted", 0) + 1
                    else:
                        order.append(
                            Turn(None, moment, transcript.session, "system", "compaction", place_of(start), transcript.origin)
                        )
                    continue
                if kind in _SKIP or kind == "session_meta":
                    label = kind if isinstance(kind, str) else "invalid"
                    dropped[label] = dropped.get(label, 0) + 1
                    continue
                if kind != "response_item":
                    unknown += 1
                    label = kind if isinstance(kind, str) else "invalid"
                    dropped[label] = dropped.get(label, 0) + 1
                    continue
                payload = value.get("payload")
                if not isinstance(payload, dict):
                    unknown += 1
                    dropped["invalid"] = dropped.get("invalid", 0) + 1
                    continue
                at = parse_time(value.get("timestamp")) or parse_time(payload.get("timestamp"))
                if at is None:
                    unknown += 1
                    dropped["invalid"] = dropped.get("invalid", 0) + 1
                    continue
                item = payload.get("type")
                if item in _ITEM_SKIP:
                    label = item if isinstance(item, str) else "item"
                    dropped[label] = dropped.get(label, 0) + 1
                    continue
                if item == "message":
                    turn = _message(payload, at, start, transcript)
                    if turn is not None:
                        order.append(turn)
                    else:
                        dropped["message"] = dropped.get("message", 0) + 1
                    continue
                if item in {"custom_tool_call", "function_call"}:
                    turn = _tool_call(payload, at, start, transcript)
                    order.append(turn)
                    if turn.turn_id:
                        pending[turn.turn_id] = turn
                    continue
                if item in {"custom_tool_call_output", "function_call_output"}:
                    call_id = payload.get("call_id") if isinstance(payload.get("call_id"), str) else ""
                    output = _output_text(payload.get("output"))
                    tool = pending.get(call_id)
                    if tool is not None and output:
                        order[order.index(tool)] = Turn(
                            tool.turn_id, tool.at, tool.session, "tool", output, tool.place, tool.origin, tool.name, ()
                        )
                    continue
                unknown += 1
                label = item if isinstance(item, str) else "response_item"
                dropped[label] = dropped.get(label, 0) + 1
            except KeyboardInterrupt:
                raise
            except BaseException as exc:
                faults.append((exc.__class__.__name__, place))
                dropped[exc.__class__.__name__] = dropped.get(exc.__class__.__name__, 0) + 1
        if transcript.origin == "subagent" and order:
            order = _as_subagent_turns(order)
        if internal:
            return ReadBatch((), new_offset, unknown, True, counts_of(dropped), tuple(faults))
        return ReadBatch(tuple(order), new_offset, unknown, False, counts_of(dropped), tuple(faults))

    def _open(self, path: Path, root: Path) -> Transcript:
        session = path.stem
        cwd = ""
        origin = "unknown"
        raw = ""
        parent = ""
        version = ""
        for value in head_objects(path):
            if not version:
                version = stated_version(value)
            if value.get("type") != "session_meta":
                continue
            payload = value.get("payload") if isinstance(value.get("payload"), dict) else {}
            if isinstance(payload.get("id"), str) and payload["id"]:
                session = payload["id"]
            elif isinstance(payload.get("session_id"), str) and payload["session_id"]:
                session = payload["session_id"]
            if isinstance(payload.get("cwd"), str):
                cwd = payload["cwd"]
            origin, raw, parent = _origin(payload)
            if not version:
                version = stated_version(payload)
            break
        return Transcript(
            relative_key(path, root), path, session, cwd, origin, False, raw, version, True, parent
        )


def _sessions_root(settings: IntakeToolSettings) -> Path | None:
    if settings.path:
        return tool_home(override=settings.path)
    home = os.environ.get("CODEX_HOME")
    if home:
        return tool_home(override=str(Path(home).expanduser() / "sessions"))
    return tool_home(".codex", "sessions")


def _find_rollout(root: Path, session_id: str, *, max_files: int | None = None) -> Path | None:
    """The rollout named ``session_id``. A hook stops after ``max_files`` and leaves the rest to a sweep."""
    token = session_token(session_id)
    if token is None:
        return None
    seen = 0
    if not root.is_dir() or root.is_symlink():
        return None
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if not name.startswith(".") and not Path(dirpath, name).is_symlink()]
        for name in sorted(files):
            if name.startswith(".") or not (name.startswith("rollout-") and name.endswith(".jsonl")):
                continue
            seen += 1
            if max_files is not None and seen > max_files:
                raise MemoryForestError(
                    "transcript_deferred",
                    "The hook left this session for a sweep.",
                    details={"adapter": "codex"},
                )
            if not name_has_id(name, token):
                continue
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file():
                continue
            return path
    return None


def _origin(payload: dict) -> tuple[str, str, str]:
    """Origin, the raw thread source, and a parent id. A parent id alone is not a sub-agent."""
    source = payload.get("source")
    thread = payload.get("thread_source") if isinstance(payload.get("thread_source"), str) else ""
    raw = thread or (source if isinstance(source, str) else "")
    parent = payload.get("parent_thread_id") if isinstance(payload.get("parent_thread_id"), str) else ""
    if thread == "subagent" or (isinstance(source, dict) and "subagent" in source):
        return "subagent", raw, parent
    if thread in _HEADLESS or (isinstance(source, str) and source in _HEADLESS):
        return "headless", raw, parent
    if thread in _INTERACTIVE or (isinstance(source, str) and source in _INTERACTIVE):
        return "interactive", raw, parent
    return "unknown", raw, parent


def _message(payload: dict, at, start: int, transcript: Transcript) -> Turn | None:
    role = payload.get("role")
    session = transcript.session
    origin = transcript.origin
    turn_id = payload.get("id") if isinstance(payload.get("id"), str) else None
    if role == "developer":
        text = _join(payload.get("content"), {"input_text", "output_text"})
        if not text:
            return None
        return Turn(turn_id, at, session, "system", text, place_of(start), origin)
    if role == "assistant":
        text = _join(payload.get("content"), {"output_text"})
        if not text:
            return None
        return Turn(turn_id, at, session, "agent", text, place_of(start), origin)
    if role == "user":
        text, parts, owner = _user_pieces(payload)
        if not text and not parts:
            return None
        role_name = "owner" if owner else "system"
        return Turn(turn_id, at, session, role_name, text, place_of(start), origin, None, parts)
    if isinstance(role, str) and role:
        text = _join(payload.get("content"), {"input_text", "output_text"})
        if not text:
            return None
        return Turn(turn_id, at, session, f"other:{role}", text, place_of(start), origin)
    return None


def _user_pieces(payload: dict) -> tuple[str, tuple[Part, ...], bool]:
    content = payload.get("content")
    if not isinstance(content, list):
        return "", (), False
    meta = payload.get("internal_chat_message_metadata_passthrough")
    rows = meta.get("content_item_metadata") if isinstance(meta, dict) else None
    pieces: list[str] = []
    parts: list[Part] = []
    cursor = 0
    owner = False
    for index, item in enumerate(content):
        if not isinstance(item, dict):
            continue
        provenance = _provenance(rows, index)
        kind = item.get("type")
        if kind == "input_text" and isinstance(item.get("text"), str):
            text = item["text"]
            # Only ``user`` provenance, or none, is the owner's: ``tool``, ``harness`` and any other kind is not.
            harness = (provenance is not None and provenance != "user") or provenance in _HARNESS_PROVENANCE \
                or harness_only(text)
            if provenance == "user" or (provenance is None and not harness):
                owner = True
            if pieces:
                pieces.append("\n")
                cursor += 1
            if harness:
                parts.append(Part(cursor, cursor + len(text), "harness"))
            else:
                for part in find_marks(text):
                    parts.append(Part(part.start + cursor, part.end + cursor, part.kind))
                for part in harness_parts(text):
                    parts.append(Part(part.start + cursor, part.end + cursor, part.kind))
            pieces.append(text)
            cursor += len(text)
        elif kind in {"input_file", "input_image", "image"}:
            snippet = "[file]"
            if pieces:
                pieces.append("\n")
                cursor += 1
            parts.append(Part(cursor, cursor + len(snippet), "file"))
            pieces.append(snippet)
            cursor += len(snippet)
            owner = True
    return "".join(pieces), tuple(parts), owner


def _provenance(rows: object, index: int) -> str | None:
    if not isinstance(rows, list) or index >= len(rows) or not isinstance(rows[index], dict):
        return None
    provenance = rows[index].get("provenance")
    if isinstance(provenance, dict) and isinstance(provenance.get("type"), str):
        return provenance["type"]
    return None


def _join(content: object, kinds: set[str]) -> str:
    if not isinstance(content, list):
        return ""
    pieces = []
    for item in content:
        if isinstance(item, dict) and item.get("type") in kinds and isinstance(item.get("text"), str):
            pieces.append(item["text"])
    return "\n".join(pieces)


def _tool_call(payload: dict, at, start: int, transcript: Transcript) -> Turn:
    name = payload.get("name") if isinstance(payload.get("name"), str) and payload["name"] else "tool"
    call_id = payload.get("call_id") if isinstance(payload.get("call_id"), str) else None
    if not call_id and isinstance(payload.get("id"), str):
        call_id = payload["id"]
    origin = "subagent" if name in _SUBAGENT_TOOLS or transcript.origin == "subagent" else transcript.origin
    return Turn(call_id, at, transcript.session, "tool", "", place_of(start), origin, name)


def _output_text(output: object) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        return _join(output, {"input_text", "output_text", "text"})
    if isinstance(output, dict):
        for key in ("text", "content", "message", "stdout"):
            value = output.get(key)
            if isinstance(value, str):
                return value
    return ""


def _as_subagent_turns(turns: list[Turn]) -> list[Turn]:
    """Each message keeps its own id. The session id is not the id of the whole session."""
    found = []
    for turn in turns:
        if turn.role == "system":
            found.append(turn)
            continue
        found.append(
            Turn(
                turn.turn_id,
                turn.at,
                turn.session,
                "tool",
                turn.text,
                turn.place,
                "subagent",
                turn.name or "subagent",
                turn.parts,
                turn.arguments,
            )
        )
    return found


adapter = CodexAdapter()
