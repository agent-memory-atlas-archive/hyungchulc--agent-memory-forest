from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "docs" / "archive-0.3"
GUIDE = ARCHIVE / "retrieval-guide.md"
PROVENANCE_GUIDE = ARCHIVE / "provenance-and-promotion.md"
WSL2_GUIDE = ROOT / "docs" / "wsl2.md"
LINK_RE = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")
SCHEMAS = (
    "daily-plan.schema.json",
    "promotion-plan.schema.json",
    "query-plan.schema.json",
    "structured-sweep-plan.schema.json",
    "write-receipt.schema.json",
)


class RetrievalDocumentationTests(unittest.TestCase):
    """The 0.3 guides stay readable in the archive while the 0.3 code is in the tree."""

    def test_archived_guides_keep_their_relative_links_resolvable(self) -> None:
        for page in sorted(ARCHIVE.glob("*.md")):
            body = page.read_text(encoding="utf-8")
            for raw_target in LINK_RE.findall(body):
                target = raw_target.split("#", maxsplit=1)[0]
                if not target or "://" in target or target.startswith("mailto:"):
                    continue
                with self.subTest(page=page.name, target=raw_target):
                    self.assertTrue((page.parent / target).exists())

    def test_the_0_3_schemas_live_beside_the_docs_not_in_the_archive(self) -> None:
        for name in SCHEMAS:
            with self.subTest(schema=name):
                self.assertTrue((ROOT / "docs" / name).is_file())
                self.assertFalse((ARCHIVE / name).exists())

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

    def test_the_0_3_release_stays_in_the_changelog(self) -> None:
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## 0.3.0 - 2026-08-31", changelog)
        self.assertIn("license changed from MIT to GPL-3.0-only", changelog)

    def test_public_guidance_is_provider_neutral(self) -> None:
        for path in (
            ROOT / "README.md",
            ROOT / "README.ko.md",
            ARCHIVE / "daily-and-istm-companions.md",
        ):
            with self.subTest(path=path.name):
                body = path.read_text(encoding="utf-8")
                self.assertNotIn("GPT-5.6", body)
                self.assertNotIn("reasoning effort xhigh", body)
        guide = GUIDE.read_text(encoding="utf-8")
        self.assertIn("an integrating application's private production alias", guide)

    def test_archived_writer_docs_share_the_integrated_writer_contract(self) -> None:
        paths = (
            ARCHIVE / "roadmap.md",
            ARCHIVE / "automation.md",
            ARCHIVE / "assets" / "memory-forest-automation.svg",
        )
        for path in paths:
            with self.subTest(path=path.name):
                body = path.read_text(encoding="utf-8")
                self.assertIn("apply-structured", body)
                self.assertNotIn("apply-daily + promote", body)
                self.assertNotIn("`apply-daily` or `promote`", body)

    def test_archived_provenance_guide_matches_the_v03_writer_contract(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
