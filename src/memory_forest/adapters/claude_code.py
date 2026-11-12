"""The Claude Code adapter.

Read against Claude Code 2.1.318. A session is one JSONL file under
``~/.claude/projects/<encoded-folder>/``. A line is an object whose ``type`` is
``user``, ``assistant`` or ``attachment``, among bookkeeping types that are
understood and not turns. A ``user`` line carries ``message.content`` (a string
or a list of ``text`` and ``tool_result`` blocks), ``sessionId``, ``timestamp``,
``cwd``, ``entrypoint`` and ``isSidechain``. An ``assistant`` line carries
``text`` and ``tool_use`` blocks (``name``, ``id``, ``input``). An attachment of
type ``file`` carries ``content``. A hook's JSON on stdin carries
``transcript_path``, ``session_id`` and ``cwd``.

A line whose ``type`` is not one of the shapes this module knows is skipped and
counted. A last line with no newline is left for the next read.

``entrypoint`` ``sdk-cli`` is a headless run. ``isSidechain`` lines, and a hook
that carries ``agent_id``, are a sub-agent: they are recorded as one tool turn,
not as the owner. The transcript does not store the process environment. A hook
sees ``MEMORY_FOREST_INTERNAL`` in its own environment, because the hook is a
child of the session. A sweep sees the marker only when a line carries it as a
key (it is not in the environment attachment, which holds the platform and the
working directory, not the process environment).
"""

from __future__ import annotations

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
    harness_only,
        head_internal,
        head_objects,
        parse_object,
    parse_time,
    place_of,
    require_file,
    tool_home,
    walk_jsonl,
)


# Types observed in 2.1.318 that are not turns. A type absent from this set and
# from the turn types is an unknown shape.
_SKIP = frozenset(
    {
        "agent-name",
        "ai-title",
        "api-request",
        "api-request-blob",
        "api-request-shape",
        "atis-latch",
        "bridge-session",
        "compact-boundary",
        "cost-state",
        "custom-title",
        "file-history-snapshot",
        "last-prompt",
        "mode",
        "permission-mode",
        "pr-link",
        "progress",
        "queue-operation",
        "rate-limit-info",
        "summary",
        "system",
    }
)
_HEADLESS = frozenset({"sdk-cli", "sdk", "print", "noninteractive"})
_SUBAGENT_TOOLS = frozenset({"Agent", "Task", "spawn_agent", "spawn_subagent"})


class ClaudeCodeAdapter:
    name = "claude-code"

    def discover(self, settings: IntakeToolSettings) -> list[Transcript]:
        root = tool_home(".claude", "projects", override=settings.path)
        if root is None:
            return []
        return [self._open(path) for path in walk_jsonl(root, lambda name: name.endswith(".jsonl"))]

    def open_path(self, path: Path, settings: IntakeToolSettings) -> Transcript:
        del settings
        return self._open(require_file(path))

    def hook(self, payload: dict, settings: IntakeToolSettings) -> Transcript:
        del settings
        raw = payload.get("transcript_path") or payload.get("transcriptPath")
        if not isinstance(raw, str) or not raw:
            raise MemoryForestError(
                "hook_incomplete",
                "The hook payload has no transcript path.",
                details={"adapter": self.name},
            )
        transcript = self._open(require_file(Path(raw)))
        session = payload.get("session_id") or payload.get("sessionId") or transcript.session
        cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else transcript.cwd
        origin = transcript.origin
        if payload.get("agent_id") or payload.get("agentId"):
            origin = "subagent"
        return Transcript(transcript.key, transcript.path, str(session), cwd, origin, transcript.internal)

    def read(self, transcript: Transcript, offset: int) -> ReadBatch:
        lines, new_offset = complete_lines(transcript.path, offset)
        unknown = 0
        pending: dict[str, Turn] = {}
        order: list[Turn] = []
        sidechain: list[Turn] = []
        side_name = "subagent"

        def flush_sidechain() -> None:
            if not sidechain:
                return
            first = sidechain[0]
            texts = [item.text for item in sidechain if item.role == "agent" and item.text]
            order.append(
                Turn(
                    first.turn_id,
                    first.at,
                    first.session,
                    "tool",
                    "\n".join(texts),
                    first.place,
                    "subagent",
                    side_name or "subagent",
                    (),
                )
            )
            sidechain.clear()

        for start, raw in lines:
            value = parse_object(raw)
            if value is None:
                continue
            if value is False or not isinstance(value, dict):
                unknown += 1
                continue
            kind = value.get("type")
            if not isinstance(kind, str):
                unknown += 1
                continue
            if kind in _SKIP:
                continue
            if kind == "attachment":
                flush_sidechain()
                turn = _attachment(value, start, transcript)
                if turn is not None:
                    order.append(turn)
                continue
            if kind not in {"user", "assistant"}:
                unknown += 1
                continue
            built = _message_turns(value, start, transcript)
            results = _results(value)
            side = value.get("isSidechain") is True or transcript.origin == "subagent"
            if side:
                if isinstance(value.get("agentName"), str) and value["agentName"]:
                    side_name = value["agentName"]
                sidechain.extend(built)
                for tool_id, output in results:
                    _fill(pending, order, tool_id, output)
                continue
            flush_sidechain()
            for turn in built:
                order.append(turn)
                if turn.role == "tool" and turn.turn_id:
                    pending[turn.turn_id] = turn
            for tool_id, output in results:
                _fill(pending, order, tool_id, output)
        flush_sidechain()
        if head_internal(transcript.path):
            return ReadBatch((), new_offset, unknown, internal=True)
        return ReadBatch(tuple(order), new_offset, unknown, internal=False)

    def _open(self, path: Path) -> Transcript:
        session = path.stem
        cwd = ""
        origin = "interactive"
        for value in head_objects(path):
            if isinstance(value.get("sessionId"), str) and value["sessionId"]:
                session = value["sessionId"]
            if isinstance(value.get("cwd"), str) and value["cwd"]:
                cwd = value["cwd"]
            entry = value.get("entrypoint")
            if isinstance(entry, str) and entry in _HEADLESS:
                origin = "headless"
            if value.get("isSidechain") is True:
                origin = "subagent"
            if cwd and session:
                break
        return Transcript(file_key(path), path, session, cwd, origin, head_internal(path))


def _fill(pending: dict[str, Turn], order: list[Turn], tool_id: str, output: str) -> None:
    tool = pending.get(tool_id)
    if tool is None or not output or tool.text:
        return
    order[order.index(tool)] = Turn(
        tool.turn_id, tool.at, tool.session, tool.role, output, tool.place, tool.origin, tool.name, ()
    )


def _message_turns(value: dict, start: int, transcript: Transcript) -> list[Turn]:
    message = value.get("message")
    if not isinstance(message, dict):
        return []
    at = parse_time(value.get("timestamp"))
    if at is None:
        return []
    session = value.get("sessionId") if isinstance(value.get("sessionId"), str) else transcript.session
    origin = "subagent" if value.get("isSidechain") is True or transcript.origin == "subagent" else transcript.origin
    turn_id = value.get("uuid") if isinstance(value.get("uuid"), str) else None
    role = message.get("role")
    content = message.get("content")
    found: list[Turn] = []
    if role == "assistant":
        text, tools = _assistant(content)
        if text and not harness_only(text):
            found.append(Turn(turn_id, at, str(session), "agent", text, place_of(start), origin))
        for index, (name, tool_id) in enumerate(tools, start=1):
            tool_origin = "subagent" if name in _SUBAGENT_TOOLS else origin
            found.append(
                Turn(tool_id or turn_id, at, str(session), "tool", "", place_of(start, index), tool_origin, name)
            )
        return found
    if role == "user":
        text, parts = _user_text(content)
        if text is None:
            return []
        if (not text or harness_only(text)) and not parts:
            return []
        if not text and not parts:
            return []
        return [Turn(turn_id, at, str(session), "owner", text, place_of(start), origin, None, parts)]
    if isinstance(role, str) and role:
        text, parts = _user_text(content)
        if text:
            return [Turn(turn_id, at, str(session), f"other:{role}", text, place_of(start), origin, None, parts)]
    return found


def _assistant(content: object) -> tuple[str, list[tuple[str, str]]]:
    if isinstance(content, str):
        return content, []
    texts: list[str] = []
    tools: list[tuple[str, str]] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text" and isinstance(block.get("text"), str):
                texts.append(block["text"])
            elif kind == "tool_use":
                name = block.get("name") if isinstance(block.get("name"), str) else "tool"
                tool_id = block.get("id") if isinstance(block.get("id"), str) else ""
                tools.append((name, tool_id))
    return "\n".join(texts), tools


def _user_text(content: object) -> tuple[str, tuple[Part, ...]] | tuple[None, tuple[Part, ...]]:
    """Owner text, or ``(None, ())`` when the line is only tool results."""
    if isinstance(content, str):
        return content, find_marks(content)
    if not isinstance(content, list):
        return None, ()
    only_results = True
    pieces: list[str] = []
    parts: list[Part] = []
    cursor = 0
    for block in content:
        if not isinstance(block, dict):
            only_results = False
            continue
        if block.get("type") == "tool_result":
            continue
        only_results = False
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            text = block["text"]
            for part in find_marks(text):
                parts.append(Part(part.start + cursor, part.end + cursor, part.kind))
            pieces.append(text)
            cursor += len(text)
            if cursor and pieces:
                # Blocks are one text. A join without a separator keeps the spans exact.
                pass
        elif block.get("type") in {"image", "document"}:
            snippet = "[file]"
            parts.append(Part(cursor, cursor + len(snippet), "file"))
            pieces.append(snippet)
            cursor += len(snippet)
    if only_results:
        return None, ()
    text = "".join(pieces)
    return text, tuple(parts)


def _results(value: dict) -> list[tuple[str, str]]:
    message = value.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []
    found = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        tool_id = block.get("tool_use_id") if isinstance(block.get("tool_use_id"), str) else ""
        found.append((tool_id, _result_text(block.get("content"))))
    return found


def _result_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        bits = []
        for item in content:
            if isinstance(item, str):
                bits.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                bits.append(item["text"])
        return "\n".join(bits)
    return ""


def _attachment(value: dict, start: int, transcript: Transcript) -> Turn | None:
    attachment = value.get("attachment")
    if not isinstance(attachment, dict) or attachment.get("type") != "file":
        return None
    content = attachment.get("content")
    if not isinstance(content, str) or not content:
        return None
    at = parse_time(value.get("timestamp"))
    if at is None:
        return None
    session = value.get("sessionId") if isinstance(value.get("sessionId"), str) else transcript.session
    turn_id = value.get("uuid") if isinstance(value.get("uuid"), str) else None
    return Turn(
        turn_id,
        at,
        str(session),
        "owner",
        content,
        place_of(start),
        transcript.origin,
        None,
        (Part(0, len(content), "file"),),
    )


adapter = ClaudeCodeAdapter()
