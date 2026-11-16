"""The front page as a package index shows it (``scripts/pypi_readme.py``), and the workflow that uploads it.

An index renders the page away from the repository, so every link on it must be a whole address, and each
address the script writes must name a file of this tree at the version's tag. Nothing else on the page may
change: the commands a reader copies are the same there as here.
"""

from __future__ import annotations

import difflib
import importlib.util
import re
import tomllib
import unittest
from pathlib import Path

import bounds  # installs a deadline for every unittest invocation

REPOSITORY = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pypi_readme", REPOSITORY / "scripts" / "pypi_readme.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

TARGET = re.compile(r"\]\(([^)\s]+)\)")
WHOLE = re.compile(r"[a-z][a-z0-9+.-]*:|#")
FENCE = re.compile(r"(?ms)^```([a-z]*)\n(.*?)^```\n")


def relative(line: str) -> list[str]:
    return [target for target in TARGET.findall(line) if not WHOLE.match(target)]


class IndexPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.page = (REPOSITORY / "README.md").read_text(encoding="utf-8")
        self.written = MODULE.for_index(self.page, "9.9.9")

    def test_every_link_is_a_whole_address_that_names_a_file_of_this_tree(self) -> None:
        self.assertTrue(relative(self.page), "the front page links into the tree")
        self.assertEqual(relative(self.written), [])
        named = [target for target in TARGET.findall(self.written)
                 if target.startswith((MODULE.HOME + "/blob/", MODULE.HOME + "/tree/"))]
        self.assertGreaterEqual(len(named), len(relative(self.page)))
        for target in named:
            with self.subTest(target=target):
                kind, tag, path = target[len(MODULE.HOME) + 1:].split("/", 2)
                self.assertEqual(tag, "v9.9.9")
                place = REPOSITORY / path.partition("#")[0]
                self.assertTrue(place.is_dir() if kind == "tree" else place.is_file(), f"{place} is not there")

    def test_the_marker_and_the_diagram_are_written_for_a_page_that_draws_neither(self) -> None:
        self.assertIn("> [!IMPORTANT]\n> Agent Memory Forest is not", self.page)
        self.assertIn("```mermaid\n", self.page)
        self.assertIn("> **Important**\n>\n> Agent Memory Forest is not", self.written)
        self.assertNotRegex(self.written, r"(?m)^> \[!")
        self.assertNotIn("```mermaid", self.written)
        self.assertIn(f"]({MODULE.HOME}/blob/v9.9.9/README.md#the-forest-in-one-picture)", self.written)
        self.assertIn("\n## The forest in one picture\n", self.page)

    def test_nothing_else_on_the_page_changes(self) -> None:
        def blocks(text: str) -> list[str]:
            return [body for language, body in FENCE.findall(text) if language != "mermaid"]

        self.assertEqual(blocks(self.written), blocks(self.page))
        before, after = self.page.splitlines(), self.written.splitlines()
        diagram: set[int] = set()
        inside = False
        for number, line in enumerate(before):
            if line == "```mermaid":
                inside = True
            if inside:
                diagram.add(number)
                inside = line != "```"
        changed = [number
                   for tag, first, last, _, _ in difflib.SequenceMatcher(a=before, b=after, autojunk=False).get_opcodes()
                   if tag != "equal" for number in range(first, last)]
        self.assertTrue(changed)
        for number in changed:
            line = before[number]
            self.assertTrue(number in diagram or line == "> [!IMPORTANT]" or relative(line),
                            f"line {number + 1} changed without a reason: {line!r}")

    def test_the_address_is_the_one_the_package_declares(self) -> None:
        settings = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(settings["project"]["urls"]["Repository"], MODULE.HOME)
        self.assertEqual(settings["project"]["readme"], "README.md")


class PublishWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = (REPOSITORY / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")

    def test_it_runs_only_by_hand_and_uploads_only_when_asked(self) -> None:
        trigger = self.workflow.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(re.findall(r"(?m)^  (\w+):", trigger), ["workflow_dispatch"])
        upload = self.workflow.split("\n  upload:\n", 1)[1]
        self.assertIn("    if: inputs.upload\n", upload)
        self.assertIn("    needs: build\n", upload)

    def test_the_page_is_rewritten_before_the_build_and_the_tag_names_the_version(self) -> None:
        build = self.workflow.split("\n  build:\n", 1)[1].split("\n  upload:\n", 1)[0]
        self.assertLess(build.index("scripts/pypi_readme.py --write"), build.index("python -m build"))
        self.assertIn('test "$REF" = "v$version"', build)

    def test_no_secret_is_named_and_only_the_upload_may_ask_for_an_identity(self) -> None:
        self.assertNotIn("secrets.", self.workflow)
        self.assertNotIn("password", self.workflow)
        head, upload = self.workflow.split("\n  upload:\n", 1)
        self.assertNotIn("id-token", head)
        self.assertIn("      id-token: write\n", upload)
        self.assertIn("\npermissions:\n  contents: read\n", head)


if __name__ == "__main__":
    unittest.main()
