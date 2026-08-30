from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "retrieval-guide.md"
PROVENANCE_GUIDE = ROOT / "docs" / "provenance-and-promotion.md"
WSL2_GUIDE = ROOT / "docs" / "wsl2.md"
LINK_RE = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")


class RetrievalDocumentationTests(unittest.TestCase):
    def test_guide_covers_the_public_retrieval_contract(self) -> None:
        body = GUIDE.read_text(encoding="utf-8")
        for heading in (
            "## 1. Intake: select a root, then treat the query as data",
            "## 3. Optional multilingual QueryPlan probes",
            "## 4. Generate and rank bounded candidates deterministically",
            "## 5. Materialize the canonical trail root-first",
            "## 6. Verify source state, then keep body access explicit",
            "## 7. Freshness, conflicts, no evidence, and chronology fallback",
            "## 8. Hybrid and external integration boundary",
            "## 9. Evaluate the two questions separately",
        ):
            self.assertIn(heading, body)
        self.assertIn("01 XLTM -> 02 LTM -> 03 MTM -> 04 STM", body)
        self.assertIn("`05 daily` and `06 istm` are not implicit `retrieve` candidates", body)

    def test_relative_markdown_links_in_guide_resolve(self) -> None:
        body = GUIDE.read_text(encoding="utf-8")
        for raw_target in LINK_RE.findall(body):
            target = raw_target.split("#", maxsplit=1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            with self.subTest(target=raw_target):
                self.assertTrue((GUIDE.parent / target).is_file())

    def test_english_and_korean_readmes_link_to_the_guide(self) -> None:
        for name in ("README.md", "README.ko.md"):
            with self.subTest(name=name):
                self.assertIn(
                    "docs/retrieval-guide.md",
                    (ROOT / name).read_text(encoding="utf-8"),
                )

    def test_wsl2_guide_is_linked_and_states_the_portability_boundary(self) -> None:
        body = WSL2_GUIDE.read_text(encoding="utf-8")
        for phrase in (
            "WSL Linux filesystem",
            "`/mnt/c`",
            "`0700`",
            "`0600`",
            "backup",
            "Windows ACL",
        ):
            self.assertIn(phrase, body)
        for name in ("README.md", "README.ko.md"):
            with self.subTest(name=name):
                self.assertIn(
                    "docs/wsl2.md",
                    (ROOT / name).read_text(encoding="utf-8"),
                )

    def test_release_metadata_matches_v03_publication(self) -> None:
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
        self.assertNotIn("## Unreleased", changelog)
        self.assertIn("## 0.3.0 - 2026-08-31", changelog)
        self.assertIn("license changed from MIT to GPL-3.0-only", changelog)
        self.assertIn("version: 0.3.0", citation)
        self.assertIn("date-released: 2026-08-31", citation)

    def test_public_guidance_is_provider_neutral(self) -> None:
        for path in (
            ROOT / "README.md",
            ROOT / "README.ko.md",
            ROOT / "docs" / "daily-and-istm-companions.md",
        ):
            with self.subTest(path=path.name):
                body = path.read_text(encoding="utf-8")
                self.assertNotIn("GPT-5.6", body)
                self.assertNotIn("reasoning effort xhigh", body)
        guide = GUIDE.read_text(encoding="utf-8")
        self.assertNotIn("Aria's private production alias maps", guide)
        self.assertIn("an integrating application's private production alias", guide)

    def test_english_and_korean_readmes_share_the_reference_write_path(self) -> None:
        for name in ("README.md", "README.ko.md"):
            with self.subTest(name=name):
                body = (ROOT / name).read_text(encoding="utf-8")
                self.assertIn("memory-forest apply-structured", body)
                self.assertIn("structured-context", body)
                self.assertIn("docs/integrated-structured-sweep.md", body)

    def test_release_docs_share_the_integrated_writer_contract(self) -> None:
        paths = (
            ROOT / "docs" / "roadmap.md",
            ROOT / "docs" / "automation.md",
            ROOT / "docs" / "assets" / "memory-forest-automation.svg",
        )
        for path in paths:
            with self.subTest(path=path.name):
                body = path.read_text(encoding="utf-8")
                self.assertIn("apply-structured", body)
                self.assertNotIn("apply-daily + promote", body)
                self.assertNotIn("`apply-daily` or `promote`", body)

    def test_provenance_guide_matches_the_v03_writer_contract(self) -> None:
        body = PROVENANCE_GUIDE.read_text(encoding="utf-8")
        self.assertIn(
            "`schema_version`, `forest_id`, `transaction_id`, `date`, `entries`, and",
            body,
        )
        self.assertIn("`memory-forest apply-structured ROOT PLAN`", body)
        self.assertIn(
            "All three writer operations, `apply-daily`, `apply-structured`, and `promote`",
            body,
        )
