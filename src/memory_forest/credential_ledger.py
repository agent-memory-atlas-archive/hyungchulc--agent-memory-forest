"""Sign-in values no label word names: a ledger of keyed digests (contract, section 4, Sign-in values).

A bare code in a quoted turn ("4821, see you at the gate") is a sign-in value only
when a reader says so. Once judged, it is masked wherever it appears again,
without the forest keeping the value: the ledger keeps the HMAC-SHA256 of the
value under a key of the forest's own.

- ``.forest/credentials/key`` holds the key: 32 random bytes, made on the first
  judgement, mode 0600.
- ``.forest/credentials/sign-in.jsonl`` holds one digest per value judged a
  sign-in value; ``mask_known`` replaces each such value by ``[REDACTED]``.
- ``.forest/credentials/judged.jsonl`` holds one digest per value judged not to
  be one, so the lint does not count it again.

Neither the key nor a digest is ever printed, returned to a caller or put in an
error: a candidate is named by its page, its line and its place in the line.
The ledger, like the move log, is kept state: deleting it forgets every
judgement.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePath as Path
from typing import Any, Final

from . import layout, system
from .config import Config, load_config
from .errors import MemoryForestError
from .locking import LockToken, append_lines, writer_lock
from .safety import require_real_root
from .view import read_text

FOLDER: Final[str] = f"{layout.STATE_DIR}/credentials"
KEY_FILE: Final[str] = f"{FOLDER}/key"
SIGN_IN_FILE: Final[str] = f"{FOLDER}/sign-in.jsonl"
JUDGED_FILE: Final[str] = f"{FOLDER}/judged.jsonl"
KEY_BYTES: Final[int] = 32
REDACTED: Final[str] = "[REDACTED]"
# A token that may be a value: no space, no bracket or quote; its edges' punctuation is not part of it.
_TOKEN_RE: Final = re.compile(r"[^\s\"'`()\[\]{}<>«»“”‘’,;]+")
_EDGE: Final[str] = ".:!?*_"
_DATE_RE: Final = re.compile(r"\d{1,4}[-./]\d{1,2}[-./]\d{1,4}")
_TIME_RE: Final = re.compile(r"\d{1,2}[:.]\d{2}(?:[:.]\d{2})?")
_LINE_QUOTE_RE: Final = re.compile(r"^\s*(?:[-*+]\s+)?>")


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start] in _EDGE:
        start += 1
    while end > start and text[end - 1] in _EDGE:
        end -= 1
    return start, end


def maybe_value(token: str) -> bool:
    """Whether a token can be a value at all: 4 to 64 characters with a digit, not starred, no path, mail,
    date or time. Only such tokens are digested or counted."""
    return (4 <= len(token) <= 64 and any(character.isdigit() for character in token) and "*" not in token
            and "/" not in token and "@" not in token and REDACTED not in token
            and not _DATE_RE.fullmatch(token) and not _TIME_RE.fullmatch(token))


def candidate_shape(token: str) -> bool:
    """A token shaped like a code a reader should judge: 4 to 8 digits that do not read as a year, or 6 to 32
    letters and digits mixed with at least two digits."""
    if not maybe_value(token):
        return False
    if token.isdigit():
        return 4 <= len(token) <= 8 and not (len(token) == 4 and token[:2] in ("19", "20"))
    return 6 <= len(token) <= 32 and token.isalnum() and sum(character.isdigit() for character in token) >= 2 \
        and any(character.isalpha() for character in token)


def tokens(line: str) -> list[tuple[int, int]]:
    """The spans of the value-like tokens of a line, in order."""
    found = []
    for match in _TOKEN_RE.finditer(line):
        start, end = _trim(line, *match.span())
        if maybe_value(line[start:end]):
            found.append((start, end))
    return found


def _quoted_spans(line: str, marks: Iterable[str]) -> list[tuple[int, int]]:
    """Where a line quotes a turn: the whole line when it is a quote block line, else each part between a
    pair of quote marks (``record.quote_marks``)."""
    if _LINE_QUOTE_RE.match(line):
        return [(0, len(line))]
    spans = []
    for pair in marks:
        opening, closing = pair[0], pair[1]
        start = line.find(opening)
        while start >= 0:
            end = line.find(closing, start + 1)
            if end < 0:
                break
            spans.append((start + 1, end))
            start = line.find(opening, end + 1)
    return spans


def candidates(line: str, config: Config) -> list[tuple[int, int]]:
    """The tokens of a line that stand in a quoted turn and have a candidate's shape, in order."""
    quoted = _quoted_spans(line, config.record.quote_marks)
    if not quoted:
        return []
    return [(start, end) for start, end in tokens(line) if candidate_shape(line[start:end])
            and any(left <= start and end <= right for left, right in quoted)]


# ─────────────────────────────── the ledger ───────────────────────────────


@dataclass(frozen=True, slots=True)
class Ledger:
    """The judged values of one forest, held as keyed digests (the key never leaves this object)."""

    _key: bytes
    sign_in: frozenset[str]
    judged: frozenset[str]

    def digest(self, value: str) -> str:
        return hmac.new(self._key, value.encode("utf-8", "surrogateescape"), hashlib.sha256).hexdigest()

    def known(self, value: str) -> bool:
        return self.digest(value) in self.sign_in

    def settled(self, value: str) -> bool:
        return self.known(value) or self.digest(value) in self.judged

    def __repr__(self) -> str:  # never shows the key or a digest
        return f"Ledger(sign_in={len(self.sign_in)}, judged={len(self.judged)})"


def _private_file(path: Path) -> bytes:
    """The key's bytes, read from the descriptor whose mode was checked."""
    try:
        handle = system.open_file(path)
    except (system.Link, system.NotAFile) as exc:
        raise MemoryForestError("credential_ledger_unsafe", "A ledger file must be a regular file, never a link.",
                                details={"file": path.name}) from exc
    with handle:
        if handle.look.mode & 0o077:
            raise MemoryForestError("credential_key_exposed", "The ledger key must be readable by its owner only "
                                    "(mode 0600).", details={"file": path.name})
        return handle.read()


def _digests(path: Path) -> frozenset[str]:
    try:
        lines = system.read_text(path).splitlines()
    except system.Absent:
        return frozenset()
    found = set()
    for line in lines:
        try:
            value = json.loads(line).get("digest")
        except (ValueError, AttributeError):
            continue
        if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
            found.add(value)
    return frozenset(found)


_CACHE: dict[str, tuple[tuple[Any, ...], Ledger | None]] = {}


def _stamp(*paths: Path) -> tuple[Any, ...]:
    stamp = []
    for path in paths:
        info = system.look_or_none(path)
        stamp.append(None if info is None else (info.mtime_ns, info.size, info.mode, info.ino, info.kind))
    return tuple(stamp)


def load(root: str | os.PathLike[str]) -> Ledger | None:
    """The forest's ledger, or None when it has no key yet (nothing was judged). Read again when a file
    changed."""
    safe_root = Path(root)
    key_path = layout.state_path(safe_root, KEY_FILE)
    sign_in = layout.state_path(safe_root, SIGN_IN_FILE)
    judged = layout.state_path(safe_root, JUDGED_FILE)
    stamp = _stamp(key_path, sign_in, judged)
    cached = _CACHE.get(str(key_path))
    if cached is not None and cached[0] == stamp:
        return cached[1]
    ledger = None
    if stamp[0] is not None:
        key = _private_file(key_path)
        if len(key) != KEY_BYTES:
            raise MemoryForestError("credential_key_invalid", "The ledger key is not the size it was made.")
        ledger = Ledger(key, _digests(sign_in), _digests(judged))
    elif stamp[1] is not None or stamp[2] is not None:
        raise MemoryForestError("credential_key_missing", "The ledger holds judgements but its key is gone; "
                                "the values it names can no longer be recognised.")
    _CACHE[str(key_path)] = (stamp, ledger)
    return ledger


def for_config(config: Config) -> Ledger | None:
    """The ledger of the forest whose settings are ``config`` (None for settings read from no forest)."""
    if not config.source:
        return None
    root = Path(config.source).parent
    if system.kind(root / layout.STATE_DIR) != "folder":
        return None
    return load(root)


def mask_known(text: str, ledger: Ledger | None, *, guarded: bool = True) -> str:
    """``text`` with every value the ledger holds as a sign-in value replaced by ``[REDACTED]``."""
    if ledger is None or not ledger.sign_in:
        return text
    if guarded and not any(character.isdigit() for character in text):
        return text
    pieces: list[str] = []
    cursor = 0
    for start, end in tokens(text):
        if ledger.known(text[start:end]):
            pieces += [text[cursor:start], REDACTED]
            cursor = end
    if not pieces:
        return text
    pieces.append(text[cursor:])
    return "".join(pieces)


def _make_key(path: Path) -> None:
    """Make the key. Another process having made it already leaves that key in place."""
    system.create_new(path, secrets.token_bytes(KEY_BYTES), mode=0o600)


# ─────────────────────────────── candidates of a forest ───────────────────────────────


@dataclass(frozen=True, slots=True)
class Candidate:
    """A code-shaped token in a quoted turn that nobody judged: its page, line and place (never its value)."""

    path: str
    line: int
    place: int

    @property
    def name(self) -> str:
        return f"{self.path}:{self.line}:{self.place}"

    def as_dict(self) -> dict[str, Any]:
        return {"line": self.line, "path": self.path, "place": self.place}


def unjudged(root: str | os.PathLike[str], texts: Iterable[tuple[str, str]], config: Config) -> list[Candidate]:
    """Each candidate of the pages ``(path, text)`` that the ledger does not settle."""
    ledger = load(root)
    found = []
    for path, text in texts:
        for number, line in enumerate(text.split("\n"), start=1):
            if not any(character.isdigit() for character in line):
                continue
            for place, (start, end) in enumerate(candidates(line, config), start=1):
                if ledger is None or not ledger.settled(line[start:end]):
                    found.append(Candidate(path, number, place))
    return found


def _candidate_value(root: Path, name: str, config: Config) -> str:
    path, _, rest = name.rpartition(":")
    path, _, number = path.rpartition(":")
    if not path or not number.isdigit() or not rest.isdigit():
        raise MemoryForestError("credential_candidate_invalid", "Name a candidate as PATH:LINE:PLACE.")
    relative = layout.normalize(path)
    if not layout.classify(relative).in_forest:
        raise MemoryForestError("credential_candidate_invalid", "A candidate stands on a forest page.",
                                details={"path": relative})
    lines = read_text(layout.resolve(root, relative, must_exist=True)).split("\n")
    line_number, place = int(number), int(rest)
    if not 1 <= line_number <= len(lines):
        raise MemoryForestError("credential_candidate_invalid", "The page has no such line.", details={"path": relative})
    spans = candidates(lines[line_number - 1], config)
    if not 1 <= place <= len(spans):
        raise MemoryForestError("credential_candidate_invalid", "The line has no candidate at that place.",
                                details={"path": relative, "line": line_number})
    start, end = spans[place - 1]
    return lines[line_number - 1][start:end]


def judge(
    root: str | os.PathLike[str],
    names: Iterable[str],
    *,
    sign_in: bool,
    config: Config | None = None,
    lock: LockToken | str | None = None,
) -> int:
    """Record a reader's judgement of each named candidate (``PATH:LINE:PLACE``): a sign-in value, masked from
    then on wherever it appears, or not one, no longer counted. Returns how many digests were added."""
    safe_root = require_real_root(root)
    settings = config if config is not None else load_config(safe_root)
    chosen = list(names)
    if not chosen:
        raise MemoryForestError("credential_candidate_invalid", "Name at least one candidate.")
    values = [_candidate_value(safe_root, name, settings) for name in chosen]
    with writer_lock(safe_root, token=lock, label="credentials judge"):
        folder = layout.ensure_folder(safe_root, FOLDER)
        system.set_mode(folder, 0o700)
        key_path = layout.state_path(safe_root, KEY_FILE)
        if system.look_or_none(key_path) is None:
            if any(system.look_or_none(layout.state_path(safe_root, name)) is not None
                   for name in (SIGN_IN_FILE, JUDGED_FILE)):
                load(safe_root)  # raises: judgements without their key
            _make_key(key_path)
        ledger = load(safe_root)
        assert ledger is not None
        target = SIGN_IN_FILE if sign_in else JUDGED_FILE
        held = ledger.sign_in if sign_in else ledger.judged
        rows = []
        for value in dict.fromkeys(values):
            digest = ledger.digest(value)
            if digest not in held:
                rows.append(json.dumps({"digest": digest}, sort_keys=True))
        append_lines(layout.state_path(safe_root, target), rows)
    return len(rows)


__all__ = ["Candidate", "Ledger", "candidate_shape", "candidates", "for_config", "judge", "load", "mask_known",
           "unjudged"]
