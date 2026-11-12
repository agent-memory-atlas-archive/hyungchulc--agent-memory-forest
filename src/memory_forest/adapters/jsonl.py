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
    confine_file,
    counts_of,
    find_marks,
    head_internal,
    head_objects,
    marked_internal,
    origin_or,
    parse_object,
    parse_time,
    place_of,
    relative_key,
    text_internal,
)


class JsonlAdapter:
    name = "jsonl"

    def discover(self, settings: IntakeToolSettings) -> list[Transcript]:
        folder = _root(settings)
        if folder is None:
            return []
        found = []
        for path in sorted(folder.iterdir(), key=lambda item: item.name):
            if path.is_symlink() or not path.is_file() or not path.name.endswith(".jsonl"):
                continue
            if path.name.startswith("."):
                continue
            found.append(self._open(path, folder))
        return found

    def open_path(self, path: Path, settings: IntakeToolSettings) -> Transcript:
        folder = _root(settings)
        file = Path(path)
        if folder is None:
            # The operator named one file. Its folder is the root, so the stored path stays relative.
            if file.is_symlink() or not file.is_file():
                raise MemoryForestError(
                    "transcript_missing",
                    "The transcript is not a file.",
                    details={"adapter": self.name, "path": file.name},
                )
            return self._open(file, file.parent)
        return self._open(confine_file(str(file), folder), folder)

    def hook(self, payload: dict, settings: IntakeToolSettings) -> Transcript:
        """The transcript the hook names. The file body is not read."""
        folder = _root(settings)
        if folder is None:
            raise MemoryForestError(
                "intake_path_unset",
                "The jsonl adapter needs the folder of record files. Set path under [intake.tools.jsonl].",
                details={"adapter": self.name},
            )
        raw = payload.get("transcript_path") or payload.get("transcriptPath") or payload.get("transcript")
        if not isinstance(raw, str) or not raw:
            raise MemoryForestError(
                "hook_incomplete",
                "The hook payload has no transcript path.",
                details={"adapter": self.name},
            )
        path = confine_file(raw, folder)
        session = payload.get("session_id") or payload.get("sessionId") or path.stem
        cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else ""
        return Transcript(relative_key(path, folder), path, str(session), cwd, "unknown", described=False)

    def describe(self, transcript: Transcript, settings: IntakeToolSettings) -> Transcript:
        folder = _root(settings)
        if folder is None:
            return transcript
        return self._open(transcript.path, folder)

    def read(self, transcript: Transcript, offset: int) -> ReadBatch:
        lines, new_offset = complete_lines(transcript.path, offset)
        turns: list[Turn] = []
        unknown = 0
        dropped: dict[str, int] = {}
        faults: list[tuple[str, str]] = []
        internal = transcript.internal
        for index, (start, raw) in enumerate(lines):
            place = place_of(start, index)
            try:
                value = parse_object(raw)
                if value is None:
                    continue
                if value is False:
                    unknown += 1
                    dropped["invalid"] = dropped.get("invalid", 0) + 1
                    continue
                if value.get("internal") is True or marked_internal(value, record=True):
                    internal = True
                turn = _turn(value, start, transcript)
                if turn is None:
                    unknown += 1
                    label = value.get("type") if isinstance(value.get("type"), str) else "record"
                    dropped[label] = dropped.get(label, 0) + 1
                    continue
                if text_internal(turn.text):
                    internal = True
                turns.append(turn)
            except Exception as exc:
                faults.append((exc.__class__.__name__, place))
                dropped[exc.__class__.__name__] = dropped.get(exc.__class__.__name__, 0) + 1
        if internal:
            return ReadBatch((), new_offset, unknown, True, counts_of(dropped), tuple(faults))
        return ReadBatch(tuple(turns), new_offset, unknown, False, counts_of(dropped), tuple(faults))

    def _open(self, path: Path, root: Path) -> Transcript:
        session = path.stem
        cwd = ""
        origin = "unknown"
        for value in head_objects(path, limit=5):
            if isinstance(value.get("session"), str) and value["session"]:
                session = value["session"]
            if isinstance(value.get("cwd"), str):
                cwd = value["cwd"]
            origin = origin_or(value.get("origin"), "unknown")
            break
        return Transcript(
            relative_key(path, root),
            path,
            session,
            cwd,
            origin,
            head_internal(path, record=True),
            origin if origin != "unknown" else "",
            "",
            True,
            "",
        )


def _root(settings: IntakeToolSettings) -> Path | None:
    if not settings.path:
        return None
    folder = Path(settings.path).expanduser()
    if not folder.is_dir() or folder.is_symlink():
        return None
    return folder


def _turn(value: dict, start: int, transcript: Transcript) -> Turn | None:
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
    origin = origin_or(value.get("origin"), transcript.origin if transcript.origin in {"interactive", "headless", "subagent", "unknown"} else "unknown")
    if "origin" not in value and transcript.origin == "unknown":
        origin = "unknown"
    name = value.get("name") if isinstance(value.get("name"), str) and value["name"] else None
    if role == "tool" and not name:
        name = "tool"
    parts = _parts(value, text)
    turn_id = value.get("id") if isinstance(value.get("id"), str) and value["id"] else None
    source = value.get("source")
    place = place_of(start)
    if isinstance(source, dict) and isinstance(source.get("place"), str) and source["place"]:
        place = source["place"]
    return Turn(turn_id, at, session, role, text, place, origin, name, parts)


def _role_ok(role: str) -> bool:
    if role in {"owner", "agent", "tool", "system"}:
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
