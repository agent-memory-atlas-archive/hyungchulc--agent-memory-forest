"""Every record line a tool writes is one of the three kinds, on the page it is about (contract, section 4).

Each operation runs on a fresh made-up forest. Afterwards ``forest record
kinds`` counts no run's note, ``forest lint`` reports no ``record_run_note``,
and a page that only referred to what moved keeps its ``## Update record``
byte for byte (the move log is the record of that rewrite).
"""

from __future__ import annotations

import bounds  # every invocation has a deadline

import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v1_collections import COLLECTION, CollectionTestCase  # noqa: E402
from test_v1_move import MILLSTONE, REPAIRS, TODAY, stream_page  # noqa: E402
from v1_support import SYNTHETIC_FOREST, ForestTestCase, leaf_text  # noqa: E402

from memory_forest import collections, levels, lint, maps, move, numbers, record, repeats  # noqa: E402
from memory_forest.config import load_config  # noqa: E402
from memory_forest.model import RECORD_SECTION, parse_page  # noqa: E402

# A page that names the leaves an operation moves, with a record line of its own.
REFERRER = "04 stm/people/self/ilse-varrow.md"
REFERRER_RECORD = ("- 2051-03-01 — Opening hours: was \"8 to 4\", now \"7 to 3\". Why: the summer timetable.\n")


def record_bytes(root: Path, path: str) -> bytes:
    section = parse_page((root / path).read_text(encoding="utf-8")).section(RECORD_SECTION)
    return "".join(section.lines).encode("utf-8") if section is not None else b""


class ToolRecordTests(CollectionTestCase):
    def forest(self) -> Path:
        root = super().forest()
        page = root / REFERRER
        text = page.read_text(encoding="utf-8").replace(
            "Something happened.",
            f"Buys from `{MILLSTONE}`; the oven's fixes are in `{REPAIRS}`; regulars in `{COLLECTION}`.")
        page.write_text(text + "\n## Update record\n\n" + REFERRER_RECORD, encoding="utf-8")
        (root / "04 stm/bakery/deck-oven/firing.md").write_text(
            leaf_text("deck-oven").replace("subtopic: notes", "subtopic: firing"), encoding="utf-8")
        maps.apply(root)
        return root

    def assertOnlyThreeKinds(self, root: Path, what: str) -> None:
        report = record.kinds(root, examples=3)
        self.assertEqual(report.totals.get(record.RUN_NOTE, 0), 0, f"{what}: {report.examples}")
        hits = lint.lint(root, codes=["record_run_note"]).hits
        self.assertEqual([hit.render() for hit in hits], [], what)

    def assertReferrerRecordKept(self, root: Path, before: bytes, what: str) -> None:
        if (root / REFERRER).exists():
            self.assertEqual(record_bytes(root, REFERRER), before, what)

    def test_each_move_operation(self) -> None:
        cases = [
            ("leaf", {"source": REPAIRS, "target": "04 stm/bakery/deck-oven/fixes.md"}),
            ("leaf", {"source": MILLSTONE, "target": "04 stm/bakery/deck-oven", "entity": "deck-oven"}),
            ("rename", {"target": "deck-oven", "new": "hearth-oven", "name": "Hearth Oven"}),
            ("rename", {"target": "bakery/suppliers", "new": "mills"}),
            ("promote", {"subject": "millstone-flour"}),
            ("promote", {"subject": "deck-oven"}),
            ("merge", {"keep": "deck-oven", "drop": "millstone-flour"}),
            ("archive", {"path": REPAIRS, "reason": "the oven was sold"}),
            ("move-branch", {"branch": "bakery/deck-oven", "tree": "people"}),
            ("new-tree", {"tree": "garden", "description": "the herb garden."}),
            ("new-agent", {"id": "till-bot", "name": "Till Bot", "role": "counts the till"}),
            ("retire-tree", {"tree": "front-desk"}),
            ("set-type", {"subject": "deck-oven", "type": "component"}),
            ("set", {"path": REPAIRS, "kind": "plan", "about": "millstone-flour"}),
        ]
        for operation, arguments in cases:
            with self.subTest(operation=operation, arguments=arguments):
                root = self.make_root(operation)
                before = record_bytes(root, REFERRER)
                move.execute(root, operation, today=TODAY, **arguments)
                self.assertOnlyThreeKinds(root, operation)
                self.assertReferrerRecordKept(root, before, operation)

    def make_root(self, name: str) -> Path:
        """A fresh copy of the made-up forest in a folder of its own."""
        self._count = getattr(self, "_count", 0) + 1
        self._name = f"{name}-{self._count}"
        return self.forest()

    def make_forest(self, files=None, *, name: str = "forest") -> Path:  # type: ignore[override]
        return super().make_forest(files, name=getattr(self, "_name", name))

    def test_fold_tree_and_recut_stream(self) -> None:
        root = self.make_root("fold")
        leaf = "04 stm/front-desk/shifts/opening.md"
        (root / leaf).parent.mkdir(parents=True)
        (root / leaf).write_text(leaf_text("front-desk").replace("subtopic: notes", "subtopic: shifts"),
                                 encoding="utf-8")
        (root / "03 mtm/front-desk/shifts.md").write_text("# shifts\n\nWho opens and closes.\n", encoding="utf-8")
        maps.apply(root)
        move.execute(root, "fold-tree", tree="front-desk", into="bakery", today=TODAY)
        self.assertOnlyThreeKinds(root, "fold-tree")
        root = self.make_root("recut")
        pages = {"04 stm/bakery/deck-oven/repairs-2051-01.md": stream_page("2051-01", [
            "- 2051-01-04 08:00 Pacific/Auckland — The door seal was replaced."]),
            "04 stm/bakery/deck-oven/repairs-2051-02.md": stream_page("2051-02", [
                "- 2051-02-11 07:45 Pacific/Auckland — The thermostat was set again."])}
        for path, text in pages.items():
            (root / path).write_text(text, encoding="utf-8")
        maps.apply(root)
        move.execute(root, "recut-stream", subject="deck-oven", leaves=sorted(pages), today=TODAY)
        self.assertOnlyThreeKinds(root, "recut-stream")

    def test_collections_levels_and_sync(self) -> None:
        for action, target in (("admit", "tamsin-orrow"), ("demote", "millstone-flour")):
            with self.subTest(action=action):
                root = self.make_root(action)
                before = record_bytes(root, REFERRER)
                collections.execute(root, action, target, today=TODAY)
                self.assertOnlyThreeKinds(root, action)
                self.assertReferrerRecordKept(root, before, action)
        root = self.make_root("levels")
        timeline = root / COLLECTION
        timeline.write_text(timeline.read_text(encoding="utf-8").replace(
            "## Links", "- 2051-03-04 09:00 Pacific/Auckland — Tamsin Orrow: came back for rye.\n\n## Links"),
            encoding="utf-8")
        levels.apply(root, today=TODAY)
        self.assertOnlyThreeKinds(root, "levels apply")
        before = {path.as_posix(): record_bytes(root, path.relative_to(root).as_posix())
                  for path in root.rglob("*.md") if ".forest" not in path.parts}
        board = root / "03 mtm/bakery/deck-oven.md"
        board.write_text(board.read_text(encoding="utf-8").replace("## Leaves", "## Leaves\n\n- `gone.md` — gone"),
                         encoding="utf-8")
        maps.apply(root)
        after = {path.as_posix(): record_bytes(root, path.relative_to(root).as_posix())
                 for path in root.rglob("*.md") if ".forest" not in path.parts}
        self.assertEqual(after, before, "a derived-list sync writes no record line")

    def test_set_writes_the_change_on_the_leaf_and_nothing_in_the_log(self) -> None:
        root = self.make_root("set")
        result = move.execute(root, "set", path=REPAIRS, kind="plan", today=TODAY)
        self.assertEqual(result["moves"], [])
        lines = record.record_lines(parse_page((root / REPAIRS).read_text(encoding="utf-8")))
        self.assertEqual(lines, [f'- {TODAY} — `kind:`: was "event", now "plan". Why: set by hand.'])
        result = move.execute(root, "set-type", subject="deck-oven", type="component", today=TODAY)
        self.assertEqual(result["moves"], [])


# ─────────────────────────────── every writer, on the synthetic forest ───────────────────────────────

ACQUAINTANCES = "04 stm/people/acquaintances"
KILN = "04 stm/studio/kiln-two"
# An invented caller saved under a made-up number of the reserved 555-01xx range.
NUMBER_ID = "19025550147"
NUMBER_NAME = "+1 902 555 0147"
NUMBER_LEAF = f"{ACQUAINTANCES}/{NUMBER_ID}.md"
PROFILE_LINES = [
    "- The kiln's lid hinge was oiled in the spring and has not squeaked since then.",
    "- Its controller keeps the last twenty firing programs and names each one.",
    "- The vent fan runs for an hour after every firing, whatever the program.",
]


def all_record_lines(root: Path) -> dict[str, Counter[str]]:
    """Every page's record lines: its ``## Update record`` and a collection entry's ``### Update record``."""
    found = {}
    for page in sorted(root.rglob("*.md")):
        relative = page.relative_to(root).as_posix()
        if relative.split("/", 1)[0] not in ("01 xltm", "02 ltm", "03 mtm", "04 stm"):
            continue
        lines: Counter[str] = Counter()
        inside = False
        for line in page.read_text(encoding="utf-8").splitlines():
            if line.startswith("#"):
                inside = line.lstrip("#").strip() == "Update record" and line.startswith(("## ", "### "))
            elif inside and line.startswith("- "):
                lines[line] += 1
        found[relative] = lines
    return found


def add_number_subject(root: Path) -> None:
    index = root / "03 mtm/people/people_MTM.md"
    index.write_text(index.read_text(encoding="utf-8").replace(
        "- `odile-brask`", f"- `{NUMBER_ID}` — {NUMBER_NAME} · person · leaf · `{NUMBER_LEAF}`\n- `odile-brask`", 1),
        encoding="utf-8")
    (root / NUMBER_LEAF).write_text(
        f"---\nentity: {NUMBER_ID}\nsubtopic: calls\nkind: conversation\nabout: none\ntimezone: America/Halifax\n"
        "updated_at: 2042-05-12\n---\n\n# A caller\n\nA caller who asked about mug orders.\n\n## Timeline\n\n"
        "- 2042-05-12 10:00 America/Halifax — asked whether the studio sells mugs by the dozen.\n\n## Links\n\n"
        "- up: [[03 mtm/people/acquaintances|acquaintances MTM]]\n", encoding="utf-8")


def add_repeats(root: Path) -> None:
    for name in ("profile.md", "settings.md"):
        page = root / KILN / name
        page.write_text(page.read_text(encoding="utf-8").replace(
            "## Timeline", "## Notes\n\n" + "\n".join(PROFILE_LINES) + "\n\n## Timeline", 1), encoding="utf-8")


def repeats_apply(root: Path) -> None:
    add_repeats(root)
    found = repeats.check(root)["undecided"]
    decisions = [repeats.Decision(item["subject"], item["digest"], f"{KILN}/profile.md", "the profile")
                 for item in found]
    repeats.execute(root, decisions, today=TODAY)


def record_trim(root: Path) -> None:
    settings = root / "forest.toml"
    settings.write_text(settings.read_text(encoding="utf-8").replace("record_keep_bytes = 2048",
                                                                     "record_keep_bytes = 120"), encoding="utf-8")
    record.trim(root, all_pages=True)


def numbers_star(root: Path) -> None:
    page = root / ACQUAINTANCES / "odile-brask.md"
    page.write_text(page.read_text(encoding="utf-8").replace("## Timeline", f"Her number is {NUMBER_NAME}.\n\n"
                                                             "## Timeline", 1), encoding="utf-8")
    numbers.star(root, all_pages=True)


def board_sync(root: Path) -> None:
    board = root / "03 mtm/studio/kiln-two.md"
    board.write_text(board.read_text(encoding="utf-8").replace("## Leaves\n", "## Leaves\n\n- `gone.md` — gone\n", 1),
                     encoding="utf-8")
    maps.apply(root)


def _move(operation: str, **arguments):
    return lambda root: move.execute(root, operation, today=TODAY, **arguments)


WRITERS = [
    ("move leaf", _move("leaf", source=f"{KILN}/settings.md", target=f"{KILN}/firing-limits.md")),
    ("move rename subject", _move("rename", target="kiln-two", new="big-kiln", name="Big Kiln")),
    ("move rename branch", _move("rename", target="admin/shops", new="stores")),
    ("move promote", _move("promote", subject="odile-brask")),
    ("move merge of a number-named subject", lambda root: (add_number_subject(root), move.execute(
        root, "merge", keep="odile-brask", drop=NUMBER_ID, today=TODAY))),
    ("move archive", _move("archive", path=f"{KILN}/settings.md", reason="the limits moved to the controller")),
    ("move move-branch", _move("move-branch", branch="people/acquaintances", tree="admin")),
    ("move new-tree", _move("new-tree", tree="garden", description="the clay garden behind the studio.")),
    ("move new-agent", _move("new-agent", id="glaze-bot", name="Glaze Bot", role="mixes the glaze batches")),
    ("move retire-tree", _move("retire-tree", tree="ledger-bot")),
    ("move fold-tree", _move("fold-tree", tree="kiln-bot", into="studio")),
    ("move set-type", _move("set-type", subject="kiln-two", type="component")),
    ("move set", _move("set", path=f"{ACQUAINTANCES}/odile-brask.md", kind="profile")),
    ("move recut-stream", _move("recut-stream", subject="kiln-two",
                                leaves=[f"{KILN}/firings-2042-q1.md", f"{KILN}/firings-2042-q2.md"])),
    ("move plan", _move("plan", operations=[{"op": "rename", "target": "admin/self", "new": "owner"},
                                             {"op": "set", "path": f"{KILN}/repairs.md", "kind": "event"}])),
    ("collections demote", lambda root: collections.execute(root, "demote", "cinderwell-supply", today=TODAY)),
    ("collections admit", lambda root: collections.execute(root, "admit", "wren-castellane", today=TODAY)),
    ("levels apply", lambda root: levels.apply(root, today=TODAY)),
    ("record trim", record_trim),
    ("numbers star", numbers_star),
    ("repeats apply", repeats_apply),
    ("check --sync", board_sync),
]


class EveryWriterTests(ForestTestCase):
    """Each writer runs on a fresh copy of the synthetic forest; each record line it adds is one of the three
    kinds by ``record.classify``, and none writes the made-up caller's number whole."""

    def test_every_record_line_a_writer_writes_is_one_of_the_three_kinds(self) -> None:
        for number, (name, write) in enumerate(WRITERS):
            with self.subTest(writer=name):
                root = self.base / f"synthetic-{number}"
                shutil.copytree(SYNTHETIC_FOREST, root)
                before = all_record_lines(root)
                write(root)
                config = load_config(root)
                added = []
                for path, lines in all_record_lines(root).items():
                    added += [(path, line) for line in (lines - before.get(path, Counter())).elements()]
                for path, line in added:
                    self.assertIsNotNone(record.classify(line, config=config), f"{name}: {path}: {line}")
                    self.assertNotIn("".join(filter(str.isdigit, NUMBER_NAME)), "".join(filter(str.isdigit, line)))
                    self.assertNotIn(NUMBER_ID, line)
                if name in ("numbers star", "check --sync"):
                    self.assertEqual(added, [], f"{name} writes no record line")
                if name.startswith("move merge"):
                    self.assertTrue(added, name)
