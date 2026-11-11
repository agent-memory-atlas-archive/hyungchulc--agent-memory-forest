"""A small made-up forest that passes the structure checks and the lint, for the tests to break one page at a time.

Every name below belongs to an invented world (a bicycle workshop kept by
Tobin Reel, its truing stand, a desk agent); none describes a real person,
company or product. Numbers in the tests are made up or reserved for
examples: the 555 exchange, the published test card range, an IBAN with a
country code no country has.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v1_support import ForestTestCase  # noqa: E402

SETTINGS = """[forest]
timezone = "Pacific/Auckland"

[owner]
id = "tobin-reel"
name = "Tobin"
aliases = ["Tobin Reel"]

[agents]
orchestrators = ["desk-bot"]
"""

STAND = "04 stm/workshop/truing-stand/repairs.md"
OWNER_LEAF = "04 stm/workshop/people/tobin-reel.md"
COLLECTION = "04 stm/workshop/people/seen-once-2051-q1.md"
AGENT_LEAF = "04 stm/desk-bot/runs/daily-runs.md"
STAND_BOARD = "03 mtm/workshop/truing-stand.md"
PEOPLE_BOARD = "03 mtm/workshop/people.md"
INDEX = "03 mtm/workshop/workshop_MTM.md"
TREE_PAGE = "02 ltm/workshop_LTM.md"
AGENT_PAGE = "02 ltm/desk-bot_LTM.md"
ROOT_INDEX = "01 xltm/XLTM.md"


def leaf(entity: str, up: str, *, kind: str = "event", about: str | None = "none", subtopic: str = "repairs",
         body: str = "The stand was trued and checked.", timeline: str = "- 2051-03-02 10:00 Pacific/Auckland — trued the stand.",
         record: str = "", extra: str = "") -> str:
    head = ["---", f"entity: {entity}", f"subtopic: {subtopic}", f"kind: {kind}"]
    if about is not None:
        head.append(f"about: {about}")
    if extra:
        head.append(extra)
    head += ["timezone: Pacific/Auckland", "updated_at: 2051-03-02", "---"]
    parts = ["\n".join(head), "", f"# {entity}", "", body, "", "## Timeline", "", timeline, "", "## Links", "",
             f"- up: [[{up}|up]]", "", "## Update record", ""]
    text = "\n".join(parts) + "\n"
    if record:
        text += "\n" + record.rstrip("\n") + "\n"
    return text


def files() -> dict[str, str]:
    return {
        "forest.toml": SETTINGS,
        ROOT_INDEX: (
            "# XLTM\n\n## Identity\n\n- Name: Tobin Reel; memory text calls him Tobin.\n\n"
            "## Trees\n\n- `workshop` — the bicycle workshop: its tools, its repairs, its people.\n\n"
            "## Agents\n\n- `desk-bot` — Desk Bot · orchestrator: talks with Tobin\n"
        ),
        TREE_PAGE: (
            "# workshop\n\n## Scope\n\nThe bicycle workshop.\n\n## Patterns and rules\n\n## Current state\n\n"
            "- The truing stand works again (as of 2051-03-02; source `04 stm/workshop/truing-stand/repairs.md`)\n\n"
            "## Branches\n\n- `truing-stand` — the shop's truing stand\n- `people` — the people Tobin meets\n\n"
            "## Links\n\n- up: [[01 xltm/XLTM|XLTM]]\n\n## Update record\n"
        ),
        AGENT_PAGE: (
            "---\nentity: desk-bot\n---\n\n# Desk Bot\n\n## Role\n\nTalks with Tobin.\n\n## Standing instructions\n\n"
            "## Lessons\n\n## Current state\n\n## Branches\n\n- `runs` — its runs\n\n"
            "## Links\n\n- up: [[01 xltm/XLTM|XLTM]]\n\n## Update record\n"
        ),
        INDEX: (
            "# workshop index\n\n## Subjects\n\n"
            "- `truing-stand` — Truing Stand · device · branch · `03 mtm/workshop/truing-stand.md` · aliases: the stand\n"
            "- `tobin-reel` — Tobin Reel · person · leaf · `04 stm/workshop/people/tobin-reel.md` · aliases: Tobin\n"
            "- `people-seen-once` — People seen once · collection · leaf · `04 stm/workshop/people/seen-once-2051-q1.md`\n"
            "\n## Branches\n\n- `truing-stand.md` — the truing stand\n- `people.md` — people\n\n"
            "## Links\n\n- up: [[02 ltm/workshop_LTM|workshop LTM]]\n\n## Update record\n"
        ),
        STAND_BOARD: (
            "---\nentity: truing-stand\n---\n\n# Truing Stand\n\n## About\n\nThe workshop's truing stand.\n\n"
            "## Current state\n\n"
            "- Works again after the clamp repair (as of 2051-03-02; source `04 stm/workshop/truing-stand/repairs.md`)\n\n"
            "## Leaves\n\n- `repairs.md` — its repairs (updated 2051-03-02)\n\n## Pointers\n\n"
            "## Links\n\n- up: [[02 ltm/workshop_LTM|workshop LTM]]\n\n## Update record\n"
        ),
        PEOPLE_BOARD: (
            "# People\n\n## About\n\nPeople Tobin meets.\n\n## Current state\n\n## Leaves\n\n"
            "- `tobin-reel.md` — Tobin himself (updated 2051-03-02)\n"
            "- `seen-once-2051-q1.md` — people seen once (updated 2051-03-02)\n\n## Pointers\n\n"
            "## Links\n\n- up: [[02 ltm/workshop_LTM|workshop LTM]]\n\n## Update record\n"
        ),
        "03 mtm/desk-bot/desk-bot_MTM.md": (
            "# desk-bot index\n\n## Subjects\n\n- `desk-bot` — Desk Bot · agent · tree · `02 ltm/desk-bot_LTM.md`\n\n"
            "## Branches\n\n- `runs.md` — its runs\n\n## Links\n\n- up: [[02 ltm/desk-bot_LTM|desk-bot LTM]]\n\n"
            "## Update record\n"
        ),
        "03 mtm/desk-bot/runs.md": (
            "---\nentity: desk-bot\n---\n\n# Desk Bot: runs\n\n## About\n\nIts runs.\n\n## Current state\n\n"
            "## Leaves\n\n- `daily-runs.md` — its runs (updated 2051-03-02)\n\n## Pointers\n\n"
            "## Links\n\n- up: [[02 ltm/desk-bot_LTM|desk-bot LTM]]\n\n## Update record\n"
        ),
        STAND: leaf("truing-stand", "03 mtm/workshop/truing-stand"),
        OWNER_LEAF: leaf("tobin-reel", "03 mtm/workshop/people", kind="profile", subtopic="profile",
                         body="Tobin keeps the workshop.", timeline="- 2051-03-02 Pacific/Auckland — opened the workshop."),
        AGENT_LEAF: leaf("desk-bot", "03 mtm/desk-bot/runs", kind="work", subtopic="runs",
                         body="The desk's runs.", timeline="- 2051-03-02 08:00 Pacific/Auckland — desk-bot ran the morning list."),
        COLLECTION: (
            "---\nentity: people-seen-once\nsubtopic: seen-once\nkind: collection\ntimezone: Pacific/Auckland\n"
            "updated_at: 2051-03-02\n---\n\n# People seen once (2051-q1)\n\nPeople Tobin met on one day.\n\n"
            "## Marit Olm\n\n- entry: `marit-olm` · person · aliases: Marit\n\nBrought a wheel to true.\n\n"
            "## Timeline\n\n- 2051-03-02 11:00 Pacific/Auckland — Marit Olm: brought a wheel to true.\n\n"
            "## Links\n\n- up: [[03 mtm/workshop/people|people MTM]]\n\n## Update record\n"
        ),
    }


class CheckForestTestCase(ForestTestCase):
    """A test case that writes the small forest above, with any pages replaced or removed."""

    def workshop(self, changes: dict[str, str | None] | None = None) -> Path:
        pages = files()
        for path, text in (changes or {}).items():
            if text is None:
                pages.pop(path, None)
            else:
                pages[path] = text
        return self.make_forest(pages, name=f"forest-{len(list(self.base.iterdir()))}")

    def edit(self, root: Path, path: str, old: str, new: str) -> None:
        target = root / path
        text = target.read_text(encoding="utf-8")
        self.assertIn(old, text)
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
