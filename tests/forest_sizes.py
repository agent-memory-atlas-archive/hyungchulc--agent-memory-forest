"""Forests of a chosen size, made from generated pages, and what one query costs on them at the door.

A forest of ``leaves`` leaves holds the pages of ``test_v1_find.FILES`` and, under ``04 stm/bakery``, branches of
``BRANCH`` leaves each, with a board per branch: every leaf is a tray of the bakery's proofing room, registered
on the bakery's index. ``costs`` counts, after a build with nothing changed, the door's calls (by name), the
bytes it read and the SQLite statements of one ``search``, one ``timeline`` read and one ``index status``.
``tests/test_v1_query_cost.py`` pins how the counts grow; ``scripts/query_cost.py`` prints them, with times.

The bakery and its trays are invented. Nothing here is a real person, shop or transcript.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memory_forest import search, system, timeline

from test_v1_find import FILES

BRANCH = 12  # leaves on one branch board
READER = "till-bot"
WORDS = "proofed"
PHRASE = "2051-03-02"
INDEX = "03 mtm/bakery/bakery_MTM.md"
SHELVES = ("upper", "middle", "lower")
LEAF = """---
entity: {ident}
subtopic: notes
kind: event
about: none
timezone: UTC
updated_at: 2051-03-02
---

# Tray {number}

Tray {number} proofed on the {shelf} shelf of the proofing room.

## Links

- up: [[03 mtm/bakery/{branch}|{branch}]]
"""


def branches(leaves: int) -> int:
    return math.ceil(leaves / BRANCH)


def generated(leaves: int) -> dict[str, str]:
    """The files of a forest with ``leaves`` generated leaves, each registered, on ``branches(leaves)`` boards."""
    files = dict(FILES)
    lines = []
    for number in range(1, leaves + 1):
        branch = f"batch-{(number - 1) // BRANCH + 1:04d}"
        ident = f"tray-{number:05d}"
        home = f"04 stm/bakery/{branch}/{ident}.md"
        files[home] = LEAF.format(ident=ident, number=number, shelf=SHELVES[number % 3], branch=branch)
        files.setdefault(f"03 mtm/bakery/{branch}.md", f"# {branch}\n\n## Leaves\n")
        lines.append(f"- `{ident}` — Tray {number} · device · leaf · `{home}`")
    head, tail = files[INDEX].split("\n\n## Branches", 1)
    files[INDEX] = head + "".join(f"\n{line}" for line in lines) + "\n\n## Branches" + tail
    return files


def make_forest(folder: Path, leaves: int) -> Path:
    """Write the generated forest into ``folder`` (which must not exist) and build its index."""
    for relative, text in generated(leaves).items():
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    built = search.build(folder)
    assert built.get("busy") is False, built
    return folder


@dataclass(frozen=True)
class Cost:
    """What one call did at the door: its calls by name, the bytes it read and the statements SQLite ran."""

    calls: dict[str, int]
    bytes_read: int
    statements: int

    @property
    def total(self) -> int:
        return sum(self.calls.values())


def cost(call: Callable[[], Any]) -> Cost:
    with system.watching() as watch:
        call()
    return Cost(dict(sorted(Counter(name for name, _path in watch.calls).items())), watch.bytes_read,
                watch.statements)


def calls(root: Path) -> dict[str, Callable[[], Any]]:
    """The three calls whose cost is counted, as an agent's search and timeline read and the owner's status."""
    return {
        "search": lambda: search.query(root, WORDS, reader=READER),
        "timeline": lambda: timeline.query(root, PHRASE, reader=READER),
        "index status": lambda: search.status(root),
    }


def costs(root: Path) -> dict[str, Cost]:
    return {name: cost(call) for name, call in calls(root).items()}
