"""The page on writing a reader and the ``Adapter`` Protocol name the same members with the same marks."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from memory_forest import adapters

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "adapters.md"

REQUIRED = ("name", "discover", "read", "open_path", "hook")
OPTIONAL = ("describe", "transcript_root", "id_stands_alone", "ids_stand_alone")


class AdapterGuideTests(unittest.TestCase):
    def test_the_page_marks_each_member_as_the_protocol_does(self) -> None:
        text = PAGE.read_text(encoding="utf-8")
        rows = dict(re.findall(r"^\| `(\w+)[^`]*` \| (required|optional) \|", text, re.MULTILINE))
        self.assertEqual(rows, {**dict.fromkeys(REQUIRED, "required"), **dict.fromkeys(OPTIONAL, "optional")})
        for words in ("`ReadBatch.carry` (optional)", "`max_bytes`", "`transcript_deferred` (optional"):
            self.assertIn(words, text)

    def test_the_protocol_names_the_same_members(self) -> None:
        doc = adapters.Adapter.__doc__ or ""
        required, _, optional = doc.partition("Optional")
        for member in REQUIRED:
            self.assertIn(f"``{member}``", required)
        for member in (*OPTIONAL, "ReadBatch.carry", "transcript_deferred"):
            self.assertIn(f"``{member}", optional)
        for member in ("discover", "read", "open_path", "hook"):
            self.assertTrue(getattr(adapters.Adapter, member).__doc__.startswith("Required."), member)
        self.assertIn("carry", adapters.Adapter.read.__code__.co_varnames)
        self.assertIn("max_bytes", adapters.Adapter.read.__code__.co_varnames)

    def test_the_page_links_resolve(self) -> None:
        text = PAGE.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", text):
            self.assertTrue((PAGE.parent / target).resolve().exists(), target)
        self.assertIn("](adapters.md)", (ROOT / "docs" / "tools.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
