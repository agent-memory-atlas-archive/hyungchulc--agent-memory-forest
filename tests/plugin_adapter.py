"""An adapter the tests plug in the way a third party would: one module behind ``memory_forest.adapters``.

The package ships only the ``jsonl`` reader. A reader of another program's own transcripts is written by
whoever knows that program and registered through the entry point group. This module is such a reader for
an invented format, the kiln log, so the tests can reach what ``jsonl`` never produces: a head line that
states a session, its raw origin, a version and a parent session; ids that stand alone or not, decided
per id; a tool call whose output arrives in a later read; and a hook payload of its own shape, which may
leave the session for a sweep.

A ``.kiln`` file holds one JSON object per line. The first may be a head (``{"head": {...}}``). A turn line
carries ``at``, ``role``, ``text`` and optionally ``id``, ``name``, ``arguments``, ``parts`` (``[start,
end, kind]`` lists) and ``origin``. A tool line with ``"pending": true`` waits for a later line
``{"output_for": <id>, "text": ...}``. Nothing here is copied from a real transcript.
"""

from __future__ import annotations

import importlib.metadata
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from memory_forest import adapters as adapter_loader
from memory_forest.adapters.common import (
    Part,
    ReadBatch,
    Transcript,
    Turn,
    complete_lines,
    confine_file,
    counts_of,
    head_objects,
    origin_or,
    parse_object,
    parse_time,
    place_of,
    relative_key,
)
from memory_forest.errors import MemoryForestError

NAME = "kiln-log"


class KilnLogAdapter:
    name = NAME

    def id_stands_alone(self, turn_id: str) -> bool:
        """A short ``call_`` id repeats across sessions; any other id of this format is unique by itself."""
        return not (turn_id.startswith("call_") and len(turn_id) < 32)

    def discover(self, settings) -> list[Transcript]:
        folder = _root(settings)
        if folder is None:
            return []
        return [self._open(path, folder) for path in sorted(folder.glob("*.kiln"))
                if path.is_file() and not path.is_symlink()]

    def open_path(self, path, settings) -> Transcript:
        folder = _root(settings)
        if folder is None:
            raise MemoryForestError("transcript_missing", "The transcript is not a file.",
                                    details={"adapter": self.name})
        return self._open(confine_file(str(path), folder), folder)

    def hook(self, payload: dict, settings) -> Transcript:
        if payload.get("defer"):
            # The hook cannot find its transcript cheaply, so it leaves the session for a sweep.
            raise MemoryForestError("transcript_deferred", "The hook left this session for a sweep.",
                                    details={"adapter": self.name})
        folder = _root(settings)
        raw = payload.get("kiln_log")
        if folder is None or not isinstance(raw, str) or not raw:
            raise MemoryForestError("hook_incomplete", "The hook payload has no transcript path.",
                                    details={"adapter": self.name})
        path = confine_file(raw, folder)
        origin = "subagent" if payload.get("helper") else "unknown"
        return Transcript(relative_key(path, folder), path, path.stem, "", origin, described=False)

    def transcript_root(self, settings) -> Path | None:
        return _root(settings)

    def describe(self, transcript: Transcript, settings) -> Transcript:
        folder = _root(settings)
        return transcript if folder is None else self._open(transcript.path, folder)

    def read(self, transcript: Transcript, offset: int, carry: dict | None = None,
             max_bytes: int | None = None) -> ReadBatch:
        lines, new_offset = complete_lines(transcript.path, offset, max_bytes=max_bytes)
        turns: list[Turn] = []
        dropped: dict[str, int] = {}
        unknown = 0
        pending = dict(carry) if isinstance(carry, dict) and carry.get("id") else None
        for start, raw in lines:
            value = parse_object(raw)
            if value is None or (isinstance(value, dict) and "head" in value):
                continue
            if value is False:
                unknown += 1
                dropped["invalid"] = dropped.get("invalid", 0) + 1
                continue
            if "output_for" in value:
                if pending and pending["id"] == value["output_for"]:
                    turns.append(_turn(pending, pending["start"], transcript, text=str(value.get("text") or "")))
                    pending = None
                continue
            if value.get("pending"):
                pending = {**value, "start": start}
                continue
            turn = _turn(value, start, transcript)
            if turn is None:
                unknown += 1
                dropped["record"] = dropped.get("record", 0) + 1
                continue
            turns.append(turn)
        if transcript.internal:
            return ReadBatch((), new_offset, unknown, True, counts_of(dropped))
        return ReadBatch(tuple(turns), new_offset, unknown, False, counts_of(dropped), carry=pending)

    def _open(self, path: Path, root: Path) -> Transcript:
        head: dict = {}
        for value in head_objects(path, limit=1):
            if isinstance(value.get("head"), dict):
                head = value["head"]
        raw = str(head.get("origin") or "")
        origin = {"desk": "interactive", "batch": "headless"}.get(raw, "unknown")
        return Transcript(relative_key(path, root), path, str(head.get("session") or path.stem),
                          str(head.get("cwd") or ""), origin, False, raw, str(head.get("version") or ""),
                          True, str(head.get("parent") or ""))


def _root(settings) -> Path | None:
    if not settings.path:
        return None
    folder = Path(settings.path).expanduser()
    return folder if folder.is_dir() and not folder.is_symlink() else None


def _turn(value: dict, start: int, transcript: Transcript, *, text: str | None = None) -> Turn | None:
    at = parse_time(value.get("at"))
    role = value.get("role")
    body = value.get("text", "") if text is None else text
    if at is None or role not in {"owner", "agent", "tool", "system"} or not isinstance(body, str):
        return None
    parts = tuple(Part(int(a), int(b), str(kind)) for a, b, kind in value.get("parts", ()))
    name = value.get("name") or ("tool" if role == "tool" else None)
    return Turn(value.get("id"), at, str(value.get("session") or transcript.session), role, body,
                place_of(start), origin_or(value.get("origin"), transcript.origin), name, parts,
                value.get("arguments"))


adapter = KilnLogAdapter()


@contextmanager
def plugged() -> Iterator[None]:
    """The kiln-log adapter registered under the entry point group, beside whatever is installed there."""
    real = adapter_loader.entry_points
    entry = importlib.metadata.EntryPoint(NAME, f"{__name__}:adapter", "memory_forest.adapters")

    def group(group: str):
        return [*real(group=group), entry]

    adapter_loader.entry_points = group
    adapter_loader.clear_adapter_cache()
    try:
        yield
    finally:
        adapter_loader.entry_points = real
        adapter_loader.clear_adapter_cache()
