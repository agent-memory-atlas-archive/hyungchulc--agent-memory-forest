"""The move, levels, collections and check tools on a copy of the synthetic forest and through the CLI."""

from __future__ import annotations

import bounds  # every invocation has a deadline

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v1_cli import forest  # noqa: E402
from test_v1_move import moves, snapshot  # noqa: E402
from v1_support import ForestTestCase  # noqa: E402

from memory_forest import collections, levels, maps, move, movelog, registry  # noqa: E402

TODAY = "2042-06-01"
ENTRY_PAGE = "04 stm/people/acquaintances/seen-once-2042-q2.md"


class SyntheticForestTests(ForestTestCase):
    def test_levels_check_reports_nothing_due(self) -> None:
        root = self.copy_synthetic()
        self.assertEqual(levels.check(root), [])
        result = forest("levels", "check", "--root", str(root))
        self.assertEqual((result.returncode, result.stdout), (0, b""), result.stderr)

    def test_check_sync_changes_nothing(self) -> None:
        root = self.copy_synthetic()
        before = snapshot(root)
        self.assertEqual(maps.check(root), [])
        result = forest("check", "--sync", "--root", str(root))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(snapshot(root), before)
        self.assertEqual(forest("check", "--root", str(root)).returncode, 0)

    def test_a_round_of_operations_ends_clean_and_every_old_path_resolves(self) -> None:
        root = self.copy_synthetic()
        old_tree = sorted(path for path in snapshot(root) if path.startswith(("02 ltm/kiln-bot", "03 mtm/kiln-bot",
                                                                             "04 stm/kiln-bot")))
        self.assertTrue(old_tree)
        admitted = collections.execute(root, "admit", "wren-castellane", today=TODAY)
        leaf = admitted["created"][0]
        move.execute(root, "promote", subject="wren-castellane", today=TODAY)
        move.execute(root, "rename", target="wren-castellane", new="wren-castellane-ashby",
                     name="Wren Castellane-Ashby", today=TODAY)
        folded = move.execute(root, "fold-tree", tree="kiln-bot", into="studio-hub", today=TODAY)
        self.assertTrue(folded["notes"])

        self.assertEqual(registry.check(root), [])
        result = forest("registry", "check", "--root", str(root))
        self.assertEqual((result.returncode, result.stdout), (0, b""), result.stderr)
        self.assertEqual(maps.check(root), [])
        self.assertEqual(levels.check(root), [])

        now = snapshot(root)
        for old in [f"{ENTRY_PAGE}#wren-castellane", leaf, *old_tree]:
            found = movelog.resolve(root, old)
            self.assertEqual(found.status, "current", old)
            self.assertIn(found.path.split("#", 1)[0], now, old)
        self.assertTrue(movelog.resolve(root, leaf).path.startswith("04 stm/people/wren-castellane-ashby/"))
        self.assertEqual(movelog.resolve(root, "02 ltm/kiln-bot_LTM.md").path, "03 mtm/studio-hub/kiln-bot.md")
        operations = {entry["operation"] for entry in moves(root)}
        self.assertTrue({"admit", "promote", "rename", "fold-tree"} <= operations, operations)


class ToolCommandTests(ForestTestCase):
    def test_move_dry_run_prints_the_plan_and_writes_nothing(self) -> None:
        root = self.copy_synthetic()
        before = snapshot(root)
        result = forest("move", "new-tree", "garden", "--description", "a made-up garden.", "--dry-run",
                        "--root", str(root))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["created"], ["02 ltm/garden_LTM.md", "03 mtm/garden/garden_MTM.md"])
        self.assertEqual(snapshot(root), before)

    def test_move_writes_and_prints_what_changed(self) -> None:
        root = self.copy_synthetic()
        result = forest("move", "new-agent", "loom-bot", "--name", "Loom Bot", "--role", "weaves the samples",
                        "--why", "a new helper", "--root", str(root))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"created: 02 ltm/loom-bot_LTM.md", result.stdout)
        self.assertEqual(registry.check(root), [])

    def test_a_refused_move_exits_2_with_its_code(self) -> None:
        root = self.copy_synthetic()
        result = forest("move", "promote", "no-such-subject", "--root", str(root))
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"subject_unknown", result.stderr)

    def test_a_plan_file_runs_through_the_cli(self) -> None:
        root = self.copy_synthetic()
        plan = self.base / "plan.json"
        plan.write_text(json.dumps([{"op": "new-tree", "tree": "garden", "description": "a made-up garden."},
                                    {"op": "new-tree", "tree": "orchard", "description": "a made-up orchard."}]),
                        encoding="utf-8")
        result = forest("move", "plan", str(plan), "--json", "--root", str(root))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)["operations"]), 2)

    def test_collections_list_and_levels_apply_through_the_cli(self) -> None:
        root = self.copy_synthetic()
        listed = forest("collections", "list", "--json", "--root", str(root))
        self.assertEqual(listed.returncode, 0, listed.stderr)
        pages = json.loads(listed.stdout)["pages"]
        self.assertIn(ENTRY_PAGE, [page["path"] for page in pages])
        applied = forest("levels", "apply", "--root", str(root))
        self.assertEqual((applied.returncode, applied.stdout), (0, b"nothing is due\n"), applied.stderr)
