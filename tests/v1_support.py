"""Helpers for the version 1 tests: made-up forests in private temporary folders.

Every name below belongs to an invented world (a bakery owner called Ilse
Varrow, her oven, her suppliers and two helper agents); none describes a
real person, company or product.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_FOREST = REPOSITORY_ROOT / "examples" / "synthetic-forest"

SETTINGS = """[forest]
timezone = "Pacific/Auckland"

[owner]
id = "ilse-varrow"
name = "Ilse"
aliases = ["Ilse Varrow"]

[agents]
orchestrators = ["front-desk"]
"""

ROOT_INDEX = """# XLTM

## Identity

- Name: Ilse Varrow; memory text calls her Ilse.

## Trees

- `bakery` — the bakery: its ovens, recipes and suppliers.
- `people` — the people Ilse knows.

## Agents

- `front-desk` — Front Desk · orchestrator
"""


def leaf_text(entity: str, *, kind: str = "event", about: str | None = "none", extra: str = "", body: str = "") -> str:
    lines = ["---", f"entity: {entity}", "subtopic: notes", f"kind: {kind}"]
    if about is not None:
        lines.append(f"about: {about}")
    if extra:
        lines.append(extra)
    lines += ["updated_at: 2051-03-02", "---", "", f"# {entity}", "", body or "Something happened.", ""]
    return "\n".join(lines)


def index_text(tree: str, subjects: list[str]) -> str:
    return (
        f"# {tree} index\n\n## Subjects\n\n"
        + "".join(line + "\n" for line in subjects)
        + f"\n## Branches\n\n## Links\n\n- up: [[02 ltm/{tree}_LTM|{tree} LTM]]\n\n## Update record\n"
    )


BAKERY_FILES = {
    "forest.toml": SETTINGS,
    "01 xltm/XLTM.md": ROOT_INDEX,
    "02 ltm/bakery_LTM.md": "# bakery\n\n## Scope\n",
    "02 ltm/people_LTM.md": "# people\n\n## Scope\n",
    "02 ltm/front-desk_LTM.md": "---\nentity: front-desk\n---\n\n# Front Desk\n\n## Role\n",
    "03 mtm/bakery/bakery_MTM.md": index_text(
        "bakery",
        [
            "- `deck-oven` — Deck Oven · device · branch · `03 mtm/bakery/deck-oven.md` · aliases: the old oven; DO-3",
            "- `millstone-flour` — Millstone Flour · organization · leaf · `04 stm/bakery/suppliers/millstone-flour.md`"
            " · aliases: Millstone",
        ],
    ),
    "03 mtm/bakery/deck-oven.md": "---\nentity: deck-oven\n---\n\n# Deck Oven\n\n## Pointers\n\n"
    "- `04 stm/bakery/suppliers/millstone-flour.md` — Millstone Flour: what it delivered\n",
    "03 mtm/bakery/suppliers.md": "# suppliers\n\n## Leaves\n",
    "03 mtm/people/people_MTM.md": index_text(
        "people",
        [
            "- `ilse-varrow` — Ilse Varrow · person · leaf · `04 stm/people/self/ilse-varrow.md` · aliases: Ilse",
            "- `regulars-seen-once` — Regulars seen once · collection · leaf · "
            "`04 stm/people/regulars/seen-once-2051-q1.md`",
        ],
    ),
    "03 mtm/front-desk/front-desk_MTM.md": index_text(
        "front-desk",
        ["- `front-desk` — Front Desk · agent · tree · `02 ltm/front-desk_LTM.md` · aliases: the desk"],
    ),
    "04 stm/bakery/deck-oven/repairs.md": leaf_text("deck-oven"),
    "04 stm/bakery/suppliers/millstone-flour.md": leaf_text(
        "millstone-flour", kind="purchase", about="deck-oven, ilse-varrow, nobody-known"
    ),
    "04 stm/people/self/ilse-varrow.md": leaf_text("ilse-varrow", kind="profile"),
    "04 stm/people/regulars/seen-once-2051-q1.md": """---
entity: regulars-seen-once
subtopic: seen-once
kind: collection
timezone: Pacific/Auckland
updated_at: 2051-03-02
---

# Regulars seen once (2051-q1)

People who came in on one day.

## Tamsin Orrow (Тамсин Орров)

- entry: `tamsin-orrow` · person · aliases: Tam
- kind: event

Asked for rye loaves on Fridays.

## Bram Eskell

- entry: `bram-eskell` · person

Delivered a crate of apples.

## Timeline

- 2051-03-02 09:10 Pacific/Auckland — Tamsin Orrow: asked for rye loaves.

## Links

- up: [[03 mtm/people/regulars|regulars MTM]]

## Update record
""",
}


class ForestTestCase(unittest.TestCase):
    """A test case with a private, real (symlink-free) temporary folder."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.base = Path(self._temporary.name).resolve()
        self.addCleanup(self._temporary.cleanup)

    def make_forest(self, files: dict[str, str] | None = None, *, name: str = "forest") -> Path:
        root = self.base / name
        root.mkdir(mode=0o700)
        for relative, text in (BAKERY_FILES if files is None else files).items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        return root

    def copy_synthetic(self) -> Path:
        root = self.base / "synthetic"
        shutil.copytree(SYNTHETIC_FOREST, root)
        return root
