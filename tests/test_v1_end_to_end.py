"""A forest from nothing, as the README shows it: init, three agents, a few leaves by hand, then every check.

Every name belongs to an invented world: Juno, who runs a small pottery
studio, her kiln, a glaze she mixes, and three agents.
"""

from __future__ import annotations

import bounds  # every invocation has a deadline

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v1_support import REPOSITORY_ROOT, ForestTestCase  # noqa: E402

KILNS_BOARD = """# Kilns

## About

The studio's kilns, one leaf each.

## Current state

## Leaves

## Pointers

## Links

- up: [[02 ltm/studio_LTM|studio LTM]]
"""

KILN_TWO = """---
entity: kiln-two
subtopic: profile
kind: profile
about: none
timezone: America/Halifax
updated_at: 2042-05-14
---

# Kiln Two

The electric kiln in the back room, bought second-hand.

## Timeline

- 2042-05-14 08:30 America/Halifax — Juno fired a load of bowls to cone 6.

## Links

- up: [[03 mtm/studio/kilns|kilns MTM]]
"""

CELADON = """---
entity: celadon
subtopic: profile
kind: work
about: kiln-two
timezone: America/Halifax
updated_at: 2042-05-14
---

# Celadon glaze

Juno's pale green glaze, mixed in five-litre batches.

## Timeline

- 2042-05-14 10:00 America/Halifax — mixed a batch for the next firing in Kiln Two.

## Links

- up: [[03 mtm/studio/glazes|glazes MTM]]
"""

SUBJECTS = (
    "- `kiln-two` — Kiln Two · device · leaf · `04 stm/studio/kilns/kiln-two.md` · aliases: the back kiln\n"
    "- `celadon` — Celadon glaze · topic · leaf · `04 stm/studio/glazes/celadon.md`\n"
)


def forest(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", "from memory_forest.cli import forest_main; raise SystemExit(forest_main())",
         *arguments],
        capture_output=True, text=True, env={"PYTHONPATH": str(REPOSITORY_ROOT / "src"), "PATH": "/usr/bin:/bin"},
    )


class EndToEndTests(ForestTestCase):
    def run_ok(self, *arguments: str) -> str:
        result = forest(*arguments)
        self.assertEqual(result.returncode, 0, f"forest {' '.join(arguments)}\n{result.stdout}{result.stderr}")
        return result.stdout

    def test_a_forest_from_nothing(self) -> None:
        root = self.base / "studio-forest"
        at = ("--root", str(root))
        self.run_ok("init", str(root))

        # The owner, the orchestrator, and one agent declared in the settings.
        settings = root / "forest.toml"
        text = settings.read_text(encoding="utf-8")
        text = text.replace('timezone = "UTC"', 'timezone = "America/Halifax"')
        text = text.replace('name = "the owner"', 'name = "Juno"').replace('id = "owner"', 'id = "juno-hale"')
        text = text.replace("orchestrators = []", 'orchestrators = ["studio-hub"]')
        text += ('\n[agents.known.studio-hub]\nname = "Studio Hub"\n'
                 'role = "talks with Juno and hands work to the bots"\n')
        settings.write_text(text, encoding="utf-8")

        # Two agents by their raw lanes: each wrote its first line.
        for agent in ("kiln-bot", "post-bot"):
            lane = root / "06 istm" / agent
            lane.mkdir()
            (lane / "2042-05-14.jsonl").write_text('{"text": "started"}\n', encoding="utf-8")

        # A shared tree, and a few leaves written by hand as the contract shapes them.
        self.run_ok("move", "new-tree", "studio", "--description", "the pottery studio: kilns, glazes, orders.", *at)
        index = root / "03 mtm/studio/studio_MTM.md"
        index.write_text(index.read_text(encoding="utf-8").replace("## Subjects\n", "## Subjects\n\n" + SUBJECTS),
                         encoding="utf-8")
        (root / "03 mtm/studio/kilns.md").write_text(KILNS_BOARD.replace("## Current state\n", (
            "## Current state\n\n- Kiln Two: fired a load of bowls to cone 6 "
            "(as of 2042-05-14; source `04 stm/studio/kilns/kiln-two.md`)\n")), encoding="utf-8")
        (root / "03 mtm/studio/glazes.md").write_text(KILNS_BOARD.replace("Kilns", "Glazes").replace(
            "kilns, one leaf each", "glazes, one leaf each").replace("## Current state\n", (
                "## Current state\n\n- Celadon: a batch mixed for the next firing "
                "(as of 2042-05-14; source `04 stm/studio/glazes/celadon.md`)\n")), encoding="utf-8")
        for path, page in (("04 stm/studio/kilns/kiln-two.md", KILN_TWO), ("04 stm/studio/glazes/celadon.md",
                                                                            CELADON)):
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(page, encoding="utf-8")

        # One command makes the three agents' trees.
        self.assertEqual(forest("levels", "check", *at).returncode, 1)
        applied = self.run_ok("levels", "apply", *at)
        self.assertEqual(sorted(applied.splitlines()), [
            "kiln-bot: none -> tree (agent; its raw lane holds a file)",
            "post-bot: none -> tree (agent; its raw lane holds a file)",
            "studio-hub: none -> tree (agent; declared in the settings)",
        ])
        for agent in ("kiln-bot", "post-bot", "studio-hub"):
            self.assertTrue((root / f"02 ltm/{agent}_LTM.md").is_file(), agent)
        agents = (root / "01 xltm/XLTM.md").read_text(encoding="utf-8").split("## Agents", 1)[1]
        self.assertIn("- `studio-hub` — Studio Hub · talks with Juno and hands work to the bots", agents)

        self.run_ok("check", "--sync", *at)
        self.assertEqual(self.run_ok("check", *at), "")
        self.assertIn("0 violations, 0 upkeep, 0 due", self.run_ok("lint", *at))
        self.assertIn("run-note 0", self.run_ok("record", "kinds", *at))
        self.run_ok("numbers", "check", *at)
        self.assertEqual(self.run_ok("registry", "check", *at), "")
        self.assertEqual(self.run_ok("levels", "check", *at), "")
