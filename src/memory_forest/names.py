"""An agent's name, its key and its id (design, section 6; contract, section 2, Ids from names).

**The key.** :func:`key` is the only comparison of agent names in the package: the name in NFKC, case-folded,
with white space, hyphens and dashes of every kind and underscores taken out. Every other character counts:
a letter with an accent is another letter, and a dot, a plus sign or an exclamation mark is part of the
name. ``Kiln Bot``, ``kiln_bot`` and ``KILN-BOT`` have one key; ``Kiln.Bot`` and ``Kíln Bot`` have others.

**Finding an agent.** :func:`find` takes the given text and the rows ``(id, name)`` of the agents there are.
The text names an agent in two ways only: it **is** the agent's id, character for character, or its key is
the key of the agent's stored name (an agent with no name is named by its id). Nothing else: the letters of
an id are never compared with the given text by any other rule.

**The id.** :func:`make_id` makes a handle once, when a new agent is admitted, and it is stored beside the
name; nothing computes it again. It is ASCII, as the contract's ids are, made letter for letter by one table
per script (the JSON files of ``letters/``, read by :func:`tables`): not a language's spelling, and no sound
changes. When any letter, digit or symbol of the name is carried by no table, the id is ``agent-`` and the
first six hexadecimal digits of the SHA-256 of the name's key; the carried rest is not used. An id longer than
:data:`NAME_MAX` is cut and ends in the same six digits. The id never makes two names one agent: a taken id
is followed by ``-agent`` (held by something that is not an agent) or by the smallest free number from 2
(held by an agent with another key), :func:`new_id`.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final

from . import system
from .errors import MemoryForestError

# The longest name, and the longest id, in characters (a file name holds an id).
NAME_MAX: Final[int] = 200
HASH_DIGITS: Final[int] = 6
HASH_PREFIX: Final[str] = "agent"
LETTERS_DIR: Final[Path] = Path(__file__).parent / "letters"
# Taken out of a name before its id is made, with nothing in their place: O'Neil is oneil.
_DROPPED: Final[frozenset[str]] = frozenset("'\u2018\u2019\u02bc\u02bb`\u00b4")
_LINE_BREAKS: Final[frozenset[str]] = frozenset("\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")


def _separator(character: str) -> bool:
    """White space, a hyphen or dash of any kind, or an underscore."""
    return (
        character.isspace()
        or character == "_"
        or character == "\u00ad"
        or unicodedata.category(character) == "Pd"
    )


def folded(name: str) -> str:
    """The name in NFKC, case-folded, and in NFKC again (case folding can undo the first)."""
    return unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", name).casefold())


def key(name: str) -> str:
    """The name's key: NFKC, case folding, and no white space, hyphens, dashes or underscores."""
    return "".join(character for character in folded(name) if not _separator(character))


def same(one: str, other: str) -> bool:
    """Whether two names are one agent's name: the same key."""
    return key(one) == key(other)


def check(given: object) -> str:
    """The name a caller gave, its outer white space trimmed, or the refusal for a bad argument.

    A bad argument is only: not text, empty or white space alone (``agent_unset``), longer than
    :data:`NAME_MAX` characters, or holding a control character or a line break (``agent_name_invalid``).
    Every other text is a name, in any script. No retry can pass these, so the move is ``fix``.
    """
    if not isinstance(given, str):
        raise MemoryForestError(
            "agent_name_invalid", "The agent is text: the caller's own name.", next_step="fix"
        )
    trimmed = given.strip()
    if not trimmed:
        raise MemoryForestError("agent_unset", "Name the caller: pass agent, the caller's own name.", next_step="fix")
    if len(trimmed) > NAME_MAX:
        raise MemoryForestError(
            "agent_name_invalid",
            f"A name is at most {NAME_MAX} characters. Pass the caller's own name.",
            details={"name": trimmed[:80]},
            next_step="fix",
        )
    if any(character in _LINE_BREAKS or unicodedata.category(character) == "Cc" for character in trimmed):
        raise MemoryForestError(
            "agent_name_invalid",
            "A name holds no control character and no line break. Pass the caller's own name.",
            details={"name": trimmed[:80].encode("unicode_escape").decode("ascii")},
            next_step="fix",
        )
    return trimmed


def bad_reason(given: object) -> str | None:
    """Why ``given`` is a bad argument, or None (the same rule as :func:`check`)."""
    try:
        check(given)
    except MemoryForestError as exc:
        return exc.message
    return None


def find(given: str, rows: Iterable[tuple[str, str]]) -> list[str]:
    """The ids ``given`` names among ``rows`` of ``(id, name)``, each once, in the order of the rows.

    The given text is an agent's id character for character, or its key is the key of an agent's name (a row
    with no name is named by its id). An id that matches wins over a key. An empty list is no agent; more than
    one id is ambiguous.
    """
    if not isinstance(given, str):
        return []
    listed = list(rows)
    exact = [agent_id for agent_id, _name in listed if given == agent_id]
    if exact:
        return exact[:1]
    wanted = key(given)
    found: list[str] = []
    for agent_id, name in listed:
        if key(name or agent_id) == wanted and agent_id not in found:
            found.append(agent_id)
    return found


# ─────────────────────────────── the tables ───────────────────────────────


@dataclass(frozen=True, slots=True)
class Table:
    """One script's letters: each key (one character, or two for a pair) and its ASCII letters."""

    name: str
    script: str
    letters: Mapping[str, str]
    doubling: frozenset[str]


@lru_cache(maxsize=1)
def tables() -> tuple[Table, ...]:
    """Every table of ``letters/``, by file name. A table that cannot be read is an error of the package."""
    found = []
    for entry in sorted(system.listing(LETTERS_DIR), key=lambda item: item.name):
        if entry.kind != "file" or not entry.name.endswith(".json"):
            continue
        data = json.loads(system.read_text(entry.path))
        found.append(Table(
            entry.name[: -len(".json")],
            str(data["script"]),
            {str(item): str(value) for item, value in dict(data["letters"]).items()},
            frozenset(str(item) for item in data.get("doubling", ())),
        ))
    return tuple(found)


@lru_cache(maxsize=1)
def _merged() -> tuple[Mapping[str, str], frozenset[str]]:
    letters: dict[str, str] = {}
    doubling: set[str] = set()
    for table in tables():
        letters.update(table.letters)
        doubling.update(table.doubling)
    return letters, frozenset(doubling)


def _hangul_apart(text: str) -> str:
    """Each Hangul syllable as its initial, vowel and final (the Unicode decomposition)."""
    return "".join(
        unicodedata.normalize("NFD", character) if 0xAC00 <= ord(character) <= 0xD7A3 else character
        for character in text
    )


def _base(character: str, letters: Mapping[str, str]) -> str | None:
    """A letter no table holds, taken apart and its marks dropped, when every part is carried; else None."""
    parts = unicodedata.normalize("NFKD", character)
    kept = [part for part in parts if not unicodedata.combining(part)]
    if not kept or "".join(kept) == character:
        return None
    out = []
    for part in kept:
        if part in letters:
            out.append(letters[part])
        elif part.isascii() and part.isalnum():
            out.append(part.lower())
        elif part in _DROPPED:
            continue
        elif _separator(part) or unicodedata.category(part).startswith("P"):
            out.append("-")
        else:
            return None
    return "".join(out)


def transliterated(name: str) -> str | None:
    """The name letter for letter in ASCII, separators as hyphens; None when a character is carried by no table."""
    letters, doubling = _merged()
    text = _hangul_apart(folded(name))
    pieces: list[str] = []
    double = False
    index = 0
    while index < len(text):
        character = text[index]
        pair = text[index : index + 2]
        if len(pair) == 2 and pair in letters:
            value = letters[pair]
            index += 2
        else:
            index += 1
            if character in doubling:
                double = True
                continue
            category = unicodedata.category(character)
            if character in _DROPPED or category.startswith("M"):
                continue
            if character in letters:
                value = letters[character]
            elif character.isascii() and character.isalnum():
                value = character
            elif _separator(character) or category.startswith("P"):
                pieces.append("-")
                double = False
                continue
            else:
                based = _base(character, letters)
                if based is None:
                    return None
                value = based
        if double and value and value[0] not in "aeiou-":
            value = ("t" if value.startswith("ch") else value[0]) + value
        double = False
        pieces.append(value)
    return "".join(pieces)


def _hash(name: str) -> str:
    return hashlib.sha256(key(name).encode("utf-8")).hexdigest()[:HASH_DIGITS]


def _tidy(text: str) -> str:
    out: list[str] = []
    for character in text:
        if character == "-" and (not out or out[-1] == "-"):
            continue
        out.append(character)
    return "".join(out).strip("-")


def make_id(name: str) -> str:
    """The id a new agent of this name is first offered (:func:`new_id` decides when it is taken).

    Letter for letter by the tables; ``agent-`` and six hexadecimal digits of the key's SHA-256 when a
    character is carried by no table, or nothing is left; cut to :data:`NAME_MAX` with the same digits.
    """
    carried = transliterated(name)
    slug = _tidy(carried) if carried is not None else ""
    if not slug:
        return f"{HASH_PREFIX}-{_hash(name)}"
    if len(slug) > NAME_MAX:
        slug = slug[: NAME_MAX - HASH_DIGITS - 1].rstrip("-") + "-" + _hash(name)
    return slug


def _fit(base: str, suffix: str) -> str:
    """``base`` and ``suffix`` within :data:`NAME_MAX`, the base cut first."""
    room = NAME_MAX - len(suffix)
    return base[:room].rstrip("-") + suffix


def new_id(name: str, held: Callable[[str], list[str]]) -> str:
    """The id of a new agent named ``name``: :func:`make_id`, unless something holds it.

    ``held(id)`` is the kinds of thing that hold an id (``agent`` for an agent). Held by something that is not
    an agent: the id, a hyphen and ``agent``, then ``-agent-2`` and on. Held by an agent (which has another
    key, or the name would have found it): the id, a hyphen and the smallest number from 2 that nothing holds.
    """
    base = make_id(name)
    kinds = held(base)
    if not kinds:
        return base
    number = 1
    by_agent_only = all(kind == "agent" for kind in kinds)
    while True:
        if by_agent_only:
            candidate = _fit(base, f"-{number + 1}")
        else:
            candidate = _fit(base, "-agent" if number == 1 else f"-agent-{number}")
        if not held(candidate):
            return candidate
        number += 1
