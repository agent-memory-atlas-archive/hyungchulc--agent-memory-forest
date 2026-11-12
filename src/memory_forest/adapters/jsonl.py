"""The ``jsonl`` adapter: a folder of files already in the turn record's shape.

Any tool that can write or export the record uses this. A line that is not a
JSON object with ``at``, ``session``, ``role`` and ``text`` is skipped and
counted. A last line with no newline is left for the next read.

There is no tool version: the shape is the record itself (contract, section 1).
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
    head_internal,
    head_objects,
    origin_or,
    parse_object,
    parse_time,
    place_of,
    require_file,
)


class JsonlAdapter:
    name = "jsonl"

    def discover(self, settings: IntakeToolSettings) -> list[Transcript]:
        folder = Path(settings.path).expanduser() if settings.path else None
        if folder is None or not folder.is_dir() or folder.is_symlink():
            return []
        found = []
        for path in sorted(folder.iterdir(), key=lambda item: item.name):
            if path.is_symlink() or not path.is_file() or not path.name.endswith(".jsonl"):
                continue
            if path.name.startswith("."):
                continue
            found.append(self._open(path))
        return found

    def open_path(self, path: Path, settings: IntakeToolSettings) -> Transcript:
        del settings
        return self._open(require_file(path))

    def hook(self, payload: dict, settings: IntakeToolSettings) -> Transcript:
        raw = payload.get("transcript_path") or payload.get("transcriptPath") or payload.get("transcript")
        if not isinstance(raw, str) or not raw:
            raise MemoryForestError(
                "hook_incomplete",
                "The hook payload has no transcript path.",
                details={"adapter": self.name},
            )
        transcript = self._open(require_file(Path(raw)))
        session = payload.get("session_id") or payload.get("sessionId") or transcript.session
        cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else transcript.cwd
        return Transcript(
            transcript.key,
            transcript.path,
            str(session),
            cwd,
            transcript.origin,
            transcript.internal,
        )

    def read(self, transcript: Transcript, offset: int) -> ReadBatch:
        lines, new_offset = complete_lines(transcript.path, offset)
        turns: list[Turn] = []
        unknown = 0
        internal = transcript.internal
        for index, (start, raw) in enumerate(lines):
            value = parse_object(raw)
            if value is None:
                continue
            if value is False:
                unknown += 1
                continue
            if value.get("internal") is True:
                internal = True
            turn = _turn(value, start, index, transcript)
            if turn is None:
                unknown += 1
                continue
            turns.append(turn)
        if internal or head_internal(transcript.path, record=True):
            return ReadBatch((), new_offset, unknown, internal=True)
        return ReadBatch(tuple(turns), new_offset, unknown, internal=False)

    def _open(self, path: Path) -> Transcript:
        session = path.stem
        cwd = ""
        origin = "interactive"
        for value in head_objects(path, limit=5):
            if isinstance(value.get("session"), str) and value["session"]:
                session = value["session"]
            if isinstance(value.get("cwd"), str):
                cwd = value["cwd"]
            origin = origin_or(value.get("origin"), origin)
            break
        return Transcript(
            file_key(path),
            path,
            session,
            cwd,
            origin,
            head_internal(path, record=True),
        )


def _turn(value: dict, start: int, index: int, transcript: Transcript) -> Turn | None:
    at = parse_time(value.get("at"))
    session = value.get("session")
    role = value.get("role")
    text = value.get("text")
    if at is None or not isinstance(session, str) or not session:
        return None
    if not isinstance(role, str) or not _role_ok(role):
        return None
    if not isinstance(text, str):
        return None
    origin = origin_or(value.get("origin"), transcript.origin)
    name = value.get("name") if isinstance(value.get("name"), str) and value["name"] else None
    if role == "tool" and not name:
        name = "tool"
    parts = _parts(value, text)
    turn_id = value.get("id") if isinstance(value.get("id"), str) and value["id"] else None
    source = value.get("source")
    place = place_of(start, index)
    if isinstance(source, dict) and isinstance(source.get("place"), str) and source["place"]:
        place = source["place"]
    return Turn(turn_id, at, session, role, text, place, origin, name, parts)


def _role_ok(role: str) -> bool:
    if role in {"owner", "agent", "tool"}:
        return True
    return role.startswith("other:") and bool(role.removeprefix("other:").strip()) and "\n" not in role


def _parts(value: dict, text: str) -> tuple[Part, ...]:
    raw = value.get("parts")
    if isinstance(raw, list):
        found = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind")
            start = item.get("start")
            end = item.get("end")
            if kind not in {"paste", "file", "quote"}:
                continue
            if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text):
                found.append(Part(start, end, kind))
        if found:
            return tuple(found)
    return find_marks(text)


adapter = JsonlAdapter()
