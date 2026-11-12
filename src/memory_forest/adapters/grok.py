"""The Grok adapter.

Read against grok 1.0.52. A session is a directory under
``$GROK_HOME/sessions/<encoded-cwd>/<session-id>/`` (or ``~/.grok/sessions``).
The conversation is ``updates.jsonl``: each line has ``method``, ``params`` and
``timestamp``. ``params.update.sessionUpdate`` is ``user_message_chunk``,
``agent_message_chunk``, ``agent_thought_chunk``, ``tool_call``,
``tool_call_update`` or ``turn_completed``. Chunks of one update are one turn.
A tool's name is ``params.update._meta["x.ai/tool"].name`` (or ``title``).
``summary.json`` holds ``info.id``, ``info.cwd``, ``session_kind`` and, for a
fork, ``parent_session_id``. ``prompt_context.json`` holds ``is_non_interactive``.

A line that is not one of those updates is skipped and counted. A last line
with no newline is left for the next read.

``session_kind`` ``headless`` or ``is_non_interactive`` is a headless run.
``parent_session_id``, or a hook whose event is a sub-agent, makes the whole
session one tool turn. The files do not store the process environment. A hook
sees ``MEMORY_FOREST_INTERNAL`` in its own environment. A sweep sees it when
``summary.json``, ``prompt_context.json`` or a line carries it as a key. A hook
sends ``sessionId`` and ``cwd`` and not a transcript path; the session directory
is the encoded working directory, or a directory of that session id.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote

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
    marked_internal,
    parse_object,
    parse_time,
    place_of,
    require_file,
    tool_home,
)


_SKIP_UPDATES = frozenset({"agent_thought_chunk", "turn_completed"})
_SUBAGENT_TOOLS = frozenset({"spawn_subagent", "spawn_agent", "Agent", "Task"})


class GrokAdapter:
    name = "grok"

    def discover(self, settings: IntakeToolSettings) -> list[Transcript]:
        root = _sessions_root(settings)
        if root is None:
            return []
        found = []
        for path in _updates(root):
            found.append(self._open(path))
        return found

    def open_path(self, path: Path, settings: IntakeToolSettings) -> Transcript:
        del settings
        return self._open(require_file(path))

    def hook(self, payload: dict, settings: IntakeToolSettings) -> Transcript:
        session = payload.get("sessionId") or payload.get("session_id")
        cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else ""
        if not isinstance(session, str) or not session:
            raise MemoryForestError(
                "hook_incomplete",
                "The hook payload has no session id.",
                details={"adapter": self.name},
            )
        path = _locate(settings, session, cwd)
        if path is None:
            raise MemoryForestError(
                "transcript_missing",
                "The hook's session has no transcript.",
                details={"adapter": self.name},
            )
        transcript = self._open(path)
        origin = transcript.origin
        event = payload.get("hookEventName") or payload.get("hook_event_name") or ""
        if isinstance(event, str) and "subagent" in event.casefold():
            origin = "subagent"
        return Transcript(transcript.key, transcript.path, session, cwd or transcript.cwd, origin, transcript.internal)

    def read(self, transcript: Transcript, offset: int) -> ReadBatch:
        lines, new_offset = complete_lines(transcript.path, offset)
        unknown = 0
        events: list[dict] = []
        for start, raw in lines:
            value = parse_object(raw)
            if value is None:
                continue
            if value is False or not isinstance(value, dict):
                unknown += 1
                continue
            update = _update(value)
            if update is None:
                unknown += 1
                continue
            kind = update.get("sessionUpdate")
            if not isinstance(kind, str):
                unknown += 1
                continue
            if kind in _SKIP_UPDATES:
                continue
            if kind not in {"user_message_chunk", "agent_message_chunk", "tool_call", "tool_call_update"}:
                unknown += 1
                continue
            at = parse_time(value.get("timestamp"))
            if at is None:
                at = parse_time(_meta(update).get("agentTimestampMs")) if isinstance(_meta(update), dict) else None
            if at is None:
                unknown += 1
                continue
            events.append({"kind": kind, "at": at, "start": start, "update": update, "turn_id": _event_id(value, update)})
        turns = _coalesce(events, transcript)
        if transcript.origin == "subagent" and turns:
            turns = [_one_subagent(turns, transcript)]
        if transcript.internal:
            return ReadBatch((), new_offset, unknown, internal=True)
        return ReadBatch(tuple(turns), new_offset, unknown, internal=False)

    def _open(self, path: Path) -> Transcript:
        folder = path.parent
        session = folder.name
        cwd = ""
        origin = "interactive"
        internal = False
        summary = _read_json(folder / "summary.json")
        if isinstance(summary, dict):
            info = summary.get("info") if isinstance(summary.get("info"), dict) else {}
            if isinstance(info.get("id"), str) and info["id"]:
                session = info["id"]
            if isinstance(info.get("cwd"), str):
                cwd = info["cwd"]
            if summary.get("session_kind") == "headless":
                origin = "headless"
            if summary.get("parent_session_id"):
                origin = "subagent"
            internal = marked_internal(summary)
        context = _read_json(folder / "prompt_context.json")
        if isinstance(context, dict):
            if context.get("is_non_interactive") is True and origin != "subagent":
                origin = "headless"
            if not cwd and isinstance(context.get("working_directory"), str):
                cwd = context["working_directory"]
            internal = internal or marked_internal(context)
        return Transcript(file_key(path), path, session, cwd, origin, internal)


def _sessions_root(settings: IntakeToolSettings) -> Path | None:
    if settings.path:
        return tool_home(override=settings.path)
    home = os.environ.get("GROK_HOME")
    if home:
        return tool_home(override=str(Path(home).expanduser() / "sessions"))
    return tool_home(".grok", "sessions")


def _updates(root: Path) -> list[Path]:
    found = []
    if not root.is_dir() or root.is_symlink():
        return found
    for group in sorted(root.iterdir(), key=lambda item: item.name):
        if not group.is_dir() or group.is_symlink() or group.name.startswith("."):
            continue
        for session in sorted(group.iterdir(), key=lambda item: item.name):
            if not session.is_dir() or session.is_symlink():
                continue
            path = session / "updates.jsonl"
            if path.is_file() and not path.is_symlink():
                found.append(path)
    return found


def _locate(settings: IntakeToolSettings, session: str, cwd: str) -> Path | None:
    if "/" in session or "\\" in session or len(session) > 200:
        return None
    root = _sessions_root(settings)
    if root is None:
        return None
    if cwd:
        direct = root / quote(cwd, safe="~") / session / "updates.jsonl"
        if direct.is_file() and not direct.is_symlink():
            return direct
    for path in _updates(root):
        if path.parent.name == session:
            return path
    return None


def _read_json(path: Path) -> object:
    if not path.is_file() or path.is_symlink():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _update(value: dict) -> dict | None:
    params = value.get("params")
    if not isinstance(params, dict):
        return None
    update = params.get("update")
    return update if isinstance(update, dict) else None


def _meta(update: dict) -> dict:
    # The line's _meta sits on the update; the tool record is under "x.ai/tool".
    raw = update.get("_meta")
    return raw if isinstance(raw, dict) else {}


def _event_id(value: dict, update: dict) -> str | None:
    meta = _meta(update)
    line_meta = value.get("params")
    if isinstance(line_meta, dict) and isinstance(line_meta.get("_meta"), dict):
        event = line_meta["_meta"].get("eventId")
        if isinstance(event, str) and event:
            return event
    event = meta.get("eventId")
    if isinstance(event, str) and event:
        return event
    tool_call = update.get("toolCallId")
    return tool_call if isinstance(tool_call, str) and tool_call else None


def _coalesce(events: list[dict], transcript: Transcript) -> list[Turn]:
    turns: list[Turn] = []
    pending: dict | None = None

    def flush_chunk() -> None:
        nonlocal pending
        if pending is None:
            return
        text = "".join(pending["texts"])
        role = "agent" if pending["kind"] == "agent_message_chunk" else "owner"
        if text:
            turns.append(
                Turn(
                    pending["turn_id"],
                    pending["at"],
                    transcript.session,
                    role,
                    text,
                    place_of(pending["start"]),
                    transcript.origin,
                    None,
                    tuple(pending["parts"]),
                )
            )
        pending = None

    def flush_tool() -> None:
        nonlocal pending_tool
        if pending_tool is None:
            return
        turns.append(pending_tool)
        pending_tool = None

    pending_tool: Turn | None = None
    for event in events:
        kind = event["kind"]
        update = event["update"]
        if kind in {"user_message_chunk", "agent_message_chunk"}:
            flush_tool()
            prompt = update.get("prompt_id") or update.get("promptId")
            if pending is not None and pending["kind"] == kind and pending["prompt"] == prompt:
                _add_text(pending, update)
                continue
            flush_chunk()
            pending = {"kind": kind, "prompt": prompt, "texts": [], "parts": [], "at": event["at"],
                       "start": event["start"], "turn_id": event["turn_id"]}
            _add_text(pending, update)
            continue
        flush_chunk()
        if kind == "tool_call":
            flush_tool()
            name = _tool_name(update)
            origin = "subagent" if name in _SUBAGENT_TOOLS or transcript.origin == "subagent" else transcript.origin
            pending_tool = Turn(
                event["turn_id"], event["at"], transcript.session, "tool", "", place_of(event["start"]), origin, name
            )
            continue
        if kind == "tool_call_update" and pending_tool is not None:
            output = _tool_output(update)
            if output:
                pending_tool = Turn(
                    pending_tool.turn_id,
                    pending_tool.at,
                    pending_tool.session,
                    "tool",
                    output,
                    pending_tool.place,
                    pending_tool.origin,
                    pending_tool.name,
                    (),
                )
            status = update.get("status")
            if status in {"completed", "failed"}:
                flush_tool()
            continue
        # An update with no call beside it is bookkeeping for a call already recorded.
    flush_chunk()
    flush_tool()
    return turns


def _add_text(pending: dict, update: dict) -> None:
    text, parts = _content_text(update.get("content"))
    cursor = sum(len(item) for item in pending["texts"])
    for part in parts:
        pending["parts"].append(Part(part.start + cursor, part.end + cursor, part.kind))
    pending["texts"].append(text)


def _content_text(content: object) -> tuple[str, tuple[Part, ...]]:
    if isinstance(content, str):
        return content, find_marks(content)
    if isinstance(content, dict):
        if content.get("type") in {"image", "resource", "resource_link"}:
            return "[file]", (Part(0, 6, "file"),)
        if isinstance(content.get("text"), str):
            text = content["text"]
            return text, find_marks(text)
        inner = content.get("content")
        if inner is not None and inner is not content:
            return _content_text(inner)
        return "", ()
    if isinstance(content, list):
        pieces: list[str] = []
        parts: list[Part] = []
        cursor = 0
        for item in content:
            text, found = _content_text(item)
            for part in found:
                parts.append(Part(part.start + cursor, part.end + cursor, part.kind))
            pieces.append(text)
            cursor += len(text)
        return "".join(pieces), tuple(parts)
    return "", ()


def _tool_name(update: dict) -> str:
    meta = _meta(update)
    tool = meta.get("x.ai/tool")
    if isinstance(tool, dict) and isinstance(tool.get("name"), str) and tool["name"]:
        return tool["name"]
    if isinstance(update.get("title"), str) and update["title"]:
        return update["title"]
    if isinstance(update.get("kind"), str) and update["kind"]:
        return update["kind"]
    return "tool"


def _tool_output(update: dict) -> str:
    raw = update.get("rawOutput")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for key in ("message", "content", "text", "stdout", "stderr"):
            value = raw.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, list) and value and all(isinstance(item, int) and 0 <= item <= 255 for item in value[:8]):
                try:
                    return bytes(value).decode("utf-8", "replace")
                except ValueError:
                    return ""
    content = update.get("content")
    text, _ = _content_text(content)
    return text


def _one_subagent(turns: list[Turn], transcript: Transcript) -> Turn:
    texts = [turn.text for turn in turns if turn.role == "agent" and turn.text]
    name = next((turn.name for turn in turns if turn.name), None) or "subagent"
    first = turns[0]
    return Turn(
        transcript.session,
        first.at,
        transcript.session,
        "tool",
        "\n".join(texts),
        first.place,
        "subagent",
        name,
        (),
    )


adapter = GrokAdapter()
