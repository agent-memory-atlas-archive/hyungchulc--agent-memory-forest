"""``forest levels check | apply`` on made-up forests."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v1_move import MILLSTONE, TODAY, difference, snapshot  # noqa: E402
from v1_support import ForestTestCase  # noqa: E402

from memory_forest import levels, maps, movelog, registry  # noqa: E402

COLLECTION = "04 stm/people/regulars/seen-once-2051-q1.md"


def timeline(days: list[str], text: str = "A delivery came.") -> str:
    return "## Timeline\n\n" + "".join(f"- {day} 06:00 Pacific/Auckland — {text}\n" for day in days) + "\n"


class LevelsTests(ForestTestCase):
    def forest(self) -> Path:
        root = self.make_forest()
        (root / "03 mtm/people/regulars.md").write_text("# regulars\n\nPeople who come in.\n", encoding="utf-8")
        maps.apply(root)
        return root

    def add_timeline(self, root: Path, path: str, days: list[str], text: str = "A delivery came.") -> None:
        page = root / path
        page.write_text(page.read_text(encoding="utf-8").rstrip("\n") + "\n\n" + timeline(days, text),
                        encoding="utf-8")

    def test_nothing_is_due_on_a_quiet_forest_and_check_writes_nothing(self) -> None:
        root = self.forest()
        before = snapshot(root)
        self.assertEqual(levels.check(root), [])
        self.assertEqual(snapshot(root), before)

    def test_a_second_day_makes_an_entry_due_and_apply_admits_it(self) -> None:
        root = self.forest()
        page = root / COLLECTION
        page.write_text(page.read_text(encoding="utf-8").replace(
            "## Links", "- 2051-03-04 11:00 Pacific/Auckland — Tamsin Orrow: came back for rye.\n\n## Links"),
            encoding="utf-8")
        found = levels.check(root)
        self.assertEqual([(item.subject, item.action, item.new_level) for item in found],
                         [("tamsin-orrow", "admit", "leaf")])
        self.assertEqual(found[0].days, ["2051-03-02", "2051-03-04"])
        before = snapshot(root)
        result = levels.apply(root, today=TODAY)
        self.assertEqual([item["subject"] for item in result["changes"]], ["tamsin-orrow"])
        self.assertEqual(result["refused"], [])
        changed = difference(before, snapshot(root))
        self.assertEqual(changed["created"], ["04 stm/people/regulars/tamsin-orrow.md"])
        self.assertEqual(levels.check(root), [])
        self.assertEqual(registry.check(root), [])
        found = movelog.resolve(root, f"{COLLECTION}#tamsin-orrow")
        self.assertEqual(found.path, "04 stm/people/regulars/tamsin-orrow.md")

    def test_five_days_make_a_leaf_subject_a_branch(self) -> None:
        root = self.forest()
        self.add_timeline(root, MILLSTONE, ["2051-01-02", "2051-01-09", "2051-01-16", "2051-01-23", "2051-01-30"])
        found = levels.check(root)
        self.assertEqual([(item.subject, item.level, item.new_level) for item in found],
                         [("millstone-flour", "leaf", "branch")])
        result = levels.apply(root, today=TODAY)
        self.assertEqual(len(result["changes"]), 1)
        self.assertIn("- `millstone-flour` — Millstone Flour · organization · branch · "
                      "`03 mtm/bakery/millstone-flour.md`",
                      (root / "03 mtm/bakery/bakery_MTM.md").read_text(encoding="utf-8"))
        self.assertEqual(levels.check(root), [])
        why = [entry for entry in movelog.read(root) if entry.operation == "promote"]
        self.assertTrue(why and all(entry.reason.startswith("level leaf -> branch:") for entry in why))

    def test_work_lines_reference_lines_and_superseded_lines_count_no_day(self) -> None:
        root = self.forest()
        page = root / MILLSTONE
        page.write_text(page.read_text(encoding="utf-8").rstrip("\n") + "\n\n## Timeline\n\n"
                        "- 2051-01-02 06:00 Pacific/Auckland — A delivery came.\n"
                        "- 2051-01-03 06:00 Pacific/Auckland — Front Desk noted the invoice.\n"
                        "- 2051-01-04 06:00 Pacific/Auckland — The filing run recorded a copy.\n"
                        "- 2051-01-05 06:00 Pacific/Auckland — Calendar alerted the delivery.\n"
                        "- 2051-01-06 06:00 Pacific/Auckland — A delivery came.\n", encoding="utf-8")
        self.assertEqual(levels.check(root), [])
        plan = levels.Plan.open(root)
        counted = levels.DayCounter(plan).leaf_days(page.read_text(encoding="utf-8"))
        self.assertEqual(counted, {"2051-01-02", "2051-01-06"})

    def test_a_dry_run_reports_the_changes_and_writes_nothing(self) -> None:
        root = self.forest()
        self.add_timeline(root, MILLSTONE, ["2051-01-02", "2051-01-09", "2051-01-16", "2051-01-23", "2051-01-30"])
        before = snapshot(root)
        result = levels.apply(root, dry_run=True, today=TODAY)
        self.assertEqual([item["subject"] for item in result["changes"]], ["millstone-flour"])
        self.assertFalse(result["written"])
        self.assertEqual(snapshot(root), before)

    def test_subjects_limits_the_check(self) -> None:
        root = self.forest()
        self.add_timeline(root, MILLSTONE, ["2051-01-02", "2051-01-09", "2051-01-16", "2051-01-23", "2051-01-30"])
        self.assertEqual(levels.check(root, ["deck-oven"]), [])
        self.assertEqual(len(levels.check(root, ["millstone-flour"])), 1)
