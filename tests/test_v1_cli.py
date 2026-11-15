from __future__ import annotations

import json
import os
import shlex
import re
import subprocess
import sys
import unittest

import bounds  # installs a deadline for every unittest invocation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v1_support import REPOSITORY_ROOT, SYNTHETIC_FOREST, ForestTestCase  # noqa: E402

from memory_forest import cli  # noqa: E402
from memory_forest.config import find_root  # noqa: E402

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
            [sys.executable, "-m", "memory_forest", "--version"],
            capture_output=True,
            env=environment,
            cwd=self.base,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.decode().strip(), f"memory-forest {cli.LEGACY_VERSION}")
        self.assertEqual(cli.LEGACY_VERSION, "0.3.0")

    def test_one_version_string_is_what_the_package_and_forest_command_report(self) -> None:
        import tomllib

        from memory_forest import __version__

        self.assertEqual(__version__, "1.0.0a1")
        project = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(project["project"]["dynamic"], ["version"])
        self.assertEqual(project["tool"]["setuptools"]["dynamic"]["version"]["attr"], "memory_forest.__version__")
        self.assertNotIn("version", project["project"])
        citation = (REPOSITORY_ROOT / "CITATION.cff").read_text(encoding="utf-8")
        self.assertIn('title: "Agent Memory Forest"', citation)
        self.assertIn(f"version: {__version__}", citation)
        self.assertNotIn("date-released:", citation)
        for name in ("README.md", "README.ko.md", "CHANGELOG.md"):
            text = (REPOSITORY_ROOT / name).read_text(encoding="utf-8")
            self.assertIn(__version__, text, name)
            self.assertNotIn("1.0.0 (alpha)", text, name)
            self.assertNotIn("Version 1.0.0 is not released", text, name)
            self.assertNotIn("Version 1.0.0 is unreleased", text, name)
            self.assertNotIn("버전 1.0.0은 아직 배포되지 않았습니다", text, name)
        self.assertIn(f"## Unreleased — {__version__}", (REPOSITORY_ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
        workflow = (REPOSITORY_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn(f'memory_forest.__version__ == "{__version__}" == version("memory-forest")', workflow)
        reported = forest("--version", cwd=self.base)
        self.assertEqual(reported.returncode, 0, reported.stderr)
        self.assertEqual(reported.stdout.decode().strip(), f"forest {__version__}")

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


def command_argv(command: cli.ForestCommand) -> list[str]:
    """Enough arguments for the command to parse, so the forest-root rule is what answers."""
    argv = list(command.words)
    for argument in command.arguments:
        flag = argument.flags[0]
        if flag.startswith("-"):
            if argument.options.get("required"):
                argv.extend([flag, "x"])
            continue
        if argument.options.get("nargs") in ("?", "*"):
            continue
        argv.append("x")
    if command.words == ("credentials", "judge"):
        argv.append("--sign-in")
    return argv


def milestone_1_lines(readme: str) -> str:
    """The Milestone 1 lines of the README quick-start shell block, comments included."""
    blocks = re.findall(r"```sh\n(.*?)```", readme, re.DOTALL)
    block = next((item for item in blocks if "forest init my-forest" in item and "Milestone 1" in item), None)
    if block is None:
        raise AssertionError("the quick start has no Milestone 1 shell block")
    lines: list[str] = []
    started = False
    for line in block.splitlines():
        if not started:
            if re.search(r"Milestone 1\b", line):
                started = True
            continue
        if re.search(r"Milestone 2\b", line):
            break
        lines.append(line)
    if not any(line.lstrip().startswith(("forest ", "cd ")) for line in lines):
        raise AssertionError("Milestone 1 has no commands")
    return "\n".join(lines) + "\n"


class ForestRootTests(ForestTestCase):
    def test_every_command_but_init_refuses_an_empty_folder_and_writes_nothing(self) -> None:
        creators = [command.words for command in cli.FOREST_COMMANDS if not command.uses_root]
        # `forest setup` creates a forest only in the folder its --root names (test_v1_setup); the guide writes nothing.
        self.assertEqual(creators, [("init",), ("guide",), ("setup",)])
        parser = cli.build_forest_parser()
        folder = self.base / "empty"
        folder.mkdir()
        self.assertIsNone(find_root(folder))
        for command in cli.FOREST_COMMANDS:
            if not command.uses_root:
                continue
            argv = command_argv(command)
            with self.subTest(command=" ".join(command.words)):
                try:
                    parser.parse_args(argv)
                except SystemExit as exc:
                    self.fail(f"{argv} did not parse: {exc}")
                result = forest(*argv, cwd=folder)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                text = (result.stderr + result.stdout).decode()
                self.assertIn("forest.toml", text)
                self.assertIn("forest init", text)
                self.assertIn("--root", text)
                self.assertEqual(list(folder.iterdir()), [])

    def test_a_named_folder_without_settings_is_refused_and_left_empty(self) -> None:
        folder = self.base / "named"
        folder.mkdir()
        result = forest("registry", "build", "--root", str(folder), cwd=folder)
        self.assertNotEqual(result.returncode, 0)
        text = result.stderr.decode()
        self.assertIn(f"No forest.toml in {shlex.quote(str(folder))}.", text)
        # Only the caller knows which folder holds its forest: the refusal names the flag, never a command
        # with a folder filled in.
        self.assertIn("Name the forest's folder with `--root <folder>`", text)
        self.assertIn("`forest init <folder>` makes a new forest in an empty folder", text)
        self.assertNotIn(f"forest init {folder}", text)
        self.assertEqual(list(folder.iterdir()), [])
        as_json = forest("registry", "build", "--root", str(folder), "--json", cwd=folder)
        self.assertNotEqual(as_json.returncode, 0)
        self.assertEqual(json.loads(as_json.stdout)["error"]["code"], "no_forest")
        self.assertEqual(list(folder.iterdir()), [])

    def test_a_missing_root_is_still_not_found(self) -> None:
        missing = self.base / "missing"
        result = forest("registry", "build", "--root", str(missing))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"root_not_found", result.stderr)
        self.assertFalse(missing.exists())

    def test_an_explicit_root_wins_over_the_folder_the_command_is_in(self) -> None:
        first = self.base / "first"
        second = self.base / "second"
        self.assertEqual(forest("init", str(first)).returncode, 0)
        self.assertEqual(forest("init", str(second)).returncode, 0)
        result = forest("registry", "build", "--root", str(second), cwd=first)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((second / ".forest" / "registry.json").is_file())
        self.assertFalse((first / ".forest" / "registry.json").exists())

    def test_the_readme_milestone_1_quick_start_runs(self) -> None:
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        script = milestone_1_lines(readme)
        self.assertIn("forest init my-forest", script)
        self.assertIn("\ncd my-forest", script)
        self.assertNotIn("forest setup", script)
        work = self.base / "quick-start"
        work.mkdir()
        bindir = work / "bin"
        bindir.mkdir()
        wrapper = bindir / "forest"
        wrapper.write_text(
            "#!/bin/sh\n"
            "exec \"$FOREST_PYTHON\" -c "
            "'from memory_forest.cli import forest_main; raise SystemExit(forest_main())' \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        env = dict(os.environ, PYTHONPATH=str(REPOSITORY_ROOT / "src"), FOREST_PYTHON=sys.executable)
        env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
        result = subprocess.run(
            ["/bin/sh", "-c", "set -e\n" + script],
            cwd=work,
            env=env,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode() + "\n" + result.stdout.decode())
        created = work / "my-forest"
        self.assertTrue((created / "forest.toml").is_file())
        self.assertTrue((created / ".forest" / "registry.json").is_file())
        self.assertTrue((created / "02 ltm" / "studio-hub_LTM.md").is_file())
        self.assertFalse((work / ".forest").exists())


if __name__ == "__main__":
    unittest.main()
