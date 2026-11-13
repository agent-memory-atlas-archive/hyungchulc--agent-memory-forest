"""``forest move …`` on made-up forests: whole listings with bytes before and after each operation."""

from __future__ import annotations

import bounds  # every invocation has a deadline

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v1_support import ForestTestCase, leaf_text  # noqa: E402

from memory_forest import lint, maps, move, movelog, registry  # noqa: E402
from memory_forest.errors import MemoryForestError  # noqa: E402

TODAY = "2051-03-05"
REPAIRS = "04 stm/bakery/deck-oven/repairs.md"
MILLSTONE = "04 stm/bakery/suppliers/millstone-flour.md"


def snapshot(root: Path) -> dict[str, bytes]:
    """Every page of the forest with its bytes (the ``.forest`` state folder aside)."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".forest" not in path.relative_to(root).parts
    }


def difference(before: dict[str, bytes], after: dict[str, bytes]) -> dict[str, list[str]]:
    return {
        "created": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "changed": sorted(path for path in set(before) & set(after) if before[path] != after[path]),
    }


def moves(root: Path) -> list[dict]:
    log = root / ".forest" / "moves.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


class MoveTestCase(ForestTestCase):
    def forest(self) -> Path:
        root = self.make_forest()
        maps.apply(root)
        self.assertEqual(maps.check(root), [])
        self.assertEqual(registry.check(root), [])
        return root

    def run_move(self, root: Path, operation: str, **arguments) -> tuple[dict, dict[str, list[str]]]:
        before = snapshot(root)
        result = move.execute(root, operation, today=TODAY, **arguments)
        after = snapshot(root)
        self.assertEqual(registry.check(root), [], operation)
        self.assertEqual(maps.check(root), [], operation)
        return result, difference(before, after)

    def text(self, root: Path, path: str) -> str:
        return (root / path).read_text(encoding="utf-8")

    def assertResolves(self, root: Path, old: str, current: str) -> None:
        found = movelog.resolve(root, old)
        self.assertEqual((found.status, found.path), ("current", current), old)


class LeafAndRenameTests(MoveTestCase):
    def test_leaf_moves_the_page_rewrites_references_and_records_the_change(self) -> None:
        root = self.forest()
        target = "04 stm/bakery/deck-oven/fixes.md"
        _, changed = self.run_move(root, "leaf", source=REPAIRS, target=target)
        self.assertEqual(changed["created"], [target])
        self.assertEqual(changed["removed"], [REPAIRS])
        self.assertIn("03 mtm/bakery/deck-oven.md", changed["changed"])
        moved = self.text(root, target)
        self.assertIn(f'Path changed: was "{REPAIRS}", now "{target}".', moved)
        self.assertIn("[[03 mtm/bakery/deck-oven|", moved)
        self.assertIn("`fixes.md`", self.text(root, "03 mtm/bakery/deck-oven.md"))
        self.assertNotIn("`repairs.md`", self.text(root, "03 mtm/bakery/deck-oven.md"))
        self.assertResolves(root, REPAIRS, target)
        self.assertEqual([(entry["operation"], entry["from"], entry["to"]) for entry in moves(root)],
                         [("move", REPAIRS, target)])

    def test_leaf_into_a_branch_folder_keeps_its_name_and_its_listed_description(self) -> None:
        root = self.forest()
        board = root / "03 mtm/bakery/suppliers.md"
        board.write_text(board.read_text(encoding="utf-8").replace(
            "`millstone-flour.md` — millstone-flour", "`millstone-flour.md` — what the mill sends"), encoding="utf-8")
        _, changed = self.run_move(root, "leaf", source=MILLSTONE, target="04 stm/bakery/deck-oven")
        target = "04 stm/bakery/deck-oven/millstone-flour.md"
        self.assertEqual(changed["created"], [target])
        self.assertIn("`millstone-flour.md` — what the mill sends", self.text(root, "03 mtm/bakery/deck-oven.md"))
        self.assertIn(f"`{target}`", self.text(root, "03 mtm/bakery/bakery_MTM.md"))
        self.assertResolves(root, MILLSTONE, target)

    def test_a_dry_run_and_a_refused_move_write_nothing(self) -> None:
        root = self.forest()
        before = snapshot(root)
        result = move.execute(root, "leaf", source=REPAIRS, target="04 stm/bakery/deck-oven/fixes.md",
                              dry_run=True, today=TODAY)
        self.assertEqual(result["created"], ["04 stm/bakery/deck-oven/fixes.md"])
        with self.assertRaises(MemoryForestError):
            move.execute(root, "leaf", source=REPAIRS, target=MILLSTONE, today=TODAY)
        with self.assertRaises(MemoryForestError):
            move.execute(root, "leaf", source="04 stm/bakery/deck-oven/missing.md", target="04 stm/bakery/x.md",
                         today=TODAY)
        self.assertEqual(snapshot(root), before)
        self.assertEqual(moves(root), [])

    def test_rename_a_branch_subject_moves_board_and_folder_and_keeps_the_old_name_as_an_alias(self) -> None:
        root = self.forest()
        _, changed = self.run_move(root, "rename", target="deck-oven", new="hearth-oven", name="Hearth Oven")
        self.assertEqual(changed["created"], ["03 mtm/bakery/hearth-oven.md", "04 stm/bakery/hearth-oven/repairs.md"])
        self.assertEqual(changed["removed"], ["03 mtm/bakery/deck-oven.md", REPAIRS])
        index = self.text(root, "03 mtm/bakery/bakery_MTM.md")
        self.assertIn("- `hearth-oven` — Hearth Oven · device · branch · `03 mtm/bakery/hearth-oven.md`", index)
        self.assertIn("aliases: the old oven; DO-3; Deck Oven", index)
        self.assertNotIn("- `deck-oven`", index)
        self.assertIn("- `hearth-oven.md` — Hearth Oven", index)
        self.assertIn("\n# Hearth Oven\n", self.text(root, "03 mtm/bakery/hearth-oven.md"))
        self.assertIn('Subject id: was "deck-oven", now "hearth-oven".', index)
        about = self.text(root, MILLSTONE)
        self.assertIn("hearth-oven", about)
        self.assertNotIn("deck-oven", about.split("---", 2)[1])
        self.assertResolves(root, "03 mtm/bakery/deck-oven.md", "03 mtm/bakery/hearth-oven.md")
        self.assertResolves(root, REPAIRS, "04 stm/bakery/hearth-oven/repairs.md")

    def test_rename_a_branch_folder_only(self) -> None:
        root = self.forest()
        _, changed = self.run_move(root, "rename", target="bakery/suppliers", new="mills")
        self.assertEqual(changed["created"], ["03 mtm/bakery/mills.md", "04 stm/bakery/mills/millstone-flour.md"])
        self.assertIn("`04 stm/bakery/mills/millstone-flour.md`", self.text(root, "03 mtm/bakery/bakery_MTM.md"))
        self.assertResolves(root, MILLSTONE, "04 stm/bakery/mills/millstone-flour.md")

    def test_rename_only_the_name_moves_no_page(self) -> None:
        root = self.forest()
        _, changed = self.run_move(root, "rename", target="millstone-flour", name="Millstone Mill")
        self.assertEqual((changed["created"], changed["removed"]), ([], []))
        self.assertIn("— Millstone Mill · organization · leaf", self.text(root, "03 mtm/bakery/bakery_MTM.md"))

    def test_a_rename_to_a_name_that_reads_as_a_sign_in_value_is_refused(self) -> None:
        root = self.forest()
        before = snapshot(root)
        with self.assertRaises(MemoryForestError) as raised:
            move.execute(root, "rename", target="millstone-flour", name="Millstone verification code 4821", today=TODAY)
        self.assertEqual(raised.exception.code, "plan_check_failed")
        problems = raised.exception.details["problems"]
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].startswith("registry_name_masked: 03 mtm/bakery/bakery_MTM.md: "), problems)
        self.assertNotIn("4821", problems[0])  # the reason shows the masked form
        self.assertEqual(snapshot(root), before)


class LevelTests(MoveTestCase):
    def test_promote_a_leaf_subject_to_a_branch(self) -> None:
        root = self.forest()
        result, changed = self.run_move(root, "promote", subject="millstone-flour")
        board = "03 mtm/bakery/millstone-flour.md"
        self.assertEqual(changed["created"], [board, "04 stm/bakery/millstone-flour/notes.md"])
        self.assertEqual(changed["removed"], [MILLSTONE])
        self.assertIn("- `millstone-flour` — Millstone Flour · organization · branch · "
                      f"`{board}`", self.text(root, "03 mtm/bakery/bakery_MTM.md"))
        self.assertIn("`notes.md`", self.text(root, board))
        self.assertResolves(root, MILLSTONE, "04 stm/bakery/millstone-flour/notes.md")
        self.assertEqual(result["operations"], ["promote millstone-flour -> branch"])

    def test_promote_a_branch_subject_to_a_tree(self) -> None:
        root = self.forest()
        (root / "04 stm/bakery/deck-oven/firing.md").write_text(
            leaf_text("deck-oven").replace("subtopic: notes", "subtopic: firing"), encoding="utf-8")
        maps.apply(root)
        _, changed = self.run_move(root, "promote", subject="deck-oven")
        self.assertIn("02 ltm/deck-oven_LTM.md", changed["created"])
        self.assertIn("03 mtm/deck-oven/deck-oven_MTM.md", changed["created"])
        self.assertIn("03 mtm/bakery/deck-oven.md", changed["removed"])
        index = self.text(root, "03 mtm/deck-oven/deck-oven_MTM.md")
        self.assertIn("- `deck-oven` — Deck Oven · device · tree · `02 ltm/deck-oven_LTM.md`", index)
        self.assertNotIn("- `deck-oven`", self.text(root, "03 mtm/bakery/bakery_MTM.md"))
        self.assertIn("`deck-oven`", self.text(root, "01 xltm/XLTM.md").split("## Trees", 1)[1])
        self.assertResolves(root, "03 mtm/bakery/deck-oven.md", "02 ltm/deck-oven_LTM.md")
        resolved = movelog.resolve(root, REPAIRS)
        self.assertEqual(resolved.status, "current")
        self.assertTrue(resolved.path.startswith("04 stm/deck-oven/"))

    def test_promote_refuses_a_branch_that_holds_another_subjects_pages(self) -> None:
        root = self.forest()
        self.run_move(root, "leaf", source=MILLSTONE, target="04 stm/bakery/deck-oven")
        before = snapshot(root)
        with self.assertRaises(MemoryForestError) as caught:
            move.execute(root, "promote", subject="deck-oven", today=TODAY)
        self.assertEqual(caught.exception.code, "branch_holds_other_subjects")
        self.assertEqual(snapshot(root), before)

    def test_merge_moves_the_lines_and_the_ids_of_the_dropped_subject(self) -> None:
        root = self.forest()
        _, changed = self.run_move(root, "merge", keep="deck-oven", drop="millstone-flour")
        self.assertEqual(changed["removed"], [MILLSTONE])
        index = self.text(root, "03 mtm/bakery/bakery_MTM.md")
        self.assertNotIn("- `millstone-flour`", index)
        self.assertIn("aliases: the old oven; DO-3; Millstone Flour; Millstone", index)
        resolved = movelog.resolve(root, MILLSTONE)
        self.assertEqual(resolved.status, "current")
        self.assertTrue(resolved.path.startswith("04 stm/bakery/deck-oven/"))

    def test_set_type_and_set_change_one_field_each(self) -> None:
        root = self.forest()
        _, changed = self.run_move(root, "set-type", subject="deck-oven", type="component")
        self.assertEqual(changed, {"created": [], "removed": [], "changed": ["03 mtm/bakery/bakery_MTM.md"]})
        self.assertIn("— Deck Oven · component · branch", self.text(root, "03 mtm/bakery/bakery_MTM.md"))
        _, changed = self.run_move(root, "set", path=REPAIRS, kind="plan", about="millstone-flour")
        self.assertEqual(changed["changed"], [REPAIRS])
        head = self.text(root, REPAIRS).split("---", 2)[1]
        self.assertIn("kind: plan", head)
        self.assertIn("about: millstone-flour", head)
        with self.assertRaises(MemoryForestError):
            move.execute(root, "set", path=REPAIRS, kind="not-a-kind", today=TODAY)
        with self.assertRaises(MemoryForestError):
            move.execute(root, "set-type", subject="deck-oven", type="not-a-type", today=TODAY)


class ArchiveAndBranchTests(MoveTestCase):
    def test_archive_takes_the_page_out_and_marks_its_references(self) -> None:
        root = self.forest()
        board = root / "03 mtm/bakery/deck-oven.md"
        board.write_text(board.read_text(encoding="utf-8").replace(
            "## Pointers", f"See `{REPAIRS}` for the old fixes.\n\n## Pointers"), encoding="utf-8")
        result, changed = self.run_move(root, "archive", path=REPAIRS, reason="the oven was replaced")
        self.assertEqual(changed["removed"], [REPAIRS])
        self.assertEqual(result["archived"], [REPAIRS])
        self.assertIn(f"`{REPAIRS}` (archived)", self.text(root, "03 mtm/bakery/deck-oven.md"))
        self.assertEqual(movelog.resolve(root, REPAIRS).status, "archived")
        self.assertTrue(any((root / ".forest" / "archive").rglob("*.gz")))

    def test_move_branch_carries_board_folder_and_registry_line(self) -> None:
        root = self.forest()
        _, changed = self.run_move(root, "move-branch", branch="bakery/deck-oven", tree="people")
        self.assertEqual(changed["created"], ["03 mtm/people/deck-oven.md", "04 stm/people/deck-oven/repairs.md"])
        self.assertEqual(changed["removed"], ["03 mtm/bakery/deck-oven.md", REPAIRS])
        self.assertIn("- `deck-oven` — Deck Oven · device · branch · `03 mtm/people/deck-oven.md`",
                      self.text(root, "03 mtm/people/people_MTM.md"))
        self.assertNotIn("- `deck-oven`", self.text(root, "03 mtm/bakery/bakery_MTM.md"))
        self.assertResolves(root, REPAIRS, "04 stm/people/deck-oven/repairs.md")
        self.assertResolves(root, "03 mtm/bakery/bakery_MTM.md#deck-oven", "03 mtm/people/people_MTM.md#deck-oven")

    def test_resolve_follows_a_folder_that_moved_whole_twice(self) -> None:
        root = self.forest()
        self.run_move(root, "move-branch", branch="bakery/deck-oven", tree="people")
        self.run_move(root, "new-tree", tree="garden", description="the herb garden.")
        self.run_move(root, "move-branch", branch="people/deck-oven", tree="garden")
        for old in ("04 stm/bakery/deck-oven", "04 stm/bakery/deck-oven/", "04 stm/people/deck-oven"):
            self.assertResolves(root, old, "04 stm/garden/deck-oven")
        found = movelog.resolve(root, "04 stm/bakery/deck-oven")
        self.assertEqual([item.to_path for item in found.chain], ["04 stm/people/deck-oven", "04 stm/garden/deck-oven"])
        self.assertResolves(root, REPAIRS, "04 stm/garden/deck-oven/repairs.md")

    def test_move_branch_under_another_name_renames_the_branch_subject_with_it(self) -> None:
        root = self.forest()
        _, changed = self.run_move(root, "move-branch", branch="bakery/deck-oven", tree="people", name="oven")
        self.assertEqual(changed["created"], ["03 mtm/people/oven.md", "04 stm/people/oven/repairs.md"])
        self.assertIn("- `oven` — Deck Oven · device · branch · `03 mtm/people/oven.md`",
                      self.text(root, "03 mtm/people/people_MTM.md"))
        self.assertResolves(root, REPAIRS, "04 stm/people/oven/repairs.md")


class TreeTests(MoveTestCase):
    def test_new_tree_and_new_agent_create_their_pages_and_root_lines(self) -> None:
        root = self.forest()
        _, changed = self.run_move(root, "new-tree", tree="garden", description="the herb garden.")
        self.assertEqual(changed["created"], ["02 ltm/garden_LTM.md", "03 mtm/garden/garden_MTM.md"])
        self.assertEqual(changed["changed"], ["01 xltm/XLTM.md"])
        self.assertIn("- `garden` — the herb garden", self.text(root, "01 xltm/XLTM.md"))
        _, changed = self.run_move(root, "new-agent", id="till-bot", name="Till Bot", role="counts the till",
                                   aliases=["the till"])
        self.assertEqual(changed["created"], ["02 ltm/till-bot_LTM.md", "03 mtm/till-bot/till-bot_MTM.md"])
        agents = self.text(root, "01 xltm/XLTM.md").split("## Agents", 1)[1]
        self.assertIn("`till-bot`", agents)
        self.assertIn("- `till-bot` — Till Bot · agent · tree · `02 ltm/till-bot_LTM.md` · aliases: the till",
                      self.text(root, "03 mtm/till-bot/till-bot_MTM.md"))
        with self.assertRaises(MemoryForestError):
            move.execute(root, "new-tree", tree="garden", description="again", today=TODAY)

    def test_retire_an_agent_tree_keeps_its_pages_and_marks_it_ended(self) -> None:
        root = self.forest()
        _, changed = self.run_move(root, "retire-tree", tree="front-desk")
        self.assertEqual((changed["created"], changed["removed"]), ([], []))
        self.assertEqual(changed["changed"], ["01 xltm/XLTM.md", "03 mtm/front-desk/front-desk_MTM.md"])
        self.assertIn("· aliases: the desk · ended", self.text(root, "03 mtm/front-desk/front-desk_MTM.md"))
        self.assertIn("ended", self.text(root, "01 xltm/XLTM.md").split("`front-desk`", 1)[1].splitlines()[0])

    def test_fold_tree_turns_a_tree_into_a_branch_and_every_old_path_resolves(self) -> None:
        root = self.forest()
        leaf = "04 stm/front-desk/shifts/opening.md"
        (root / leaf).parent.mkdir(parents=True)
        (root / leaf).write_text(leaf_text("front-desk").replace("subtopic: notes", "subtopic: shifts"),
                                 encoding="utf-8")
        (root / "03 mtm/front-desk/shifts.md").write_text("# shifts\n\nWho opens and closes.\n", encoding="utf-8")
        maps.apply(root)
        result, changed = self.run_move(root, "fold-tree", tree="front-desk", into="bakery")
        self.assertIn("03 mtm/bakery/front-desk.md", changed["created"])
        self.assertIn("02 ltm/front-desk_LTM.md", changed["removed"])
        self.assertIn("03 mtm/front-desk/front-desk_MTM.md", changed["removed"])
        self.assertNotIn("`front-desk`", self.text(root, "01 xltm/XLTM.md").split("## Agents", 1)[1])
        self.assertIn("- `front-desk` — Front Desk · component · branch · `03 mtm/bakery/front-desk.md`",
                      self.text(root, "03 mtm/bakery/bakery_MTM.md"))
        self.assertTrue(result["notes"])
        self.assertResolves(root, "02 ltm/front-desk_LTM.md", "03 mtm/bakery/front-desk.md")
        moved = movelog.resolve(root, leaf)
        self.assertEqual(moved.status, "current")
        self.assertTrue(moved.path.startswith("04 stm/bakery/front-desk/"))
        self.assertIn("subtopic: shifts", self.text(root, moved.path).split("---", 2)[1])


class PlanFileTests(MoveTestCase):
    def test_a_plan_runs_every_step_or_none(self) -> None:
        root = self.forest()
        steps = [
            {"op": "rename", "target": "bakery/suppliers", "new": "mills"},
            {"op": "promote", "subject": "millstone-flour"},
        ]
        result, changed = self.run_move(root, "plan", operations=steps)
        self.assertEqual(len(result["operations"]), 2)
        self.assertIn("03 mtm/bakery/millstone-flour.md", changed["created"])
        self.assertResolves(root, MILLSTONE, "04 stm/bakery/millstone-flour/notes.md")

        before = snapshot(root)
        logged = len(moves(root))
        broken = [{"op": "new-tree", "tree": "garden", "description": "the herb garden."},
                  {"op": "leaf", "source": "04 stm/nowhere/x.md", "target": "04 stm/bakery/x.md"}]
        with self.assertRaises(MemoryForestError):
            move.execute(root, "plan", operations=broken, today=TODAY)
        self.assertEqual(snapshot(root), before)
        self.assertEqual(len(moves(root)), logged)

    def test_read_plan_file_takes_a_list_or_an_object_with_operations(self) -> None:
        path = self.base / "plan.json"
        path.write_text(json.dumps({"operations": [{"op": "promote", "subject": "x"}]}), encoding="utf-8")
        self.assertEqual(move.read_plan_file(path), [{"op": "promote", "subject": "x"}])


def stream_page(period: str, lines: list[str]) -> str:
    return (
        "---\nentity: deck-oven\nsubtopic: repairs\nkind: event\nabout: none\nupdated_at: 2051-03-02\n---\n\n"
        f"# Deck Oven: repairs ({period})\n\n## Timeline\n\n" + "".join(f"{line}\n" for line in lines)
        + "\n## Links\n\n- up: [[03 mtm/bakery/deck-oven|Deck Oven]]\n"
    )


class RecutStreamTests(MoveTestCase):
    JANUARY = ["- 2051-01-04 08:00 Pacific/Auckland — The door seal was replaced.",
               "- 2051-01-20 17:30 Pacific/Auckland — The left burner was cleaned."]
    FEBRUARY = ["- 2051-02-11 07:45 Pacific/Auckland — The thermostat was set again.",
                "  - The baker found it two degrees low."]

    def stream(self, root: Path) -> list[str]:
        pages = {"04 stm/bakery/deck-oven/repairs-2051-02.md": stream_page("2051-02", self.FEBRUARY),
                 "04 stm/bakery/deck-oven/repairs-2051-01.md": stream_page("2051-01", self.JANUARY)}
        for path, text in pages.items():
            (root / path).write_text(text, encoding="utf-8")
        maps.apply(root)
        return sorted(pages)

    def test_small_month_pages_join_into_one_year_page_in_time_order(self) -> None:
        root = self.forest()
        pages = self.stream(root)
        result, changed = self.run_move(root, "recut-stream", subject="deck-oven", leaves=pages)
        year = "04 stm/bakery/deck-oven/repairs-2051.md"
        self.assertEqual(changed["created"], [year])
        self.assertEqual(changed["removed"], pages)
        text = self.text(root, year)
        timeline = text.split("## Timeline", 1)[1].split("## Links", 1)[0]
        self.assertEqual([line for line in timeline.splitlines() if line.strip()], self.JANUARY + self.FEBRUARY)
        self.assertIn("updated_at: 2051-02-11", text)
        self.assertEqual(sorted(result["copies_kept"]), pages)
        for page in pages:
            self.assertResolves(root, page, year)

    def test_a_year_over_the_cap_is_cut_into_smaller_periods(self) -> None:
        root = self.forest()
        settings = root / "forest.toml"
        settings.write_text(settings.read_text(encoding="utf-8")
                            + "\n[limits]\nleaf_cap_bytes = 420\nentry_admit_margin_bytes = 100\n", encoding="utf-8")
        pages = self.stream(root)
        (root / pages[0]).write_text(stream_page("2051-01", self.JANUARY + [
            "- 2051-05-02 09:00 Pacific/Auckland — The chimney was swept.",
        ]), encoding="utf-8")
        _, changed = self.run_move(root, "recut-stream", subject="deck-oven", leaves=pages)
        made = changed["created"]
        self.assertNotIn("04 stm/bakery/deck-oven/repairs-2051.md", made)
        self.assertIn("04 stm/bakery/deck-oven/repairs-2051-q2.md", made)
        joined = "".join(self.text(root, path) for path in made + [p for p in pages if (root / p).exists()])
        for line in self.JANUARY + self.FEBRUARY:
            self.assertEqual(joined.count(line), 1, line)

    def test_a_month_over_the_cap_is_cut_into_runs_of_whole_days(self) -> None:
        root = self.forest()
        settings = root / "forest.toml"
        settings.write_text(settings.read_text(encoding="utf-8")
                            + "\n[limits]\nleaf_cap_bytes = 520\nentry_admit_margin_bytes = 100\n", encoding="utf-8")
        lines = [f"- 2051-01-{day:02d} 0{hour}:00 Pacific/Auckland — The oven door was checked, round {number}."
                 for number, (day, hour) in enumerate([(4, 7), (4, 8), (9, 7), (15, 7), (15, 8), (15, 9),
                                                       (22, 7)], start=1)]
        page = "04 stm/bakery/deck-oven/repairs-2051-01.md"
        (root / page).write_text(stream_page("2051-01", lines), encoding="utf-8")
        maps.apply(root)
        _, changed = self.run_move(root, "recut-stream", subject="deck-oven", leaves=[page])
        folder = "04 stm/bakery/deck-oven/"
        made = sorted(path for path in (root / folder).iterdir() if path.name.startswith("repairs-"))
        names = [path.name for path in made]
        self.assertIn("repairs-2051-01.md", names)  # the first run keeps the month's name
        self.assertTrue(all(name == "repairs-2051-01.md" or name.startswith("repairs-2051-01-") for name in names),
                        names)
        self.assertGreater(len(names), 2)
        held: list[str] = []
        for path in made:
            text = path.read_text(encoding="utf-8")
            self.assertLessEqual(len(text.split("## Update record")[0].encode("utf-8")), 520 + 200)
            timeline = [line for line in text.split("## Timeline", 1)[1].split("## Links", 1)[0].splitlines()
                        if line.strip()]
            days = sorted(line[2:12] for line in timeline)
            span = days[0] if days[0] == days[-1] else f"{days[0]} to {days[-1]}"
            self.assertIn(f"({span})", text.splitlines()[text.splitlines().index("---", 1) + 2])
            if path.name != "repairs-2051-01.md":
                first = path.stem.removeprefix("repairs-")[:10]
                self.assertEqual(days[0], first)  # a later run is named by its first day
            held.extend(timeline)
        self.assertEqual(sorted(held), sorted(lines))  # each line on exactly one page
        self.assertEqual(len(held), len(set(held)))
        report = lint.lint(root, codes=["leaf_dated_name"])
        self.assertEqual([hit.render() for hit in report.hits], [])

    def test_a_day_past_the_cap_takes_numbered_pages_in_line_order(self) -> None:
        root = self.forest()
        settings = root / "forest.toml"
        settings.write_text(settings.read_text(encoding="utf-8")
                            + "\n[limits]\nleaf_cap_bytes = 470\nentry_admit_margin_bytes = 100\n", encoding="utf-8")
        lines = ["- 2051-01-03 07:00 Pacific/Auckland — The flue was swept before the first bake."]
        lines += [f"- 2051-01-15 0{hour}:00 Pacific/Auckland — The left burner was relit, try {hour}." for hour in range(1, 8)]
        page = "04 stm/bakery/deck-oven/repairs-2051-01.md"
        (root / page).write_text(stream_page("2051-01", lines), encoding="utf-8")
        maps.apply(root)
        self.run_move(root, "recut-stream", subject="deck-oven", leaves=[page])
        folder = root / "04 stm/bakery/deck-oven"
        numbered = sorted((path.name for path in folder.glob("repairs-2051-01-15-*.md")),
                          key=lambda name: int(name[:-3].rsplit("-", 1)[1]))
        self.assertTrue(numbered and numbered[0] == "repairs-2051-01-15-2.md", numbered)
        order = ["repairs-2051-01.md", "repairs-2051-01-15.md", *numbered]
        order = [name for name in order if (folder / name).exists()]
        held = [line for name in order
                for line in (folder / name).read_text(encoding="utf-8").split("## Timeline", 1)[1]
                .split("## Links", 1)[0].splitlines() if line.strip()]
        self.assertEqual(held, lines)  # in line order, each once

    def test_a_stream_page_with_free_sections_is_refused(self) -> None:
        root = self.forest()
        pages = self.stream(root)
        target = root / pages[0]
        target.write_text(target.read_text(encoding="utf-8").replace("## Links", "## Notes\n\nfree text\n\n## Links"),
                          encoding="utf-8")
        before = snapshot(root)
        with self.assertRaises(MemoryForestError) as caught:
            move.execute(root, "recut-stream", subject="deck-oven", leaves=pages, today=TODAY)
        self.assertEqual(caught.exception.code, "stream_leaf_not_a_list")
        self.assertEqual(snapshot(root), before)
