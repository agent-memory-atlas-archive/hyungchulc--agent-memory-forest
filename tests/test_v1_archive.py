from __future__ import annotations

import gzip
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v1_support import ForestTestCase  # noqa: E402

from memory_forest import archive  # noqa: E402
from memory_forest.archive import ArchiveProofError  # noqa: E402
from memory_forest.errors import MemoryForestError  # noqa: E402
from memory_forest.locking import writer_lock  # noqa: E402

LEAF = "04 stm/bakery/deck-oven/repairs.md"
DAY = datetime(2051, 3, 2, 9, 30, tzinfo=timezone.utc)


class ArchiveTests(ForestTestCase):
    def test_put_keeps_the_exact_bytes_under_a_dated_folder_and_lists_them(self) -> None:
        root = self.make_forest()
        original = (root / LEAF).read_bytes()
        entry = archive.put(root, LEAF, reason="folded into maintenance", successor="04 stm/bakery/deck-oven/maintenance.md", when=DAY)
        self.assertFalse((root / LEAF).exists())
        self.assertEqual(entry.archive_file, f"2051-03-02/{LEAF}.gz")
        self.assertEqual(entry.archive_path, f".forest/archive/2051-03-02/{LEAF}.gz")
        self.assertEqual(gzip.decompress((root / entry.archive_path).read_bytes()), original)
        self.assertEqual((entry.bytes, entry.time), (len(original), "2051-03-02T09:30:00Z"))
        self.assertEqual(archive.entries(root), [entry])
        self.assertEqual(archive.show(root, LEAF), original)
        manifest = (root / ".forest" / "archive" / "manifest.jsonl").read_text(encoding="utf-8")
        self.assertEqual(manifest.count("\n"), 1)
        self.assertIn('"successor": "04 stm/bakery/deck-oven/maintenance.md"', manifest)

    def test_keep_archives_the_version_before_a_rewrite(self) -> None:
        root = self.make_forest()
        original = (root / LEAF).read_bytes()
        archive.put(root, LEAF, reason="before a trim", keep=True, when=DAY)
        self.assertEqual((root / LEAF).read_bytes(), original)
        (root / LEAF).write_text("rewritten\n", encoding="utf-8")
        second = archive.put(root, LEAF, reason="before another trim", keep=True, when=DAY)
        self.assertNotEqual(second.archive_file, f"2051-03-02/{LEAF}.gz")
        self.assertEqual(archive.show(root, LEAF), b"rewritten\n")
        self.assertEqual([item.reason for item in archive.entries(root, LEAF)], ["before a trim", "before another trim"])
        # the same bytes again reuse their copy
        third = archive.put(root, LEAF, reason="again", keep=True, when=DAY)
        self.assertEqual(third.archive_file, second.archive_file)

    def test_store_keeps_bytes_of_a_page_no_longer_on_disk(self) -> None:
        root = self.make_forest()
        entry = archive.store(root, "04 stm/bakery/deck-oven/gone.md", b"# gone\n", reason="merged")
        self.assertEqual(archive.show(root, "04 stm/bakery/deck-oven/gone.md"), b"# gone\n")
        self.assertEqual(archive.latest(root, "04 stm/bakery/deck-oven/gone.md"), entry)

    def test_only_forest_pages_with_a_reason_are_archived(self) -> None:
        root = self.make_forest()
        for path in ("05 daily/2051-03-02.md", "../outside.md", "forest.toml", ".forest/registry.json"):
            with self.subTest(path=path):
                with self.assertRaises(MemoryForestError):
                    archive.put(root, path, reason="x")
        with self.assertRaises(MemoryForestError):
            archive.put(root, LEAF, reason="  ")
        with self.assertRaises(MemoryForestError):
            archive.put(root, "04 stm/bakery/deck-oven/missing.md", reason="x")
        self.assertTrue((root / LEAF).exists())

    def test_symlinked_pages_are_refused(self) -> None:
        root = self.make_forest()
        outside = self.base / "outside.md"
        outside.write_text("secret\n", encoding="utf-8")
        os.symlink(outside, root / "04 stm/bakery/deck-oven/link.md")
        with self.assertRaises(MemoryForestError):
            archive.put(root, "04 stm/bakery/deck-oven/link.md", reason="x")
        self.assertTrue(outside.exists())

    def test_put_needs_the_writer_lock_or_its_token(self) -> None:
        root = self.make_forest()
        with writer_lock(root) as token:
            with self.assertRaises(MemoryForestError) as caught:
                archive.put(root, LEAF, reason="x", keep=True)
            self.assertEqual(caught.exception.code, "lock_already_held")
            archive.put(root, LEAF, reason="x", keep=True, lock=token)
        self.assertEqual(len(archive.entries(root)), 1)

    def test_a_copy_that_no_longer_matches_is_refused(self) -> None:
        root = self.make_forest()
        entry = archive.put(root, LEAF, reason="x", keep=True)
        (root / entry.archive_path).write_bytes(gzip.compress(b"tampered\n"))
        with self.assertRaises(MemoryForestError) as caught:
            archive.show(root, LEAF)
        self.assertEqual(caught.exception.code, "archive_copy_changed")
        with self.assertRaises(MemoryForestError):
            archive.show(root, "04 stm/never/archived/page.md")


class ProofTests(ForestTestCase):
    def test_lines_in_the_copy_are_proved_byte_for_byte(self) -> None:
        root = self.make_forest()
        page = root / LEAF
        page.write_text(
            "# Repairs\n\n## Update record\n\n- 2051-01-02 — first.\n- 2051-02-03 — second.\r\n- 2051-03-01 — third.\n",
            encoding="utf-8",
            newline="",
        )
        entry = archive.put(root, LEAF, reason="record trim", keep=True)
        self.assertEqual(
            archive.prove_lines_archived(root, LEAF, ["- 2051-01-02 — first.", "- 2051-02-03 — second.\n"]),
            entry,
        )
        with self.assertRaises(ArchiveProofError) as caught:
            archive.prove_lines_archived(root, LEAF, ["- 2051-01-02 — first", "- 2051-03-01 — third. "])
        self.assertEqual(caught.exception.details["missing_count"], 2)
        with self.assertRaises(ArchiveProofError):
            archive.prove_lines_archived(root, "04 stm/bakery/deck-oven/other.md", ["x"])
        with self.assertRaises(ArchiveProofError):
            archive.prove_lines_archived(root, "04 stm/bakery/deck-oven/other.md", ["x"], entry=entry)


if __name__ == "__main__":
    unittest.main()
