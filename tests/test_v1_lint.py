from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v1_checks_support import (  # noqa: E402
    AGENT_LEAF,
    COLLECTION,
    INDEX,
    OWNER_LEAF,
    PEOPLE_BOARD,
    STAND,
    STAND_BOARD,
    TREE_PAGE,
    CheckForestTestCase,
    leaf,
)
from v1_support import REPOSITORY_ROOT, SYNTHETIC_FOREST  # noqa: E402

from memory_forest import lint, movelog  # noqa: E402
from memory_forest.errors import MemoryForestError  # noqa: E402

STAND_UP = "03 mtm/workshop/truing-stand"


def forest(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    environment = dict(os.environ, PYTHONPATH=str(REPOSITORY_ROOT / "src"))
    return subprocess.run(
        [sys.executable, "-c", "from memory_forest.cli import forest_main; raise SystemExit(forest_main())", *arguments],
        capture_output=True,
        env=environment,
        cwd=REPOSITORY_ROOT,
        check=False,
    )


class LintCodeTests(CheckForestTestCase):
    """For each code: the clean forest holds no hit, and one changed page hits on that page only."""

    def hit_paths(self, root: Path, code: str) -> list[str]:
        report = lint.lint(root, codes=[code])
        self.assertEqual(report.not_checked, [])
        return sorted({hit.path for hit in report.hits if hit.code == code})

    def assert_hits(self, code: str, changes: dict[str, str | None], expected: list[str]) -> None:
        root = self.workshop(changes)
        self.assertEqual(self.hit_paths(root, code), expected)

    def test_the_clean_forest_hits_no_implemented_code(self) -> None:
        report = lint.lint(self.workshop())
        self.assertEqual([hit.render() for hit in report.hits], [])
        self.assertEqual(sorted(report.not_checked), sorted(lint.SUPPLIED_LATER))
        self.assertEqual(report.exit_code, lint.EXIT_CLEAN)

    def test_every_contract_code_is_registered_once(self) -> None:
        contract = (REPOSITORY_ROOT / "docs" / "contract.md").read_text(encoding="utf-8")
        table = contract.split("## 8. The lint", 1)[1].split("## 9. Tools", 1)[0]
        written = set()
        for line in table.splitlines():
            if line.startswith("| `"):
                cell = line.split("|")[1]
                written.update(part.strip().strip("`") for part in cell.split(","))
        self.assertEqual(written, set(lint.CODES))

    def test_leaf_path_shape(self) -> None:
        self.assert_hits("leaf_path_shape", {"04 stm/workshop/truing-stand/Old_Notes.md": leaf("truing-stand", STAND_UP)},
                         ["04 stm/workshop/truing-stand/Old_Notes.md"])

    def test_leaf_entity_missing(self) -> None:
        self.assert_hits("leaf_entity_missing", {STAND: leaf("", STAND_UP)}, [STAND])

    def test_leaf_entity_unknown(self) -> None:
        self.assert_hits("leaf_entity_unknown", {STAND: leaf("nobody-known", STAND_UP)}, [STAND])

    def test_leaf_misplaced(self) -> None:
        self.assert_hits("leaf_misplaced", {"04 stm/workshop/people/wheels.md": leaf("truing-stand", "03 mtm/workshop/people")},
                         ["04 stm/workshop/people/wheels.md"])

    def test_leaf_too_large(self) -> None:
        self.assert_hits("leaf_too_large", {STAND: leaf("truing-stand", STAND_UP, body="x" * 21000)}, [STAND])

    def test_leaf_dated_name(self) -> None:
        self.assert_hits("leaf_dated_name", {"04 stm/workshop/truing-stand/repairs-march.md": leaf("truing-stand", STAND_UP)},
                         ["04 stm/workshop/truing-stand/repairs-march.md"])

    def test_leaf_dated_name_allows_a_period_suffix(self) -> None:
        self.assert_hits("leaf_dated_name", {"04 stm/workshop/truing-stand/repairs-2051-q1.md": leaf("truing-stand", STAND_UP)},
                         [])

    def test_a_day_suffix_is_a_period_only_beside_its_streams_other_period_pages(self) -> None:
        month, day = "04 stm/workshop/truing-stand/repairs-2051-03.md", "04 stm/workshop/truing-stand/repairs-2051-03-14.md"
        page = leaf("truing-stand", STAND_UP)
        self.assert_hits("leaf_dated_name", {month: page, day: page, day.replace(".md", "-2.md"): page}, [])
        self.assert_hits("leaf_dated_name", {day: page}, [day])

    def test_board_state_empty(self) -> None:
        empty = "## Current state\n\n- Tobin keeps the workshop (as of 2051-03-02; source `04 stm/workshop/people/tobin-reel.md`)\n\n"
        root = self.workshop()
        self.edit(root, PEOPLE_BOARD, empty, "## Current state\n\n")
        self.assertEqual(self.hit_paths(root, "board_state_empty"), [PEOPLE_BOARD])
        # A folder that holds only collection pages asks for no current state.
        (root / OWNER_LEAF).unlink()
        self.assertEqual(self.hit_paths(root, "board_state_empty"), [])

    def test_leaf_recorder_dump(self) -> None:
        body = "- seen 10:01:02, 10:01:09, 10:01:15, 10:01:21 on the stand camera"
        self.assert_hits("leaf_recorder_dump", {STAND: leaf("truing-stand", STAND_UP, body=body)}, [STAND])

    def test_leaf_kind_invalid(self) -> None:
        self.assert_hits("leaf_kind_invalid", {STAND: leaf("truing-stand", STAND_UP, kind="diary")}, [STAND])

    def test_about_unknown(self) -> None:
        self.assert_hits("about_unknown", {STAND: leaf("truing-stand", STAND_UP, about="tobin-reel, ghost-id")}, [STAND])

    def test_about_naming_an_entry_or_a_subject_is_fine(self) -> None:
        self.assert_hits("about_unknown", {STAND: leaf("truing-stand", STAND_UP, about="marit-olm")}, [])

    def test_collection_entry_malformed(self) -> None:
        root = self.workshop()
        self.edit(root, COLLECTION, "- entry: `marit-olm` · person · aliases: Marit\n", "")
        self.assertEqual(self.hit_paths(root, "collection_entry_malformed"), [COLLECTION])

    def test_collection_timeline_line_names_its_entry(self) -> None:
        root = self.workshop()
        self.edit(root, COLLECTION, "— Marit Olm: brought", "— someone brought")
        self.assertEqual(self.hit_paths(root, "collection_entry_malformed"), [COLLECTION])

    def test_machine_page(self) -> None:
        body = "```json\n{\"run\": 1}\n```"
        self.assert_hits("machine_page", {STAND: leaf("truing-stand", STAND_UP, body=body)}, [STAND])

    def test_registry_codes(self) -> None:
        root = self.workshop()
        self.edit(root, INDEX, "## Branches", "- `truing-stand` — Copy · device · branch · `03 mtm/workshop/truing-stand.md`\n"
                  "- `stand-two` — Stand Two · device · leaf · `04 stm/workshop/people/stand-two.md` · aliases: the stand\n"
                  "- broken line\n"
                  "- `marit-twin` — Marit · person · leaf · `04 stm/workshop/people/marit-twin.md`\n\n## Branches")
        self.assertEqual(self.hit_paths(root, "registry_duplicate_id"), [INDEX])
        self.assertEqual(self.hit_paths(root, "registry_duplicate_alias"), [INDEX])
        self.assertEqual(self.hit_paths(root, "registry_line_invalid"), [INDEX])
        self.assertEqual(self.hit_paths(root, "registry_home_missing"), [INDEX])
        self.assertEqual(self.hit_paths(root, "registry_entry_is_subject"), [COLLECTION])

    def test_current_state_untagged(self) -> None:
        root = self.workshop()
        self.edit(root, STAND_BOARD, "## Leaves", "- Wobbles a little\n\n## Leaves")
        self.assertEqual(self.hit_paths(root, "current_state_untagged"), [STAND_BOARD])

    def test_current_state_stale(self) -> None:
        root = self.workshop()
        self.edit(root, STAND, "updated_at: 2051-03-02", "updated_at: 2051-04-01")
        self.assertEqual(self.hit_paths(root, "current_state_stale"), [TREE_PAGE, STAND_BOARD])

    def test_current_state_source_gone_is_stale(self) -> None:
        root = self.workshop()
        self.edit(root, STAND_BOARD, "truing-stand/repairs.md`)", "truing-stand/clamps.md`)")
        self.assertEqual(self.hit_paths(root, "current_state_stale"), [STAND_BOARD])

    def test_reference_stale(self) -> None:
        body = "See `04 stm/workshop/truing-stand/gone.md` and `04 stm/workshop/old.md` (archived)."
        self.assert_hits("reference_stale", {STAND: leaf("truing-stand", STAND_UP, body=body)}, [STAND])

    def test_reference_in_a_record_is_history(self) -> None:
        record = "- 2051-03-02 — clamp: was \"loose\", now \"tight\". Source: `04 stm/workshop/gone.md`."
        self.assert_hits("reference_stale", {STAND: leaf("truing-stand", STAND_UP, record=record)}, [])

    def test_account_number_in_full(self) -> None:
        body = "Paid with the card 4111 1111 1111 1111; called +1 555 0100 2233."
        self.assert_hits("account_number_in_full", {STAND: leaf("truing-stand", STAND_UP, body=body)}, [STAND])

    def test_account_number_starred_is_fine(self) -> None:
        body = "Paid with the card 411* **** **** 1111; called +1 5** **** 2233."
        self.assert_hits("account_number_in_full", {STAND: leaf("truing-stand", STAND_UP, body=body)}, [])

    def test_leaf_over_cap(self) -> None:
        self.assert_hits("leaf_over_cap", {STAND: leaf("truing-stand", STAND_UP, body="y" * 9000)}, [STAND])

    def test_a_long_record_does_not_put_a_leaf_over_the_cap(self) -> None:
        record = "\n".join(f"- 2051-03-{day:02d} — clamp: was \"{'a' * 300}\", now \"b\"." for day in range(1, 29))
        self.assert_hits("leaf_over_cap", {STAND: leaf("truing-stand", STAND_UP, record=record)}, [])

    def test_record_over_budget(self) -> None:
        record = "\n".join(f"- 2051-03-{day:02d} — clamp: was \"{'a' * 100}\", now \"b\"." for day in range(1, 29))
        self.assert_hits("record_over_budget", {STAND: leaf("truing-stand", STAND_UP, record=record)}, [STAND])

    def test_record_run_note(self) -> None:
        record = "- 2051-03-02 — rechecked the page; 3 lines unchanged."
        self.assert_hits("record_run_note", {STAND: leaf("truing-stand", STAND_UP, record=record)}, [STAND])

    def test_leaf_kind_missing(self) -> None:
        text = leaf("truing-stand", STAND_UP).replace("kind: event\n", "")
        self.assert_hits("leaf_kind_missing", {STAND: text}, [STAND])

    def test_leaf_about_missing(self) -> None:
        self.assert_hits("leaf_about_missing", {STAND: leaf("truing-stand", STAND_UP, about=None)}, [STAND])

    def test_leaf_updated_missing(self) -> None:
        text = leaf("truing-stand", STAND_UP).replace("updated_at: 2051-03-02\n", "")
        root = self.workshop({STAND: text})
        self.assertEqual(self.hit_paths(root, "leaf_updated_missing"), [STAND])

    def test_leaf_lines_repeated(self) -> None:
        body = "\n".join(f"- The clamp number {n} was replaced with a stronger steel one." for n in range(3))
        self.assert_hits("leaf_lines_repeated", {
            STAND: leaf("truing-stand", STAND_UP, body=body),
            "04 stm/workshop/truing-stand/clamps.md": leaf("truing-stand", STAND_UP, subtopic="clamps", body=body),
        }, ["04 stm/workshop/truing-stand/clamps.md", STAND])

    def test_two_shared_lines_are_not_a_copy(self) -> None:
        body = "\n".join(f"- The clamp number {n} was replaced with a stronger steel one." for n in range(2))
        self.assert_hits("leaf_lines_repeated", {
            STAND: leaf("truing-stand", STAND_UP, body=body),
            "04 stm/workshop/truing-stand/clamps.md": leaf(
                "truing-stand", STAND_UP, subtopic="clamps", body=body, timeline="- 2051-03-03 — new clamps."),
        }, [])

    def test_board_entity_missing(self) -> None:
        root = self.workshop()
        self.edit(root, STAND_BOARD, "---\nentity: truing-stand\n---\n\n", "")
        self.assertEqual(self.hit_paths(root, "board_entity_missing"), [STAND_BOARD])

    def test_current_state_date_ahead(self) -> None:
        root = self.workshop()
        self.edit(root, STAND_BOARD, "(as of 2051-03-02;", "(as of 2051-03-09;")
        self.assertEqual(self.hit_paths(root, "current_state_date_ahead"), [STAND_BOARD])

    def test_leaf_channel_subtopic(self) -> None:
        self.assert_hits("leaf_channel_subtopic",
                         {STAND: leaf("truing-stand", STAND_UP, subtopic="chat")}, [STAND])

    def test_a_channel_subject_may_have_channel_leaves(self) -> None:
        root = self.workshop()
        self.edit(root, INDEX, "## Branches", "- `shop-chat` — Shop chat · app · branch · `03 mtm/workshop/shop-chat.md`\n\n## Branches")
        (root / "03 mtm/workshop/shop-chat.md").write_text("---\nentity: shop-chat\n---\n\n# x\n", encoding="utf-8")
        (root / "04 stm/workshop/shop-chat").mkdir()
        (root / "04 stm/workshop/shop-chat/chat.md").write_text(
            leaf("shop-chat", "03 mtm/workshop/shop-chat", subtopic="chat"), encoding="utf-8")
        self.assertEqual(self.hit_paths(root, "leaf_channel_subtopic"), [])

    def test_timeline_zone_missing(self) -> None:
        text = leaf("truing-stand", STAND_UP, timeline="- 2051-03-02 10:00 — trued the stand.").replace(
            "timezone: Pacific/Auckland\n", "")
        self.assert_hits("timeline_zone_missing", {STAND: text}, [STAND])

    def test_leaf_writer_remark(self) -> None:
        body = "The clamp was replaced; this could not be verified."
        self.assert_hits("leaf_writer_remark", {STAND: leaf("truing-stand", STAND_UP, body=body)}, [STAND])

    def test_a_quoted_remark_is_the_source_s_words(self) -> None:
        body = 'Marit said: "it could not be verified" about the old rim.'
        self.assert_hits("leaf_writer_remark", {STAND: leaf("truing-stand", STAND_UP, body=body)}, [])

    def test_supplied_later_codes_are_reported_as_not_checked(self) -> None:
        report = lint.lint(self.workshop(), codes=["daily_section_unfiled", "leaf_over_cap"])
        self.assertEqual(report.not_checked, ["daily_section_unfiled"])
        self.assertEqual(report.checked, ["leaf_over_cap"])
        self.assertEqual(lint.SUPPLIED_LATER, ("daily_section_unfiled",))
        with self.assertRaises(NotImplementedError):
            lint.CODES["daily_section_unfiled"].check(None)  # type: ignore[arg-type]

    def test_an_unknown_code_is_an_error(self) -> None:
        with self.assertRaises(MemoryForestError):
            lint.lint(self.workshop(), codes=["no_such_code"])


class BaselineTests(CheckForestTestCase):
    def test_against_a_baseline_only_what_the_run_added_is_the_run_s(self) -> None:
        root = self.workshop({STAND: leaf("truing-stand", STAND_UP, about=None, kind="diary")})
        baseline = lint.lint(root).baseline()
        self.assertEqual(lint.lint(root, against=baseline).must_fix, [])
        (root / AGENT_LEAF).write_text(leaf("desk-bot", "03 mtm/desk-bot/runs", subtopic="runs", about=None),
                                       encoding="utf-8")
        (root / OWNER_LEAF).write_text(
            (root / OWNER_LEAF).read_text(encoding="utf-8").replace("kind: profile", "kind: diary"), encoding="utf-8")
        report = lint.lint(root, against=baseline)
        self.assertEqual(sorted((hit.code, hit.path) for hit in report.must_fix),
                         [("leaf_about_missing", AGENT_LEAF), ("leaf_kind_invalid", OWNER_LEAF)])
        self.assertIn(("leaf_kind_invalid", STAND), [(hit.code, hit.path) for hit in report.upkeep_run])
        self.assertIn(("leaf_about_missing", STAND), [(hit.code, hit.path) for hit in report.upkeep_run])
        self.assertEqual(report.exit_code, lint.EXIT_RUN_MUST_FIX)

    def test_upkeep_a_run_may_add_is_the_upkeep_run_s(self) -> None:
        root = self.workshop()
        baseline = lint.lint(root).baseline()
        self.edit(root, STAND_BOARD, "(as of 2051-03-02;", "(as of 2051-03-09;")
        report = lint.lint(root, against=baseline)
        self.assertEqual(report.must_fix, [])
        self.assertEqual([hit.code for hit in report.upkeep_run], ["current_state_date_ahead"])
        self.assertTrue(report.upkeep_run[0].new)

    def test_a_leaf_over_the_cap_is_new_only_when_it_grew(self) -> None:
        root = self.workshop({STAND: leaf("truing-stand", STAND_UP, body="y" * 9000)})
        baseline = lint.lint(root).baseline()
        self.assertEqual(lint.lint(root, against=baseline).must_fix, [])
        self.edit(root, STAND, "y" * 9000, "y" * 9100)
        self.assertEqual([hit.code for hit in lint.lint(root, against=baseline).must_fix], ["leaf_over_cap"])

    def test_a_moved_page_keeps_its_baseline_hits(self) -> None:
        root = self.workshop({STAND: leaf("truing-stand", STAND_UP, about=None)})
        baseline = lint.lint(root).baseline()
        moved = "04 stm/workshop/truing-stand/stand-repairs.md"
        (root / STAND).rename(root / moved)
        movelog.append(root, [movelog.record("move", STAND, moved, time="2999-01-01T00:00:00Z")])
        report = lint.lint(root, against=baseline)
        self.assertNotIn(("leaf_about_missing", moved), [(hit.code, hit.path) for hit in report.must_fix])

    def test_a_baseline_file_round_trips(self) -> None:
        root = self.workshop({STAND: leaf("truing-stand", STAND_UP, about=None)})
        target = self.base / "baseline.json"
        lint.write_baseline(target, lint.lint(root).baseline())
        loaded = lint.load_baseline(target)
        self.assertEqual(loaded.counts, {("leaf_about_missing", STAND): 1})
        self.assertEqual(lint.lint(root, against=loaded).must_fix, [])
        target.write_text("{}", encoding="utf-8")
        with self.assertRaises(MemoryForestError):
            lint.load_baseline(target)


class LintCommandTests(CheckForestTestCase):
    def test_the_synthetic_forest_passes(self) -> None:
        result = forest("lint", "--root", str(SYNTHETIC_FOREST))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(b"0 violations, 0 upkeep", result.stdout)
        self.assertIn(b"not checked: daily_section_unfiled\n", result.stdout)

    def test_baseline_then_against_and_exit_codes(self) -> None:
        root = self.workshop({STAND: leaf("truing-stand", STAND_UP, kind="diary")})
        baseline = self.base / "start.json"
        first = forest("lint", "--root", str(root), "--baseline", str(baseline))
        self.assertEqual(first.returncode, 1, first.stdout)
        self.assertTrue(baseline.is_file())
        again = forest("lint", "--root", str(root), "--against", str(baseline), "--json")
        self.assertEqual(again.returncode, 0, again.stdout)
        report = json.loads(again.stdout)
        self.assertEqual(report["must_fix"], [])
        self.assertEqual([item["code"] for item in report["upkeep_run"]], ["leaf_kind_invalid"])
        (root / PEOPLE_BOARD).write_text((root / PEOPLE_BOARD).read_text(encoding="utf-8").replace(
            "## Leaves", "- untagged state line\n\n## Leaves"), encoding="utf-8")
        worse = forest("lint", "--root", str(root), "--against", str(baseline))
        self.assertEqual(worse.returncode, 1, worse.stdout)
        self.assertIn(b"must fix current_state_untagged", worse.stdout)

    def test_codes_limits_the_run(self) -> None:
        root = self.workshop({STAND: leaf("truing-stand", STAND_UP, kind="diary")})
        result = forest("lint", "--root", str(root), "--codes", "leaf_over_cap", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["checked"], ["leaf_over_cap"])
        bad = forest("lint", "--root", str(root), "--codes", "nope")
        self.assertEqual(bad.returncode, 2)
        self.assertIn(b"lint_code_unknown", bad.stderr)
