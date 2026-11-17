"""Every language page of the front page keeps the frame of the English one: only the prose differs."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

import bounds  # installs a deadline for every unittest invocation
from memory_forest import __version__

ROOT = Path(__file__).resolve().parents[1]
# The row of languages in the order every page shows it: the file, and the name the row gives it.
LANGUAGES = (
    ("README.md", "English"),
    ("README.ko.md", "한국어"),
    ("README.zh-CN.md", "简体中文"),
    ("README.ja.md", "日本語"),
    ("README.es.md", "Español"),
    ("README.fr.md", "Français"),
    ("README.de.md", "Deutsch"),
)
LANGUAGE_PAGE = re.compile(r"^README(\.[A-Za-z-]+)?\.md$")
FENCE = re.compile(r"```([a-z]*)\n(.*?)```", re.S)
NOTES = ("> [!IMPORTANT]", "> [!NOTE]", "> [!WARNING]", "> [!TIP]", "> [!CAUTION]")


def page(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def prose(text: str) -> str:
    """The page with each code block folded into one word."""
    return FENCE.sub("CODEBLOCK", text)


def lines(text: str, start: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(start)]


def heading_levels(text: str) -> list[int]:
    return [len(line) - len(line.lstrip("#")) for line in lines(prose(text), "#")]


def command(line: str) -> str:
    """A shell line without its comment: the part a reader runs."""
    found = re.search(r"(^|\s)#", line)
    return (line[: found.start()] if found else line).rstrip()


def structure(line: str) -> str:
    """A diagram line with its labels blanked: the nodes, edges and groups are what is left."""
    line = re.sub(r'"[^"]*"', '""', line)
    line = re.sub(r"--\s.*?\s-->", "-- -->", line)
    line = re.sub(r"-\.\s.*?\s\.->", "-. .->", line)
    return line.rstrip()


def link_targets(text: str) -> list[str]:
    """Every link target in order, the row of languages left out."""
    return [target for target in re.findall(r"\]\(([^)\s]+)\)", text) if not LANGUAGE_PAGE.match(target)]


def sections(text: str) -> list[tuple[int, int]]:
    """Per section, in order: how many paragraphs and how many list items it holds."""
    out: list[tuple[int, int]] = []
    body: list[str] = []

    def close() -> None:
        paragraphs = [part for part in re.split(r"\n\s*\n", "\n".join(body)) if part.strip()]
        items = sum(1 for line in body if re.match(r"\s*(?:[-*]|\d+\.)\s", line))
        out.append((len(paragraphs), items))

    for line in prose(text).splitlines():
        if line.startswith("#"):
            close()
            body = []
        else:
            body.append(line)
    close()
    return out


def inline_code(text: str) -> set[str]:
    return set(re.findall(r"`[^`\n]+`", prose(text)))


class LanguagePageTests(unittest.TestCase):
    def test_the_row_names_every_language_page_in_the_tree(self) -> None:
        found = sorted(path.name for path in ROOT.glob("README*.md"))
        self.assertEqual(found, sorted(name for name, _ in LANGUAGES))

    def test_every_page_shows_the_row_with_its_own_language_marked(self) -> None:
        for name, _ in LANGUAGES:
            row = " · ".join(
                f"**{label}**" if other == name else f"[{label}]({other})" for other, label in LANGUAGES)
            with self.subTest(page=name):
                self.assertEqual(page(name).splitlines().count(row), 1)

    def test_every_page_names_this_release(self) -> None:
        for name, _ in LANGUAGES:
            with self.subTest(page=name):
                self.assertIn(__version__, page(name))

    def test_every_relative_link_of_the_front_page_leads_into_the_tree(self) -> None:
        for target in link_targets(page("README.md")):
            if "://" in target:
                continue
            with self.subTest(target=target):
                self.assertTrue((ROOT / target.split("#")[0]).exists())

    def test_the_picture_draws_an_agents_tree_like_a_shared_tree(self) -> None:
        # The two boxes of trees hold the same three nodes with the same words, and the edges from the root index
        # into them carry no label: where the repository's page draws this picture, a labelled edge puts its box
        # a rank lower, and the two trees no longer stand side by side.
        for name, _ in LANGUAGES:
            picture = next(body for kind, body in FENCE.findall(page(name)) if kind == "mermaid")
            words = dict(re.findall(r'\b(a?[lm]tm|a?stm)\["([^"]*)"\]', picture))
            drawn = [line.strip() for line in picture.splitlines()]
            with self.subTest(page=name):
                self.assertEqual([words[node] for node in ("altm", "amtm", "astm")],
                                 [words[node] for node in ("ltm", "mtm", "stm")])
                self.assertIn("xltm --> ltm", drawn)
                self.assertIn("xltm --> altm", drawn)

    def test_every_page_keeps_the_frame_of_the_english_page(self) -> None:
        english = page("README.md")
        for name, _ in LANGUAGES[1:]:
            other = page(name)
            with self.subTest(page=name, part="title"):
                self.assertEqual(other.splitlines()[0], english.splitlines()[0])
            with self.subTest(page=name, part="headings"):
                self.assertEqual(heading_levels(other), heading_levels(english))
            with self.subTest(page=name, part="links"):
                self.assertEqual(link_targets(other), link_targets(english))
            with self.subTest(page=name, part="notes, badges and table rows"):
                for mark in NOTES:
                    self.assertEqual(other.count(mark), english.count(mark), mark)
                self.assertEqual(lines(other, "[!["), lines(english, "[!["))
                self.assertEqual(len(lines(other, "|")), len(lines(english, "|")))
            with self.subTest(page=name, part="paragraphs and list items"):
                self.assertEqual(sections(other), sections(english))
            with self.subTest(page=name, part="inline code"):
                self.assertEqual(inline_code(english) - inline_code(other), set())
            here, there = FENCE.findall(english), FENCE.findall(other)
            with self.subTest(page=name, part="code blocks"):
                self.assertEqual([kind for kind, _ in there], [kind for kind, _ in here])
            for number, ((kind, ours), (_, theirs)) in enumerate(zip(here, there), 1):
                # A command is the same in every language; a comment and a diagram label are prose.
                keep = structure if kind == "mermaid" else command
                with self.subTest(page=name, part=f"code block {number}"):
                    self.assertEqual(
                        [keep(line) for line in theirs.splitlines()],
                        [keep(line) for line in ours.splitlines()],
                    )


if __name__ == "__main__":
    unittest.main()
