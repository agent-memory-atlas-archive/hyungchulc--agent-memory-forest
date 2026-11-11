"""An agent's tree comes by itself: from its settings entry or its first raw lane file (contract, section 3).

The forest is a copy of the invented synthetic forest (an owner who runs a
pottery studio, an orchestrator and two bots); every agent added here is made
up in the same style.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v1_support import ForestTestCase  # noqa: E402

from memory_forest import agents, levels, lint, move, registry  # noqa: E402
from memory_forest.checks import run_checks  # noqa: E402
from memory_forest.config import load_config, parse_config  # noqa: E402

TODAY = "2042-05-15"
LANE_LINE = '{"at": "2042-05-15T09:00:00-03:00", "text": "packed two parcels"}\n'


class AgentTestCase(ForestTestCase):
    def forest(self, settings: str = "") -> Path:
        root = self.copy_synthetic()
        if settings:
            path = root / "forest.toml"
            path.write_text(path.read_text(encoding="utf-8") + settings, encoding="utf-8")
        return root

    def lane(self, root: Path, name: str) -> None:
        folder = root / "06 istm" / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "2042-05-15.jsonl").write_text(LANE_LINE, encoding="utf-8")

    def problems(self, root: Path) -> list[str]:
        return [problem.render() for problem in run_checks(root, load_config(root))[1]]

    def lint_hits(self, root: Path) -> list[str]:
        report = lint.lint(root)
        return [hit.render() for hit in report.hits]

    def readable(self, root: Path, reader: str | None, **options) -> list[str]:
        return agents.readable_trees(load_config(root), registry.build(root), reader, **options)

    def assertClean(self, root: Path) -> None:
        self.assertEqual(self.problems(root), [])
        self.assertEqual(self.lint_hits(root), [])


class AutomaticTreeTests(AgentTestCase):
    def test_a_lane_folder_makes_the_tree_through_levels_apply(self) -> None:
        root = self.forest()
        self.assertClean(root)
        self.lane(root, "post-bot")
        due = levels.check(root)
        self.assertEqual([(item.subject, item.action, item.new_level, item.home) for item in due],
                         [("post-bot", "agent", "tree", "06 istm/post-bot/")])
        self.assertIn("level_due", "".join(self.problems(root)))
        result = levels.apply(root, today=TODAY)
        self.assertEqual([item["subject"] for item in result["changes"]], ["post-bot"])
        self.assertEqual(result["refused"], [])
        page = (root / "02 ltm/post-bot_LTM.md").read_text(encoding="utf-8")
        self.assertIn("entity: post-bot", page)
        self.assertIn(f"## Role\n\n{move.ROLE_UNSET}\n", page)
        self.assertIn("- `post-bot` — post-bot · agent · tree · `02 ltm/post-bot_LTM.md`",
                      (root / "03 mtm/post-bot/post-bot_MTM.md").read_text(encoding="utf-8"))
        self.assertIn(f"- `post-bot` — post-bot · {move.ROLE_UNSET}",
                      (root / "01 xltm/XLTM.md").read_text(encoding="utf-8"))
        self.assertClean(root)
        self.assertIn("post-bot", self.readable(root, "studio-hub"))
        self.assertNotIn("post-bot", self.readable(root, "kiln-bot"))
        self.assertEqual(levels.check(root), [])

    def test_a_settings_entry_alone_makes_the_tree_with_its_name_and_role(self) -> None:
        root = self.forest('\n[agents.known.glaze-bot]\nname = "Glaze Bot"\nrole = "mixes and tests glazes"\n')
        self.assertEqual([item.home for item in levels.check(root)], ["forest.toml"])
        levels.apply(root, today=TODAY)
        self.assertIn("- `glaze-bot` — Glaze Bot · mixes and tests glazes",
                      (root / "01 xltm/XLTM.md").read_text(encoding="utf-8"))
        self.assertClean(root)
        self.assertIn("glaze-bot", self.readable(root, "studio-hub"))
        self.assertNotIn("glaze-bot", self.readable(root, "ledger-bot"))

    def test_two_agents_in_one_apply(self) -> None:
        root = self.forest('\n[agents.known.glaze-bot]\nname = "Glaze Bot"\n')
        self.lane(root, "post-bot")
        result = levels.apply(root, today=TODAY)
        self.assertEqual(sorted(item["subject"] for item in result["changes"]), ["glaze-bot", "post-bot"])
        self.assertClean(root)

    def test_new_agent_then_apply_changes_nothing(self) -> None:
        root = self.forest()
        self.lane(root, "post-bot")
        move.execute(root, "new-agent", id="post-bot", name="Post Bot", role="packs and ships orders", today=TODAY)
        self.assertEqual(levels.check(root), [])
        self.assertEqual(levels.apply(root, today=TODAY)["changes"], [])
        self.assertClean(root)

    def declare(self, root: Path, entry: str) -> None:
        settings = root / "forest.toml"
        settings.write_text(settings.read_text(encoding="utf-8") + entry, encoding="utf-8")

    def test_the_settings_fill_what_a_tree_left_unset(self) -> None:
        root = self.forest()
        self.lane(root, "post-bot")
        levels.apply(root, today=TODAY)
        self.declare(root, '\n[agents.known.post-bot]\nname = "Parcel Bot"\nrole = "ships orders"\n')
        self.assertEqual([hit for hit in self.lint_hits(root) if "agent_settings_differ" in hit], [])
        due = levels.check(root)
        self.assertEqual([(item.subject, item.action, item.why) for item in due],
                         [("post-bot", "fill", "agent; its settings entry gives its unset name and role")])
        result = levels.apply(root, today="2042-05-16")
        self.assertEqual(result["refused"], [])
        page = (root / "02 ltm/post-bot_LTM.md").read_text(encoding="utf-8")
        self.assertIn("# Parcel Bot\n", page)
        self.assertIn("## Role\n\nships orders\n", page)
        self.assertIn('Name of `post-bot`: was "post-bot", now "Parcel Bot"', page)
        self.assertIn(f'Role of `post-bot`: was "{move.ROLE_UNSET}", now "ships orders"', page)
        self.assertIn("- `post-bot` — Parcel Bot · agent · tree · `02 ltm/post-bot_LTM.md`",
                      (root / "03 mtm/post-bot/post-bot_MTM.md").read_text(encoding="utf-8"))
        self.assertIn("- `post-bot` — Parcel Bot · ships orders", (root / "01 xltm/XLTM.md").read_text(encoding="utf-8"))
        self.assertEqual(levels.check(root), [])
        self.assertClean(root)

    def test_a_set_value_is_never_changed_and_the_lint_reports_one_set_differently(self) -> None:
        root = self.forest()
        self.lane(root, "post-bot")
        move.execute(root, "new-agent", id="post-bot", name="Post Bot", role="packs and ships orders", today=TODAY)
        before = (root / "02 ltm/post-bot_LTM.md").read_bytes()
        self.declare(root, '\n[agents.known.post-bot]\nname = "Parcel Bot"\nrole = "ships orders"\n')
        self.assertEqual(levels.check(root), [])
        self.assertEqual(levels.apply(root, today=TODAY)["changes"], [])
        self.assertEqual((root / "02 ltm/post-bot_LTM.md").read_bytes(), before)
        hits = [hit for hit in self.lint_hits(root) if "agent_settings_differ" in hit]
        self.assertEqual(len(hits), 2, hits)
        self.declare(root, "")
        settings = root / "forest.toml"
        settings.write_text(settings.read_text(encoding="utf-8").replace('"Parcel Bot"', '"Post Bot"').replace(
            '"ships orders"', '"Packs and ships orders."'), encoding="utf-8")
        self.assertEqual([hit for hit in self.lint_hits(root) if "agent_settings_differ" in hit], [])

    def test_auto_create_off_makes_a_lane_an_upkeep_hit(self) -> None:
        root = self.forest("")
        settings = root / "forest.toml"
        settings.write_text(settings.read_text(encoding="utf-8").replace(
            'default_scope = "own+shared"', 'default_scope = "own+shared"\nauto_create = false'), encoding="utf-8")
        self.lane(root, "post-bot")
        self.assertEqual(levels.check(root), [])
        self.assertEqual([problem.split(":")[1].strip() for problem in self.problems(root)],
                         ["agent_lane_without_tree"])
        self.assertEqual([hit.split()[0] for hit in self.lint_hits(root)], ["agent_lane_without_tree"])

    def test_a_lane_folder_that_cannot_be_an_agent_never_becomes_a_tree(self) -> None:
        for name in ("Post Bot", "studio", "juno-hale", "none"):
            with self.subTest(name=name):
                root = self.forest()
                self.addCleanup(shutil.rmtree, root, True)
                self.lane(root, name)
                self.assertEqual(levels.check(root), [])
                problems = self.problems(root)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("agent_id_invalid", problems[0])
                self.assertNotIn(name, registry.build(root).agents)
            shutil.rmtree(root, True)

    def test_a_folded_agent_s_lane_makes_no_tree_again(self) -> None:
        root = self.forest()
        self.lane(root, "post-bot")
        levels.apply(root, today=TODAY)
        move.execute(root, "fold-tree", tree="post-bot", into="studio", today=TODAY)
        self.assertEqual(levels.check(root), [])
        self.assertIn("- `post-bot` — post-bot · component · branch",
                      (root / "03 mtm/studio/studio_MTM.md").read_text(encoding="utf-8"))


class ReadableTreeTests(AgentTestCase):
    def test_each_kind_of_reader(self) -> None:
        root = self.forest()
        every = ["admin", "kiln-bot", "ledger-bot", "people", "studio", "studio-hub"]
        shared = ["admin", "people", "studio"]
        self.assertEqual(self.readable(root, "studio-hub"), every)
        self.assertEqual(self.readable(root, "kiln-bot"), [*shared[:1], "kiln-bot", *shared[1:]])
        self.assertEqual(self.readable(root, "kiln-bot", named=["ledger-bot", "people"]),
                         ["admin", "kiln-bot", "ledger-bot", "people", "studio"])
        self.assertEqual(self.readable(root, "a-stranger"), shared)
        self.assertEqual(self.readable(root, None), shared)
        config = parse_config((root / "forest.toml").read_text(encoding="utf-8").replace(
            'default_scope = "own+shared"', 'default_scope = "all"'))
        self.assertEqual(agents.readable_trees(config, registry.build(root), "kiln-bot"), every)
