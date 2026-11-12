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

``thread_source`` ``subagent`` (or ``source.subagent``, or ``parent_thread_id``)
is a sub-agent: the whole session is one tool turn. ``thread_source``
``automation``, ``exec``, ``github`` or ``mcp`` is headless. Anything else,
including ``cli`` and ``vscode``, is interactive. The rollout does not store the
process environment. A hook sees ``MEMORY_FOREST_INTERNAL`` in its own
environment. A sweep sees it when ``session_meta`` (or another line) carries it
as a key. A hook sends ``transcript_path`` (which may be null), ``session_id``
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
    file_key,
    find_marks,
    head_internal,
    head_objects,
    parse_object,
    parse_time,
    place_of,
    require_file,
    tool_home,
    walk_jsonl,
)


_SKIP = frozenset({"event_msg", "token_usage_record", "turn_context", "world_state"})
_ITEM_SKIP = frozenset({"additional_tools", "reasoning"})
_HEADLESS = frozenset({"automation", "exec", "github", "mcp"})
_SUBAGENT_TOOLS = frozenset({"spawn_agent", "spawn_subagent", "Agent", "Task"})


class CodexAdapter:
    name = "codex"

    def discover(self, settings: IntakeToolSettings) -> list[Transcript]:
        root = _sessions_root(settings)
        if root is None:
            return []
        found = []
        for path in walk_jsonl(root, lambda name: name.startswith("rollout-") and name.endswith(".jsonl")):
            transcript = self._open(path)
            found.append(transcript)
        return found

    def open_path(self, path: Path, settings: IntakeToolSettings) -> Transcript:
        del settings
        return self._open(require_file(path))

    def hook(self, payload: dict, settings: IntakeToolSettings) -> Transcript:
        raw = payload.get("transcript_path") or payload.get("transcriptPath")
        path: Path | None = None
        if isinstance(raw, str) and raw:
            path = require_file(Path(raw))
        else:
            session = payload.get("session_id") or payload.get("sessionId")
            root = _sessions_root(settings)
            if isinstance(session, str) and root is not None:
                path = _find_rollout(root, session)
        if path is None:
            raise MemoryForestError(
                "hook_incomplete",
                "The hook payload has no transcript path.",
                details={"adapter": self.name},
            )
        transcript = self._open(path)
        cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else transcript.cwd
        session = payload.get("session_id") or payload.get("sessionId") or transcript.session
        return Transcript(transcript.key, transcript.path, str(session), cwd, transcript.origin, transcript.internal)

    def read(self, transcript: Transcript, offset: int) -> ReadBatch:
        lines, new_offset = complete_lines(transcript.path, offset)
        unknown = 0
        order: list[Turn] = []
        pending: dict[str, Turn] = {}
        for start, raw in lines:
            value = parse_object(raw)
            if value is None:
                continue
            if value is False or not isinstance(value, dict):
                unknown += 1
                continue
            kind = value.get("type")
            if kind in _SKIP or kind == "session_meta":
                continue
            if kind != "response_item":
                unknown += 1
                continue
            payload = value.get("payload")
            if not isinstance(payload, dict):
                unknown += 1
                continue
            at = parse_time(value.get("timestamp")) or parse_time(payload.get("timestamp"))
            if at is None:
                unknown += 1
                continue
            item = payload.get("type")
            if item in _ITEM_SKIP:
                continue
            if item == "message":
                turn = _message(payload, at, start, transcript)
                if turn is not None:
                    order.append(turn)
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
        if transcript.origin == "subagent" and order:
            order = [_one_subagent(order, transcript)]
        if head_internal(transcript.path):
            return ReadBatch((), new_offset, unknown, internal=True)
        return ReadBatch(tuple(order), new_offset, unknown, internal=False)

    def _open(self, path: Path) -> Transcript:
        session = path.stem
        cwd = ""
        origin = "interactive"
        for value in head_objects(path):
            if value.get("type") != "session_meta":
                continue
            payload = value.get("payload") if isinstance(value.get("payload"), dict) else {}
            if isinstance(payload.get("id"), str) and payload["id"]:
                session = payload["id"]
            elif isinstance(payload.get("session_id"), str) and payload["session_id"]:
                session = payload["session_id"]
            if isinstance(payload.get("cwd"), str):
                cwd = payload["cwd"]
            origin = _origin(payload)
            break
        return Transcript(file_key(path), path, session, cwd, origin, head_internal(path))


def _sessions_root(settings: IntakeToolSettings) -> Path | None:
    if settings.path:
        return tool_home(override=settings.path)
    home = os.environ.get("CODEX_HOME")
    if home:
        return tool_home(override=str(Path(home).expanduser() / "sessions"))
    return tool_home(".codex", "sessions")


def _find_rollout(root: Path, session_id: str) -> Path | None:
    if not session_id or "/" in session_id or "\\" in session_id or len(session_id) > 200:
        return None
    for path in walk_jsonl(root, lambda name: name.startswith("rollout-") and session_id in name and name.endswith(".jsonl")):
        return path
    return None


def _origin(payload: dict) -> str:
    source = payload.get("source")
    thread = payload.get("thread_source")
    if (
        thread == "subagent"
        or payload.get("parent_thread_id")
        or (isinstance(source, dict) and "subagent" in source)
    ):
        return "subagent"
    if thread in _HEADLESS or (isinstance(source, str) and source in _HEADLESS):
        return "headless"
    return "interactive"


def _message(payload: dict, at, start: int, transcript: Transcript) -> Turn | None:
    role = payload.get("role")
    session = transcript.session
    origin = transcript.origin
    turn_id = payload.get("id") if isinstance(payload.get("id"), str) else None
    if role == "developer":
        return None
    if role == "assistant":
        text = _join(payload.get("content"), {"output_text"})
        if not text:
            return None
        return Turn(turn_id, at, session, "agent", text, place_of(start), origin)
    if role == "user":
        text, parts = _user_pieces(payload)
        if not text and not parts:
            return None
        return Turn(turn_id, at, session, "owner", text, place_of(start), origin, None, parts)
    if isinstance(role, str) and role:
        text = _join(payload.get("content"), {"input_text", "output_text"})
        if not text:
            return None
        return Turn(turn_id, at, session, f"other:{role}", text, place_of(start), origin)
    return None


def _user_pieces(payload: dict) -> tuple[str, tuple[Part, ...]]:
    content = payload.get("content")
    if not isinstance(content, list):
        return "", ()
    meta = payload.get("internal_chat_message_metadata_passthrough")
    rows = meta.get("content_item_metadata") if isinstance(meta, dict) else None
    pieces: list[str] = []
    parts: list[Part] = []
    cursor = 0
    for index, item in enumerate(content):
        if not isinstance(item, dict):
            continue
        provenance = _provenance(rows, index)
        kind = item.get("type")
        if kind == "input_text" and isinstance(item.get("text"), str):
            if provenance not in (None, "user"):
                continue
            text = item["text"]
            if pieces:
                pieces.append("\n")
                cursor += 1
            for part in find_marks(text):
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
    return "".join(pieces), tuple(parts)


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


def _one_subagent(turns: list[Turn], transcript: Transcript) -> Turn:
    texts = [turn.text for turn in turns if turn.role == "agent" and turn.text]
    first = turns[0]
    return Turn(
        transcript.session,
        first.at,
        transcript.session,
        "tool",
        "\n".join(texts),
        first.place,
        "subagent",
        "subagent",
        (),
    )


adapter = CodexAdapter()
