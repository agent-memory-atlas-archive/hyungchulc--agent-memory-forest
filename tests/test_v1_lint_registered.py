"""The lint codes the derived lists, the levels, the collections and the agents register, each with a page
that hits and one that does not, and the agreement of ``forest check`` with the lint on them."""

from __future__ import annotations

import bounds  # every invocation has a deadline

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v1_support import ForestTestCase, leaf_text  # noqa: E402

from memory_forest import lint, maps  # noqa: E402
from memory_forest.checks import run_checks  # noqa: E402
from memory_forest.config import load_config  # noqa: E402

BOARD = "03 mtm/bakery/deck-oven.md"
SUPPLIERS = "03 mtm/bakery/suppliers.md"
COLLECTION = "04 stm/people/regulars/seen-once-2051-q1.md"
CODES = ("board_leaves_stale", "branch_list_stale", "board_pointers_stale", "board_pointer_stale", "level_due",
         "category_too_large")


class RegisteredCodeTests(ForestTestCase):
    def forest(self) -> Path:
        root = self.make_forest()
        (root / "03 mtm/people/regulars.md").write_text("# regulars\n\nPeople who come in.\n", encoding="utf-8")
        maps.apply(root)
        return root

    def hits(self, root: Path, code: str) -> list[str]:
        return sorted({hit.path for hit in lint.lint(root, codes=[code]).hits})

    def edit(self, root: Path, path: str, old: str, new: str) -> None:
        page = root / path
        text = page.read_text(encoding="utf-8")
        self.assertIn(old, text)
        page.write_text(text.replace(old, new, 1), encoding="utf-8")

    def test_none_hits_on_a_synced_forest_and_none_is_left_unchecked(self) -> None:
        root = self.forest()
        report = lint.lint(root, codes=list(CODES))
        self.assertEqual(report.not_checked, [])
        self.assertEqual([hit.render() for hit in report.hits], [])

    def test_board_leaves_stale(self) -> None:
        root = self.forest()
        (root / "04 stm/bakery/deck-oven/firing.md").write_text(leaf_text("deck-oven"), encoding="utf-8")
        self.assertEqual(self.hits(root, "board_leaves_stale"), [BOARD])
        maps.apply(root)
        self.assertEqual(self.hits(root, "board_leaves_stale"), [])

    def test_branch_list_stale(self) -> None:
        root = self.forest()
        self.edit(root, "02 ltm/bakery_LTM.md", "- `suppliers`", "- `gone-branch`")
        self.assertEqual(self.hits(root, "branch_list_stale"), ["02 ltm/bakery_LTM.md"])
        maps.apply(root)
        self.assertEqual(self.hits(root, "branch_list_stale"), [])

    def test_board_pointers_stale(self) -> None:
        root = self.forest()
        self.edit(root, "04 stm/bakery/suppliers/millstone-flour.md", "about: deck-oven, ", "about: ")
        self.assertEqual(self.hits(root, "board_pointers_stale"), [BOARD])
        maps.apply(root)
        self.assertEqual(self.hits(root, "board_pointers_stale"), [])

    def test_board_pointer_stale(self) -> None:
        root = self.forest()
        self.assertEqual(self.hits(root, "board_pointer_stale"), [])
        self.edit(root, BOARD, "## Pointers\n", "## Pointers\n\n- the millstone notes\n")
        self.assertEqual(self.hits(root, "board_pointer_stale"), [BOARD])

    def test_level_due(self) -> None:
        root = self.forest()
        self.assertEqual(self.hits(root, "level_due"), [])
        self.edit(root, COLLECTION, "## Links", "- 2051-03-04 09:00 Pacific/Auckland — Tamsin Orrow: came back.\n\n"
                  "## Links")
        self.assertEqual(self.hits(root, "level_due"), [f"{COLLECTION}#tamsin-orrow"])

    def test_category_too_large(self) -> None:
        root = self.forest()
        self.assertEqual(self.hits(root, "category_too_large"), [])
        (root / "04 stm/bakery/suppliers/rye-mill.md").write_text(leaf_text("millstone-flour").replace(
            "subtopic: notes", "subtopic: rye"), encoding="utf-8")
        settings = root / "forest.toml"
        settings.write_text(settings.read_text(encoding="utf-8") + "\n[limits]\ncategory_max_leaves = 1\n",
                            encoding="utf-8")
        self.assertEqual(self.hits(root, "category_too_large"), [SUPPLIERS])

    def test_forest_check_and_the_lint_agree(self) -> None:
        root = self.forest()
        (root / "04 stm/bakery/deck-oven/firing.md").write_text(leaf_text("deck-oven"), encoding="utf-8")
        self.edit(root, BOARD, "## Pointers\n", "## Pointers\n\n- the millstone notes\n")
        self.edit(root, COLLECTION, "## Links", "- 2051-03-04 09:00 Pacific/Auckland — Tamsin Orrow: came back.\n\n"
                  "## Links")
        checked = {(problem.code, problem.path) for problem in run_checks(root, load_config(root))[1]
                   if problem.code in CODES}
        linted = {(hit.code, hit.path) for hit in lint.lint(root, codes=list(CODES)).hits}
        self.assertEqual(checked, linted)
        self.assertTrue(linted)
