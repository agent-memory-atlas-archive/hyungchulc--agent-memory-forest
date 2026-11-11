from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v1_support import REPOSITORY_ROOT, SYNTHETIC_FOREST, ForestTestCase  # noqa: E402

from memory_forest import cli  # noqa: E402

LEAF = "04 stm/bakery/deck-oven/repairs.md"


def forest(*arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess[bytes]:
    environment = dict(os.environ, PYTHONPATH=str(REPOSITORY_ROOT / "src"))
    return subprocess.run(
        [sys.executable, "-c", "from memory_forest.cli import forest_main; raise SystemExit(forest_main())", *arguments],
        capture_output=True,
        env=environment,
        cwd=cwd or REPOSITORY_ROOT,
        check=False,
    )


class ForestCommandTests(ForestTestCase):
    def test_init_then_registry_build_on_the_new_forest(self) -> None:
        target = self.base / "fresh"
        created = forest("init", str(target))
        self.assertEqual(created.returncode, 0, created.stderr)
        self.assertTrue((target / "forest.toml").is_file())
        built = forest("registry", "build", "--root", str(target), "--json")
        self.assertEqual(built.returncode, 0, built.stderr)
        self.assertEqual(json.loads(built.stdout)["subjects"], 0)
        again = forest("init", str(target))
        self.assertEqual(again.returncode, 2)
        self.assertIn(b"root_not_empty", again.stderr)

    def test_registry_check_passes_on_the_synthetic_forest(self) -> None:
        result = forest("registry", "check", "--root", str(SYNTHETIC_FOREST))
        self.assertEqual((result.returncode, result.stdout), (0, b""), result.stderr)

    def test_registry_build_writes_the_file_and_finds_the_root_from_below(self) -> None:
        root = self.copy_synthetic()
        result = forest("registry", "build", cwd=root / "04 stm" / "studio")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"0 problems", result.stdout)
        self.assertTrue((root / ".forest" / "registry.json").is_file())

    def test_registry_check_prints_one_line_per_problem_and_exits_1(self) -> None:
        root = self.make_forest()
        index = root / "03 mtm" / "bakery" / "bakery_MTM.md"
        index.write_text(
            index.read_text(encoding="utf-8").replace(
                "## Branches", "- `deck-oven` — Copy · device · branch · `03 mtm/bakery/deck-oven.md`\n- broken\n\n## Branches"
            ),
            encoding="utf-8",
        )
        result = forest("--root", str(root), "registry", "check")
        self.assertEqual(result.returncode, 1)
        lines = result.stdout.decode("utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("03 mtm/bakery/bakery_MTM.md:8: registry_duplicate_id: "), lines)
        self.assertIn("registry_line_invalid", lines[1])

    def test_registry_show_by_id_name_or_alias(self) -> None:
        root = self.make_forest()
        for name in ("deck-oven", "Deck Oven", "the old oven"):
            with self.subTest(name=name):
                result = forest("registry", "show", name, "--root", str(root))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["id"], "deck-oven")
        entry = forest("registry", "show", "Tam", "--root", str(root))
        self.assertEqual(json.loads(entry.stdout)["collection"], "regulars-seen-once")
        close = forest("registry", "show", "oven", "--root", str(root))
        self.assertEqual(close.returncode, 1)
        self.assertIn(b"deck-oven", close.stdout)

    def test_archive_put_show_list_and_move_resolve(self) -> None:
        root = self.make_forest()
        original = (root / LEAF).read_bytes()
        put = forest("archive", "put", LEAF, "--reason", "folded", "--root", str(root))
        self.assertEqual(put.returncode, 0, put.stderr)
        self.assertFalse((root / LEAF).exists())
        shown = forest("archive", "show", LEAF, "--root", str(root))
        self.assertEqual(shown.stdout, original)
        listed = forest("archive", "list", "--root", str(root), "--json")
        self.assertEqual(json.loads(listed.stdout)["entries"][0]["path"], LEAF)
        resolved = forest("move", "resolve", LEAF, "--root", str(root), "--json")
        self.assertEqual(resolved.returncode, 0)
        self.assertEqual(json.loads(resolved.stdout)["status"], "archived")
        unknown = forest("move", "resolve", "04 stm/x/y/z.md", "--root", str(root))
        self.assertEqual(unknown.returncode, 1)
        self.assertTrue(unknown.stdout.startswith(b"unknown: "))
        missing = forest("archive", "put", LEAF, "--reason", "again", "--root", str(root), "--json")
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(json.loads(missing.stdout)["error"]["code"], "path_not_found")

    def test_the_0_3_command_still_works(self) -> None:
        environment = dict(os.environ, PYTHONPATH=str(REPOSITORY_ROOT / "src"))
        result = subprocess.run(
            [sys.executable, "-m", "memory_forest", "--version"], capture_output=True, env=environment, check=False
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"memory-forest", result.stdout)

    def test_the_console_scripts_are_declared(self) -> None:
        text = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('memory-forest = "memory_forest.cli:main"', text)
        self.assertIn('forest = "memory_forest.cli:forest_main"', text)


class DispatchTests(unittest.TestCase):
    def test_a_command_is_one_function_and_one_table_line(self) -> None:
        calls: list[str] = []

        def run(arguments: object) -> int:
            calls.append(getattr(arguments, "thing"))
            return 0

        commands = [*cli.FOREST_COMMANDS, cli.ForestCommand(("levels", "probe"), run, "test", (cli.arg("thing"),))]
        parser = cli.build_forest_parser(commands)
        arguments = parser.parse_args(["levels", "probe", "x", "--root", "/r"])
        self.assertEqual(arguments._run(arguments), 0)
        self.assertEqual((calls, arguments.root), (["x"], "/r"))
        words = {command.words for command in cli.FOREST_COMMANDS}
        for expected in (("init",), ("registry", "build"), ("registry", "check"), ("registry", "show"),
                         ("move", "resolve"), ("archive", "put"), ("archive", "show"), ("archive", "list")):
            self.assertIn(expected, words)

    def test_every_rooted_command_takes_root_and_json(self) -> None:
        parser = cli.build_forest_parser()
        for command in cli.FOREST_COMMANDS:
            if not command.uses_root:
                continue
            positional = [
                "x" for argument in command.arguments if not argument.flags[0].startswith("-")
            ]
            required = []
            for argument in command.arguments:
                if argument.options.get("required"):
                    required += [argument.flags[0], "x"]
            with self.subTest(command=command.words):
                arguments = parser.parse_args([*command.words, *positional, *required, "--root", "/r", "--json"])
                self.assertEqual((arguments.root, arguments.json), ("/r", True))


if __name__ == "__main__":
    unittest.main()
