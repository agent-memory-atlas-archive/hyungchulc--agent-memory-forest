"""Repeated lines: the lint counts fact lines only, and ``forest repeats`` takes the count to zero.

The forest is the invented bakery of ``v1_support`` (Ilse Varrow, her deck oven and its supplier).
"""

from __future__ import annotations

import bounds  # every invocation has a deadline

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v1_support import REPOSITORY_ROOT, ForestTestCase  # noqa: E402

from memory_forest import journal, layout, lint, maps, movelog, repeats  # noqa: E402
from memory_forest.errors import MemoryForestError  # noqa: E402
from memory_forest.model import parse_page  # noqa: E402

TODAY = "2051-03-05"
OVEN = "04 stm/bakery/deck-oven"
HISTORY = f"{OVEN}/history.md"
JANUARY = f"{OVEN}/repairs-2051-01.md"
FEBRUARY = f"{OVEN}/repairs-2051-02.md"
FAN = f"{OVEN}/fan.md"
REPAIRS = f"{OVEN}/repairs.md"
MILL = "04 stm/bakery/suppliers/millstone-flour.md"

REF_FLOUR = f"- 2051-01-02 — a sack of rye flour came damp from the mill: recorded in `{MILL}`."
REF_JANUARY = f"- 2051-01-04 — the door seal was replaced after it cracked: recorded in `{JANUARY}`."
REF_FEBRUARY = f"- 2051-02-11 — the thermostat was set again by the baker: recorded in `{FEBRUARY}`."
REF_FAN = f"- 2051-02-20 — the extractor fan started rattling at dawn: recorded in `{FAN}`."
FACTS = [
    "- The oven holds twelve loaves on its upper deck and ten on the lower one.",
    "- Its burners were last serviced by the same technician who built the flue.",
    "- Ilse keeps a spare door seal in the flour store behind the counter.",
]


def page(entity: str, title: str, sections: dict[str, list[str]], *, kind: str = "event",
         description: str = "What happened to the oven, one line each.") -> str:
    lines = ["---", f"entity: {entity}", "subtopic: repairs", f"kind: {kind}", "about: none",
             "updated_at: 2051-02-20", "---", "", f"# {title}", "", description, ""]
    for heading, body in sections.items():
        lines += [f"## {heading}", "", *body, ""]
    lines += ["## Links", "", "- up: [[03 mtm/bakery/deck-oven|Deck Oven]]", ""]
    return "\n".join(lines)


def stream(month: str, timeline: list[str], references: list[str]) -> str:
    return page("deck-oven", f"Deck Oven: repairs ({month})",
                {"Recorded elsewhere": references, "Timeline": timeline})


class RepeatsTestCase(ForestTestCase):
    def forest(self, *, facts: bool = True) -> Path:
        root = self.make_forest()
        files = {
            HISTORY: page("deck-oven", "Deck Oven: its history", {
                "Recorded elsewhere": [REF_FLOUR, REF_JANUARY, REF_FEBRUARY, REF_FAN],
                "What it is": FACTS if facts else ["- A two-deck oven."],
            }, kind="profile", description="What the oven is and where its events are recorded."),
            JANUARY: stream("2051-01", ["- 2051-01-04 08:00 Pacific/Auckland — The door seal was replaced."],
                            [REF_FLOUR, REF_FEBRUARY, REF_FAN]),
            FEBRUARY: stream("2051-02", ["- 2051-02-11 07:45 Pacific/Auckland — The thermostat was set again."],
                             [REF_FLOUR, REF_JANUARY, REF_FAN,
                              f"- 2051-02-01 — the burner check found nothing wrong: recorded in `{JANUARY}`."]),
            FAN: page("deck-oven", "Deck Oven: the extractor fan", {"Recorded elsewhere": [REF_FLOUR, REF_JANUARY]},
                      description="Where the fan's rattle is recorded."),
            REPAIRS: page("deck-oven", "Deck Oven: notes", {"Notes": FACTS + ["- The left door sticks in the cold."]}),
        }
        for relative, text in files.items():
            (root / relative).write_text(text, encoding="utf-8")
        maps.apply(root)
        return root

    def hits(self, root: Path) -> list[str]:
        report = lint.lint(root, codes=["leaf_lines_repeated"])
        return sorted({hit.path for hit in report.hits})

    def text(self, root: Path, path: str) -> str:
        return (root / path).read_text(encoding="utf-8")

    def decisions(self, root: Path, home: str) -> Path:
        plan = repeats.check(root)
        items = [{"subject": item["subject"], "digest": item["digest"], "home": home,
                  "reason": "Ilse wrote it on both pages on purpose" if home == repeats.BOTH else "its profile"}
                 for item in plan["undecided"]]
        path = self.base / "decisions.json"
        path.write_text(json.dumps(items), encoding="utf-8")
        return path


class FactLineTests(RepeatsTestCase):
    def test_fenced_lines_and_bare_references_are_no_fact_lines(self) -> None:
        self.assertTrue(repeats.is_bare_reference(f"- `{MILL}`, `{JANUARY}`"))
        self.assertTrue(repeats.is_bare_reference("- [[03 mtm/bakery/deck-oven|Deck Oven]] https://example.invalid/x"))
        self.assertFalse(repeats.is_bare_reference(f"- `{MILL}` — what the mill delivered in January"))
        self.assertFalse(repeats.is_bare_reference(REF_FLOUR))
        root = self.make_forest()
        shared = ["```text", *FACTS, "```", f"- `{MILL}` · `{JANUARY}` · `{FEBRUARY}`",
                  f"- [[03 mtm/bakery/deck-oven|Deck Oven]] <https://example.invalid/oven-manual-page>"]
        for path in (REPAIRS, HISTORY):
            (root / path).write_text(page("deck-oven", "Deck Oven", {"Kept as written": shared}), encoding="utf-8")
        maps.apply(root)
        self.assertEqual(self.hits(root), [])
        (root / HISTORY).write_text(page("deck-oven", "Deck Oven", {"Notes": FACTS}), encoding="utf-8")
        (root / REPAIRS).write_text(page("deck-oven", "Deck Oven", {"Notes": FACTS}), encoding="utf-8")
        self.assertEqual(self.hits(root), [HISTORY, REPAIRS])

    def test_the_index_leaf_names_the_most_distinct_pages(self) -> None:
        root = self.forest()
        pages = {path: parse_page(self.text(root, path)) for path in (HISTORY, JANUARY, FEBRUARY, FAN)}
        self.assertEqual(repeats.index_leaf(pages, layout.folder_stems(pages)), HISTORY)
        # A tie goes to the page without a period suffix.
        pages[HISTORY] = parse_page(page("deck-oven", "x", {"Recorded elsewhere": [REF_FLOUR, REF_JANUARY, REF_FAN]}))
        self.assertEqual(repeats.index_leaf(pages, layout.folder_stems([*pages, JANUARY, FEBRUARY])), HISTORY)


class ApplyTests(RepeatsTestCase):
    def test_plan_writes_nothing_and_lists_the_lines_that_wait_for_a_reading(self) -> None:
        root = self.forest()
        self.assertEqual(self.hits(root), sorted([FEBRUARY, HISTORY, JANUARY, REPAIRS]))
        before = {path: self.text(root, path) for path in (HISTORY, JANUARY, FEBRUARY, FAN, REPAIRS)}
        result = repeats.execute(root, dry_run=True, today=TODAY)
        self.assertFalse(result["written"])
        self.assertEqual({path: self.text(root, path) for path in before}, before)
        outcome = result["result"]
        self.assertEqual(outcome["index"], {"deck-oven": HISTORY})
        self.assertEqual(outcome["folded"], {FAN: HISTORY})
        self.assertEqual(len(outcome["undecided"]), len(FACTS))
        self.assertFalse(repeats.ledger_path(root).exists())

    def test_apply_takes_each_line_to_its_home_and_the_count_to_zero(self) -> None:
        root = self.forest()
        decisions = repeats.read_decisions(self.decisions(root, HISTORY))
        january_before = self.text(root, JANUARY)
        result = repeats.execute(root, decisions, today=TODAY)
        self.assertTrue(result["written"])
        self.assertEqual(result["result"]["hits_after"], 0)
        self.assertEqual(self.hits(root), [])
        self.assertTrue(repeats.check(root)["ok"])
        # The period pages keep their own lines only: no reference line to a sibling period, no copied line.
        january = self.text(root, JANUARY)
        self.assertNotIn("recorded in", january.split("## Update record")[0])
        self.assertNotIn("## Recorded elsewhere", january)
        self.assertIn("- 2051-01-04 08:00 Pacific/Auckland — The door seal was replaced.", january)
        kept = january.split("## Update record")[0].rstrip("\n") + "\n"
        self.assertEqual(repeats.removal_proof(january_before, kept, [REF_FLOUR, REF_FEBRUARY, REF_FAN]), [])
        # The sibling reference only February held moved into the index leaf, once.
        history = self.text(root, HISTORY)
        self.assertEqual(history.count("the burner check found nothing wrong"), 1)
        for line in FACTS:
            self.assertEqual(history.count(line), 1)
            self.assertNotIn(line, self.text(root, REPAIRS))
        self.assertIn("- The left door sticks in the cold.", self.text(root, REPAIRS))
        # The shell went to the archive, folded into the index leaf; its old path resolves there.
        self.assertFalse((root / FAN).exists())
        found = movelog.resolve(root, FAN)
        self.assertEqual((found.status, found.successor), ("archived", HISTORY))
        self.assertNotIn("`fan.md`", self.text(root, "03 mtm/bakery/deck-oven.md").split("## Leaves")[1])
        # Each changed leaf names where its lines are kept, a line of where its lines went.
        record = january.split("## Update record", 1)[1]
        self.assertIn(f"Repeated lines taken out, each kept in `{HISTORY}`", record)
        self.assertEqual(lint.lint(root, codes=["record_run_note"]).hits, [])

    def test_a_line_judged_rightly_in_two_leaves_is_logged_once_and_leaves_the_count(self) -> None:
        root = self.forest()
        decisions = repeats.read_decisions(self.decisions(root, repeats.BOTH))
        repeats.execute(root, decisions, today=TODAY)
        rows = [json.loads(line) for line in repeats.ledger_path(root).read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), len(FACTS))
        self.assertEqual({row["subject"] for row in rows}, {"deck-oven"})
        self.assertEqual(self.hits(root), [])
        for line in FACTS:
            self.assertIn(line, self.text(root, REPAIRS))
        repeats.execute(root, decisions, today=TODAY)  # judged once: a second run adds no row
        self.assertEqual(len(repeats.ledger_path(root).read_text(encoding="utf-8").splitlines()), len(FACTS))

    def test_a_shell_that_is_a_subjects_home_stays(self) -> None:
        root = self.forest(facts=False)
        index = root / "03 mtm/bakery/bakery_MTM.md"
        index.write_text(index.read_text(encoding="utf-8").replace(
            "- `millstone-flour`",
            f"- `oven-fan` — Oven Fan · device · leaf · `{FAN}`\n- `millstone-flour`"), encoding="utf-8")
        (root / FAN).write_text(self.text(root, FAN).replace("entity: deck-oven", "entity: oven-fan"),
                                encoding="utf-8")
        result = repeats.execute(root, dry_run=True, today=TODAY)["result"]
        self.assertNotIn(FAN, result["folded"])

    def test_a_decision_whose_home_does_not_hold_the_line_is_refused(self) -> None:
        root = self.forest()
        decisions = repeats.read_decisions(self.decisions(root, JANUARY))
        with self.assertRaises(MemoryForestError) as caught:
            repeats.execute(root, decisions, today=TODAY)
        self.assertEqual(caught.exception.code, "decision_home_invalid")


class LedgerJournalTests(RepeatsTestCase):
    def test_a_rolled_back_commit_cuts_the_ledger_back(self) -> None:
        root = self.forest()
        ledger = repeats.ledger_path(root)
        ledger.write_text(repeats.ledger_row("deck-oven", "0" * 16, reason="kept", day=TODAY, paths=[]) + "\n",
                          encoding="utf-8")
        length = ledger.stat().st_size
        journal.write(root, label="repeats", before={}, made_dirs=[], removed_dirs=[],
                      appends={layout.REPEATS_JUDGED_FILE: length})
        with open(ledger, "a", encoding="utf-8") as handle:
            handle.write('{"subject": "deck-oven", "digest": "1111')  # a kill in the middle of the append
        journal.roll_back(root)
        self.assertEqual(ledger.stat().st_size, length)
        self.assertEqual(repeats.judged(root), {("deck-oven", "0" * 16)})

    def test_the_ledger_is_kept_state(self) -> None:
        self.assertIn(layout.REPEATS_JUDGED_FILE, layout.KEPT_STATE)


class CommandLineTests(RepeatsTestCase):
    def forest_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ, PYTHONPATH=str(REPOSITORY_ROOT / "src"))
        return subprocess.run(
            [sys.executable, "-c", "from memory_forest.cli import forest_main; raise SystemExit(forest_main())",
             *arguments], capture_output=True, text=True, env=environment, cwd=REPOSITORY_ROOT, check=False)

    def test_plan_apply_and_check_from_the_command_line(self) -> None:
        root = self.forest()
        checked = self.forest_cli("repeats", "check", "--root", str(root))
        self.assertEqual(checked.returncode, 1, checked.stderr)
        self.assertIn("lines wait for a reading", checked.stdout)
        planned = self.forest_cli("repeats", "plan", "--root", str(root))
        self.assertEqual(planned.returncode, 0, planned.stderr)
        self.assertIn("would be archived", planned.stdout)
        self.assertTrue((root / FAN).exists())
        decisions = self.decisions(root, HISTORY)
        applied = self.forest_cli("repeats", "apply", "--root", str(root), "--decisions", str(decisions))
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertIn("hits after: 0", applied.stdout)
        self.assertEqual(self.forest_cli("repeats", "check", "--root", str(root)).returncode, 0)
