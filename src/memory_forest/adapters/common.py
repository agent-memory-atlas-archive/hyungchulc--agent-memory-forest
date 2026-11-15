"""Shared shapes for a transcript adapter.

An adapter reads one tool's own transcript. It yields turns and a cursor. A
line that does not end in a newline is still being written, and is left for
the next read. A line this package does not understand is skipped and counted;
``forest intake status`` shows the count.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..errors import MemoryForestError


MARKER: str = "MEMORY_FOREST_INTERNAL"
# The first line of a prompt a stage sends. Intake treats that line, in a session's
# first message, as the same mark as the environment variable.
MARKER_LINE: str = MARKER
ORIGINS: tuple[str, ...] = ("interactive", "headless", "subagent", "unknown")
PART_KINDS: tuple[str, ...] = ("paste", "file", "quote", "harness")
# Regions a tool wraps around text the owner pasted, attached or quoted.
# The record keeps the whole text; these name the spans.
_MARKS: tuple[tuple[str, str], ...] = (
    ("pasted_contents", "paste"),
    ("pasted_text", "paste"),
    ("attached_files", "file"),
    ("file_contents", "file"),
    ("ide_opened_file", "file"),
    ("quoted_message", "quote"),
    ("quoted_selection", "quote"),
    ("ide_selection", "quote"),
)
_MARK_RE = re.compile(
    "|".join(rf"<(?P<tag{index}>{tag})\b[^>]*>(?P<body{index}>.*?)</{tag}>" for index, (tag, _) in enumerate(_MARKS)),
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class Part:
    """A span of ``text`` the tool marked as pasted, attached or quoted."""

    start: int
    end: int
    kind: str


@dataclass(frozen=True, slots=True)
class Turn:
    """One turn an adapter read, before the writer masks it and gives it an id."""

    turn_id: str | None
    at: datetime
    session: str
    role: str
    text: str
    place: str
    origin: str
    name: str | None = None
    parts: tuple[Part, ...] = ()
    arguments: str | None = None


@dataclass(frozen=True, slots=True)
class Transcript:
    """One transcript file and the session it belongs to."""

    key: str
    path: Path
    session: str
    cwd: str
    origin: str
    internal: bool = False
    origin_raw: str = ""
    version: str = ""
    described: bool = False
    parent: str = ""


@dataclass(frozen=True, slots=True)
class ReadBatch:
    """Turns after a cursor, the cursor to store, and how many lines were not understood."""

    turns: tuple[Turn, ...]
    offset: int
    unknown: int
    internal: bool = False
    # (type name, count) for lines that were not turns, and (class, place) for a line
    # that could not be taken at all. The cursor still moves past both.
    dropped: tuple[tuple[str, int], ...] = ()
    faults: tuple[tuple[str, str], ...] = ()
    # A tool call whose output has not arrived yet. The cursor keeps it for the next read.
    carry: dict[str, Any] | None = None


def env_internal(environ: Mapping[str, str]) -> bool:
    """Whether this process was started by a forest stage (``MEMORY_FOREST_INTERNAL``)."""
    return _truthy(environ.get(MARKER, ""))


def speaks_as_owner(role: str, origin: str) -> bool:
    """The owner at the keyboard. ``unknown`` is decided like ``headless``: it is not."""
    return role == "owner" and origin == "interactive"


def text_internal(text: str) -> bool:
    """Whether ``text`` opens with the stage marker line."""
    if not text:
        return False
    first = text.lstrip("\ufeff").splitlines()[:1]
    return bool(first) and first[0].strip() == MARKER_LINE


def session_token(value: object) -> str | None:
    """A session id that cannot be a path: no separator, no ``..``, bounded length."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 200 or text in {".", ".."}:
        return None
    if any(mark in text for mark in ("/", "\\", "\n", "\r", "..")):
        return None
    return text


def relative_key(path: Path, root: Path) -> str:
    """``path`` relative to the tool's transcript root. Never an absolute home path."""
    return path.resolve().relative_to(root.resolve()).as_posix()


def confine_file(raw: str, root: Path) -> Path:
    """The real regular file ``raw`` names, inside ``root``.

    A ``..`` segment, a symlink, and a path that resolves outside ``root`` are refused.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise MemoryForestError(
            "hook_incomplete",
            "The hook payload has no transcript path.",
        )
    candidate = Path(raw)
    if ".." in candidate.parts:
        raise MemoryForestError(
            "unsafe_transcript",
            "A transcript path cannot contain '..'.",
            details={"path": candidate.name},
        )
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_symlink():
        raise MemoryForestError(
            "unsafe_transcript",
            "A transcript must be a regular file, never a symlink.",
            details={"path": candidate.name},
        )
    try:
        real = candidate.resolve()
        base = root.resolve()
    except OSError as exc:
        raise MemoryForestError(
            "unsafe_transcript",
            "The transcript path could not be resolved.",
            details={"path": candidate.name},
        ) from exc
    if not real.is_relative_to(base):
        raise MemoryForestError(
            "unsafe_transcript",
            "The transcript is outside the tool's transcript root.",
            details={"path": real.name},
        )
    return require_file(real)


def counts_of(items: dict[str, int]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted((key, value) for key, value in items.items() if value))


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return False


def require_file(path: Path) -> Path:
    """A regular transcript file. A symlink is refused."""
    if path.is_symlink():
        raise MemoryForestError(
            "unsafe_transcript",
            "A transcript must be a regular file, never a symlink.",
            details={"path": path.name},
        )
    if not path.is_file():
        raise MemoryForestError(
            "transcript_missing",
            "The transcript is not a file.",
            details={"path": path.name},
        )
    return path


def file_key(path: Path) -> str:
    """A stable cursor key for a regular file."""
    return str(require_file(path).resolve())


def parse_time(value: object) -> datetime | None:
    """An instant with an offset. A bare clock is read as UTC. An integer of
    milliseconds (or seconds) is read as UTC. None when the value is not a time."""
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
        if abs(seconds) > 10_000_000_000:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def parse_object(raw: bytes) -> dict[str, Any] | None | bool:
    """One JSON object, ``None`` for a blank line, or ``False`` when the line is not one."""
    if not raw.strip():
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return False
    return value if isinstance(value, dict) else False


def complete_lines(
    path: Path, offset: int, *, max_bytes: int | None = None
) -> tuple[list[tuple[int, bytes]], int]:
    """Complete lines at and after ``offset``, and where the next read starts.

    The returned offset is the start of a trailing line that has no newline,
    or the end of the file when every line is complete. The trailing line is
    not returned. ``max_bytes`` stops the read after that many bytes, so a
    hook's work stays bounded. A line longer than that gives no line and no
    progress: it is the sweep's, which reads without a bound.
    """
    with path.open("rb") as handle:
        handle.seek(max(0, offset))
        data = handle.read() if max_bytes is None else handle.read(max(0, max_bytes))
    if not data:
        return [], offset
    lines: list[tuple[int, bytes]] = []
    cursor = 0
    while True:
        newline = data.find(b"\n", cursor)
        if newline < 0:
            break
        lines.append((offset + cursor, data[cursor:newline]))
        cursor = newline + 1
    return lines, offset + cursor


def complete_end(path: Path) -> int:
    """The offset after the last complete line, without reading the whole file."""
    size = path.stat().st_size
    if size == 0:
        return 0
    with path.open("rb") as handle:
        handle.seek(size - 1)
        if handle.read(1) == b"\n":
            return size
        position = size
        while position > 0:
            step = min(8192, position)
            position -= step
            handle.seek(position)
            chunk = handle.read(step)
            found = chunk.rfind(b"\n")
            if found >= 0:
                return position + found + 1
    return 0


def head_objects(path: Path, limit: int = 20) -> list[dict[str, Any]]:
    """The first JSON objects of a transcript, reading only its head."""
    with path.open("rb") as handle:
        data = handle.read(256 * 1024)
    end = data.rfind(b"\n")
    if end < 0:
        return []
    found: list[dict[str, Any]] = []
    for raw in data[: end + 1].splitlines():
        value = parse_object(raw)
        if isinstance(value, dict):
            found.append(value)
        if len(found) >= limit:
            break
    return found


def find_marks(text: str) -> tuple[Part, ...]:
    """Spans a tool marked inside ``text``. The text itself is not changed."""
    found: list[Part] = []
    for match in _MARK_RE.finditer(text):
        for index, (_, kind) in enumerate(_MARKS):
            body = match.group(f"body{index}")
            if body is None:
                continue
            start, end = match.span(f"body{index}")
            if end > start:
                found.append(Part(start, end, kind))
            break
    return tuple(found)


def place_of(offset: int, index: int = 0) -> str:
    """Where in the transcript a turn was read. ``index`` separates turns that share a line."""
    if index:
        return f"byte {offset}#{index}"
    return f"byte {offset}"


def origin_or(value: object, default: str) -> str:
    if isinstance(value, str) and value in ORIGINS:
        return value
    return default
