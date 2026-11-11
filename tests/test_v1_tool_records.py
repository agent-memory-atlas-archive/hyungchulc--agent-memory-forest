"""Every record line a tool writes is one of the three kinds, on the page it is about (contract, section 4).

Each operation runs on a fresh made-up forest. Afterwards ``forest record
kinds`` counts no run's note, ``forest lint`` reports no ``record_run_note``,
and a page that only referred to what moved keeps its ``## Update record``
byte for byte (the move log is the record of that rewrite).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v1_collections import COLLECTION, CollectionTestCase  # noqa: E402
from test_v1_move import MILLSTONE, REPAIRS, TODAY, stream_page  # noqa: E402
from v1_support import leaf_text  # noqa: E402

from memory_forest import collections, levels, lint, maps, move, record  # noqa: E402
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
