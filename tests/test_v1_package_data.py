"""The guide ships: its three files exist and the package data in ``pyproject.toml`` covers them.

``forest guide`` reads its parts from the installed package, so a wheel without them would print nothing
an agent can follow. This reads the files and the settings only; it builds nothing. The wheel smoke step of
the CI workflow checks the built wheel's file list for the same three files.
"""

from __future__ import annotations

import fnmatch
import tomllib
import unittest
from pathlib import Path

from memory_forest import cli  # noqa: F401  (cli_setup is imported through cli, which it imports)
from memory_forest import cli_setup

REPOSITORY = Path(__file__).resolve().parents[1]
PACKAGE = REPOSITORY / "src" / "memory_forest"
PARTS = ("setup", "routine", "use")


class GuidePackageDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))

    def test_the_guide_has_its_three_parts(self) -> None:
        self.assertEqual(tuple(cli_setup.GUIDE_PARTS), PARTS)
        self.assertEqual(Path(cli_setup.GUIDE_DIR).resolve(), (PACKAGE / "guide").resolve())
        for part in PARTS:
            with self.subTest(part=part):
                path = PACKAGE / "guide" / f"{part}.md"
                self.assertTrue(path.is_file(), path)
                self.assertTrue(path.read_text(encoding="utf-8").startswith("# "), path)

    def test_the_package_comes_from_src(self) -> None:
        setuptools = self.settings["tool"]["setuptools"]
        self.assertEqual(setuptools["package-dir"], {"": "src"})
        self.assertEqual(setuptools["packages"]["find"]["where"], ["src"])
        self.assertTrue((PACKAGE / "__init__.py").is_file())

    def test_the_package_data_covers_every_guide_file(self) -> None:
        patterns = self.settings["tool"]["setuptools"]["package-data"]["memory_forest"]
        for part in PARTS:
            with self.subTest(part=part):
                name = f"guide/{part}.md"
                self.assertTrue(any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns),
                                f"{name} is in no pattern of {patterns}")

    def test_the_wheel_smoke_checks_the_guide_files(self) -> None:
        workflow = (REPOSITORY / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        smoke = workflow.split("- name: Clean-install wheel smoke", 1)[1]
        self.assertIn("memory_forest/guide/", smoke)
        for part in PARTS:
            self.assertIn(f'"{part}"', smoke)


if __name__ == "__main__":
    unittest.main()
