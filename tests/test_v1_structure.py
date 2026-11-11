from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v1_checks_support import (  # noqa: E402
    AGENT_PAGE,
    COLLECTION,
    INDEX,
    OWNER_LEAF,
    PEOPLE_BOARD,
    ROOT_INDEX,
    STAND,
    STAND_BOARD,
    TREE_PAGE,
    CheckForestTestCase,
    leaf,
)
from v1_support import REPOSITORY_ROOT, SYNTHETIC_FOREST  # noqa: E402

from memory_forest import validate  # noqa: E402

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


class StructureTests(CheckForestTestCase):
    def problems(self, root: Path) -> list[tuple[str, str]]:
        return sorted((problem.code, problem.path) for problem in validate.check_structure(root))

    def test_the_small_forest_and_the_synthetic_forest_are_clean(self) -> None:
        self.assertEqual(self.problems(self.workshop()), [])
        self.assertEqual(self.problems(SYNTHETIC_FOREST), [])

    def test_a_symlink_is_reported_and_nothing_past_it_is_read(self) -> None:
        root = self.workshop()
        (root / "04 stm/workshop/truing-stand/link.md").symlink_to(root / STAND)
        self.assertEqual(self.problems(root), [("symlink", "04 stm/workshop/truing-stand/link.md")])

    def test_files_and_folders_outside_the_layout(self) -> None:
        root = self.workshop()
        (root / "04 stm/workshop/truing-stand/photo.jpg").write_bytes(b"\xff")
        (root / "04 stm/workshop/truing-stand/old").mkdir()
        (root / "01 xltm/notes.md").write_text("# notes\n", encoding="utf-8")
        self.assertCountEqual(self.problems(root), [
            ("page_outside_layout", "01 xltm/notes.md"),
            ("page_outside_layout", "04 stm/workshop/truing-stand/old/"),
            ("page_outside_layout", "04 stm/workshop/truing-stand/photo.jpg"),
        ])

    def test_leaf_entity_and_home(self) -> None:
        root = self.workshop({
            STAND: leaf("", STAND_UP),
            "04 stm/workshop/truing-stand/clamps.md": leaf("ghost", STAND_UP, subtopic="clamps"),
            "04 stm/workshop/people/rims.md": leaf("truing-stand", "03 mtm/workshop/people", subtopic="rims"),
        })
        self.assertCountEqual(self.problems(root), [
            ("leaf_entity_unknown", "04 stm/workshop/truing-stand/clamps.md"),
            ("leaf_entity_missing", STAND),
            ("leaf_misplaced", "04 stm/workshop/people/rims.md"),
        ])

    def test_boards_and_folders_are_paired(self) -> None:
        root = self.workshop()
        (root / "04 stm/workshop/spokes").mkdir()
        (root / "03 mtm/workshop/wheels.md").write_text((root / PEOPLE_BOARD).read_text(encoding="utf-8"),
                                                        encoding="utf-8")
        self.assertCountEqual(self.problems(root), [
            ("board_unpaired", "03 mtm/workshop/wheels.md"),
            ("board_unpaired", "04 stm/workshop/spokes/"),
        ])

    def test_a_tree_needs_its_page_its_index_and_its_root_line(self) -> None:
        root = self.workshop({TREE_PAGE: None})
        self.edit(root, ROOT_INDEX, "- `workshop` — the bicycle workshop: its tools, its repairs, its people.\n", "")
        found = self.problems(root)
        self.assertIn(("tree_incomplete", "03 mtm/workshop/"), found)
        self.assertIn(("tree_incomplete", ROOT_INDEX), found)

    def test_an_agent_needs_its_tree_and_its_line(self) -> None:
        root = self.workshop()
        self.edit(root, ROOT_INDEX, "- `desk-bot` — Desk Bot · orchestrator: talks with Tobin\n",
                  "- `night-bot` — Night Bot · watcher\n")
        found = self.problems(root)
        self.assertIn(("agent_tree_incomplete", ROOT_INDEX), found)
        details = [problem.detail for problem in validate.check_structure(root) if problem.code == "agent_tree_incomplete"]
        self.assertEqual(len(details), 2)

    def test_frontmatter_keys_and_values(self) -> None:
        root = self.workshop({STAND: leaf("truing-stand", STAND_UP, extra="mood: calm\nstatus: superseded")})
        self.edit(root, COLLECTION, "subtopic: seen-once", "subtopic: people")
        self.edit(root, AGENT_PAGE, "entity: desk-bot", "entity: tobin-reel")
        self.assertCountEqual(self.problems(root), [
            ("frontmatter_value_invalid", AGENT_PAGE),
            ("frontmatter_value_invalid", COLLECTION),
            ("frontmatter_key_unknown", STAND),
            ("frontmatter_value_invalid", STAND),
        ])

    def test_sections_missing_unknown_and_out_of_order(self) -> None:
        root = self.workshop()
        self.edit(root, STAND_BOARD, "## Pointers\n\n", "## Gossip\n\n")
        self.edit(root, INDEX, "## Links\n\n- up: [[02 ltm/workshop_LTM|workshop LTM]]\n\n## Update record\n",
                  "## Update record\n\n## Links\n\n- up: [[02 ltm/workshop_LTM|workshop LTM]]\n")
        self.edit(root, OWNER_LEAF, "## Links", "## Later notes\n\nMore.\n\n## Links")
        self.assertCountEqual(self.problems(root), [
            ("section_order", INDEX),
            ("section_missing", STAND_BOARD),
            ("section_unknown", STAND_BOARD),
            ("section_order", OWNER_LEAF),
        ])

    def test_up_links_name_the_parent_and_other_wikilinks_are_code_spans(self) -> None:
        root = self.workshop()
        self.edit(root, STAND, "- up: [[03 mtm/workshop/truing-stand|up]]", "- up: [[03 mtm/workshop/people|up]]")
        self.edit(root, OWNER_LEAF, "- up: [[03 mtm/workshop/people|up]]", "")
        self.edit(root, TREE_PAGE, "The bicycle workshop.", "The bicycle workshop, see [[02 ltm/desk-bot_LTM]].")
        self.assertCountEqual(self.problems(root), [
            ("wikilink_not_up", TREE_PAGE),
            ("up_link_wrong", STAND),
            ("up_link_missing", OWNER_LEAF),
        ])


class CheckCommandTests(CheckForestTestCase):
    def test_the_synthetic_forest_passes_forest_check(self) -> None:
        result = forest("check", "--root", str(SYNTHETIC_FOREST))
        self.assertEqual((result.returncode, result.stdout), (0, b""), result.stderr)

    def test_one_line_per_problem_and_exit_1(self) -> None:
        root = self.workshop({STAND: leaf("", STAND_UP)})
        result = forest("check", "structure", "--root", str(root))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.decode().splitlines(), [f"{STAND}: leaf_entity_missing: no `entity:` in the frontmatter"])
        as_json = json.loads(forest("check", "--root", str(root), "--json").stdout)
        self.assertEqual(as_json["checks"], ["journal", "structure", "lists", "pointers", "agents", "levels", "categories"])
        self.assertFalse(as_json["ok"])
