"""Values sent to sign in or to pay (contract, section 4, Sign-in values).

A memory that records conversations is handed passwords, PINs, one-time
codes and door codes in passing. Such a value is never starred like a number
(``numbers``): it is removed. The page keeps that a value was sent (who, to
whom, for which account) and ``[REDACTED]`` in the value's place.

A value is recognised by the word before it, one of ``credentials.label_words``
(English by default; a forest adds the words of its other languages):

- after a colon or an equals sign (``PIN: 4821``, ``password = tulip``), any
  value that holds a letter or a digit (``PIN: -`` says there is none);
- without one (``door code 4821``, ``the password is tulip7``), a run of 3 to 64
  characters that holds a digit and is no date, time, path, address or mail
  address, and is not followed by one of ``credentials.count_words``
  (``a password of 12 characters``): a word alone after a label is prose.

A value with no label word (a bare code in a quoted turn) is masked once a
reader has judged it, through the forest's ledger of keyed digests
(``credential_ledger``).

Each step runs only when the text holds what its pattern cannot match
without (a guard that is a necessary condition of the pattern, so a guarded
step gives what the unguarded one gives). ``mask`` is idempotent: a masked
text is a fixed point. What the rule cannot see (a code with no label word,
a value split over lines, a label word the settings do not list) is said in
``docs/privacy-and-trust.md``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any, Final

from . import archive as forest_archive
from . import layout
from .config import Config, load_config
from .errors import MemoryForestError
from .locking import LockToken, atomic_write, writer_lock
from .safety import require_real_root
from .view import read_text

REDACTED: Final[str] = "[REDACTED]"
# The version of what ``shown`` does to a text (this module's rules and ``numbers.star_numbers``). Raise it with
# any change that can make a read show less than before: text kept masked under the old rules (the search
# index) is then masked again.
MASKING_VERSION: Final[str] = "1"
FIX_REASON: Final[str] = "credential-masking"
MIN_BARE: Final[int] = 3
MAX_BARE: Final[int] = 64
MAX_QUOTED: Final[int] = 128
_ASSIGN_MARKS: Final[str] = ":=："
_OPENERS: Final[str] = "\"'`([{<“‘«"
_CLOSERS: Final[str] = "\"'`)]}>”’».,;:!?"
_QUOTE_PAIRS: Final[dict[str, str]] = {'"': '"', "'": "'", "`": "`", "“": "”", "‘": "’", "«": "»"}
_DATE_RE: Final = re.compile(r"\d{1,4}[-./]\d{1,2}[-./]\d{1,4}")
_TIME_RE: Final = re.compile(r"\d{1,2}[:.]\d{2}(?:[:.]\d{2})?(?:am|pm)?", re.IGNORECASE)
_MAIL_RE: Final = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")


def _phrase(word: str) -> str:
    """A label word as a pattern: its parts joined by any run of spaces, tabs or hyphens (never a line break:
    nothing the rules find crosses a line, so a page and its lines are masked alike)."""
    parts = [re.escape(part) for part in re.split(r"[\s\-]+", word.strip()) if part]
    return r"[ \t\-]*".join(parts)


@dataclass(frozen=True, slots=True)
class _Rules:
    plain: re.Pattern[str] | None  # the label words with no edges: the guard
    label: re.Pattern[str] | None  # the label words as whole words
    assign: re.Pattern[str]  # after a label: the joining word, a colon or an equals sign, the value
    bare: re.Pattern[str]  # after a label: the joining word, spaces, the value
    count: re.Pattern[str] | None  # a count word right after a value


@lru_cache(maxsize=32)
def _rules(labels: tuple[str, ...], joining: tuple[str, ...], counts: tuple[str, ...]) -> _Rules:
    forms = sorted({_phrase(word) for word in labels if word.strip()}, key=len, reverse=True)
    plain = re.compile("|".join(forms), re.IGNORECASE) if forms else None
    label = re.compile(r"(?<!\w)(?:" + "|".join(forms) + r")(?!\w)", re.IGNORECASE) if forms else None
    joins = sorted({_phrase(word) for word in joining if word.strip()}, key=len, reverse=True)
    join = r"(?:[ \t]+(?:" + "|".join(joins) + r")(?!\w))?" if joins else ""
    # A label set in bold or italics keeps its marks: `**PIN:** 4821`, `**PIN**: 4821`.
    assign = re.compile(r"[*_]{0,3}" + join + r"[ \t]*[:=：][*_]{0,3}[ \t]*(?P<value>\S+)", re.IGNORECASE)
    bare = re.compile(r"[*_]{0,3}" + join + r"[ \t]{1,3}(?P<value>\S+)", re.IGNORECASE)
    words = sorted({_phrase(word) for word in counts if word.strip()}, key=len, reverse=True)
    count = re.compile(r"[ \t]+(?:" + "|".join(words) + r")(?!\w)", re.IGNORECASE) if words else None
    return _Rules(plain, label, assign, bare, count)


def rules_for(config: Config) -> _Rules:
    settings = config.credentials
    return _rules(settings.label_words, settings.joining_words, settings.count_words)


def _value_span(text: str, start: int, end: int) -> tuple[int, int]:
    """The value inside a token: a quoted value whole (to its closing mark on the same line), else the token
    without the brackets, quotes and punctuation at its edges."""
    opener = text[start] if start < end else ""
    if opener in _QUOTE_PAIRS:
        line_end = text.find("\n", start)
        line_end = len(text) if line_end < 0 else line_end
        close = text.find(_QUOTE_PAIRS[opener], start + 1, min(line_end, start + 1 + MAX_QUOTED))
        if close > start + 1:
            return start + 1, close
    while start < end and text[start] in _OPENERS:
        start += 1
    while end > start and text[end - 1] in _CLOSERS:
        end -= 1
    return start, end


def _not_a_value(core: str) -> bool:
    if REDACTED in core:
        return True
    if "/" in core or "\\" in core or _MAIL_RE.fullmatch(core):
        return True
    return bool(_DATE_RE.fullmatch(core) or _TIME_RE.fullmatch(core))


def _counted(rules: _Rules, text: str, end: int) -> bool:
    return rules.count is not None and rules.count.match(text, end) is not None


def _step(rules: _Rules, text: str, after: re.Pattern[str], accept: Callable[[str], bool]) -> str:
    """Each value that ``after`` finds right after a label word and ``accept`` takes, replaced by REDACTED."""
    assert rules.label is not None
    pieces: list[str] = []
    cursor = 0
    for found in rules.label.finditer(text):
        if found.start() < cursor:
            continue
        match = after.match(text, found.end())
        if match is None or REDACTED in match.group("value"):
            continue
        start, end = _value_span(text, *match.span("value"))
        core = text[start:end]
        if not core or not accept(core) or _counted(rules, text, max(end, match.end("value"))):
            continue
        pieces += [text[cursor:start], REDACTED]
        cursor = end
    if not pieces:
        return text
    pieces.append(text[cursor:])
    return "".join(pieces)


def _assigned(core: str) -> bool:
    return REDACTED not in core and any(character.isalnum() for character in core)


def _bare(core: str) -> bool:
    return MIN_BARE <= len(core) <= MAX_BARE and any(character.isdigit() for character in core) \
        and not _not_a_value(core)


def mask_labelled(text: str, *, config: Config, guarded: bool = True) -> str:
    """``text`` with every value after a label word replaced by ``[REDACTED]`` (the two steps above, in turn).

    ``guarded=False`` runs each step whatever the text holds; it exists to prove the guards change nothing.
    """
    rules = rules_for(config)
    if rules.plain is None or rules.label is None:
        return text
    holds_label = (not guarded) or rules.plain.search(text) is not None
    if not holds_label:
        return text
    if not guarded or any(mark in text for mark in _ASSIGN_MARKS):
        text = _step(rules, text, rules.assign, _assigned)
    if not guarded or any(character.isdigit() for character in text):
        text = _step(rules, text, rules.bare, _bare)
    return text


_LOOKUP: Final[Any] = object()  # ``ledger`` not given: find the forest's ledger from ``config``


def mask(text: str, *, config: Config, ledger: Any = _LOOKUP) -> str:
    """Every sign-in value ``text`` holds replaced by ``[REDACTED]``: after a label word, and, in a forest
    that keeps a judged ledger (``credential_ledger``), every value a reader judged a sign-in value.

    A caller masking many texts passes the ledger it loaded once (or None for none); without it the ledger is
    looked up for each call."""
    from . import credential_ledger

    text = mask_labelled(text, config=config)
    known = credential_ledger.for_config(config) if ledger is _LOOKUP else ledger
    return credential_ledger.mask_known(text, known)


def shown(value: Any, *, config: Config, ledger: Any = _LOOKUP) -> Any:
    """What a tool shows (a text, or the strings of a JSON-like value) with its sign-in values masked and its
    numbers starred: the read path hides what the write path keeps out."""
    from . import credential_ledger, numbers

    if ledger is _LOOKUP:
        ledger = credential_ledger.for_config(config)
    if isinstance(value, str):
        return numbers.star_numbers(mask(value, config=config, ledger=ledger), config=config)[0]
    if isinstance(value, dict):
        return {key: shown(item, config=config, ledger=ledger) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [shown(item, config=config, ledger=ledger) for item in value]
    return value


def fingerprint(config: Config, ledger: Any = _LOOKUP) -> str:
    """One digest of everything ``shown`` reads: ``MASKING_VERSION``, the number and sign-in settings, and the
    set of sign-in digests of the ledger. A text masked under one fingerprint may show more under another, so
    whatever keeps masked text (the search index) is rebuilt when it changes. The digest names no value."""
    from . import credential_ledger

    if ledger is _LOOKUP:
        ledger = credential_ledger.for_config(config)
    signed = sorted(ledger.sign_in) if ledger is not None else []
    material = {
        "credentials": asdict(config.credentials),
        "ledger": hashlib.sha256("\n".join(signed).encode("ascii")).hexdigest() if signed else "",
        "numbers": asdict(config.numbers),
        "version": MASKING_VERSION,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def name_problem(names: Iterable[str], *, config: Config) -> str | None:
    """Why one of ``names`` cannot stand as a name (contract, section 2), or None.

    A name says who or what a subject is. One built from the words of what was sent before digits
    (``Courier verification code 4821``) reads as a sign-in value: every line that carries it is a
    ``credential_in_full`` line, every read shows it masked, and ``fix`` would take the name off the subject's
    own pages. A name that already holds the mark has lost that part. The reason shows the masked form, never
    the name.
    """
    for name in names:
        text = str(name or "")
        if REDACTED in text:
            return (f"the name `{text}` holds a masked value, which names nothing; "
                    "name it by who sends or what it is")
        masked = mask(text, config=config) if text else text
        if masked != text:
            return (f"the name `{masked}` reads as a sign-in value, which every read masks; "
                    "name it by who sends or what it is, not by the words of what was sent")
    return None


def changed_lines(text: str, *, config: Config) -> list[int]:
    """The 1-based numbers of the lines ``mask`` would change (never the values themselves)."""
    if mask(text, config=config) == text:
        return []
    found = []
    for number, line in enumerate(text.split("\n"), start=1):
        if mask(line, config=config) != line:
            found.append(number)
    return found


# ─────────────────────────────── pages ───────────────────────────────


@dataclass(slots=True)
class PageCredentials:
    path: str
    lines: list[int]
    written: bool = False
    archive_path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"archive": self.archive_path, "lines": self.lines, "path": self.path, "written": self.written}


def _chosen(root: Any, paths: Iterable[str], all_pages: bool) -> list[str]:
    chosen = [layout.normalize(path) for path in paths]
    for path in chosen:
        if not layout.classify(path).in_forest:
            raise MemoryForestError("credentials_path_invalid", "Only pages of the four forest layers are masked.",
                                    details={"path": path})
    if all_pages or not chosen:
        chosen += [path for path in layout.iter_pages(root) if path not in chosen]
    return chosen


def check(
    root: str | os.PathLike[str], paths: Iterable[str] = (), *, all_pages: bool = False, config: Config | None = None
) -> list[PageCredentials]:
    """The pages that hold a sign-in value (every page when none is named): their line numbers only."""
    safe_root = require_real_root(root)
    settings = config if config is not None else load_config(safe_root)
    found = []
    for path in _chosen(safe_root, paths, all_pages):
        lines = changed_lines(read_text(layout.resolve(safe_root, path, must_exist=True)), config=settings)
        if lines:
            found.append(PageCredentials(path, lines))
    return found


def fix(
    root: str | os.PathLike[str],
    paths: Iterable[str] = (),
    *,
    all_pages: bool = False,
    config: Config | None = None,
    dry_run: bool = False,
    lock: LockToken | str | None = None,
) -> list[PageCredentials]:
    """Mask the sign-in values of the named pages (every page when none is named).

    Each page is archived first (a kept copy with the reason ``credential-masking``); the copy must read back
    to the page's bytes before the masked page is written whole. No record line is written: a record line
    would have to say what was removed.
    """
    safe_root = require_real_root(root)
    settings = config if config is not None else load_config(safe_root)
    found = check(safe_root, paths, all_pages=all_pages, config=settings)
    if dry_run or not found:
        return found
    with writer_lock(safe_root, token=lock, label="credentials fix") as held:
        for item in found:
            target = layout.resolve(safe_root, item.path, must_exist=True)
            data = target.read_bytes()
            text = data.decode("utf-8", "surrogateescape")
            new_text = mask(text, config=settings)
            if changed_lines(new_text, config=settings):
                raise MemoryForestError("credentials_fix_incomplete", "A sign-in value would still stand; not written.",
                                        details={"path": item.path})
            entry = forest_archive.put(safe_root, item.path, reason=FIX_REASON, keep=True, lock=held)
            if forest_archive.read_copy(safe_root, entry) != data or target.read_bytes() != data:
                raise MemoryForestError("credentials_archive_mismatch",
                                        "The archive copy does not hold the page as it was; not written.",
                                        details={"path": item.path})
            atomic_write(target, new_text.encode("utf-8", "surrogateescape"))
            item.written = True
            item.archive_path = entry.archive_path
    return found


__all__ = [
    "FIX_REASON",
    "MASKING_VERSION",
    "REDACTED",
    "PageCredentials",
    "changed_lines",
    "check",
    "fingerprint",
    "fix",
    "mask",
    "mask_labelled",
    "shown",
]
