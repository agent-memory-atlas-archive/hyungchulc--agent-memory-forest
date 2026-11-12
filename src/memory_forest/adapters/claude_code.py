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

``entrypoint`` ``cli`` is the owner's own client. ``sdk-cli``, ``sdk``,
``print`` and ``noninteractive`` are headless. Any other entrypoint is
``unknown``, and the raw value is kept. ``isSidechain`` lines, and a hook that
carries ``agent_id``, are a sub-agent: each line keeps its own id and is a tool
turn, not the owner. A ``parentUuid`` is the previous line, not a parent session.
The transcript does not store the process environment. A hook
sees ``MEMORY_FOREST_INTERNAL`` in its own environment, because the hook is a
child of the session. A sweep sees the marker only when a line carries it as a
key (it is not in the environment attachment, which holds the platform and the
working directory, not the process environment).
"""

from __future__ import annotations

import json
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
    find_marks,
    harness_only,
    harness_parts,
    head_internal,
    head_objects,
    marked_internal,
    parse_object,
    parse_time,
    place_of,
    relative_key,
    session_token,
    stated_version,
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
    }
)
_HEADLESS = frozenset({"sdk-cli", "sdk", "print", "noninteractive"})
# The owner's own client. Anything else, including a value this adapter does not know, is unknown.
_INTERACTIVE = frozenset({"cli"})
_SUBAGENT_TOOLS = frozenset({"Agent", "Task", "spawn_agent", "spawn_subagent"})


class ClaudeCodeAdapter:
    name = "claude-code"
    # A line's ``uuid`` is its own id. The same uuid in another session file is the same turn.
    ids_stand_alone = True

    def discover(self, settings: IntakeToolSettings) -> list[Transcript]:
        root = tool_home(".claude", "projects", override=settings.path)
        if root is None:
            return []
        return [self._open(path, root) for path in walk_jsonl(root, lambda name: name.endswith(".jsonl"))]

    def open_path(self, path: Path, settings: IntakeToolSettings) -> Transcript:
        root = tool_home(".claude", "projects", override=settings.path)
        if root is None:
            raise MemoryForestError(
                "transcript_missing",
                "The transcript root is not a directory.",
                details={"adapter": self.name},
            )
        return self._open(confine_file(str(path), root), root)

    def hook(self, payload: dict, settings: IntakeToolSettings) -> Transcript:
        """The transcript the hook names. The file body is not read."""
        root = tool_home(".claude", "projects", override=settings.path)
        if root is None:
            raise MemoryForestError(
                "transcript_missing",
                "The transcript root is not a directory.",
                details={"adapter": self.name},
            )
        raw = payload.get("transcript_path") or payload.get("transcriptPath")
        if not isinstance(raw, str) or not raw:
            raise MemoryForestError(
                "hook_incomplete",
                "The hook payload has no transcript path.",
                details={"adapter": self.name},
            )
        path = confine_file(raw, root)
        session = session_token(payload.get("session_id") or payload.get("sessionId")) or path.stem
        cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else ""
        origin = "subagent" if payload.get("agent_id") or payload.get("agentId") else "unknown"
        return Transcript(relative_key(path, root), path, session, cwd, origin, described=False)

    def transcript_root(self, settings: IntakeToolSettings) -> Path | None:
        return tool_home(".claude", "projects", override=settings.path)

    def describe(self, transcript: Transcript, settings: IntakeToolSettings) -> Transcript:
        root = tool_home(".claude", "projects", override=settings.path)
        if root is None:
            return transcript
        return self._open(transcript.path, root)

    def read(self, transcript: Transcript, offset: int, carry: dict | None = None, max_bytes: int | None = None) -> ReadBatch:
        del carry
        lines, new_offset = complete_lines(transcript.path, offset, max_bytes=max_bytes)
        unknown = 0
        dropped: dict[str, int] = {}
        faults: list[tuple[str, str]] = []
        pending: dict[str, Turn] = {}
        order: list[Turn] = []
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
                if marked_internal(value):
                    internal = True
                kind = value.get("type")
                if not isinstance(kind, str):
                    unknown += 1
                    dropped["invalid"] = dropped.get("invalid", 0) + 1
                    continue
                if kind in {"summary", "compact-boundary", "compact_boundary"}:
                    turn = _system_line(value, start, transcript, kind)
                    if turn is None:
                        dropped[kind] = dropped.get(kind, 0) + 1
                    else:
                        order.append(turn)
                    continue
                if kind in _SKIP:
                    dropped[kind] = dropped.get(kind, 0) + 1
                    continue
                if kind == "attachment":
                    turn = _attachment(value, start, transcript)
                    if turn is None:
                        label = _attachment_type(value)
                        dropped[label] = dropped.get(label, 0) + 1
                    else:
                        order.append(turn)
                    continue
                if kind not in {"user", "assistant", "system"}:
                    unknown += 1
                    dropped[kind] = dropped.get(kind, 0) + 1
                    continue
                built = _message_turns(value, start, transcript)
                if not built and kind != "user":
                    dropped[kind] = dropped.get(kind, 0) + 1
                results = _results(value)
                side = value.get("isSidechain") is True or transcript.origin == "subagent"
                if side:
                    name = value.get("agentName") if isinstance(value.get("agentName"), str) and value["agentName"] else "subagent"
                    for turn in built:
                        order.append(
                            Turn(
                                turn.turn_id,
                                turn.at,
                                turn.session,
                                "tool",
                                turn.text,
                                turn.place,
                                "subagent",
                                turn.name or name,
                                turn.parts,
                                turn.arguments,
                            )
                        )
                    continue
                for turn in built:
                    order.append(turn)
                    if turn.role == "tool" and turn.turn_id:
                        pending[turn.turn_id] = turn
                for tool_id, output in results:
                    _fill(pending, order, tool_id, output)
            except KeyboardInterrupt:
                raise
            except BaseException as exc:
                faults.append((exc.__class__.__name__, place))
                dropped[exc.__class__.__name__] = dropped.get(exc.__class__.__name__, 0) + 1
        if internal:
            return ReadBatch((), new_offset, unknown, True, counts_of(dropped), tuple(faults))
        return ReadBatch(tuple(order), new_offset, unknown, False, counts_of(dropped), tuple(faults))

    def _open(self, path: Path, root: Path) -> Transcript:
        session = path.stem
        cwd = ""
        origin = "unknown"
        raw = ""
        version = ""
        for value in head_objects(path):
            if not version:
                version = stated_version(value)
            if isinstance(value.get("sessionId"), str) and value["sessionId"]:
                session = value["sessionId"]
            if isinstance(value.get("cwd"), str) and value["cwd"]:
                cwd = value["cwd"]
            entry = value.get("entrypoint")
            if isinstance(entry, str) and entry:
                raw = entry
                if value.get("isSidechain") is True:
                    origin = "subagent"
                elif entry in _HEADLESS:
                    origin = "headless"
                elif entry in _INTERACTIVE:
                    origin = "interactive"
                else:
                    origin = "unknown"
            elif value.get("isSidechain") is True:
                origin = "subagent"
            if cwd and session:
                break
        return Transcript(
            relative_key(path, root),
            path,
            session,
            cwd,
            origin,
            head_internal(path),
            raw,
            version,
            True,
            "",
        )


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
        elif text and harness_only(text):
            found.append(Turn(turn_id, at, str(session), "system", text, place_of(start), origin, None, harness_parts(text)))
        for index, (name, tool_id, arguments) in enumerate(tools, start=1):
            tool_origin = "subagent" if name in _SUBAGENT_TOOLS else origin
            found.append(
                Turn(
                    tool_id or turn_id,
                    at,
                    str(session),
                    "tool",
                    "",
                    place_of(start, index),
                    tool_origin,
                    name,
                    (),
                    arguments,
                )
            )
        return found
    if role in {"user", "system"}:
        text, parts = _user_text(content)
        if text is None:
            return []
        parts = parts + harness_parts(text or "")
        if (
            harness_only(text or "")
            or value.get("isMeta") is True
            or value.get("isCompactSummary") is True
            or role == "system"
        ):
            if not text and not parts:
                return []
            return [Turn(turn_id, at, str(session), "system", text or "", place_of(start), origin, None, parts)]
        if not text and not parts:
            return []
        return [Turn(turn_id, at, str(session), "owner", text, place_of(start), origin, None, parts)]
    if isinstance(role, str) and role:
        text, parts = _user_text(content)
        if text:
            return [Turn(turn_id, at, str(session), f"other:{role}", text, place_of(start), origin, None, parts)]
    return found


def _assistant(content: object) -> tuple[str, list[tuple[str, str, str | None]]]:
    if isinstance(content, str):
        return content, []
    texts: list[str] = []
    tools: list[tuple[str, str, str | None]] = []
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
                raw = block.get("input")
                arguments = json.dumps(raw, ensure_ascii=False, sort_keys=True) if isinstance(raw, dict) else None
                tools.append((name, tool_id, arguments))
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


def _attachment_type(value: dict) -> str:
    attachment = value.get("attachment")
    if isinstance(attachment, dict) and isinstance(attachment.get("type"), str) and attachment["type"]:
        return attachment["type"]
    return "attachment"


def _system_line(value: dict, start: int, transcript: Transcript, kind: str) -> Turn | None:
    """A compaction summary or a summary line. The body is kept only when it is a string."""
    at = parse_time(value.get("timestamp"))
    if at is None:
        return None
    text = value.get("summary") if isinstance(value.get("summary"), str) else ""
    if not text and "compact" in kind:
        text = "compaction"
    if not text:
        return None
    session = value.get("sessionId") if isinstance(value.get("sessionId"), str) else transcript.session
    turn_id = value.get("uuid") if isinstance(value.get("uuid"), str) else None
    return Turn(turn_id, at, str(session), "system", text, place_of(start), transcript.origin)


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
