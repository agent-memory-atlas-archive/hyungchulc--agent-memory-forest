"""The front page's finding example is what the invented forest answers.

The section "Finding in five lines" shows a few commands run in a copy of the synthetic forest, with lines of
their answers under them. This runs each command there and looks for every shown line in what the command
printed, so the front page cannot drift from the package.
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

import bounds  # installs a deadline for every unittest invocation

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v1_cli import forest  # noqa: E402
from v1_support import REPOSITORY_ROOT, ForestTestCase  # noqa: E402


def example(readme: str) -> list[tuple[list[str], list[str]]]:
    """Each command of the section's shell block, with the pieces of answer shown under it.

    A full-line comment is a line of the answer; `...` stands for lines left out, and a long line may be
    wrapped, so the lines between two `...` are one piece, compared without regard to white space.
    """
    section = readme.split("\n## Finding in five lines\n", 1)[1].split("\n## ", 1)[0]
    found = re.search(r"```sh\n(.*?)```", section, re.DOTALL)
    assert found, "the section has no shell block"
    commands: list[tuple[list[str], list[str]]] = []
    piece: list[str] = []

    def close() -> None:
        if piece:
            commands[-1][1].append(" ".join(" ".join(piece).split()))
            piece.clear()

    for line in found.group(1).splitlines():
        if line.startswith("#"):
            shown = line[1:].strip()
            if shown == "...":
                close()
            else:
                piece.append(shown)
            continue
        close()
        words = shlex.split(line, comments=True)
        assert words[0] == "forest", line
        commands.append((words[1:], []))
    close()
    return commands


class FindingExampleTests(ForestTestCase):
    def test_every_shown_line_is_in_what_the_command_prints(self) -> None:
        root = self.copy_synthetic()
        commands = example((REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertEqual([arguments[0] for arguments, _ in commands], ["search", "read", "timeline", "navigate", "read"])
        for arguments, pieces in commands:
            with self.subTest(command=" ".join(arguments)):
                result = forest(*arguments, cwd=root)
                printed = " ".join((result.stdout + result.stderr).decode("utf-8").split())
                refused = any(piece.startswith("forest: ") for piece in pieces)
                self.assertEqual(result.returncode != 0, refused, printed[:400])
                at = 0
                for piece in pieces:
                    place = printed.find(piece, at)
                    self.assertGreaterEqual(place, 0, f"not printed, or out of order: {piece!r}")
                    at = place + len(piece)

    def test_the_example_names_what_it_shows(self) -> None:
        commands = dict((" ".join(arguments[:2]), pieces)
                        for arguments, pieces in example((REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")))
        # The search is by an alias, and the answer says so; the last read is another agent's tree, refused.
        self.assertTrue(any('why: alias: "the big kiln" names kiln-two' in piece for piece in commands["search the big kiln"]))
        self.assertTrue(any("out_of_scope" in piece for piece in commands["read 02 ltm/kiln-bot_LTM.md"]))


if __name__ == "__main__":
    import unittest

    unittest.main()
