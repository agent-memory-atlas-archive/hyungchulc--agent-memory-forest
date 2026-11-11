"""Account and identity numbers, and a person's phone number (contract, section 4).

A bank account number, an IBAN, a card number and a person's phone number
are kept with their middle starred: the first three and the last four
characters stay. A national identity number loses the trailing characters its
format names. What makes a number one of these:

- an IBAN: two letters, two check digits and up to 30 letters and digits whose
  ISO 7064 mod-97 check holds, whatever the country letters;
- a card number: 13 to 19 digits whose Luhn check digit holds, unless a
  reference word (``numbers.reference_words``: an order, a parcel, …) stands
  closer before it than any account word;
- an account number: 8 to 34 digits with an account word or a bank's name
  (``numbers.account_words``) shortly before it on the same line, and no
  reference word closer;
- an identity number: a shape of ``numbers.identity_formats`` whose leading
  digits read as a birth date (``YYYYMMDD`` or ``YYMMDD``) or that follows an
  identity word (``numbers.identity_words``);
- a phone number: the international form, a plus sign and 8 to 15 digits with
  spaces, hyphens, dots or parentheses between groups, always; a national form
  only through ``numbers.phone_patterns``. A number that a ``numbers.keep_whole``
  pattern matches is a company's or a service's line and stays whole.

A date, an amount, a number inside a URL and a number already starred stay as
they are. ``find_numbers`` never returns the digits to a caller that prints:
reports name the line and the kind only.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Any, Final

from . import archive as forest_archive
from . import layout
from .config import Config, load_config
from .errors import MemoryForestError
from .locking import LockToken, atomic_write, writer_lock
from .safety import require_real_root
from .view import read_text


STAR_REASON: Final[str] = "number-starring"
KEEP_HEAD: Final[int] = 3
KEEP_TAIL: Final[int] = 4
# How far before a number (in characters, on its line) a word that marks it is read.
WORD_WINDOW: Final[int] = 32
ACCOUNT_MIN_DIGITS: Final[int] = 8
ACCOUNT_MAX_DIGITS: Final[int] = 34
KINDS: Final[tuple[str, ...]] = ("account", "iban", "card", "identity", "phone")

_URL_RE: Final[re.Pattern[str]] = re.compile(r"[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
_IBAN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9*])[A-Z]{2}[0-9]{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?(?![A-Za-z0-9*])"
)
_PHONE_RE: Final[re.Pattern[str]] = re.compile(r"(?<![\w*+])\+\(?[0-9][0-9 ().-]*[0-9](?![\w*])")
_RUN_RE: Final[re.Pattern[str]] = re.compile(r"(?<![\w*.,/:+-])[0-9]+(?:[ .-][0-9]+)*(?![\w*:])")
_DATE_RE: Final[re.Pattern[str]] = re.compile(r"[0-9]{4}[-./][0-9]{2}[-./][0-9]{2}")


@dataclass(frozen=True, slots=True)
class NumberSpan:
    """One number written in full: where it is, what kind it is, and how it is written starred."""

    start: int
    end: int
    kind: str
    starred: str

    def line(self, text: str) -> int:
        return text.count("\n", 0, self.start) + 1


def _digits(text: str) -> str:
    return "".join(character for character in text if character.isdigit())


def star_middle(text: str, *, head: int = KEEP_HEAD, tail: int = KEEP_TAIL) -> str:
    """``text`` with every letter and digit starred except the first ``head`` and last ``tail``.

    A leading plus sign counts as one of the first characters; spaces, hyphens,
    dots and parentheses stay where they are.
    """
    positions = [index for index, character in enumerate(text) if character.isalnum() or character == "+"]
    hidden = set(positions[head : max(head, len(positions) - tail)])
    return "".join("*" if index in hidden else character for index, character in enumerate(text))


def names_a_number(text: str) -> bool:
    """Whether a name or an id is nothing but a number of seven or more digits (a contact saved under its
    phone number): digits, with spaces, hyphens, dots, parentheses and a leading plus between them."""
    value = str(text).strip()
    if value.startswith("+"):
        value = value[1:]
    compact = re.sub(r"[ .()\-]", "", value)
    return len(compact) >= 7 and compact.isdigit()


def luhn_holds(digits: str) -> bool:
    total = 0
    for position, character in enumerate(reversed(digits)):
        value = int(character)
        if position % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return bool(digits) and total % 10 == 0


def iban_holds(text: str) -> bool:
    compact = text.replace(" ", "")
    if not 15 <= len(compact) <= 34:
        return False
    moved = compact[4:] + compact[:4]
    number = "".join(str(int(character, 36)) for character in moved)
    return int(number) % 97 == 1


def _reads_as_birth_date(digits: str) -> bool:
    for width, century in ((8, None), (6, 1900), (6, 2000)):
        if len(digits) < width:
            continue
        head = digits[:width]
        try:
            if width == 8:
                date(int(head[:4]), int(head[4:6]), int(head[6:8]))
            else:
                date(century + int(head[:2]), int(head[2:4]), int(head[4:6]))
        except ValueError:
            continue
        return True
    return False


@lru_cache(maxsize=64)
def _word_re(words: tuple[str, ...]) -> re.Pattern[str] | None:
    forms = sorted({word.strip() for word in words if word.strip()}, key=len, reverse=True)
    if not forms:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(word) for word in forms) + r")(?!\w)", re.IGNORECASE)


def _nearest_word(text: str, start: int, words: tuple[str, ...]) -> int | None:
    """How many characters before ``start`` the nearest of ``words`` ends, on the same line and in the window."""
    pattern = _word_re(words)
    if pattern is None:
        return None
    line_start = text.rfind("\n", 0, start) + 1
    window_start = max(line_start, start - WORD_WINDOW - 40)
    nearest = None
    for found in pattern.finditer(text, window_start, start):
        distance = start - found.end()
        if distance <= WORD_WINDOW:
            nearest = distance
    return nearest


def _closer(marking: int | None, reference: int | None) -> bool:
    """Whether a marking word stands before a number and no reference word stands closer to it."""
    return marking is not None and (reference is None or marking < reference)


@lru_cache(maxsize=64)
def _patterns(values: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(value) for value in values)


def _keep_whole(number: str, config: Config) -> bool:
    compact = "".join(character for character in number if character.isdigit() or character == "+")
    return any(
        pattern.search(number) or pattern.search(compact) for pattern in _patterns(config.numbers.keep_whole)
    )


def find_numbers(text: str, *, config: Config) -> list[NumberSpan]:
    """Every number of the kinds above that ``text`` writes in full, in the order they appear."""
    taken: list[tuple[int, int]] = [(found.start(), found.end()) for found in _URL_RE.finditer(text)]
    spans: list[NumberSpan] = []

    def free(start: int, end: int) -> bool:
        return all(end <= left or start >= right for left, right in taken)

    def add(start: int, end: int, kind: str | None, starred: str = "") -> None:
        taken.append((start, end))
        if kind is not None:
            spans.append(NumberSpan(start, end, kind, starred))

    numbers = config.numbers
    phone_candidates = [(found.start(), found.end(), 8, 15) for found in _PHONE_RE.finditer(text)]
    for pattern in _patterns(numbers.phone_patterns):
        phone_candidates += [(found.start(), found.end(), 1, 99) for found in pattern.finditer(text)]
    for start, end, least, most in sorted(phone_candidates):
        written = text[start:end]
        if not free(start, end) or "*" in written or not least <= len(_digits(written)) <= most:
            continue
        add(start, end, None if _keep_whole(written, config) else "phone", star_middle(written))

    for found in _IBAN_RE.finditer(text):
        if free(*found.span()) and iban_holds(found.group()):
            add(*found.span(), "iban", star_middle(found.group()))

    for form in numbers.identity_formats:
        for found in form.pattern().finditer(text):
            if not free(*found.span()):
                continue
            by_word = _nearest_word(text, found.start(), numbers.identity_words) is not None
            if by_word or _reads_as_birth_date(_digits(found.group())):
                add(*found.span(), "identity", form.mask(found.group()))

    for found in _RUN_RE.finditer(text):
        start, end = found.span()
        written = found.group()
        digits = _digits(written)
        if not free(start, end) or _DATE_RE.fullmatch(written):
            continue
        account = _nearest_word(text, start, numbers.account_words)
        reference = _nearest_word(text, start, numbers.reference_words)
        if 13 <= len(digits) <= 19 and luhn_holds(digits) and (reference is None or _closer(account, reference)):
            add(start, end, "card", star_middle(written))
        elif ACCOUNT_MIN_DIGITS <= len(digits) <= ACCOUNT_MAX_DIGITS and _closer(account, reference):
            add(start, end, "account", star_middle(written))
    return sorted(spans, key=lambda span: span.start)


def star_numbers(text: str, *, config: Config) -> tuple[str, Counter[str]]:
    """``text`` with every number of ``find_numbers`` starred, and how many of each kind there were."""
    spans = find_numbers(text, config=config)
    pieces: list[str] = []
    cursor = 0
    for span in spans:
        pieces.append(text[cursor : span.start])
        pieces.append(span.starred)
        cursor = span.end
    pieces.append(text[cursor:])
    return "".join(pieces), Counter(span.kind for span in spans)


# ─────────────────────────────── pages ───────────────────────────────


@dataclass(slots=True)
class PageNumbers:
    path: str
    lines: list[int]
    kinds: dict[str, int]
    written: bool = False
    archive_path: str | None = None

    @property
    def count(self) -> int:
        return sum(self.kinds.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "archive": self.archive_path,
            "count": self.count,
            "kinds": self.kinds,
            "lines": self.lines,
            "path": self.path,
            "written": self.written,
        }


def _chosen(root: Any, paths: Iterable[str], all_pages: bool) -> list[str]:
    chosen = [layout.normalize(path) for path in paths]
    for path in chosen:
        if not layout.classify(path).in_forest:
            raise MemoryForestError("numbers_path_invalid", "Only pages of the four forest layers are starred.",
                                    details={"path": path})
    if all_pages or not chosen:
        chosen += [path for path in layout.iter_pages(root) if path not in chosen]
    return chosen


def page_numbers(text: str, path: str, *, config: Config) -> PageNumbers | None:
    spans = find_numbers(text, config=config)
    if not spans:
        return None
    return PageNumbers(
        path=path,
        lines=sorted({span.line(text) for span in spans}),
        kinds=dict(sorted(Counter(span.kind for span in spans).items())),
    )


def check(
    root: str | os.PathLike[str], paths: Iterable[str] = (), *, all_pages: bool = False, config: Config | None = None
) -> list[PageNumbers]:
    """The pages that write a number in full (every page when none is named): lines and kinds, never digits."""
    safe_root = require_real_root(root)
    settings = config if config is not None else load_config(safe_root)
    found = []
    for path in _chosen(safe_root, paths, all_pages):
        item = page_numbers(read_text(layout.resolve(safe_root, path, must_exist=True)), path, config=settings)
        if item is not None:
            found.append(item)
    return found


def star(
    root: str | os.PathLike[str],
    paths: Iterable[str] = (),
    *,
    all_pages: bool = False,
    config: Config | None = None,
    dry_run: bool = False,
    lock: LockToken | str | None = None,
) -> list[PageNumbers]:
    """Star the numbers written in full on the named pages (every page when none is named).

    Each page is archived first (a kept copy with the reason
    ``number-starring``, the receipt of the change) and then written whole; no
    record line is added, since no statement of the page changes.
    """
    safe_root = require_real_root(root)
    settings = config if config is not None else load_config(safe_root)
    found = check(safe_root, paths, all_pages=all_pages, config=settings)
    if dry_run or not found:
        return found
    with writer_lock(safe_root, token=lock, label="numbers star") as held:
        for item in found:
            target = layout.resolve(safe_root, item.path, must_exist=True)
            text = read_text(target)
            new_text, _counts = star_numbers(text, config=settings)
            if find_numbers(new_text, config=settings):
                raise MemoryForestError("numbers_star_incomplete", "A number would still stand in full; not written.",
                                        details={"path": item.path})
            entry = forest_archive.put(safe_root, item.path, reason=STAR_REASON, keep=True, lock=held)
            if read_text(target) != text:
                raise MemoryForestError("numbers_page_changed", "The page changed while it was archived; not written.",
                                        details={"path": item.path})
            atomic_write(target, new_text.encode("utf-8", "surrogateescape"))
            item.written = True
            item.archive_path = entry.archive_path
    return found


__all__ = [
    "KINDS",
    "NumberSpan",
    "PageNumbers",
    "check",
    "find_numbers",
    "iban_holds",
    "luhn_holds",
    "names_a_number",
    "star",
    "star_middle",
    "star_numbers",
]
